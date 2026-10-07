"""고객 질문 하나의 AI 처리 흐름

    고객 질문 (AI 처리 중) → AI 서버 (분류 + 정책 검색 + 답변 초안, 이전 대화·고른 주문 포함) → 자동답변 판단
        ├─ 신뢰도 기준 이상 → 챗봇 답변 전송 (ai_response status=SENT, 자동) → 자동 종료 타이머 시작
        └─ 기준 미만 등   → 초안만 저장 (ai_response status=REVIEW_PENDING), 고객 화면엔 '담당자에게 전달했어요' 안내
                             (입력은 계속 가능, 타이머 멈춤 → 관리자 답변이 같은 채팅에 붙음)

AI 답변에 치환자({{주문번호}} 등)가 있으면 팀 DB 값으로 채운 뒤 저장·전송합니다 (services/placeholders.py).
채우지 못한 치환자가 남으면 자동 전송하지 않고 관리자 검토로 보냅니다.

팀 DB에 남는 것: ai_analysis(분류·판단·검토 사유, 질문마다 1행) + ai_response(초안·전송) + retrieved_policy(근거)
AI 서버 오류 내용과 응답 시간은 DB에 칸이 없어서 서버 로그에만 남깁니다.
AI 서버가 돌려준 근거 중 팀 DB의 정책 조각(document_key + 조각 순서)과 연결되는 것만 retrieved_policy에 남습니다.

고객이 답을 기다리지 않고 연달아 보내도, 한 채팅의 질문은 보낸 순서대로 하나씩 처리합니다 (채팅별 잠금).
AI 서버 호출이 끝난 뒤 결과를 한 번에 저장하므로, 중간에 서버가 꺼져도 반쯤 저장된 데이터가 남지 않습니다.
꺼질 때 'AI 처리 중'이던 질문은 서버가 다시 켜질 때 resume_unfinished()가 다시 처리합니다.
"""
import logging
import threading
import time

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.enums import REVIEW_REASON_LABELS, UNCLASSIFIED, MessageRole, QuestionStatus, ReviewReason, db_statuses
from app.models import (
    AIAnalysis,
    AIResponse,
    Conversation,
    ConversationExt,
    Customer,
    Inquiry,
    PolicyChunk,
    PolicyDocument,
    RetrievedPolicy,
    utcnow,
)
from app.plugins.ai_client import AIRequest, AIResult, HistoryItem, OrderContext, get_ai_client
from app.plugins.order_source import product_name
from app.services.conversations import chat_no, chat_order, question_status, transcript
from app.services.decision import decide
from app.services.placeholders import fill as fill_placeholders
from app.services.placeholders import latest_order

log = logging.getLogger(__name__)

PROCESSING_STATUSES = db_statuses(QuestionStatus.PROCESSING)
AUTO_OK = "자동답변 기준 충족"          # 검토 사유가 없을 때 decision_reason
_REASON_BY_LABEL = {label: code for code, label in REVIEW_REASON_LABELS.items()}


def reason_text(reasons: list[ReviewReason]) -> str:
    """검토 사유 → 팀 DB ai_analysis.decision_reason (사람이 읽는 목록)."""
    return ", ".join(REVIEW_REASON_LABELS[r] for r in reasons) or AUTO_OK


def reason_codes(text: str | None) -> list[str]:
    """decision_reason → 검토 사유 코드 (모르는 문구는 문구 그대로)."""
    if not text or text == AUTO_OK:
        return []
    return [_REASON_BY_LABEL[p].value if p in _REASON_BY_LABEL else p for p in text.split(", ")]


def _history(conv: Conversation, question: Inquiry) -> list[HistoryItem]:
    n = get_settings().ai_history_messages
    if n <= 0:
        return []
    # 이 질문보다 앞의 대화 + 그 사이에 붙은 앞 질문들의 답변 (이 질문과 뒤에 보낸 질문, 안내 메시지는 제외)
    prev = [m for m in transcript(conv)
            if not m.is_notice and not (m.role == MessageRole.CUSTOMER and m.question_id >= question.id)]
    return [HistoryItem(role="customer" if m.role == MessageRole.CUSTOMER else "assistant", content=m.content)
            for m in prev[-n:]]


def _chunk_id(db, document_key: str | None, chunk_index: int | None) -> int | None:
    """AI 서버가 돌려준 근거를 팀 DB의 정책 조각과 연결 (사용 중인 버전 기준, 못 찾으면 None)."""
    if not document_key or chunk_index is None:
        return None
    return db.scalar(select(PolicyChunk.id)
                     .join(PolicyDocument, PolicyChunk.policy_document_id == PolicyDocument.id)
                     .where(PolicyDocument.document_key == document_key, PolicyDocument.is_active.is_(True),
                            PolicyChunk.chunk_order == chunk_index))


def _confidence(v: float | None) -> float | None:
    return v if v is not None and 0 <= v <= 1 else None    # 팀 DB는 0~1만 저장 가능


def _label(v: str | None) -> str:
    return v.strip() if v and v.strip() else UNCLASSIFIED


_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


def _conversation_lock(conversation_id: int) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(conversation_id, threading.Lock())


def process_question(question_id: int) -> None:
    """질문이 속한 채팅의 'AI 처리 중' 질문들을 보낸 순서대로 처리.

    앞 질문을 처리하는 중에 새 질문이 오면, 앞 질문이 끝날 때까지 기다렸다가 이어서 처리합니다.
    """
    with SessionLocal() as db:
        conversation_id = db.scalar(select(Inquiry.conversation_id).where(Inquiry.id == question_id))
    if conversation_id is None:
        return
    with _conversation_lock(conversation_id):
        done: set[int] = set()
        while True:
            with SessionLocal() as db:
                next_id = db.scalar(select(Inquiry.id)
                                    .where(Inquiry.conversation_id == conversation_id,
                                           Inquiry.status.in_(PROCESSING_STATUSES), Inquiry.id.not_in(done))
                                    .order_by(Inquiry.id).limit(1))
            if next_id is None:
                return
            done.add(next_id)
            _process_one(next_id)


def _load(db, question_id: int) -> Inquiry | None:
    q = db.get(Inquiry, question_id)
    if q is None or question_status(q) != QuestionStatus.PROCESSING or q.conversation.ext is None:
        return None
    return q


def _process_one(question_id: int) -> None:
    s = get_settings()
    with SessionLocal() as db:
        q = _load(db, question_id)
        if q is None:
            return
        conv = q.conversation
        o = chat_order(conv)
        order = OrderContext(order_no=o.order_no, product_name=product_name(o)) if o else None
        request = AIRequest(inquiry_no=chat_no(conv), question=q.content, history=_history(conv, q), order=order)

    # AI 서버 호출 (DB 연결을 잡고 있지 않은 상태로 — 오래 걸릴 수 있음)
    result: AIResult | None = None
    error: str | None = None
    t0 = time.perf_counter()
    try:
        result = get_ai_client().answer(request)
    except Exception as e:  # 어떤 오류든 검토 대기로 넘겨서 고객 질문이 사라지지 않게 함
        error = f"{type(e).__name__}: {e}"[:2000]
        log.exception("AI 처리 실패: %s / 질문 %s", request.inquiry_no, question_id)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    decision = decide(result, error, s)

    with SessionLocal() as db:
        q = _load(db, question_id)
        if q is None:
            return
        now = utcnow()

        # AI 답변의 치환자({{…}})를 팀 DB 값으로 채움. 못 채운 것이 남으면 자동 전송하지 않고 관리자 검토로
        answer = (result.answer or "").strip() if result else ""
        if "{{" in answer:
            order = chat_order(q.conversation) or latest_order(db, q.customer_id)
            answer, unfilled = fill_placeholders(answer, db.get(Customer, q.customer_id), order)
            if unfilled:
                decision.auto_send = False
                decision.reasons.append(ReviewReason.UNFILLED_PLACEHOLDER)
                log.info("문의 %s: 채우지 못한 치환자 %s", q.inquiry_no, unfilled)

        analysis = AIAnalysis(
            inquiry_id=q.id,
            category=_label(result.category if result else None),
            intent=_label(result.intent if result else None),
            category_confidence=_confidence(result.category_confidence) if result else None,
            intent_confidence=_confidence(result.intent_confidence) if result else None,
            auto_response_allowed=decision.auto_send,
            decision_reason=reason_text(decision.reasons),
            decision_rule_version=f"backend-v1 (min_confidence={s.review_min_confidence})",
            analysis_model=(result.model or "unknown") if result else "ai-error",
            created_at=now,
        )
        db.add(analysis)
        db.flush()   # 답변의 자동 전송 검사(팀 DB 트리거)가 이 분석을 보므로 먼저 저장

        if answer:
            response = AIResponse(inquiry_id=q.id, analysis_id=analysis.id, response_text=answer,
                                  generation_model=result.model or "unknown",
                                  prompt_version=result.prompt_version or "unknown", created_at=now)
            if decision.auto_send:
                response.status, response.final_response_text = "SENT", answer
                response.sent_automatically, response.sent_at = True, now
            else:
                response.status = "REVIEW_PENDING"
            db.add(response)
            db.flush()
            linked: list[int] = []
            for src in result.sources:
                chunk_id = _chunk_id(db, src.document_key, src.chunk_index)
                if chunk_id is not None and chunk_id not in linked:
                    linked.append(chunk_id)
                    db.add(RetrievedPolicy(response_id=response.id, policy_chunk_id=chunk_id,
                                           similarity_score=src.score, rank=len(linked), created_at=now,
                                           score_metric=result.score_metric or "unspecified"))

        # 자동답변이면 팀 DB 트리거가 AUTO_ANSWERED로 바꿈 (같은 값으로 맞춰 둠). 자동 종료 타이머는 sent_at 기준
        q.status = "AUTO_ANSWERED" if decision.auto_send else "REVIEW_PENDING"
        db.commit()
        log.info("문의 %s: %s %s (%dms)", q.inquiry_no, q.status, [r.value for r in decision.reasons], latency_ms)


def resume_unfinished() -> int:
    """서버 시작 시 'AI 처리 중'으로 남은 질문을 다시 처리 (별도 스레드에서). 백엔드 채팅의 질문만."""
    with SessionLocal() as db:
        ids = list(db.scalars(select(Inquiry.id)
                              .join(ConversationExt, ConversationExt.conversation_id == Inquiry.conversation_id)
                              .where(Inquiry.status.in_(PROCESSING_STATUSES)).order_by(Inquiry.id)))
    if ids:
        log.info("미처리 질문 %d건 다시 처리 시작", len(ids))

        def _run():
            for i in ids:
                process_question(i)
        threading.Thread(target=_run, daemon=True, name="resume-questions").start()
    return len(ids)
