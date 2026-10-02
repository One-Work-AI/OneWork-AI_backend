"""정책 문서 조각 나누기 — 빈 줄로 문단을 나누고, max_chars를 넘지 않게 이어 붙입니다.

RAG 팀의 조각 나누기 방식이 정해지면 이 함수를 그 방식으로 바꾸세요.
조각 순서(0부터)가 AI 서버가 돌려주는 chunk_index와 같아야 근거가 정확히 연결됩니다.
"""
import re


def split_into_chunks(text: str, max_chars: int = 500) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, buf = [], ""
    for p in paragraphs:
        while len(p) > max_chars:                 # 문단 하나가 너무 길면 잘라서 넣기
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(p[:max_chars])
            p = p[max_chars:]
        if buf and len(buf) + 2 + len(p) > max_chars:
            chunks.append(buf)
            buf = p
        else:
            buf = f"{buf}\n\n{p}" if buf else p
    if buf:
        chunks.append(buf)
    return chunks
