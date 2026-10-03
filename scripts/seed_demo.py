"""시연용 문의 넣기 — 가짜 고객 여러 명이 실제 채팅·AI 처리 흐름을 그대로 거쳐서 모든 테이블이 채워집니다.

    python -m scripts.seed_demo --csv ..\\EXAONE_COMMON_SPLIT_SAFE\\validation_canonical_981.csv --n 20

- 고객: 시연용 고객(김민지) + 가짜 고객 여러 명 (이름·연락처·주문 모두 가짜)
- 질문은 CSV의 '문의 내용' 열에서 무작위로 뽑습니다. test 계열 파일(최종 평가용)은 쓰지 마세요.
- 채팅마다 주문 상품을 고르거나 '선택 안 함'으로 시작하고 질문 1~2개를 보낸 뒤 종료합니다.
  검토로 간 채팅은 '검토대기'로 남습니다 (관리자 화면에서 승인해 볼 수 있음).
- 채팅에 시연용 표시(conversation_ext.is_demo)가 붙습니다. 대시보드에서 include_demo=false로 뺄 수 있고,
  학습 데이터 내보내기에서는 자동으로 빠집니다.
- 팀 DB는 상담 기록(AI 분석·검토 기록·전송된 답변)을 지울 수 없게 되어 있어서, 한 번 넣은 시연 데이터는 삭제하지 않습니다.
  실제 운영 DB가 아닌 연습용 DB에서만 실행하세요.
- 먼저 python -m scripts.seed_base 로 시연용 계정을 만들어야 합니다.
"""
import argparse
import csv
import random

from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionLocal
from app.enums import CloseReason
from app.models import ConversationExt, Customer, Inquiry
from app.services import conversations as svc
from app.services.pipeline import process_question
from scripts.seed_base import add_orders

# 가짜 고객 (이름, 전화번호, 이메일, [(주문번호, 상품명, 가격, 며칠 전)])
FAKE_CUSTOMERS = [
    ("박서연", "01023456789", "seoyeon.park@email.com", [("20241209-5551234", "에어프라이어 5.5L", 119000, 2)]),
    ("김하늘", "01034567890", "haneul@email.com", [("20241207-6662345", "세라믹 머그컵 2P 세트", 24000, 4)]),
    ("정다은", "01067890123", "daeun@email.com", []),
    ("이준호", "01045678901", "junho.lee@email.com", [("20241206-7773456", "캠핑 의자 2인 세트", 79000, 6)]),
    ("최지훈", "01056789012", "jihoon.choi@email.com", [("20241204-8884567", "기계식 키보드 (적축)", 129000, 9)]),
]


def main():
    p = argparse.ArgumentParser(description="시연용 문의 넣기")
    p.add_argument("--csv", required=True, help="'문의 내용' 열이 있는 CSV")
    p.add_argument("--n", type=int, default=20, help="만들 채팅 수")
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    with open(args.csv, encoding="utf-8-sig", newline="") as f:
        questions = [row["문의 내용"].strip() for row in csv.DictReader(f) if (row.get("문의 내용") or "").strip()]
    rng = random.Random(args.seed)
    rng.shuffle(questions)

    with SessionLocal() as db:
        demo = db.scalar(select(Customer).where(Customer.phone == get_settings().demo_customer_phone))
        if demo is None:
            raise SystemExit("시연용 고객이 없습니다. python -m scripts.seed_base 를 먼저 실행하세요.")
        customer_ids = [demo.id]
        for name, phone, email, orders in FAKE_CUSTOMERS:
            c = db.scalar(select(Customer).where(Customer.phone == phone).order_by(Customer.id))
            if c is None:
                c = Customer(name=name, phone=phone, email=email)
                db.add(c)
                db.flush()
            add_orders(db, c, orders)
            customer_ids.append(c.id)
        db.commit()

    for k in range(args.n):
        cid = customer_ids[k % len(customer_ids)]
        with SessionLocal() as db:
            customer = db.get(Customer, cid)
            orders = svc.list_orders(db, customer)
            order_no = rng.choice(orders).order_no if orders and rng.random() < 0.7 else None
            conv, question = svc.start_conversation(db, customer, questions.pop()[:2000], order_no, is_demo=True)
            inquiry_no, qid = conv.ext.inquiry_no, question.id
        process_question(qid)
        if rng.random() < 0.3:      # 가끔 이어서 질문
            with SessionLocal() as db:
                qid = svc.add_message(db, db.get(Customer, cid), inquiry_no, questions.pop()[:2000]).id
            process_question(qid)
        with SessionLocal() as db:
            svc.close_conversation(db, db.get(Customer, cid), inquiry_no, CloseReason.USER)
        if (k + 1) % 5 == 0:
            print(f"{k + 1}/{args.n}")

    with SessionLocal() as db:
        counts = dict(db.execute(select(Inquiry.status, func.count(Inquiry.id))
                                 .join(ConversationExt, ConversationExt.conversation_id == Inquiry.conversation_id)
                                 .where(ConversationExt.is_demo.is_(True)).group_by(Inquiry.status)).all())
    print("완료 (시연 질문 상태별):", counts)


if __name__ == "__main__":
    main()
