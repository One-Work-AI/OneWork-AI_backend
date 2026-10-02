"""정책 문서 폴더를 한 번에 등록 (PDF·TXT·MD). 관리자 화면의 '새 PDF 등록'과 같은 방식입니다.

    python -m scripts.ingest_policies --dir policies
    python -m scripts.ingest_policies --dir policies_sample      (샘플 문서로 시험할 때)

여러 번 실행해도 안전합니다 (같은 파일 이름의 문서는 내용이 바뀐 경우에만 파일 교체 → 버전 +1).
규칙은 app/services/policies.py 위쪽 설명 참고.

※ 이 스크립트는 백엔드에만 등록합니다 (관리자 목록·PDF 보기, 근거 연결, mock 검색용).
  AI 서버의 적재는 RAG 팀이 같은 파일로 따로 합니다. 파일 이름(확장자 제외)이 같으면 doc_key로 근거가 연결됩니다.
"""
import argparse
from pathlib import Path

from app.db import SessionLocal
from app.plugins.document_parser import supported_extensions
from app.services.policies import ingest_directory


def main():
    p = argparse.ArgumentParser(description="정책 문서 적재")
    p.add_argument("--dir", required=True, help="정책 문서 폴더")
    args = p.parse_args()
    root = Path(args.dir)
    if not root.is_dir():
        raise SystemExit(f"폴더가 없습니다: {root}")

    print("읽을 수 있는 형식:", ", ".join(supported_extensions()))
    with SessionLocal() as db:
        r = ingest_directory(db, root)
    for label, items in (("새로 추가", r.added), ("새 버전", r.updated), ("변경 없음", r.unchanged), ("건너뜀", r.skipped)):
        print(f"{label}: {len(items)}건")
        for x in items:
            print("   -", x)


if __name__ == "__main__":
    main()
