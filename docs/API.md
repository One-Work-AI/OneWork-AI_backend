# API 안내 (프론트엔드 전달용 — DAITDA 화면 기준)

- 서버를 켜면 **http://localhost:8000/docs** 에서 모든 API를 직접 눌러보며 확인할 수 있습니다.
- 모든 시각은 UTC로 내려갑니다 (예: `2026-10-01T01:28:38Z`). 화면에는 한국 시간으로 바꿔서 보여주세요.
- 문의 유형(`category`)은 화면용 5개로 내려갑니다: **배송 / 결제 / 교환/환불 / 주문 / 기타** (분류 전이면 `null`)
- 문의번호(`inquiry_no`) 형식: `Q20261002-001` (채팅 1개 = 문의번호 1개). CS 번호는 쓰지 않습니다.
- 문의 상태: **상담 중(CHATTING) / 검토대기(REVIEWING) / 답변완료(ANSWERED)**, 답변완료면 `answered_by`: **AI / ADMIN**

## 로그인 화면 — 체험 입장

| 버튼 | API |
|---|---|
| 고객으로 체험 | `POST /api/demo/customer` → 시연 고객(김민지) 토큰 |
| 관리자로 체험 | `POST /api/demo/admin` → 관리자 토큰 (비밀번호 없음) |

응답: `{"access_token": "...", "role": "customer", "name": "김민지"}` — `name`은 왼쪽 아래 "김민지님"에 표시

- `st.session_state`에 토큰을 넣어 두고 모든 요청에 `Authorization: Bearer {access_token}`
- **로그아웃 = 토큰을 지우고 로그인 화면으로.** 이메일·비밀번호 입력란은 백엔드와 연결되지 않습니다 (체험 버튼만 동작).

## 에러 형식 (공통)

```json
{"error": {"code": "CONVERSATION_CLOSED", "message": "종료된 상담입니다. 새 채팅으로 문의해 주세요."}}
```

| code | 상태 | 언제 |
|---|---|---|
| `VALIDATION_ERROR` | 422 | 입력 형식 오류 (`details`에 칸별 메시지) |
| `UNAUTHORIZED` | 401 | 토큰이 없거나 만료 → 로그인 화면으로 |
| `CONVERSATION_NOT_FOUND` / `ORDER_NOT_FOUND` | 404 | 없는 문의번호·주문번호 |
| `CONVERSATION_CLOSED` | 409 | 종료된 채팅에 메시지를 보냄 (입력이 막히는 유일한 경우) |
| `NOT_REVIEW_PENDING` | 409 | 이미 처리한 질문을 승인하려 함 |
| `DUPLICATE_POLICY` | 409 | 같은 파일 이름의 정책 문서가 이미 있음 |
| `UNSUPPORTED_FILE_TYPE` / `FILE_TOO_LARGE` | 422 / 413 | PDF가 아니거나 200MB 초과 |

---

## 고객 문의 (채팅)

**화면을 열 때** `GET /api/conversations/current` → 진행 중인 채팅이 있으면 이어서 보여주고, 없으면 `null`
(인사말 "안녕하세요, daitda 고객센터 AI 상담사예요…"와 추천 질문 버튼은 화면에서 고정 문구로)

**문의할 주문 상품** `GET /api/orders` → `[{"order_no": "20241210-1234567", "product_name": "무선 블루투스 이어폰 Pro"}, …]`
맨 위에 "선택 안 함"을 화면에서 추가

**첫 메시지 보내기** `POST /api/conversations?wait=true`
```json
{"content": "주문한 상품 언제 도착하나요?", "order_no": "20241210-1234567"}
```
- 상품을 '선택 안 함'이면 `order_no`를 빼면 됩니다. 고른 상품은 채팅 위에 `product_name`으로 표시
- `wait=true`면 AI 답변까지 기다렸다가 채팅 전체를 돌려줍니다 (`st.spinner`로 감싸기)

**이어서 보내기** `POST /api/conversations/{문의번호}/messages?wait=true` `{"content": "..."}`
- 앞 질문의 답을 기다리지 않고 보내도 됩니다. 서버가 **보낸 순서대로** 하나씩 답합니다.
  (`wait=true`면 앞 질문 → 이번 질문 답변까지 기다렸다가 응답)

**채팅 응답**
```json
{
  "inquiry_no": "Q20261002-010", "status_code": "CHATTING", "status_label": "상담 중",
  "product_name": "무선 블루투스 이어폰 Pro", "category": "배송",
  "input_locked": false, "answering": false, "review_pending": false, "auto_close_at": "2026-10-02T05:19:00Z",
  "messages": [
    {"id": "q31", "question_id": 31, "role": "CUSTOMER", "content": "주문한 상품 언제 도착하나요?", "is_notice": false},
    {"id": "r18", "question_id": 31, "role": "BOT", "content": "주문하신 상품은 …", "is_notice": false}
  ]
}
```
- `role`: CUSTOMER(오른쪽 주황 말풍선) / BOT(AI 상담사) / ADMIN(상담원 답변)
- `id`: 화면 목록용 고유 키 (문자열). `question_id`: 이 메시지가 속한 고객 질문 번호 (질문이면 자기 자신)
- **입력창 잠금** `input_locked`: **상담이 종료됐을 때만** `true`
  → "채팅이 종료되었어요. 문의번호 #…로 저장되었어요" + **새 채팅하기 / 문의 내역 보기**
- `answering: true`: AI가 답을 만드는 중 (입력은 그대로 가능, "답변 작성 중…" 표시 정도)
- `review_pending: true`: 담당자 확인 중인 질문이 있음. 해당 질문 뒤에 `is_notice: true`인 BOT 안내
  ("담당자에게 전달했어요. 답변이 오면 이 채팅에서 알려드릴게요…")가 붙어 있고, **입력은 계속 가능**합니다.
  관리자가 승인하면 **같은 채팅에 ADMIN 메시지**(상담원 답변, `question_id` = 해당 질문)가 붙고 상담은 계속됩니다.
- **5분 자동 종료**: `auto_close_at`까지 메시지가 없으면 서버가 종료합니다.
  챗봇·상담원 답변 후에만 세고, **검토 대기 중인 질문이 있으면 적용하지 않습니다** (`auto_close_at: null`).
  화면을 다시 그릴 때 `status`가 `CLOSED`인지 확인하면 됩니다.

**채팅 종료하기** `POST /api/conversations/{문의번호}/close?reason=USER`
**새 채팅하기** `POST /api/conversations/{문의번호}/close?reason=NEW_CHAT` 후 빈 채팅 화면
(진행 중인 채팅이 있는데 `POST /api/conversations`를 보내도 이전 채팅은 자동으로 종료·저장됩니다)

## 문의 내역

`GET /api/conversations?status=&q=&page=1&size=10` (최신순)

- 탭: 전체(생략) / 상담 중 `status=CHATTING` / 답변완료 `status=ANSWERED`
  - 고객 화면에는 '검토대기' 상태가 없습니다. 검토 대기 중인 문의는 관리자가 답할 때까지 '상담 중'으로 보입니다.
- `q`: 문의 내용, 상품명 검색
- 카드: `status_label`(상담 중/답변완료) + `answered_by_label`(AI 답변/관리자 답변), `category`,
  `product_name`(null이면 "주문 상품 미선택"), `preview`(첫 질문), `created_at` / 페이지: `page.total`

## 문의 상세 (고객)

`GET /api/conversations/{문의번호}` → 위 '채팅 응답' + 아래 항목

| 화면 | 필드 |
|---|---|
| 배지 | `status_label`, `answered_by_label` |
| 제목 | 첫 고객 메시지 (`messages[0].content`) |
| 문의번호 / 문의 유형 / 주문 상품 | `inquiry_no`, `category`, `product_name` |
| 등록일 / 답변완료 | `created_at`, `answered_at` |
| 답변 칸 | `admin_answer` (마지막 관리자 답변, 채팅 안에도 있음). `null`이고 AI가 답했으면 "AI 상담사가 채팅에서 바로 답변드렸어요" |
| 상담 내역 | `messages`, 위에 "`created_at` 시작 · `close_reason_label` `closed_at`" (예: 고객 종료 14:36) |
| 이어서 채팅하기 | `status_code`가 `CHATTING`일 때만 → 고객 문의 화면 |

---

## 관리자 — 문의 검토

`GET /api/admin/reviews?tab=pending&q=&category=&page=1&size=20` (최신순)

- 탭: `pending`(검토대기) / `all`(전체) / `done`(답변완료), `counts`: `{"pending": 4, "all": 10, "done": 6}`
  - 상단 배너 "AI가 답변하지 못한 문의 **n건**" = `counts.pending`
  - 전체 = 검토대기 + 답변완료 (상담 중인 채팅은 끝난 뒤에 목록에 나타남)
- `category`: 배송 / 결제 / 교환/환불 / 주문 / 기타 (생략하면 전체)
- `q`: 고객명, 문의번호, 상품명, 메시지 내용
- 표: `inquiry_no`, `customer_name`, `category`, `status_label`, `product_name`, `customer_email`, `customer_phone`,
  `created_at`(등록일자), `answered_at`(답변)
- (선택) 대기 시간 `waiting_minutes`, 검토로 온 이유 `reasons` — 화면에 넣을지는 팀에서 결정

## 관리자 — 문의 상세

`GET /api/admin/reviews/{문의번호}`

| 화면 | 필드 |
|---|---|
| 채팅 내역 | `messages`, "`close_reason_label` · `closed_at`" |
| 고객·문의 정보 | `customer.name/email/phone`, `category`, `product_name`, `order_no`, `created_at`(접수일), `answered_at` |
| AI 답변 초안 | `review_question.ai_draft.text` |
| 최종 답변 (검토 후 수정) | 기본값으로 초안을 넣고 수정 |
| 이전 채팅 내역 | `previous_inquiries` (같은 고객의 다른 문의, 최신순) |

`review_question`은 검토대기 질문이고, 처리 후에는 처리한 질문입니다 (이때 `review_question.review`에 처리 기록).
AI만 답한 문의면 `null`. 고객이 검토 중에도 계속 채팅할 수 있어서, 채팅 내역에는 그 질문 뒤의 대화도 함께 보입니다
(`messages` 중 `question_id`가 `review_question.question_id`와 같은 메시지를 강조하면 됩니다).

**승인하기** `POST /api/admin/reviews/{문의번호}/questions/{review_question.question_id}/approve`
```json
{"response_text": "최종 답변"}
```
→ 고객 채팅에 상담원 답변이 붙고 **상담은 계속**됩니다. (선택 항목: `category`·`intent`·`use_for_training`·`remark` — 지금 화면에선 안 보내도 됨)

## 관리자 — 운영 대시보드 (전체 기간)

`GET /api/admin/dashboard`

| 화면 | 필드 |
|---|---|
| 전체 문의 | `total_inquiries` (= 검토대기 + 답변완료) |
| 검토대기 | `pending_review` |
| 답변완료 · AI n건 · 관리자 n건 | `answered.total`, `answered.ai`, `answered.admin` |
| AI 자동 응답률 | `ai_auto_rate` (0.4 = 40%) |
| 문의 유형별 비중 | `by_category` (`name`, `count`, `ratio`) |
| 시간대별 인입 및 처리 | `hourly` (0~23시, `received` / `answered`) — 09~18시만 잘라서 표시 |
| 피크 타임 알림 | `peak_window` → "13:00 ~ 15:00 구간 기타 문의 집중" (`start_hour`, `end_hour`, `top_category`) |
| 검토대기 문의 (오래된 순) | `pending_list` (`inquiry_no`, `customer_name`, `category`, `product_name`, `preview`, `created_at`) |

## 관리자 — 정책 문서 관리 (PDF)

| 화면 | API |
|---|---|
| 정책 문서 목록 | `GET /api/admin/policies?q=` → `title`, `filename`, `size_bytes`, `created_at` |
| 선택한 문서 정보 | `GET /api/admin/policies/{id}` → 문서명 `title`, 설명 `description`, 파일 `filename`·`size_bytes`, 등록일 `created_at`, `version` |
| PDF 미리보기 | `GET /api/admin/policies/{id}/file` (브라우저에서 보기) |
| 파일 받기 | `GET /api/admin/policies/{id}/file?download=true` |
| PDF 파일 교체 | `PUT /api/admin/policies/{id}/file` (multipart, 필드 `file`) → 버전 +1, **응답의 `id`가 새 버전 id로 바뀜** |
| 문서 삭제 | `DELETE /api/admin/policies/{id}` (목록에서 숨김) |
| 새 PDF 등록 | `POST /api/admin/policies` (multipart: `file`, `title`(비우면 파일명), `description`) |

- PDF만, 200MB까지. 같은 파일 이름의 문서가 있으면 409 → 'PDF 파일 교체'를 쓰면 됩니다.
- 교체하면 팀 DB에 새 버전 행이 생겨서 `id`가 바뀝니다. 교체 후에는 목록을 다시 불러오세요 (이전 id로 불러도 사용 중인 버전을 돌려줍니다).
- **시연 중에 새로 등록하거나 교체한 내용은 AI가 알지 못합니다** (AI 검색은 RAG 팀이 같은 PDF를 시연 전에 적재).

## 기타

- 코드값: `GET /api/admin/meta` (문의 유형 5개, 상태 이름)
- 학습 데이터 내보내기: `GET /api/admin/training-data/export` (CSV)
