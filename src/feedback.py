"""답변 평가(👍/👎) 저장과 만족도 집계. data/feedback.jsonl 에 한 줄씩 쌓인다."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import config


def save(question: str, answer: str, rating: int, extra: dict | None = None,
         path: Path | None = None) -> None:
    """rating: 1 = 👍, 0 = 👎"""
    path = path or config.FEEDBACK_PATH
    rec = {"time": datetime.now().isoformat(timespec="seconds"), "question": question,
           "answer": answer, "rating": int(rating), **(extra or {})}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def stats(path: Path | None = None) -> tuple[int, int]:
    """(평가 수, 👍 수)"""
    path = path or config.FEEDBACK_PATH
    if not path.exists():
        return 0, 0
    recs = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return len(recs), sum(r["rating"] for r in recs)
