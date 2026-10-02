"""mock용 간단한 정책 검색 — 질문의 핵심 단어가 정책 조각에 몇 % 들어 있는지로 점수를 매깁니다 (0~1).

단어 앞 2글자를 어간처럼 써서 '취소하고'·'취소는'을 같은 단어로 봅니다 (형태소 분석기 없이 쓰는 대략적인 방법).
실제 서비스에서는 AI 서버(RAG 팀)가 임베딩 검색을 하므로, 이 파일은 AI_MODE=mock 일 때만 쓰입니다.
mock 점수는 실제 임베딩 점수와 범위가 다르니, 기준값(REVIEW_MIN_RETRIEVAL_SCORE)은 실제 AI 서버 기준으로 다시 정하세요.
"""
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PolicyChunk, PolicyDocument

# 질문에 흔히 나오지만 내용과 상관없는 단어 (앞 2글자 기준)
STOP_STEMS = {"언제", "어디", "어떻", "무엇", "뭐가", "뭔가", "있나", "있어", "있을", "하나", "싶어", "싶은", "알려", "주세",
              "부탁", "궁금", "문의", "해주", "하고", "되나", "인가", "제가", "저는", "이거", "혹시", "지금", "어떤",
              "하는", "하려", "할수", "가능", "얼마", "되는", "해야", "하면", "그리", "그런", "좀"}


@dataclass
class Hit:
    document_key: str
    chunk_index: int
    title: str
    text: str
    score: float


def _stems(text: str) -> set[str]:
    words = re.findall(r"[0-9A-Za-z가-힣]+", text.lower())
    return {w[:2] for w in words if len(w) >= 2 and w[:2] not in STOP_STEMS}


def search(db: Session, query: str, top_k: int = 3) -> list[Hit]:
    stems = _stems(query)
    if not stems:
        return []
    rows = db.execute(
        select(PolicyChunk, PolicyDocument)
        .join(PolicyDocument, PolicyChunk.policy_document_id == PolicyDocument.id)
        .where(PolicyDocument.is_deleted.is_(False))
    ).all()
    hits = []
    for chunk, doc in rows:
        haystack = (doc.title + " " + chunk.chunk_text).lower()
        score = sum(1 for s in stems if s in haystack) / len(stems)
        hits.append(Hit(doc.doc_key, chunk.chunk_order, doc.title, chunk.chunk_text, round(score, 4)))
    hits.sort(key=lambda h: -h.score)
    return hits[:top_k]
