"""AI 서버 연결 (AI_MODE=http) 테스트 — 실제 AI 서버 대신 HTTP 응답을 흉내 냄 (GPU·모델 없이 실행).

팀 AI 서버(ddongzz/cs_chatbot) /chat 형식: {"query", "user_id"} → {"answer", "referenced_context", "processing_time"}
"""
import httpx
import pytest

from app.plugins import http_ai
from app.plugins.ai_client import AIRequest
from app.plugins.http_ai import NO_INFO_CONFIDENCE, HttpAIClient, from_chat_response

AUTO_Q = "배송 기간이 며칠이나 걸리나요?"      # 키워드 2개 → 0.95
CONTEXT = "배송은 결제 완료 후 1~3일 안에 출고됩니다.\n\n도서산간 지역은 2~3일이 더 걸릴 수 있습니다."
NO_INFO = "제공된 약관에서 해당 내용을 찾을 수 없어 정확한 안내가 어렵습니다."


class FakeAIServer:
    """httpx.post를 대신해 AI 서버처럼 응답하고, 받은 요청을 기록."""

    def __init__(self, answer: str, **extra):
        self.body = {"answer": answer, "referenced_context": CONTEXT, "processing_time": 1.2, **extra}
        self.calls: list[tuple[str, dict]] = []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        return httpx.Response(200, json=self.body, request=httpx.Request("POST", url))


@pytest.fixture
def fake_server(monkeypatch):
    def use(answer: str, **extra) -> FakeAIServer:
        server = FakeAIServer(answer, **extra)
        monkeypatch.setattr(http_ai.httpx, "post", server.post)
        monkeypatch.setattr("app.services.pipeline.get_ai_client", lambda: HttpAIClient())
        return server
    return use


def test_chat_response_fills_classification_from_question():
    r = from_chat_response({"answer": "1~3일 걸립니다.", "referenced_context": CONTEXT}, AUTO_Q)
    assert (r.category, r.intent) == ("배송", "배송 기간 확인")
    assert r.category_confidence == r.intent_confidence == 0.95
    assert r.answer == "1~3일 걸립니다." and r.sources[0].text == CONTEXT


def test_chat_response_prefers_ai_server_values():
    r = from_chat_response({"answer": "네.", "category": "환불", "intent": "환불받기",
                            "category_confidence": 0.6, "intent_confidence": 0.7}, AUTO_Q)
    assert (r.category, r.intent, r.category_confidence, r.intent_confidence) == ("환불", "환불받기", 0.6, 0.7)


def test_no_info_answer_lowers_confidence():
    r = from_chat_response({"answer": NO_INFO, "referenced_context": ""}, AUTO_Q)
    assert r.category_confidence == r.intent_confidence == NO_INFO_CONFIDENCE and r.sources == []


def test_client_calls_chat_endpoint(fake_server):
    server = fake_server("1~3일 걸립니다.")
    HttpAIClient().answer(AIRequest(inquiry_no="Q20261006-001", question=AUTO_Q))
    url, body = server.calls[0]
    assert url.endswith("/chat") and body == {"query": AUTO_Q, "user_id": "Q20261006-001"}


def test_chat_flow_auto_answer_and_review(client, customer_headers, admin_headers, fake_server):
    fake_server("배송은 결제 완료 후 1~3일 안에 출고됩니다.")
    d = client.post("/api/conversations", params={"wait": True}, json={"content": AUTO_Q}, headers=customer_headers).json()
    assert [m["role"] for m in d["messages"]] == ["CUSTOMER", "BOT"]
    assert d["messages"][1]["content"] == "배송은 결제 완료 후 1~3일 안에 출고됩니다."     # AI 서버 답변 그대로 전송

    fake_server(NO_INFO)                                                                # AI가 답을 못 찾음 → 검토
    r = client.post(f"/api/conversations/{d['inquiry_no']}/messages", params={"wait": True},
                    json={"content": AUTO_Q}, headers=customer_headers)
    assert r.json()["review_pending"] is True
    rq = client.get(f"/api/admin/reviews/{d['inquiry_no']}", headers=admin_headers).json()["review_question"]
    assert rq["ai_draft"]["text"] == NO_INFO and "/chat" in rq["analysis"]["analysis_model"]
    assert [x["code"] for x in rq["analysis"]["reasons"]] == ["LOW_CONFIDENCE"]
