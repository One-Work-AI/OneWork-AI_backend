"""★ 주문 정보를 가져오는 곳 — 쇼핑몰 DB가 준비되면 이 파일만 바꾸면 됩니다.

지금은 이 백엔드 DB의 local_order 테이블(가짜 주문)을 씁니다 (.env: ORDER_SOURCE=local).

쇼핑몰 DB에 연결할 때
1. 아래 OrderSource 형식에 맞는 클래스를 하나 만듭니다 (예: ShopDBOrderSource).
   - 쇼핑몰 DB 접속은 별도 엔진으로: create_engine(SHOP_DATABASE_URL)
   - 이 백엔드의 고객과 쇼핑몰 고객을 어떻게 맞출지 정해야 합니다 (전화번호·이메일, 또는 쇼핑몰 고객 ID 저장)
2. get_order_source()에서 .env 값에 따라 그 클래스를 돌려주게 합니다.
채팅에는 고른 주문의 주문번호·상품명을 사본으로 저장하므로, 쇼핑몰 DB가 바뀌어도 지난 문의 기록은 그대로입니다.
"""
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Customer, LocalOrder


class OrderInfo(BaseModel):
    order_no: str
    product_name: str
    ordered_at: datetime | None = None


class OrderSource(Protocol):
    def list_orders(self, db: Session, customer: Customer) -> list[OrderInfo]: ...
    def get_order(self, db: Session, customer: Customer, order_no: str) -> OrderInfo | None: ...


class LocalOrderSource:
    def list_orders(self, db: Session, customer: Customer) -> list[OrderInfo]:
        rows = db.scalars(select(LocalOrder).where(LocalOrder.customer_id == customer.id)
                          .order_by(LocalOrder.ordered_at.desc())).all()
        return [OrderInfo(order_no=o.order_no, product_name=o.product_name, ordered_at=o.ordered_at) for o in rows]

    def get_order(self, db: Session, customer: Customer, order_no: str) -> OrderInfo | None:
        o = db.scalar(select(LocalOrder).where(LocalOrder.customer_id == customer.id, LocalOrder.order_no == order_no))
        return OrderInfo(order_no=o.order_no, product_name=o.product_name, ordered_at=o.ordered_at) if o else None


def get_order_source() -> OrderSource:
    source = get_settings().order_source
    if source == "local":
        return LocalOrderSource()
    raise ValueError(f"알 수 없는 ORDER_SOURCE: {source}")
