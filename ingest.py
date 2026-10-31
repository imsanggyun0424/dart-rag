"""명령줄에서 공시 수집하기.

예)
    python ingest.py 삼성전자 SK하이닉스
    python ingest.py 005930 --report 반기보고서 --limit 1
    python ingest.py 삼성전자 --force   # 다시 색인
    python ingest.py --list
"""
import argparse

from src.ingest import ingest_company
from src.store import HybridStore


def main():
    p = argparse.ArgumentParser(description="DART 공시를 수집해 검색 색인에 추가합니다.")
    p.add_argument("companies", nargs="*", help="회사명 또는 종목코드")
    p.add_argument("--report", default="사업보고서", choices=["사업보고서", "반기보고서", "분기보고서"])
    p.add_argument("--years", type=int, default=2, help="최근 몇 년치를 찾을지")
    p.add_argument("--limit", type=int, default=2, help="회사당 최대 보고서 수")
    p.add_argument("--force", action="store_true", help="이미 색인된 보고서도 다시 읽어서 색인")
    p.add_argument("--list", action="store_true", help="색인된 보고서 목록 보기")
    a = p.parse_args()

    store = HybridStore()
    if a.list or not a.companies:
        for r in store.list_reports():
            print(f'{r["company"]:<12} {r["doc_name"]:<24} {r["rcept_dt"]}  청크 {r["chunks"]}')
        return
    for name in a.companies:
        ingest_company(name, store, a.report, a.years, a.limit, force=a.force)


if __name__ == "__main__":
    main()
