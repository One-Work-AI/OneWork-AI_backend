"""관리자 문의 검토 (채팅 단위) — 목록(검토대기 / 전체 / 답변완료), 상세, 검토 답변 승인.

- 검토대기: 관리자 검토가 필요한 질문이 있는 문의
- 답변완료: 상담이 끝났고 검토할 질문이 없는 문의 (AI가 답한 문의 포함)
- 전체: 검토대기 + 답변완료 (상담 중인 채팅은 끝난 뒤에 목록에 나타남)
목록은 최신순입니다.

승인하면 관리자 답변이 같은 채팅에 상담원 답변으로 붙고, 상담은 계속됩니다 (5분 타이머 다시 시작).
"""
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.enums import (
    CLOSE_REASON_LABELS,
    INQUIRY_STATUS_LABELS,
    INTENT_TO_CATEGORY,
    QUESTION_STATUS_LABELS,
    REVIEW_REASON_LABELS,
    ConversationStatus,
    MessageRole,
    QuestionStatus,
    ReviewReason,
    categories_for_display,
    display_category,
)
from app.errors import AppError
from app.models import AdminReview, AdminUser, Conversation, Customer, Message, utcnow
from app.schemas import (
    AIDraftOut,
    AnalysisOut,
    ApproveRequest,
    CustomerOut,
    Page,
    QuestionDetail,
    ReasonOut,
    RelatedInquiry,
    RetrievedOut,
    ReviewDetail,
    ReviewListItem,
    ReviewListOut,
    ReviewRecordOut,
)
from app.services.conversations import (
    STATUS_CONDITIONS,
    answered_at,
    answered_by,
    questions_of,
    status_code,
    transcript,
)
from app.services.pipeline import is_first_question
from app.utils import as_utc, format_phone

PREVIEW_CHARS = 80

TAB_CONDITIONS = {
    "pending": STATUS_CONDITIONS["REVIEWING"],
    "done": STATUS_CONDITIONS["ANSWERED"],
    "all": [or_(*[c for c in STATUS_CONDITIONS["REVIEWING"]],
                Conversation.status == ConversationStatus.CLOSED)],
}


def reasons_out(codes: list[str] | None) -> list[ReasonOut]:
    out = []
    for c in codes or []:
        try:
            out.append(ReasonOut(code=c, label=REVIEW_REASON_LABELS[ReviewReason(c)]))
        except ValueError:
            out.append(ReasonOut(code=c, label=c))
    return out


def _preview(text: str) -> str:
    return text[:PREVIEW_CHARS] + ("…" if len(text) > PREVIEW_CHARS else "")


def pending_questions(conv: Conversation) -> list[Message]:
    return [m for m in conv.messages if m.question_status == QuestionStatus.REVIEW_PENDING]


def waiting_minutes(pending: list[Message]) -> int | None:
    if not pending:
        return None
    oldest = min(as_utc(m.created_at) for m in pending)
    return int((utcnow() - oldest).total_seconds() // 60)


def pending_reasons(pending: list[Message]) -> list[ReasonOut]:
    codes: list[str] = []
    for m in pending:
        for c in (m.analysis.decision_reasons if m.analysis else []):
            if c not in codes:
                codes.append(c)
    return reasons_out(codes)


def list_reviews(db: Session, tab: str, q: str | None, category: str | None, page: int, size: int) -> ReviewListOut:
    cond = list(TAB_CONDITIONS[tab])
    if category:
        cond.append(Conversation.category.in_(categories_for_display(category)))
    if q and q.strip():
        kw = q.strip()
        cond.append(or_(Conversation.inquiry_no.contains(kw),
                        Conversation.product_name.contains(kw),
                        Conversation.customer.has(Customer.name.contains(kw)),
                        exists().where(Message.conversation_id == Conversation.id, Message.content.contains(kw))))
    total = db.scalar(select(func.count(Conversation.id)).where(*cond)) or 0
    rows = db.scalars(select(Conversation).where(*cond)
                      .order_by(Conversation.created_at.desc(), Conversation.id.desc())
                      .options(selectinload(Conversation.customer),
                               selectinload(Conversation.messages).selectinload(Message.analysis))
                      .offset((page - 1) * size).limit(size)).all()

    items = []
    for c in rows:
        pending = pending_questions(c)
        qs = questions_of(c)
        shown = pending[0] if pending else (qs[0] if qs else None)
        code = status_code(c)
        items.append(ReviewListItem(
            inquiry_no=c.inquiry_no, customer_name=c.customer.name, customer_email=c.customer.email,
            customer_phone=format_phone(c.customer.phone), category=display_category(c.category),
            product_name=c.product_name, status_code=code, status_label=INQUIRY_STATUS_LABELS[code],
            answered_by=answered_by(c), preview=_preview(shown.content) if shown else "",
            created_at=c.created_at, answered_at=answered_at(c),
            waiting_minutes=waiting_minutes(pending), reasons=pending_reasons(pending),
        ))
    counts = {t: db.scalar(select(func.count(Conversation.id)).where(*conds)) or 0
              for t, conds in TAB_CONDITIONS.items()}
    return ReviewListOut(items=items, page=Page(page=page, size=size, total=total), counts=counts)


def _get(db: Session, inquiry_no: str) -> Conversation:
    conv = db.scalar(select(Conversation).where(Conversation.inquiry_no == inquiry_no))
    if conv is None:
        raise AppError(404, "CONVERSATION_NOT_FOUND", "문의를 찾을 수 없습니다.")
    return conv


def _question_detail(conv: Conversation, q: Message) -> QuestionDetail:
    a, d, rv = q.analysis, q.draft, q.review
    answer = next((m for m in conv.messages if m.reply_to_id == q.id and not m.is_notice), None)
    return QuestionDetail(
        message_id=q.id, content=q.content, created_at=q.created_at, status=q.question_status,
        status_label=QUESTION_STATUS_LABELS[q.question_status],
        needs_review=q.question_status == QuestionStatus.REVIEW_PENDING,
        analysis=AnalysisOut(
            category=a.category, display_category=display_category(a.category), intent=a.intent,
            category_confidence=a.category_confidence, intent_confidence=a.intent_confidence,
            auto_response_allowed=a.auto_response_allowed, reasons=reasons_out(a.decision_reasons),
            error_message=a.error_message, analysis_model=a.analysis_model,
        ) if a else None,
        ai_draft=AIDraftOut(text=d.response_text, generation_model=d.generation_model, prompt_version=d.prompt_version,
                            sent_automatically=d.sent_automatically, latency_ms=d.latency_ms) if d else None,
        retrieved_policies=[RetrievedOut(
            rank=p.rank, document_title=p.document_title, document_key=p.document_key,
            policy_document_id=p.policy_document_id, chunk_text=p.chunk_text, similarity_score=p.similarity_score,
        ) for p in q.retrieved],
        answer_text=answer.content if answer else None,
        answered_at=q.answered_at,
        review=ReviewRecordOut(
            admin_name=rv.admin.name, original_category=rv.original_category, modified_category=rv.modified_category,
            original_intent=rv.original_intent, modified_intent=rv.modified_intent,
            original_response=rv.original_response, modified_response=rv.modified_response,
            use_for_training=rv.use_for_training, remark=rv.remark, reviewed_at=rv.reviewed_at,
        ) if rv else None,
    )


def get_detail(db: Session, inquiry_no: str) -> ReviewDetail:
    conv = _get(db, inquiry_no)
    c = conv.customer
    questions = [_question_detail(conv, m) for m in questions_of(conv)]
    review_q = (next((qd for qd in questions if qd.needs_review), None)
                or next((qd for qd in reversed(questions) if qd.review), None))
    others = db.scalars(select(Conversation)
                        .where(Conversation.customer_id == c.id, Conversation.id != conv.id)
                        .order_by(Conversation.created_at.desc(), Conversation.id.desc())
                        .options(selectinload(Conversation.messages))).all()
    code = status_code(conv)
    return ReviewDetail(
        inquiry_no=conv.inquiry_no, status_code=code, status_label=INQUIRY_STATUS_LABELS[code],
        chat_status=conv.status,
        close_reason_label=CLOSE_REASON_LABELS[conv.close_reason] if conv.close_reason else None,
        created_at=conv.created_at, closed_at=conv.closed_at, answered_at=answered_at(conv),
        customer=CustomerOut(id=c.id, name=c.name, phone=format_phone(c.phone), email=c.email),
        category=display_category(conv.category), order_no=conv.order_no, product_name=conv.product_name,
        messages=transcript(conv), review_question=review_q, questions=questions,
        previous_inquiries=[RelatedInquiry(
            inquiry_no=o.inquiry_no, preview=_preview(questions_of(o)[0].content) if questions_of(o) else "",
            category=display_category(o.category), status_code=status_code(o),
            status_label=INQUIRY_STATUS_LABELS[status_code(o)], created_at=o.created_at) for o in others],
    )


def approve(db: Session, inquiry_no: str, message_id: int, admin: AdminUser, req: ApproveRequest) -> ReviewDetail:
    """검토대기 질문에 최종 답변 승인 → 고객 채팅에 상담원 답변이 붙고, 상담은 계속됨."""
    conv = _get(db, inquiry_no)
    q = db.get(Message, message_id)
    if q is None or q.conversation_id != conv.id or q.role != MessageRole.CUSTOMER:
        raise AppError(404, "QUESTION_NOT_FOUND", "질문을 찾을 수 없습니다.")
    if q.question_status != QuestionStatus.REVIEW_PENDING:
        raise AppError(409, "NOT_REVIEW_PENDING", "검토대기 중인 질문만 승인할 수 있습니다.")

    a = q.analysis
    orig_cat, orig_intent = (a.category, a.intent) if a else (None, None)
    intent = req.intent or orig_intent           # 의도만 고치면 카테고리는 의도에 맞춰 자동으로
    category = req.category or (INTENT_TO_CATEGORY.get(req.intent) if req.intent else orig_cat)
    if intent not in INTENT_TO_CATEGORY or category != INTENT_TO_CATEGORY[intent]:
        raise AppError(422, "LABEL_REQUIRED", "AI 분류 결과가 없거나 잘못되어 있습니다. 올바른 카테고리와 의도를 지정해 주세요.")

    now = utcnow()
    q.review = AdminReview(
        admin_id=admin.id, original_category=orig_cat, modified_category=category,
        original_intent=orig_intent, modified_intent=intent,
        original_response=q.draft.response_text if q.draft else None, modified_response=req.response_text,
        use_for_training=req.use_for_training, remark=req.remark, reviewed_at=now,
    )
    conv.messages.append(Message(role=MessageRole.ADMIN, content=req.response_text, reply_to_id=q.id, created_at=now))
    q.question_status = QuestionStatus.ADMIN_ANSWERED
    q.answered_at = now
    if conv.status == ConversationStatus.OPEN:   # 5분 자동 종료 타이머 다시 시작 (다른 검토 대기가 없을 때 적용)
        conv.last_answer_at = now
    if is_first_question(db, q):                 # 목록에 보이는 문의 유형도 고친 값으로
        conv.category, conv.intent = category, intent
    db.commit()
    return get_detail(db, inquiry_no)
