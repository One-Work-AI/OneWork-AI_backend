# 실제 정책 문서를 넣는 폴더

- 하위 폴더 이름을 카테고리 이름(결제 / 문의 / 배송 / 배송지 / 주문 / 취소 / 환불)으로 하면 카테고리가 자동으로 붙습니다.
  예: `policies/환불/refund_policy.md`
- 지금 읽을 수 있는 형식: `.txt`, `.md` (다른 형식은 `app/plugins/document_parser.py`에 읽는 함수를 추가)
- 넣은 뒤 실행: `python -m scripts.ingest_policies --dir policies`
- AI 서버(RAG 팀)도 **같은 폴더 구조·같은 파일 이름**으로 적재하면 근거 문서가 자동으로 연결됩니다 (doc_key = `환불/refund_policy`).

이 README 파일은 적재 대상에서 제외됩니다.
