"""DB 테이블.

팀 DB (cs_chatbot 스키마, DB 담당자의 schema.sql) — 백엔드가 쓰는 컬럼만 적었습니다. 구조는 바꾸지 않습니다.
- customer, admin_user                 고객, 관리자 (체험 계정도 팀 DB의 가상 계정)
- product, orders, order_item           주문 (선택 항목. 화면에서는 주문 상품 선택을 뺐음)
- conversation                         채팅 1개
- inquiry                              고객 질문 1개 (채팅 하나에 여러 개). inquiry_no = 채팅 문의번호 + 순서
- ai_analysis                          질문 하나의 AI 분류와 자동답변 판단 (수정·삭제 불가, 다시 하면 새 행)
- ai_response                          AI 답변 초안 + 고객에게 나간 최종 답변 (status=SENT)
- retrieved_policy                     답변의 근거로 검색된 정책 조각
- admin_review                         관리자 검토 기록 (수정·삭제 불가)
- policy_document / policy_chunk       정책 문서 버전별 1행 (PDF 교체 = 새 버전 행), 조각

백엔드 전용 (cs_backend 스키마, migrations/) — 팀 DB 값으로 계산할 수 없는 것만
- conversation_ext   채팅 종료 사유, 시연 표시

팀 DB 값으로 계산하는 것 (따로 저장하지 않음)
- 채팅 문의번호 Q20261002-001  = 첫 질문 inquiry_no(Q20261002-001-01)에서 순서를 뗀 값
- 문의 유형                     = 첫 질문의 관리자 검토 라벨, 없으면 AI 분류 (ai_analysis)
- 5분 자동 종료 기준 시각       = 마지막으로 전송된 답변의 ai_response.sent_at
- 검토 사유                     = ai_analysis.decision_reason (사람이 읽는 사유 목록)
- 정책 PDF 파일                 = policy_document.source_file(올린 파일 이름) + 서버의 storage/policies/{id}.pdf

팀 DB의 트리거가 지키는 규칙 (백엔드도 맞춰서 씀)
- ai_response: 자동 전송은 ai_analysis.auto_response_allowed가 참일 때만, 관리자 전송은 같은 최종 문구를 승인한 검토가 있어야 함
- SENT가 되면 inquiry.status가 AUTO_ANSWERED(자동) / COMPLETED(관리자)로 바뀜
- ai_analysis, admin_review, policy_chunk는 수정·삭제 불가 / policy_document는 is_active만 바꿀 수 있음
"""
from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import BACKEND_SCHEMA, TEAM_SCHEMA, Base

TEAM = {"schema": TEAM_SCHEMA}
BACKEND = {"schema": BACKEND_SCHEMA}


def utcnow() -> datetime:
    return datetime.now(UTC)


def _ts(**kw):
    return mapped_column(DateTime(timezone=True), **kw)


def _fk(table: str, schema: str = TEAM_SCHEMA, **kw):
    return ForeignKey(f"{schema}.{table}.id", **kw)


# ───────────────────────── 팀 DB: 고객·관리자·주문 ─────────────────────────

class Customer(Base):
    __tablename__ = "customer"
    __table_args__ = TEAM

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)      # 숫자만 저장 (예: 01012345678)
    email: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts(default=utcnow)


class AdminUser(Base):
    __tablename__ = "admin_user"
    __table_args__ = TEAM

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    login_id: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)      # 체험 입장은 비밀번호를 쓰지 않음 (로그인 불가 표시값)
    name: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _ts(default=utcnow)


class Product(Base):
    __tablename__ = "product"
    __table_args__ = TEAM

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    product_name: Mapped[str] = mapped_column(Text)
    price: Mapped[int] = mapped_column(BigInteger)
    stock_quantity: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _ts(default=utcnow)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = TEAM

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_no: Mapped[str] = mapped_column(Text, unique=True)
    customer_id: Mapped[int] = mapped_column(BigInteger, _fk("customer"))
    order_date: Mapped[datetime] = _ts(default=utcnow)
    status: Mapped[str] = mapped_column(Text, default="PENDING")
    total_amount: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = _ts(default=utcnow)

    items: Mapped[list["OrderItem"]] = relationship(order_by="OrderItem.id", lazy="selectin")


class OrderItem(Base):
    __tablename__ = "order_item"
    __table_args__ = TEAM

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(BigInteger, _fk("orders"))
    product_id: Mapped[int] = mapped_column(BigInteger, _fk("product"))
    product_name: Mapped[str] = mapped_column(Text)
    product_option: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[int] = mapped_column(Integer)
    price: Mapped[int] = mapped_column(BigInteger)
    discount_amount: Mapped[int] = mapped_column(BigInteger, default=0)
    amount: Mapped[int] = mapped_column(BigInteger)


# ───────────────────────── 팀 DB: 상담 ─────────────────────────

class Conversation(Base):
    __tablename__ = "conversation"
    __table_args__ = TEAM

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    customer_id: Mapped[int] = mapped_column(BigInteger, _fk("customer"))
    title: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="OPEN")          # OPEN / CLOSED
    created_at: Mapped[datetime] = _ts(default=utcnow)
    closed_at: Mapped[datetime | None] = _ts()

    customer: Mapped[Customer] = relationship(lazy="joined")
    ext: Mapped["ConversationExt"] = relationship(back_populates="conversation", lazy="joined")
    inquiries: Mapped[list["Inquiry"]] = relationship(back_populates="conversation", order_by="Inquiry.id",
                                                      lazy="selectin")


class Inquiry(Base):
    """고객 질문 1개."""
    __tablename__ = "inquiry"
    __table_args__ = TEAM

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    inquiry_no: Mapped[str] = mapped_column(Text, unique=True)   # 채팅 문의번호 + 질문 순서 (Q20261002-001-01)
    conversation_id: Mapped[int] = mapped_column(BigInteger, _fk("conversation"))
    customer_id: Mapped[int] = mapped_column(BigInteger, _fk("customer"))
    order_id: Mapped[int | None] = mapped_column(BigInteger, _fk("orders"))
    content: Mapped[str] = mapped_column(Text)
    detected_language: Mapped[str] = mapped_column(Text, default="ko")
    # RECEIVED(AI 처리 전·중) / REVIEW_PENDING / AUTO_ANSWERED / COMPLETED(관리자 답변) — enums.QUESTION_STATUS_FROM_DB
    status: Mapped[str] = mapped_column(Text, default="RECEIVED")
    created_at: Mapped[datetime] = _ts(default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="inquiries")
    order: Mapped[Order | None] = relationship()
    analyses: Mapped[list["AIAnalysis"]] = relationship(order_by="AIAnalysis.id", lazy="selectin")
    responses: Mapped[list["AIResponse"]] = relationship(order_by="AIResponse.id", lazy="selectin")


class AIAnalysis(Base):
    __tablename__ = "ai_analysis"
    __table_args__ = TEAM

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    inquiry_id: Mapped[int] = mapped_column(BigInteger, _fk("inquiry"))
    category: Mapped[str] = mapped_column(Text)            # AI 오류 등으로 분류가 없으면 '미분류'
    intent: Mapped[str] = mapped_column(Text)
    category_confidence: Mapped[float | None] = mapped_column(Numeric(6, 5, asdecimal=False))
    intent_confidence: Mapped[float | None] = mapped_column(Numeric(6, 5, asdecimal=False))
    urgency: Mapped[str] = mapped_column(Text, default="NORMAL")
    auto_response_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    decision_reason: Mapped[str | None] = mapped_column(Text)   # 검토 사유 목록 ("신뢰도 기준 미만, …")
    decision_rule_version: Mapped[str] = mapped_column(Text)
    analysis_model: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts(default=utcnow)


class AIResponse(Base):
    __tablename__ = "ai_response"
    __table_args__ = TEAM

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    inquiry_id: Mapped[int] = mapped_column(BigInteger, _fk("inquiry"))
    analysis_id: Mapped[int] = mapped_column(BigInteger, _fk("ai_analysis"))
    response_text: Mapped[str] = mapped_column(Text)               # AI가 만든 원문 (바꿀 수 없음)
    response_language: Mapped[str] = mapped_column(Text, default="ko")
    generation_model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    final_response_text: Mapped[str | None] = mapped_column(Text)  # 고객에게 나간 최종 문구
    status: Mapped[str] = mapped_column(Text, default="DRAFT")     # REVIEW_PENDING / SENT 를 씀
    sent_automatically: Mapped[bool] = mapped_column(Boolean, default=False)
    sent_at: Mapped[datetime | None] = _ts()
    created_at: Mapped[datetime] = _ts(default=utcnow)

    analysis: Mapped[AIAnalysis] = relationship()
    reviews: Mapped[list["AdminReview"]] = relationship(order_by="AdminReview.id", lazy="selectin")
    retrieved: Mapped[list["RetrievedPolicy"]] = relationship(order_by="RetrievedPolicy.rank")


class RetrievedPolicy(Base):
    __tablename__ = "retrieved_policy"
    __table_args__ = TEAM

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    response_id: Mapped[int] = mapped_column(BigInteger, _fk("ai_response"))
    policy_chunk_id: Mapped[int] = mapped_column(BigInteger, _fk("policy_chunk"))
    similarity_score: Mapped[float | None] = mapped_column()
    score_metric: Mapped[str] = mapped_column(Text)
    rank: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = _ts(default=utcnow)

    chunk: Mapped["PolicyChunk"] = relationship(lazy="joined")


class AdminReview(Base):
    __tablename__ = "admin_review"
    __table_args__ = TEAM

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    response_id: Mapped[int] = mapped_column(BigInteger, _fk("ai_response"))
    admin_id: Mapped[int] = mapped_column(BigInteger, _fk("admin_user"))
    original_category: Mapped[str] = mapped_column(Text)
    modified_category: Mapped[str] = mapped_column(Text)
    original_intent: Mapped[str] = mapped_column(Text)
    modified_intent: Mapped[str] = mapped_column(Text)
    original_urgency: Mapped[str] = mapped_column(Text)
    modified_urgency: Mapped[str] = mapped_column(Text)
    original_response: Mapped[str] = mapped_column(Text)
    modified_response: Mapped[str] = mapped_column(Text)
    approved: Mapped[bool] = mapped_column(Boolean)
    use_for_training: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed_at: Mapped[datetime] = _ts(default=utcnow)
    remark: Mapped[str | None] = mapped_column(Text)

    admin: Mapped[AdminUser] = relationship(lazy="joined")


# ───────────────────────── 팀 DB: 정책 문서 ─────────────────────────

class PolicyDocument(Base):
    """정책 문서의 한 버전. PDF를 교체하면 같은 document_key로 새 행을 만들고 이전 행은 is_active=false."""
    __tablename__ = "policy_document"
    __table_args__ = TEAM

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_key: Mapped[str] = mapped_column(Text)   # 처음 올린 파일 이름에서 확장자를 뺀 값 (AI 서버와 문서를 맞추는 기준)
    title: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, default="")   # PDF에서 뽑은 글자 (mock 검색·근거 연결용)
    source_file: Mapped[str | None] = mapped_column(Text)   # 올린 파일 이름 (파일은 서버 storage/policies/{id}.pdf)
    version: Mapped[str] = mapped_column(Text)               # "1", "2", … (팀 샘플은 "demo-v1" 같은 값도 있음)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _ts(default=utcnow)

    chunks: Mapped[list["PolicyChunk"]] = relationship(back_populates="document", order_by="PolicyChunk.chunk_order")


class PolicyChunk(Base):
    __tablename__ = "policy_chunk"
    __table_args__ = TEAM

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    policy_document_id: Mapped[int] = mapped_column(BigInteger, _fk("policy_document"))
    chunk_text: Mapped[str] = mapped_column(Text)
    chunk_order: Mapped[int] = mapped_column(Integer)
    category: Mapped[str | None] = mapped_column(Text)
    embedding_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts(default=utcnow)

    document: Mapped[PolicyDocument] = relationship(back_populates="chunks", lazy="joined")


# ───────────────────────── 백엔드 전용 (cs_backend) ─────────────────────────

class ConversationExt(Base):
    """채팅 1개에 1행 (팀 DB에 칸이 없는 것만). 이 행이 있는 채팅만 백엔드 화면에 나옵니다 (팀 샘플·실습 채팅은 제외)."""
    __tablename__ = "conversation_ext"
    __table_args__ = BACKEND

    conversation_id: Mapped[int] = mapped_column(BigInteger, _fk("conversation"), primary_key=True)
    close_reason: Mapped[str | None] = mapped_column(Text)          # USER / NEW_CHAT / TIMEOUT
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)   # scripts.seed_demo 로 넣은 시연 채팅

    conversation: Mapped[Conversation] = relationship(back_populates="ext")
