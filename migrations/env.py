"""Alembic 설정 — 백엔드 전용 cs_backend 스키마만 관리합니다.

- DB 주소는 alembic.ini가 아니라 .env(DATABASE_URL 또는 PG* 값)를 씁니다.
- 팀 DB 테이블(cs_chatbot 스키마)은 DB 담당자의 schema.sql이 만들고 관리하므로 여기서 건드리지 않습니다.
- 버전 기록 테이블(alembic_version)도 cs_backend 스키마에 둡니다.
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool, text

from app import models  # noqa: F401  (테이블 정의를 불러와야 비교가 동작)
from app.config import get_settings
from app.db import BACKEND_SCHEMA, TEAM_SCHEMA, Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    # 백엔드 전용 스키마의 테이블만 비교·생성
    if type_ == "table":
        return obj.schema == BACKEND_SCHEMA
    return True


def _configure(**kw) -> None:
    context.configure(target_metadata=target_metadata, include_schemas=True, include_object=include_object,
                      version_table_schema=BACKEND_SCHEMA, **kw)


def run_migrations_offline() -> None:
    _configure(url=get_settings().sqlalchemy_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(get_settings().sqlalchemy_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        if connection.execute(text("SELECT 1 FROM pg_namespace WHERE nspname = :s"), {"s": TEAM_SCHEMA}).first() is None:
            raise SystemExit(f"팀 DB 스키마({TEAM_SCHEMA})가 없습니다. DB 담당자의 schema.sql(init_db.py)로 먼저 만들어 주세요.")
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {BACKEND_SCHEMA}"))
        connection.commit()
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
