"""가짜 AI (.env: AI_MODE=mock) — 모델 서버 없이 전체 흐름을 개발·시연하기 위한 것.

- 분류: 키워드 규칙
- 검색: keyword_retriever (팀 DB에 등록된 정책 문서)
- 답변: 가장 관련 있는 정책 조각의 앞부분을 붙여서 만듦
- 이전 대화(history)는 쓰지 않음
실제 모델 품질과는 관계없으니 점수·답변 내용으로 모델을 평가하지 마세요.
"""
import re

from app.db import SessionLocal
from app.enums import INTENT_TO_CATEGORY
from app.plugins import keyword_retriever
from app.plugins.ai_client import AIRequest, AIResult, AISource

INTENT_KEYWORDS: dict[str, list[str]] = {
    "결제 문제": ["결제 오류", "결제가 안", "결제 실패", "중복 결제", "결제 문제", "결제가 되지", "두 번 결제"],
    "결제 수단 확인": ["결제 수단", "결제 방법", "카드", "페이", "무통장", "계좌이체"],
    "청구서 받기": ["청구서 받", "청구서 발급", "영수증 발급", "청구서를 보내", "세금계산서"],
    "청구서 확인": ["청구서 확인", "청구서 조회", "청구 내역", "영수증 확인", "청구서"],
    "고객 서비스 문의": ["고객센터", "상담원", "상담사", "전화번호", "운영 시간", "연락"],
    "배송 기간 확인": ["언제 도착", "배송 기간", "며칠", "도착 예정", "얼마나 걸", "언제 와"],
    "배송 방법 확인": ["배송 방법", "택배사", "빠른 배송", "배송 옵션", "해외 배송", "새벽 배송"],
    "배송지 변경": ["배송지 변경", "주소 변경", "주소를 바꾸", "주소 수정", "배송지를 바꾸"],
    "배송지 설정": ["배송지 설정", "주소 등록", "기본 배송지", "주소 추가", "배송지 추가"],
    "주문 배송 조회": ["배송 조회", "송장", "어디쯤", "배송 상태", "운송장", "배송 현황"],
    "주문 변경": ["주문 변경", "옵션 변경", "수량 변경", "주문 수정", "주문을 바꾸"],
    "주문 취소": ["주문 취소", "취소하고", "취소 신청", "취소할", "취소가 가능"],
    "주문하기": ["주문하", "구매하", "주문 방법", "사고 싶", "주문을 하"],
    "취소 수수료 확인": ["취소 수수료", "위약금", "취소 비용", "수수료"],
    "환불 정책 확인": ["환불 정책", "환불 규정", "환불 기준", "환불 가능", "환불이 되나"],
    "환불 진행 상황 조회": ["환불 언제", "환불 진행", "환불 상태", "환불이 아직", "환불 처리"],
    "환불받기": ["환불 받", "환불 신청", "환불해", "돈 돌려", "환불을 원"],
}


def classify(question: str) -> tuple[str, float]:
    best_intent, best_hits = "고객 서비스 문의", 0
    for intent, keywords in INTENT_KEYWORDS.items():
        hits = sum(1 for k in keywords if k in question)
        if hits > best_hits:
            best_intent, best_hits = intent, hits
    confidence = 0.4 if best_hits == 0 else min(0.95, 0.65 + 0.15 * best_hits)
    return best_intent, confidence


def _first_sentences(text: str, max_chars: int = 200) -> str:
    lines = [ln for ln in text.splitlines()
             if ln.strip() and not ln.lstrip().startswith(("#", "※"))]   # 제목·주석 줄은 빼고 본문만
    text = re.sub(r"\s+", " ", " ".join(lines)).strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    end = max(cut.rfind("다."), cut.rfind("요."))
    return cut[:end + 2] if end > 0 else cut + "…"


class MockAIClient:
    def answer(self, request: AIRequest) -> AIResult:
        intent, conf = classify(request.question)
        with SessionLocal() as db:
            hits = keyword_retriever.search(db, request.question, top_k=3)
        if hits:
            answer = (f"문의하신 내용은 '{hits[0].title}' 기준으로 안내드립니다. "
                      f"{_first_sentences(hits[0].text)} 추가로 궁금하신 점이 있으면 다시 문의해 주세요.")
        else:
            answer = "문의 주셔서 감사합니다. 확인 후 안내드리겠습니다."
        return AIResult(
            category=INTENT_TO_CATEGORY[intent],
            intent=intent,
            category_confidence=conf,
            intent_confidence=conf,
            answer=answer,
            sources=[AISource(document_key=h.document_key, chunk_index=h.chunk_index,
                              title=h.title, text=h.text, score=h.score) for h in hits],
            score_metric="keyword_overlap",
            model="mock",
            prompt_version="mock-v1",
        )
