"""목차 단위 텍스트 → 검색용 청크.

원칙
1. 청크는 섹션 경계를 넘지 않는다 (서로 다른 주제가 섞이지 않게).
2. 줄(표의 한 행) 단위로 자르고, 한 줄이 너무 길면 그때만 글자 단위로 자른다.
3. 청크 앞에 '회사 / 보고서 / 목차 경로'를 붙여 검색과 답변 모두에 맥락을 준다.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import config
from .parser import ParsedDoc


@dataclass
class Chunk:
    id: str
    text: str          # 임베딩/검색에 쓰는 전체 텍스트 (헤더 포함)
    body: str          # 헤더를 뺀 본문 (화면 표시용)
    metadata: dict


def _split_long(line: str, size: int, overlap: int) -> list[str]:
    step = max(1, size - overlap)
    return [line[i:i + size] for i in range(0, len(line), step)]


def split_text(text: str, size: int, overlap: int) -> list[str]:
    lines: list[str] = []
    for ln in text.split("\n"):
        lines.extend(_split_long(ln, size, overlap) if len(ln) > size else [ln])

    chunks, cur, cur_len = [], [], 0
    for ln in lines:
        if cur and cur_len + len(ln) + 1 > size:
            chunks.append("\n".join(cur))
            # 뒤쪽 몇 줄을 겹치게 남겨서 문맥이 끊기지 않게
            keep, kept_len = [], 0
            for prev in reversed(cur):
                if kept_len + len(prev) > overlap:
                    break
                keep.insert(0, prev)
                kept_len += len(prev) + 1
            cur, cur_len = keep, kept_len
        cur.append(ln)
        cur_len += len(ln) + 1
    if cur:
        chunks.append("\n".join(cur))
    return [c for c in chunks if c.strip()]


def chunk_document(doc: ParsedDoc, rcept_no: str, rcept_dt: str = "",
                   size: int = config.CHUNK_SIZE, overlap: int = config.CHUNK_OVERLAP,
                   extra_meta: dict | None = None) -> list[Chunk]:
    out: list[Chunk] = []
    for si, sec in enumerate(doc.sections):
        for ci, body in enumerate(split_text(sec.text, size, overlap)):
            header = f"[{doc.company} | {doc.doc_name} | {sec.path}]"
            out.append(Chunk(
                id=f"{rcept_no}-{si:04d}-{ci:03d}",
                text=f"{header}\n{body}",
                body=body,
                metadata={
                    "rcept_no": rcept_no,
                    "rcept_dt": rcept_dt,
                    "company": doc.company,
                    "doc_name": doc.doc_name,
                    "section": sec.path,
                    "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
                    **(extra_meta or {}),
                },
            ))
    return out
