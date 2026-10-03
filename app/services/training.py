"""학습 데이터 내보내기 — 관리자가 '학습 데이터로 사용'을 체크하고 승인한 질문을 CSV로.

열 이름은 train.csv와 같게 맞춰서 다음 학습 때 그대로 이어 붙일 수 있게 합니다.
채팅 중 이어지는 질문은 앞 대화를 봐야 이해되는 경우가 있어서 문의번호와 질문 순서(turn)를 함께 넣습니다.
시연용 데이터(is_demo)는 제외합니다.
"""
import csv
import io

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.models import AdminReview, AIResponse, ConversationExt, Inquiry
from app.utils import to_kst

COLUMNS = ["플래그", "문의 내용", "카테고리", "의도", "응답", "source_file", "inquiry_no", "turn", "reviewed_at"]


def export_training_csv(db: Session) -> str:
    earlier = aliased(Inquiry)
    turn = (select(func.count(earlier.id))
            .where(earlier.conversation_id == Inquiry.conversation_id, earlier.id <= Inquiry.id)
            .scalar_subquery())
    rows = db.execute(
        select(Inquiry.content, AdminReview, ConversationExt.inquiry_no, turn)
        .join(AIResponse, AIResponse.id == AdminReview.response_id)
        .join(Inquiry, Inquiry.id == AIResponse.inquiry_id)
        .join(ConversationExt, ConversationExt.conversation_id == Inquiry.conversation_id)
        .where(AdminReview.use_for_training.is_(True), AdminReview.approved.is_(True),
               ConversationExt.is_demo.is_(False))
        .order_by(AdminReview.reviewed_at)
    ).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(COLUMNS)
    for content, rv, inquiry_no, n in rows:
        w.writerow(["", content, rv.modified_category, rv.modified_intent, rv.modified_response,
                    "admin_review", inquiry_no, n, to_kst(rv.reviewed_at).isoformat()])
    return "﻿" + buf.getvalue()   # 엑셀에서 한글이 안 깨지게 BOM (train.csv와 같은 utf-8-sig)
