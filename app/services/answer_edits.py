"""관리자 답변 수정 기록 (DB 구조를 바꿀 수 없어서 백엔드 서버의 파일에 저장).

팀 DB 규칙상 고객에게 나간 답변(ai_response, status=SENT)은 수정할 수 없습니다 (트리거: Sent response is immutable).
그래서 수정본은 storage/answer_edits.json 에 따로 저장하고, 채팅 내역·답변 칸을 보여줄 때 덮어씁니다.
- 팀 DB의 원래 답변은 그대로 남습니다 (학습 데이터 내보내기·대시보드는 원래 답변 기준)
- 서버를 재시작해도 파일에 남아 있습니다
- 나중에 DB에 수정 테이블이 생기면 이 파일의 get/save만 DB 코드로 바꾸면 됩니다
"""
import json
import threading
from pathlib import Path

from app.config import get_settings
from app.models import utcnow

_lock = threading.Lock()


def _path() -> Path:
    return Path(get_settings().policy_file_dir).parent / "answer_edits.json"   # 기본: storage/answer_edits.json


def _load() -> dict:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def get(response_id: int) -> dict | None:
    """{"text", "edited_at"(ISO, UTC), "edited_by"} 또는 None"""
    return _load().get(str(response_id))


def save(response_id: int, text: str, admin_name: str) -> dict:
    with _lock:
        data = _load()
        data[str(response_id)] = {"text": text, "edited_at": utcnow().isoformat(), "edited_by": admin_name}
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
        return data[str(response_id)]


def final_text(r) -> str:
    """고객에게 보여줄 최종 답변 (수정본이 있으면 수정본)."""
    e = get(r.id)
    return e["text"] if e else r.final_response_text