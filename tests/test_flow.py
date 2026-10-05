# """전체 흐름 테스트 — 실행: pytest -q

# mock AI의 신뢰도: 키워드 0개 0.4 / 1개 0.8 / 2개 이상 0.95 → 기준 0.8 이상이면 자동답변
# """
# import re
# from datetime import timedelta

# from sqlalchemy import func, select

# from app.config import Settings
# from app.db import SessionLocal
# from app.enums import ReviewReason
# from app.models import AdminReview, AIResponse, Conversation, Customer, Inquiry, RetrievedPolicy, utcnow
# from app.plugins.ai_client import AIResult, AISource
# from app.security import create_access_token
# from app.services.decision import decide
# from tests.conftest import make_pdf

# AUTO_Q = "배송 기간이 며칠이나 걸리나요?"            # 키워드 2개 → 0.95 → 자동답변
# REVIEW_Q = "안녕하세요 그냥 여쭤볼 게 있어서요"       # 키워드 0개 → 0.4 → 검토
# EARPHONE = "20241210-1234567"                          # 체험 고객의 테스트용 주문 (conftest.TEST_ORDERS)


# def _conv(db, no) -> Conversation:
#     """채팅 문의번호로 찾기 (첫 질문의 inquiry_no = 문의번호-01)."""
#     return db.scalar(select(Conversation).join(Inquiry, Inquiry.conversation_id == Conversation.id)
#                      .where(Inquiry.inquiry_no == f"{no}-01"))


# def _later(monkeypatch, minutes: int) -> None:
#     """서버 시계를 앞으로 돌림 (전송된 답변 시각은 팀 DB 규칙상 바꿀 수 없어서)."""
#     t = utcnow() + timedelta(minutes=minutes)
#     monkeypatch.setattr("app.services.conversations.utcnow", lambda: t)


# def _start(client, headers, content, order_no=None, wait=True):
#     body = {"content": content, **({"order_no": order_no} if order_no else {})}
#     r = client.post("/api/conversations", params={"wait": wait}, json=body, headers=headers)
#     assert r.status_code == 201, r.text
#     return r.json()


# def _send(client, headers, no, content, wait=True):
#     return client.post(f"/api/conversations/{no}/messages", params={"wait": wait}, json={"content": content},
#                        headers=headers)


# def _approve(client, admin_headers, no, **body):
#     qid = client.get(f"/api/admin/reviews/{no}", headers=admin_headers).json()["review_question"]["question_id"]
#     return client.post(f"/api/admin/reviews/{no}/questions/{qid}/approve", headers=admin_headers,
#                        json={"response_text": "담당자 답변입니다.", **body})


# # ───────── 체험 입장 / 권한 ─────────

# def test_demo_entry_and_roles(client, customer_headers, admin_headers):
#     r = client.post("/api/demo/customer")
#     assert r.json()["role"] == "customer" and r.json()["name"] == "테스트고객"
#     assert client.post("/api/demo/admin").json()["role"] == "admin"
#     assert client.get("/api/orders").status_code == 401
#     assert client.get("/api/admin/reviews", headers=customer_headers).status_code == 401
#     assert client.get("/api/orders", headers=admin_headers).status_code == 401


# # ───────── 고객: 주문 상품 / 채팅 ─────────

# def test_orders_for_dropdown(client, customer_headers):
#     orders = client.get("/api/orders", headers=customer_headers).json()
#     assert [o["product_name"] for o in orders] == ["무선 블루투스 이어폰 Pro", "데일리 코튼 니트 (베이지, M)",
#                                                    "스테인리스 텀블러 500ml"]


# def test_auto_answer_chat_with_order(client, customer_headers, monkeypatch):
#     seen = {}
#     from app.plugins import mock_ai
#     original = mock_ai.MockAIClient.answer

#     def spy(self, request):
#         seen.setdefault("requests", []).append(request)
#         return original(self, request)
#     monkeypatch.setattr(mock_ai.MockAIClient, "answer", spy)

#     d = _start(client, customer_headers, AUTO_Q, order_no=EARPHONE)
#     assert re.fullmatch(r"Q\d{8}-\d{3}", d["inquiry_no"])
#     assert d["product_name"] == "무선 블루투스 이어폰 Pro" and d["order_no"] == EARPHONE
#     assert [m["role"] for m in d["messages"]] == ["CUSTOMER", "BOT"]
#     assert "sources" not in d["messages"][1]                     # 관련 정책은 고객 화면에서 뺌
#     assert d["status_code"] == "CHATTING" and d["status_label"] == "상담 중"
#     assert d["input_locked"] is False and d["auto_close_at"] is not None
#     assert d["category"] == "배송"                               # 화면용 유형
#     assert seen["requests"][0].order.product_name == "무선 블루투스 이어폰 Pro"   # AI 서버에 고른 상품 전달

#     r = _send(client, customer_headers, d["inquiry_no"], "결제 수단은 카드만 되나요?")
#     assert r.status_code == 201, r.text
#     assert [h.role for h in seen["requests"][1].history] == ["customer", "assistant"]

#     bad = client.post("/api/conversations", json={"content": AUTO_Q, "order_no": "없는주문"}, headers=customer_headers)
#     assert bad.status_code == 404 and bad.json()["error"]["code"] == "ORDER_NOT_FOUND"


# def test_review_keeps_chat_open_and_admin_answer_joins_chat(client, customer_headers, admin_headers):
#     d = _start(client, customer_headers, REVIEW_Q)
#     no = d["inquiry_no"]
#     assert [m["role"] for m in d["messages"]] == ["CUSTOMER", "BOT"]
#     assert d["messages"][1]["is_notice"] is True and "담당자에게 전달" in d["messages"][1]["content"]
#     assert d["input_locked"] is False and d["review_pending"] is True       # 검토 중에도 입력 가능
#     assert d["status_code"] == "CHATTING" and d["status_label"] == "상담 중"  # 고객 화면엔 검토 상태 없음
#     assert d["auto_close_at"] is None and d["product_name"] is None         # 검토 대기 중엔 5분 규칙 없음

#     r = _send(client, customer_headers, no, AUTO_Q)                         # 검토 중에 이어서 질문
#     assert r.status_code == 201, r.text
#     assert [m["role"] for m in r.json()["messages"]] == ["CUSTOMER", "BOT", "CUSTOMER", "BOT"]
#     assert r.json()["auto_close_at"] is None                                # 아직 검토 대기 질문이 있음

#     detail = client.get(f"/api/admin/reviews/{no}", headers=admin_headers).json()
#     assert detail["status_code"] == "REVIEWING" and detail["status_label"] == "검토대기"   # 관리자 화면엔 검토대기
#     assert detail["customer"]["email"] == "demo@example.invalid" and detail["customer"]["phone"] == "010-1234-5678"
#     rq = detail["review_question"]
#     assert rq["needs_review"] and rq["content"] == REVIEW_Q and rq["analysis"]["intent_confidence"] == 0.4

#     r = _approve(client, admin_headers, no, response_text="안녕하세요, 어떤 점이 궁금하신가요?")
#     assert r.status_code == 200, r.text
#     assert r.json()["chat_status"] == "OPEN" and r.json()["status_code"] == "CHATTING"

#     d = client.get(f"/api/conversations/{no}", headers=customer_headers).json()
#     assert d["messages"][-1]["role"] == "ADMIN" and d["messages"][-1]["question_id"] == d["messages"][0]["question_id"]
#     assert d["status"] == "OPEN" and d["input_locked"] is False and d["auto_close_at"] is not None   # 계속 채팅, 타이머 재시작
#     assert d["admin_answer"] == "안녕하세요, 어떤 점이 궁금하신가요?"
#     assert _send(client, customer_headers, no, "감사합니다 배송 기간 며칠인가요?").status_code == 201
#     assert _approve(client, admin_headers, no).status_code == 409    # 이미 처리함 (review_question = 처리한 질문)

#     d = client.post(f"/api/conversations/{no}/close", headers=customer_headers).json()
#     assert d["status_label"] == "답변완료" and d["answered_by"] == "ADMIN" and d["answered_by_label"] == "관리자 답변"


# def test_questions_answered_in_order(client, customer_headers):
#     from app.services.pipeline import process_question

#     no = _start(client, customer_headers, AUTO_Q)["inquiry_no"]
#     with SessionLocal() as db:   # 앞 답변을 기다리지 않고 두 질문을 연달아 보낸 상태를 만듦
#         conv = _conv(db, no)
#         for turn, text in enumerate(("결제 수단은 뭐가 있나요?", "배송지 변경하고 싶어요"), start=2):
#             db.add(Inquiry(inquiry_no=f"{no}-{turn:02d}", conversation_id=conv.id, customer_id=conv.customer_id,
#                            content=text))
#             db.flush()
#         second_id = db.scalar(select(Inquiry.id).where(Inquiry.conversation_id == conv.id).order_by(Inquiry.id.desc()))
#         db.commit()
#     process_question(second_id)                       # 뒤 질문으로 불러도 앞 질문부터 처리
#     msgs = client.get(f"/api/conversations/{no}", headers=customer_headers).json()["messages"]
#     questions = [m["question_id"] for m in msgs if m["role"] == "CUSTOMER"]
#     answers = [m["question_id"] for m in msgs if m["role"] == "BOT"]
#     assert answers == questions                       # 보낸 순서대로 답변
#     assert len({m["id"] for m in msgs}) == len(msgs)  # 화면 목록 키는 겹치지 않음


# def test_new_chat_and_close_button(client, customer_headers):
#     a = _start(client, customer_headers, AUTO_Q)
#     b = _start(client, customer_headers, AUTO_Q)
#     old = client.get(f"/api/conversations/{a['inquiry_no']}", headers=customer_headers).json()
#     assert old["status"] == "CLOSED" and old["close_reason_label"] == "새 채팅 시작" and len(old["messages"]) == 2
#     assert old["answered_by"] == "AI" and old["admin_answer"] is None
#     assert client.get("/api/conversations/current", headers=customer_headers).json()["inquiry_no"] == b["inquiry_no"]

#     r = client.post(f"/api/conversations/{b['inquiry_no']}/close", headers=customer_headers)
#     assert r.json()["close_reason_label"] == "고객 종료" and r.json()["input_locked"] is True
#     r = _send(client, customer_headers, b["inquiry_no"], "추가 질문")
#     assert r.status_code == 409 and r.json()["error"]["code"] == "CONVERSATION_CLOSED"
#     assert client.get("/api/conversations/current", headers=customer_headers).json() is None


# def test_idle_timeout_only_while_chatting_with_bot(client, customer_headers, monkeypatch):
#     started = _start(client, customer_headers, AUTO_Q)
#     chatting, deadline = started["inquiry_no"], started["auto_close_at"]
#     assert deadline is not None
#     _later(monkeypatch, 6)
#     d = client.get(f"/api/conversations/{chatting}", headers=customer_headers).json()
#     assert d["status"] == "CLOSED" and d["close_reason"] == "TIMEOUT" and d["closed_at"] == deadline
#     monkeypatch.undo()

#     reviewing = _start(client, customer_headers, REVIEW_Q)["inquiry_no"]
#     with SessionLocal() as db:
#         q = _conv(db, reviewing).inquiries[0]
#         q.created_at = q.created_at - timedelta(hours=2)                       # 2시간째 검토 대기
#         db.commit()
#     _send(client, customer_headers, reviewing, AUTO_Q)                        # 챗봇 답변이 붙어도
#     _later(monkeypatch, 30)
#     d = client.get(f"/api/conversations/{reviewing}", headers=customer_headers).json()
#     assert d["status"] == "OPEN" and d["review_pending"] is True              # 검토 대기 중이라 자동 종료 안 됨


# def test_closed_while_reviewing_still_gets_admin_answer(client, customer_headers, admin_headers):
#     no = _start(client, customer_headers, REVIEW_Q)["inquiry_no"]
#     client.post(f"/api/conversations/{no}/close", headers=customer_headers)
#     assert client.get(f"/api/conversations/{no}", headers=customer_headers).json()["status_code"] == "CHATTING"
#     assert _approve(client, admin_headers, no).status_code == 200
#     d = client.get(f"/api/conversations/{no}", headers=customer_headers).json()
#     assert d["close_reason"] == "USER" and d["status_code"] == "ANSWERED" and d["admin_answer"]


# def test_my_list_tabs_search_and_ownership(client, customer_headers):
#     _start(client, customer_headers, "텀블러 환불 신청은 어디서 하나요? 환불 받고 싶어요", order_no="20241205-3456789")
#     all_items = client.get("/api/conversations", headers=customer_headers, params={"size": 100}).json()
#     assert all_items["page"]["total"] == len(all_items["items"])
#     assert {i["status_code"] for i in all_items["items"]} == {"CHATTING", "ANSWERED"}   # 고객 화면 상태는 2개
#     created = [i["created_at"] for i in all_items["items"]]
#     assert created == sorted(created, reverse=True)
#     assert client.get("/api/conversations", headers=customer_headers, params={"status": "REVIEWING"}).status_code == 422
#     for code in ("CHATTING", "ANSWERED"):
#         items = client.get("/api/conversations", headers=customer_headers, params={"status": code}).json()["items"]
#         assert items and all(i["status_code"] == code for i in items)

#     by_product = client.get("/api/conversations", headers=customer_headers, params={"q": "텀블러"}).json()["items"]
#     assert by_product and by_product[0]["product_name"] == "스테인리스 텀블러 500ml"

#     with SessionLocal() as db:
#         other = Customer(name="다른고객", phone="01099998888")
#         db.add(other)
#         db.commit()
#         token, _ = create_access_token("customer", other.id)
#     r = client.get(f"/api/conversations/{all_items['items'][0]['inquiry_no']}",
#                    headers={"Authorization": f"Bearer {token}"})
#     assert r.status_code == 404


# def test_ai_error_goes_to_review(client, customer_headers, admin_headers, monkeypatch):
#     class Broken:
#         def answer(self, request):
#             raise RuntimeError("model server down")
#     monkeypatch.setattr("app.services.pipeline.get_ai_client", lambda: Broken())
#     no = _start(client, customer_headers, "결제가 두 번 됐어요")["inquiry_no"]
#     rq = client.get(f"/api/admin/reviews/{no}", headers=admin_headers).json()["review_question"]
#     assert [x["code"] for x in rq["analysis"]["reasons"]] == ["AI_ERROR"] and rq["ai_draft"] is None
#     assert _approve(client, admin_headers, no).json()["error"]["code"] == "LABEL_REQUIRED"
#     assert _approve(client, admin_headers, no, category="결제", intent="결제 문제").status_code == 200


# # ───────── 관리자 ─────────

# def test_admin_tabs_filters_and_detail(client, admin_headers):
#     get = lambda **p: client.get("/api/admin/reviews", params=p, headers=admin_headers).json()
#     pending, all_, done = get(tab="pending"), get(tab="all", size=100), get(tab="done")
#     assert pending["counts"]["all"] == pending["counts"]["pending"] + pending["counts"]["done"]
#     assert all(i["status_code"] == "REVIEWING" for i in pending["items"])
#     assert all(i["status_code"] == "ANSWERED" for i in done["items"])
#     assert {i["status_code"] for i in all_["items"]} <= {"REVIEWING", "ANSWERED"}   # 상담 중은 끝난 뒤에 보임
#     created = [i["created_at"] for i in pending["items"]]
#     assert created == sorted(created, reverse=True)                                   # 최신순
#     item = all_["items"][0]
#     assert item["customer_email"] and item["customer_phone"]

#     refund = get(tab="all", category="교환/환불")["items"]
#     assert refund and all(i["category"] == "교환/환불" for i in refund)
#     assert client.get("/api/admin/reviews", params={"category": "환불"}, headers=admin_headers).status_code == 422
#     assert [i["product_name"] for i in get(tab="all", q="텀블러")["items"]] == ["스테인리스 텀블러 500ml"]

#     d = client.get(f"/api/admin/reviews/{item['inquiry_no']}", headers=admin_headers).json()
#     assert d["previous_inquiries"] and all(p["inquiry_no"] != item["inquiry_no"] for p in d["previous_inquiries"])


# def test_dashboard_all_time(client, admin_headers):
#     d = client.get("/api/admin/dashboard", headers=admin_headers).json()
#     assert d["total_inquiries"] == d["pending_review"] + d["answered"]["total"]
#     assert d["answered"]["total"] == d["answered"]["ai"] + d["answered"]["admin"]
#     assert d["ai_auto_rate"] == round(d["answered"]["ai"] / d["total_inquiries"], 4)
#     assert {c["name"] for c in d["by_category"]} <= {"배송", "결제", "교환/환불", "주문", "기타", "미분류"}
#     assert len(d["hourly"]) == 24 and d["peak_window"]["end_hour"] - d["peak_window"]["start_hour"] == 2
#     waits = [p["waiting_minutes"] for p in d["pending_list"]]
#     assert waits == sorted(waits, reverse=True) and waits[0] >= 120     # 오래된 순
#     assert d["chatting_now"] >= 0


# def test_policies_pdf(client, admin_headers):
#     names = [p["title"] for p in client.get("/api/admin/policies", headers=admin_headers).json()]
#     assert "배송 안내 (샘플)" in names

#     pdf = make_pdf("Delivery policy: shipped within 2 business days")
#     r = client.post("/api/admin/policies", headers=admin_headers,
#                     files={"file": ("delivery_policy.pdf", pdf, "application/pdf")},
#                     data={"title": "배송 정책 · 배송 기간 안내"})
#     assert r.status_code == 201, r.text
#     p = r.json()
#     assert p["title"] == "배송 정책 · 배송 기간 안내" and p["filename"] == "delivery_policy.pdf"
#     assert p["text_extracted"] is True and p["version"] == 1 and p["doc_key"] == "delivery_policy"
#     pid = p["id"]

#     dup = client.post("/api/admin/policies", headers=admin_headers, files={"file": ("delivery_policy.pdf", pdf, "application/pdf")})
#     assert dup.status_code == 409
#     bad = client.post("/api/admin/policies", headers=admin_headers, files={"file": ("x.docx", b"PK", "application/octet-stream")})
#     assert bad.status_code == 422 and bad.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"
#     untitled = client.post("/api/admin/policies", headers=admin_headers,
#                            files={"file": ("exchange_policy.pdf", make_pdf("Exchange"), "application/pdf")})
#     assert untitled.json()["title"] == "exchange_policy"               # 문서명 비우면 파일명

#     view = client.get(f"/api/admin/policies/{pid}/file", headers=admin_headers)
#     assert view.status_code == 200 and view.headers["content-type"] == "application/pdf"
#     assert view.headers["content-disposition"].startswith("inline") and view.content == pdf
#     dl = client.get(f"/api/admin/policies/{pid}/file", params={"download": True}, headers=admin_headers)
#     assert dl.headers["content-disposition"].startswith("attachment")

#     new_pdf = make_pdf("Delivery policy v2")
#     r = client.put(f"/api/admin/policies/{pid}/file", headers=admin_headers,
#                    files={"file": ("delivery_policy_v2.pdf", new_pdf, "application/pdf")})
#     v2 = r.json()
#     assert v2["version"] == 2 and v2["filename"] == "delivery_policy_v2.pdf"
#     assert v2["doc_key"] == "delivery_policy"                        # 파일을 바꿔도 문서 키는 그대로
#     assert v2["id"] != pid and v2["created_at"] == p["created_at"]   # 새 버전 행, 등록일은 처음 등록한 날
#     old_id_file = client.get(f"/api/admin/policies/{pid}/file", headers=admin_headers)
#     assert old_id_file.content == new_pdf                            # 이전 id로 불러도 사용 중인 버전
#     ids = [x["id"] for x in client.get("/api/admin/policies", headers=admin_headers).json()]
#     assert v2["id"] in ids and pid not in ids

#     assert client.delete(f"/api/admin/policies/{v2['id']}", headers=admin_headers).status_code == 204
#     assert client.get(f"/api/admin/policies/{pid}", headers=admin_headers).status_code == 404
#     assert v2["id"] not in [x["id"] for x in client.get("/api/admin/policies", headers=admin_headers).json()]
#     again = client.post("/api/admin/policies", headers=admin_headers,
#                         files={"file": ("delivery_policy.pdf", pdf, "application/pdf")})
#     assert again.status_code == 201 and again.json()["version"] == 3   # 삭제 뒤 다시 등록하면 다음 버전


# def test_training_export_and_meta(client, admin_headers, customer_headers):
#     no = _start(client, customer_headers, REVIEW_Q)["inquiry_no"]
#     _approve(client, admin_headers, no, intent="고객 서비스 문의", use_for_training=True)
#     text = client.get("/api/admin/training-data/export", headers=admin_headers).content.decode("utf-8-sig")
#     assert text.splitlines()[0] == "플래그,문의 내용,카테고리,의도,응답,source_file,inquiry_no,turn,reviewed_at"
#     assert no in text

#     m = client.get("/api/admin/meta", headers=admin_headers).json()
#     assert m["display_categories"] == ["배송", "결제", "교환/환불", "주문", "기타"]
#     assert m["display_category_map"]["취소"] == "주문" and m["display_category_map"]["문의"] == "기타"


# def test_seed_base_keeps_existing_team_accounts(capsys):
#     """팀 DB에 계정이 이미 있으면 seed_base는 아무것도 바꾸지 않음."""
#     from scripts import seed_base
#     with SessionLocal() as db:
#         before = db.scalar(select(func.count(Customer.id)))
#     seed_base.main()
#     with SessionLocal() as db:
#         assert db.scalar(select(func.count(Customer.id))) == before
#     assert "그대로 사용" in capsys.readouterr().out


# def test_removed_features(client, customer_headers, admin_headers):
#     assert client.get("/api/me", headers=customer_headers).status_code == 404               # 계정 정보 탭 삭제
#     assert client.post("/api/admin/policies/1/attachments", headers=admin_headers).status_code == 404
#     no = client.get("/api/conversations", headers=customer_headers).json()["items"][0]["inquiry_no"]
#     assert client.delete(f"/api/conversations/{no}", headers=customer_headers).status_code == 405   # 고객 삭제 없음


# # ───────── 팀 DB에 남는 기록 ─────────

# def test_team_db_records(client, customer_headers, admin_headers):
#     auto_no = _start(client, customer_headers, AUTO_Q)["inquiry_no"]
#     review_no = _start(client, customer_headers, REVIEW_Q)["inquiry_no"]
#     assert _approve(client, admin_headers, review_no, intent="고객 서비스 문의").status_code == 200
#     with SessionLocal() as db:
#         auto_q = _conv(db, auto_no).inquiries[0]
#         assert auto_q.inquiry_no == f"{auto_no}-01" and auto_q.status == "AUTO_ANSWERED"
#         sent = db.scalar(select(AIResponse).where(AIResponse.inquiry_id == auto_q.id))
#         assert sent.status == "SENT" and sent.sent_automatically and sent.final_response_text == sent.response_text
#         assert db.scalar(select(RetrievedPolicy).where(RetrievedPolicy.response_id == sent.id)) is not None  # 근거

#         review_q = _conv(db, review_no).inquiries[0]
#         assert review_q.status == "COMPLETED"                                    # 팀 DB 트리거와 같은 값
#         r = db.scalar(select(AIResponse).where(AIResponse.inquiry_id == review_q.id))
#         rv = db.scalar(select(AdminReview).where(AdminReview.response_id == r.id))
#         assert r.status == "SENT" and not r.sent_automatically and r.final_response_text == rv.modified_response
#         assert rv.original_response == r.response_text and rv.modified_intent == "고객 서비스 문의"


# def test_only_backend_chats_are_listed(client, customer_headers, admin_headers):
#     """팀 샘플·실습 스크립트로 만든 채팅(conversation_ext 없음)은 백엔드 화면에 나오지 않음."""
#     with SessionLocal() as db:
#         cid = db.scalar(select(Customer.id).where(Customer.email == "demo@example.invalid"))
#         conv = Conversation(customer_id=cid, title="실습")
#         db.add(conv)
#         db.flush()
#         db.add(Inquiry(inquiry_no="PRACTICE-001", conversation_id=conv.id, customer_id=cid, content="실습 문의"))
#         db.commit()
#     mine = client.get("/api/conversations", headers=customer_headers, params={"q": "실습 문의"}).json()
#     assert mine["items"] == []
#     found = client.get("/api/admin/reviews", headers=admin_headers, params={"tab": "all", "q": "실습 문의"}).json()
#     assert found["items"] == []


# # ───────── 자동답변 판단 규칙 ─────────

# def _result(**kw):
#     base = {"category": "배송", "intent": "배송 기간 확인", "category_confidence": 0.9, "intent_confidence": 0.9,
#             "answer": "배송은 보통 1~3일 걸립니다.", "sources": [AISource(title="배송", text="...", score=0.8)]}
#     base.update(kw)
#     return AIResult(**base)


# def test_decision_rules():
#     s = Settings(_env_file=None)
#     assert decide(_result(), None, s).auto_send
#     assert decide(_result(intent_confidence=0.8, category_confidence=0.8), None, s).auto_send
#     assert decide(_result(intent_confidence=0.79), None, s).reasons == [ReviewReason.LOW_CONFIDENCE]
#     assert decide(_result(intent_confidence=None, category_confidence=None), None, s).reasons == [ReviewReason.NO_CONFIDENCE]
#     assert decide(None, "boom", s).reasons == [ReviewReason.AI_ERROR]
#     assert decide(_result(category="환불", intent="환불받기", sources=[], answer="네."), None, s).auto_send



"""전체 흐름 테스트 — 실행: pytest -q

mock AI의 신뢰도: 키워드 0개 0.4 / 1개 0.8 / 2개 이상 0.95 → 기준 0.8 이상이면 자동답변
"""
import re
from datetime import timedelta

from sqlalchemy import func, select

from app.config import Settings
from app.db import SessionLocal
from app.enums import ReviewReason
from app.models import AdminReview, AIResponse, Conversation, Customer, Inquiry, RetrievedPolicy, utcnow
from app.plugins.ai_client import AIResult, AISource
from app.security import create_access_token
from app.services.decision import decide
from tests.conftest import make_pdf

AUTO_Q = "배송 기간이 며칠이나 걸리나요?"            # 키워드 2개 → 0.95 → 자동답변
REVIEW_Q = "안녕하세요 그냥 여쭤볼 게 있어서요"       # 키워드 0개 → 0.4 → 검토
EARPHONE = "20241210-1234567"                          # 체험 고객의 테스트용 주문 (conftest.TEST_ORDERS)


def _conv(db, no) -> Conversation:
    """채팅 문의번호로 찾기 (첫 질문의 inquiry_no = 문의번호-01)."""
    return db.scalar(select(Conversation).join(Inquiry, Inquiry.conversation_id == Conversation.id)
                     .where(Inquiry.inquiry_no == f"{no}-01"))


def _later(monkeypatch, minutes: int) -> None:
    """서버 시계를 앞으로 돌림 (전송된 답변 시각은 팀 DB 규칙상 바꿀 수 없어서)."""
    t = utcnow() + timedelta(minutes=minutes)
    monkeypatch.setattr("app.services.conversations.utcnow", lambda: t)


def _start(client, headers, content, order_no=None, wait=True):
    body = {"content": content, **({"order_no": order_no} if order_no else {})}
    r = client.post("/api/conversations", params={"wait": wait}, json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _send(client, headers, no, content, wait=True):
    return client.post(f"/api/conversations/{no}/messages", params={"wait": wait}, json={"content": content},
                       headers=headers)


def _approve(client, admin_headers, no, **body):
    qid = client.get(f"/api/admin/reviews/{no}", headers=admin_headers).json()["review_question"]["question_id"]
    return client.post(f"/api/admin/reviews/{no}/questions/{qid}/approve", headers=admin_headers,
                       json={"response_text": "담당자 답변입니다.", **body})


# ───────── 체험 입장 / 권한 ─────────

def test_demo_entry_and_roles(client, customer_headers, admin_headers):
    r = client.post("/api/demo/customer")
    assert r.json()["role"] == "customer" and r.json()["name"] == "테스트고객"
    assert client.post("/api/demo/admin").json()["role"] == "admin"
    assert client.get("/api/orders").status_code == 401
    assert client.get("/api/admin/reviews", headers=customer_headers).status_code == 401
    assert client.get("/api/orders", headers=admin_headers).status_code == 401


# ───────── 고객: 주문 상품 / 채팅 ─────────

def test_orders_for_dropdown(client, customer_headers):
    orders = client.get("/api/orders", headers=customer_headers).json()
    assert [o["product_name"] for o in orders] == ["무선 블루투스 이어폰 Pro", "데일리 코튼 니트 (베이지, M)",
                                                   "스테인리스 텀블러 500ml"]


def test_auto_answer_chat_with_order(client, customer_headers, monkeypatch):
    seen = {}
    from app.plugins import mock_ai
    original = mock_ai.MockAIClient.answer

    def spy(self, request):
        seen.setdefault("requests", []).append(request)
        return original(self, request)
    monkeypatch.setattr(mock_ai.MockAIClient, "answer", spy)

    d = _start(client, customer_headers, AUTO_Q, order_no=EARPHONE)
    assert re.fullmatch(r"Q\d{8}-\d{3}", d["inquiry_no"])
    assert d["product_name"] == "무선 블루투스 이어폰 Pro" and d["order_no"] == EARPHONE
    assert [m["role"] for m in d["messages"]] == ["CUSTOMER", "BOT"]
    assert "sources" not in d["messages"][1]                     # 관련 정책은 고객 화면에서 뺌
    assert d["status_code"] == "CHATTING" and d["status_label"] == "상담 중"
    assert d["input_locked"] is False and d["auto_close_at"] is not None
    assert d["category"] == "배송"                               # 화면용 유형
    assert seen["requests"][0].order.product_name == "무선 블루투스 이어폰 Pro"   # AI 서버에 고른 상품 전달

    r = _send(client, customer_headers, d["inquiry_no"], "결제 수단은 카드만 되나요?")
    assert r.status_code == 201, r.text
    assert [h.role for h in seen["requests"][1].history] == ["customer", "assistant"]

    bad = client.post("/api/conversations", json={"content": AUTO_Q, "order_no": "없는주문"}, headers=customer_headers)
    assert bad.status_code == 404 and bad.json()["error"]["code"] == "ORDER_NOT_FOUND"


def test_review_keeps_chat_open_and_admin_answer_joins_chat(client, customer_headers, admin_headers):
    d = _start(client, customer_headers, REVIEW_Q)
    no = d["inquiry_no"]
    assert [m["role"] for m in d["messages"]] == ["CUSTOMER", "BOT"]
    assert d["messages"][1]["is_notice"] is True and "담당자에게 전달" in d["messages"][1]["content"]
    assert d["input_locked"] is False and d["review_pending"] is True       # 검토 중에도 입력 가능
    assert d["status_code"] == "CHATTING" and d["status_label"] == "상담 중"  # 고객 화면엔 검토 상태 없음
    assert d["auto_close_at"] is None and d["product_name"] is None         # 검토 대기 중엔 5분 규칙 없음

    r = _send(client, customer_headers, no, AUTO_Q)                         # 검토 중에 이어서 질문
    assert r.status_code == 201, r.text
    assert [m["role"] for m in r.json()["messages"]] == ["CUSTOMER", "BOT", "CUSTOMER", "BOT"]
    assert r.json()["auto_close_at"] is None                                # 아직 검토 대기 질문이 있음

    detail = client.get(f"/api/admin/reviews/{no}", headers=admin_headers).json()
    assert detail["status_code"] == "REVIEWING" and detail["status_label"] == "검토대기"   # 관리자 화면엔 검토대기
    assert detail["customer"]["email"] == "demo@example.invalid" and detail["customer"]["phone"] == "010-1234-5678"
    rq = detail["review_question"]
    assert rq["needs_review"] and rq["content"] == REVIEW_Q and rq["analysis"]["intent_confidence"] == 0.4

    r = _approve(client, admin_headers, no, response_text="안녕하세요, 어떤 점이 궁금하신가요?")
    assert r.status_code == 200, r.text
    assert r.json()["chat_status"] == "OPEN" and r.json()["status_code"] == "ANSWERED"   # 관리자가 답하면 답변완료 (채팅은 계속)

    d = client.get(f"/api/conversations/{no}", headers=customer_headers).json()
    assert d["messages"][-1]["role"] == "ADMIN" and d["messages"][-1]["question_id"] == d["messages"][0]["question_id"]
    assert d["status"] == "OPEN" and d["input_locked"] is False and d["auto_close_at"] is not None   # 계속 채팅, 타이머 재시작
    assert d["admin_answer"] == "안녕하세요, 어떤 점이 궁금하신가요?"
    assert _send(client, customer_headers, no, "감사합니다 배송 기간 며칠인가요?").status_code == 201
    assert _approve(client, admin_headers, no).status_code == 409    # 이미 처리함 (review_question = 처리한 질문)

    d = client.post(f"/api/conversations/{no}/close", headers=customer_headers).json()
    assert d["status_label"] == "답변완료" and d["answered_by"] == "ADMIN" and d["answered_by_label"] == "관리자 답변"


def test_questions_answered_in_order(client, customer_headers):
    from app.services.pipeline import process_question

    no = _start(client, customer_headers, AUTO_Q)["inquiry_no"]
    with SessionLocal() as db:   # 앞 답변을 기다리지 않고 두 질문을 연달아 보낸 상태를 만듦
        conv = _conv(db, no)
        for turn, text in enumerate(("결제 수단은 뭐가 있나요?", "배송지 변경하고 싶어요"), start=2):
            db.add(Inquiry(inquiry_no=f"{no}-{turn:02d}", conversation_id=conv.id, customer_id=conv.customer_id,
                           content=text))
            db.flush()
        second_id = db.scalar(select(Inquiry.id).where(Inquiry.conversation_id == conv.id).order_by(Inquiry.id.desc()))
        db.commit()
    process_question(second_id)                       # 뒤 질문으로 불러도 앞 질문부터 처리
    msgs = client.get(f"/api/conversations/{no}", headers=customer_headers).json()["messages"]
    questions = [m["question_id"] for m in msgs if m["role"] == "CUSTOMER"]
    answers = [m["question_id"] for m in msgs if m["role"] == "BOT"]
    assert answers == questions                       # 보낸 순서대로 답변
    assert len({m["id"] for m in msgs}) == len(msgs)  # 화면 목록 키는 겹치지 않음


def test_new_chat_and_close_button(client, customer_headers):
    a = _start(client, customer_headers, AUTO_Q)
    b = _start(client, customer_headers, AUTO_Q)
    old = client.get(f"/api/conversations/{a['inquiry_no']}", headers=customer_headers).json()
    assert old["status"] == "CLOSED" and old["close_reason_label"] == "새 채팅 시작" and len(old["messages"]) == 2
    assert old["answered_by"] == "AI" and old["admin_answer"] is None
    assert client.get("/api/conversations/current", headers=customer_headers).json()["inquiry_no"] == b["inquiry_no"]

    r = client.post(f"/api/conversations/{b['inquiry_no']}/close", headers=customer_headers)
    assert r.json()["close_reason_label"] == "고객 종료" and r.json()["input_locked"] is True
    r = _send(client, customer_headers, b["inquiry_no"], "추가 질문")
    assert r.status_code == 409 and r.json()["error"]["code"] == "CONVERSATION_CLOSED"
    assert client.get("/api/conversations/current", headers=customer_headers).json() is None


def test_idle_timeout_only_while_chatting_with_bot(client, customer_headers, monkeypatch):
    started = _start(client, customer_headers, AUTO_Q)
    chatting, deadline = started["inquiry_no"], started["auto_close_at"]
    assert deadline is not None
    _later(monkeypatch, 6)
    d = client.get(f"/api/conversations/{chatting}", headers=customer_headers).json()
    assert d["status"] == "CLOSED" and d["close_reason"] == "TIMEOUT" and d["closed_at"] == deadline
    monkeypatch.undo()

    reviewing = _start(client, customer_headers, REVIEW_Q)["inquiry_no"]
    with SessionLocal() as db:
        q = _conv(db, reviewing).inquiries[0]
        q.created_at = q.created_at - timedelta(hours=2)                       # 2시간째 검토 대기
        db.commit()
    _send(client, customer_headers, reviewing, AUTO_Q)                        # 챗봇 답변이 붙어도
    _later(monkeypatch, 30)
    d = client.get(f"/api/conversations/{reviewing}", headers=customer_headers).json()
    assert d["status"] == "OPEN" and d["review_pending"] is True              # 검토 대기 중이라 자동 종료 안 됨


def test_closed_while_reviewing_still_gets_admin_answer(client, customer_headers, admin_headers):
    no = _start(client, customer_headers, REVIEW_Q)["inquiry_no"]
    client.post(f"/api/conversations/{no}/close", headers=customer_headers)
    assert client.get(f"/api/conversations/{no}", headers=customer_headers).json()["status_code"] == "ANSWERED"  # 고객: 종료=답변완료
    assert client.get(f"/api/admin/reviews/{no}", headers=admin_headers).json()["status_code"] == "REVIEWING"   # 관리자: 검토 유지
    assert _approve(client, admin_headers, no).status_code == 200
    d = client.get(f"/api/conversations/{no}", headers=customer_headers).json()
    assert d["close_reason"] == "USER" and d["status_code"] == "ANSWERED" and d["admin_answer"]


def test_my_list_tabs_search_and_ownership(client, customer_headers):
    _start(client, customer_headers, "텀블러 환불 신청은 어디서 하나요? 환불 받고 싶어요", order_no="20241205-3456789")
    all_items = client.get("/api/conversations", headers=customer_headers, params={"size": 100}).json()
    assert all_items["page"]["total"] == len(all_items["items"])
    assert {i["status_code"] for i in all_items["items"]} == {"CHATTING", "ANSWERED"}   # 고객 화면 상태는 2개
    created = [i["created_at"] for i in all_items["items"]]
    assert created == sorted(created, reverse=True)
    assert client.get("/api/conversations", headers=customer_headers, params={"status": "REVIEWING"}).status_code == 422
    for code in ("CHATTING", "ANSWERED"):
        items = client.get("/api/conversations", headers=customer_headers, params={"status": code}).json()["items"]
        assert items and all(i["status_code"] == code for i in items)

    by_product = client.get("/api/conversations", headers=customer_headers, params={"q": "텀블러"}).json()["items"]
    assert by_product and by_product[0]["product_name"] == "스테인리스 텀블러 500ml"

    with SessionLocal() as db:
        other = Customer(name="다른고객", phone="01099998888")
        db.add(other)
        db.commit()
        token, _ = create_access_token("customer", other.id)
    r = client.get(f"/api/conversations/{all_items['items'][0]['inquiry_no']}",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404


def test_ai_error_goes_to_review(client, customer_headers, admin_headers, monkeypatch):
    class Broken:
        def answer(self, request):
            raise RuntimeError("model server down")
    monkeypatch.setattr("app.services.pipeline.get_ai_client", lambda: Broken())
    no = _start(client, customer_headers, "결제가 두 번 됐어요")["inquiry_no"]
    rq = client.get(f"/api/admin/reviews/{no}", headers=admin_headers).json()["review_question"]
    assert [x["code"] for x in rq["analysis"]["reasons"]] == ["AI_ERROR"] and rq["ai_draft"] is None
    assert _approve(client, admin_headers, no).json()["error"]["code"] == "LABEL_REQUIRED"
    assert _approve(client, admin_headers, no, category="결제", intent="결제 문제").status_code == 200


# ───────── 관리자 ─────────

def test_admin_tabs_filters_and_detail(client, admin_headers):
    get = lambda **p: client.get("/api/admin/reviews", params=p, headers=admin_headers).json()
    pending, all_, done = get(tab="pending"), get(tab="all", size=100), get(tab="done")
    assert pending["counts"]["all"] == pending["counts"]["pending"] + pending["counts"]["done"]
    assert all(i["status_code"] == "REVIEWING" for i in pending["items"])
    assert all(i["status_code"] == "ANSWERED" for i in done["items"])
    assert {i["status_code"] for i in all_["items"]} <= {"REVIEWING", "ANSWERED"}   # 상담 중은 끝난 뒤에 보임
    created = [i["created_at"] for i in pending["items"]]
    assert created == sorted(created, reverse=True)                                   # 최신순
    item = all_["items"][0]
    assert item["customer_email"] and item["customer_phone"]

    refund = get(tab="all", category="교환/환불")["items"]
    assert refund and all(i["category"] == "교환/환불" for i in refund)
    assert client.get("/api/admin/reviews", params={"category": "환불"}, headers=admin_headers).status_code == 422
    assert [i["product_name"] for i in get(tab="all", q="텀블러")["items"]] == ["스테인리스 텀블러 500ml"]

    d = client.get(f"/api/admin/reviews/{item['inquiry_no']}", headers=admin_headers).json()
    assert d["previous_inquiries"] and all(p["inquiry_no"] != item["inquiry_no"] for p in d["previous_inquiries"])


def test_dashboard_all_time(client, admin_headers):
    d = client.get("/api/admin/dashboard", headers=admin_headers).json()
    assert d["total_inquiries"] == d["pending_review"] + d["answered"]["total"]
    assert d["answered"]["total"] == d["answered"]["ai"] + d["answered"]["admin"]
    assert d["ai_auto_rate"] == round(d["answered"]["ai"] / d["total_inquiries"], 4)
    assert {c["name"] for c in d["by_category"]} <= {"배송", "결제", "교환/환불", "주문", "기타", "미분류"}
    assert len(d["hourly"]) == 24 and d["peak_window"]["end_hour"] - d["peak_window"]["start_hour"] == 2
    waits = [p["waiting_minutes"] for p in d["pending_list"]]
    assert waits == sorted(waits, reverse=True) and waits[0] >= 120     # 오래된 순
    assert d["chatting_now"] >= 0


def test_policies_pdf(client, admin_headers):
    names = [p["title"] for p in client.get("/api/admin/policies", headers=admin_headers).json()]
    assert "배송 안내 (샘플)" in names

    pdf = make_pdf("Delivery policy: shipped within 2 business days")
    r = client.post("/api/admin/policies", headers=admin_headers,
                    files={"file": ("delivery_policy.pdf", pdf, "application/pdf")},
                    data={"title": "배송 정책 · 배송 기간 안내"})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["title"] == "배송 정책 · 배송 기간 안내" and p["filename"] == "delivery_policy.pdf"
    assert p["text_extracted"] is True and p["version"] == 1 and p["doc_key"] == "delivery_policy"
    pid = p["id"]

    dup = client.post("/api/admin/policies", headers=admin_headers, files={"file": ("delivery_policy.pdf", pdf, "application/pdf")})
    assert dup.status_code == 409
    bad = client.post("/api/admin/policies", headers=admin_headers, files={"file": ("x.docx", b"PK", "application/octet-stream")})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"
    untitled = client.post("/api/admin/policies", headers=admin_headers,
                           files={"file": ("exchange_policy.pdf", make_pdf("Exchange"), "application/pdf")})
    assert untitled.json()["title"] == "exchange_policy"               # 문서명 비우면 파일명

    view = client.get(f"/api/admin/policies/{pid}/file", headers=admin_headers)
    assert view.status_code == 200 and view.headers["content-type"] == "application/pdf"
    assert view.headers["content-disposition"].startswith("inline") and view.content == pdf
    dl = client.get(f"/api/admin/policies/{pid}/file", params={"download": True}, headers=admin_headers)
    assert dl.headers["content-disposition"].startswith("attachment")

    new_pdf = make_pdf("Delivery policy v2")
    r = client.put(f"/api/admin/policies/{pid}/file", headers=admin_headers,
                   files={"file": ("delivery_policy_v2.pdf", new_pdf, "application/pdf")})
    v2 = r.json()
    assert v2["version"] == 2 and v2["filename"] == "delivery_policy_v2.pdf"
    assert v2["doc_key"] == "delivery_policy"                        # 파일을 바꿔도 문서 키는 그대로
    assert v2["id"] != pid and v2["created_at"] == p["created_at"]   # 새 버전 행, 등록일은 처음 등록한 날
    old_id_file = client.get(f"/api/admin/policies/{pid}/file", headers=admin_headers)
    assert old_id_file.content == new_pdf                            # 이전 id로 불러도 사용 중인 버전
    ids = [x["id"] for x in client.get("/api/admin/policies", headers=admin_headers).json()]
    assert v2["id"] in ids and pid not in ids

    assert client.delete(f"/api/admin/policies/{v2['id']}", headers=admin_headers).status_code == 204
    assert client.get(f"/api/admin/policies/{pid}", headers=admin_headers).status_code == 404
    assert v2["id"] not in [x["id"] for x in client.get("/api/admin/policies", headers=admin_headers).json()]
    again = client.post("/api/admin/policies", headers=admin_headers,
                        files={"file": ("delivery_policy.pdf", pdf, "application/pdf")})
    assert again.status_code == 201 and again.json()["version"] == 3   # 삭제 뒤 다시 등록하면 다음 버전


def test_training_export_and_meta(client, admin_headers, customer_headers):
    no = _start(client, customer_headers, REVIEW_Q)["inquiry_no"]
    _approve(client, admin_headers, no, intent="고객 서비스 문의", use_for_training=True)
    text = client.get("/api/admin/training-data/export", headers=admin_headers).content.decode("utf-8-sig")
    assert text.splitlines()[0] == "플래그,문의 내용,카테고리,의도,응답,source_file,inquiry_no,turn,reviewed_at"
    assert no in text

    m = client.get("/api/admin/meta", headers=admin_headers).json()
    assert m["display_categories"] == ["배송", "결제", "교환/환불", "주문", "기타"]
    assert m["display_category_map"]["취소"] == "주문" and m["display_category_map"]["문의"] == "기타"


def test_seed_base_keeps_existing_team_accounts(capsys):
    """팀 DB에 계정이 이미 있으면 seed_base는 아무것도 바꾸지 않음."""
    from scripts import seed_base
    with SessionLocal() as db:
        before = db.scalar(select(func.count(Customer.id)))
    seed_base.main()
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Customer.id))) == before
    assert "그대로 사용" in capsys.readouterr().out


def test_removed_features(client, customer_headers, admin_headers):
    assert client.get("/api/me", headers=customer_headers).status_code == 404               # 계정 정보 탭 삭제
    assert client.post("/api/admin/policies/1/attachments", headers=admin_headers).status_code == 404
    no = client.get("/api/conversations", headers=customer_headers).json()["items"][0]["inquiry_no"]
    assert client.delete(f"/api/conversations/{no}", headers=customer_headers).status_code == 405   # 고객 삭제 없음


# ───────── 팀 DB에 남는 기록 ─────────

def test_team_db_records(client, customer_headers, admin_headers):
    auto_no = _start(client, customer_headers, AUTO_Q)["inquiry_no"]
    review_no = _start(client, customer_headers, REVIEW_Q)["inquiry_no"]
    assert _approve(client, admin_headers, review_no, intent="고객 서비스 문의").status_code == 200
    with SessionLocal() as db:
        auto_q = _conv(db, auto_no).inquiries[0]
        assert auto_q.inquiry_no == f"{auto_no}-01" and auto_q.status == "AUTO_ANSWERED"
        sent = db.scalar(select(AIResponse).where(AIResponse.inquiry_id == auto_q.id))
        assert sent.status == "SENT" and sent.sent_automatically and sent.final_response_text == sent.response_text
        assert db.scalar(select(RetrievedPolicy).where(RetrievedPolicy.response_id == sent.id)) is not None  # 근거

        review_q = _conv(db, review_no).inquiries[0]
        assert review_q.status == "COMPLETED"                                    # 팀 DB 트리거와 같은 값
        r = db.scalar(select(AIResponse).where(AIResponse.inquiry_id == review_q.id))
        rv = db.scalar(select(AdminReview).where(AdminReview.response_id == r.id))
        assert r.status == "SENT" and not r.sent_automatically and r.final_response_text == rv.modified_response
        assert rv.original_response == r.response_text and rv.modified_intent == "고객 서비스 문의"


def test_only_backend_chats_are_listed(client, customer_headers, admin_headers):
    """팀 샘플·실습 스크립트로 만든 채팅(conversation_ext 없음)은 백엔드 화면에 나오지 않음."""
    with SessionLocal() as db:
        cid = db.scalar(select(Customer.id).where(Customer.email == "demo@example.invalid"))
        conv = Conversation(customer_id=cid, title="실습")
        db.add(conv)
        db.flush()
        db.add(Inquiry(inquiry_no="PRACTICE-001", conversation_id=conv.id, customer_id=cid, content="실습 문의"))
        db.commit()
    mine = client.get("/api/conversations", headers=customer_headers, params={"q": "실습 문의"}).json()
    assert mine["items"] == []
    found = client.get("/api/admin/reviews", headers=admin_headers, params={"tab": "all", "q": "실습 문의"}).json()
    assert found["items"] == []


# ───────── 자동답변 판단 규칙 ─────────

def _result(**kw):
    base = {"category": "배송", "intent": "배송 기간 확인", "category_confidence": 0.9, "intent_confidence": 0.9,
            "answer": "배송은 보통 1~3일 걸립니다.", "sources": [AISource(title="배송", text="...", score=0.8)]}
    base.update(kw)
    return AIResult(**base)


def test_decision_rules():
    s = Settings(_env_file=None)
    assert decide(_result(), None, s).auto_send
    assert decide(_result(intent_confidence=0.8, category_confidence=0.8), None, s).auto_send
    assert decide(_result(intent_confidence=0.79), None, s).reasons == [ReviewReason.LOW_CONFIDENCE]
    assert decide(_result(intent_confidence=None, category_confidence=None), None, s).reasons == [ReviewReason.NO_CONFIDENCE]
    assert decide(None, "boom", s).reasons == [ReviewReason.AI_ERROR]
    assert decide(_result(category="환불", intent="환불받기", sources=[], answer="네."), None, s).auto_send


# ───────── 문의 상태 규칙 / 답변 수정 / 목록 ─────────

def _tab(client, admin_headers, tab) -> list[str]:
    r = client.get("/api/admin/reviews", headers=admin_headers, params={"tab": tab, "size": 100})
    return [i["inquiry_no"] for i in r.json()["items"]]


def test_admin_status_follows_admin_answer_and_new_reviews(client, customer_headers, admin_headers):
    """관리자가 답하면 답변완료 → 고객이 다시 물어 검토로 넘어가면 검토대기 → 다시 답하면 답변완료."""
    no = _start(client, customer_headers, REVIEW_Q)["inquiry_no"]
    assert no in _tab(client, admin_headers, "pending")
    _approve(client, admin_headers, no)
    assert no in _tab(client, admin_headers, "done") and no not in _tab(client, admin_headers, "pending")
    assert client.get(f"/api/conversations/{no}", headers=customer_headers).json()["status_code"] == "CHATTING"

    _send(client, customer_headers, no, REVIEW_Q)
    assert client.get(f"/api/admin/reviews/{no}", headers=admin_headers).json()["status_code"] == "REVIEWING"
    _approve(client, admin_headers, no, response_text="두 번째 답변입니다.")
    assert client.get(f"/api/admin/reviews/{no}", headers=admin_headers).json()["status_code"] == "ANSWERED"


def test_ai_only_chat_appears_in_done_after_timeout(client, customer_headers, admin_headers, monkeypatch):
    no = _start(client, customer_headers, AUTO_Q)["inquiry_no"]
    assert no not in _tab(client, admin_headers, "all")          # AI와만 대화 중이면 관리자 목록에 없음
    _later(monkeypatch, 6)
    assert no in _tab(client, admin_headers, "done")              # 목록을 여는 순간 5분 자동 종료 → 답변완료
    monkeypatch.undo()
    d = client.get(f"/api/conversations/{no}", headers=customer_headers).json()
    assert d["close_reason"] == "TIMEOUT" and d["status_code"] == "ANSWERED"


def test_edit_admin_answer(client, customer_headers, admin_headers):
    no = _start(client, customer_headers, REVIEW_Q)["inquiry_no"]
    qid = _approve(client, admin_headers, no).json()["review_question"]["question_id"]
    r = client.put(f"/api/admin/reviews/{no}/questions/{qid}/answer", headers=admin_headers,
                   json={"response_text": "  고친 답변입니다.  "})
    assert r.status_code == 200, r.text
    assert next(q for q in r.json()["questions"] if q["question_id"] == qid)["answer_text"] == "고친 답변입니다."

    d = client.get(f"/api/conversations/{no}", headers=customer_headers).json()
    admin_msg = [m for m in d["messages"] if m["role"] == "ADMIN"][-1]
    assert admin_msg["content"] == "고친 답변입니다." and admin_msg["edited_at"] and admin_msg["edited_by"]
    assert d["admin_answer"] == "고친 답변입니다."

    auto = _start(client, customer_headers, AUTO_Q)
    auto_qid = auto["messages"][0]["question_id"]
    r = client.put(f"/api/admin/reviews/{auto['inquiry_no']}/questions/{auto_qid}/answer", headers=admin_headers,
                   json={"response_text": "AI 답변은 못 고침"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_ADMIN_ANSWER"
    r = client.put(f"/api/admin/reviews/{no}/questions/{qid}/answer", headers=admin_headers, json={"response_text": " "})
    assert r.status_code == 422


def test_my_list_counts_and_category(client, customer_headers):
    _start(client, customer_headers, AUTO_Q)
    r = client.get("/api/conversations", headers=customer_headers, params={"size": 100}).json()
    counts = r["counts"]
    assert counts["all"] == r["page"]["total"] == counts["CHATTING"] + counts["ANSWERED"]
    assert counts["CHATTING"] == 1                               # 진행 중인 채팅은 1개
    cats = {i["category"] for i in r["items"] if i["category"]}
    assert cats
    cat = min(cats)
    filtered = client.get("/api/conversations", headers=customer_headers, params={"category": cat, "size": 100}).json()
    assert filtered["items"] and all(i["category"] == cat for i in filtered["items"])
    assert filtered["page"]["total"] == len(filtered["items"])


def test_policy_list_newest_first(client, admin_headers):
    ids = [p["id"] for p in client.get("/api/admin/policies", headers=admin_headers).json()]
    assert ids == sorted(ids, reverse=True)
