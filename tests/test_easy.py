"""쉬운 말 모드 테스트."""
from types import SimpleNamespace

from src.easy import EASY_INSTRUCTIONS, GLOSSARY, find_terms
from src.rag import DartRAG, SYSTEM_PROMPT, system_prompt
from tests.test_features import FakeClaude, rag  # noqa: F401  (fixture)


def test_find_terms_longest_first_and_in_order():
    text = "영업이익률은 13%이고, 부채비율은 25%입니다. 영업이익은 40조 원입니다."
    terms = [t for t, _ in find_terms(text)]
    assert terms == ["영업이익률", "부채비율", "영업이익"]


def test_find_terms_no_partial_duplicate():
    assert [t for t, _ in find_terms("영업이익률이 올랐어요")] == ["영업이익률"]


def test_glossary_plain_language():
    assert all(len(v) < 120 and v.endswith("요.") for v in GLOSSARY.values())


def test_system_prompt_switch():
    assert system_prompt(False) == SYSTEM_PROMPT
    assert system_prompt(True).startswith(SYSTEM_PROMPT) and EASY_INSTRUCTIONS in system_prompt(True)
    assert "한 줄 정리" in EASY_INSTRUCTIONS and "근거해서만" in system_prompt(True)


def test_answer_easy_flag(rag):  # noqa: F811
    claude = FakeClaude("본업으로 번 돈은 40조 원이에요 [1].\n\n**한 줄 정리:** 장사를 잘했어요.")
    r = DartRAG(rag, client=claude, model="t")
    r.answer("영업이익은?", easy=True)
    assert EASY_INSTRUCTIONS in claude.calls[-1]["system"]
    r.answer("영업이익은?")
    assert EASY_INSTRUCTIONS not in claude.calls[-1]["system"]
