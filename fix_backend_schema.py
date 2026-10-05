"""cs_backend 테이블 점검 / 새 구조로 맞추기 (백엔드 폴더 맨 위에서 실행).

  python fix_backend_schema.py            → 점검만 (아무것도 바꾸지 않음)
  python fix_backend_schema.py --upgrade  → 예전 구조를 새 구조로 바꿈 (기존 채팅은 그대로 남김)
  python fix_backend_schema.py --reset    → cs_backend 테이블을 지우고 새로 만듦 (기존 채팅이 화면에서 사라짐)

새 구조 (refactor: 백엔드 전용 테이블을 1개로 줄이고 팀 DB 값으로 계산)
- cs_backend.conversation_ext 에는 conversation_id, close_reason, is_demo 만 둠
- 예전 칸(inquiry_no, order_id, order_no, product_name, category, intent, last_answer_at)과
  예전 테이블(analysis_ext, policy_file)은 팀 DB 값으로 계산하게 바뀌어서 필요 없음
- 마이그레이션 파일(a1c4e7f20b31)을 같은 번호로 고쳐서, 예전 구조가 남은 DB는 alembic이 '최신'으로 보고 그대로 둠
  → 예전 칸 inquiry_no 가 NOT NULL 이라 새 채팅을 만들 때 실패함. 그래서 --upgrade 로 맞춰야 함

팀 DB(cs_chatbot 스키마)의 테이블은 건드리지 않습니다.
"""
import sys

from sqlalchemy import inspect, text

import app.models  # noqa: F401  (모델을 불러와야 Base.metadata에 테이블이 등록됨)
from app.db import BACKEND_SCHEMA, Base, engine

EXPECTED = {t.name: {c.name for c in t.columns}
            for t in Base.metadata.tables.values() if t.schema == BACKEND_SCHEMA}
OLD_TABLES = ["analysis_ext", "policy_file"]   # 예전 구조에만 있던 테이블


def check() -> bool:
    insp = inspect(engine)
    print(f"DB: {engine.url.render_as_string(hide_password=True)}")
    schemas = insp.get_schema_names()
    existing = set(insp.get_table_names(schema=BACKEND_SCHEMA)) if BACKEND_SCHEMA in schemas else set()
    ok = True
    with engine.connect() as conn:
        for name, cols in sorted(EXPECTED.items()):
            if name not in existing:
                print(f"  [없음] {BACKEND_SCHEMA}.{name}  → python -m alembic upgrade head")
                ok = False
                continue
            have = {c["name"] for c in insp.get_columns(name, schema=BACKEND_SCHEMA)}
            rows = conn.execute(text(f'SELECT count(*) FROM {BACKEND_SCHEMA}."{name}"')).scalar()
            missing, extra = sorted(cols - have), sorted(have - cols)
            if missing or extra:
                ok = False
                print(f"  [구조 다름] {BACKEND_SCHEMA}.{name} (행 {rows}개)"
                      + (f" — 없는 칸: {', '.join(missing)}" if missing else "")
                      + (f" — 예전 칸: {', '.join(extra)}" if extra else ""))
            else:
                print(f"  [정상] {BACKEND_SCHEMA}.{name} (행 {rows}개)")
    old = [t for t in OLD_TABLES if t in existing]
    if old:
        ok = False
        print(f"  [예전 테이블] {', '.join(old)} (새 구조에서는 쓰지 않음)")
    return ok


def upgrade() -> None:
    """예전 칸·테이블만 지움. conversation_ext 행(채팅 목록에 보이는 기준)은 그대로 남김."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for name, cols in EXPECTED.items():
            if name not in insp.get_table_names(schema=BACKEND_SCHEMA):
                continue
            for col in sorted({c["name"] for c in insp.get_columns(name, schema=BACKEND_SCHEMA)} - cols):
                conn.execute(text(f'ALTER TABLE {BACKEND_SCHEMA}."{name}" DROP COLUMN "{col}" CASCADE'))
                print(f"  {name}.{col} 칸을 지웠어요.")
        for t in OLD_TABLES:
            conn.execute(text(f'DROP TABLE IF EXISTS {BACKEND_SCHEMA}."{t}" CASCADE'))
    print("새 구조로 바꿨어요. 기존 채팅은 그대로 남아 있어요.")


def reset() -> None:
    from alembic import command
    from alembic.config import Config
    with engine.begin() as conn:
        for name in [*EXPECTED, *OLD_TABLES]:
            conn.execute(text(f'DROP TABLE IF EXISTS {BACKEND_SCHEMA}."{name}" CASCADE'))
    print("cs_backend 테이블을 지웠어요. 다시 만드는 중…")
    cfg = Config("alembic.ini")
    command.stamp(cfg, "base")
    command.upgrade(cfg, "head")


if __name__ == "__main__":
    healthy = check()
    if "--upgrade" in sys.argv or "--reset" in sys.argv:
        upgrade() if "--upgrade" in sys.argv else reset()
        print("\n바꾼 뒤 점검 결과:")
        healthy = check()
    print("\n결과:", "정상이에요. 서버를 재시작하세요." if healthy
          else "구조가 맞지 않아요. 'python fix_backend_schema.py --upgrade' 를 실행하세요.")
