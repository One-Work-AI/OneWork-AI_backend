"""관리자 문의 검토 (채팅 단위) — 목록(검토대기 / 전체 / 답변완료), 상세, 검토 답변 승인.

- 검토대기: 관리자 검토가 필요한 질문이 있는 문의
- 답변완료: 상담이 끝났고 검토할 질문이 없는 문의 (AI가 답한 문의 포함)
- 전체: 검토대기 + 답변완료 (상담 중인 채팅은 끝난 뒤에 목록에 나타남)
목록은 최신순입니다.

승인하면 관리자 답변이 같은 채팅에 상담원 답변으로 붙고, 상담은 계속됩니다 (5분 타이머 다시 시작).
팀 DB에는 admin_review(검토 기록) 1행이 생기고, 그 질문의 ai_response가 관리자 최종 문구로 SENT가 됩니다.
AI 초안이 없는 질문(AI 서버 오류·빈 답변)은 팀 DB 규칙(검토는 답변에 연결)에 맞추려고
'AI 답변 초안 없음' 표시용 답변 행을 먼저 만든 뒤 검토합니다 (NO_DRAFT_MODEL).
"""
from sqlalchemy import and_, exists, or_
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.enums import (
    INQUIRY_STATUS_LABELS,
    INTENT_TO_CATEGORY,
    QUESTION_STATUS_LABELS,
    REVIEW_REASON_LABELS,
    QuestionStatus,
    ReviewReason,
    categories_for_display,
    display_category,
)
from app.errors import AppError
from app.models import (
    AdminReview,
    AdminUser,
    AIAnalysis,
    AIResponse,
    Conversation,
    ConversationExt,
    Customer,
    Inquiry,
    utcnow,
)
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
from app.services import answer_edits
from app.services.conversations import (
    OPEN,
    STATUS_CONDITIONS,
    answered_at,
    answered_by,
    chats,
    close_idle_conversations,
    close_if_idle,
    close_reason_label,
    count_chats,
    last_activity,
    latest_analysis,
    question_answered_at,
    question_status,
    questions_of,
    sent_response,
    status_code,
    transcript,
)
from app.services.pipeline import is_first_question
from app.utils import as_utc, format_phone

PREVIEW_CHARS = 80
NO_DRAFT_MODEL = "none"                 
NO_DRAFT_TEXT = "(AI 답변 초안 없음)"

TAB_CONDITIONS = {
    "pending": STATUS_CONDITIONS["REVIEWING"],
    "done": STATUS_CONDITIONS["ANSWERED"],
    "all": [or_(and_(*STATUS_CONDITIONS["REVIEWING"]), and_(*STATUS_CONDITIONS["ANSWERED"]))],
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


def _reason_codes(q: Inquiry) -> list[str]:
    a = latest_analysis(q)
    return list(a.ext.reasons) if a and a.ext else []


def pending_questions(conv: Conversation) -> list[Inquiry]:
    return [q for q in conv.inquiries if question_status(q) == QuestionStatus.REVIEW_PENDING]


def waiting_minutes(pending: list[Inquiry]) -> int | None:
    if not pending:
        return None
    oldest = min(as_utc(q.created_at) for q in pending)
    return int((utcnow() - oldest).total_seconds() // 60)


def pending_reasons(pending: list[Inquiry]) -> list[ReasonOut]:
    codes: list[str] = []
    for q in pending:
        for c in _reason_codes(q):
            if c not in codes:
                codes.append(c)
    return reasons_out(codes)


def list_reviews(db: Session, tab: str, q: str | None, category: str | None, page: int, size: int) -> ReviewListOut:
    close_idle_conversations()     
    cond = list(TAB_CONDITIONS[tab])
    if category:
        cond.append(ConversationExt.category.in_(categories_for_display(category)))
    if q and q.strip():
        kw = q.strip()
        cond.append(or_(ConversationExt.inquiry_no.contains(kw),
                        ConversationExt.product_name.contains(kw),
                        Conversation.customer.has(Customer.name.contains(kw)),
                        exists().where(Inquiry.conversation_id == Conversation.id, Inquiry.content.contains(kw)),
                        exists().where(Inquiry.conversation_id == Conversation.id, AIResponse.inquiry_id == Inquiry.id,
                                       AIResponse.final_response_text.contains(kw))))
    total = db.scalar(count_chats(*cond)) or 0
    rows = db.scalars(chats().where(*cond).order_by(last_activity().desc(), Conversation.id.desc())
                      .offset((page - 1) * size).limit(size)).all()

    items = []
    for c in rows:
        pending = pending_questions(c)
        qs = questions_of(c)
        shown = pending[0] if pending else (qs[0] if qs else None)
        code = status_code(c)
        items.append(ReviewListItem(
            inquiry_no=c.ext.inquiry_no, customer_name=c.customer.name, customer_email=c.customer.email,
            customer_phone=format_phone(c.customer.phone), category=display_category(c.ext.category),
            product_name=c.ext.product_name, status_code=code, status_label=INQUIRY_STATUS_LABELS[code],
            answered_by=answered_by(c), preview=_preview(shown.content) if shown else "",
            created_at=c.created_at, answered_at=answered_at(c),
            waiting_minutes=waiting_minutes(pending), reasons=pending_reasons(pending),
        ))
    counts = {t: db.scalar(count_chats(*conds)) or 0 for t, conds in TAB_CONDITIONS.items()}
    return ReviewListOut(items=items, page=Page(page=page, size=size, total=total), counts=counts)


def _get(db: Session, inquiry_no: str) -> Conversation:
    conv = db.scalar(chats().where(ConversationExt.inquiry_no == inquiry_no))
    if conv is None:
        raise AppError(404, "CONVERSATION_NOT_FOUND", "문의를 찾을 수 없습니다.")
    return conv


def _draft(q: Inquiry) -> AIResponse | None:
    """AI가 만든 답변 초안 (검토용으로 만든 '초안 없음' 행은 제외)."""
    return next((r for r in reversed(q.responses) if r.generation_model != NO_DRAFT_MODEL), None)


def _latest_review(q: Inquiry) -> AdminReview | None:
    reviews = [rv for r in q.responses for rv in r.reviews]
    return max(reviews, key=lambda rv: rv.id) if reviews else None


def _question_detail(q: Inquiry) -> QuestionDetail:
    a, d, rv, sent = latest_analysis(q), _draft(q), _latest_review(q), sent_response(q)
    status = question_status(q)
    return QuestionDetail(
        question_id=q.id, content=q.content, created_at=q.created_at, status=status,
        status_label=QUESTION_STATUS_LABELS[status],
        needs_review=status == QuestionStatus.REVIEW_PENDING,
        analysis=AnalysisOut(
            category=a.category, display_category=display_category(a.category), intent=a.intent,
            category_confidence=a.category_confidence, intent_confidence=a.intent_confidence,
            auto_response_allowed=a.auto_response_allowed, reasons=reasons_out(_reason_codes(q)),
            error_message=a.ext.error_message if a.ext else None, analysis_model=a.analysis_model,
        ) if a else None,
        ai_draft=AIDraftOut(text=d.response_text, generation_model=d.generation_model, prompt_version=d.prompt_version,
                            sent_automatically=d.sent_automatically,
                            latency_ms=a.ext.latency_ms if a and a.ext else None) if d else None,
        retrieved_policies=[RetrievedOut(
            rank=p.rank, document_title=p.chunk.document.title, document_key=p.chunk.document.document_key,
            policy_document_id=p.chunk.document.id, chunk_text=p.chunk.chunk_text, similarity_score=p.similarity_score,
        ) for p in (d.retrieved if d else [])],
        answer_text=answer_edits.final_text(sent) if sent else None,
        answered_at=question_answered_at(q),
        review=ReviewRecordOut(
            admin_name=rv.admin.name, original_category=rv.original_category, modified_category=rv.modified_category,
            original_intent=rv.original_intent, modified_intent=rv.modified_intent,
            original_response=None if rv.original_response == NO_DRAFT_TEXT else rv.original_response,
            modified_response=rv.modified_response,
            use_for_training=rv.use_for_training, remark=rv.remark, reviewed_at=rv.reviewed_at,
        ) if rv else None,
    )


def get_detail(db: Session, inquiry_no: str) -> ReviewDetail:
    conv = _get(db, inquiry_no)
    if close_if_idle(conv):      
        db.commit()
    c = conv.customer
    questions = [_question_detail(q) for q in questions_of(conv)]
    review_q = (next((qd for qd in questions if qd.needs_review), None)
                or next((qd for qd in reversed(questions) if qd.review), None))
    others = db.scalars(chats().where(Conversation.customer_id == c.id, Conversation.id != conv.id)
                        .order_by(last_activity().desc(), Conversation.id.desc())).all()
    code = status_code(conv)
    return ReviewDetail(
        inquiry_no=conv.ext.inquiry_no, status_code=code, status_label=INQUIRY_STATUS_LABELS[code],
        chat_status=conv.status, close_reason_label=close_reason_label(conv),
        created_at=conv.created_at, closed_at=conv.closed_at, answered_at=answered_at(conv),
        customer=CustomerOut(id=c.id, name=c.name, phone=format_phone(c.phone), email=c.email),
        category=display_category(conv.ext.category), order_no=conv.ext.order_no, product_name=conv.ext.product_name,
        messages=transcript(conv), review_question=review_q, questions=questions,
        previous_inquiries=[RelatedInquiry(
            inquiry_no=o.ext.inquiry_no, preview=_preview(o.inquiries[0].content) if o.inquiries else "",
            category=display_category(o.ext.category), status_code=status_code(o),
            status_label=INQUIRY_STATUS_LABELS[status_code(o)], created_at=o.created_at) for o in others],
    )


def _unsent_response(db: Session, q: Inquiry, a: AIAnalysis) -> AIResponse:
    """검토할 답변 행 — AI 초안이 있으면 그것, 없으면 '초안 없음' 행을 새로 만듦."""
    r = next((r for r in reversed(q.responses) if r.status != "SENT"), None)
    if r is None:
        r = AIResponse(inquiry_id=q.id, analysis_id=a.id, response_text=NO_DRAFT_TEXT, generation_model=NO_DRAFT_MODEL,
                       prompt_version=NO_DRAFT_MODEL, status="REVIEW_PENDING")
        db.add(r)
        db.flush()
    return r


def approve(db: Session, inquiry_no: str, question_id: int, admin: AdminUser, req: ApproveRequest) -> ReviewDetail:
    """검토대기 질문에 최종 답변 승인 → 고객 채팅에 상담원 답변이 붙고, 상담은 계속됨."""
    conv = _get(db, inquiry_no)
    q = next((x for x in conv.inquiries if x.id == question_id), None)
    if q is None:
        raise AppError(404, "QUESTION_NOT_FOUND", "질문을 찾을 수 없습니다.")
    a = latest_analysis(q)
    if question_status(q) != QuestionStatus.REVIEW_PENDING or a is None:
        raise AppError(409, "NOT_REVIEW_PENDING", "검토대기 중인 질문만 승인할 수 있습니다.")

    intent = req.intent or a.intent         
    category = req.category or (INTENT_TO_CATEGORY.get(req.intent) if req.intent else a.category)
    if intent not in INTENT_TO_CATEGORY or category != INTENT_TO_CATEGORY[intent]:
        raise AppError(422, "LABEL_REQUIRED", "AI 분류 결과가 없거나 잘못되어 있습니다. 올바른 카테고리와 의도를 지정해 주세요.")

    now = utcnow()
    try:
        r = _unsent_response(db, q, a)
        db.add(AdminReview(
            response_id=r.id, admin_id=admin.id,
            original_category=a.category, modified_category=category,
            original_intent=a.intent, modified_intent=intent,
            original_urgency=a.urgency, modified_urgency=a.urgency,
            original_response=r.response_text, modified_response=req.response_text,
            approved=True, use_for_training=req.use_for_training, remark=req.remark, reviewed_at=now,
        ))
        db.flush()
        r.final_response_text, r.status, r.sent_at, r.sent_automatically = req.response_text, "SENT", now, False
        q.status = "COMPLETED"                   
        if conv.status == OPEN:                     
            conv.ext.last_answer_at = now
        if is_first_question(conv, q):              
            conv.ext.category, conv.ext.intent = category, intent
        db.commit()
    except DBAPIError as e:
        db.rollback()
        if getattr(e.orig, "sqlstate", None) in ("23514", "23505"):  
            raise AppError(409, "NOT_REVIEW_PENDING", "이미 처리된 질문입니다. 화면을 새로고침해 주세요.") from None
        raise
    db.expire_all()
    return get_detail(db, inquiry_no)


def edit_answer(db: Session, inquiry_no: str, question_id: int, admin: AdminUser, text: str) -> ReviewDetail:
    conv = _get(db, inquiry_no)
    q = next((x for x in conv.inquiries if x.id == question_id), None)
    if q is None:
        raise AppError(404, "QUESTION_NOT_FOUND", "질문을 찾을 수 없습니다.")
    sent = sent_response(q)
    if sent is None or sent.sent_automatically:
        raise AppError(409, "NOT_ADMIN_ANSWER", "상담원이 보낸 답변만 수정할 수 있습니다.")
    answer_edits.save(sent.id, text, admin.name)
    return get_detail(db, inquiry_no)