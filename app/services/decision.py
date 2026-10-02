"""자동답변 판단 — 신뢰도가 기준(REVIEW_MIN_CONFIDENCE, 기본 0.8) 이상이면 챗봇이 자동답변, 아니면 관리자 검토.

- 신뢰도 = AI 서버가 준 카테고리·의도 신뢰도 중 작은 값. AI 서버가 신뢰도를 주지 않으면 기준을 확인할 수 없으므로 검토로 보냅니다.
- 항상 검토: AI 서버 오류, 빈 답변, 정의되지 않은 카테고리·의도 (고객에게 보낼 수 있는 답변이 없거나 라벨이 깨져 있음)
- 추가 규칙(검색 관련도, 항상 검토할 의도, 짧은 답변)은 코드에 남아 있고 기본은 꺼져 있습니다. .env에서 켤 수 있습니다.
"""
from dataclasses import dataclass, field

from app.config import Settings
from app.enums import CATEGORY_INTENTS, INTENT_TO_CATEGORY, ReviewReason
from app.plugins.ai_client import AIResult


@dataclass
class Decision:
    auto_send: bool
    reasons: list[ReviewReason] = field(default_factory=list)


def decide(result: AIResult | None, error: str | None, s: Settings) -> Decision:
    if error is not None or result is None:
        return Decision(False, [ReviewReason.AI_ERROR])

    reasons: list[ReviewReason] = []
    answer = (result.answer or "").strip()

    if not answer:
        reasons.append(ReviewReason.EMPTY_ANSWER)
    if (result.intent not in INTENT_TO_CATEGORY
            or result.category not in CATEGORY_INTENTS
            or INTENT_TO_CATEGORY[result.intent] != result.category):
        reasons.append(ReviewReason.INVALID_CLASSIFICATION)

    # 기본 규칙: 신뢰도
    given = [c for c in (result.category_confidence, result.intent_confidence) if c is not None]
    if not given:
        reasons.append(ReviewReason.NO_CONFIDENCE)
    elif min(given) < s.review_min_confidence:
        reasons.append(ReviewReason.LOW_CONFIDENCE)

    # 추가 규칙 (기본 꺼짐)
    if s.review_low_retrieval_enabled:
        scores = [src.score for src in result.sources if src.score is not None]
        if not result.sources or (scores and max(scores) < s.review_min_retrieval_score):
            reasons.append(ReviewReason.LOW_RETRIEVAL_SCORE)
    if s.review_always_intents_enabled and result.intent in s.review_always_intents:
        reasons.append(ReviewReason.ALWAYS_REVIEW_INTENT)
    if s.review_short_answer_enabled and answer and len(answer) < s.review_min_answer_chars:
        reasons.append(ReviewReason.SHORT_ANSWER)

    return Decision(auto_send=not reasons, reasons=reasons)
