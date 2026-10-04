"""★ 주문 정보를 가져오는 곳 — 지금은 팀 DB의 orders / order_item 테이블을 읽습니다 (.env: ORDER_SOURCE=team).

다른 쇼핑몰 DB에서 읽게 될 때
1. 아래 OrderSource 형식에 맞는 클래스를 하나 만듭니다 (예: ShopDBOrderSource).
2. get_order_source()에서 .env 값에 따라 그 클래스를 돌려주게 합니다.
채팅에는 고른 주문의 주문번호·상품명을 사본으로 저장하므로, 주문 정보가 바뀌어도 지난 문의 기록은 그대로입니다.
"""
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Customer, Order


class OrderInfo(BaseModel):
    order_id: int | None = None    # 팀 DB orders.id (inquiry.order_id에 연결)
    order_no: str
    product_name: str
    ordered_at: datetime | None = None


class OrderSource(Protocol):
    def list_orders(self, db: Session, customer: Customer) -> list[OrderInfo]: ...
    def get_order(self, db: Session, customer: Customer, order_no: str) -> OrderInfo | None: ...


def product_name(order: Order) -> str:
    """주문 상품 이름. 상품이 여러 개면 '첫 상품 외 N건'."""
    if not order.items:
        return "(상품 정보 없음)"
    first = order.items[0].product_name
    return f"{first} 외 {len(order.items) - 1}건" if len(order.items) > 1 else first


def _info(o: Order) -> OrderInfo:
    return OrderInfo(order_id=o.id, order_no=o.order_no, product_name=product_name(o), ordered_at=o.order_date)


class TeamOrderSource:
    def list_orders(self, db: Session, customer: Customer) -> list[OrderInfo]:
        rows = db.scalars(select(Order).where(Order.customer_id == customer.id)
                          .order_by(Order.order_date.desc(), Order.id.desc())).all()
        return [_info(o) for o in rows]

    def get_order(self, db: Session, customer: Customer, order_no: str) -> OrderInfo | None:
        o = db.scalar(select(Order).where(Order.customer_id == customer.id, Order.order_no == order_no))
        return _info(o) if o else None


def get_order_source() -> OrderSource:
    source = get_settings().order_source
    if source == "team":
        return TeamOrderSource()
    raise ValueError(f"알 수 없는 ORDER_SOURCE: {source}")
