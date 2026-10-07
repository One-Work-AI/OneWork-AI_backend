"""고정 코드값 — 채팅·질문 상태, 메시지 종류, 문의 유형, 검토 사유.

문의 유형
- DB와 AI는 학습 데이터(train.csv)와 같은 카테고리 7개 / 의도 17개를 씁니다 (CATEGORY_INTENTS).
- 화면에는 5개(배송 / 결제 / 교환/환불 / 주문 / 기타)로 바꿔서 보여줍니다 (DISPLAY_CATEGORY).
"""
from enum import Enum


class ConversationStatus(str, Enum):
    OPEN = "OPEN"        # 상담 중
    CLOSED = "CLOSED"    # 상담 종료


class CloseReason(str, Enum):
    USER = "USER"                      # 고객이 '채팅 종료하기'를 누름
    NEW_CHAT = "NEW_CHAT"              # 고객이 '새 채팅하기'를 누름
    TIMEOUT = "TIMEOUT"                # 챗봇·상담원 답변 후 일정 시간 동안 고객 응답 없음


CLOSE_REASON_LABELS = {
    CloseReason.USER: "고객 종료",
    CloseReason.NEW_CHAT: "새 채팅 시작",
    CloseReason.TIMEOUT: "응답 없음 자동 종료",
}


class MessageRole(str, Enum):
    CUSTOMER = "CUSTOMER"
    BOT = "BOT"          # 챗봇(AI 상담사) — 자동답변, 또는 '담당자에게 전달했어요' 안내
    ADMIN = "ADMIN"      # 관리자(상담원)가 검토 후 보낸 답변


class QuestionStatus(str, Enum):
    """고객 메시지(질문) 하나의 처리 상태."""
    PROCESSING = "PROCESSING"            # AI 처리 중
    AUTO_ANSWERED = "AUTO_ANSWERED"      # 챗봇이 자동답변
    REVIEW_PENDING = "REVIEW_PENDING"    # 관리자 검토 대기
    ADMIN_ANSWERED = "ADMIN_ANSWERED"    # 관리자가 검토 후 답변


# 팀 DB inquiry.status → 질문 상태
QUESTION_STATUS_FROM_DB: dict[str, QuestionStatus] = {
    "RECEIVED": QuestionStatus.PROCESSING,
    "PROCESSING": QuestionStatus.PROCESSING,
    "AUTO_ANSWERED": QuestionStatus.AUTO_ANSWERED,
    "REVIEW_PENDING": QuestionStatus.REVIEW_PENDING,
    "FAILED": QuestionStatus.REVIEW_PENDING,       # 백엔드는 쓰지 않지만, 생기면 사람이 봐야 하므로 검토대기
    "COMPLETED": QuestionStatus.ADMIN_ANSWERED,
}


def db_statuses(status: QuestionStatus) -> list[str]:
    """질문 상태 → 해당하는 팀 DB inquiry.status 값 (SQL 조건용)."""
    return [k for k, v in QUESTION_STATUS_FROM_DB.items() if v == status]


QUESTION_STATUS_LABELS = {
    QuestionStatus.PROCESSING: "AI 처리 중",
    QuestionStatus.AUTO_ANSWERED: "AI 답변",
    QuestionStatus.REVIEW_PENDING: "검토대기",
    QuestionStatus.ADMIN_ANSWERED: "관리자 답변",
}

# 고객 화면 문의 상태 (검토 대기 중인 문의도 관리자가 답할 때까지 '상담 중')
CUSTOMER_STATUS_LABELS = {
    "CHATTING": "상담 중",
    "ANSWERED": "답변완료",
}
# 관리자 화면 문의 상태
INQUIRY_STATUS_LABELS = {
    "CHATTING": "상담 중",     # 채팅 진행 중 (검토 대기 질문 없음)
    "REVIEWING": "검토대기",   # 관리자 검토가 필요한 질문이 있음
    "ANSWERED": "답변완료",    # 채팅이 끝났고 검토할 질문이 없음
}
# 답변완료일 때 누가 답했는지
ANSWERED_BY_LABELS = {"AI": "AI 답변", "ADMIN": "관리자 답변"}


CATEGORY_INTENTS: dict[str, list[str]] = {
    "결제": ["결제 문제", "결제 수단 확인", "청구서 받기", "청구서 확인"],
    "문의": ["고객 서비스 문의"],
    "배송": ["배송 기간 확인", "배송 방법 확인"],
    "배송지": ["배송지 변경", "배송지 설정"],
    "주문": ["주문 배송 조회", "주문 변경", "주문 취소", "주문하기"],
    "취소": ["취소 수수료 확인"],
    "환불": ["환불 정책 확인", "환불 진행 상황 조회", "환불받기"],
}
INTENT_TO_CATEGORY: dict[str, str] = {i: c for c, intents in CATEGORY_INTENTS.items() for i in intents}

# 화면에 보여줄 문의 유형 5개
DISPLAY_CATEGORIES = ["배송", "결제", "교환/환불", "주문", "기타"]
DISPLAY_CATEGORY: dict[str, str] = {
    "배송": "배송", "배송지": "배송",
    "결제": "결제",
    "환불": "교환/환불",
    "주문": "주문", "취소": "주문",
    "문의": "기타",
}
UNCLASSIFIED = "미분류"


def display_category(category: str | None) -> str | None:
    """DB 카테고리(7개) → 화면 유형(5개). 분류 전이면 None."""
    return DISPLAY_CATEGORY.get(category) if category else None


def categories_for_display(display: str) -> list[str]:
    """화면 유형(5개) → 해당하는 DB 카테고리 목록 (목록 거르기용)."""
    return [c for c, d in DISPLAY_CATEGORY.items() if d == display]


class ReviewReason(str, Enum):
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    NO_CONFIDENCE = "NO_CONFIDENCE"
    LOW_RETRIEVAL_SCORE = "LOW_RETRIEVAL_SCORE"
    ALWAYS_REVIEW_INTENT = "ALWAYS_REVIEW_INTENT"
    SHORT_ANSWER = "SHORT_ANSWER"
    EMPTY_ANSWER = "EMPTY_ANSWER"
    INVALID_CLASSIFICATION = "INVALID_CLASSIFICATION"
    AI_ERROR = "AI_ERROR"
    UNFILLED_PLACEHOLDER = "UNFILLED_PLACEHOLDER"


REVIEW_REASON_LABELS = {
    ReviewReason.LOW_CONFIDENCE: "신뢰도 기준 미만",
    ReviewReason.NO_CONFIDENCE: "신뢰도 없음",
    ReviewReason.LOW_RETRIEVAL_SCORE: "검색 관련도 낮음",
    ReviewReason.ALWAYS_REVIEW_INTENT: "항상 검토하는 의도",
    ReviewReason.SHORT_ANSWER: "답변이 너무 짧음",
    ReviewReason.EMPTY_ANSWER: "답변 없음",
    ReviewReason.INVALID_CLASSIFICATION: "정의되지 않은 유형·의도",
    ReviewReason.AI_ERROR: "AI 서버 오류",
    ReviewReason.UNFILLED_PLACEHOLDER: "채우지 못한 빈칸이 있음",
}
