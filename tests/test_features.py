"""새 기능 테스트: 재무제표, 비용 집계, 평가 저장, 비교·리포트. API 키 불필요."""
from types import SimpleNamespace

import pytest

from src import config, feedback
from src.analysis import compare, report, to_html
from src.chunker import chunk_document
from src.financials import fmt_krw, get_financials, rows_to_table, to_int, to_text
from src.parser import parse_document
from src.rag import DartRAG, response_text
from src.store import HybridStore
from src.usage import Usage, UsageTracker
from tests.test_pipeline import FIXTURE, FakeEmbedder


def acct(name, fs, t, f, b, sj="IS"):
    return {"account_nm": name, "fs_div": fs, "sj_div": sj,
            "thstrm_amount": t, "frmtrm_amount": f, "bfefrmtrm_amount": b}


ROWS_2025 = [
    acct("매출액", "CFS", "300,000,000,000,000", "280,000,000,000,000", "250,000,000,000,000"),
    acct("영업이익", "CFS", "40,000,000,000,000", "30,000,000,000,000", "-5,000,000,000,000"),
    acct("당기순이익", "CFS", "35,000,000,000,000", "25,000,000,000,000", "10,000,000,000,000"),
    acct("부채총계", "CFS", "100,000,000,000,000", "90,000,000,000,000", "", sj="BS"),
    acct("자본총계", "CFS", "400,000,000,000,000", "360,000,000,000,000", "", sj="BS"),
    acct("매출액", "OFS", "1", "1", "1"),   # 별도 재무제표는 무시되어야 함
]
ROWS_2022 = [acct("매출액", "CFS", "240,000,000,000,000", "230,000,000,000,000", "200,000,000,000,000"),
             acct("영업이익", "CFS", "20,000,000,000,000", "25,000,000,000,000", "21,000,000,000,000")]


class FakeDart:
    def __init__(self):
        self.calls = []

    def key_accounts(self, corp_code, year, reprt_code="11011"):
        self.calls.append(year)
        return {2025: ROWS_2025, 2022: ROWS_2022}.get(year, [])


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FIN_DIR", tmp_path / "fin")
    (tmp_path / "fin").mkdir()
    monkeypatch.setattr(config, "FEEDBACK_PATH", tmp_path / "fb.jsonl")


def test_to_int():
    assert to_int("1,234") == 1234 and to_int("-5") == -5 and to_int("(300)") == -300
    assert to_int("") is None and to_int("-") is None


def test_rows_prefer_consolidated():
    t, basis = rows_to_table(ROWS_2025, 2025)
    assert basis == "연결"
    assert t[2025]["매출액"] == 300_000_000_000_000 and t[2023]["영업이익"] == -5_000_000_000_000
    assert "부채총계" not in t[2023]


def test_get_financials_6_years_and_ratios():
    dart = FakeDart()
    df, basis = get_financials(dart, "00126380", 2025)
    assert list(df.index) == [2020, 2021, 2022, 2023, 2024, 2025]
    assert df.loc[2025, "영업이익률(%)"] == pytest.approx(13.3)
    assert df.loc[2025, "부채비율(%)"] == 25.0
    assert df.loc[2023, "매출액"] == 250_000_000_000_000   # 최근 보고서 값이 우선
    # 두 번째 호출은 캐시에서
    get_financials(dart, "00126380", 2025)
    assert dart.calls == [2025, 2022]


def test_fmt_and_text():
    assert fmt_krw(300_870_000_000_000) == "300.9조"
    assert fmt_krw(123_400_000_000) == "1,234억"
    assert fmt_krw(-5e12) == "-5.0조"
    df, b = get_financials(FakeDart(), "x", 2025)
    txt = to_text(df, "가상전자", b)
    assert "2025 | 300.0조 | 40.0조" in txt and "13.3%" in txt


def test_usage_tracker_nested_and_cost(monkeypatch):
    monkeypatch.setattr(config, "PRICE_INPUT_PER_MTOK", 2)
    monkeypatch.setattr(config, "PRICE_OUTPUT_PER_MTOK", 10)
    monkeypatch.setattr(config, "USD_KRW", 1000)
    t = UsageTracker()
    with t.track() as outer:
        t.record(SimpleNamespace(input_tokens=1000, output_tokens=100))
        with t.track() as inner:
            t.record(SimpleNamespace(input_tokens=500, output_tokens=0, cache_read_input_tokens=500))
    assert (outer.input_tokens, outer.output_tokens, outer.calls) == (2000, 100, 2)
    assert inner.input_tokens == 1000
    assert outer.cost_usd == pytest.approx(0.005)
    assert "약 ₩5" in outer.summary()
    total = Usage()
    total.merge(outer)
    assert total.calls == 2


def test_response_text_skips_thinking():
    r = SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking="음..."),
                                 SimpleNamespace(type="text", text="답 [1]")])
    assert response_text(r) == "답 [1]"


def test_feedback_stats():
    assert feedback.stats() == (0, 0)
    feedback.save("q1", "a1", 1)
    feedback.save("q2", "a2", 0)
    feedback.save("q3", "a3", 1)
    assert feedback.stats() == (3, 2)


class FakeClaude:
    def __init__(self, reply):
        self.reply, self.calls = reply, []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.reply)],
                               usage=SimpleNamespace(input_tokens=3000, output_tokens=500))


@pytest.fixture
def rag(tmp_path):
    s = HybridStore(embedder=FakeEmbedder(), path=tmp_path / "chroma")
    doc = parse_document(FIXTURE.read_bytes())
    s.add(chunk_document(doc, "R1", "20260301"))
    doc.company = "다른전자"
    s.add(chunk_document(doc, "R2", "20260301"))
    return s


def test_compare_one_call_with_financials(rag):
    claude = FakeClaude("요약\n| 항목 | 가상전자 | 다른전자 |\n|---|---|---|\n| 연구개발 | 11% [1] | 11% [2] |")
    r = DartRAG(rag, client=claude, model="t")
    res = compare(r, [{"company": "가상전자주식회사", "rcept_no": "R1"},
                      {"company": "다른전자", "rcept_no": "R2"}], ["[가상전자 재무 요약]", "[다른전자 재무 요약]"])
    assert len(claude.calls) == 1
    prompt = claude.calls[0]["messages"][0]["content"]
    assert "<financials>" in prompt and "[다른전자 재무 요약]" in prompt
    assert {h["metadata"]["rcept_no"] for h in res.sources} == {"R1", "R2"}
    assert res.cited == [1, 2] and res.usage.calls == 1


def test_report_and_html(rag):
    claude = FakeClaude("# 가상전자 기업 요약\n> 메모리 회사 [1]\n\n## 리스크\n- 특허 소송 350억원 [2]")
    r = DartRAG(rag, client=claude, model="t")
    res = report(r, "가상전자주식회사", "R1", "[재무]")
    assert all(h["metadata"]["rcept_no"] == "R1" for h in res.sources)
    html = to_html("가상전자 기업 요약", res.text, res.sources, res.cited)
    assert "<h1>가상전자 기업 요약</h1>" in html and "<blockquote>" in html
    assert 'id="s1"' in html and 'id="s3"' not in html   # 인용된 출처만
