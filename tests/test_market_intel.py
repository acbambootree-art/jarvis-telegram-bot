"""Checks venture query rotation and header stripping.

Run: python3 tests/test_market_intel.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.market_intel import (  # noqa: E402
    _VENTURES,
    _build_queries,
    _strip_leading_header,
)


def demo():
    start = datetime(2026, 9, 13)
    q = _build_queries(start)
    assert list(q) == list(_VENTURES)
    assert all(v.endswith("September 2026") for v in q.values())
    # Every angle of every venture gets used within a week
    week = [_build_queries(start + timedelta(days=i)) for i in range(7)]
    for venture, angles in _VENTURES.items():
        used = {d[venture].rsplit(" ", 2)[0] for d in week}
        assert used == set(angles), venture

    body = "*🤖 AI automation consulting*\n- item"
    copied = "📈 *Market Intel — Sun 13 Sep*\n_Deep Tech wildcard · positive-sum_\n\n" + body
    assert _strip_leading_header(copied) == body
    assert _strip_leading_header("📈 Market Intel — Sun 13 Sep\n\n" + body) == body
    assert _strip_leading_header(body) == body
    print("ok")


if __name__ == "__main__":
    demo()
