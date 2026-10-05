"""실제 AI 서버 호출 (.env: AI_MODE=http, AI_SERVER_URL=..., AI_SERVER_FORMAT=chat | v1).

chat (기본) — 팀 AI 서버 ddongzz/cs_chatbot 의 api/main.py
    POST {AI_SERVER_URL}/chat   {"query": 질문, "user_id": 문의번호}
    → {"answer": 답변, "referenced_context": 참고 약관(검색된 조각 3개를 이은 글), "processing_time": 초}
    AI 서버가 카테고리·의도·신뢰도를 주지 않아서 백엔드가 채웁니다 (응답에 그 값이 있으면 응답 값을 씀).
    - 카테고리·의도·신뢰도: 질문 키워드 분류 (mock_ai.classify와 같은 규칙)
    - AI가 '약관에서 찾을 수 없다'고 답하면 신뢰도를 낮춰 관리자 검토로 보냄
    - 근거: referenced_context를 그대로 1개로 넘김. 팀 DB 정책 조각 번호가 없어서 retrieved_policy에는 남지 않음
    이전 대화(history)와 고른 주문(order)은 /chat이 받지 않아서 보내지 않습니다.

v1 — docs/AI_SERVER_CONTRACT.md 형식 POST {AI_SERVER_URL}/v1/answer (AI 서버가 분류·신뢰도·근거를 모두 줌)
"""
import httpx

from app.config import get_settings
from app.enums import INTENT_TO_CATEGORY
from app.plugins.ai_client import AIRequest, AIResult, AISource
from app.plugins.mock_ai import classify

NO_INFO_PHRASE = "약관에서 해당 내용을 찾을 수 없"   # 팀 AI 서버 프롬프트의 '답변 불가' 문구 (rag_chain.py)
NO_INFO_CONFIDENCE = 0.3                             # 이 문구가 나오면 신뢰도를 이 값 이하로 → 관리자 검토
CHAT_MODEL = "cs_chatbot /chat + 백엔드 키워드 분류"


def _cap(confidence: float | None, limit: float) -> float:
    return limit if confidence is None else min(confidence, limit)


def from_chat_response(data: dict, question: str) -> AIResult:
    """팀 AI 서버 /chat 응답 → 백엔드 형식. 없는 분류·신뢰도는 질문 키워드로 채움."""
    answer = (data.get("answer") or "").strip()
    keyword_intent, keyword_conf = classify(question)
    intent = data.get("intent") or keyword_intent
    category = data.get("category") or INTENT_TO_CATEGORY.get(intent)
    category_conf = data.get("category_confidence", keyword_conf)
    intent_conf = data.get("intent_confidence", keyword_conf)
    if NO_INFO_PHRASE in answer:
        category_conf, intent_conf = _cap(category_conf, NO_INFO_CONFIDENCE), _cap(intent_conf, NO_INFO_CONFIDENCE)
    context = (data.get("referenced_context") or "").strip()
    return AIResult(
        category=category, intent=intent, category_confidence=category_conf, intent_confidence=intent_conf,
        answer=answer, sources=[AISource(title="참고 약관", text=context)] if context else [],
        model=data.get("model") or CHAT_MODEL, prompt_version=data.get("prompt_version"),
    )


class HttpAIClient:
    def __init__(self):
        s = get_settings()
        self.base_url = s.ai_server_url.rstrip("/")
        self.format = s.ai_server_format
        self.timeout = s.ai_timeout_seconds

    def answer(self, request: AIRequest) -> AIResult:
        if self.format == "chat":
            resp = httpx.post(f"{self.base_url}/chat", json={"query": request.question, "user_id": request.inquiry_no},
                              timeout=self.timeout)
            resp.raise_for_status()
            return from_chat_response(resp.json(), request.question)
        resp = httpx.post(f"{self.base_url}/v1/answer", json=request.model_dump(), timeout=self.timeout)
        resp.raise_for_status()
        return AIResult.model_validate(resp.json())
