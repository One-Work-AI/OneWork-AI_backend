"""공통 도구 — 시간(한국 시간), 전화번호 표시."""
from datetime import UTC, date, datetime, time, timedelta, timezone

KST = timezone(timedelta(hours=9), "KST")


def as_utc(dt: datetime | None) -> datetime | None:
    """SQLite는 시간대 정보를 버리므로, 시간대가 없는 값은 UTC로 간주."""
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def kst_today() -> date:
    return datetime.now(KST).date()


def kst_day_range_utc(day: date) -> tuple[datetime, datetime]:
    """한국 날짜 하루 → UTC 시각 범위 [start, end)."""
    start = datetime.combine(day, time.min, KST).astimezone(UTC)
    return start, start + timedelta(days=1)


def to_kst(dt: datetime | None) -> datetime | None:
    return as_utc(dt).astimezone(KST) if dt else None


def format_phone(digits: str | None) -> str | None:
    if not digits:
        return None
    if digits.startswith("02") and len(digits) in (9, 10):
        return f"{digits[:2]}-{digits[2:-4]}-{digits[-4:]}"
    if len(digits) in (10, 11):
        return f"{digits[:3]}-{digits[3:-4]}-{digits[-4:]}"
    return digits
