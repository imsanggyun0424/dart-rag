"""OpenDART API 클라이언트.

사용하는 API
- corpCode.xml : 전체 회사 고유번호 목록 (ZIP)
- list.json    : 공시 검색
- document.xml : 공시서류 원본파일 (ZIP 안에 XML 여러 개)
"""
from __future__ import annotations

import io
import time
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta

import requests

from . import config

BASE = "https://opendart.fss.or.kr/api"

# 정기공시 보고서 종류 코드 (list.json 의 pblntf_detail_ty)
REPORT_TYPES = {
    "사업보고서": "A001",
    "반기보고서": "A002",
    "분기보고서": "A003",
}


class DartError(RuntimeError):
    pass


@dataclass
class Corp:
    corp_code: str
    corp_name: str
    stock_code: str


@dataclass
class Filing:
    rcept_no: str
    corp_name: str
    report_nm: str
    rcept_dt: str

    @property
    def url(self) -> str:
        return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={self.rcept_no}"


class DartClient:
    def __init__(self, api_key: str | None = None, session: requests.Session | None = None):
        self.api_key = api_key or config.DART_API_KEY
        if not self.api_key:
            raise DartError("DART_API_KEY 가 없습니다. .env 파일을 확인하세요.")
        self.s = session or requests.Session()

    # ---------- 내부 ----------
    def _get(self, endpoint: str, **params) -> requests.Response:
        params["crtfc_key"] = self.api_key
        for attempt in range(3):
            r = self.s.get(f"{BASE}/{endpoint}", params=params, timeout=60)
            r.raise_for_status()
            # ZIP 응답이 아니면 JSON/XML 에러 메시지일 수 있다
            if endpoint.endswith(".json"):
                status = r.json().get("status")
                if status == "020":  # 요청 제한 초과
                    time.sleep(2 ** attempt)
                    continue
            return r
        raise DartError("요청 제한 초과(020). 잠시 후 다시 시도하세요.")

    @staticmethod
    def _check_zip(r: requests.Response) -> zipfile.ZipFile:
        try:
            return zipfile.ZipFile(io.BytesIO(r.content))
        except zipfile.BadZipFile:
            # 에러일 때는 ZIP 대신 <result><status>..</status><message>..</message></result> 가 온다
            try:
                root = ET.fromstring(r.content)
                raise DartError(f"[{root.findtext('status')}] {root.findtext('message')}")
            except ET.ParseError:
                raise DartError(f"예상치 못한 응답: {r.content[:200]!r}")

    # ---------- 회사 코드 ----------
    def load_corps(self, refresh: bool = False) -> list[Corp]:
        """상장사 목록 (stock_code 가 있는 회사만). 처음 한 번 받아 캐시한다."""
        path = config.CORP_CODE_PATH
        if refresh or not path.exists():
            zf = self._check_zip(self._get("corpCode.xml"))
            path.write_bytes(zf.read(zf.namelist()[0]))
        root = ET.parse(path).getroot()
        corps = []
        for el in root.iter("list"):
            stock = (el.findtext("stock_code") or "").strip()
            if stock:
                corps.append(Corp(el.findtext("corp_code"), el.findtext("corp_name").strip(), stock))
        return corps

    def find_corp(self, name_or_stock: str) -> Corp:
        q = name_or_stock.strip()
        corps = self.load_corps()
        for c in corps:
            if q in (c.corp_name, c.stock_code):
                return c
        partial = [c for c in corps if q in c.corp_name]
        if len(partial) == 1:
            return partial[0]
        if partial:
            names = ", ".join(c.corp_name for c in partial[:10])
            raise DartError(f"'{q}' 와 비슷한 회사가 여러 개입니다: {names}")
        raise DartError(f"'{q}' 회사를 찾을 수 없습니다.")

    # ---------- 공시 검색 ----------
    def list_filings(self, corp_code: str, report: str = "사업보고서", years: int = 2) -> list[Filing]:
        end = date.today()
        bgn = end - timedelta(days=365 * years)
        r = self._get(
            "list.json",
            corp_code=corp_code,
            bgn_de=bgn.strftime("%Y%m%d"),
            end_de=end.strftime("%Y%m%d"),
            pblntf_detail_ty=REPORT_TYPES.get(report, report),
            last_reprt_at="Y",   # 정정공시가 있으면 최종본만
            page_count=100,
        ).json()
        if r.get("status") == "013":   # 조회된 데이터 없음
            return []
        if r.get("status") != "000":
            raise DartError(f"[{r.get('status')}] {r.get('message')}")
        return [Filing(x["rcept_no"], x["corp_name"], x["report_nm"].strip(), x["rcept_dt"]) for x in r["list"]]

    # ---------- 재무제표 (주요계정) ----------
    def key_accounts(self, corp_code: str, bsns_year: int, reprt_code: str = "11011") -> list[dict]:
        """단일회사 주요계정 (fnlttSinglAcnt). 사업보고서(11011) 한 건에 당기·전기·전전기 3년치가 들어있다.
        데이터가 없으면 빈 리스트."""
        r = self._get("fnlttSinglAcnt.json", corp_code=corp_code, bsns_year=str(bsns_year),
                      reprt_code=reprt_code).json()
        if r.get("status") == "013":
            return []
        if r.get("status") != "000":
            raise DartError(f"[{r.get('status')}] {r.get('message')}")
        return r.get("list", [])

    # ---------- 원문 ----------
    def download_document(self, rcept_no: str) -> bytes:
        """원문 XML(본문)을 bytes 로 반환. data/raw 에 캐시한다."""
        cache = config.RAW_DIR / f"{rcept_no}.xml"
        if cache.exists():
            return cache.read_bytes()
        zf = self._check_zip(self._get("document.xml", rcept_no=rcept_no))
        # ZIP 안에는 본문({rcept_no}.xml)과 감사보고서 등 첨부가 함께 들어있다. 본문을 우선 사용.
        names = sorted(zf.namelist(), key=lambda n: (n != f"{rcept_no}.xml", n))
        content = zf.read(names[0])
        cache.write_bytes(content)
        return content
