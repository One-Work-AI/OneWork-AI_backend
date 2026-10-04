"""관리자 운영 대시보드 — 전체 기간 기준.

- 전체 문의 = 검토대기 + 답변완료 (상담 중인 채팅은 끝난 뒤에 셈)
- 답변완료는 누가 답했는지로 나눔: AI(관리자 답변 없음) / 관리자
- AI 자동 응답률 = AI가 끝까지 답한 문의 / 전체 문의
- 시간대별 현황: 전체 기간의 고객 질문을 시간대(한국 시간)별로 합침
- 피크 타임: 질문이 가장 많이 들어온 2시간 구간과 그 구간에 가장 많은 문의 유형
집계는 파이썬에서 합니다 (프로젝트 규모에서는 충분히 빠름).
"""
from collections import Counter

from sqlalchemy.orm import Session

from app.enums import UNCLASSIFIED, display_category
from app.models import Conversation, ConversationExt
from app.schemas import CountItem, DashboardOut, HourlyItem, PeakWindow, PendingItem
from app.services.conversations import answered_by, chats, close_idle_conversations, question_answered_at, questions_of, status_code
from app.services.reviews import pending_questions, pending_reasons, waiting_minutes
from app.utils import to_kst

PEAK_HOURS = 2


def build_dashboard(db: Session, include_demo: bool) -> DashboardOut:
    close_idle_conversations()    
    cond = [] if include_demo else [ConversationExt.is_demo.is_(False)]
    convs = db.scalars(chats().where(*cond)).all()

    by_status: dict[str, list[Conversation]] = {"CHATTING": [], "REVIEWING": [], "ANSWERED": []}
    for c in convs:
        by_status[status_code(c)].append(c)
    pending, answered = by_status["REVIEWING"], by_status["ANSWERED"]
    counted = pending + answered
    total = len(counted)
    ai_done = sum(1 for c in answered if answered_by(c) == "AI")
    admin_done = sum(1 for c in answered if answered_by(c) == "ADMIN")

    cats = Counter(display_category(c.ext.category) or UNCLASSIFIED for c in counted)

    # 시간대별 (전체 기간, 고객 질문 단위)
    questions = [(q, c) for c in convs for q in questions_of(c)]
    received = Counter(to_kst(q.created_at).hour for q, _ in questions)
    answered_h = Counter(to_kst(t).hour for q, _ in questions if (t := question_answered_at(q)))
    hourly = [HourlyItem(hour=h, received=received.get(h, 0), answered=answered_h.get(h, 0)) for h in range(24)]

    peak = None
    if received:
        start = max(range(24 - PEAK_HOURS + 1), key=lambda h: sum(received.get(h + i, 0) for i in range(PEAK_HOURS)))
        in_window = [c for q, c in questions if start <= to_kst(q.created_at).hour < start + PEAK_HOURS]
        top = Counter(display_category(c.ext.category) or UNCLASSIFIED for c in in_window).most_common(1)
        peak = PeakWindow(start_hour=start, end_hour=start + PEAK_HOURS, received=len(in_window),
                          top_category=top[0][0] if top else None)

    pending_list = []
    for c in pending:
        pend = pending_questions(c)
        pending_list.append(PendingItem(
            inquiry_no=c.ext.inquiry_no, customer_name=c.customer.name, category=display_category(c.ext.category),
            product_name=c.ext.product_name, preview=pend[0].content, created_at=c.created_at,
            waiting_minutes=waiting_minutes(pend) or 0, reasons=pending_reasons(pend)))
    pending_list.sort(key=lambda p: -p.waiting_minutes)  

    return DashboardOut(
        total_inquiries=total,
        chatting_now=len(by_status["CHATTING"]),
        pending_review=len(pending),
        answered={"total": len(answered), "ai": ai_done, "admin": admin_done},
        ai_auto_rate=round(ai_done / total, 4) if total else None,
        by_category=[CountItem(name=k, count=v, ratio=round(v / total, 4)) for k, v in cats.most_common()],
        hourly=hourly,
        peak_window=peak,
        pending_list=pending_list,
    )