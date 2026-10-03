"""고객 질문 하나의 AI 처리 흐름

    고객 질문 (AI 처리 중) → AI 서버 (분류 + 정책 검색 + 답변 초안, 이전 대화·고른 주문 포함) → 자동답변 판단
        ├─ 신뢰도 기준 이상 → 챗봇 답변 전송 (ai_response status=SENT, 자동) → 자동 종료 타이머 시작
        └─ 기준 미만 등   → 초안만 저장 (ai_response status=REVIEW_PENDING), 고객 화면엔 '담당자에게 전달했어요' 안내
                             (입력은 계속 가능, 타이머 멈춤 → 관리자 답변이 같은 채팅에 붙음)

팀 DB에 남는 것: ai_analysis(분류·판단, 질문마다 1행) + ai_response(초안·전송) + retrieved_policy(근거)
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
from app.enums import INTENT_TO_CATEGORY, REVIEW_REASON_LABELS, UNCLASSIFIED, MessageRole, QuestionStatus, db_statuses
from app.models import (
    AIAnalysis,
    AIResponse,
    AnalysisExt,
    Conversation,
    ConversationExt,
    Inquiry,
    PolicyChunk,
    PolicyDocument,
    RetrievedPolicy,
    utcnow,
)
from app.plugins.ai_client import AIRequest, AIResult, HistoryItem, OrderContext, get_ai_client
from app.services.conversations import OPEN, question_status, transcript
from app.services.decision import decide

log = logging.getLogger(__name__)

PROCESSING_STATUSES = db_statuses(QuestionStatus.PROCESSING)


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


def is_first_question(conv: Conversation, question: Inquiry) -> bool:
    return bool(conv.inquiries) and conv.inquiries[0].id == question.id


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
        conv, ext = q.conversation, q.conversation.ext
        order = (OrderContext(order_no=ext.order_no, product_name=ext.product_name)
                 if ext.order_no and ext.product_name else None)
        request = AIRequest(inquiry_no=ext.inquiry_no, question=q.content, history=_history(conv, q), order=order)

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
        conv, ext = q.conversation, q.conversation.ext
        now = utcnow()

        analysis = AIAnalysis(
            inquiry_id=q.id,
            category=_label(result.category if result else None),
            intent=_label(result.intent if result else None),
            category_confidence=_confidence(result.category_confidence) if result else None,
            intent_confidence=_confidence(result.intent_confidence) if result else None,
            auto_response_allowed=decision.auto_send,
            decision_reason=", ".join(REVIEW_REASON_LABELS[r] for r in decision.reasons) or "자동답변 기준 충족",
            decision_rule_version=f"backend-v1 (min_confidence={s.review_min_confidence})",
            analysis_model=(result.model or "unknown") if result else "ai-error",
            created_at=now,
        )
        analysis.ext = AnalysisExt(reasons=[r.value for r in decision.reasons], error_message=error,
                                   latency_ms=latency_ms)
        db.add(analysis)
        db.flush()   # 답변의 자동 전송 검사(팀 DB 트리거)가 이 분석을 보므로 먼저 저장

        answer = (result.answer or "").strip() if result else ""
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

        # 채팅 목록에 보일 문의 유형 = 첫 질문의 분류
        if (ext.category is None and result is not None and result.intent in INTENT_TO_CATEGORY
                and is_first_question(conv, q)):
            ext.intent = result.intent
            ext.category = INTENT_TO_CATEGORY[result.intent]

        if decision.auto_send:
            q.status = "AUTO_ANSWERED"     # 팀 DB 트리거도 같은 값으로 바꿈
            if conv.status == OPEN:
                ext.last_answer_at = now   # 자동 종료 타이머 시작
        else:
            q.status = "REVIEW_PENDING"
        db.commit()
        log.info("문의 %s 질문 %s: %s %s", ext.inquiry_no, q.id, q.status, [r.value for r in decision.reasons])


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
