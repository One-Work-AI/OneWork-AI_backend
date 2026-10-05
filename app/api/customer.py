"""고객 API — 고객 문의(채팅) 화면, 문의 내역, 문의 상세.

고객 CRUD
- C: 새 채팅 시작(주문 상품 선택), 메시지 보내기
- R: 주문 상품 목록, 진행 중인 채팅, 문의 내역, 문의 상세(채팅 전체)
- U: 채팅 종료 (채팅 종료하기 / 새 채팅하기)
- D: 없음 (보류). 보낸 메시지는 수정·취소할 수 없습니다.

wait=true 로 보내면 AI 답변이 나올 때까지 기다렸다가 결과를 줍니다 (Streamlit에서 쓰기 편함).
wait=false(기본)면 바로 응답하고, 프론트가 상세를 2~3초마다 다시 조회합니다.
"""
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_customer
from app.db import get_db
from app.enums import CloseReason
from app.models import Customer
from app.schemas import ConversationCreate, ConversationDetail, ConversationListOut, MessageCreate, OrderOut
from app.services import conversations as svc
from app.services.pipeline import process_question

router = APIRouter(prefix="/api", tags=["고객"])


def _run(question_id: int, wait: bool, background: BackgroundTasks) -> None:
    if wait:
        process_question(question_id)
    else:
        background.add_task(process_question, question_id)


@router.get("/orders", response_model=list[OrderOut], summary="문의할 주문 상품 목록 (최신 주문순)")
def list_orders(db: Session = Depends(get_db), customer: Customer = Depends(get_current_customer)):
    return svc.list_orders(db, customer)


@router.get("/conversations", response_model=ConversationListOut, summary="문의 내역 (최신순)")
def list_conversations(
    status: Literal["CHATTING", "ANSWERED"] | None = Query(
        None, description="탭: CHATTING(상담 중, 검토 대기 포함) / ANSWERED(답변완료). 비우면 전체"),
    q: str | None = Query(None, description="문의 내용, 상품명 검색"),
    category: str | None = Query(None, description="문의 유형: 배송 / 결제 / 교환/환불 / 주문 / 기타 (비우면 전체)"),
    page: int = Query(1, ge=1), size: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db), customer: Customer = Depends(get_current_customer),
):
    return svc.list_conversations(db, customer, status, q, page, size, category)


@router.get("/conversations/current", response_model=ConversationDetail | None,
            summary="진행 중인 채팅 (없으면 null) — 고객 문의 화면을 열 때, '이어서 채팅하기'")
def current_conversation(db: Session = Depends(get_db), customer: Customer = Depends(get_current_customer)):
    conv = svc.get_current(db, customer)
    return svc.to_detail(conv) if conv else None


@router.post("/conversations", response_model=ConversationDetail, status_code=201,
             summary="새 채팅 시작 (첫 메시지 + 고른 주문 상품). 진행 중이던 채팅은 저장된 채로 종료")
def start_conversation(data: ConversationCreate, background: BackgroundTasks,
                       wait: bool = Query(False, description="true면 AI 답변까지 기다렸다가 응답"),
                       db: Session = Depends(get_db), customer: Customer = Depends(get_current_customer)):
    conv, question = svc.start_conversation(db, customer, data.content, data.order_no)
    _run(question.id, wait, background)
    db.expire_all()
    return svc.to_detail(svc.get_owned(db, customer, conv.ext.inquiry_no))


@router.get("/conversations/{inquiry_no}", response_model=ConversationDetail, summary="문의 상세 (채팅 전체)")
def get_conversation(inquiry_no: str, db: Session = Depends(get_db),
                     customer: Customer = Depends(get_current_customer)):
    return svc.to_detail(svc.get_owned(db, customer, inquiry_no))


@router.post("/conversations/{inquiry_no}/messages", response_model=ConversationDetail, status_code=201,
             summary="메시지 보내기 (이어서 질문)")
def send_message(inquiry_no: str, data: MessageCreate, background: BackgroundTasks,
                 wait: bool = Query(False, description="true면 AI 답변까지 기다렸다가 응답"),
                 db: Session = Depends(get_db), customer: Customer = Depends(get_current_customer)):
    question = svc.add_message(db, customer, inquiry_no, data.content)
    _run(question.id, wait, background)
    db.expire_all()
    return svc.to_detail(svc.get_owned(db, customer, inquiry_no))


@router.post("/conversations/{inquiry_no}/close", response_model=ConversationDetail,
             summary="채팅 종료 (채팅 종료하기 / 새 채팅하기). 채팅 내용은 저장됨")
def close_conversation(inquiry_no: str,
                       reason: Literal["USER", "NEW_CHAT"] = Query("USER", description="USER=채팅 종료하기, NEW_CHAT=새 채팅하기"),
                       db: Session = Depends(get_db), customer: Customer = Depends(get_current_customer)):
    return svc.to_detail(svc.close_conversation(db, customer, inquiry_no, CloseReason(reason)))