"""배포용 안전장치: 하루 Claude 사용 횟수 제한.

인터넷에 공개하면 다른 사람의 질문도 내 크레딧으로 결제된다.
질문·비교·리포트를 실행할 때마다 1회로 세고, DAILY_LIMIT 을 넘으면 막는다.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from . import config


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def used_today(path: Path | None = None) -> int:
    return _load(path or config.DAILY_USAGE_PATH).get(date.today().isoformat(), 0)


def remaining(limit: int | None = None, path: Path | None = None) -> int | None:
    """남은 횟수. 제한이 없으면 None."""
    limit = config.DAILY_LIMIT if limit is None else limit
    if not limit:
        return None
    return max(0, limit - used_today(path))


def try_consume(limit: int | None = None, path: Path | None = None) -> bool:
    """1회 사용을 기록한다. 한도를 넘으면 기록하지 않고 False."""
    path = path or config.DAILY_USAGE_PATH
    left = remaining(limit, path)
    if left is not None and left <= 0:
        return False
    data = _load(path)
    today = date.today().isoformat()
    data = {today: data.get(today, 0) + 1}   # 지난 날짜 기록은 버린다
    path.write_text(json.dumps(data), encoding="utf-8")
    return True
