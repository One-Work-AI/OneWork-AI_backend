"""팀 DB가 닫혀 있을 때: 내 PC에 임시 PostgreSQL을 띄워서 백엔드를 실행합니다.

    python run_local.py          → 로컬 DB로 서버 실행 (http://127.0.0.1:8000/docs)
    python run_local.py --reset  → 로컬 DB를 지우고 처음부터 다시 만든 뒤 실행

- 팀 DB 구조(tests/team_schema.sql) 그대로 만들고, 백엔드 테이블(alembic) + 체험 계정 + 샘플 정책까지 넣습니다.
  (테스트 코드 tests/conftest.py 와 같은 준비 과정)
- 데이터는 백엔드 폴더의 .localdb/ 에 저장돼서 껐다 켜도 남습니다. 팀 DB에는 아무 영향이 없습니다.
- AI는 mock(가짜 AI)으로 실행합니다. 실제 AI 서버를 쓰려면 --ai 를 붙이세요 (.env의 AI 설정 사용).
- 필요한 패키지: pgserver (테스트용으로 이미 설치돼 있을 수 있음. 없으면 pip install pgserver)
- 프론트(Streamlit)는 그대로 실행하면 됩니다. 주소가 같아서(localhost:8000) 바꿀 것이 없습니다.
"""
import os
import shutil
import sys
from pathlib import Path

import pgserver
import psycopg

ROOT = Path(__file__).resolve().parent
DATA = ROOT / ".localdb"
DB_NAME = "cs_chatbot"

if "--reset" in sys.argv and DATA.exists():
    shutil.rmtree(DATA)
    print("로컬 DB를 지웠어요. 처음부터 다시 만들어요.")

DATA.mkdir(exist_ok=True)
pg = pgserver.get_server(DATA / "pgdata", cleanup_mode="stop")   # 서버를 꺼도 데이터는 남김

with psycopg.connect(pg.get_uri(), autocommit=True) as c:
    if not c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,)).fetchone():
        c.execute(f"CREATE DATABASE {DB_NAME}")
with psycopg.connect(pg.get_uri(DB_NAME), autocommit=True) as c:
    if not c.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name = 'cs_chatbot'").fetchone():
        c.execute((ROOT / "tests" / "team_schema.sql").read_text(encoding="utf-8"))
        print("팀 DB 구조(team_schema.sql)를 만들었어요.")

# 아래 설정이 .env 보다 우선합니다 (이 실행에서만)
os.environ["DATABASE_URL"] = pg.get_uri(DB_NAME).replace("postgresql://", "postgresql+psycopg://", 1)
os.environ["POLICY_FILE_DIR"] = (DATA / "policies").as_posix()
if "--ai" not in sys.argv:
    os.environ["AI_MODE"] = "mock"

sys.path.insert(0, str(ROOT))
from app.db import SessionLocal

seeded = DATA / ".seeded"
if (ROOT / "alembic.ini").exists():
    from alembic import command
    from alembic.config import Config
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")      # 백엔드 전용 cs_backend 테이블
if not seeded.exists():
    from scripts import seed_base
    seed_base.main()                                                  # 체험 고객·관리자·주문 등
    sample = ROOT / "policies_sample"
    if sample.exists():
        from app.services.policies import ingest_directory
        with SessionLocal() as db:
            ingest_directory(db, sample)                              # 샘플 정책 PDF
    seeded.write_text("ok", encoding="utf-8")
    print("체험 계정과 샘플 정책을 넣었어요.")

print("\n로컬 DB로 백엔드를 실행해요 → http://127.0.0.1:8000/docs  (끄려면 Ctrl + C)\n")
import uvicorn

try:
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
finally:
    pg.cleanup()       # PostgreSQL만 멈춤 (데이터는 .localdb/ 에 남음)
