"""DB 테이블.

구조
- customer           고객 (체험은 김민지 1명, 시연 데이터에는 가짜 고객 여러 명)
- admin_user         관리자 (시연에서는 1명, 비밀번호 없음)
- local_order        가짜 주문 (쇼핑몰 DB가 연결되기 전까지 사용. 연결 후에는 쓰지 않음)
- conversation       채팅 1개 = 문의 1건 (문의번호). 고른 주문 상품을 사본으로 저장
- message            채팅 안의 메시지 (고객 질문 / 챗봇 답변·안내 / 관리자 답변). 한 번 저장되면 수정·삭제하지 않음
- ai_analysis        고객 질문 하나에 대한 AI 분류 결과와 자동답변 판단
- ai_response        AI가 만든 답변 초안 (검토로 간 경우에도 저장 → 관리자 화면에 표시)
- retrieved_policy   AI가 근거로 검색한 정책 조각 (문서가 바뀌어도 당시 근거가 남도록 사본 저장)
- admin_review       관리자가 검토한 기록 (원래/수정 라벨·답변, 학습 데이터 여부)
- policy_document / policy_chunk   정책 문서(PDF 1개 = 문서 1개)와 PDF에서 뽑은 글자 조각
"""
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.enums import CloseReason, ConversationStatus, MessageRole, QuestionStatus


def utcnow() -> datetime:
    return datetime.now(UTC)


def _enum(e):
    # DB 종류와 상관없이 문자열로 저장 (PostgreSQL로 옮겨도 그대로 동작)
    return Enum(e, native_enum=False, length=30)


class Customer(Base):
    __tablename__ = "customer"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(20))      # 숫자만 저장 (예: 01012345678)
    email: Mapped[str | None] = mapped_column(String(100))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)   # 시연 데이터로 만든 가짜 고객
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    conversations: Mapped[list["Conversation"]] = relationship(back_populates="customer")


class AdminUser(Base):
    __tablename__ = "admin_user"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LocalOrder(Base):
    """가짜 주문 — 쇼핑몰 DB가 연결되기 전까지 '문의할 주문 상품' 목록에 씀."""
    __tablename__ = "local_order"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id", ondelete="CASCADE"), index=True)
    order_no: Mapped[str] = mapped_column(String(50), unique=True)
    product_name: Mapped[str] = mapped_column(String(200))
    ordered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Conversation(Base):
    __tablename__ = "conversation"

    id: Mapped[int] = mapped_column(primary_key=True)
    inquiry_no: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), index=True)
    status: Mapped[ConversationStatus] = mapped_column(_enum(ConversationStatus), default=ConversationStatus.OPEN, index=True)
    # 고른 주문 상품 (사본 — 주문 정보가 나중에 바뀌어도 문의 당시 값 유지). '선택 안 함'이면 비어 있음
    order_no: Mapped[str | None] = mapped_column(String(50))
    product_name: Mapped[str | None] = mapped_column(String(200))
    # 문의 유형 = 첫 질문의 최종 라벨 (DB 카테고리 7개 기준, 관리자가 고치면 고친 값)
    category: Mapped[str | None] = mapped_column(String(30), index=True)
    intent: Mapped[str | None] = mapped_column(String(50))
    # 자동 종료 타이머 기준 시각 = 마지막으로 챗봇 답변이 표시된 시각
    last_answer_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[CloseReason | None] = mapped_column(_enum(CloseReason))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    customer: Mapped[Customer] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.id")


class Message(Base):
    __tablename__ = "message"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversation.id"), index=True)
    role: Mapped[MessageRole] = mapped_column(_enum(MessageRole))
    content: Mapped[str] = mapped_column(Text)
    # 답변·안내 메시지(BOT/ADMIN)가 어떤 고객 질문에 대한 것인지
    reply_to_id: Mapped[int | None] = mapped_column(ForeignKey("message.id"), index=True)
    # 챗봇의 '담당자에게 전달했어요' 안내 (답변이 아님)
    is_notice: Mapped[bool] = mapped_column(Boolean, default=False)
    # 고객 질문(CUSTOMER)일 때만 사용
    question_status: Mapped[QuestionStatus | None] = mapped_column(_enum(QuestionStatus), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))   # 질문에 답변이 붙은 시각

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    analysis: Mapped["AIAnalysis | None"] = relationship(back_populates="question", cascade="all, delete-orphan", uselist=False)
    draft: Mapped["AIResponse | None"] = relationship(back_populates="question", cascade="all, delete-orphan", uselist=False)
    retrieved: Mapped[list["RetrievedPolicy"]] = relationship(
        back_populates="question", cascade="all, delete-orphan", order_by="RetrievedPolicy.rank")
    review: Mapped["AdminReview | None"] = relationship(back_populates="question", cascade="all, delete-orphan", uselist=False)


class AIAnalysis(Base):
    __tablename__ = "ai_analysis"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("message.id"), unique=True)
    category: Mapped[str | None] = mapped_column(String(30))
    intent: Mapped[str | None] = mapped_column(String(50))
    category_confidence: Mapped[float | None] = mapped_column(Float)
    intent_confidence: Mapped[float | None] = mapped_column(Float)
    auto_response_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    decision_reasons: Mapped[list] = mapped_column(JSON, default=list)
    error_message: Mapped[str | None] = mapped_column(Text)
    analysis_model: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    question: Mapped[Message] = relationship(back_populates="analysis")


class AIResponse(Base):
    __tablename__ = "ai_response"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("message.id"), unique=True)
    response_text: Mapped[str] = mapped_column(Text)
    generation_model: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(50))
    sent_automatically: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    question: Mapped[Message] = relationship(back_populates="draft")


class RetrievedPolicy(Base):
    __tablename__ = "retrieved_policy"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("message.id"), index=True)
    policy_document_id: Mapped[int | None] = mapped_column(ForeignKey("policy_document.id", ondelete="SET NULL"))
    policy_chunk_id: Mapped[int | None] = mapped_column(ForeignKey("policy_chunk.id", ondelete="SET NULL"))
    document_key: Mapped[str | None] = mapped_column(String(200))
    document_title: Mapped[str] = mapped_column(String(200))
    chunk_text: Mapped[str] = mapped_column(Text)
    similarity_score: Mapped[float | None] = mapped_column(Float)
    rank: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    question: Mapped[Message] = relationship(back_populates="retrieved")


class AdminReview(Base):
    __tablename__ = "admin_review"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("message.id"), unique=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("admin_user.id"))
    original_category: Mapped[str | None] = mapped_column(String(30))
    modified_category: Mapped[str] = mapped_column(String(30))
    original_intent: Mapped[str | None] = mapped_column(String(50))
    modified_intent: Mapped[str] = mapped_column(String(50))
    original_response: Mapped[str | None] = mapped_column(Text)
    modified_response: Mapped[str] = mapped_column(Text)
    use_for_training: Mapped[bool] = mapped_column(Boolean, default=False)
    remark: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    question: Mapped[Message] = relationship(back_populates="review")
    admin: Mapped[AdminUser] = relationship()


class PolicyDocument(Base):
    """정책 문서 1개 = PDF 파일 1개. PDF를 교체하면 같은 문서의 버전이 올라갑니다."""
    __tablename__ = "policy_document"

    id: Mapped[int] = mapped_column(primary_key=True)
    doc_key: Mapped[str] = mapped_column(String(200), index=True)   # 파일 이름에서 확장자를 뺀 값 (AI 서버와 문서를 맞추는 기준)
    title: Mapped[str] = mapped_column(String(200))                 # 문서명
    description: Mapped[str | None] = mapped_column(Text)            # 설명 (선택)
    category: Mapped[str | None] = mapped_column(String(30))
    filename: Mapped[str] = mapped_column(String(255))               # 올린 파일 이름
    stored_name: Mapped[str] = mapped_column(String(100))            # 서버에 저장한 파일 이름 (겹치지 않게 무작위)
    size_bytes: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text, default="")           # PDF에서 뽑은 글자 (mock 검색·근거 연결용)
    content_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)   # 문서 삭제 = 숨김
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)   # 등록일
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    chunks: Mapped[list["PolicyChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="PolicyChunk.chunk_order")


class PolicyChunk(Base):
    __tablename__ = "policy_chunk"

    id: Mapped[int] = mapped_column(primary_key=True)
    policy_document_id: Mapped[int] = mapped_column(ForeignKey("policy_document.id"), index=True)
    chunk_order: Mapped[int] = mapped_column(Integer)
    chunk_text: Mapped[str] = mapped_column(Text)
    embedding_id: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    document: Mapped[PolicyDocument] = relationship(back_populates="chunks")
