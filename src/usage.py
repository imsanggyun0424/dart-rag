"""Claude 사용량(토큰)·시간·비용 집계.

    with tracker.track() as u:
        ... Claude 호출들 ...
    print(u.summary())   # ⏱ 3.1초 · 입력 5,210 / 출력 420 토큰 · 약 ₩21
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field

from . import config


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    seconds: float = 0.0
    _start: float = field(default=0.0, repr=False)

    def add(self, usage) -> None:
        """anthropic 응답의 usage 객체를 더한다. 캐시 읽기/쓰기 토큰도 입력으로 계산."""
        if usage is None:
            return
        self.input_tokens += (getattr(usage, "input_tokens", 0) or 0) \
            + (getattr(usage, "cache_read_input_tokens", 0) or 0) \
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.calls += 1

    def merge(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.calls += other.calls
        self.seconds += other.seconds

    @property
    def cost_usd(self) -> float:
        return (self.input_tokens * config.PRICE_INPUT_PER_MTOK
                + self.output_tokens * config.PRICE_OUTPUT_PER_MTOK) / 1_000_000

    @property
    def cost_krw(self) -> float:
        return self.cost_usd * config.USD_KRW

    def summary(self) -> str:
        return (f"⏱ {self.seconds:.1f}초 · 입력 {self.input_tokens:,} / 출력 {self.output_tokens:,} 토큰"
                f" · 약 ₩{self.cost_krw:,.0f} (${self.cost_usd:.4f})")


class UsageTracker:
    """여러 Claude 호출을 하나의 Usage 로 묶는다. 중첩해서 써도 된다."""

    def __init__(self):
        self._active: list[Usage] = []

    def record(self, usage) -> None:
        for u in self._active:
            u.add(usage)

    @contextmanager
    def track(self):
        u = Usage(_start=time.time())
        self._active.append(u)
        try:
            yield u
        finally:
            u.seconds = time.time() - u._start
            self._active.remove(u)
