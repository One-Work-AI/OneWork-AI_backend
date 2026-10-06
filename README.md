# DAITDA CS 챗봇 — 백엔드

FastAPI + SQLAlchemy + Alembic. DB는 **팀 PostgreSQL**(DB 담당자의 `schema.sql`로 만든 `cs_chatbot` 스키마)을 그대로 씁니다.
팀 DB 값으로 계산할 수 없는 정보(채팅 종료 사유, 시연 표시)만 `cs_backend` 스키마의 작은 테이블 1개에 둡니다
(아래 [팀 DB와 연결](#팀-db와-연결)).

- 프론트엔드용 API 안내 (DAITDA 화면별): [docs/API.md](docs/API.md) — 서버 실행 후 http://localhost:8000/docs 에서도 확인
- 모델·RAG 팀용 AI 서버 입출력 형식: [docs/AI_SERVER_CONTRACT.md](docs/AI_SERVER_CONTRACT.md)
- 배포 (Render): [docs/DEPLOY.md](docs/DEPLOY.md) — 설정은 `render.yaml`
- 팀 DB (스키마·ERD·데이터 사전): [One-Work-AI/DAITDA_WOO](https://github.com/One-Work-AI/DAITDA_WOO)
- 프론트엔드는 별도 레포에서 이 API를 호출합니다.

## 처음 실행하기 (Windows PowerShell, uv 사용)

저장소 맨 위 폴더에서 실행합니다. Python 3.12(.python-version), uv를 씁니다.

```powershell
uv sync                                # 가상환경 + 패키지 설치 (.venv)
copy .env.example .env                 # 그다음 .env에서 PGPASSWORD(DB 폴더 .env와 같은 값)와 JWT_SECRET 바꾸기

# 팀 DB에 접속되는지 먼저 확인 (Tailscale 켜기 → TcpTestSucceeded : True)
Test-NetConnection 100.106.86.93 -Port 5433

uv run alembic upgrade head            # 백엔드 전용 테이블 1개(cs_backend.conversation_ext) 만들기 — 팀 DB 테이블은 건드리지 않음
uv run python -m scripts.seed_base     # 체험 계정 확인 (팀 DB의 '테스트고객'·extra_demo_admin, 없을 때만 만듦)

uv run uvicorn app.main:app --reload   # http://localhost:8000/docs
```

정책 문서는 팀 DB에 있는 것을 씁니다. 빈 연습용 DB에서 샘플 정책이 필요하면
`uv run python -m scripts.ingest_policies --dir policies_sample` (실제 PDF는 관리자 화면에서 등록).

시연용 문의 넣기 (팀 DB의 기존 가상 고객 여러 명으로):

```powershell
uv run python -m scripts.seed_demo --csv <validation_canonical_981.csv 경로> --n 20
```

팀 DB는 상담 기록(AI 분석·검토 기록·전송된 답변)을 지울 수 없게 되어 있어서 시연 데이터 삭제 스크립트는 없습니다.
시연 채팅에는 `is_demo` 표시가 붙어서 대시보드(`include_demo=false`)와 학습 데이터 내보내기에서 뺄 수 있습니다.

테스트와 코드 검사 (테스트용 임시 PostgreSQL을 따로 띄우므로 팀 DB가 꺼져 있어도 되고, 팀 DB에 영향 없음):

```powershell
uv run pytest -q
uv run ruff check .                    # PR을 올리면 GitHub Actions(CI)가 같은 검사와 테스트를 돌립니다
```

## 처리 흐름

```
[고객으로 체험] → 고객 문의: 메시지 보냄
        │
        ▼
   AI 처리 ─▶ AI 서버: 분류 + 정책 검색 + 답변 초안 (이전 대화·고른 상품 포함)
   (연달아 보내면 보낸 순서대로 하나씩 처리)
                                  │
              ┌───────────────────┴──────────────────────┐
     신뢰도 80% 이상                                 80% 미만 / 오류
   AI 상담사가 바로 답변                    "담당자에게 전달했어요" 안내 (입력은 계속 가능, 5분 타이머 멈춤)
   5분 타이머 시작                                      │ [관리자로 체험] → 문의 검토 → 승인하기
        │                                    상담원 답변이 같은 채팅에 붙음 → 상담 계속, 5분 타이머 다시 시작
        ▼
 계속 질문 / 채팅 종료하기 / 새 채팅하기(이전 채팅 저장·종료) / 5분 무응답 → 자동 종료
 ※ 입력이 막히는 건 상담이 종료됐을 때뿐
```

## CRUD 구성

| 구분 | C (생성) | R (조회) | U (수정) | D (삭제) |
|---|---|---|---|---|
| 고객 | 새 채팅(주문 상품 선택), 메시지 보내기 | 주문 상품 목록, 진행 중인 채팅, 문의 내역, 문의 상세 | 채팅 종료 (채팅 종료하기 / 새 채팅하기) | 없음 (보류). 보낸 메시지는 수정·취소 불가 |
| 관리자 (문의 검토) | 검토대기 질문에 최종 답변 승인 | 검토대기 / 전체 / 답변완료 목록, 상세, 이전 채팅 내역 | 최종 답변 수정 | 없음 (상담 기록 보존) |
| 관리자 (정책 문서) | 새 PDF 등록 | 목록, 문서 정보, PDF 보기·받기 | PDF 파일 교체 (버전 +1) | 문서 삭제 (숨김) |

이 외 관리자 기능: 운영 대시보드(전체 기간), 학습 데이터 CSV 내보내기.

## 폴더 구조

```
app/
  main.py              서버 시작점 (CORS, 에러 형식, 라우터, 미처리 질문 재처리, 자동 종료 확인)
  config.py            .env 설정
  db.py, models.py     DB 연결, 테이블
  enums.py             상태, 문의 유형 (DB 7개 / 화면 5개), 검토 사유
  schemas.py           API 요청·응답 형식
  security.py          체험 입장 토큰 (고객 / 관리자)
  api/demo.py          고객으로 체험 / 관리자로 체험
  api/customer.py      고객 API (고객 문의, 문의 내역, 문의 상세)
  api/admin.py         관리자 API (문의 검토, 운영 대시보드, 정책 문서 관리, 학습 데이터)
  services/            실제 로직 (채팅, AI 처리 흐름, 자동답변 판단, 검토, 대시보드, 정책, 학습 데이터)
  plugins/             ★ 다른 팀 결과물이 나오면 바꾸는 곳
    ai_client.py         AI 서버 입출력 형식, mock/http 선택
    order_source.py      주문 정보 가져오는 곳 (지금: 팀 DB의 orders / order_item)
    document_parser.py   PDF·TXT·MD → 글자
    chunker.py           정책 글자 조각 나누기
    mock_ai.py, keyword_retriever.py, http_ai.py
migrations/            Alembic (백엔드 전용 cs_backend 테이블만)
scripts/               시연용 계정, 정책 등록, 시연 문의, 가짜 AI 서버
policies/              실제 정책 문서를 넣는 폴더
policies_sample/       시험용 샘플 정책 문서 (실제 정책 아님)
storage/policies/      등록한 정책 파일 (자동 생성)
tests/                 자동 테스트 (team_schema.sql = DB 담당자의 schema.sql 사본)
```

## 다른 팀 결과물이 나오면 바꿀 곳

| 무엇이 정해지면 | 바꿀 곳 |
|---|---|
| **AI 서버(모델·RAG)** 연결 | `.env`에서 `AI_MODE=http`, `AI_SERVER_URL=http://<AI 서버 PC>:9000` (팀 AI 서버 `/chat` 형식이 기본). 자세한 내용은 docs/AI_SERVER_CONTRACT.md |
| **신뢰도 계산 방식·범위** | `.env`의 `REVIEW_MIN_CONFIDENCE` (지금 0.8) |
| **다른 쇼핑몰 DB**에서 주문을 읽게 되면 | `app/plugins/order_source.py`에 클래스 추가 + `.env`의 `ORDER_SOURCE`. 고객을 어떻게 맞출지 정해야 함 |
| **RAG 팀 조각 나누기 방식** | `app/plugins/chunker.py` |
| **진짜 로그인** | `app/api/demo.py`만 바꾸면 됨 (나머지 API는 같은 토큰 사용) |
| **팀 DB 구조(schema.sql)** 변경 | `app/models.py`의 팀 DB 테이블 정의를 맞추고, `tests/team_schema.sql`을 새 schema.sql로 바꾼 뒤 `pytest` |

## 팀에서 정한 사항

| 항목 | 결정 |
|---|---|
| 입장 | 로그인 화면의 **고객으로 체험 / 관리자로 체험** 버튼만 동작 (비밀번호 없음). 이메일·비밀번호 입력란은 백엔드와 연결 안 함 |
| 문의 방식 | **채팅**. 채팅 1개 = 문의번호 1개. 화면에서 '문의할 주문 상품' 선택은 뺌 (API에는 선택 항목으로 남아 있음) |
| 자동답변 | AI 신뢰도 **80% 이상**이면 AI 상담사가 바로 답변, 미만이면 관리자 검토. 다른 규칙은 코드에 있지만 꺼둠 |
| 검토 흐름 | "담당자에게 전달했어요" 안내 → **입력은 계속 가능** → 관리자 답변이 **같은 채팅에 상담원 답변으로** 붙고 상담 계속 |
| 입력 잠금 | **상담이 종료됐을 때만** (채팅 종료하기 / 새 채팅하기 / 5분 무응답). AI가 답을 만드는 중에도 보낼 수 있고, **보낸 순서대로** 답함 |
| 5분 규칙 | 챗봇·상담원 답변 후 5분 무응답이면 종료. **검토 대기 중인 질문이 있으면 적용 안 함** |
| 고객 메시지 | 수정·취소 불가. 삭제는 보류 |
| 문의 상태 | 고객 화면: **상담 중 / 답변완료** (검토 대기 중이어도 관리자가 답할 때까지 '상담 중'), 탭은 전체 / 상담 중 / 답변완료. 관리자 화면: 검토대기 / 상담 중 / 답변완료 |
| 문의 내역 | 상담 중인 채팅도 표시하고 "이어서 채팅하기" 가능 |
| 문의 유형 | DB·AI는 7개 유지, 화면은 5개: 배송(배송·배송지), 결제, 교환/환불(환불), 주문(주문·취소), 기타(문의) |
| 관리자 검토 | 탭 검토대기 / 전체 / 답변완료, **최신순**. 상세에 이전 채팅 내역. 라벨 수정·학습 데이터·메모는 API 선택 항목으로 남김 |
| 대시보드 | **전체 기간** 기준. 피크 타임은 가장 많이 들어온 2시간 구간과 그 구간 최다 유형만 계산 |
| 정책 문서 | **PDF**. 새 PDF 등록, PDF 파일 교체, 문서 삭제(숨김), 미리보기. 시연용: AI 검색은 RAG 팀이 같은 PDF를 시연 전에 적재 |
| 주문 정보 | 팀 DB의 orders / order_item. 상품이 여러 개인 주문은 '첫 상품 외 N건'으로 표시 |
| 시연 데이터 | 팀 DB에 있는 가상 데이터를 그대로 씀 — 체험 고객 '테스트고객'(demo@example.invalid), 관리자 extra_demo_admin, 정책 문서. 백엔드가 새 가짜 고객·주문을 넣지 않음 |
| 제외 | 긴급도, 영어 문의, 만족도, 연락처 전체 보기, 고객 화면의 관련 정책, 계정 정보 탭, 관리자 비밀번호 로그인 |
| 문의번호 | `Q20261002-001` (Q + 한국 날짜 + 하루 일련번호 3자리). 채팅 1개 = 문의번호 1개. CS 번호는 쓰지 않음 |

## 구현하면서 정한 세부 동작

- 한 고객이 채팅을 여러 개 동시에 진행할 수 있습니다 (체험 입장은 모두 같은 고객 계정을 써서, 서로의 채팅을 끊지 않게 함).
  새 채팅을 시작해도 다른 채팅은 그대로 상담 중이고, 채팅 종료하기 / 새 채팅하기 버튼 / 5분 무응답으로 채팅마다 따로 종료됩니다.
- 검토 대기 중에 고객이 채팅을 종료해도 관리자 답변은 그 채팅에 붙고 문의 내역 상세에서 보입니다 (종료된 채팅은 다시 열리지 않음).
- 관리자 화면의 상태는 검토대기 질문이 있으면 '검토대기'가 우선이고, 없으면 상담 중 / 답변완료입니다. 답변완료일 때 관리자 답변이 하나라도 있으면 '관리자 답변', 아니면 'AI 답변'입니다.
- 질문을 연달아 보내면 채팅별로 하나씩 순서대로 처리합니다. 뒤 질문의 AI 요청에는 앞 질문의 답변까지 이전 대화로 들어갑니다.
- 문의 유형은 채팅의 **첫 질문** 분류이며, 관리자가 첫 질문의 라벨을 고치면 같이 바뀝니다.
- 고른 주문 상품은 채팅에 사본으로 저장합니다 (쇼핑몰 DB가 바뀌어도 지난 문의 기록 유지). AI 서버에도 선택 항목 `order`로 보냅니다.
- AI 서버가 신뢰도를 주지 않으면 80% 기준을 확인할 수 없어서 검토로 보냅니다.
- 정책 문서 키(doc_key)는 처음 등록한 파일 이름(확장자 제외)이며, PDF를 교체해도 바뀌지 않습니다. 이전 파일은 서버에 남깁니다.
  교체하면 팀 DB 규칙에 따라 새 버전 행이 생겨서 문서 id가 바뀝니다 (이전 id로 불러도 사용 중인 버전).
- 같은 파일 이름의 문서를 새로 등록하면 거절하고 'PDF 파일 교체'를 안내합니다.
- 대시보드의 '전체 문의'는 검토대기 + 답변완료입니다 (상담 중인 채팅은 끝난 뒤에 셈, `chatting_now`로 따로 제공).
- 학습 데이터 CSV는 train.csv와 같은 열에 문의번호와 질문 순서(turn)를 더하고, 시연용 데이터는 뺍니다.

## 팀 DB와 연결

DB 담당자의 `schema.sql`(cs_chatbot 스키마)을 기준으로 저장합니다. 백엔드는 팀 DB 테이블의 구조를 바꾸지 않습니다.

| 화면의 개념 | 팀 DB (cs_chatbot) | 백엔드 전용 (cs_backend) |
|---|---|---|
| 채팅 1개 | `conversation` | `conversation_ext` — 종료 사유(고객 종료 / 새 채팅 / 자동 종료), 시연 표시 |
| 고객 질문 | `inquiry` (`inquiry_no` = 문의번호-순서, 예: `Q20261002-001-01`) | |
| AI 분류·자동답변 판단·검토 사유 | `ai_analysis` (`decision_reason`) | |
| AI 답변 초안 / 챗봇·상담원 답변 | `ai_response` (`status=SENT`가 고객에게 나간 답변) | |
| 관리자 검토 | `admin_review` | |
| 답변 근거 | `retrieved_policy` | |
| 정책 문서 (PDF) | `policy_document`(버전별 1행, `source_file`=올린 파일 이름), `policy_chunk` | |
| 주문 (선택 항목) | `orders`, `order_item`, `product`, `inquiry.order_id` | |

화면에 보이지만 따로 저장하지 않고 팀 DB 값으로 계산하는 것

| 화면 값 | 계산 방법 |
|---|---|
| 문의번호 `Q20261002-001` | 첫 질문 `inquiry_no`(`Q20261002-001-01`)에서 순서를 뗀 값 |
| 문의 유형 | 첫 질문의 관리자 검토 라벨(`admin_review.modified_category`), 없으면 AI 분류(`ai_analysis.category`) |
| 5분 자동 종료 기준 | 마지막으로 전송된 답변의 `ai_response.sent_at` |
| 검토 사유 | `ai_analysis.decision_reason`의 사유 목록 |
| 정책 PDF 파일·크기 | 서버의 `POLICY_FILE_DIR/{문서 id}.pdf` |
| 주문 상품 | 첫 질문의 `inquiry.order_id` → `order_item` |

DB에 칸이 없어서 저장하지 않는 것: 정책 문서 설명, AI 서버 오류 내용·응답 시간(서버 로그에만 남김).

- `conversation_ext`가 있는 채팅만 백엔드 화면에 나옵니다. 팀 샘플 데이터나 실습 스크립트(save_inquiry.py)로 만든 채팅은 보이지 않습니다.
- '담당자에게 전달했어요' 안내는 저장하지 않고, 자동답변이 허용되지 않은 분석이 있으면 그 시각에 채팅에 표시합니다.
- 팀 DB의 트리거 규칙을 그대로 따릅니다: 자동 전송은 분석에서 허용된 경우만, 관리자 전송은 같은 문구를 승인한 검토가 있어야 하고,
  분석·검토 기록·전송된 답변·정책 버전은 고치거나 지울 수 없습니다.
- AI 서버 오류처럼 초안이 없는 질문은 승인할 때 '(AI 답변 초안 없음)' 답변 행을 먼저 만들고 검토합니다 (팀 DB는 검토를 답변에 연결).
- AI 근거는 팀 DB 정책 조각과 연결되는 것만 남습니다 (document_key + chunk_index, docs/AI_SERVER_CONTRACT.md).
