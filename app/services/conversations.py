"""고객 채팅 — 새 채팅(주문 상품 선택), 메시지 보내기, 채팅 종료, 문의 내역, 자동 종료.

규칙
- 고객이 보낸 메시지는 수정·취소할 수 없습니다 (그런 기능 자체가 없음).
- 고객당 진행 중인 채팅은 1개. 새 채팅을 시작하면 이전 채팅은 저장된 채로 '새 채팅 시작'으로 종료됩니다.
- 입력이 막히는 건 상담이 종료됐을 때뿐입니다 (채팅 종료하기, 새 채팅하기, 5분 무응답).
  AI가 답을 만드는 중이거나 검토 대기 중에도 계속 보낼 수 있고, 질문은 보낸 순서대로 답합니다 (pipeline.py).
- 관리자 검토 답변은 같은 채팅에 상담원 답변으로 붙고, 상담은 계속됩니다 (reviews.py).
- 챗봇·상담원 답변이 표시된 뒤 고객이 CHAT_IDLE_TIMEOUT_MINUTES 동안 말이 없으면 자동 종료됩니다.
  AI 처리 중이거나 검토 대기 중인 질문이 있으면 타이머가 멈춥니다.

팀 DB에 저장하는 방식
- 채팅 = conversation (+ 백엔드 전용 conversation_ext: 문의번호, 고른 주문, 문의 유형, 타이머, 종료 사유)
- 고객 질문 = inquiry (문의번호-순서, 예: Q20261002-001-01)
- 챗봇·상담원 답변 = ai_response 중 status=SENT 인 것 (sent_automatically로 챗봇/상담원 구분)
- '담당자에게 전달했어요' 안내는 저장하지 않고, 자동답변이 허용되지 않은 분석(ai_analysis)이 있으면 그 시각에 표시

문의 상태
- 고객 화면: 상담 중(CHATTING) / 답변완료(ANSWERED). 검토 대기 중인 문의는 관리자가 답할 때까지 '상담 중'
- 관리자 화면: 검토대기(REVIEWING) / 상담 중(CHATTING) / 답변완료(ANSWERED) — status_code()
"""
import logging
import threading
from datetime import timedelta

from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.enums import (
    ANSWERED_BY_LABELS,
    CLOSE_REASON_LABELS,
    CUSTOMER_STATUS_LABELS,
    QUESTION_STATUS_FROM_DB,
    CloseReason,
    ConversationStatus,
    MessageRole,
    QuestionStatus,
    db_statuses,
    display_category,
)
from app.errors import AppError
from app.models import AIAnalysis, AIResponse, Conversation, ConversationExt, Customer, Inquiry, utcnow
from app.plugins.order_source import get_order_source
from app.schemas import ChatMessageOut, ConversationDetail, ConversationListOut, ConversationSummary, OrderOut, Page
from app.utils import as_utc, kst_today

log = logging.getLogger(__name__)

OPEN, CLOSED = ConversationStatus.OPEN.value, ConversationStatus.CLOSED.value


def _has_question(status: QuestionStatus):
    return exists().where(Inquiry.conversation_id == Conversation.id, Inquiry.status.in_(db_statuses(status)))


# 문의 상태별 SQL 조건 (목록 거르기·건수 세기용)
HAS_PENDING = _has_question(QuestionStatus.REVIEW_PENDING)
HAS_PROCESSING = _has_question(QuestionStatus.PROCESSING)
# 관리자 화면 상태
STATUS_CONDITIONS = {
    "REVIEWING": [HAS_PENDING],
    "CHATTING": [~HAS_PENDING, Conversation.status == OPEN],
    "ANSWERED": [~HAS_PENDING, Conversation.status == CLOSED],
}
# 고객 화면 상태 (검토 대기 중이면 '상담 중')
CUSTOMER_STATUS_CONDITIONS = {
    "CHATTING": [or_(HAS_PENDING, Conversation.status == OPEN)],
    "ANSWERED": STATUS_CONDITIONS["ANSWERED"],
}


def chats():
    """백엔드 채팅(conversation_ext가 있는 것)만 고르는 기본 쿼리."""
    return select(Conversation).join(Conversation.ext)


def count_chats(*cond) -> int:
    return select(func.count(Conversation.id)).join(Conversation.ext).where(*cond)


# ───────────────────────── 질문 하나 ─────────────────────────

def question_status(q: Inquiry) -> QuestionStatus:
    return QUESTION_STATUS_FROM_DB.get(q.status, QuestionStatus.PROCESSING)


def latest_analysis(q: Inquiry) -> AIAnalysis | None:
    return q.analyses[-1] if q.analyses else None


def sent_response(q: Inquiry) -> AIResponse | None:
    """고객에게 나간 답변 (팀 DB 규칙상 질문당 최대 1개)."""
    return next((r for r in q.responses if r.status == "SENT"), None)


def question_answered_at(q: Inquiry):
    r = sent_response(q)
    return r.sent_at if r else None


# ───────────────────────── 상태 계산 ─────────────────────────

def questions_of(conv: Conversation) -> list[Inquiry]:
    return list(conv.inquiries)


def has_question(conv: Conversation, status: QuestionStatus) -> bool:
    return any(question_status(q) == status for q in conv.inquiries)


def status_code(conv: Conversation) -> str:
    """관리자 화면 상태: 검토대기 / 상담 중 / 답변완료."""
    if has_question(conv, QuestionStatus.REVIEW_PENDING):
        return "REVIEWING"
    return "CHATTING" if conv.status == OPEN else "ANSWERED"


def customer_status_code(conv: Conversation) -> str:
    """고객 화면 상태: 상담이 끝났고 검토 대기 질문이 없으면 답변완료, 아니면 상담 중."""
    return "ANSWERED" if status_code(conv) == "ANSWERED" else "CHATTING"


def _sent(conv: Conversation) -> list[AIResponse]:
    return sorted((r for q in conv.inquiries if (r := sent_response(q))), key=lambda r: as_utc(r.sent_at))


def answered_by(conv: Conversation) -> str | None:
    """답변완료일 때 누가 답했는지: 관리자 답변이 하나라도 있으면 ADMIN, 아니면 AI."""
    if status_code(conv) != "ANSWERED":
        return None
    sent = _sent(conv)
    if any(not r.sent_automatically for r in sent):
        return "ADMIN"
    return "AI" if sent else None


def answered_at(conv: Conversation):
    sent = _sent(conv)
    return sent[-1].sent_at if sent else None


def admin_answer(conv: Conversation) -> str | None:
    admin = [r for r in _sent(conv) if not r.sent_automatically]
    return admin[-1].final_response_text if admin else None


def close_reason(conv: Conversation) -> CloseReason | None:
    return CloseReason(conv.ext.close_reason) if conv.ext.close_reason else None


def close_reason_label(conv: Conversation) -> str | None:
    reason = close_reason(conv)
    return CLOSE_REASON_LABELS[reason] if reason else None


def auto_close_at(conv: Conversation):
    """자동 종료 예정 시각. 타이머가 멈춘 상태(답변 전, AI 처리 중, 검토 대기, 종료됨)면 None."""
    if (conv.status != OPEN or conv.ext.last_answer_at is None
            or has_question(conv, QuestionStatus.PROCESSING) or has_question(conv, QuestionStatus.REVIEW_PENDING)):
        return None
    return as_utc(conv.ext.last_answer_at) + timedelta(minutes=get_settings().chat_idle_timeout_minutes)


def _close(conv: Conversation, reason: CloseReason, at=None) -> None:
    if conv.status == OPEN:
        conv.status = CLOSED
        conv.closed_at = at or utcnow()
        conv.ext.close_reason = reason.value


def close_if_idle(conv: Conversation) -> bool:
    deadline = auto_close_at(conv)
    if deadline is not None and deadline <= utcnow():
        _close(conv, CloseReason.TIMEOUT, at=deadline)    # 실제로 시간이 다 된 시각으로 기록
        return True
    return False


def close_idle_conversations() -> int:
    """자동 종료 대상 채팅을 한 번에 종료 (백그라운드에서 주기적으로 실행)."""
    with SessionLocal() as db:
        convs = db.scalars(chats().where(Conversation.status == OPEN, ConversationExt.last_answer_at.is_not(None),
                                         ~HAS_PENDING, ~HAS_PROCESSING)).all()
        closed = sum(close_if_idle(c) for c in convs)
        if closed:
            db.commit()
    return closed


def start_timeout_sweeper(stop: threading.Event) -> threading.Thread | None:
    interval = get_settings().chat_timeout_sweep_seconds
    if interval <= 0:
        return None

    def _run():
        while not stop.wait(interval):
            try:
                n = close_idle_conversations()
                if n:
                    log.info("응답 없음으로 채팅 %d건 자동 종료", n)
            except Exception:
                log.exception("자동 종료 확인 실패")
    t = threading.Thread(target=_run, daemon=True, name="chat-timeout-sweeper")
    t.start()
    return t


# ───────────────────────── 화면용 변환 ─────────────────────────

def transcript(conv: Conversation) -> list[ChatMessageOut]:
    """고객이 보는 채팅 내역 (시간순): 질문, '담당자에게 전달했어요' 안내, 챗봇·상담원 답변."""
    notice = get_settings().review_notice_message
    events = []
    for q in conv.inquiries:
        events.append((q.created_at, q.id, 0, ChatMessageOut(
            id=f"q{q.id}", question_id=q.id, role=MessageRole.CUSTOMER, content=q.content, created_at=q.created_at,
            is_notice=False, question_status=question_status(q))))
        a = latest_analysis(q)
        if a is not None and not a.auto_response_allowed:
            events.append((a.created_at, q.id, 1, ChatMessageOut(
                id=f"n{a.id}", question_id=q.id, role=MessageRole.BOT, content=notice, created_at=a.created_at,
                is_notice=True, question_status=None)))
        r = sent_response(q)
        if r is not None:
            events.append((r.sent_at, q.id, 2, ChatMessageOut(
                id=f"r{r.id}", question_id=q.id, role=MessageRole.BOT if r.sent_automatically else MessageRole.ADMIN,
                content=r.final_response_text, created_at=r.sent_at, is_notice=False, question_status=None)))
    events.sort(key=lambda e: (as_utc(e[0]), e[1], e[2]))
    return [e[3] for e in events]


def to_detail(conv: Conversation) -> ConversationDetail:
    code, by, ext = customer_status_code(conv), answered_by(conv), conv.ext
    return ConversationDetail(
        inquiry_no=ext.inquiry_no, status=conv.status, status_code=code, status_label=CUSTOMER_STATUS_LABELS[code],
        answered_by=by, answered_by_label=ANSWERED_BY_LABELS.get(by) if by else None,
        category=display_category(ext.category), order_no=ext.order_no, product_name=ext.product_name,
        created_at=conv.created_at, answered_at=answered_at(conv), closed_at=conv.closed_at,
        close_reason=close_reason(conv), close_reason_label=close_reason_label(conv),
        admin_answer=admin_answer(conv),
        input_locked=conv.status == CLOSED,
        answering=has_question(conv, QuestionStatus.PROCESSING),
        review_pending=has_question(conv, QuestionStatus.REVIEW_PENDING),
        auto_close_at=auto_close_at(conv),
        messages=transcript(conv),
    )


def to_summary(conv: Conversation) -> ConversationSummary:
    code, by, ext = customer_status_code(conv), answered_by(conv), conv.ext
    messages = transcript(conv)
    return ConversationSummary(
        inquiry_no=ext.inquiry_no, preview=conv.inquiries[0].content if conv.inquiries else "",
        category=display_category(ext.category), product_name=ext.product_name,
        status_code=code, status_label=CUSTOMER_STATUS_LABELS[code],
        answered_by=by, answered_by_label=ANSWERED_BY_LABELS.get(by) if by else None,
        created_at=conv.created_at, last_message_at=messages[-1].created_at if messages else conv.created_at)


# ───────────────────────── 기능 ─────────────────────────

def list_orders(db: Session, customer: Customer) -> list[OrderOut]:
    return [OrderOut(order_no=o.order_no, product_name=o.product_name, ordered_at=o.ordered_at)
            for o in get_order_source().list_orders(db, customer)]


def _next_inquiry_no(db: Session) -> str:
    """Q20261002-001 형식. 하루 999건을 넘으면 1000, 1001 …로 자릿수가 늘어납니다."""
    prefix = f"{get_settings().inquiry_no_prefix}{kst_today():%Y%m%d}-"
    today = db.scalars(select(ConversationExt.inquiry_no).where(ConversationExt.inquiry_no.like(prefix + "%"))).all()
    seq = max((int(no.rsplit("-", 1)[1]) for no in today), default=0) + 1
    return f"{prefix}{seq:03d}"


def _question_no(chat_no: str, turn: int) -> str:
    """팀 DB inquiry.inquiry_no — 채팅 문의번호 + 질문 순서 (Q20261002-001-01)."""
    return f"{chat_no}-{turn:02d}"


def _close_open_chats(db: Session, customer_id: int) -> None:
    for old in db.scalars(chats().where(Conversation.customer_id == customer_id, Conversation.status == OPEN)):
        _close(old, CloseReason.NEW_CHAT)


def get_owned(db: Session, customer: Customer, inquiry_no: str) -> Conversation:
    conv = db.scalar(chats().where(ConversationExt.inquiry_no == inquiry_no.strip(),
                                   Conversation.customer_id == customer.id))
    if conv is None:
        raise AppError(404, "CONVERSATION_NOT_FOUND", "문의를 찾을 수 없습니다.")
    if close_if_idle(conv):
        db.commit()
    return conv


def get_current(db: Session, customer: Customer) -> Conversation | None:
    conv = db.scalar(chats().where(Conversation.customer_id == customer.id, Conversation.status == OPEN)
                     .order_by(Conversation.id.desc()))
    if conv is not None and close_if_idle(conv):
        db.commit()
        return None
    return conv


def start_conversation(db: Session, customer: Customer, content: str, order_no: str | None = None,
                       is_demo: bool = False) -> tuple[Conversation, Inquiry]:
    """새 채팅 시작 (+ 고른 주문 상품). 진행 중이던 채팅은 저장된 채로 '새 채팅 시작'으로 종료."""
    order = None
    if order_no:
        order = get_order_source().get_order(db, customer, order_no.strip())
        if order is None:
            raise AppError(404, "ORDER_NOT_FOUND", "주문을 찾을 수 없습니다.")
    order_id = order.order_id if order else None
    for _ in range(5):   # 동시에 시작돼 문의번호가 겹치면 다시 시도
        _close_open_chats(db, customer.id)
        chat_no = _next_inquiry_no(db)
        conv = Conversation(customer_id=customer.id, title=content[:50], status=OPEN, created_at=utcnow())
        conv.ext = ConversationExt(inquiry_no=chat_no, is_demo=is_demo, order_id=order_id,
                                   order_no=order.order_no if order else None,
                                   product_name=order.product_name if order else None)
        question = Inquiry(inquiry_no=_question_no(chat_no, 1), customer_id=customer.id, order_id=order_id,
                           content=content, created_at=conv.created_at)
        conv.inquiries.append(question)
        db.add(conv)
        try:
            db.commit()
            return conv, question
        except IntegrityError:
            db.rollback()
    raise AppError(503, "INQUIRY_NO_CONFLICT", "잠시 후 다시 시도해 주세요.")


def add_message(db: Session, customer: Customer, inquiry_no: str, content: str) -> Inquiry:
    conv = get_owned(db, customer, inquiry_no)
    if conv.status == CLOSED:
        raise AppError(409, "CONVERSATION_CLOSED", "종료된 상담입니다. 새 채팅으로 문의해 주세요.")
    conv_id, chat_no, order_id = conv.id, conv.ext.inquiry_no, conv.ext.order_id
    for _ in range(5):   # 동시에 보내서 질문 순서 번호가 겹치면 다시 시도
        turn = (db.scalar(select(func.count(Inquiry.id)).where(Inquiry.conversation_id == conv_id)) or 0) + 1
        question = Inquiry(inquiry_no=_question_no(chat_no, turn), conversation_id=conv_id, customer_id=customer.id,
                           order_id=order_id, content=content)
        db.add(question)
        try:
            db.commit()
            return question
        except IntegrityError:
            db.rollback()
    raise AppError(503, "INQUIRY_NO_CONFLICT", "잠시 후 다시 시도해 주세요.")


def close_conversation(db: Session, customer: Customer, inquiry_no: str, reason: CloseReason) -> Conversation:
    """채팅 종료 (이미 종료됐으면 그대로). 검토 대기 중에도 종료할 수 있고, 관리자 답변은 나중에 문의 내역에서 보입니다."""
    conv = get_owned(db, customer, inquiry_no)
    _close(conv, reason)
    db.commit()
    return conv


def list_conversations(db: Session, customer: Customer, status: str | None, q: str | None,
                       page: int, size: int) -> ConversationListOut:
    close_idle_conversations()
    cond = [Conversation.customer_id == customer.id, *CUSTOMER_STATUS_CONDITIONS.get(status, [])]
    if q and q.strip():
        kw = q.strip()
        cond.append(or_(ConversationExt.product_name.contains(kw),
                        exists().where(Inquiry.conversation_id == Conversation.id, Inquiry.content.contains(kw))))
    total = db.scalar(count_chats(*cond)) or 0
    rows = db.scalars(chats().where(*cond).order_by(Conversation.created_at.desc(), Conversation.id.desc())
                      .offset((page - 1) * size).limit(size)).all()
    return ConversationListOut(items=[to_summary(c) for c in rows], page=Page(page=page, size=size, total=total))
