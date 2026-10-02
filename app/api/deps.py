from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import AppError
from app.models import AdminUser, Customer
from app.security import decode_access_token

_bearer = HTTPBearer(auto_error=False)


def get_current_customer(cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
                         db: Session = Depends(get_db)) -> Customer:
    cid = decode_access_token(cred.credentials, "customer") if cred else None
    customer = db.get(Customer, cid) if cid else None
    if customer is None:
        raise AppError(401, "UNAUTHORIZED", "고객으로 체험하기를 먼저 눌러주세요.")
    return customer


def get_current_admin(cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
                      db: Session = Depends(get_db)) -> AdminUser:
    aid = decode_access_token(cred.credentials, "admin") if cred else None
    admin = db.get(AdminUser, aid) if aid else None
    if admin is None or not admin.is_active:
        raise AppError(401, "UNAUTHORIZED", "관리자로 체험하기를 먼저 눌러주세요.")
    return admin
