"""실제 AI 서버 호출 (.env: AI_MODE=http, AI_SERVER_URL=...)."""
import httpx

from app.config import get_settings
from app.plugins.ai_client import AIRequest, AIResult


class HttpAIClient:
    def __init__(self):
        s = get_settings()
        self.url = s.ai_server_url.rstrip("/") + "/v1/answer"
        self.timeout = s.ai_timeout_seconds

    def answer(self, request: AIRequest) -> AIResult:
        resp = httpx.post(self.url, json=request.model_dump(), timeout=self.timeout)
        resp.raise_for_status()
        return AIResult.model_validate(resp.json())
