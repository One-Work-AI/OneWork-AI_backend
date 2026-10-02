"""고객 채팅 — 새 채팅(주문 상품 선택), 메시지 보내기, 채팅 종료, 문의 내역, 자동 종료.

규칙
- 고객이 보낸 메시지는 수정·취소할 수 없습니다 (그런 기능 자체가 없음).
- 고객당 진행 중인 채팅은 1개. 새 채팅을 시작하면 이전 채팅은 저장된 채로 '새 채팅 시작'으로 종료됩니다.
- 입력이 막히는 건 상담이 종료됐을 때뿐입니다 (채팅 종료하기, 새 채팅하기, 5분 무응답).
  AI가 답을 만드는 중이거나 검토 대기 중에도 계속 보낼 수 있고, 질문은 보낸 순서대로 답합니다 (pipeline.py).
- 관리자 검토 답변은 같은 채팅에 상담원 답변으로 붙고, 상담은 계속됩니다 (reviews.py).
- 챗봇·상담원 답변이 표시된 뒤 고객이 CHAT_IDLE_TIMEOUT_MINUTES 동안 말이 없으면 자동 종료됩니다.
  AI 처리 중이거나 검토 대기 중인 질문이 있으면 타이머가 멈춥니다.

문의 상태
- 고객 화면: 상담 중(CHATTING) / 답변완료(ANSWERED). 검토 대기 중인 문의는 관리자가 답할 때까지 '상담 중'
- 관리자 화면: 검토대기(REVIEWING) / 상담 중(CHATTING) / 답변완료(ANSWERED) — status_code()
"""
import logging
import threading
from datetime import timedelta

from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.db import SessionLocal
from app.enums import (
    ANSWERED_BY_LABELS,
    CLOSE_REASON_LABELS,
    CUSTOMER_STATUS_LABELS,
    CloseReason,
    ConversationStatus,
    MessageRole,
    QuestionStatus,
    display_category,
)
from app.errors import AppError
from app.models import Conversation, Customer, Message, utcnow
from app.plugins.order_source import get_order_source
from app.schemas import ChatMessageOut, ConversationDetail, ConversationListOut, ConversationSummary, OrderOut, Page
from app.utils import as_utc, kst_today

log = logging.getLogger(__name__)


def _has_question(status: QuestionStatus):
    return exists().where(Message.conversation_id == Conversation.id, Message.question_status == status)


# 문의 상태별 SQL 조건 (목록 거르기·건수 세기용)
HAS_PENDING = _has_question(QuestionStatus.REVIEW_PENDING)
HAS_PROCESSING = _has_question(QuestionStatus.PROCESSING)
# 관리자 화면 상태
STATUS_CONDITIONS = {
    "REVIEWING": [HAS_PENDING],
    "CHATTING": [~HAS_PENDING, Conversation.status == ConversationStatus.OPEN],
    "ANSWERED": [~HAS_PENDING, Conversation.status == ConversationStatus.CLOSED],
}
# 고객 화면 상태 (검토 대기 중이면 '상담 중')
CUSTOMER_STATUS_CONDITIONS = {
    "CHATTING": [or_(HAS_PENDING, Conversation.status == ConversationStatus.OPEN)],
    "ANSWERED": STATUS_CONDITIONS["ANSWERED"],
}


# ───────────────────────── 상태 계산 ─────────────────────────

def questions_of(conv: Conversation) -> list[Message]:
    return [m for m in conv.messages if m.role == MessageRole.CUSTOMER]


def status_code(conv: Conversation) -> str:
    """관리자 화면 상태: 검토대기 / 상담 중 / 답변완료."""
    if has_question(conv, QuestionStatus.REVIEW_PENDING):
        return "REVIEWING"
    return "CHATTING" if conv.status == ConversationStatus.OPEN else "ANSWERED"


def customer_status_code(conv: Conversation) -> str:
    """고객 화면 상태: 상담이 끝났고 검토 대기 질문이 없으면 답변완료, 아니면 상담 중."""
    return "ANSWERED" if status_code(conv) == "ANSWERED" else "CHATTING"


def answered_by(conv: Conversation) -> str | None:
    """답변완료일 때 누가 답했는지: 관리자 답변이 하나라도 있으면 ADMIN, 아니면 AI."""
    if status_code(conv) != "ANSWERED":
        return None
    if any(m.role == MessageRole.ADMIN for m in conv.messages):
        return "ADMIN"
    if any(m.role == MessageRole.BOT and not m.is_notice for m in conv.messages):
        return "AI"
    return None


def answered_at(conv: Conversation):
    times = [q.answered_at for q in questions_of(conv) if q.answered_at]
    return max(times, key=as_utc) if times else None


def admin_answer(conv: Conversation) -> str | None:
    admin_msgs = [m for m in conv.messages if m.role == MessageRole.ADMIN]
    return admin_msgs[-1].content if admin_msgs else None


def has_question(conv: Conversation, status: QuestionStatus) -> bool:
    return any(q.question_status == status for q in questions_of(conv))


def auto_close_at(conv: Conversation):
    """자동 종료 예정 시각. 타이머가 멈춘 상태(답변 전, AI 처리 중, 검토 대기, 종료됨)면 None."""
    if (conv.status != ConversationStatus.OPEN or conv.last_answer_at is None
            or has_question(conv, QuestionStatus.PROCESSING) or has_question(conv, QuestionStatus.REVIEW_PENDING)):
        return None
    return as_utc(conv.last_answer_at) + timedelta(minutes=get_settings().chat_idle_timeout_minutes)


def close_if_idle(conv: Conversation) -> bool:
    deadline = auto_close_at(conv)
    if deadline is not None and deadline <= utcnow():
        conv.status = ConversationStatus.CLOSED
        conv.close_reason = CloseReason.TIMEOUT
        conv.closed_at = deadline          # 실제로 시간이 다 된 시각으로 기록
        return True
    return False


def close_idle_conversations() -> int:
    """자동 종료 대상 채팅을 한 번에 종료 (백그라운드에서 주기적으로 실행)."""
    with SessionLocal() as db:
        convs = db.scalars(select(Conversation)
                           .where(Conversation.status == ConversationStatus.OPEN,
                                  Conversation.last_answer_at.is_not(None), ~HAS_PENDING, ~HAS_PROCESSING)
                           .options(selectinload(Conversation.messages))).all()
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
    return [ChatMessageOut(id=m.id, role=m.role, content=m.content, created_at=m.created_at,
                           reply_to_id=m.reply_to_id, is_notice=m.is_notice, question_status=m.question_status)
            for m in conv.messages]


def to_detail(conv: Conversation) -> ConversationDetail:
    code, by = customer_status_code(conv), answered_by(conv)
    return ConversationDetail(
        inquiry_no=conv.inquiry_no, status=conv.status, status_code=code, status_label=CUSTOMER_STATUS_LABELS[code],
        answered_by=by, answered_by_label=ANSWERED_BY_LABELS.get(by) if by else None,
        category=display_category(conv.category), order_no=conv.order_no, product_name=conv.product_name,
        created_at=conv.created_at, answered_at=answered_at(conv), closed_at=conv.closed_at,
        close_reason=conv.close_reason,
        close_reason_label=CLOSE_REASON_LABELS[conv.close_reason] if conv.close_reason else None,
        admin_answer=admin_answer(conv),
        input_locked=conv.status == ConversationStatus.CLOSED,
        answering=has_question(conv, QuestionStatus.PROCESSING),
        review_pending=has_question(conv, QuestionStatus.REVIEW_PENDING),
        auto_close_at=auto_close_at(conv),
        messages=transcript(conv),
    )


def to_summary(conv: Conversation) -> ConversationSummary:
    code, by = customer_status_code(conv), answered_by(conv)
    first_q = next(iter(questions_of(conv)), None)
    return ConversationSummary(
        inquiry_no=conv.inquiry_no, preview=first_q.content if first_q else "",
        category=display_category(conv.category), product_name=conv.product_name,
        status_code=code, status_label=CUSTOMER_STATUS_LABELS[code],
        answered_by=by, answered_by_label=ANSWERED_BY_LABELS.get(by) if by else None,
        created_at=conv.created_at, last_message_at=conv.messages[-1].created_at if conv.messages else conv.created_at)


# ───────────────────────── 기능 ─────────────────────────

def list_orders(db: Session, customer: Customer) -> list[OrderOut]:
    return [OrderOut(**o.model_dump()) for o in get_order_source().list_orders(db, customer)]


def _next_inquiry_no(db: Session) -> str:
    """Q20261002-001 형식. 하루 999건을 넘으면 1000, 1001 …로 자릿수가 늘어납니다."""
    prefix = f"{get_settings().inquiry_no_prefix}{kst_today():%Y%m%d}-"
    today = db.scalars(select(Conversation.inquiry_no).where(Conversation.inquiry_no.like(prefix + "%"))).all()
    seq = max((int(no.rsplit("-", 1)[1]) for no in today), default=0) + 1
    return f"{prefix}{seq:03d}"


def _close(conv: Conversation, reason: CloseReason) -> None:
    if conv.status == ConversationStatus.OPEN:
        conv.status = ConversationStatus.CLOSED
        conv.close_reason = reason
        conv.closed_at = utcnow()


def _close_open_chats(db: Session, customer_id: int) -> None:
    for old in db.scalars(select(Conversation).where(Conversation.customer_id == customer_id,
                                                     Conversation.status == ConversationStatus.OPEN)):
        _close(old, CloseReason.NEW_CHAT)


def get_owned(db: Session, customer: Customer, inquiry_no: str) -> Conversation:
    conv = db.scalar(select(Conversation).where(Conversation.inquiry_no == inquiry_no.strip(),
                                                Conversation.customer_id == customer.id))
    if conv is None:
        raise AppError(404, "CONVERSATION_NOT_FOUND", "문의를 찾을 수 없습니다.")
    if close_if_idle(conv):
        db.commit()
    return conv


def get_current(db: Session, customer: Customer) -> Conversation | None:
    conv = db.scalar(select(Conversation).where(Conversation.customer_id == customer.id,
                                                Conversation.status == ConversationStatus.OPEN)
                     .order_by(Conversation.id.desc()))
    if conv is not None and close_if_idle(conv):
        db.commit()
        return None
    return conv


def start_conversation(db: Session, customer: Customer, content: str, order_no: str | None = None,
                       is_demo: bool = False) -> tuple[Conversation, Message]:
    """새 채팅 시작 (+ 고른 주문 상품). 진행 중이던 채팅은 저장된 채로 '새 채팅 시작'으로 종료."""
    order = None
    if order_no:
        order = get_order_source().get_order(db, customer, order_no.strip())
        if order is None:
            raise AppError(404, "ORDER_NOT_FOUND", "주문을 찾을 수 없습니다.")
    for _ in range(5):   # 동시에 시작돼 문의번호가 겹치면 다시 시도
        _close_open_chats(db, customer.id)
        conv = Conversation(inquiry_no=_next_inquiry_no(db), customer_id=customer.id, is_demo=is_demo,
                            order_no=order.order_no if order else None,
                            product_name=order.product_name if order else None)
        question = Message(role=MessageRole.CUSTOMER, content=content, question_status=QuestionStatus.PROCESSING)
        conv.messages.append(question)
        db.add(conv)
        try:
            db.commit()
            return conv, question
        except IntegrityError:
            db.rollback()
    raise AppError(503, "INQUIRY_NO_CONFLICT", "잠시 후 다시 시도해 주세요.")


def add_message(db: Session, customer: Customer, inquiry_no: str, content: str) -> Message:
    conv = get_owned(db, customer, inquiry_no)
    if conv.status == ConversationStatus.CLOSED:
        raise AppError(409, "CONVERSATION_CLOSED", "종료된 상담입니다. 새 채팅으로 문의해 주세요.")
    question = Message(role=MessageRole.CUSTOMER, content=content, question_status=QuestionStatus.PROCESSING)
    conv.messages.append(question)
    db.commit()
    return question


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
        cond.append(or_(Conversation.product_name.contains(kw),
                        exists().where(Message.conversation_id == Conversation.id,
                                       Message.role == MessageRole.CUSTOMER, Message.content.contains(kw))))
    total = db.scalar(select(func.count(Conversation.id)).where(*cond)) or 0
    rows = db.scalars(select(Conversation).where(*cond).order_by(Conversation.created_at.desc(), Conversation.id.desc())
                      .options(selectinload(Conversation.messages)).offset((page - 1) * size).limit(size)).all()
    return ConversationListOut(items=[to_summary(c) for c in rows], page=Page(page=page, size=size, total=total))
