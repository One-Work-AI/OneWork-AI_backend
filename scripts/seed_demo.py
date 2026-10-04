"""시연용 문의 넣기 — 가상 고객 여러 명이 실제 채팅·AI 처리 흐름을 그대로 거쳐서 모든 테이블이 채워집니다.

    python -m scripts.seed_demo --csv ..\\EXAONE_COMMON_SPLIT_SAFE\\validation_canonical_981.csv --n 20

- 고객: 팀 DB에 이미 있는 가상 고객 (--customers 명, 시연용 체험 고객 포함). 새 고객·주문은 만들지 않습니다.
- 질문은 CSV의 '문의 내용' 열에서 무작위로 뽑습니다. test 계열 파일(최종 평가용)은 쓰지 마세요.
- 채팅마다 질문 1~2개를 보낸 뒤 종료합니다 (주문 상품은 고르지 않음).
  검토로 간 채팅은 '검토대기'로 남습니다 (관리자 화면에서 승인해 볼 수 있음).
- 채팅에 시연용 표시(conversation_ext.is_demo)가 붙습니다. 대시보드에서 include_demo=false로 뺄 수 있고,
  학습 데이터 내보내기에서는 자동으로 빠집니다.
- 팀 DB는 상담 기록(AI 분석·검토 기록·전송된 답변)을 지울 수 없게 되어 있어서, 한 번 넣은 시연 데이터는 삭제하지 않습니다.
  실제 운영 DB가 아닌 연습용 DB에서만 실행하세요.
- 먼저 python -m scripts.seed_base 로 시연용 계정을 확인하세요.
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


def main():
    p = argparse.ArgumentParser(description="시연용 문의 넣기")
    p.add_argument("--csv", required=True, help="'문의 내용' 열이 있는 CSV")
    p.add_argument("--n", type=int, default=20, help="만들 채팅 수")
    p.add_argument("--customers", type=int, default=6, help="문의를 나눠 넣을 고객 수 (팀 DB의 기존 고객)")
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    with open(args.csv, encoding="utf-8-sig", newline="") as f:
        questions = [row["문의 내용"].strip() for row in csv.DictReader(f) if (row.get("문의 내용") or "").strip()]
    rng = random.Random(args.seed)
    rng.shuffle(questions)

    with SessionLocal() as db:
        demo = db.scalar(select(Customer.id).where(Customer.email == get_settings().demo_customer_email)
                         .order_by(Customer.id))
        if demo is None:
            raise SystemExit("시연용 고객이 없습니다. python -m scripts.seed_base 로 확인하세요.")
        others = db.scalars(select(Customer.id).where(Customer.id != demo).order_by(Customer.id)
                            .limit(max(args.customers - 1, 0))).all()
        customer_ids = [demo, *others]

    for k in range(args.n):
        cid = customer_ids[k % len(customer_ids)]
        with SessionLocal() as db:
            customer = db.get(Customer, cid)
            conv, question = svc.start_conversation(db, customer, questions.pop()[:2000], is_demo=True)
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
