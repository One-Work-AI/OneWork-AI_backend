"""가짜 AI 서버 — GPU 없이 AI_MODE=http 연결을 시험하기 위한 것. 실제 AI 서버와 같은 주소·형식으로 응답합니다.

    uvicorn scripts.fake_ai_server:app --port 9000
    (백엔드 .env: AI_MODE=http, AI_SERVER_URL=http://localhost:9000)

- POST /chat       팀 AI 서버(ddongzz/cs_chatbot)와 같은 형식 (AI_SERVER_FORMAT=chat, 기본)
- POST /v1/answer  docs/AI_SERVER_CONTRACT.md 형식 (AI_SERVER_FORMAT=v1)
답변 내용은 mock AI와 같은 로직(키워드 검색 + 정책 문장)으로 만듭니다.
"""
import time

from fastapi import FastAPI
from pydantic import BaseModel

from app.plugins.ai_client import AIRequest, AIResult
from app.plugins.mock_ai import MockAIClient

app = FastAPI(title="Fake AI Server")


class ChatRequest(BaseModel):
    query: str
    user_id: str = "guest"


class ChatResponse(BaseModel):
    answer: str
    referenced_context: str
    processing_time: float


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    t0 = time.perf_counter()
    result = MockAIClient().answer(AIRequest(inquiry_no=req.user_id, question=req.query))
    return ChatResponse(answer=result.answer, referenced_context="\n\n".join(s.text for s in result.sources),
                        processing_time=round(time.perf_counter() - t0, 2))


@app.post("/v1/answer", response_model=AIResult)
def answer(req: AIRequest) -> AIResult:
    return MockAIClient().answer(req)


@app.get("/health")
def health():
    return {"status": "ok", "message": "fake AI server"}
