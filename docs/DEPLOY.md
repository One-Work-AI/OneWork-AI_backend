# 백엔드 배포 (Render)

백엔드를 [Render](https://render.com) 무료 웹 서비스에 올리는 순서입니다. 설정은 레포 맨 위의 `render.yaml`에 들어 있습니다.

```
[프론트: Streamlit Cloud] ──▶ [백엔드: Render] ──▶ [팀 DB: 배포된 PostgreSQL]
                                     └──────────▶ [AI 서버: RunPod /chat]
```

## 준비물

| 무엇 | 어디서 |
|---|---|
| 배포된 팀 DB 접속 정보 (host, port, DB 이름, 계정, 비밀번호) | DB 담당자. **인터넷에서 접속되는 주소**여야 합니다 (Tailscale 100.x 주소는 Render에서 접속 불가) |
| 그 DB에 팀 스키마(`cs_chatbot`)와 샘플 데이터 | DB 담당자의 `schema.sql`, `seed_demo.sql`, `seed_extra.sql` (체험 계정 `demo@example.invalid`, `extra_demo_admin`이 여기 들어 있음) |
| AI 서버 주소 | RunPod 주소 (예: `https://<pod-id>-9000.proxy.runpod.net`) |

## 순서

1. https://render.com 에 GitHub 계정으로 가입·로그인합니다.
2. **New → Blueprint** → `One-Work-AI/OneWork-AI_backend` 레포를 고릅니다.
   - 레포가 목록에 없으면 **Configure account**에서 Render GitHub 앱에 이 레포 접근을 허용합니다 (조직 레포라 조직 관리자 승인이 필요할 수 있음).
3. `render.yaml`을 읽어서 입력 칸이 나옵니다. 아래 값을 넣고 **Apply**:

   | 항목 | 값 |
   |---|---|
   | `PGHOST` | DB 주소 |
   | `PGDATABASE` | DB 이름 (예: `cs_chatbot`) |
   | `PGUSER` | DB 계정 |
   | `PGPASSWORD` | DB 비밀번호 |
   | `AI_SERVER_URL` | RunPod 주소 (끝에 `/` 없이) |

   포트가 5432가 아니면 서비스의 **Environment**에서 `PGPORT`도 고칩니다.
4. 배포가 끝나면 주소가 생깁니다: `https://onework-ai-backend.onrender.com` (이름이 겹치면 뒤에 글자가 붙음)
5. 확인:
   - `https://<주소>/health` → `{"status":"ok","ai_mode":"http"}`
   - `https://<주소>/docs` → API 문서 화면
6. 프론트의 백엔드 주소를 이 주소로 바꿉니다.

## 켜질 때 일어나는 일

- `alembic upgrade head`: 백엔드 전용 테이블 `cs_backend.conversation_ext`를 만듭니다 (이미 있으면 그대로).
  팀 스키마(`cs_chatbot`)가 없으면 여기서 멈추고 로그에 이유가 나옵니다 → DB 담당자가 `schema.sql`을 먼저 넣어야 합니다.
- 체험 계정이 있는지 확인하려면 Render **Shell**(유료) 대신, 배포 후 `POST /api/demo/customer`가 200인지 보면 됩니다.
  503 `DEMO_NOT_READY`면 DB에 체험 계정이 없는 것입니다 (샘플 데이터를 넣거나 `DEMO_*` 환경변수를 DB에 있는 계정으로 바꿈).

## 무료 플랜에서 알아둘 점

- **15분간 요청이 없으면 잠듭니다.** 다음 첫 요청은 50초쯤 걸립니다 → 시연 직전에 `/health`를 한 번 열어 깨워 두세요.
- **서버 디스크는 재시작하면 비워집니다.** 관리자 화면에서 올린 정책 PDF 파일이 사라집니다 (DB의 문서 기록·글자는 남고, 'PDF 보기'만 안 됨).
- 서버는 1대로 돕니다. 답변 순서 보장(채팅별 잠금)과 5분 자동 종료가 서버 안에서 동작하므로 여러 대로 늘리지 마세요.
- 잠든 동안에는 5분 자동 종료 확인도 멈추고, 다음에 누가 그 채팅을 조회할 때 종료 처리됩니다.

## 환경변수를 바꿀 때

Render 서비스 → **Environment**에서 고치고 저장하면 자동으로 다시 배포됩니다.

| 상황 | 바꿀 것 |
|---|---|
| AI 서버(RunPod)를 껐다 | `AI_MODE=mock` (백엔드 안의 가짜 AI로 동작). 안 바꾸면 모든 질문이 'AI 오류'로 관리자 검토에 쌓임 |
| RunPod 주소가 바뀌었다 | `AI_SERVER_URL` |
| DB 주소·비밀번호가 바뀌었다 | `PG*` |

## 코드를 고친 뒤

`main` 브랜치에 머지하면 Render가 자동으로 다시 배포합니다 (작업 브랜치 → `develop` → `main`).
