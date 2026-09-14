"""Checks the daily law rotation.

Run: python3 tests/test_power_laws.py
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.power_laws import (  # noqa: E402
    LAWS,
    STRATEGIES,
    _CURRICULUM_START,
    _STRATEGY_START,
    law_for_date,
    strategy_for_date,
)


def demo():
    assert len(LAWS) == 48 and len(set(LAWS)) == 48
    assert law_for_date(_CURRICULUM_START) == (1, LAWS[0])
    assert law_for_date(_CURRICULUM_START + timedelta(days=47)) == (48, LAWS[47])
    assert law_for_date(_CURRICULUM_START + timedelta(days=48)) == (1, LAWS[0])
    # 48 consecutive days cover every law exactly once
    served = {law_for_date(_CURRICULUM_START + timedelta(days=i))[0] for i in range(48)}
    assert served == set(range(1, 49))

    # 33 strategies: same rotation, parts in book order
    assert len(STRATEGIES) == 33 and len({t for _, t, _ in STRATEGIES}) == 33
    assert strategy_for_date(_STRATEGY_START)[:2] == (1, "Declare War on Your Enemies")
    assert strategy_for_date(_STRATEGY_START + timedelta(days=10))[1] == "Trade Space for Time"
    assert strategy_for_date(_STRATEGY_START + timedelta(days=33))[0] == 1
    served = {strategy_for_date(_STRATEGY_START + timedelta(days=i))[0] for i in range(33)}
    assert served == set(range(1, 34))
    parts = [p for p, _, _ in STRATEGIES]
    order = ["Self-Directed War", "Organizational War", "Defensive War", "Offensive War", "Unconventional War"]
    assert [p for i, p in enumerate(parts) if i == 0 or p != parts[i - 1]] == order
    print("ok")


if __name__ == "__main__":
    demo()
