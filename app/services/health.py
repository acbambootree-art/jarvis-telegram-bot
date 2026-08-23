from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import dateparser
import structlog

from app.config import settings
from app.db.database import async_session
from app.db.repositories import HealthMetricRepository

logger = structlog.get_logger()

# Default units per metric type
DEFAULT_UNITS = {
    "steps": "steps",
    "weight": "kg",
    "sleep": "hours",
    "heart_rate": "bpm",
    "calories": "kcal",
    "distance": "km",
    "water": "ml",
    "blood_pressure_systolic": "mmHg",
    "blood_pressure_diastolic": "mmHg",
    "body_fat": "%",
}


async def log_health_metric(
    user_id: UUID,
    metric_type: str,
    value: float,
    unit: str = "",
    notes: str = "",
    recorded_at: str = None,
) -> dict:
    metric_type = metric_type.lower().replace(" ", "_")
    if not unit:
        unit = DEFAULT_UNITS.get(metric_type, "")

    parsed_date = None
    if recorded_at:
        parsed_date = dateparser.parse(recorded_at)

    async with async_session() as session:
        repo = HealthMetricRepository(session)
        metric = await repo.create(
            user_id=user_id,
            metric_type=metric_type,
            value=Decimal(str(value)),
            unit=unit,
            notes=notes,
            recorded_at=parsed_date,
        )

    return {
        "success": True,
        "metric_id": str(metric.id),
        "metric_type": metric.metric_type,
        "value": float(metric.value),
        "unit": metric.unit,
        "recorded_at": metric.recorded_at.isoformat() if metric.recorded_at else None,
    }


async def get_health_summary(
    user_id: UUID,
    metric_type: str,
    period: str = "this_week",
    start_date: str = None,
    end_date: str = None,
) -> dict:
    tz = ZoneInfo(settings.default_timezone)
    now = datetime.now(tz)
    metric_type = metric_type.lower().replace(" ", "_")

    if period == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now
    elif period == "this_week":
        start = now - timedelta(days=now.weekday())
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now
    elif period == "this_month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = now
    elif period == "last_month":
        first_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = first_of_month - timedelta(seconds=1)
        start = end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period == "custom" and start_date and end_date:
        start = dateparser.parse(start_date, settings={"TIMEZONE": settings.default_timezone, "RETURN_AS_TIMEZONE_AWARE": True})
        end = dateparser.parse(end_date, settings={"TIMEZONE": settings.default_timezone, "RETURN_AS_TIMEZONE_AWARE": True})
    else:
        start = now - timedelta(days=now.weekday())
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now

    async with async_session() as session:
        repo = HealthMetricRepository(session)
        summary = await repo.get_summary(user_id, metric_type, start, end)

    unit = DEFAULT_UNITS.get(metric_type, "")

    return {
        "success": True,
        "metric_type": metric_type,
        "period": period,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "unit": unit,
        **summary,
    }


async def list_health_metrics(
    user_id: UUID, metric_type: str = None, limit: int = 10
) -> dict:
    if metric_type:
        metric_type = metric_type.lower().replace(" ", "_")

    async with async_session() as session:
        repo = HealthMetricRepository(session)
        metrics = await repo.list_metrics(user_id, metric_type=metric_type, limit=limit)

    return {
        "success": True,
        "count": len(metrics),
        "metrics": [
            {
                "metric_id": str(m.id),
                "metric_type": m.metric_type,
                "value": float(m.value),
                "unit": m.unit,
                "notes": m.notes,
                "recorded_at": m.recorded_at.isoformat() if m.recorded_at else None,
            }
            for m in metrics
        ],
    }


# --- Apple Health import -------------------------------------------------
# Target for the phone-side sync (iOS Shortcut or Health Auto Export). Two
# payload shapes are reduced to one list of readings:
#
#   flat   {"date": "2026-08-17", "steps": 8432, "sleep": 6.2, ...}
#          any numeric key is a metric; "date" applies to all of them
#   HAE    {"data": {"metrics": [{"name": "step_count", "units": "count",
#                                 "data": [{"date": "...", "qty": 8432}]}]}}
#          Health Auto Export's REST format, verbatim
#
# Rows the sync writes are tagged IMPORT_SOURCE in notes. A re-sync of the
# same metric and day replaces those rows instead of stacking duplicates,
# and never touches readings the user logged by hand.

IMPORT_SOURCE = "apple_health"

# Health Auto Export names -> our metric types. Unlisted names pass through
# as-is (vo2_max, flights_climbed, ...) so nothing is silently dropped.
_HAE_NAMES = {
    "step_count": "steps",
    "heart_rate": "heart_rate",
    "resting_heart_rate": "resting_heart_rate",
    "heart_rate_variability": "hrv",
    "sleep_analysis": "sleep",
    "active_energy": "calories",
    "walking_running_distance": "distance",
    "weight_body_mass": "weight",
    "body_fat_percentage": "body_fat",
    "dietary_water": "water",
    "blood_oxygen_saturation": "blood_oxygen",
}
_HAE_UNITS = {"count": "", "count/min": "bpm", "hr": "hours", "mL": "ml"}


def normalise_readings(payload: dict) -> list[dict]:
    """Reduce either payload shape to [{type, value, unit, date}].

    Pure — see tests/test_health_import.py.
    """
    data = payload.get("data")
    if isinstance(data, dict) and "metrics" in data:
        return _from_hae(data.get("metrics") or [])
    return _from_flat(payload)


def _reading(metric_type, value, unit, date):
    return {"type": metric_type, "value": float(value), "unit": unit, "date": date}


def _from_flat(payload: dict) -> list[dict]:
    date = payload.get("date")
    out = []
    for key, value in payload.items():
        if key == "date" or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        out.append(_reading(key.lower().replace(" ", "_"), value, "", date))
    return out


def _from_hae(metrics: list) -> list[dict]:
    out = []
    for metric in metrics:
        raw_name = metric.get("name") or ""
        name = _HAE_NAMES.get(raw_name, raw_name)
        unit = _HAE_UNITS.get(metric.get("units", ""), metric.get("units", ""))
        for entry in metric.get("data") or []:
            date = entry.get("date")
            if name == "sleep":
                hours = entry.get("totalSleep", entry.get("asleep"))
                if hours is None:
                    stages = [entry.get(s) for s in ("core", "deep", "rem")]
                    hours = sum(s for s in stages if s is not None) if any(s is not None for s in stages) else None
                if hours is not None:
                    out.append(_reading("sleep", hours, unit or "hours", date))
            elif "systolic" in entry or "diastolic" in entry:
                for part in ("systolic", "diastolic"):
                    if entry.get(part) is not None:
                        out.append(_reading(f"blood_pressure_{part}", entry[part], unit, date))
            elif entry.get("qty") is not None:
                out.append(_reading(name, entry["qty"], unit, date))
            elif entry.get("Avg") is not None:      # heart_rate-style Min/Avg/Max
                out.append(_reading(name, entry["Avg"], unit, date))
    return out


def _parse_when(raw, tz: ZoneInfo) -> datetime:
    """HAE sends 'yyyy-MM-dd HH:mm:ss Z'; the Shortcut sends 'yyyy-MM-dd'.
    A bare date lands at local noon so it sits squarely inside its day."""
    if not raw:
        return datetime.now(tz).replace(hour=12, minute=0, second=0, microsecond=0)
    when = dateparser.parse(str(raw), settings={"TIMEZONE": str(tz), "RETURN_AS_TIMEZONE_AWARE": True})
    if when is None:
        raise ValueError(f"unparseable date: {raw!r}")
    when = when.astimezone(tz)  # dateparser hands back its own tz class; keep one type downstream
    if len(str(raw).strip()) == 10:
        when = when.replace(hour=12, minute=0, second=0, microsecond=0)
    return when


async def import_readings(user_id: UUID, payload: dict, timezone: str, dry_run: bool = False) -> dict:
    tz = ZoneInfo(timezone)
    readings = normalise_readings(payload)
    if not readings:
        return {"success": False, "error": "no numeric readings found in payload"}

    rows = []
    for r in readings:
        unit = r["unit"] or DEFAULT_UNITS.get(r["type"], "")
        rows.append((r["type"], r["value"], unit, _parse_when(r["date"], tz)))

    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "would_store": [
                {"type": t, "value": v, "unit": u, "recorded_at": when.isoformat()}
                for t, v, u, when in rows
            ],
        }

    # Group by (metric, local day): clear what the sync wrote for that day,
    # then insert. Hourly HAE rows for one day land as one group, so a
    # re-sync swaps the whole day rather than leaving half of it behind.
    groups: dict[tuple, list] = {}
    for t, v, u, when in rows:
        groups.setdefault((t, when.astimezone(tz).date()), []).append((v, u, when))

    replaced = 0
    async with async_session() as session:
        repo = HealthMetricRepository(session)
        for (metric_type, day), items in groups.items():
            start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
            replaced += await repo.delete_in_window(
                user_id, metric_type, start, start + timedelta(days=1), notes=IMPORT_SOURCE
            )
            for v, u, when in items:
                await repo.create(
                    user_id=user_id, metric_type=metric_type, value=Decimal(str(round(v, 2))),
                    unit=u, notes=IMPORT_SOURCE, recorded_at=when,
                )

    logger.info("health_import", stored=len(rows), replaced=replaced, metrics=sorted({t for t, _ in groups}))
    return {
        "success": True,
        "stored": len(rows),
        "replaced": replaced,
        "metrics": sorted({t for t, _ in groups}),
        "days": sorted({str(d) for _, d in groups}),
    }
