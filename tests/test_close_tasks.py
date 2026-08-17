"""Checks the letter/title resolution behind the close_tasks tool.

Run: python3 tests/test_close_tasks.py
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.tasks import resolve_refs  # noqa: E402


def T(i, title):
    return SimpleNamespace(id=i, title=title)


OPEN = [T(1, "Call the bank"), T(2, "File taxes"), T(3, "Book flights")]


def titles(tasks):
    return [t.title for t in tasks]


def demo():
    # letters map by position, whatever wrapping the user typed around them
    for ref, expected in [
        ("A", "Call the bank"),
        ("a", "Call the bank"),
        ("A.)", "Call the bank"),
        ("B)", "File taxes"),
        ("task b", "File taxes"),
        ("Task C.", "Book flights"),
    ]:
        m, u = resolve_refs(OPEN, [ref])
        assert not u, (ref, u)
        assert titles(m) == [expected], (ref, titles(m))

    # several letters at once, order preserved
    m, u = resolve_refs(OPEN, ["A", "C"])
    assert titles(m) == ["Call the bank", "Book flights"] and not u

    # title fragments work, and are case insensitive
    m, u = resolve_refs(OPEN, ["bank"])
    assert titles(m) == ["Call the bank"] and not u

    # out of range letter is reported, not silently applied to the wrong task
    m, u = resolve_refs(OPEN, ["Z"])
    assert m == [] and u[0]["reason"] == "no open task matches"

    # a good ref still lands even when another ref in the batch fails
    m, u = resolve_refs(OPEN, ["A", "Z"])
    assert titles(m) == ["Call the bank"] and len(u) == 1

    # ambiguous fragment refuses rather than guessing
    m, u = resolve_refs([T(1, "Book flights"), T(2, "Book hotel")], ["book"])
    assert m == [] and u[0]["reason"] == "matches several open tasks"
    assert set(u[0]["candidates"]) == {"Book flights", "Book hotel"}

    # same task named twice is only closed once
    m, u = resolve_refs(OPEN, ["A", "bank"])
    assert titles(m) == ["Call the bank"] and not u

    # empty list is a no-op, not a crash
    assert resolve_refs(OPEN, []) == ([], [])
    assert resolve_refs([], ["A"])[0] == []

    print("close_tasks resolution: all checks passed")


if __name__ == "__main__":
    demo()
