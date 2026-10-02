"""★ 갈아 끼우는 자리 — 다른 팀 결과물이 나오면 이 폴더만 바꾸면 됩니다.

- ai_client.py         : AI 서버 입출력 형식 + .env의 AI_MODE에 따라 mock/http 선택
- mock_ai.py           : 가짜 AI (모델 서버 없이 개발할 때)
- http_ai.py           : 실제 AI 서버 호출
- document_parser.py   : 정책 문서 파일 읽기 (지금은 .txt/.md, PDF 등은 여기에 함수 추가)
- chunker.py           : 정책 문서 조각 나누기
- keyword_retriever.py : mock용 간단한 검색 (실제 검색은 AI 서버가 함)
"""
