"""정책 문서 (PDF) — 목록·정보, 새 PDF 등록, PDF 파일 교체, 문서 삭제(숨김), 파일 보기·받기.

- 문서 1개 = PDF 파일 1개. 등록할 때 PDF에서 글자를 한 번 뽑아 둡니다 (mock 검색, 근거 연결용).
- doc_key = 처음 등록한 파일 이름에서 확장자를 뺀 값 (예: delivery_policy). AI 서버와 문서를 맞추는 기준이며,
  파일을 교체해도 바뀌지 않습니다 (팀 DB policy_document.document_key).
- 팀 DB 규칙상 정책 문서·조각은 고치지 않고 버전마다 새 행으로 남깁니다.
  파일을 교체하면 같은 document_key로 새 버전 행(+ 조각)을 만들고 이전 버전은 is_active=false가 됩니다.
  화면의 문서 id는 사용 중인 버전의 id라서 교체하면 바뀝니다 (이전 id로 불러도 사용 중인 버전을 돌려줌).
- 문서 삭제는 숨김(is_active=false)입니다 (지난 문의의 근거 기록을 지키기 위해).
- PDF 파일 정보(파일 이름, 저장 이름, 크기, 설명)는 백엔드 전용 policy_file 테이블에 둡니다.
  팀에서 파일 없이 넣은 문서도 목록에는 나오지만 'PDF 보기'는 할 수 없습니다.

※ 시연용 방식: 실제 AI 검색은 RAG 팀이 같은 PDF를 시연 전에 한 번 적재해서 합니다.
  시연 중에 새로 등록하거나 교체한 내용은 AI 서버가 알지 못합니다.

scripts.ingest_policies 는 폴더의 파일(PDF·TXT·MD)을 같은 방식으로 한 번에 등록합니다.
"""
import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.enums import CATEGORY_INTENTS
from app.errors import AppError
from app.models import PolicyChunk, PolicyDocument, PolicyFile
from app.plugins import chunker, document_parser
from app.schemas import PolicyDetail, PolicyListItem

log = logging.getLogger(__name__)
UPLOAD_CHUNK = 1024 * 1024
UI_EXTENSIONS = [".pdf"]   


def _storage_dir() -> Path:
    d = Path(get_settings().policy_file_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _extract(raw: bytes, filename: str) -> str:
    try:
        return document_parser.parse_bytes(raw, filename).text
    except NotImplementedError:
        raise
    except Exception:  
        log.warning("정책 파일에서 글자를 뽑지 못했습니다: %s", filename, exc_info=True)
        return ""


def _save_file(raw: bytes, filename: str) -> str:
    stored_name = f"{uuid.uuid4().hex}{Path(filename).suffix.lower()}"
    (_storage_dir() / stored_name).write_bytes(raw)
    return stored_name


def _version_no(version: str) -> int:
    return int(version) if version.isdigit() else 1


def _next_version(db: Session, doc_key: str) -> str:
    versions = db.scalars(select(PolicyDocument.version).where(PolicyDocument.document_key == doc_key)).all()
    return str(max([_version_no(v) for v in versions] + [len(versions)]) + 1) if versions else "1"


def _new_version(db: Session, doc_key: str, raw: bytes, filename: str, title: str, category: str | None,
                 description: str | None) -> PolicyDocument:
    """정책 문서 새 버전 행 + 조각 + 파일 정보 (이전 버전은 먼저 숨긴 뒤 호출)."""
    text = _extract(raw, filename)
    name = Path(filename).name[:255]
    doc = PolicyDocument(document_key=doc_key, title=title, category=category, content=text, source_file=name,
                         version=_next_version(db, doc_key), is_active=True)
    doc.file = PolicyFile(filename=name, stored_name=_save_file(raw, filename), size_bytes=len(raw),
                          content_hash=hashlib.sha256(raw).hexdigest(), description=description)
    doc.chunks = [PolicyChunk(chunk_order=i, chunk_text=t, category=category)
                  for i, t in enumerate(chunker.split_into_chunks(text, get_settings().chunk_max_chars))]
    db.add(doc)
    return doc


def _active_by_key(db: Session, doc_key: str) -> PolicyDocument | None:
    return db.scalar(select(PolicyDocument).where(PolicyDocument.document_key == doc_key,
                                                  PolicyDocument.is_active.is_(True)))


def register(db: Session, raw: bytes, filename: str, title: str | None = None, description: str | None = None,
             category: str | None = None) -> PolicyDocument:
    doc_key = Path(filename).stem
    if _active_by_key(db, doc_key) is not None:
        raise AppError(409, "DUPLICATE_POLICY", "같은 파일 이름의 문서가 이미 있습니다. 해당 문서에서 'PDF 파일 교체'를 사용하세요.")
    doc = _new_version(db, doc_key, raw, filename, title=(title or "").strip()[:200] or doc_key, category=category,
                       description=(description or "").strip() or None)
    db.commit()
    return doc


def replace_file(db: Session, doc: PolicyDocument, raw: bytes, filename: str) -> PolicyDocument:
    doc.is_active = False
    db.flush()  
    new = _new_version(db, doc.document_key, raw, filename, title=doc.title, category=doc.category,
                       description=doc.file.description if doc.file else None)
    db.commit()
    return new


# ───────────────────────── 화면 (관리자) ─────────────────────────

async def _read_upload(file: UploadFile) -> tuple[bytes, str]:
    s = get_settings()
    filename = Path(file.filename or "").name
    if not filename or Path(filename).suffix.lower() not in UI_EXTENSIONS:
        raise AppError(422, "UNSUPPORTED_FILE_TYPE", "PDF 파일만 올릴 수 있습니다.")
    max_bytes = s.policy_max_mb * 1024 * 1024
    parts, size = [], 0
    while chunk := await file.read(UPLOAD_CHUNK):
        size += len(chunk)
        if size > max_bytes:
            raise AppError(413, "FILE_TOO_LARGE", f"파일은 {s.policy_max_mb}MB까지 올릴 수 있습니다.")
        parts.append(chunk)
    if size == 0:
        raise AppError(422, "EMPTY_FILE", "빈 파일은 올릴 수 없습니다.")
    return b"".join(parts), filename


def _get(db: Session, policy_id: int) -> PolicyDocument:
    """문서 id → 사용 중인 버전 (교체 전 id로 불러도 같은 문서의 사용 중인 버전)."""
    doc = db.get(PolicyDocument, policy_id)
    active = _active_by_key(db, doc.document_key) if doc else None
    if active is None:
        raise AppError(404, "POLICY_NOT_FOUND", "정책 문서를 찾을 수 없습니다.")
    return active


def _first_created(db: Session, doc_keys: list[str]) -> dict:
    """문서별 등록일 = 첫 버전을 만든 시각."""
    return dict(db.execute(select(PolicyDocument.document_key, func.min(PolicyDocument.created_at))
                           .where(PolicyDocument.document_key.in_(doc_keys))
                           .group_by(PolicyDocument.document_key)).all())


def _list_item(doc: PolicyDocument, created_at) -> PolicyListItem:
    f = doc.file
    return PolicyListItem(id=doc.id, title=doc.title, filename=f.filename if f else (doc.source_file or doc.document_key),
                          size_bytes=f.size_bytes if f else 0, version=_version_no(doc.version),
                          created_at=created_at or doc.created_at, updated_at=doc.created_at)


def to_detail(db: Session, doc: PolicyDocument) -> PolicyDetail:
    item = _list_item(doc, _first_created(db, [doc.document_key]).get(doc.document_key))
    return PolicyDetail(**item.model_dump(), doc_key=doc.document_key,
                        description=doc.file.description if doc.file else None,
                        text_extracted=bool(doc.content.strip()))


def list_policies(db: Session, q: str | None) -> list[PolicyListItem]:
    cond = [PolicyDocument.is_active.is_(True)]
    if q and q.strip():
        cond.append(PolicyDocument.title.contains(q.strip()))
    docs = db.scalars(select(PolicyDocument).where(*cond).order_by(PolicyDocument.id.desc())).all() 
    first = _first_created(db, [d.document_key for d in docs])
    return [_list_item(d, first.get(d.document_key)) for d in docs]


def get_policy(db: Session, policy_id: int) -> PolicyDetail:
    return to_detail(db, _get(db, policy_id))


async def upload_new(db: Session, file: UploadFile, title: str | None, description: str | None) -> PolicyDetail:
    raw, filename = await _read_upload(file)
    return to_detail(db, register(db, raw, filename, title, description))


async def upload_replacement(db: Session, policy_id: int, file: UploadFile) -> PolicyDetail:
    doc = _get(db, policy_id)
    raw, filename = await _read_upload(file)
    return to_detail(db, replace_file(db, doc, raw, filename))


def delete_policy(db: Session, policy_id: int) -> None:
    doc = _get(db, policy_id)
    doc.is_active = False
    db.commit()


def get_file(db: Session, policy_id: int) -> tuple[PolicyDocument, Path]:
    doc = _get(db, policy_id)
    path = _storage_dir() / doc.file.stored_name if doc.file else None
    if path is None or not path.exists():
        raise AppError(404, "POLICY_FILE_MISSING", "서버에 파일이 없습니다.")
    return doc, path


# ───────────────────────── 폴더 등록 (스크립트) ─────────────────────────

@dataclass
class IngestReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def ingest_directory(db: Session, root: Path) -> IngestReport:
    report = IngestReport()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root)
        if path.name.startswith(".") or path.name.lower() == "readme.md":
            continue
        if path.suffix.lower() not in document_parser.supported_extensions():
            report.skipped.append(f"{rel.as_posix()} (읽을 수 없는 형식)")
            continue
        raw = path.read_bytes()
        existing = _active_by_key(db, path.stem)
        if existing is not None:
            if existing.file and existing.file.content_hash == hashlib.sha256(raw).hexdigest():
                report.unchanged.append(path.stem)
            else:
                replace_file(db, existing, raw, path.name)
                report.updated.append(path.stem)
            continue
        parsed_title = None
        if path.suffix.lower() == ".md":
            parsed_title = document_parser.parse_bytes(raw, path.name).title
        category = rel.parts[0] if len(rel.parts) > 1 and rel.parts[0] in CATEGORY_INTENTS else None
        register(db, raw, path.name, title=parsed_title, category=category)
        report.added.append(path.stem)
    return report