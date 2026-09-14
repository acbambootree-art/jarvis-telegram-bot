"""Checks the Greene class progression rules and quiz-grade parsing.

Run: python3 tests/test_power_laws.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.power_laws import (  # noqa: E402
    BOOKS,
    LAWS,
    PASS_SCORE,
    STRATEGIES,
    _parse_grade,
    new_state,
    record_result,
    review_pick,
    start_class,
)


def demo():
    assert len(LAWS) == 48 and len(set(LAWS)) == 48
    assert len(STRATEGIES) == 33 and len({t for _, t, _ in STRATEGIES}) == 33
    assert BOOKS["48_laws"]["total"] == 48 and BOOKS["33_strategies"]["total"] == 33
    parts = [p for p, _, _ in STRATEGIES]
    order = ["Self-Directed War", "Organizational War", "Defensive War", "Offensive War", "Unconventional War"]
    assert [p for i, p in enumerate(parts) if i == 0 or p != parts[i - 1]] == order

    # First class is attempt 1 and waits for answers.
    st = start_class(new_state())
    assert (st["current"], st["attempt"], st["awaiting"]) == (1, 1, True)

    # Unanswered: the same class comes back as take 2.
    st = start_class(st)
    assert (st["current"], st["attempt"]) == (1, 2)

    # Fail: stays on class 1, remembers what to re-teach, next send is take 3.
    st = record_result(st, 33, PASS_SCORE - 1, "spotting the tells")
    assert (st["current"], st["awaiting"], st["weak_points"]) == (1, False, "spotting the tells")
    assert start_class(st)["attempt"] == 3

    # Pass: class 1 mastered, class 2 unlocked as a fresh attempt 1.
    st = record_result(st, 33, 9)
    assert st["mastered"] == [1] and st["current"] == 2 and st["weak_points"] == ""
    assert start_class(st)["attempt"] == 1
    assert st["scores"]["1"] == 9

    # Best score is kept, not the latest.
    assert record_result({**st, "current": 1}, 33, 3)["scores"]["1"] == 9

    # Review questions only draw on mastered classes other than today's.
    assert review_pick(new_state(), 5) is None
    assert review_pick({"mastered": [1, 2], "current": 2}, 7) == 1

    # Finishing the course.
    st = {**new_state(), "current": 3, "mastered": [1, 2]}
    st = record_result(st, 3, 10)
    assert st["completed"] is True and st["mastered"] == [1, 2, 3]

    # Passing the last unmastered class wraps back to an earlier gap.
    st = record_result({**new_state(), "current": 3, "mastered": [1, 3]}, 3, 8)
    assert st["current"] == 2 and not st["completed"]

    # Grader output parsing.
    assert _parse_grade('noise {"score": 8.4, "per_question": []} noise')["score"] == 8
    assert _parse_grade('{"score": 14}')["score"] == 10
    try:
        _parse_grade("no json here")
        raise AssertionError("should have raised")
    except ValueError:
        pass
    print("ok")


if __name__ == "__main__":
    demo()
