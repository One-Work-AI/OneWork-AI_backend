"""관리자 API — 문의 검토, 운영 대시보드, 정책 문서 관리.

관리자 CRUD (문의 검토 기준)
- C: 검토대기 질문에 최종 답변 작성·승인
- R: 검토대기 / 전체 / 답변완료 목록, 상세(채팅 내역, 고객·문의 정보, AI 초안, 이전 채팅 내역)
- U: 최종 답변 수정 (+ 선택: 문의 유형·의도, 학습 데이터, 메모)
- D: 없음 (상담 기록 보존)
정책 문서: 목록·정보, 새 PDF 등록, PDF 파일 교체, 문서 삭제(숨김), PDF 보기·받기
"""
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin
from app.db import get_db
from app.enums import (
    CATEGORY_INTENTS,
    DISPLAY_CATEGORIES,
    DISPLAY_CATEGORY,
    INQUIRY_STATUS_LABELS,
    QUESTION_STATUS_LABELS,
    REVIEW_REASON_LABELS,
)
from app.errors import AppError
from app.models import AdminUser
from app.schemas import (
    AnswerEditRequest,
    ApproveRequest,
    DashboardOut,
    MetaOut,
    PolicyDetail,
    PolicyListItem,
    ReviewDetail,
    ReviewListOut,
)
from app.services import dashboard, policies, reviews, training
from app.utils import kst_today

router = APIRouter(prefix="/api/admin", tags=["관리자"])


@router.get("/meta", response_model=MetaOut, summary="코드값 (문의 유형 5개, 상태 이름 등)")
def meta(_: AdminUser = Depends(get_current_admin)):
    return MetaOut(
        display_categories=DISPLAY_CATEGORIES,
        category_intents=CATEGORY_INTENTS,
        display_category_map=DISPLAY_CATEGORY,
        inquiry_statuses=INQUIRY_STATUS_LABELS,
        question_statuses={k.value: v for k, v in QUESTION_STATUS_LABELS.items()},
        review_reasons={k.value: v for k, v in REVIEW_REASON_LABELS.items()},
    )


# ───────── 문의 검토 ─────────

@router.get("/reviews", response_model=ReviewListOut, summary="문의 검토 목록 (탭: 검토대기 / 전체 / 답변완료, 최신순)")
def list_reviews(
    tab: Literal["pending", "all", "done"] = Query("pending"),
    q: str | None = Query(None, description="고객명, 문의번호, 상품명, 메시지 내용 검색"),
    category: str | None = Query(None, description="문의 유형: 배송 / 결제 / 교환/환불 / 주문 / 기타. 비우면 전체"),
    page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin),
):
    if category is not None and category not in DISPLAY_CATEGORIES:
        raise AppError(422, "INVALID_CATEGORY", f"문의 유형은 {', '.join(DISPLAY_CATEGORIES)} 중 하나입니다.")
    return reviews.list_reviews(db, tab, q, category, page, size)


@router.get("/reviews/{inquiry_no}", response_model=ReviewDetail,
            summary="문의 상세 (채팅 내역, 고객·문의 정보, AI 답변·관리자 검토, 이전 채팅 내역)")
def review_detail(inquiry_no: str, db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin)):
    return reviews.get_detail(db, inquiry_no)


@router.post("/reviews/{inquiry_no}/questions/{question_id}/approve", response_model=ReviewDetail,
             summary="승인하기 (최종 답변 → 같은 채팅에 상담원 답변으로 전달, 상담은 계속)")
def approve(inquiry_no: str, question_id: int, data: ApproveRequest, db: Session = Depends(get_db),
            admin: AdminUser = Depends(get_current_admin)):
    return reviews.approve(db, inquiry_no, question_id, admin, data)


@router.put("/reviews/{inquiry_no}/questions/{question_id}/answer", response_model=ReviewDetail,
            summary="답변 수정하기 (상담원 답변을 고침 → 고객 채팅에 '수정됨'으로 표시)")
def edit_answer(inquiry_no: str, question_id: int, data: AnswerEditRequest, db: Session = Depends(get_db),
                admin: AdminUser = Depends(get_current_admin)):
    return reviews.edit_answer(db, inquiry_no, question_id, admin, data.response_text)


# ───────── 운영 대시보드 ─────────

@router.get("/dashboard", response_model=DashboardOut, summary="운영 대시보드 (전체 기간)")
def get_dashboard(include_demo: bool = Query(True, description="시연용 데이터 포함 여부"),
                  db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin)):
    return dashboard.build_dashboard(db, include_demo)


# ───────── 정책 문서 관리 ─────────

@router.get("/policies", response_model=list[PolicyListItem], summary="정책 문서 목록")
def list_policies(q: str | None = Query(None, description="문서명 검색"),
                  db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin)):
    return policies.list_policies(db, q)


@router.post("/policies", response_model=PolicyDetail, status_code=201, summary="새 PDF 등록")
async def create_policy(file: UploadFile = File(..., description="PDF 파일 (200MB까지)"),
                        title: str | None = Form(None, description="문서명 (비우면 파일명)"),
                        description: str | None = Form(None, description="설명 (선택)"),
                        db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin)):
    return await policies.upload_new(db, file, title, description)


@router.get("/policies/{policy_id}", response_model=PolicyDetail, summary="선택한 문서 정보")
def policy_detail(policy_id: int, db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin)):
    return policies.get_policy(db, policy_id)


@router.put("/policies/{policy_id}/file", response_model=PolicyDetail,
            summary="PDF 파일 교체 (버전 +1, 새 버전의 id로 바뀜)")
async def replace_policy_file(policy_id: int, file: UploadFile = File(...), db: Session = Depends(get_db),
                              _: AdminUser = Depends(get_current_admin)):
    return await policies.upload_replacement(db, policy_id, file)


@router.get("/policies/{policy_id}/file", summary="PDF 보기(미리보기) / 받기",
            response_class=FileResponse, responses={200: {"content": {"application/pdf": {}}}})
def policy_file(policy_id: int, download: bool = Query(False, description="true면 파일 받기, false면 브라우저에서 보기"),
                db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin)):
    doc, path = policies.get_file(db, policy_id)
    media = "application/pdf" if path.suffix.lower() == ".pdf" else "application/octet-stream"
    return FileResponse(path, filename=doc.file.filename, media_type=media,
                        content_disposition_type="attachment" if download else "inline")


@router.delete("/policies/{policy_id}", status_code=204, summary="문서 삭제 (목록에서 숨김)")
def delete_policy(policy_id: int, db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin)):
    policies.delete_policy(db, policy_id)
    return Response(status_code=204)


# ───────── 학습 데이터 ─────────

@router.get("/training-data/export", summary="학습 데이터 CSV 내보내기 (train.csv와 같은 열)",
            response_class=Response, responses={200: {"content": {"text/csv": {}}}})
def export_training(db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin)):
    filename = f"training_data_{kst_today():%Y%m%d}.csv"
    return Response(content=training.export_training_csv(db).encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})