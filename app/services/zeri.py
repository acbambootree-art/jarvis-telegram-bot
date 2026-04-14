"""Ze Ri (择日) — Chinese almanac / Tong Shu daily reading via cnlunar."""

from datetime import datetime
from zoneinfo import ZoneInfo

import cnlunar
import structlog

from app.config import settings

logger = structlog.get_logger()


def get_daily_almanac(date_str: str | None = None) -> dict:
    """Return today's Chinese almanac data for the daily briefing.

    Parameters
    ----------
    date_str : str, optional
        Date in YYYY-MM-DD format.  Defaults to today in configured timezone.

    Returns
    -------
    dict with keys: success, date, lunar_date, day_ganzi, year_ganzi,
        month_ganzi, auspicious, inauspicious, day_officer, stars28,
        lucky_directions, zodiac_clash
    """
    try:
        tz = ZoneInfo(settings.default_timezone)
        if date_str:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        else:
            dt = datetime.now(tz).replace(tzinfo=None)

        # cnlunar requires naive datetime
        a = cnlunar.Lunar(dt, godType="8char")

        # Lunar date
        lunar_month = a.lunarMonthCn  # e.g. 三月
        lunar_day = a.lunarDayCn      # e.g. 十七

        # Gan-Zhi (天干地支)
        year_gz = a.year8Char         # e.g. 丙午
        month_gz = a.month8Char       # e.g. 壬辰
        day_gz = a.day8Char           # e.g. 戊午

        # Auspicious / Inauspicious activities
        good = a.goodThing or []
        bad = a.badThing or []

        # 12-day officer (建除十二神)
        day_officer = a.today12DayOfficer  # e.g. 满, 建, 除 ...

        # 28 mansions
        stars28 = a.get_the28Stars()  # e.g. 室火猪

        # Lucky god directions
        lucky_dirs = a.get_luckyGodsDirection()  # dict

        # Zodiac clash
        zodiac_clash = a.chineseZodiacClash  # e.g. 冲鼠

        return {
            "success": True,
            "date": dt.strftime("%Y-%m-%d"),
            "lunar_date": f"农历{lunar_month}{lunar_day}",
            "year_ganzi": year_gz,
            "month_ganzi": month_gz,
            "day_ganzi": day_gz,
            "auspicious": good,
            "inauspicious": bad,
            "day_officer": day_officer,
            "stars28": stars28,
            "lucky_directions": lucky_dirs,
            "zodiac_clash": zodiac_clash,
        }
    except Exception as e:
        logger.error("zeri_almanac_error", error=str(e))
        return {"success": False, "error": str(e)}


def format_almanac_for_briefing(data: dict) -> str:
    """Format almanac data into a compact string for the daily briefing."""
    if not data.get("success"):
        return ""

    parts = []

    # Lunar date + Gan-Zhi
    parts.append(f"{data['lunar_date']}  {data['day_ganzi']}日")

    # Day officer
    if data.get("day_officer"):
        parts.append(f"十二建除: {data['day_officer']}")

    # Auspicious
    good = data.get("auspicious", [])
    if good:
        good_str = "、".join(good[:8])
        parts.append(f"宜: {good_str}")

    # Inauspicious
    bad = data.get("inauspicious", [])
    if bad:
        bad_str = "、".join(bad[:8])
        parts.append(f"忌: {bad_str}")

    # Zodiac clash
    if data.get("zodiac_clash"):
        parts.append(f"冲煞: {data['zodiac_clash']}")

    return "\n  ".join(parts)
