"""고객 채팅 — 새 채팅, 메시지 보내기, 채팅 종료, 문의 내역, 자동 종료.

규칙
- 고객이 보낸 메시지는 수정·취소할 수 없습니다 (그런 기능 자체가 없음).
- 한 고객이 채팅을 여러 개 동시에 진행할 수 있습니다 (체험 입장은 모두 같은 고객 계정을 쓰므로 서로의 채팅을 끊지 않게).
  새 채팅을 시작해도 다른 채팅은 그대로 상담 중이고, 채팅마다 따로 종료됩니다.
- 입력이 막히는 건 상담이 종료됐을 때뿐입니다 (채팅 종료하기, 새 채팅하기, 5분 무응답).
  AI가 답을 만드는 중이거나 검토 대기 중에도 계속 보낼 수 있고, 질문은 보낸 순서대로 답합니다 (pipeline.py).
- 관리자 검토 답변은 같은 채팅에 상담원 답변으로 붙고, 상담은 계속됩니다 (reviews.py).
- 챗봇·상담원 답변이 표시된 뒤 고객이 CHAT_IDLE_TIMEOUT_MINUTES 동안 말이 없으면 자동 종료됩니다.
  AI 처리 중이거나 검토 대기 중인 질문이 있으면 타이머가 멈춥니다.
- 주문 상품은 선택 항목입니다 (화면에서는 뺐음). 고르면 inquiry.order_id에 연결됩니다.

팀 DB에 저장하는 방식 (models.py 위쪽 설명 참고)
- 채팅 = conversation (+ 백엔드 전용 conversation_ext: 종료 사유, 시연 표시)
- 고객 질문 = inquiry. inquiry_no = 채팅 문의번호 + 순서 (Q20261002-001-01) → 채팅 문의번호는 첫 질문에서 계산
- 챗봇·상담원 답변 = ai_response 중 status=SENT 인 것 (sent_automatically로 챗봇/상담원 구분)
- '담당자에게 전달했어요' 안내는 저장하지 않고, 자동답변이 허용되지 않은 분석(ai_analysis)이 있으면 그 시각에 표시

문의 상태
- 고객 화면: 채팅이 열려 있으면 상담 중(CHATTING), 종료되면(고객 종료·새 채팅·5분 자동 종료) 답변완료(ANSWERED)
  검토 대기 질문이 남아 있어도 고객 화면은 답변완료 (관리자 화면에는 검토대기로 남아 답변할 수 있음)
- 관리자 화면: status_code()
  - 검토대기(REVIEWING): 검토 대기 질문이 있음. 관리자가 답한 뒤 고객이 다시 물어 검토로 넘어가면 다시 검토대기
  - 답변완료(ANSWERED): 검토 대기 질문이 없고, 관리자가 답했거나 채팅이 종료됨
  - 상담 중(CHATTING): 그 외 (AI와만 대화 중) — 관리자 목록에는 나오지 않음
- 목록은 최근 대화순 (마지막 고객 메시지 시각, last_activity)
- 상담원 답변을 고치면 수정본이 채팅에 보임 (answer_edits.py — 팀 DB의 보낸 답변은 수정 불가라 따로 저장)
"""
import logging
import re
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
    INTENT_TO_CATEGORY,
    QUESTION_STATUS_FROM_DB,
    CloseReason,
    ConversationStatus,
    MessageRole,
    QuestionStatus,
    db_statuses,
    display_category,
)
from app.errors import AppError
from app.models import (
    AIAnalysis,
    AIResponse,
    Conversation,
    ConversationExt,
    Customer,
    Inquiry,
    OrderItem,
    utcnow,
)
from app.plugins.order_source import get_order_source, product_name
from app.schemas import ChatMessageOut, ConversationDetail, ConversationListOut, ConversationSummary, OrderOut, Page
from app.services import answer_edits
from app.utils import as_utc, kst_today

log = logging.getLogger(__name__)

OPEN, CLOSED = ConversationStatus.OPEN.value, ConversationStatus.CLOSED.value


def _has_question(status: QuestionStatus):
    return exists().where(Inquiry.conversation_id == Conversation.id, Inquiry.status.in_(db_statuses(status)))


# 문의 상태별 SQL 조건 (목록 거르기·건수 세기용)
HAS_PENDING = _has_question(QuestionStatus.REVIEW_PENDING)
HAS_PROCESSING = _has_question(QuestionStatus.PROCESSING)
HAS_SENT = exists().where(Inquiry.conversation_id == Conversation.id, AIResponse.inquiry_id == Inquiry.id,
                          AIResponse.status == "SENT")
# 상담원(관리자)이 보낸 답변이 하나라도 있음
HAS_ADMIN_ANSWER = exists().where(Inquiry.conversation_id == Conversation.id, AIResponse.inquiry_id == Inquiry.id,
                                  AIResponse.status == "SENT", AIResponse.sent_automatically.is_(False))
# 마지막으로 전송된 답변 시각 (5분 자동 종료 기준)
LAST_SENT_AT = (select(func.max(AIResponse.sent_at)).join(Inquiry, AIResponse.inquiry_id == Inquiry.id)
                .where(Inquiry.conversation_id == Conversation.id, AIResponse.status == "SENT")
                .correlate(Conversation).scalar_subquery())
# 관리자 화면 상태 (위 설명 참고)
STATUS_CONDITIONS = {
    "REVIEWING": [HAS_PENDING],
    "CHATTING": [~HAS_PENDING, Conversation.status == OPEN, ~HAS_ADMIN_ANSWER],
    "ANSWERED": [~HAS_PENDING, or_(Conversation.status == CLOSED, HAS_ADMIN_ANSWER)],
}
# 고객 화면 상태: 채팅이 열려 있으면 상담 중, 종료되면 답변완료
CUSTOMER_STATUS_CONDITIONS = {
    "CHATTING": [Conversation.status == OPEN],
    "ANSWERED": [Conversation.status == CLOSED],
}


def chats():
    """백엔드 채팅(conversation_ext가 있는 것)만 고르는 기본 쿼리."""
    return select(Conversation).join(Conversation.ext)


def last_activity():
    """최근 대화 시각 (목록 정렬용): 마지막 고객 메시지 시각, 메시지가 없으면 채팅 시작 시각.
    고객이 계속 채팅하면 그 문의가 목록 맨 위로 올라옵니다."""
    last_q = (select(func.max(Inquiry.created_at)).where(Inquiry.conversation_id == Conversation.id)
              .correlate(Conversation).scalar_subquery())
    return func.coalesce(last_q, Conversation.created_at)


def count_chats(*cond):
    return select(func.count(Conversation.id)).join(Conversation.ext).where(*cond)


# ───────────────────────── 문의번호 ─────────────────────────

def question_no(chat_no: str, turn: int) -> str:
    """팀 DB inquiry.inquiry_no — 채팅 문의번호 + 질문 순서 (Q20261002-001-01)."""
    return f"{chat_no}-{turn:02d}"


def split_question_no(inquiry_no: str) -> tuple[str, int]:
    """Q20261002-001-03 → ('Q20261002-001', 3)."""
    chat_no, turn = inquiry_no.rsplit("-", 1)
    return chat_no, int(turn)


def chat_no(conv: Conversation) -> str:
    """채팅 문의번호 = 첫 질문의 inquiry_no에서 순서를 뗀 값."""
    return split_question_no(conv.inquiries[0].inquiry_no)[0]


def by_chat_no(no: str):
    """채팅 문의번호로 찾는 조건 (첫 질문의 inquiry_no로 찾음)."""
    return exists().where(Inquiry.conversation_id == Conversation.id, Inquiry.inquiry_no == question_no(no.strip(), 1))


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


def question_labels(q: Inquiry) -> tuple[str | None, str | None]:
    """질문의 최종 (카테고리, 의도): 관리자 검토 라벨이 있으면 그것, 없으면 AI 분류 (정의된 유형일 때만)."""
    reviews = [rv for r in q.responses for rv in r.reviews]
    if reviews:
        rv = max(reviews, key=lambda x: x.id)
        return rv.modified_category, rv.modified_intent
    a = latest_analysis(q)
    if a is not None and INTENT_TO_CATEGORY.get(a.intent) == a.category:
        return a.category, a.intent
    return None, None


# ───────────────────────── 채팅 상태 계산 ─────────────────────────

def questions_of(conv: Conversation) -> list[Inquiry]:
    return list(conv.inquiries)


def chat_category(conv: Conversation) -> str | None:
    """문의 유형 = 첫 질문의 최종 카테고리 (DB 카테고리 7개 기준)."""
    return question_labels(conv.inquiries[0])[0] if conv.inquiries else None


def chat_order(conv: Conversation):
    """고른 주문 (첫 질문에 연결된 주문, 없으면 None)."""
    return conv.inquiries[0].order if conv.inquiries else None


def has_question(conv: Conversation, status: QuestionStatus) -> bool:
    return any(question_status(q) == status for q in conv.inquiries)


def has_admin_answer(conv: Conversation) -> bool:
    return any((r := sent_response(q)) is not None and not r.sent_automatically for q in conv.inquiries)


def status_code(conv: Conversation) -> str:
    """관리자 화면 상태: 검토대기 / 상담 중 / 답변완료 (규칙은 위쪽 설명 참고)."""
    if has_question(conv, QuestionStatus.REVIEW_PENDING):
        return "REVIEWING"
    if conv.status == CLOSED or has_admin_answer(conv):
        return "ANSWERED"
    return "CHATTING"


def customer_status_code(conv: Conversation) -> str:
    """고객 화면 상태: 채팅이 종료되면 답변완료 (고객 종료·새 채팅·5분 자동 종료), 아니면 상담 중."""
    return "ANSWERED" if conv.status == CLOSED else "CHATTING"


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
    """마지막 답변 시각 (= 5분 자동 종료 타이머 기준)."""
    sent = _sent(conv)
    return sent[-1].sent_at if sent else None


def admin_answer(conv: Conversation) -> str | None:
    admin = [r for r in _sent(conv) if not r.sent_automatically]
    return answer_edits.final_text(admin[-1]) if admin else None


def close_reason(conv: Conversation) -> CloseReason | None:
    return CloseReason(conv.ext.close_reason) if conv.ext.close_reason else None


def close_reason_label(conv: Conversation) -> str | None:
    reason = close_reason(conv)
    return CLOSE_REASON_LABELS[reason] if reason else None


def auto_close_at(conv: Conversation):
    """자동 종료 예정 시각. 타이머가 멈춘 상태(답변 전, AI 처리 중, 검토 대기, 종료됨)면 None."""
    last = answered_at(conv)
    if (conv.status != OPEN or last is None
            or has_question(conv, QuestionStatus.PROCESSING) or has_question(conv, QuestionStatus.REVIEW_PENDING)):
        return None
    return as_utc(last) + timedelta(minutes=get_settings().chat_idle_timeout_minutes)


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
    """자동 종료 대상 채팅을 한 번에 종료 (백그라운드 + 관리자 목록·상세·대시보드·고객 목록을 열 때).
    마감 시각이 지난 채팅만 DB에서 바로 골라서, 대상이 없으면 가벼운 조회 1번으로 끝납니다."""
    cutoff = utcnow() - timedelta(minutes=get_settings().chat_idle_timeout_minutes)
    with SessionLocal() as db:
        convs = db.scalars(chats().where(Conversation.status == OPEN, HAS_SENT, LAST_SENT_AT <= cutoff,
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
            edit = None if r.sent_automatically else answer_edits.get(r.id)   # 상담원 답변 수정본
            events.append((r.sent_at, q.id, 2, ChatMessageOut(
                id=f"r{r.id}", question_id=q.id, role=MessageRole.BOT if r.sent_automatically else MessageRole.ADMIN,
                content=edit["text"] if edit else r.final_response_text, created_at=r.sent_at, is_notice=False,
                question_status=None, edited_at=edit["edited_at"] if edit else None,
                edited_by=edit["edited_by"] if edit else None)))
    events.sort(key=lambda e: (as_utc(e[0]), e[1], e[2]))
    return [e[3] for e in events]


def to_detail(conv: Conversation) -> ConversationDetail:
    code, by, order = customer_status_code(conv), answered_by(conv), chat_order(conv)
    return ConversationDetail(
        inquiry_no=chat_no(conv), status=conv.status, status_code=code, status_label=CUSTOMER_STATUS_LABELS[code],
        answered_by=by, answered_by_label=ANSWERED_BY_LABELS.get(by) if by else None,
        category=display_category(chat_category(conv)),
        order_no=order.order_no if order else None, product_name=product_name(order) if order else None,
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
    code, by, order = customer_status_code(conv), answered_by(conv), chat_order(conv)
    messages = transcript(conv)
    return ConversationSummary(
        inquiry_no=chat_no(conv), preview=conv.inquiries[0].content if conv.inquiries else "",
        category=display_category(chat_category(conv)), product_name=product_name(order) if order else None,
        status_code=code, status_label=CUSTOMER_STATUS_LABELS[code],
        answered_by=by, answered_by_label=ANSWERED_BY_LABELS.get(by) if by else None,
        created_at=conv.created_at, last_message_at=messages[-1].created_at if messages else conv.created_at)


# ───────────────────────── 기능 ─────────────────────────

def list_orders(db: Session, customer: Customer) -> list[OrderOut]:
    return [OrderOut(order_no=o.order_no, product_name=o.product_name, ordered_at=o.ordered_at)
            for o in get_order_source().list_orders(db, customer)]


def _next_chat_no(db: Session) -> str:
    """Q20261002-001 형식 (오늘 만든 첫 질문 번호에서 다음 번호). 하루 999건을 넘으면 1000, 1001 …로 늘어납니다."""
    prefix = f"{get_settings().inquiry_no_prefix}{kst_today():%Y%m%d}-"
    pattern = re.compile(re.escape(prefix) + r"(\d+)-01")
    today = db.scalars(select(Inquiry.inquiry_no).where(Inquiry.inquiry_no.like(prefix + "%-01"))).all()
    seq = max((int(m.group(1)) for no in today if (m := pattern.fullmatch(no))), default=0) + 1
    return f"{prefix}{seq:03d}"


def get_owned(db: Session, customer: Customer, inquiry_no: str) -> Conversation:
    conv = db.scalar(chats().where(by_chat_no(inquiry_no), Conversation.customer_id == customer.id))
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
    """새 채팅 시작 (+ 선택: 고른 주문). 진행 중이던 다른 채팅은 건드리지 않습니다."""
    order_id = None
    if order_no:
        order = get_order_source().get_order(db, customer, order_no.strip())
        if order is None:
            raise AppError(404, "ORDER_NOT_FOUND", "주문을 찾을 수 없습니다.")
        order_id = order.order_id
    for _ in range(5):   # 동시에 시작돼 문의번호가 겹치면 다시 시도 (inquiry_no는 팀 DB에서 중복 불가)
        conv = Conversation(customer_id=customer.id, title=content[:50], status=OPEN, created_at=utcnow())
        conv.ext = ConversationExt(is_demo=is_demo)
        question = Inquiry(inquiry_no=question_no(_next_chat_no(db), 1), customer_id=customer.id, order_id=order_id,
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
    conv_id, no, order_id = conv.id, chat_no(conv), conv.inquiries[0].order_id
    for _ in range(5):   # 동시에 보내서 질문 순서 번호가 겹치면 다시 시도
        turn = (db.scalar(select(func.count(Inquiry.id)).where(Inquiry.conversation_id == conv_id)) or 0) + 1
        question = Inquiry(inquiry_no=question_no(no, turn), conversation_id=conv_id, customer_id=customer.id,
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


def contains_text(kw: str):
    """질문 내용 또는 고른 주문의 상품명에 검색어가 들어간 채팅."""
    return or_(exists().where(Inquiry.conversation_id == Conversation.id, Inquiry.content.contains(kw)),
               exists().where(Inquiry.conversation_id == Conversation.id, OrderItem.order_id == Inquiry.order_id,
                              OrderItem.product_name.contains(kw)))


def list_conversations(db: Session, customer: Customer, status: str | None, q: str | None,
                       page: int, size: int, category: str | None = None) -> ConversationListOut:
    """문의 내역 (최근 대화순). category: 화면 유형(배송/결제/교환/환불/주문/기타). counts: 탭별 건수."""
    close_idle_conversations()
    mine = Conversation.customer_id == customer.id
    cond = [mine, *CUSTOMER_STATUS_CONDITIONS.get(status, [])]
    if q and q.strip():
        cond.append(contains_text(q.strip()))
    query = chats().where(*cond).order_by(last_activity().desc(), Conversation.id.desc())
    if category:
        # 문의 유형은 첫 질문의 분석·검토에서 계산하는 값이라 불러온 뒤 거름 (관리자 목록과 같은 방식)
        matched = [c for c in db.scalars(query).all() if display_category(chat_category(c)) == category]
        total, rows = len(matched), matched[(page - 1) * size:page * size]
    else:
        total = db.scalar(count_chats(*cond)) or 0
        rows = db.scalars(query.offset((page - 1) * size).limit(size)).all()
    counts = {"all": db.scalar(count_chats(mine)) or 0,
              **{code: db.scalar(count_chats(mine, *c)) or 0 for code, c in CUSTOMER_STATUS_CONDITIONS.items()}}
    return ConversationListOut(items=[to_summary(c) for c in rows], page=Page(page=page, size=size, total=total),
                               counts=counts)
