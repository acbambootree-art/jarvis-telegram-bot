"""Ze Ri (择日) — Chinese almanac / Tong Shu daily reading via cnlunar.

Provides three layers:
1. Generic almanac (everybody's Tong Shu)
2. Personalised flags (rule-based analysis vs user's Bazi)
3. Claude-interpreted personalised reading
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic
import cnlunar
import structlog

from app.config import settings

logger = structlog.get_logger()

_claude = anthropic.Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None
_CLAUDE_MODEL = "claude-sonnet-4-20250514"

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

# ---------------------------------------------------------------------------
# Bazi relationship constants (stems / branches)
# ---------------------------------------------------------------------------

# Stem five elements (yin/yang + element)
_STEM_ELEMENT = {
    "甲": ("yang", "wood"), "乙": ("yin", "wood"),
    "丙": ("yang", "fire"), "丁": ("yin", "fire"),
    "戊": ("yang", "earth"), "己": ("yin", "earth"),
    "庚": ("yang", "metal"), "辛": ("yin", "metal"),
    "壬": ("yang", "water"), "癸": ("yin", "water"),
}

# Branch primary element
_BRANCH_ELEMENT = {
    "子": "water", "丑": "earth", "寅": "wood", "卯": "wood",
    "辰": "earth", "巳": "fire", "午": "fire", "未": "earth",
    "申": "metal", "酉": "metal", "戌": "earth", "亥": "water",
}

# Stem combinations (天干五合) -> transformed element
_STEM_COMBINE = {
    frozenset(["甲", "己"]): "earth",
    frozenset(["乙", "庚"]): "metal",
    frozenset(["丙", "辛"]): "water",
    frozenset(["丁", "壬"]): "wood",
    frozenset(["戊", "癸"]): "fire",
}

# Stem clashes (天干相冲)
_STEM_CLASH = {
    frozenset(["甲", "庚"]), frozenset(["乙", "辛"]),
    frozenset(["丙", "壬"]), frozenset(["丁", "癸"]),
}

# Branch six clashes (地支六冲)
_BRANCH_CLASH = {
    frozenset(["子", "午"]), frozenset(["丑", "未"]),
    frozenset(["寅", "申"]), frozenset(["卯", "酉"]),
    frozenset(["辰", "戌"]), frozenset(["巳", "亥"]),
}

# Branch six combinations (地支六合)
_BRANCH_COMBINE = {
    frozenset(["子", "丑"]): "earth",
    frozenset(["寅", "亥"]): "wood",
    frozenset(["卯", "戌"]): "fire",
    frozenset(["辰", "酉"]): "metal",
    frozenset(["巳", "申"]): "water",
    frozenset(["午", "未"]): "fire",  # 午未合化no transform, treat as fire/earth
}

# Branch six harms (地支六害)
_BRANCH_HARM = {
    frozenset(["子", "未"]), frozenset(["丑", "午"]),
    frozenset(["寅", "巳"]), frozenset(["卯", "辰"]),
    frozenset(["申", "亥"]), frozenset(["酉", "戌"]),
}

# Branch punishments (地支相刑) - simplified pairs
_BRANCH_PUNISH = {
    frozenset(["寅", "巳"]), frozenset(["巳", "申"]), frozenset(["寅", "申"]),
    frozenset(["丑", "戌"]), frozenset(["戌", "未"]), frozenset(["丑", "未"]),
    frozenset(["子", "卯"]),
}

_PILLAR_LABELS_CN = {"year": "年柱", "month": "月柱", "day": "日柱", "hour": "时柱"}
_PILLAR_LABELS_EN = {"year": "Year", "month": "Month", "day": "Day", "hour": "Hour"}


# ---------------------------------------------------------------------------
# User Bazi (computed once)
# ---------------------------------------------------------------------------

def _compute_user_bazi() -> dict:
    try:
        dt = datetime(
            settings.ziwei_birth_year,
            settings.ziwei_birth_month,
            settings.ziwei_birth_day,
            settings.ziwei_birth_hour,
            settings.ziwei_birth_minute,
        )
        a = cnlunar.Lunar(dt, godType="8char")
        return {
            "year": a.year8Char,
            "month": a.month8Char,
            "day": a.day8Char,
            "hour": a.twohour8Char,
            "day_master_stem": a.day8Char[0],
            "day_master_element": _STEM_ELEMENT[a.day8Char[0]][1],
        }
    except Exception as e:
        logger.error("bazi_compute_error", error=str(e))
        return {}


_USER_BAZI = _compute_user_bazi()


# ---------------------------------------------------------------------------
# Translation helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Personalised (Level 1) — rule-based flags
# ---------------------------------------------------------------------------

def _analyse_day_vs_bazi(day_gz: str) -> dict:
    """Compare today's day pillar against the user's Bazi and return flags.

    Each flag has Chinese and English labels plus a severity:
    - favourable: combine (合) forming useful element
    - unfavourable: clash (冲), harm (害), punish (刑)
    - neutral: plain combinations
    """
    if not _USER_BAZI:
        return {"flags": [], "net": "neutral"}

    today_stem, today_branch = day_gz[0], day_gz[1]
    flags = []
    positives = 0
    negatives = 0

    for pkey in ("year", "month", "day", "hour"):
        pillar = _USER_BAZI[pkey]
        p_stem, p_branch = pillar[0], pillar[1]
        p_label_cn = _PILLAR_LABELS_CN[pkey]
        p_label_en = _PILLAR_LABELS_EN[pkey]

        # Stem combinations
        combo_el = _STEM_COMBINE.get(frozenset([today_stem, p_stem]))
        if combo_el:
            flags.append({
                "cn": f"{today_stem}{p_stem}合化{_element_cn(combo_el)} ({p_label_cn})",
                "en": f"Stem combine {_translate_ganzi(today_stem+p_stem).replace('-','+')} → {combo_el} ({p_label_en})",
                "kind": "combine",
            })
            positives += 1

        # Stem clashes
        if frozenset([today_stem, p_stem]) in _STEM_CLASH:
            flags.append({
                "cn": f"{today_stem}{p_stem}相冲 ({p_label_cn})",
                "en": f"Stem clash {today_stem}↔{p_stem} ({p_label_en})",
                "kind": "clash",
            })
            negatives += 1

        # Branch clashes
        if frozenset([today_branch, p_branch]) in _BRANCH_CLASH:
            flags.append({
                "cn": f"{today_branch}{p_branch}相冲 ({p_label_cn})",
                "en": f"Branch clash {today_branch}↔{p_branch} ({p_label_en})",
                "kind": "clash",
            })
            negatives += 2 if pkey == "day" else 1

        # Branch combinations
        bcombo = _BRANCH_COMBINE.get(frozenset([today_branch, p_branch]))
        if bcombo:
            flags.append({
                "cn": f"{today_branch}{p_branch}六合 ({p_label_cn})",
                "en": f"Branch combine {today_branch}+{p_branch} ({p_label_en})",
                "kind": "combine",
            })
            positives += 1

        # Branch harm
        if frozenset([today_branch, p_branch]) in _BRANCH_HARM:
            flags.append({
                "cn": f"{today_branch}{p_branch}相害 ({p_label_cn})",
                "en": f"Branch harm {today_branch}↔{p_branch} ({p_label_en})",
                "kind": "harm",
            })
            negatives += 1

        # Branch punish
        if today_branch != p_branch and frozenset([today_branch, p_branch]) in _BRANCH_PUNISH:
            flags.append({
                "cn": f"{today_branch}{p_branch}相刑 ({p_label_cn})",
                "en": f"Branch punish {today_branch}↔{p_branch} ({p_label_en})",
                "kind": "punish",
            })
            negatives += 1

    if negatives > positives:
        net = "unfavourable"
    elif positives > negatives:
        net = "favourable"
    else:
        net = "neutral"

    return {"flags": flags, "net": net, "positives": positives, "negatives": negatives}


def _element_cn(el: str) -> str:
    return {"wood": "木", "fire": "火", "earth": "土", "metal": "金", "water": "水"}.get(el, el)


# ---------------------------------------------------------------------------
# Personalised (Level 2) — Claude interpretation
# ---------------------------------------------------------------------------

_PERSONAL_SYSTEM = """You are a Ba Zi (八字) expert giving a very short personalised daily Ze Ri reading.

Given the user's birth chart and today's day pillar plus the computed relationships, write a 2-3 sentence reading in English that:
1. States whether today is favourable, neutral, or challenging for this person specifically
2. Names ONE practical thing to prioritise today based on their chart
3. Names ONE thing to avoid

Keep under 80 words. Be direct and practical — no flowery language. No disclaimers.
"""


def _claude_personal_reading(day_gz: str, analysis: dict, generic_good: list, generic_bad: list) -> str:
    if not _claude or not _USER_BAZI:
        return ""

    flags_desc = "; ".join(f["cn"] for f in analysis["flags"]) or "no special relationships"
    prompt = (
        f"User's Bazi:\n"
        f"  Year: {_USER_BAZI['year']}\n"
        f"  Month: {_USER_BAZI['month']}\n"
        f"  Day: {_USER_BAZI['day']} (Day Master: {_USER_BAZI['day_master_stem']} / {_USER_BAZI['day_master_element']})\n"
        f"  Hour: {_USER_BAZI['hour']}\n\n"
        f"Today's day pillar: {day_gz}\n"
        f"Computed relationships vs user's Bazi: {flags_desc}\n"
        f"Net effect: {analysis['net']} (positives={analysis['positives']}, negatives={analysis['negatives']})\n"
        f"Generic almanac Do: {', '.join(generic_good[:5]) or 'none'}\n"
        f"Generic almanac Avoid: {', '.join(generic_bad[:5]) or 'none'}\n\n"
        "Give the personalised 2-3 sentence reading now."
    )
    try:
        msg = _claude.messages.create(
            model=_CLAUDE_MODEL,
            max_tokens=200,
            system=_PERSONAL_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip() if msg.content else ""
    except Exception as e:
        logger.error("zeri_claude_error", error=str(e))
        return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_daily_almanac(date_str: str | None = None) -> dict:
    """Return today's Chinese almanac with personalised Bazi analysis."""
    try:
        tz = ZoneInfo(settings.default_timezone)
        if date_str:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        else:
            dt = datetime.now(tz).replace(tzinfo=None)

        a = cnlunar.Lunar(dt, godType="8char")

        day_gz = a.day8Char
        good = a.goodThing or []
        bad = a.badThing or []

        # Level 1: rule-based personal analysis
        analysis = _analyse_day_vs_bazi(day_gz)

        # Level 2: Claude interpretation
        reading = _claude_personal_reading(day_gz, analysis, good, bad)

        return {
            "success": True,
            "date": dt.strftime("%Y-%m-%d"),
            "lunar_date": f"农历{a.lunarMonthCn}{a.lunarDayCn}",
            "year_ganzi": a.year8Char,
            "month_ganzi": a.month8Char,
            "day_ganzi": day_gz,
            "auspicious": good,
            "inauspicious": bad,
            "day_officer": a.today12DayOfficer,
            "stars28": a.get_the28Stars(),
            "lucky_directions": a.get_luckyGodsDirection(),
            "zodiac_clash": a.chineseZodiacClash,
            "personal_flags": analysis["flags"],
            "personal_net": analysis["net"],
            "personal_reading": reading,
            "user_bazi": _USER_BAZI,
        }
    except Exception as e:
        logger.error("zeri_almanac_error", error=str(e))
        return {"success": False, "error": str(e)}


def format_almanac_for_briefing(data: dict) -> str:
    """Format almanac data into a compact bilingual briefing string."""
    if not data.get("success"):
        return ""

    cn, en = [], []

    # Lunar date + Day Gan-Zhi
    day_gz = data["day_ganzi"]
    cn.append(f"{data['lunar_date']}  {day_gz}日")
    en.append(f"Lunar: {data['lunar_date']}  Day: {_translate_ganzi(day_gz)}")

    # Day officer
    officer = data.get("day_officer", "")
    if officer:
        cn.append(f"十二建除: {officer}")
        en.append(f"Day Officer: {_translate_officer(officer)} ({officer})")

    # Auspicious / Inauspicious
    good = data.get("auspicious", [])
    if good:
        cn.append(f"宜: {'、'.join(good[:8])}")
        en.append(f"Do: {', '.join(_translate_activity(g) for g in good[:8])}")
    bad = data.get("inauspicious", [])
    if bad:
        cn.append(f"忌: {'、'.join(bad[:8])}")
        en.append(f"Avoid: {', '.join(_translate_activity(b) for b in bad[:8])}")

    clash = data.get("zodiac_clash", "")
    if clash:
        cn.append(f"冲煞: {clash}")
        en.append(f"Clash: {_translate_clash(clash)}")

    base = "\n  ".join(cn) + "\n\n  " + "\n  ".join(en)

    # Personalised section (Level 1 + Level 2)
    flags = data.get("personal_flags", [])
    net = data.get("personal_net", "neutral")
    reading = data.get("personal_reading", "")

    net_icon = {"favourable": "🟢", "unfavourable": "🔴", "neutral": "🟡"}.get(net, "🟡")
    personal_lines = [f"\n\n  — *For You (八字)* — {net_icon} {net.title()}"]
    if flags:
        flag_cn = "、".join(f["cn"] for f in flags[:4]) or "无特殊关系"
        flag_en = "; ".join(f["en"] for f in flags[:4]) or "no special relationships"
        personal_lines.append(f"  个人: {flag_cn}")
        personal_lines.append(f"  Personal: {flag_en}")
    else:
        personal_lines.append("  Personal: no special relationships with your Bazi today")
    if reading:
        personal_lines.append(f"  💬 {reading}")

    return base + "\n".join(personal_lines)
