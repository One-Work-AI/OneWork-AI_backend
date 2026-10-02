"""API 요청·응답 형식. 프론트는 /docs (Swagger)에서 같은 내용을 볼 수 있습니다.

응답의 모든 시각은 UTC(끝에 Z, 예: 2026-10-01T01:28:38Z)로 내려갑니다. 화면에서는 한국 시간으로 바꿔서 보여주세요.
문의 유형(category)은 화면용 5개(배송 / 결제 / 교환/환불 / 주문 / 기타)로 내려갑니다.
"""
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field, field_validator, model_validator

from app.enums import CATEGORY_INTENTS, INTENT_TO_CATEGORY, CloseReason, ConversationStatus, MessageRole, QuestionStatus
from app.utils import as_utc

UTCDateTime = Annotated[datetime, AfterValidator(as_utc)]


def _strip_required(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("메시지를 입력해 주세요.")
    return v


class Page(BaseModel):
    page: int
    size: int
    total: int


# ───────────────────────── 체험 입장 ─────────────────────────

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="초 단위")
    role: str = Field(description="customer 또는 admin")
    name: str = Field(description="화면 왼쪽 아래에 표시할 이름")


# ───────────────────────── 고객 ─────────────────────────

class OrderOut(BaseModel):
    order_no: str
    product_name: str
    ordered_at: UTCDateTime | None


class ConversationCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000, description="첫 메시지")
    order_no: str | None = Field(None, description="'문의할 주문 상품'에서 고른 주문번호. '선택 안 함'이면 생략")

    @field_validator("content")
    @classmethod
    def _strip(cls, v: str) -> str:
        return _strip_required(v)


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000, description="고객이 보낼 메시지")

    @field_validator("content")
    @classmethod
    def _strip(cls, v: str) -> str:
        return _strip_required(v)


class ChatMessageOut(BaseModel):
    id: int
    role: MessageRole = Field(description="CUSTOMER(고객) / BOT(AI 상담사) / ADMIN(상담원)")
    content: str
    created_at: UTCDateTime
    reply_to_id: int | None = Field(description="답변·안내 메시지일 때, 어떤 질문에 대한 것인지")
    is_notice: bool = Field(description="true면 '담당자에게 전달했어요' 안내 (답변 아님)")
    question_status: QuestionStatus | None = Field(description="고객 질문일 때만: 처리 상태")


class ConversationSummary(BaseModel):
    inquiry_no: str
    preview: str = Field(description="첫 질문")
    category: str | None = Field(description="문의 유형 (화면용 5개). 분류 전이면 null")
    product_name: str | None = Field(description="고른 주문 상품. null이면 '주문 상품 미선택'")
    status_code: str = Field(description="CHATTING(상담 중) / ANSWERED(답변완료). 검토 대기 중이어도 '상담 중'")
    status_label: str
    answered_by: str | None = Field(description="답변완료일 때: AI / ADMIN")
    answered_by_label: str | None = Field(description="AI 답변 / 관리자 답변")
    created_at: UTCDateTime
    last_message_at: UTCDateTime


class ConversationListOut(BaseModel):
    items: list[ConversationSummary]
    page: Page


class ConversationDetail(BaseModel):
    inquiry_no: str
    status: ConversationStatus
    status_code: str
    status_label: str
    answered_by: str | None
    answered_by_label: str | None
    category: str | None
    order_no: str | None
    product_name: str | None
    created_at: UTCDateTime = Field(description="등록일 (상담 시작)")
    answered_at: UTCDateTime | None = Field(description="답변완료 시각 (마지막 답변)")
    closed_at: UTCDateTime | None
    close_reason: CloseReason | None
    close_reason_label: str | None = Field(description="고객 종료 / 새 채팅 시작 / 응답 없음 자동 종료")
    admin_answer: str | None = Field(description="마지막 관리자 답변 ('답변' 칸). AI만 답한 문의면 null")
    input_locked: bool = Field(description="true면 입력창을 잠그세요 (상담이 종료됐을 때만)")
    answering: bool = Field(description="AI가 답을 만드는 중인 질문이 있음 (입력은 가능, 보낸 순서대로 답함)")
    review_pending: bool = Field(description="담당자 확인 중인 질문이 있음 (입력은 가능)")
    auto_close_at: UTCDateTime | None = Field(description="이 시각까지 고객 메시지가 없으면 자동 종료 (타이머가 멈춘 상태면 null)")
    messages: list[ChatMessageOut]


# ───────────────────────── 관리자 ─────────────────────────

class ReasonOut(BaseModel):
    code: str
    label: str


class CustomerOut(BaseModel):
    id: int
    name: str
    phone: str | None
    email: str | None


class ReviewListItem(BaseModel):
    inquiry_no: str
    customer_name: str
    customer_email: str | None
    customer_phone: str | None
    category: str | None
    product_name: str | None
    status_code: str = Field(description="REVIEWING(검토대기) / ANSWERED(답변완료)")
    status_label: str
    answered_by: str | None
    preview: str = Field(description="검토대기면 검토할 질문, 아니면 첫 질문")
    created_at: UTCDateTime = Field(description="등록일자")
    answered_at: UTCDateTime | None = Field(description="답변완료 시각")
    waiting_minutes: int | None = Field(description="검토대기일 때 대기 시간(분) — 화면에 넣을지는 선택")
    reasons: list[ReasonOut] = Field(description="검토로 온 이유 — 화면에 넣을지는 선택")


class ReviewListOut(BaseModel):
    items: list[ReviewListItem]
    page: Page
    counts: dict[str, int] = Field(description="탭별 건수: pending(검토대기) / all(전체) / done(답변완료)")


class AnalysisOut(BaseModel):
    category: str | None = Field(description="AI 분류 (DB 카테고리 7개 기준)")
    display_category: str | None = Field(description="화면용 유형 5개")
    intent: str | None
    category_confidence: float | None
    intent_confidence: float | None
    auto_response_allowed: bool
    reasons: list[ReasonOut]
    error_message: str | None
    analysis_model: str | None


class AIDraftOut(BaseModel):
    text: str
    generation_model: str | None
    prompt_version: str | None
    sent_automatically: bool
    latency_ms: int | None


class RetrievedOut(BaseModel):
    rank: int
    document_title: str
    document_key: str | None
    policy_document_id: int | None
    chunk_text: str
    similarity_score: float | None


class ReviewRecordOut(BaseModel):
    admin_name: str
    original_category: str | None
    modified_category: str
    original_intent: str | None
    modified_intent: str
    original_response: str | None
    modified_response: str
    use_for_training: bool
    remark: str | None
    reviewed_at: UTCDateTime


class QuestionDetail(BaseModel):
    message_id: int
    content: str
    created_at: UTCDateTime
    status: QuestionStatus
    status_label: str
    needs_review: bool
    analysis: AnalysisOut | None
    ai_draft: AIDraftOut | None
    retrieved_policies: list[RetrievedOut]
    answer_text: str | None = Field(description="고객에게 나간 답변 (AI 답변 또는 관리자 답변)")
    answered_at: UTCDateTime | None
    review: ReviewRecordOut | None


class RelatedInquiry(BaseModel):
    inquiry_no: str
    preview: str
    category: str | None
    status_code: str
    status_label: str
    created_at: UTCDateTime


class ReviewDetail(BaseModel):
    inquiry_no: str
    status_code: str
    status_label: str
    chat_status: ConversationStatus
    close_reason_label: str | None = Field(description="채팅 내역 위 '고객 종료' 등")
    created_at: UTCDateTime = Field(description="접수일")
    closed_at: UTCDateTime | None
    answered_at: UTCDateTime | None = Field(description="답변완료 시간")
    customer: CustomerOut
    category: str | None
    order_no: str | None
    product_name: str | None
    messages: list[ChatMessageOut] = Field(description="채팅 내역 (고객이 보는 그대로)")
    review_question: QuestionDetail | None = Field(
        description="'AI 답변 · 관리자 검토' 칸에 쓸 질문: 검토대기 질문, 없으면 마지막으로 검토한 질문. AI만 답한 문의면 null")
    questions: list[QuestionDetail] = Field(description="고객 질문 전체의 AI 분석·초안·근거")
    previous_inquiries: list[RelatedInquiry] = Field(description="이전 채팅 내역 (같은 고객의 다른 문의, 최신순)")


class ApproveRequest(BaseModel):
    response_text: str = Field(min_length=1, description="최종 답변 (검토 후 수정). AI 초안을 그대로 쓰려면 그대로 보내면 됨")
    category: str | None = Field(None, description="(선택) 수정한 카테고리 — DB 카테고리 7개 기준")
    intent: str | None = Field(None, description="(선택) 수정한 의도")
    use_for_training: bool = Field(False, description="(선택) 학습 데이터로 사용")
    remark: str | None = Field(None, max_length=1000, description="(선택) 관리자 메모")

    @field_validator("response_text")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("답변을 입력해 주세요.")
        return v

    @model_validator(mode="after")
    def _labels(self):
        if self.category is not None and self.category not in CATEGORY_INTENTS:
            raise ValueError(f"정의되지 않은 카테고리입니다: {self.category}")
        if self.intent is not None:
            if self.intent not in INTENT_TO_CATEGORY:
                raise ValueError(f"정의되지 않은 의도입니다: {self.intent}")
            if self.category is not None and INTENT_TO_CATEGORY[self.intent] != self.category:
                raise ValueError(f"'{self.intent}'는 '{INTENT_TO_CATEGORY[self.intent]}' 카테고리의 의도입니다.")
        return self


class CountItem(BaseModel):
    name: str
    count: int
    ratio: float = Field(description="0~1")


class HourlyItem(BaseModel):
    hour: int = Field(description="0~23 (한국 시간)")
    received: int = Field(description="이 시간대에 들어온 고객 질문 수 (전체 기간 합계)")
    answered: int = Field(description="이 시간대에 답변된 질문 수 (AI+관리자, 전체 기간 합계)")


class PeakWindow(BaseModel):
    start_hour: int
    end_hour: int = Field(description="끝 시각 (예: 13~15시면 15)")
    received: int
    top_category: str | None = Field(description="이 구간에 가장 많이 들어온 문의 유형")


class PendingItem(BaseModel):
    inquiry_no: str
    customer_name: str
    category: str | None
    product_name: str | None
    preview: str = Field(description="검토할 질문")
    created_at: UTCDateTime
    waiting_minutes: int
    reasons: list[ReasonOut]


class DashboardOut(BaseModel):
    total_inquiries: int = Field(description="전체 문의 = 검토대기 + 답변완료 (상담 중인 채팅은 제외)")
    chatting_now: int = Field(description="지금 상담 중인 채팅 수 (참고)")
    pending_review: int = Field(description="검토대기")
    answered: dict[str, int] = Field(description="답변완료: total / ai / admin")
    ai_auto_rate: float | None = Field(description="AI 자동 응답률 = AI가 끝까지 답한 문의 / 전체 문의 (0~1)")
    by_category: list[CountItem] = Field(description="문의 유형별 비중 (화면용 5개)")
    hourly: list[HourlyItem]
    peak_window: PeakWindow | None = Field(description="질문이 가장 많이 들어온 2시간 구간")
    pending_list: list[PendingItem] = Field(description="검토대기 문의 (오래된 순)")


class PolicyListItem(BaseModel):
    id: int
    title: str = Field(description="문서명")
    filename: str
    size_bytes: int
    version: int
    created_at: UTCDateTime = Field(description="등록일")
    updated_at: UTCDateTime


class PolicyDetail(PolicyListItem):
    doc_key: str
    description: str | None
    text_extracted: bool = Field(description="PDF에서 글자를 뽑았는지 (스캔본이면 false)")


class MetaOut(BaseModel):
    display_categories: list[str] = Field(description="화면용 문의 유형 5개 (목록 거르기용)")
    category_intents: dict[str, list[str]] = Field(description="DB 카테고리 7개 → 의도 17개 (승인할 때 라벨 수정용, 선택)")
    display_category_map: dict[str, str] = Field(description="DB 카테고리 → 화면용 유형")
    inquiry_statuses: dict[str, str]
    question_statuses: dict[str, str]
    review_reasons: dict[str, str]
