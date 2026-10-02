"""고객 질문 하나의 AI 처리 흐름

    고객 질문 (AI 처리 중) → AI 서버 (분류 + 정책 검색 + 답변 초안, 이전 대화·고른 주문 포함) → 자동답변 판단
        ├─ 신뢰도 기준 이상 → 챗봇 답변 메시지 추가 (자동답변) → 자동 종료 타이머 시작
        └─ 기준 미만 등   → 챗봇이 '담당자에게 전달했어요' 안내를 남기고 관리자 검토 대기
                             (입력은 계속 가능, 타이머 멈춤 → 관리자 답변이 같은 채팅에 붙음)

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
from app.enums import INTENT_TO_CATEGORY, ConversationStatus, MessageRole, QuestionStatus
from app.models import AIAnalysis, AIResponse, Message, PolicyChunk, PolicyDocument, RetrievedPolicy, utcnow
from app.plugins.ai_client import AIRequest, AIResult, HistoryItem, OrderContext, get_ai_client
from app.services.decision import decide

log = logging.getLogger(__name__)


def _history(db, question: Message) -> list[HistoryItem]:
    n = get_settings().ai_history_messages
    if n <= 0:
        return []
    # 이 질문보다 앞의 대화 + 그 사이에 붙은 앞 질문들의 답변 (뒤에 보낸 고객 질문과 안내 메시지는 제외)
    prev = db.scalars(select(Message)
                      .where(Message.conversation_id == question.conversation_id, Message.id != question.id,
                             Message.is_notice.is_(False),
                             ~((Message.role == MessageRole.CUSTOMER) & (Message.id > question.id)))
                      .order_by(Message.id.desc()).limit(n)).all()
    return [HistoryItem(role="customer" if m.role == MessageRole.CUSTOMER else "assistant", content=m.content)
            for m in reversed(prev)]


def _link_source(db, document_key: str | None, chunk_index: int | None) -> tuple[int | None, int | None]:
    """AI 서버가 돌려준 근거를 백엔드 DB의 정책 문서·조각과 연결 (못 찾으면 None)."""
    if not document_key:
        return None, None
    doc = db.scalar(select(PolicyDocument)
                    .where(PolicyDocument.doc_key == document_key, PolicyDocument.is_deleted.is_(False)))
    if doc is None:
        return None, None
    chunk_id = None
    if chunk_index is not None:
        chunk_id = db.scalar(select(PolicyChunk.id).where(PolicyChunk.policy_document_id == doc.id,
                                                          PolicyChunk.chunk_order == chunk_index))
    return doc.id, chunk_id


def is_first_question(db, question: Message) -> bool:
    first_id = db.scalar(select(Message.id)
                         .where(Message.conversation_id == question.conversation_id,
                                Message.role == MessageRole.CUSTOMER)
                         .order_by(Message.id).limit(1))
    return first_id == question.id


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
        q = db.get(Message, question_id)
        if q is None:
            return
        conversation_id = q.conversation_id
    with _conversation_lock(conversation_id):
        done: set[int] = set()
        while True:
            with SessionLocal() as db:
                next_id = db.scalar(select(Message.id)
                                    .where(Message.conversation_id == conversation_id,
                                           Message.question_status == QuestionStatus.PROCESSING,
                                           Message.id.not_in(done))
                                    .order_by(Message.id).limit(1))
            if next_id is None:
                return
            done.add(next_id)
            _process_one(next_id)


def _process_one(question_id: int) -> None:
    s = get_settings()
    with SessionLocal() as db:
        q = db.get(Message, question_id)
        if q is None or q.role != MessageRole.CUSTOMER or q.question_status != QuestionStatus.PROCESSING:
            return
        conv = q.conversation
        order = (OrderContext(order_no=conv.order_no, product_name=conv.product_name)
                 if conv.order_no and conv.product_name else None)
        request = AIRequest(inquiry_no=conv.inquiry_no, question=q.content, history=_history(db, q), order=order)

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
        q = db.get(Message, question_id)
        if q is None or q.question_status != QuestionStatus.PROCESSING:
            return
        conv = q.conversation
        q.analysis = None   # 다시 처리하는 경우를 대비해 이전 결과 정리
        q.draft = None
        q.retrieved.clear()
        db.flush()

        q.analysis = AIAnalysis(
            category=result.category if result else None,
            intent=result.intent if result else None,
            category_confidence=result.category_confidence if result else None,
            intent_confidence=result.intent_confidence if result else None,
            auto_response_allowed=decision.auto_send,
            decision_reasons=[r.value for r in decision.reasons],
            error_message=error,
            analysis_model=result.model if result else None,
        )
        answer = (result.answer or "").strip() if result else ""
        if answer:
            q.draft = AIResponse(response_text=answer, generation_model=result.model,
                                 prompt_version=result.prompt_version, sent_automatically=decision.auto_send,
                                 latency_ms=latency_ms)
        if result is not None:
            for rank, src in enumerate(result.sources, start=1):
                doc_id, chunk_id = _link_source(db, src.document_key, src.chunk_index)
                q.retrieved.append(RetrievedPolicy(
                    policy_document_id=doc_id, policy_chunk_id=chunk_id, document_key=src.document_key,
                    document_title=src.title[:200], chunk_text=src.text, similarity_score=src.score, rank=rank))

        # 채팅 목록에 보일 문의 유형 = 첫 질문의 분류
        if (conv.category is None and result is not None and result.intent in INTENT_TO_CATEGORY
                and is_first_question(db, q)):
            conv.intent = result.intent
            conv.category = INTENT_TO_CATEGORY[result.intent]

        now = utcnow()
        if decision.auto_send:
            conv.messages.append(Message(role=MessageRole.BOT, content=answer, reply_to_id=q.id, created_at=now))
            q.question_status = QuestionStatus.AUTO_ANSWERED
            q.answered_at = now
            if conv.status == ConversationStatus.OPEN:
                conv.last_answer_at = now      # 자동 종료 타이머 시작
        else:
            q.question_status = QuestionStatus.REVIEW_PENDING
            conv.messages.append(Message(role=MessageRole.BOT, content=s.review_notice_message, reply_to_id=q.id,
                                         is_notice=True, created_at=now))
        db.commit()
        log.info("문의 %s 질문 %s: %s %s", conv.inquiry_no, q.id, q.question_status.value,
                 [r.value for r in decision.reasons])


def resume_unfinished() -> int:
    """서버 시작 시 'AI 처리 중'으로 남은 질문을 다시 처리 (별도 스레드에서)."""
    with SessionLocal() as db:
        ids = list(db.scalars(select(Message.id)
                              .where(Message.question_status == QuestionStatus.PROCESSING).order_by(Message.id)))
    if ids:
        log.info("미처리 질문 %d건 다시 처리 시작", len(ids))

        def _run():
            for i in ids:
                process_question(i)
        threading.Thread(target=_run, daemon=True, name="resume-questions").start()
    return len(ids)
