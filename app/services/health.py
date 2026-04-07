from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

import dateparser
import structlog

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
    now = datetime.utcnow()
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
        start = dateparser.parse(start_date)
        end = dateparser.parse(end_date)
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
