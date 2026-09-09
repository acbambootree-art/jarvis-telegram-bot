"""Checks the daily law rotation.

Run: python3 tests/test_power_laws.py
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.power_laws import LAWS, _CURRICULUM_START, law_for_date  # noqa: E402


def demo():
    assert len(LAWS) == 48 and len(set(LAWS)) == 48
    assert law_for_date(_CURRICULUM_START) == (1, LAWS[0])
    assert law_for_date(_CURRICULUM_START + timedelta(days=47)) == (48, LAWS[47])
    assert law_for_date(_CURRICULUM_START + timedelta(days=48)) == (1, LAWS[0])
    # 48 consecutive days cover every law exactly once
    served = {law_for_date(_CURRICULUM_START + timedelta(days=i))[0] for i in range(48)}
    assert served == set(range(1, 49))
    print("ok")


if __name__ == "__main__":
    demo()
