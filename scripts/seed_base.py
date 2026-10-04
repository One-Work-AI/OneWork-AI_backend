"""시연용 계정 확인 — '고객으로 체험' / '관리자로 체험'에 쓸 계정이 DB에 있는지 확인합니다. 여러 번 실행해도 안전합니다.

    python -m scripts.seed_base

- 팀 DB에 이미 있는 가상 계정을 그대로 씁니다 (.env 기본값)
  고객: DEMO_CUSTOMER_EMAIL = demo@example.invalid (팀 샘플 '테스트고객')
  관리자: DEMO_ADMIN_USERNAME = extra_demo_admin (팀 확장 샘플 관리자)
- 이미 있는 계정은 바꾸지 않습니다. 없을 때만(빈 연습용 DB, 테스트 DB) 새로 만듭니다.
- 관리자를 새로 만들 때는 체험 입장 전용이라 비밀번호로는 로그인할 수 없는 값을 password_hash에 넣습니다.
"""
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import AdminUser, Customer

NO_PASSWORD_LOGIN = "!DEMO_ENTRY_ONLY_NO_PASSWORD_LOGIN!"


def main():
    s = get_settings()
    with SessionLocal() as db:
        customer = db.scalar(select(Customer).where(Customer.email == s.demo_customer_email).order_by(Customer.id))
        if customer is None:
            db.add(Customer(name=s.demo_customer_name, email=s.demo_customer_email))
            print(f"시연용 고객 생성: {s.demo_customer_name} ({s.demo_customer_email})")
        else:
            print(f"시연용 고객 확인: {customer.name} ({customer.email}) 그대로 사용")

        admin = db.scalar(select(AdminUser).where(AdminUser.login_id == s.demo_admin_username))
        if admin is None:
            db.add(AdminUser(login_id=s.demo_admin_username, password_hash=NO_PASSWORD_LOGIN, name=s.demo_admin_name))
            print(f"시연용 관리자 생성: {s.demo_admin_name} ({s.demo_admin_username})")
        elif not admin.is_active:
            print(f"시연용 관리자 {admin.login_id}가 비활성 상태입니다. 활성 관리자로 DEMO_ADMIN_USERNAME을 바꿔 주세요.")
        else:
            print(f"시연용 관리자 확인: {admin.name} ({admin.login_id}) 그대로 사용")
        db.commit()


if __name__ == "__main__":
    main()
