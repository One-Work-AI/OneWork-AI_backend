"""시연용 데이터 전부 삭제 — 시연 문의, 가짜 고객과 그 주문. 시연용 계정(김민지·관리자)과 김민지의 주문은 남깁니다.

    python -m scripts.delete_demo
"""
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import Conversation, Customer, LocalOrder


def main():
    with SessionLocal() as db:
        convs = db.scalars(select(Conversation).where(Conversation.is_demo.is_(True))).all()
        for c in convs:
            db.delete(c)   # 메시지·분석·초안·검색 기록·검토 기록도 같이 삭제됨
        db.flush()
        fake_ids = list(db.scalars(select(Customer.id).where(Customer.is_demo.is_(True))))
        removed = 0
        for cid in fake_ids:
            if db.scalar(select(Conversation.id).where(Conversation.customer_id == cid).limit(1)) is None:
                db.execute(delete(LocalOrder).where(LocalOrder.customer_id == cid))
                db.execute(delete(Customer).where(Customer.id == cid))
                removed += 1
        db.commit()
    print(f"시연 문의 {len(convs)}건, 가짜 고객 {removed}명 삭제")


if __name__ == "__main__":
    main()
