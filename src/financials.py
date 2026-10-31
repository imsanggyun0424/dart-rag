"""DART 재무제표 API → 연도별 실적 표 (그래프·비교·리포트에서 공용).

AI 가 원문에서 읽은 숫자가 아니라, 금융감독원이 제공하는 **정형 데이터**라서 정확하다.
사업보고서 1건에 3개년(당기·전기·전전기)이 들어있으므로 2번 호출하면 최대 6년치를 얻는다.
"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd

from . import config
from .dart_client import DartClient

# 표시 이름 → 공시에서 쓰이는 계정명 후보 (회사·업종마다 표기가 조금씩 다르다)
ACCOUNTS = {
    "매출액": ["매출액", "수익(매출액)", "영업수익", "매출"],
    "영업이익": ["영업이익", "영업이익(손실)", "영업손실"],
    "당기순이익": ["당기순이익", "당기순이익(손실)", "당기순손실"],
    "자산총계": ["자산총계"],
    "부채총계": ["부채총계"],
    "자본총계": ["자본총계"],
}
PERIODS = [("thstrm", 0), ("frmtrm", 1), ("bfefrmtrm", 2)]   # (필드 접두어, 몇 년 전)


def to_int(v) -> int | None:
    if v in (None, "", "-"):
        return None
    s = str(v).replace(",", "").strip()
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        n = int(float(s))
    except ValueError:
        return None
    return -n if neg else n


def _match(account_nm: str) -> str | None:
    name = account_nm.replace(" ", "")
    for key, cands in ACCOUNTS.items():
        if name in [c.replace(" ", "") for c in cands]:
            return key
    return None


def rows_to_table(rows: list[dict], bsns_year: int) -> tuple[dict[int, dict], str]:
    """API rows → {연도: {계정: 금액(원)}}. 연결(CFS)을 우선하고 없으면 별도(OFS)."""
    fs = "CFS" if any(r.get("fs_div") == "CFS" for r in rows) else "OFS"
    out: dict[int, dict] = {}
    for r in rows:
        if r.get("fs_div", fs) != fs:
            continue
        key = _match(r.get("account_nm", ""))
        if not key:
            continue
        for prefix, back in PERIODS:
            v = to_int(r.get(f"{prefix}_amount"))
            if v is not None:
                out.setdefault(bsns_year - back, {}).setdefault(key, v)
    return out, ("연결" if fs == "CFS" else "별도")


def _cached_accounts(dart: DartClient, corp_code: str, year: int) -> list[dict]:
    path = config.FIN_DIR / f"{corp_code}_{year}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    rows = dart.key_accounts(corp_code, year)
    if rows or year < date.today().year - 1:   # 아직 공시 전인 해는 캐시하지 않는다
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return rows


def get_financials(dart: DartClient, corp_code: str, latest_year: int | None = None,
                   years: int = 6) -> tuple[pd.DataFrame, str]:
    """연도별 실적 DataFrame (index=연도, 금액 단위=원) 과 '연결'/'별도' 표시를 돌려준다."""
    if latest_year is None:
        # 가장 최근 사업보고서를 찾는다 (3월 말 이전이면 작년 보고서가 아직 없을 수 있음)
        latest_year = date.today().year - 1
        if not _cached_accounts(dart, corp_code, latest_year):
            latest_year -= 1
    table: dict[int, dict] = {}
    basis = "연결"
    for y in range(latest_year, latest_year - years, -3):
        rows = _cached_accounts(dart, corp_code, y)
        if not rows:
            continue
        t, b = rows_to_table(rows, y)
        if y == latest_year:
            basis = b
        for yr, vals in t.items():
            table.setdefault(yr, {}).update({k: v for k, v in vals.items() if k not in table.get(yr, {})})
    if not table:
        return pd.DataFrame(), basis
    df = pd.DataFrame.from_dict(table, orient="index").sort_index()
    df = df[df.index > latest_year - years]
    df.index.name = "연도"
    return add_ratios(df), basis


def add_ratios(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if {"매출액", "영업이익"} <= set(df):
        df["영업이익률(%)"] = (df["영업이익"] / df["매출액"] * 100).round(1)
    if {"매출액", "당기순이익"} <= set(df):
        df["순이익률(%)"] = (df["당기순이익"] / df["매출액"] * 100).round(1)
    if {"부채총계", "자본총계"} <= set(df):
        df["부채비율(%)"] = (df["부채총계"] / df["자본총계"] * 100).round(1)
    if "매출액" in df:
        df["매출 성장률(%)"] = (df["매출액"].pct_change() * 100).round(1)
    return df


def fmt_krw(n) -> str:
    """원 단위 숫자를 '300.9조' / '1,234억' 처럼 읽기 쉽게."""
    if n is None or pd.isna(n):
        return "-"
    n = float(n)
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1e12:
        return f"{sign}{n / 1e12:,.1f}조"
    if n >= 1e8:
        return f"{sign}{n / 1e8:,.0f}억"
    return f"{sign}{n:,.0f}원"


def to_text(df: pd.DataFrame, company: str, basis: str) -> str:
    """Claude 프롬프트에 넣을 실적 요약 표 (정확한 숫자 근거)."""
    if df.empty:
        return f"{company}: 재무 데이터 없음"
    lines = [f"[{company} 재무 요약 · {basis} 기준 · 출처: DART 재무제표 API]",
             "연도 | 매출액 | 영업이익 | 당기순이익 | 영업이익률 | 부채비율"]
    for y, r in df.iterrows():
        lines.append(" | ".join([str(y), fmt_krw(r.get("매출액")), fmt_krw(r.get("영업이익")),
                                 fmt_krw(r.get("당기순이익")),
                                 f'{r.get("영업이익률(%)", float("nan")):.1f}%' if pd.notna(r.get("영업이익률(%)")) else "-",
                                 f'{r.get("부채비율(%)", float("nan")):.1f}%' if pd.notna(r.get("부채비율(%)")) else "-"]))
    return "\n".join(lines)
