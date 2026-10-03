"""DB 연결 — 팀 PostgreSQL.

- cs_chatbot 스키마: 팀 DB 테이블 (DB 담당자의 schema.sql로 생성). 백엔드는 구조를 바꾸지 않고 읽고 씁니다.
- cs_backend 스키마: 팀 DB에 칸이 없는 백엔드 전용 정보 (Alembic(migrations/)으로 생성)
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings

TEAM_SCHEMA = "cs_chatbot"
BACKEND_SCHEMA = "cs_backend"

engine = create_engine(
    get_settings().sqlalchemy_url(),
    pool_pre_ping=True,                       # 원격 DB(Tailscale)가 잠깐 끊겨도 다음 요청에서 다시 연결
    connect_args={"connect_timeout": 10},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
