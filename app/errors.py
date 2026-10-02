"""에러 응답 형식 통일.

모든 에러는 아래 형식으로 내려갑니다 (프론트와 합의할 항목 — docs/API.md 참고)
{"error": {"code": "INQUIRY_NOT_FOUND", "message": "사람이 읽을 메시지", "details": [...] (선택)}}
"""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message


def _body(code: str, message: str, details=None) -> dict:
    err = {"code": code, "message": message}
    if details is not None:
        err["details"] = details
    return {"error": err}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content=_body(exc.code, exc.message))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        details = []
        for e in exc.errors():
            field = ".".join(str(p) for p in e.get("loc", []) if p not in ("body", "query", "path"))
            msg = str(e.get("msg", ""))
            msg = msg.removeprefix("Value error, ")   # 직접 쓴 한국어 메시지 앞에 붙는 문구 제거
            details.append({"field": field, "message": msg})
        return JSONResponse(status_code=422, content=_body("VALIDATION_ERROR", "입력값을 확인해 주세요.", details))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        return JSONResponse(status_code=exc.status_code, headers=getattr(exc, "headers", None),
                            content=_body(f"HTTP_{exc.status_code}", str(exc.detail)))
