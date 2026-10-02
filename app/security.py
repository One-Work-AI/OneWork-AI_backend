"""체험 입장 토큰 (JWT). 토큰에 역할(customer / admin)과 ID가 들어갑니다.

지금은 '고객으로 체험' / '관리자로 체험' 버튼으로 비밀번호 없이 토큰을 받습니다.
나중에 진짜 로그인을 붙일 때는 토큰을 발급하는 곳(app/api/demo.py)만 바꾸면 됩니다.
"""
from datetime import UTC, datetime, timedelta

import jwt

from app.config import get_settings

ALGORITHM = "HS256"


def create_access_token(role: str, subject_id: int) -> tuple[str, int]:
    s = get_settings()
    expires_in = s.jwt_expire_minutes * 60
    payload = {"sub": str(subject_id), "role": role,
               "exp": datetime.now(UTC) + timedelta(seconds=expires_in)}
    return jwt.encode(payload, s.jwt_secret, algorithm=ALGORITHM), expires_in


def decode_access_token(token: str, role: str) -> int | None:
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=[ALGORITHM])
        if payload.get("role") != role:
            return None
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
