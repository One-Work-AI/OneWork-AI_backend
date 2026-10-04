"""체험 입장 — 시작 화면의 '고객으로 체험' / '관리자로 체험' 버튼. 비밀번호 없이 토큰을 줍니다.

팀 DB에 있는 가상 계정(.env의 DEMO_CUSTOMER_EMAIL / DEMO_ADMIN_USERNAME)으로 입장합니다.
계정이 있는지는 scripts.seed_base 로 확인할 수 있습니다. 로그아웃은 프론트에서 토큰을 지우면 됩니다.
나중에 진짜 로그인을 붙일 때는 이 파일만 바꾸면 되고, 나머지 API는 그대로 토큰을 씁니다.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.errors import AppError
from app.models import AdminUser, Customer
from app.schemas import TokenOut
from app.security import create_access_token

router = APIRouter(prefix="/api/demo", tags=["체험 입장"])

NOT_SEEDED = "시연용 계정이 없습니다. 서버에서 python -m scripts.seed_base 로 확인하세요."


@router.post("/customer", response_model=TokenOut, summary="고객으로 체험 (팀 DB의 시연용 고객으로 입장)")
def enter_as_customer(db: Session = Depends(get_db)):
    s = get_settings()
    customer = db.scalar(select(Customer).where(Customer.email == s.demo_customer_email).order_by(Customer.id))
    if customer is None:
        raise AppError(503, "DEMO_NOT_READY", NOT_SEEDED)
    token, expires_in = create_access_token("customer", customer.id)
    return TokenOut(access_token=token, expires_in=expires_in, role="customer", name=customer.name)


@router.post("/admin", response_model=TokenOut, summary="관리자로 체험 (비밀번호 없이 입장)")
def enter_as_admin(db: Session = Depends(get_db)):
    admin = db.scalar(select(AdminUser).where(AdminUser.login_id == get_settings().demo_admin_username))
    if admin is None or not admin.is_active:
        raise AppError(503, "DEMO_NOT_READY", NOT_SEEDED)
    token, expires_in = create_access_token("admin", admin.id)
    return TokenOut(access_token=token, expires_in=expires_in, role="admin", name=admin.name)
