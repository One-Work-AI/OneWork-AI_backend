"""서버 시작점 — 실행: uvicorn app.main:app --reload  →  API 문서: http://localhost:8000/docs"""
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, customer, demo
from app.config import get_settings
from app.errors import register_error_handlers
from app.services.conversations import start_timeout_sweeper
from app.services.pipeline import resume_unfinished

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    s = get_settings()
    if s.jwt_secret == "change-me":
        log.warning("JWT_SECRET이 기본값입니다. .env에서 무작위 값으로 바꾸세요.")
    log.info("AI_MODE=%s, 자동답변 신뢰도 기준=%s, 자동 종료=%s분", s.ai_mode, s.review_min_confidence,
             s.chat_idle_timeout_minutes)
    resume_unfinished()                       # 꺼지기 전에 처리 중이던 질문 다시 처리
    stop = threading.Event()
    start_timeout_sweeper(stop)               # 응답 없는 채팅 자동 종료
    yield
    stop.set()


app = FastAPI(title=get_settings().app_name, version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
register_error_handlers(app)
app.include_router(demo.router)
app.include_router(customer.router)
app.include_router(admin.router)


@app.get("/health", tags=["공통"], summary="서버 상태 확인")
def health():
    return {"status": "ok", "ai_mode": get_settings().ai_mode}
