"""DART 공시 원문(XML) → 목차 단위 텍스트로 변환.

DART 원문은 대략 이런 구조다.
    <DOCUMENT>
      <DOCUMENT-NAME>사업보고서</DOCUMENT-NAME>
      <COMPANY-NAME>삼성전자</COMPANY-NAME>
      <BODY>
        <SECTION-1><TITLE>II. 사업의 내용</TITLE>
          <SECTION-2><TITLE>1. 사업의 개요</TITLE><P>...</P><TABLE>...</TABLE></SECTION-2>
        ...
XML 문법이 깨진 경우가 많아서, 깨진 곳이 있어도 끝까지 읽는 HTML 파서(html.parser)를 쓴다.
태그명은 소문자가 된다.
"""
from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, NavigableString, Tag, XMLParsedAsHTMLWarning

SECTION_RE = re.compile(r"^section-\d+$")
WS_RE = re.compile(r"\s+")


@dataclass
class Section:
    path: str   # 예) "II. 사업의 내용 > 1. 사업의 개요"
    text: str


@dataclass
class ParsedDoc:
    doc_name: str
    company: str
    sections: list[Section] = field(default_factory=list)


def _clean(s: str) -> str:
    return WS_RE.sub(" ", s.replace("\xa0", " ")).strip()


def _decode(raw: bytes | str) -> str:
    if isinstance(raw, str):
        return raw
    for enc in ("utf-8", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def table_to_text(table: Tag) -> str:
    """표를 '셀 | 셀 | 셀' 형태의 줄들로 변환. 숫자 질문에 답하려면 표 보존이 중요하다."""
    lines = []
    for tr in table.find_all("tr"):
        cells = [_clean(c.get_text(" ")) for c in tr.find_all(["td", "th", "te", "tu"])]
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def _collect(el: Tag, path: list[str], out: list[Section]) -> None:
    buf: list[str] = []

    def flush():
        text = "\n".join(b for b in buf if b).strip()
        if text:
            out.append(Section(" > ".join(path) or "(본문)", text))
        buf.clear()

    for child in el.children:
        if isinstance(child, NavigableString):
            t = _clean(str(child))
            if t:
                buf.append(t)
            continue
        if not isinstance(child, Tag):
            continue
        name = child.name or ""
        if SECTION_RE.match(name):
            flush()
            title_el = child.find("title", recursive=False)
            # html.parser 는 <title> 안을 글자로만 읽으므로, 안쪽 태그(<SPAN> 등)를 한 번 더 걷어낸다
            title = _clean(re.sub(r"<[^>]+>", " ", title_el.get_text(" "))) if title_el else ""
            _collect(child, path + ([title] if title else []), out)
        elif name == "title":
            continue  # 섹션 제목은 path 로 이미 반영됨
        elif name == "table":
            buf.append(table_to_text(child))
        elif child.find(["table"] + [f"section-{i}" for i in range(1, 6)]):
            # 표/하위섹션을 품은 컨테이너는 안으로 들어가서 순서를 유지
            flush()
            _collect(child, path, out)
        else:
            buf.append(_clean(child.get_text(" ")))
    flush()


def _soup(raw: bytes | str) -> BeautifulSoup:
    # XML 파서는 문법 오류를 만나면 그 뒤를 조용히 버린다(실제 공시에서 본문 대부분이 사라짐).
    # 순수 파이썬 HTML 파서는 깨진 태그도 끝까지 읽고, 태그 위치도 원래대로 유지한다.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        return BeautifulSoup(_decode(raw), "html.parser")


def parse_document(raw: bytes | str) -> ParsedDoc:
    soup = _soup(raw)
    doc_name = _clean(soup.find("document-name").get_text()) if soup.find("document-name") else ""
    company = _clean(soup.find("company-name").get_text()) if soup.find("company-name") else ""
    body = soup.find("body") or soup
    sections: list[Section] = []
    _collect(body, [], sections)
    # 표지/목차처럼 너무 짧은 조각은 버린다
    sections = [s for s in sections if len(s.text) >= 30]
    return ParsedDoc(doc_name, company, sections)
