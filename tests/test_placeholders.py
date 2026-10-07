"""AI 답변의 치환자({{…}}) 채우기 테스트.

테스트 DB의 체험 고객(테스트고객)은 주문 3건이 있고, 가장 최근 주문(20241210-1234567)에만
배송(TRACK-0001)·결제(PAY-0001, 89,000원, 신용카드) 정보가 있습니다 (conftest.py).
"""
import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Customer
from app.plugins import http_ai
from app.plugins.http_ai import HttpAIClient
from app.services.placeholders import fill, latest_order
from tests.test_ai_http import FakeAIServer

AUTO_Q = "배송 기간이 며칠이나 걸리나요?"      # 키워드 2개 → 신뢰도 0.95
LATEST_ORDER = "20241210-1234567"


@pytest.fixture
def ai_answers(monkeypatch):
    def use(answer: str) -> None:
        monkeypatch.setattr(http_ai.httpx, "post", FakeAIServer(answer).post)
        monkeypatch.setattr("app.services.pipeline.get_ai_client", lambda: HttpAIClient())
    return use


def _customer_and_order(db):
    customer = db.scalar(select(Customer).where(Customer.email == "demo@example.invalid"))
    return customer, latest_order(db, customer.id)


def test_fill_from_team_db():
    with SessionLocal() as db:
        customer, order = _customer_and_order(db)
        assert order.order_no == LATEST_ORDER
        text, unfilled = fill(
            "{{name}} 고객님, 주문번호 {{orders.order_no}}({{product_name}})는 {{shipping.address}}로 배송 중이며 "
            "운송장 번호는 {{ shipping.tracking_number }}입니다. 결제 {{amount}}원({{payment_method}}, {{transaction_id}})",
            customer, order)
    assert unfilled == []
    assert text == ("테스트고객 고객님, 주문번호 20241210-1234567(무선 블루투스 이어폰 Pro)는 서울시 가상구 테스트로 1로 배송 중이며 "
                    "운송장 번호는 TRACK-0001입니다. 결제 89,000원(신용카드, PAY-0001)")


def test_unknown_or_missing_values_stay():
    with SessionLocal() as db:
        customer, order = _customer_and_order(db)
        text, unfilled = fill("{{name}}님 접수번호 {{case_no}}, 환불 {{refund_amount}}원, {{Website URL}}", customer, order)
        assert text == "테스트고객님 접수번호 {{case_no}}, 환불 {{refund_amount}}원, {{Website URL}}"   # 환불 기록·사이트 주소 없음
        assert unfilled == ["case_no", "refund_amount", "Website URL"]
        assert fill("주문번호 {{order_no}}", customer, None) == ("주문번호 {{order_no}}", ["order_no"])   # 주문이 없는 고객
    assert fill("치환자가 없는 답변", None, None) == ("치환자가 없는 답변", [])


def test_filled_answer_is_sent_to_customer(client, customer_headers, ai_answers):
    ai_answers("주문번호 {{orders.order_no}}의 운송장 번호는 {{shipping.tracking_number}}입니다.")
    d = client.post("/api/conversations", params={"wait": True}, json={"content": AUTO_Q}, headers=customer_headers).json()
    assert [m["role"] for m in d["messages"]] == ["CUSTOMER", "BOT"] and d["review_pending"] is False
    assert d["messages"][1]["content"] == "주문번호 20241210-1234567의 운송장 번호는 TRACK-0001입니다."   # 채워서 자동 전송


def test_unfilled_answer_goes_to_review(client, customer_headers, admin_headers, ai_answers):
    ai_answers("{{name}} 고객님, 접수번호 {{case_no}}로 확인해 드리겠습니다.")
    d = client.post("/api/conversations", params={"wait": True}, json={"content": AUTO_Q}, headers=customer_headers).json()
    assert d["review_pending"] is True and d["messages"][1]["is_notice"] is True          # 고객에게는 안내만
    assert all("{{" not in m["content"] for m in d["messages"])                           # 치환자가 고객 화면에 안 나감
    rq = client.get(f"/api/admin/reviews/{d['inquiry_no']}", headers=admin_headers).json()["review_question"]
    assert [x["code"] for x in rq["analysis"]["reasons"]] == ["UNFILLED_PLACEHOLDER"]
    assert rq["ai_draft"]["text"] == "테스트고객 고객님, 접수번호 {{case_no}}로 확인해 드리겠습니다."   # 채울 수 있는 것만 채운 초안
