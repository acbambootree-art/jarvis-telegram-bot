"""Ze Ri (择日) — Chinese almanac / Tong Shu daily reading via cnlunar."""

from datetime import datetime
from zoneinfo import ZoneInfo

import cnlunar
import structlog

from app.config import settings

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Translation tables
# ---------------------------------------------------------------------------

_ACTIVITY_EN = {
    "祭祀": "Worship", "祈福": "Pray", "求嗣": "Pray for heirs",
    "开光": "Consecrate", "出行": "Travel", "嫁娶": "Marriage",
    "结婚姻": "Marriage", "纳采": "Engagement", "订盟": "Betroth",
    "安床": "Set up bed", "移徙": "Move house", "入宅": "Move in",
    "开市": "Open business", "交易": "Trade", "立券": "Sign contracts",
    "纳财": "Receive money", "动土": "Break ground", "修造": "Renovate",
    "竖柱": "Erect pillars", "上梁": "Raise beams", "盖屋": "Build roof",
    "安门": "Install doors", "造庙": "Build temple", "安葬": "Burial",
    "破土": "Dig earth", "启钻": "Open coffin", "除服": "End mourning",
    "成服": "Begin mourning", "修坟": "Repair tomb", "立碑": "Erect tombstone",
    "栽种": "Planting", "牧养": "Animal husbandry", "纳畜": "Acquire livestock",
    "伐木": "Fell trees", "作灶": "Build stove", "掘井": "Dig well",
    "经络": "Weaving", "捕捉": "Hunting", "取渔": "Fishing",
    "放水": "Release water", "求医": "See doctor", "裁衣": "Tailoring",
    "冠笄": "Coming-of-age", "会友": "Meet friends", "进人口": "Hire help",
    "解除": "Cleansing", "沐浴": "Bathing", "扫舍": "House cleaning",
    "塞穴": "Seal holes", "畋猎": "Hunting", "结网": "Make nets",
    "整手足甲": "Grooming", "剃头": "Haircut", "求财": "Seek wealth",
    "宴会": "Banqueting", "赴任": "Take office", "诸事不宜": "Nothing auspicious",
    "余事勿取": "Avoid other matters",
}

_OFFICER_EN = {
    "建": "Establish", "除": "Remove", "满": "Full", "平": "Balance",
    "定": "Settle", "执": "Grasp", "破": "Break", "危": "Danger",
    "成": "Succeed", "收": "Receive", "开": "Open", "闭": "Close",
}

_STEM_EN = {
    "甲": "Jia", "乙": "Yi", "丙": "Bing", "丁": "Ding", "戊": "Wu",
    "己": "Ji", "庚": "Geng", "辛": "Xin", "壬": "Ren", "癸": "Gui",
}

_BRANCH_EN = {
    "子": "Zi", "丑": "Chou", "寅": "Yin", "卯": "Mao", "辰": "Chen",
    "巳": "Si", "午": "Wu", "未": "Wei", "申": "Shen", "酉": "You",
    "戌": "Xu", "亥": "Hai",
}

_ZODIAC_EN = {
    "鼠": "Rat", "牛": "Ox", "虎": "Tiger", "兔": "Rabbit",
    "龙": "Dragon", "蛇": "Snake", "马": "Horse", "羊": "Goat",
    "猴": "Monkey", "鸡": "Rooster", "狗": "Dog", "猪": "Pig",
}


def _translate_activity(activity: str) -> str:
    return _ACTIVITY_EN.get(activity, activity)


def _translate_ganzi(gz: str) -> str:
    if len(gz) == 2:
        return f"{_STEM_EN.get(gz[0], gz[0])}-{_BRANCH_EN.get(gz[1], gz[1])}"
    return gz


def _translate_officer(officer: str) -> str:
    return _OFFICER_EN.get(officer, officer)


def _translate_clash(clash: str) -> str:
    result = clash
    for cn, en in _ZODIAC_EN.items():
        result = result.replace(cn, en)
    result = result.replace("日冲", " day clashes with ")
    return result


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
    """Format almanac data into a compact string for the daily briefing.

    Outputs Chinese first, then English translation below.
    """
    if not data.get("success"):
        return ""

    cn = []
    en = []

    # Lunar date + Gan-Zhi
    day_gz = data["day_ganzi"]
    cn.append(f"{data['lunar_date']}  {day_gz}日")
    en.append(f"Lunar: {data['lunar_date']}  Day: {_translate_ganzi(day_gz)}")

    # Day officer
    officer = data.get("day_officer", "")
    if officer:
        cn.append(f"十二建除: {officer}")
        en.append(f"Day Officer: {_translate_officer(officer)} ({officer})")

    # Auspicious
    good = data.get("auspicious", [])
    if good:
        cn.append(f"宜: {'、'.join(good[:8])}")
        en.append(f"Do: {', '.join(_translate_activity(g) for g in good[:8])}")

    # Inauspicious
    bad = data.get("inauspicious", [])
    if bad:
        cn.append(f"忌: {'、'.join(bad[:8])}")
        en.append(f"Avoid: {', '.join(_translate_activity(b) for b in bad[:8])}")

    # Zodiac clash
    clash = data.get("zodiac_clash", "")
    if clash:
        cn.append(f"冲煞: {clash}")
        en.append(f"Clash: {_translate_clash(clash)}")

    return "\n  ".join(cn) + "\n\n  " + "\n  ".join(en)
