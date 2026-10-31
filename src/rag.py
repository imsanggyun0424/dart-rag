"""검색 결과를 근거로 Claude 가 답변을 생성한다. 답변 속 [1], [2] 는 출처 번호."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator

import anthropic

from . import config
from .store import HybridStore
from .usage import Usage, UsageTracker

SYSTEM_PROMPT = """당신은 한국 상장사의 전자공시(DART)를 분석하는 애널리스트 비서입니다.

규칙:
1. 반드시 <sources> 안의 공시 발췌문에 근거해서만 답하세요. 발췌문에 없는 내용은 추측하지 말고 "제공된 공시에서 찾을 수 없습니다"라고 말하세요.
2. 문장마다 근거가 된 발췌문 번호를 [1], [2]처럼 붙이세요.
3. 숫자를 인용할 때는 단위(원, 백만원, %)와 기간(예: 2025년 반기)을 함께 적으세요. 표의 단위가 표 위에 따로 적혀 있는 경우가 많으니 주의하세요.
4. 공시 내용은 사실로 전달하되, 투자 권유나 주가 전망은 하지 마세요.
5. 한국어로, 핵심부터 간결하게 답하세요. 필요하면 짧은 목록을 쓰세요."""

CONDENSE_PROMPT = """아래 대화를 참고해서, 마지막 질문을 앞의 맥락 없이도 이해되는 하나의 검색용 질문으로 바꿔 주세요.
회사명과 기간 같은 정보를 빠뜨리지 마세요. 질문 한 문장만 출력하세요.

{history}

마지막 질문: {question}"""


@dataclass
class Answer:
    text: str
    sources: list[dict]
    search_query: str
    cited: list[int] = field(default_factory=list)
    usage: Usage | None = None


def response_text(resp) -> str:
    """응답의 텍스트 블록만 이어 붙인다. (생각 블록 등 다른 블록이 앞에 올 수 있다)"""
    return "".join(getattr(b, "text", "") for b in resp.content
                   if getattr(b, "type", "text") == "text").strip()


def format_sources(hits: list[dict], start: int = 1) -> str:
    parts = []
    for i, h in enumerate(hits, start):
        m = h["metadata"]
        parts.append(f'<source id="{i}" company="{m["company"]}" report="{m["doc_name"]}" '
                     f'date="{m["rcept_dt"]}" section="{m["section"]}">\n{h["text"]}\n</source>')
    return "<sources>\n" + "\n".join(parts) + "\n</sources>"


def cited_numbers(text: str, n: int) -> list[int]:
    return sorted({int(x) for x in re.findall(r"\[(\d+)\]", text) if 1 <= int(x) <= n})


class DartRAG:
    def __init__(self, store: HybridStore, client: anthropic.Anthropic | None = None,
                 model: str = config.CLAUDE_MODEL):
        self.store = store
        self.client = client or anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY or None)
        self.model = model
        self.tracker = UsageTracker()

    # ---------- Claude 호출 공통 ----------
    def complete(self, messages: list[dict], system: str | None = None, max_tokens: int = 1500) -> str:
        kw = {"system": system} if system else {}
        r = self.client.messages.create(model=self.model, max_tokens=max_tokens, messages=messages, **kw)
        self.tracker.record(getattr(r, "usage", None))
        return response_text(r)

    # ---------- 검색 ----------
    def condense(self, question: str, history: list[dict]) -> str:
        """후속 질문("그럼 작년은?")을 독립적인 검색 질문으로 바꾼다."""
        if not history:
            return question
        hist = "\n".join(f'{"사용자" if h["role"] == "user" else "비서"}: {h["content"][:500]}'
                         for h in history[-4:])
        out = self.complete([{"role": "user", "content": CONDENSE_PROMPT.format(history=hist, question=question)}],
                            max_tokens=300)
        return out.splitlines()[-1].strip() if out else question

    def retrieve(self, question: str, history: list[dict] | None = None,
                 rcept_nos: list[str] | None = None, k: int = config.TOP_K) -> tuple[str, list[dict]]:
        q = self.condense(question, history or [])
        return q, self.store.search(q, k=k, rcept_nos=rcept_nos)

    # ---------- 답변 ----------
    def _messages(self, question: str, hits: list[dict], history: list[dict]) -> list[dict]:
        msgs = [{"role": h["role"], "content": h["content"]} for h in history[-6:]]
        msgs.append({"role": "user", "content": f"{format_sources(hits)}\n\n질문: {question}"})
        return msgs

    def answer(self, question: str, history: list[dict] | None = None,
               rcept_nos: list[str] | None = None, k: int = config.TOP_K) -> Answer:
        with self.tracker.track() as u:
            q, hits = self.retrieve(question, history, rcept_nos, k)
            if not hits:
                return Answer("검색된 공시가 없습니다. 먼저 보고서를 수집해 주세요.", [], q, usage=u)
            text = self.complete(self._messages(question, hits, history or []), SYSTEM_PROMPT)
        return Answer(text, hits, q, cited_numbers(text, len(hits)), u)

    def stream(self, question: str, hits: list[dict], history: list[dict] | None = None) -> Iterator[str]:
        """Streamlit 화면용 스트리밍. retrieve() 로 찾은 hits 를 넘겨준다."""
        with self.client.messages.stream(model=self.model, max_tokens=1500, system=SYSTEM_PROMPT,
                                         messages=self._messages(question, hits, history or [])) as s:
            yield from s.text_stream
            final = s.get_final_message() if hasattr(s, "get_final_message") else None
            self.tracker.record(getattr(final, "usage", None))
