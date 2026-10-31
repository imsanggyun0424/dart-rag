"""API 키 없이 돌아가는 테스트. 실행: python -m pytest -q"""
import hashlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from src.chunker import chunk_document, split_text
from src.parser import parse_document
from src.rag import DartRAG, cited_numbers
from src.store import HybridStore, tokenize

FIXTURE = Path(__file__).parent / "fixtures" / "sample_report.xml"


class FakeEmbedder:
    """글자 2-gram 해시로 만드는 가짜 임베딩. 모델 다운로드 없이 벡터 검색 경로를 테스트한다."""
    def embed(self, texts):
        out = []
        for t in texts:
            v = np.zeros(256)
            for i in range(len(t) - 1):
                v[int(hashlib.md5(t[i:i + 2].encode()).hexdigest(), 16) % 256] += 1
            out.append((v / (np.linalg.norm(v) or 1)).tolist())
        return out


@pytest.fixture
def doc():
    return parse_document(FIXTURE.read_bytes())


@pytest.fixture
def store(tmp_path, doc):
    s = HybridStore(embedder=FakeEmbedder(), path=tmp_path / "chroma")
    s.add(chunk_document(doc, "20260301000001", "20260301", size=300, overlap=50))
    return s


def test_parse_metadata(doc):
    assert doc.doc_name == "사업보고서"
    assert doc.company == "가상전자주식회사"


def test_parse_section_paths(doc):
    paths = [s.path for s in doc.sections]
    assert "II. 사업의 내용 > 2. 주요 제품 및 서비스" in paths
    assert "XI. 그 밖에 투자자 보호를 위하여 필요한 사항 > 2. 우발부채 등에 관한 사항" in paths


def test_parse_table_rows(doc):
    sec = next(s for s in doc.sections if s.path.endswith("주요 제품 및 서비스"))
    assert "메모리 | DRAM, HBM, NAND | 8,520,000 | 71.0" in sec.text
    assert "(단위 : 백만원, %)" in sec.text   # 표 단위가 표와 같은 청크에 남아야 한다


def test_parse_entities(doc):
    assert any("R&D" in s.text for s in doc.sections)


def test_parse_broken_xml_cp949():
    """실제 공시에는 이스케이프 안 된 & 나 cp949 인코딩이 섞여 있다."""
    raw = FIXTURE.read_text(encoding="utf-8").replace("R&amp;D", "R&D 및 M&A")
    d = parse_document(raw.encode("cp949"))
    assert d.company == "가상전자주식회사" and len(d.sections) == 5


def test_parse_does_not_truncate_on_errors():
    """XML 파서는 첫 문법 오류에서 나머지를 버린다. 끝까지 읽는지 확인 (실제 삼성전자 보고서에서 발생)."""
    raw = FIXTURE.read_text(encoding="utf-8").replace(
        "<P>반면", '<P>반면 1<2 이고 <SPAN USERMARK="F-BT14">강조</P>').replace(
        "<P>당사는 차세대", "<P ALIGN=CENTER>당사는 차세대")
    d = parse_document(raw.encode())
    assert len(d.sections) == 5
    assert any("350억원" in s.text for s in d.sections)   # 문서 맨 끝 섹션까지 읽었는지


def test_split_respects_size():
    text = "\n".join(f"{i}번째 줄 " + "가" * 50 for i in range(40))
    parts = split_text(text, 300, 60)
    assert len(parts) > 1
    assert all(len(p) <= 300 for p in parts)
    # 겹침: 다음 청크가 이전 청크의 마지막 줄로 시작
    assert parts[1].split("\n")[0] == parts[0].split("\n")[-1]


def test_split_long_line():
    parts = split_text("나" * 1000, 300, 50)
    assert all(len(p) <= 300 for p in parts) and len(parts) >= 4


def test_chunk_header(doc):
    chunks = chunk_document(doc, "20260301000001")
    assert chunks[0].text.startswith("[가상전자주식회사 | 사업보고서 | ")
    assert chunks[0].metadata["url"].endswith("rcpNo=20260301000001")
    assert len({c.id for c in chunks}) == len(chunks)


def test_tokenize_bigrams():
    toks = tokenize("영업이익이 증가")
    assert "영업" in toks and "이익" in toks


@pytest.mark.parametrize("mode", ["keyword", "vector", "hybrid"])
def test_search_finds_lawsuit(store, mode):
    hits = store.search("특허 소송 우발부채", k=3, mode=mode)
    assert hits, mode
    assert any("우발부채" in h["metadata"]["section"] for h in hits), mode


def test_search_numbers_keyword(store):
    hits = store.search("연구개발비 매출액 비율", k=2, mode="keyword")
    assert "연구개발활동" in hits[0]["metadata"]["section"]


def test_scope_filter(store, doc):
    store.add(chunk_document(doc, "20250301000009", "20250301"))
    hits = store.search("HBM", k=10, rcept_nos=["20250301000009"])
    assert hits and all(h["metadata"]["rcept_no"] == "20250301000009" for h in hits)


def test_reports_and_delete(store):
    assert [r["rcept_no"] for r in store.list_reports()] == ["20260301000001"]
    assert store.has_report("20260301000001")
    store.delete_report("20260301000001")
    assert store.list_reports() == [] and store.search("HBM") == []


def test_cited_numbers():
    assert cited_numbers("매출 증가 [1][3]. 그리고 [9]", 4) == [1, 3]


def test_rag_answer_with_mock_claude(store):
    calls = []

    class FakeMessages:
        def create(self, **kw):
            calls.append(kw)
            return SimpleNamespace(content=[SimpleNamespace(text="소송가액은 약 350억원입니다 [1].")])

    rag = DartRAG(store, client=SimpleNamespace(messages=FakeMessages()), model="test")
    ans = rag.answer("진행 중인 소송 있어?")
    assert ans.cited == [1]
    prompt = calls[-1]["messages"][-1]["content"]
    assert "<sources>" in prompt and "질문: 진행 중인 소송 있어?" in prompt
    assert "근거해서만" in calls[-1]["system"]

    # 후속 질문이면 검색어를 먼저 다시 쓴다(condense) → Claude 호출 2번
    calls.clear()
    rag.answer("그 금액은?", history=[{"role": "user", "content": "소송 있어?"},
                                      {"role": "assistant", "content": "있습니다."}])
    assert len(calls) == 2 and "마지막 질문: 그 금액은?" in calls[0]["messages"][0]["content"]
