# AI 서버 입출력 형식 (모델·RAG 팀 전달용)

고객은 **채팅**으로 문의합니다. 고객이 메시지를 보낼 때마다 백엔드가 AI 서버를 한 번 호출합니다.
AI 서버는 **분류 → 정책 검색 → 답변 생성을 한 번에** 해서 돌려주고, 백엔드는 결과를 저장한 뒤
**신뢰도가 80% 이상이면 챗봇 답변으로 바로 보내고, 미만이면 관리자 검토**로 넘깁니다.

## 지금 연결하는 팀 AI 서버 (`/chat`, 기본)

팀 AI 서버([ddongzz/cs_chatbot](https://github.com/ddongzz/cs_chatbot) `api/main.py`)는 아래 형식이라 백엔드가 이 형식에 맞춰 호출합니다
(백엔드 `.env`: `AI_MODE=http`, `AI_SERVER_URL=http://<AI 서버 PC>:9000`, `AI_SERVER_FORMAT=chat`).

```
POST {AI_SERVER_URL}/chat
요청: {"query": "배송 기간이 며칠이나 걸리나요?", "user_id": "Q20261006-001"}
응답: {"answer": "…", "referenced_context": "검색된 약관 조각 3개를 이은 글", "processing_time": 12.3}
```

- `/chat`은 **카테고리·의도·신뢰도를 주지 않아서** 백엔드가 질문 키워드로 분류하고 신뢰도를 정합니다
  (mock AI와 같은 규칙: 키워드 0개 0.4 / 1개 0.8 / 2개 이상 0.95). 응답에 `category`·`intent`·`*_confidence`를 넣어 주면 그 값을 씁니다.
- 답변에 "약관에서 해당 내용을 찾을 수 없"는 문구가 있으면 신뢰도를 0.3으로 낮춰 **관리자 검토**로 보냅니다.
- 답변에 학습 데이터의 **치환자**(`{{orders.order_no}}`, `{{shipping.address}}`, `{{transaction_id}}`, `{{name}}` 등)가 있으면
  백엔드가 팀 DB 값(질문한 고객 + 가장 최근 주문의 배송·결제·환불)으로 채워서 보냅니다. 팀 DB에 없는 값
  (`{{case_no}}`, `{{reference_no}}` 등)이 남으면 자동 전송하지 않고 관리자 검토로 보냅니다. `{{Website URL}}`은 백엔드 `.env`의 `SITE_URL`.
- 이전 대화·고른 주문은 `/chat`이 받지 않아서 보내지 않고, 근거(`referenced_context`)는 정책 조각 번호가 없어 팀 DB `retrieved_policy`에 남지 않습니다.
- 아래 `/v1/answer` 형식(분류·신뢰도·근거 목록 포함)으로 AI 서버를 바꾸면 `.env`의 `AI_SERVER_FORMAT=v1`로 전환합니다.

## 요청 — 전체 형식 (`AI_SERVER_FORMAT=v1`)

`POST {AI_SERVER_URL}/v1/answer`

```json
{
  "inquiry_no": "Q20261001-001",
  "question": "그럼 계좌이체는요?",
  "order": {"order_no": "20241210-1234567", "product_name": "무선 블루투스 이어폰 Pro"},
  "history": [
    {"role": "customer", "content": "환불 신청했는데 언제 들어와요?"},
    {"role": "assistant", "content": "카드 결제는 영업일 기준 3~5일 안에 환불됩니다."}
  ]
}
```

- `question`: 이번에 답할 고객 메시지
- `history`: **같은 채팅의 이전 대화** (오래된 순, 최대 6개 — 백엔드 `.env`의 `AI_HISTORY_MESSAGES`)
  - `role`: `customer`(고객) / `assistant`(챗봇 또는 상담원 답변)
  - 위 예시처럼 "그럼 계좌이체는요?"는 앞 대화를 봐야 환불 질문인 걸 알 수 있습니다. 검색어를 만들 때와 답변을 만들 때 참고해 주세요.
  - **아직 쓰지 못하면 무시해도 됩니다.** 그러면 질문 하나만 보고 답하게 됩니다.
- `order`: 고객이 채팅 시작할 때 고른 **주문 상품** ('선택 안 함'이면 `null`). 답변에 참고할 수 있고, **안 쓰면 무시해도 됩니다.**
- `inquiry_no`: 로그 추적용
- 백엔드는 최대 `AI_TIMEOUT_SECONDS`(기본 120초) 동안 기다립니다.

## 응답 (200)

```json
{
  "category": "환불",
  "intent": "환불 진행 상황 조회",
  "category_confidence": 0.93,
  "intent_confidence": 0.88,
  "answer": "계좌이체로 결제하신 경우 영업일 기준 1~3일 안에 환불됩니다.",
  "sources": [
    {"document_key": "refund_policy", "chunk_index": 2, "title": "환불 정책", "text": "…", "score": 0.82}
  ],
  "score_metric": "cosine_similarity",
  "model": "EXAONE-3.5-7.8B + LoRA(run_final)",
  "prompt_version": "v1"
}
```

| 필드 | 필수 | 설명 |
|---|---|---|
| `category` | ✅ | 아래 7개 중 하나 (글자까지 똑같이) |
| `intent` | ✅ | 아래 17개 중 하나, `category`에 속한 의도여야 함 |
| `category_confidence`, `intent_confidence` | ✅ **중요** | 0~1. **둘 중 작은 값이 0.8 이상이면 자동답변.** 값이 없으면 기준을 확인할 수 없어서 전부 관리자 검토로 갑니다 |
| `answer` | ✅ | 고객에게 보낼 답변. 검토로 가는 경우에는 관리자 화면의 'AI 답변 초안'으로 보여줍니다 |
| `sources` | ✅ (빈 배열 가능) | 답변 근거로 검색한 정책 조각, 관련도 높은 순서 |
| `sources[].document_key` | 권장 | 정책 문서 키 = **PDF 파일 이름에서 확장자를 뺀 값** (예: `delivery_policy.pdf` → `delivery_policy`) |
| `sources[].chunk_index` | 권장 | 문서 안의 조각 순서 (0부터) |
| `sources[].title`, `text` | ✅ | 형식 확인용 (고객 화면에는 표시하지 않음) |
| `sources[].score` | 권장 | 검색 관련도 0~1 |
| `score_metric` | 선택 | `score`를 계산한 방식 (예: `cosine_similarity`). 팀 DB `retrieved_policy.score_metric`에 기록 |
| `model`, `prompt_version` | 선택 | 기록용 |

### 카테고리 / 의도 (학습 데이터와 같은 값)

| 카테고리 | 의도 |
|---|---|
| 결제 | 결제 문제, 결제 수단 확인, 청구서 받기, 청구서 확인 |
| 문의 | 고객 서비스 문의 |
| 배송 | 배송 기간 확인, 배송 방법 확인 |
| 배송지 | 배송지 변경, 배송지 설정 |
| 주문 | 주문 배송 조회, 주문 변경, 주문 취소, 주문하기 |
| 취소 | 취소 수수료 확인 |
| 환불 | 환불 정책 확인, 환불 진행 상황 조회, 환불받기 |

## 모델 팀에 확인 부탁드리는 것

1. **신뢰도를 줄 수 있는지, 어떻게 계산하는지.** EXAONE은 답변만 만들기 때문에 분류 신뢰도는 따로 계산해야 합니다 (분류기 확률, 생성 토큰 확률 등). 이 값이 자동답변 여부를 결정합니다.
2. **`history`와 `order`를 쓸 수 있는지.** 지금 모델은 질문 하나로 학습했으니, 이전 대화를 넣었을 때 답변 품질을 한 번 확인해 주세요.
3. **검색 점수 범위.** 0~1이 아니라면 알려주세요.

## 정책 문서 (PDF) — 시연용 방식

- 정책 문서는 PDF입니다. 관리자 화면에서 등록·교체할 수 있지만, **AI 서버에 자동으로 전달되지는 않습니다.**
- 시연 전에 **같은 PDF 파일들**을 RAG 팀이 한 번 적재해 주세요. 시연 중에는 교체하지 않는 것으로 정했습니다.
- 파일 이름(확장자 제외)을 `document_key`로, 조각 순서를 `chunk_index`로 돌려주면 팀 DB의 정책 조각과 자동 연결됩니다.
  근거는 팀 DB `retrieved_policy`에 정책 조각 번호로만 저장되므로, **둘 다 맞아야 관리자 화면에 근거가 남습니다.**
  조각 나누기 방식(`CHUNK_MAX_CHARS`, `app/plugins/chunker.py`)을 RAG 팀과 맞춰 주세요.

## 시험해 보기

GPU 없이 시험하는 가짜 서버 (`/chat`, `/v1/answer` 둘 다 응답): `uv run uvicorn scripts.fake_ai_server:app --port 9000`
백엔드 `.env`를 `AI_MODE=http`, `AI_SERVER_URL=http://localhost:9000`으로 바꾸고 백엔드를 다시 켜면 연결됩니다.
실제 AI 서버로 바꿀 때는 `AI_SERVER_URL`만 그 PC 주소로 바꾸면 됩니다 (같은 PC면 localhost, 다른 PC면 Tailscale 주소).
