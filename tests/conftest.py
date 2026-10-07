import os
import tempfile
from pathlib import Path

import pgserver
import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
TEST_DB = "cs_chatbot_test"

# 테스트 전용 임시 PostgreSQL (pgserver, 설치 없이 실행) — 팀 DB·.env에 영향 없음.
# 팀 DB와 같은 구조를 만들려고 DB 담당자의 schema.sql 사본(tests/team_schema.sql)을 그대로 실행합니다.
# app을 불러오기 전에 설정해야 함.
_tmp = Path(tempfile.mkdtemp(prefix="cs_chatbot_test_"))
_pg = pgserver.get_server(_tmp / "pgdata", cleanup_mode="delete")
with psycopg.connect(_pg.get_uri(), autocommit=True) as _c:
    _c.execute(f"CREATE DATABASE {TEST_DB}")
with psycopg.connect(_pg.get_uri(TEST_DB), autocommit=True) as _c:
    _c.execute((Path(__file__).with_name("team_schema.sql")).read_text(encoding="utf-8"))

os.environ["DATABASE_URL"] = _pg.get_uri(TEST_DB).replace("postgresql://", "postgresql+psycopg://", 1)
os.environ["AI_MODE"] = "mock"
os.environ["JWT_SECRET"] = "test-secret-for-pytest-only-0123456789"
os.environ["CHAT_TIMEOUT_SWEEP_SECONDS"] = "0"          # 테스트에서는 백그라운드 자동 종료 확인 끔
os.environ["POLICY_FILE_DIR"] = (_tmp / "policies").as_posix()
os.environ["DEMO_CUSTOMER_EMAIL"] = "demo@example.invalid"
os.environ["DEMO_CUSTOMER_NAME"] = "테스트고객"
os.environ["DEMO_ADMIN_USERNAME"] = "extra_demo_admin"

from datetime import UTC, datetime, timedelta

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal, engine
from app.main import app
from app.models import Customer, Order, OrderItem, Payment, Product, Shipping
from app.services.policies import ingest_directory
from scripts import seed_base


def make_pdf(text: str) -> bytes:
    """글자가 들어 있는 아주 작은 PDF (영문만)."""
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 144] /Contents 4 0 R "
         b"/Resources << /Font << /F1 5 0 R >> >> >>"),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


# 테스트용 주문 (주문번호, 상품명, 가격, 며칠 전) — 화면에서는 주문 상품 선택을 뺐지만 API는 선택 항목으로 남아 있음
TEST_ORDERS = [
    ("20241210-1234567", "무선 블루투스 이어폰 Pro", 89000, 3),
    ("20241208-2345678", "데일리 코튼 니트 (베이지, M)", 39000, 5),
    ("20241205-3456789", "스테인리스 텀블러 500ml", 18000, 8),
]


def _add_test_customer_data() -> None:
    """빈 테스트 DB의 체험 고객에게 연락처와 주문을 붙임 (팀 DB에서는 하지 않음)."""
    now = datetime.now(UTC)
    with SessionLocal() as db:
        customer = db.scalar(select(Customer).where(Customer.email == get_settings().demo_customer_email))
        customer.phone = "01012345678"
        for order_no, name, price, days_ago in TEST_ORDERS:
            product = Product(product_name=name, price=price)
            db.add(product)
            db.flush()
            order = Order(order_no=order_no, customer_id=customer.id, order_date=now - timedelta(days=days_ago),
                          status="DELIVERED", total_amount=price)
            order.items = [OrderItem(product_id=product.id, product_name=name, quantity=1, price=price, amount=price)]
            if order_no == TEST_ORDERS[0][0]:    # 가장 최근 주문에만 배송·결제 정보 (치환자 채우기 테스트용)
                order.shipping = Shipping(recipient_name="테스트고객", address="서울시 가상구 테스트로 1",
                                          carrier_name="가상택배", tracking_number="TRACK-0001", status="SHIPPING")
                order.payment = Payment(payment_method="CREDIT_CARD", payment_status="COMPLETED", amount=price,
                                        transaction_id="PAY-0001", paid_at=now - timedelta(days=days_ago))
            db.add(order)
        db.commit()


@pytest.fixture(scope="session", autouse=True)
def _db():
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")     # 백엔드 전용 cs_backend 테이블
    seed_base.main()                                                 # 빈 DB라서 체험 고객·관리자를 새로 만듦
    _add_test_customer_data()
    with SessionLocal() as db:
        ingest_directory(db, ROOT / "policies_sample")
    yield
    engine.dispose()
    _pg.cleanup()


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def customer_headers(client):
    r = client.post("/api/demo/customer")
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="session")
def admin_headers(client):
    r = client.post("/api/demo/admin")
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
