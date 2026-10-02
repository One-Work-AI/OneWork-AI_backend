"""가짜 AI 서버 — AI_MODE=http 연결을 미리 시험하고, 모델 팀에 입출력 형식 예시로 보여주기 위한 것.

    uvicorn scripts.fake_ai_server:app --port 9000
    (백엔드 .env: AI_MODE=http, AI_SERVER_URL=http://localhost:9000)

내부적으로는 mock AI와 같은 로직을 씁니다. 형식은 docs/AI_SERVER_CONTRACT.md 참고.
"""
from fastapi import FastAPI

from app.plugins.ai_client import AIRequest, AIResult
from app.plugins.mock_ai import MockAIClient

app = FastAPI(title="Fake AI Server")


@app.post("/v1/answer", response_model=AIResult)
def answer(req: AIRequest) -> AIResult:
    return MockAIClient().answer(req)
