"""설정 — .env 파일 또는 환경변수에서 읽습니다. 항목 설명은 .env.example 참고."""
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "CS Chatbot Backend"

    # DB
    database_url: str = "sqlite:///./cs_chatbot.db"

    # 프론트엔드 주소 (CORS — 브라우저에서 직접 호출할 때만 해당, Streamlit 서버에서 호출하면 상관없음)
    cors_origins: list[str] = ["http://localhost:8501", "http://localhost:3000", "http://localhost:5173"]

    # 체험 입장 토큰
    jwt_secret: str = "change-me"
    jwt_expire_minutes: int = 480

    # 시연용 계정 (scripts.seed_base 가 이 값으로 만듦)
    demo_customer_name: str = "김민지"
    demo_customer_phone: str = "01012345678"
    demo_customer_email: str = "kimminji@email.com"
    demo_admin_username: str = "admin"
    demo_admin_name: str = "관리자"

    # AI 서버
    ai_mode: Literal["mock", "http"] = "mock"
    ai_server_url: str = "http://localhost:9000"
    ai_timeout_seconds: float = 120
    ai_history_messages: int = 6          # AI 서버에 함께 보낼 이전 대화 개수 (0이면 안 보냄)

    # 자동답변 판단 — 신뢰도 기준 (이 값 이상이면 자동답변, 미만이면 관리자 검토)
    review_min_confidence: float = 0.8
    # 아래 규칙은 코드에 남겨두되 기본은 꺼짐 (.env에서 true로 바꾸면 켜짐)
    review_low_retrieval_enabled: bool = False
    review_min_retrieval_score: float = 0.5
    review_always_intents_enabled: bool = False
    review_always_intents: list[str] = ["결제 문제", "환불받기", "주문 취소"]
    review_short_answer_enabled: bool = False
    review_min_answer_chars: int = 10

    # 검토로 넘어갈 때 챗봇이 고객에게 남기는 안내 (이후 입력 잠금 → 관리자 답변 후 상담 종료)
    review_notice_message: str = "확인이 필요한 내용이라 담당자에게 전달했어요. 검토 후 [문의 내역]에서 답변을 확인하실 수 있어요."

    # 채팅 자동 종료 (챗봇 답변 후 고객이 이 시간 동안 말이 없으면 종료. 검토 대기 중에는 멈춤)
    chat_idle_timeout_minutes: float = 5
    chat_timeout_sweep_seconds: float = 30   # 자동 종료를 확인하는 주기 (0이면 백그라운드 확인 끔)

    # 주문 정보를 가져오는 곳: local = 이 백엔드 DB의 가짜 주문 (쇼핑몰 DB가 준비되면 app/plugins/order_source.py에 추가)
    order_source: Literal["local"] = "local"

    # 정책 문서 (PDF)
    chunk_max_chars: int = 500
    policy_file_dir: str = "storage/policies"
    policy_max_mb: int = 200

    # 문의번호 접두어
    inquiry_no_prefix: str = "CS"


@lru_cache
def get_settings() -> Settings:
    return Settings()
