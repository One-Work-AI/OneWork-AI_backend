"""시연용 계정 만들기 — 고객 1명(김민지)과 그 고객의 가짜 주문, 관리자 1명. 여러 번 실행해도 안전합니다.

    python -m scripts.seed_base

이름·연락처는 .env의 DEMO_CUSTOMER_* / DEMO_ADMIN_* 값을 씁니다 (가짜 정보).
가짜 주문은 쇼핑몰 DB가 연결되기 전까지 '문의할 주문 상품' 목록에 쓰입니다.
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import AdminUser, Customer, LocalOrder

# (주문번호, 상품명, 며칠 전 주문) — 가짜 데이터
DEMO_CUSTOMER_ORDERS = [
    ("20241210-1234567", "무선 블루투스 이어폰 Pro", 3),
    ("20241208-2345678", "데일리 코튼 니트 (베이지, M)", 5),
    ("20241205-3456789", "스테인리스 텀블러 500ml", 8),
]


def add_orders(db, customer: Customer, orders: list[tuple[str, str, int]]) -> int:
    now = datetime.now(UTC)
    added = 0
    for order_no, product, days_ago in orders:
        if db.scalar(select(LocalOrder).where(LocalOrder.order_no == order_no)) is None:
            db.add(LocalOrder(customer_id=customer.id, order_no=order_no, product_name=product,
                              ordered_at=now - timedelta(days=days_ago)))
            added += 1
    return added


def main():
    s = get_settings()
    with SessionLocal() as db:
        customer = db.scalar(select(Customer).where(Customer.phone == s.demo_customer_phone))
        if customer is None:
            customer = Customer(name=s.demo_customer_name, phone=s.demo_customer_phone, email=s.demo_customer_email)
            db.add(customer)
            db.flush()
            print(f"시연용 고객 생성: {s.demo_customer_name}")
        else:
            customer.name, customer.email = s.demo_customer_name, s.demo_customer_email
            print(f"시연용 고객 확인: {customer.name}")
        print(f"가짜 주문 {add_orders(db, customer, DEMO_CUSTOMER_ORDERS)}건 추가")

        admin = db.scalar(select(AdminUser).where(AdminUser.username == s.demo_admin_username))
        if admin is None:
            db.add(AdminUser(username=s.demo_admin_username, name=s.demo_admin_name))
            print(f"시연용 관리자 생성: {s.demo_admin_name}")
        else:
            admin.name, admin.is_active = s.demo_admin_name, True
            print(f"시연용 관리자 확인: {admin.name}")
        db.commit()


if __name__ == "__main__":
    main()
