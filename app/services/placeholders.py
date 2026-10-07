"""AI 답변의 치환자({{…}})를 팀 DB의 실제 값으로 채웁니다.

AI(EXAONE + LoRA)는 학습 데이터에 있던 치환자를 답변에 그대로 씁니다.
    "주문번호 {{orders.order_no}}의 운송장 번호는 {{shipping.tracking_number}}입니다."
AI 서버는 팀 DB를 모르기 때문에, 고객에게 보내기 전에 백엔드가 채웁니다.
    "주문번호 DEMO-ORDER-001의 운송장 번호는 DEMO-TRACK-001입니다."

규칙
- 고객 정보는 질문한 고객, 주문·배송·결제·환불 정보는 **채팅에서 고른 주문, 없으면 그 고객의 가장 최근 주문**에서 가져옵니다.
  (화면에서 주문 상품 선택을 뺐기 때문에 보통 가장 최근 주문이 쓰입니다)
- 값이 없거나 모르는 치환자는 그대로 남기고 목록으로 돌려줍니다 → pipeline이 자동 전송하지 않고 관리자 검토로 보냅니다.
- 금액은 1,000 단위 쉼표(단위는 붙이지 않음 — 학습 데이터가 "{{refund_amount}}원" 형태), 날짜는 한국 날짜(YYYY-MM-DD)로 넣습니다.

지원하는 치환자는 _values()의 키 목록입니다 (대소문자·앞뒤 공백 무시). 팀 DB에 없는 값
(reference_no, case_no, compensation_no 등)은 채우지 못합니다.
"""
import re
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Customer, Order
from app.plugins.order_source import product_name
from app.utils import to_kst

PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
PAYMENT_METHOD_LABELS = {"BANK_TRANSFER": "계좌이체", "CREDIT_CARD": "신용카드", "EASY_PAY": "간편결제"}


def latest_order(db: Session, customer_id: int) -> Order | None:
    return db.scalar(select(Order).where(Order.customer_id == customer_id)
                     .order_by(Order.order_date.desc(), Order.id.desc()).limit(1))


def _format(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, datetime):
        return to_kst(value).strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip() or None


def _values(customer: Customer | None, order: Order | None) -> dict[str, str | None]:
    """치환자 이름(소문자) → 넣을 글자. 값이 없으면 None."""
    site = get_settings().site_url.strip() or None
    v: dict[str, object] = {"website url": site, "customer support website": site}
    if customer is not None:
        v.update({"name": customer.name, "customer.name": customer.name,
                  "customer_id": str(customer.id), "customer.id": str(customer.id)})
    if order is not None:
        ship, pay, refund = order.shipping, order.payment, order.refund
        v.update({
            "order_no": order.order_no, "orders.order_no": order.order_no, "order_id": order.order_no,
            "orders.order_date": order.order_date, "orders.total_amount": order.total_amount,
            "product_name": product_name(order) if order.items else None,
        })
        if ship is not None:
            v.update({
                "shipping.address": ship.address, "shipping.recipient_name": ship.recipient_name,
                "shipping.tracking_number": ship.tracking_number, "tracking_number": ship.tracking_number,
                "shipping.shipping_method": ship.shipping_method, "shipping.carrier_name": ship.carrier_name,
                "shipping.estimated_delivery_date": ship.estimated_delivery_date,
            })
        if pay is not None:
            method = PAYMENT_METHOD_LABELS.get(pay.payment_method, pay.payment_method)
            v.update({
                "transaction_id": pay.transaction_id, "payment.transaction_id": pay.transaction_id,
                "amount": pay.amount, "payment.amount": pay.amount,
                "paid_at": pay.paid_at, "payment.paid_at": pay.paid_at,
                "payment_method": method, "payment.payment_method": method,
            })
        if refund is not None:
            v.update({"refund_amount": refund.refund_amount, "refund.refund_amount": refund.refund_amount})
    return {k: _format(x) for k, x in v.items()}


def fill(text: str, customer: Customer | None, order: Order | None) -> tuple[str, list[str]]:
    """치환자를 채운 글과, 채우지 못한 치환자 이름 목록을 돌려줍니다."""
    if "{{" not in text:
        return text, []
    values = _values(customer, order)
    unfilled: list[str] = []

    def _replace(m: re.Match) -> str:
        value = values.get(m.group(1).lower())
        if value is None:
            if m.group(1) not in unfilled:
                unfilled.append(m.group(1))
            return m.group(0)
        return value

    return PLACEHOLDER.sub(_replace, text), unfilled
