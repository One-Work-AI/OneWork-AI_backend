"""cs_backend 테이블 점검 / 다시 만들기 (백엔드 폴더 맨 위에서 실행).

  python fix_backend_schema.py          → 점검만 (아무것도 바꾸지 않음)
  python fix_backend_schema.py --reset  → cs_backend 테이블을 지우고 지금 코드 기준으로 다시 만듦

- 지우는 건 백엔드 전용 cs_backend 스키마의 테이블뿐입니다. 팀 DB(cs_chatbot) 테이블은 건드리지 않습니다.
- cs_backend에 있던 정보(문의번호·종료 사유 등)는 사라집니다. 팀 DB의 채팅·질문 행은 남지만,
  문의번호가 없어서 화면 목록에는 나오지 않습니다 (새로 만든 채팅부터 보임).
"""
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

import app.models  # noqa: F401  (모델을 불러와야 Base.metadata에 테이블이 등록됨)
from app.db import BACKEND_SCHEMA, Base, engine

EXPECTED = {t.name: {c.name for c in t.columns}
            for t in Base.metadata.tables.values() if t.schema == BACKEND_SCHEMA}


def check() -> bool:
    insp = inspect(engine)
    print(f"DB: {engine.url.render_as_string(hide_password=True)}")
    existing = set(insp.get_table_names(schema=BACKEND_SCHEMA)) if BACKEND_SCHEMA in insp.get_schema_names() else set()
    ok = True
    with engine.connect() as conn:
        for name, cols in sorted(EXPECTED.items()):
            if name not in existing:
                print(f"  [없음] {BACKEND_SCHEMA}.{name}")
                ok = False
                continue
            have = {c["name"] for c in insp.get_columns(name, schema=BACKEND_SCHEMA)}
            rows = conn.execute(text(f'SELECT count(*) FROM {BACKEND_SCHEMA}."{name}"')).scalar()
            missing = sorted(cols - have)
            if missing:
                ok = False
                print(f"  [구조 다름] {BACKEND_SCHEMA}.{name} — 없는 칸: {', '.join(missing)} (행 {rows}개)")
            else:
                print(f"  [정상] {BACKEND_SCHEMA}.{name} (행 {rows}개)")
    others = existing - set(EXPECTED) - {"alembic_version"}
    if others:
        print(f"  (코드에 없는 테이블: {', '.join(sorted(others))})")
    return ok


def reset() -> None:
    with engine.begin() as conn:
        for name in EXPECTED:
            conn.execute(text(f'DROP TABLE IF EXISTS {BACKEND_SCHEMA}."{name}" CASCADE'))
    print("cs_backend 테이블을 지웠어요. 다시 만드는 중…")
    cfg = Config("alembic.ini")
    command.stamp(cfg, "base")
    command.upgrade(cfg, "head")


if __name__ == "__main__":
    healthy = check()
    if "--reset" in sys.argv:
        reset()
        print("\n다시 만든 뒤 점검 결과:")
        healthy = check()
    print("\n결과:", "정상이에요. 서버를 재시작하세요." if healthy
          else "구조가 맞지 않아요. 팀원과 확인 후 'python fix_backend_schema.py --reset' 을 실행하세요.")