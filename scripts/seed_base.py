"""시연용 계정 만들기 — 팀 DB에 고객 1명(김민지)과 그 고객의 주문, 관리자 1명. 여러 번 실행해도 안전합니다.

    python -m scripts.seed_base

이름·연락처는 .env의 DEMO_CUSTOMER_* / DEMO_ADMIN_* 값을 씁니다 (가짜 정보).
주문은 팀 DB의 product / orders / order_item에 넣고, '문의할 주문 상품' 목록에 쓰입니다.
관리자는 체험 입장 전용이라 비밀번호로는 로그인할 수 없는 값을 password_hash에 넣습니다.
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import AdminUser, Customer, Order, OrderItem, Product

NO_PASSWORD_LOGIN = "!DEMO_ENTRY_ONLY_NO_PASSWORD_LOGIN!"

# (주문번호, 상품명, 가격, 며칠 전 주문) — 가짜 데이터
DEMO_CUSTOMER_ORDERS = [
    ("20241210-1234567", "무선 블루투스 이어폰 Pro", 89000, 3),
    ("20241208-2345678", "데일리 코튼 니트 (베이지, M)", 39000, 5),
    ("20241205-3456789", "스테인리스 텀블러 500ml", 18000, 8),
]


def _product(db, name: str, price: int) -> Product:
    p = db.scalar(select(Product).where(Product.product_name == name).order_by(Product.id).limit(1))
    if p is None:
        p = Product(product_name=name, price=price, stock_quantity=100)
        db.add(p)
        db.flush()
    return p


def add_orders(db, customer: Customer, orders: list[tuple[str, str, int, int]]) -> int:
    now = datetime.now(UTC)
    added = 0
    for order_no, name, price, days_ago in orders:
        if db.scalar(select(Order.id).where(Order.order_no == order_no)) is not None:
            continue
        product = _product(db, name, price)
        order = Order(order_no=order_no, customer_id=customer.id, order_date=now - timedelta(days=days_ago),
                      status="DELIVERED", total_amount=price)
        order.items = [OrderItem(product_id=product.id, product_name=name, quantity=1, price=price,
                                 discount_amount=0, amount=price)]
        db.add(order)
        added += 1
    return added


def main():
    s = get_settings()
    with SessionLocal() as db:
        customer = db.scalar(select(Customer).where(Customer.phone == s.demo_customer_phone).order_by(Customer.id))
        if customer is None:
            customer = Customer(name=s.demo_customer_name, phone=s.demo_customer_phone, email=s.demo_customer_email)
            db.add(customer)
            db.flush()
            print(f"시연용 고객 생성: {s.demo_customer_name}")
        else:
            customer.name, customer.email = s.demo_customer_name, s.demo_customer_email
            print(f"시연용 고객 확인: {customer.name}")
        print(f"주문 {add_orders(db, customer, DEMO_CUSTOMER_ORDERS)}건 추가")

        admin = db.scalar(select(AdminUser).where(AdminUser.login_id == s.demo_admin_username))
        if admin is None:
            db.add(AdminUser(login_id=s.demo_admin_username, password_hash=NO_PASSWORD_LOGIN, name=s.demo_admin_name))
            print(f"시연용 관리자 생성: {s.demo_admin_name}")
        else:
            admin.name, admin.is_active = s.demo_admin_name, True
            print(f"시연용 관리자 확인: {admin.name}")
        db.commit()


if __name__ == "__main__":
    main()
