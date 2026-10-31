"""회사명 → 공시 검색 → 원문 다운로드 → 파싱 → 청킹 → 색인 까지 한 번에."""
from __future__ import annotations

from typing import Callable

from .chunker import chunk_document
from .dart_client import DartClient, Filing
from .parser import parse_document
from .store import HybridStore


def ingest_filing(dart: DartClient, store: HybridStore, f: Filing, force: bool = False,
                  corp_code: str = "") -> int:
    if store.has_report(f.rcept_no) and not force:
        return 0
    if force:
        store.delete_report(f.rcept_no)
    doc = parse_document(dart.download_document(f.rcept_no))
    if not doc.company:
        doc.company = f.corp_name
    doc.doc_name = f.report_nm  # list.json 의 이름에는 기간이 들어있다. 예) 사업보고서 (2025.12)
    return store.add(chunk_document(doc, f.rcept_no, f.rcept_dt, extra_meta={"corp_code": corp_code}))


def ingest_company(name: str, store: HybridStore, report: str = "사업보고서", years: int = 2,
                   limit: int = 2, dart: DartClient | None = None, force: bool = False,
                   log: Callable[[str], None] = print) -> list[Filing]:
    dart = dart or DartClient()
    corp = dart.find_corp(name)
    filings = dart.list_filings(corp.corp_code, report, years)[:limit]
    if not filings:
        log(f"{corp.corp_name}: 최근 {years}년 {report} 없음")
        return []
    for f in filings:
        n = ingest_filing(dart, store, f, force=force, corp_code=corp.corp_code)
        log(f"{f.corp_name} {f.report_nm} ({f.rcept_dt}) → " + (f"{n}개 청크 색인" if n else "이미 색인됨"))
    return filings
