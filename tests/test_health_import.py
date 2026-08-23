"""Checks the payload normalisation behind POST /webhook/health.

Run: .venv/bin/python tests/test_health_import.py
"""
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.health import _parse_when, normalise_readings  # noqa: E402

SG = ZoneInfo("Asia/Singapore")


def by_type(readings):
    return {r["type"]: r for r in readings}


def demo():
    # --- flat shape (what the iOS Shortcut sends) --------------------------
    flat = {"date": "2026-08-17", "steps": 8432, "sleep": 6.2, "heart_rate": 68,
            "Active Energy": 512, "note": "ignored", "ok": True}
    r = by_type(normalise_readings(flat))
    assert set(r) == {"steps", "sleep", "heart_rate", "active_energy"}, set(r)
    assert r["steps"]["value"] == 8432.0 and r["steps"]["date"] == "2026-08-17"
    assert "ok" not in r, "booleans must not be treated as readings"
    assert normalise_readings({"date": "2026-08-17"}) == [], "date alone is not a reading"
    assert normalise_readings({}) == []

    # --- Health Auto Export shape -------------------------------------------
    hae = {"data": {"metrics": [
        {"name": "step_count", "units": "count",
         "data": [{"date": "2026-08-17 00:00:00 +0800", "qty": 8432}]},
        {"name": "heart_rate", "units": "count/min",
         "data": [{"date": "2026-08-17 00:00:00 +0800", "Min": 52, "Avg": 68, "Max": 141}]},
        {"name": "sleep_analysis", "units": "hr",
         "data": [{"date": "2026-08-17 00:00:00 +0800", "totalSleep": 6.4, "asleep": 6.2,
                   "core": 3.9, "deep": 1.1, "rem": 1.4, "inBed": 7.3}]},
        {"name": "blood_pressure", "units": "mmHg",
         "data": [{"date": "2026-08-17 08:00:00 +0800", "systolic": 118, "diastolic": 76}]},
        {"name": "walking_running_distance", "units": "km",
         "data": [{"date": "2026-08-17 00:00:00 +0800", "qty": 5.87}]},
        {"name": "vo2_max", "units": "mL/min·kg",
         "data": [{"date": "2026-08-17 00:00:00 +0800", "qty": 41.2}]},
        {"name": "flights_climbed", "units": "count", "data": []},
    ], "workouts": []}}
    r = by_type(normalise_readings(hae))
    assert r["steps"]["value"] == 8432.0 and r["steps"]["unit"] == "", r["steps"]
    assert r["heart_rate"]["value"] == 68.0 and r["heart_rate"]["unit"] == "bpm", "use Avg, not Min/Max"
    assert r["sleep"]["value"] == 6.4 and r["sleep"]["unit"] == "hours", "prefer totalSleep"
    assert r["blood_pressure_systolic"]["value"] == 118 and r["blood_pressure_diastolic"]["value"] == 76
    assert r["distance"]["value"] == 5.87 and r["distance"]["unit"] == "km"
    assert "vo2_max" in r, "unknown HAE metrics pass through under their own name"
    assert "flights_climbed" not in r, "empty data arrays yield nothing"

    # older HAE builds: no totalSleep, only stages
    stages_only = {"data": {"metrics": [{"name": "sleep_analysis", "units": "hr",
                   "data": [{"date": "2026-08-17 00:00:00 +0800", "core": 3.0, "deep": 1.0, "rem": 1.5}]}]}}
    assert by_type(normalise_readings(stages_only))["sleep"]["value"] == 5.5

    # HAE with "data" present but no "metrics" key is not mistaken for flat
    assert normalise_readings({"data": {"workouts": []}}) == []

    # --- timestamps ---------------------------------------------------------
    w = _parse_when("2026-08-17", SG)
    assert w.isoformat() == "2026-08-17T12:00:00+08:00", ("bare date -> local noon", w.isoformat())
    w = _parse_when("2026-08-17 00:00:00 +0800", SG)
    assert w.isoformat() == "2026-08-17T00:00:00+08:00", w.isoformat()
    w = _parse_when("2026-08-17 00:00:00 +0000", SG)
    assert w.astimezone(SG).hour == 8, "offsets are honoured, not assumed local"
    try:
        _parse_when("not a date", SG)
        raise AssertionError("garbage date should raise")
    except ValueError:
        pass

    print("health import normalisation: all checks passed")


if __name__ == "__main__":
    demo()
