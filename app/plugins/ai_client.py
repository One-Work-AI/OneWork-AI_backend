"""AI 서버 입출력 형식. 모델 팀에 전달하는 문서: docs/AI_SERVER_CONTRACT.md

백엔드는 질문(+ 같은 채팅의 이전 대화)을 보내고, AI 서버가 분류·정책 검색·답변 생성을 한 번에 해서 돌려줍니다.
"""
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from app.config import get_settings


class HistoryItem(BaseModel):
    role: Literal["customer", "assistant"]
    content: str


class OrderContext(BaseModel):
    order_no: str
    product_name: str


class AIRequest(BaseModel):
    inquiry_no: str
    question: str
    history: list[HistoryItem] = Field(default_factory=list, description="같은 채팅의 이전 대화 (오래된 순). AI 서버가 안 쓰면 무시해도 됨")
    order: OrderContext | None = Field(None, description="고객이 고른 주문 상품 ('선택 안 함'이면 null). AI 서버가 안 쓰면 무시해도 됨")


class AISource(BaseModel):
    document_key: str | None = Field(None, description="정책 문서 키 (팀 DB policy_document.document_key와 같으면 자동 연결)")
    chunk_index: int | None = Field(None, description="문서 안의 조각 순서 (0부터)")
    title: str
    text: str
    score: float | None = Field(None, description="검색 관련도. 0~1 권장")


class AIResult(BaseModel):
    category: str | None = None
    intent: str | None = None
    category_confidence: float | None = None
    intent_confidence: float | None = None
    answer: str = ""
    sources: list[AISource] = []
    score_metric: str | None = Field(None, description="sources.score를 계산한 방식 (예: cosine_similarity)")
    model: str | None = None
    prompt_version: str | None = None


class AIClient(Protocol):
    def answer(self, request: AIRequest) -> AIResult: ...


def get_ai_client() -> AIClient:
    if get_settings().ai_mode == "http":
        from app.plugins.http_ai import HttpAIClient
        return HttpAIClient()
    from app.plugins.mock_ai import MockAIClient
    return MockAIClient()
