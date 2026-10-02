"""정책 문서 파일 → 글자. 확장자별로 읽는 함수를 등록합니다.

지금 지원: .pdf (pypdf), .txt, .md
글자를 뽑는 이유: 관리자 화면 검색·mock AI 검색, AI 서버가 돌려준 근거를 백엔드 문서와 연결하기 위해서입니다.
(실제 AI 검색은 RAG 팀이 같은 PDF를 따로 적재해서 합니다.)

이미지로만 된 PDF(스캔본)는 글자가 뽑히지 않습니다. 그래도 파일은 저장되고 화면 미리보기는 됩니다.
"""
import io
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_PARSERS: dict[str, Callable[[bytes], str]] = {}


def register(ext: str):
    def deco(fn: Callable[[bytes], str]):
        _PARSERS[ext.lower()] = fn
        return fn
    return deco


@register(".txt")
@register(".md")
def _read_text(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp949"):   # 한글 윈도우에서 만든 메모장 파일(cp949)도 읽기
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("인코딩을 알 수 없는 파일입니다.")


@register(".pdf")
def _read_pdf(raw: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(raw))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    return "\n\n".join(p for p in pages if p)


def supported_extensions() -> list[str]:
    return sorted(_PARSERS)


@dataclass
class ParsedDocument:
    title: str | None   # 문서 안에서 찾은 제목 (md의 '# 제목'). 없으면 None
    text: str


def parse_bytes(raw: bytes, filename: str) -> ParsedDocument:
    ext = Path(filename).suffix.lower()
    fn = _PARSERS.get(ext)
    if fn is None:
        raise NotImplementedError(f"{ext} 파일은 읽을 수 없습니다. app/plugins/document_parser.py에 읽는 함수를 추가하세요.")
    text = fn(raw).strip()
    m = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE) if ext == ".md" else None
    return ParsedDocument(title=m.group(1).strip()[:200] if m else None, text=text)
