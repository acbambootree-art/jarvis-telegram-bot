"""Robert Greene coach: The 48 Laws of Power and The 33 Strategies of War.

Public functions:
- get_daily_law_lesson(user_id) — fired at 09:00 SGT. Walks the 48 laws one
  per day in order, then repeats. Ties the law to the user's real day.
- get_daily_strategy_lesson(user_id) — fired at 18:00 SGT. Same idea for the
  33 strategies of war.
- analyze_power_scenario(user_id, scenario) — Jarvis tool. Reads a situation
  the user posts, names the laws and strategies in play, recommends a play,
  and grades any move the user already made.
"""

import asyncio
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import anthropic
import structlog

from app.config import settings
from app.core.claude_helpers import extract_text
from app.services import facts
from app.services.coach import _gather_user_context

logger = structlog.get_logger()

_claude = anthropic.Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None
_MODEL = "claude-sonnet-5"

LAWS = [
    "Never Outshine the Master",
    "Never Put Too Much Trust in Friends, Learn How to Use Enemies",
    "Conceal Your Intentions",
    "Always Say Less Than Necessary",
    "So Much Depends on Reputation — Guard It with Your Life",
    "Court Attention at All Cost",
    "Get Others to Do the Work for You, but Always Take the Credit",
    "Make Other People Come to You — Use Bait if Necessary",
    "Win Through Your Actions, Never Through Argument",
    "Infection: Avoid the Unhappy and Unlucky",
    "Learn to Keep People Dependent on You",
    "Use Selective Honesty and Generosity to Disarm Your Victim",
    "When Asking for Help, Appeal to People's Self-Interest, Never to Their Mercy or Gratitude",
    "Pose as a Friend, Work as a Spy",
    "Crush Your Enemy Totally",
    "Use Absence to Increase Respect and Honor",
    "Keep Others in Suspended Terror: Cultivate an Air of Unpredictability",
    "Do Not Build Fortresses to Protect Yourself — Isolation Is Dangerous",
    "Know Who You're Dealing With — Do Not Offend the Wrong Person",
    "Do Not Commit to Anyone",
    "Play a Sucker to Catch a Sucker — Seem Dumber Than Your Mark",
    "Use the Surrender Tactic: Transform Weakness into Power",
    "Concentrate Your Forces",
    "Play the Perfect Courtier",
    "Re-Create Yourself",
    "Keep Your Hands Clean",
    "Play on People's Need to Believe to Create a Cultlike Following",
    "Enter Action with Boldness",
    "Plan All the Way to the End",
    "Make Your Accomplishments Seem Effortless",
    "Control the Options: Get Others to Play with the Cards You Deal",
    "Play to People's Fantasies",
    "Discover Each Man's Thumbscrew",
    "Be Royal in Your Own Fashion: Act Like a King to Be Treated Like One",
    "Master the Art of Timing",
    "Disdain Things You Cannot Have: Ignoring Them Is the Best Revenge",
    "Create Compelling Spectacles",
    "Think as You Like but Behave Like Others",
    "Stir Up Waters to Catch Fish",
    "Despise the Free Lunch",
    "Avoid Stepping into a Great Man's Shoes",
    "Strike the Shepherd and the Sheep Will Scatter",
    "Work on the Hearts and Minds of Others",
    "Disarm and Infuriate with the Mirror Effect",
    "Preach the Need for Change, but Never Reform Too Much at Once",
    "Never Appear Too Perfect",
    "Do Not Go Past the Mark You Aimed For; In Victory, Learn When to Stop",
    "Assume Formlessness",
]
assert len(LAWS) == 48

# Day 1 of the curriculum. Law N is served on day N, then the cycle repeats.
# ponytail: stateless rotation — a DB-backed "laws covered" counter if the
# user wants to skip/reorder laws.
_CURRICULUM_START = date(2026, 9, 9)

_COACH_VOICE = """You are the user's personal coach on Robert Greene's "The 48 Laws of Power", \
speaking through Telegram. You know the book cold: every law, its historical \
examples (Talleyrand, Bismarck, Louis XIV, the courtiers, the con men), its \
"keys to power", and its REVERSAL — the cases where the law backfires.

Your standard is mastery, not trivia. The user wants to actually USE these \
laws in their career, business and relationships.

Voice: cool, precise, a little Machiavellian, never preachy. No "as an AI". \
Never moralise, but always name the reversal — the price of a law used badly \
is real, and Greene says so himself. Use Telegram Markdown only (*bold*, _italic_)."""


_LESSON_SYSTEM = _COACH_VOICE + """

Each morning you teach ONE law. Structure (exact format):

👑 *Law <N>: <title>*

<2-3 sentences: the law in plain words and WHY it works — the psychology behind it.>

📜 *From history*
<One concrete historical or business example, 2-3 sentences. Name names.>

⚖️ *Reversal*
<When this law fails or should NOT be used. 1-2 sentences.>

🎯 *Apply it today*
<ONE specific move tied to the user's real calendar/tasks/people for today. \
Name the actual meeting or person. If context is empty, give a move for a \
typical workday.>

🧠 *Drill*
<One question or mini-scenario for them to answer back to you, to test \
whether they can spot the law in action.>

Length cap: 280 words."""


_ANALYSIS_SYSTEM = _COACH_VOICE + """

You also know Greene's "The 33 Strategies of War" equally well. The user \
posts a real situation. Analyse it through BOTH books: the 48 laws for power \
and positioning, the 33 strategies for how to fight or avoid the conflict.

Structure (exact format):

♟️ *The board*
<2-3 sentences: who holds power here, what each party actually wants, what \
the user's real position is. Be blunt.>

📖 *Laws and strategies in play*
<3-5 bullets. Each: "*Law N — Title*" or "*Strategy N — Title*", then how it \
applies HERE and who is using it (or failing to). Include ones being used \
AGAINST the user, not only ones they could use. Use both books when both fit.>

🎯 *Recommended play*
<Numbered steps, 2-4 of them. Concrete: what to say, when, to whom. Anchor \
each step to a law or strategy.>

⚠️ *Reversal / risk*
<What could blow up, which law's reversal applies, and the tell that it is \
going wrong.>

If the user describes something they ALREADY did or said, add before the play:

📊 *Your move, graded: <score>/10*
<Which laws or strategies they used well, which they broke, and the one \
change that would have raised the score most.>

Length cap: 350 words. Use the user's persistent facts (people, roles, \
projects) if given — that is what makes this analysis theirs and not a \
book summary."""


# (part, title, subtitle) — the book's five parts in order.
STRATEGIES = [
    ("Self-Directed War", "Declare War on Your Enemies", "The Polarity Strategy"),
    ("Self-Directed War", "Do Not Fight the Last War", "The Guerrilla-War-of-the-Mind Strategy"),
    ("Self-Directed War", "Amidst the Turmoil of Events, Do Not Lose Your Presence of Mind", "The Counterbalance Strategy"),
    ("Self-Directed War", "Create a Sense of Urgency and Desperation", "The Death-Ground Strategy"),
    ("Organizational War", "Avoid the Snares of Groupthink", "The Command-and-Control Strategy"),
    ("Organizational War", "Segment Your Forces", "The Controlled-Chaos Strategy"),
    ("Organizational War", "Transform Your War into a Crusade", "Morale Strategies"),
    ("Defensive War", "Pick Your Battles Carefully", "The Perfect-Economy Strategy"),
    ("Defensive War", "Turn the Tables", "The Counterattack Strategy"),
    ("Defensive War", "Create a Threatening Presence", "Deterrence Strategies"),
    ("Defensive War", "Trade Space for Time", "The Nonengagement Strategy"),
    ("Offensive War", "Lose Battles but Win the War", "Grand Strategy"),
    ("Offensive War", "Know Your Enemy", "The Intelligence Strategy"),
    ("Offensive War", "Overwhelm Resistance with Speed and Suddenness", "The Blitzkrieg Strategy"),
    ("Offensive War", "Control the Dynamic", "Forcing Strategies"),
    ("Offensive War", "Hit Them Where It Hurts", "The Center-of-Gravity Strategy"),
    ("Offensive War", "Defeat Them in Detail", "The Divide-and-Conquer Strategy"),
    ("Offensive War", "Expose and Attack Your Opponent's Soft Flank", "The Turning Strategy"),
    ("Offensive War", "Envelop the Enemy", "The Annihilation Strategy"),
    ("Offensive War", "Maneuver Them into Weakness", "The Ripening-for-the-Sickle Strategy"),
    ("Offensive War", "Negotiate While Advancing", "The Diplomatic-War Strategy"),
    ("Offensive War", "Know How to End Things", "The Exit Strategy"),
    ("Unconventional War", "Weave a Seamless Blend of Fact and Fiction", "Misperception Strategies"),
    ("Unconventional War", "Take the Line of Least Expectation", "The Ordinary-Extraordinary Strategy"),
    ("Unconventional War", "Occupy the Moral High Ground", "The Righteous Strategy"),
    ("Unconventional War", "Deny Them Targets", "The Strategy of the Void"),
    ("Unconventional War", "Seem to Work for the Interests of Others While Furthering Your Own", "The Alliance Strategy"),
    ("Unconventional War", "Give Your Rivals Enough Rope to Hang Themselves", "The One-Upmanship Strategy"),
    ("Unconventional War", "Take Small Bites", "The Fait Accompli Strategy"),
    ("Unconventional War", "Penetrate Their Minds", "Communication Strategies"),
    ("Unconventional War", "Destroy from Within", "The Inner-Front Strategy"),
    ("Unconventional War", "Dominate While Seeming to Submit", "The Passive-Aggression Strategy"),
    ("Unconventional War", "Sow Uncertainty and Panic Through Acts of Terror", "The Chain-Reaction Strategy"),
]
assert len(STRATEGIES) == 33

_STRATEGY_START = date(2026, 9, 14)

_WAR_VOICE = """You are the user's personal coach on Robert Greene's "The 33 Strategies \
of War", speaking through Telegram. You know the book cold: every strategy, \
its campaigns (Xenophon, Napoleon, Hannibal, Sun Tzu, Musashi, Nelson, Mao, \
Churchill, Roosevelt, Lettow-Vorbeck), and its REVERSAL.

Anchor everything in Greene's six ideals: see things as they are; judge \
people by their actions; depend on your own arms; worship Athena, not Ares; \
elevate yourself above the battlefield; spiritualize your warfare.

The user's battles are office politics, business competition, negotiations, \
betrayal and family pressure, not armies. Translate every strategy to that \
scale. For the dark strategies (deception, terror, passive aggression), teach \
first how to RECOGNISE and DEFEND against them, then the legitimate use.

Voice: calm, strategic, a general briefing an officer. Never preachy. No \
"as an AI". Use Telegram Markdown only (*bold*, _italic_)."""


_WAR_LESSON_SYSTEM = _WAR_VOICE + """

Each evening you teach ONE strategy. Structure (exact format):

⚔️ *Strategy <N>: <title>*
_<subtitle> · Part: <part>_

<2-3 sentences: the strategy in plain words and WHY it works.>

🏛️ *The campaign*
<One concrete historical example from the book, 2-3 sentences. Name names.>

🔄 *Reversal*
<When it fails, or how an opponent uses it on you and how to spot it. 1-2 sentences.>

🎯 *Your battlefield*
<ONE specific move tied to the user's real calendar/tasks/people. Name the \
actual meeting, person or project. If context is empty, give a move for a \
typical workweek.>

🧠 *Drill*
<One short scenario for them to answer back: which strategy would they use, \
and what is their first move?>

Length cap: 300 words."""


def law_for_date(d: date) -> tuple[int, str]:
    """Return (law_number, title) served on date *d*."""
    idx = (d - _CURRICULUM_START).days % 48
    return idx + 1, LAWS[idx]


def strategy_for_date(d: date) -> tuple[int, str, str, str]:
    """Return (strategy_number, title, subtitle, part) served on date *d*."""
    idx = (d - _STRATEGY_START).days % 33
    part, title, subtitle = STRATEGIES[idx]
    return idx + 1, title, subtitle, part


async def _generate_lesson(user_id: UUID, system: str, teach: str, log_key: str) -> dict:
    """Shared body of the daily lessons: user context in, one lesson out."""
    if not _claude:
        return {"success": False, "error": "claude not configured"}

    today = datetime.now(ZoneInfo(settings.default_timezone)).date()
    try:
        context, facts_digest = await asyncio.gather(
            _gather_user_context(user_id),
            facts.load_facts_for_prompt(user_id),
        )
    except Exception:
        context, facts_digest = "", ""

    user_prompt = (
        f"Today is {today.strftime('%A %d %B %Y')}. {teach}\n\n"
        f"User's day:\n{context}\n\n"
        f"What you know about them:\n{facts_digest or '(nothing yet)'}\n\n"
        "Write the lesson in the exact format from the system prompt."
    )
    try:
        msg = _claude.messages.create(
            model=_MODEL,
            max_tokens=1000,
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return {"success": True, "message": extract_text(msg) or ""}
    except Exception as e:
        logger.error(log_key, error=str(e))
        return {"success": False, "error": str(e)}


async def get_daily_law_lesson(user_id: UUID) -> dict:
    """Generate today's 09:00 law lesson."""
    number, title = law_for_date(datetime.now(ZoneInfo(settings.default_timezone)).date())
    data = await _generate_lesson(
        user_id, _LESSON_SYSTEM, f"Teach Law {number}: {title}.", "power_law_lesson_failed"
    )
    return {**data, "law": number, "title": title}


async def get_daily_strategy_lesson(user_id: UUID) -> dict:
    """Generate today's 18:00 33-Strategies-of-War lesson."""
    number, title, subtitle, part = strategy_for_date(
        datetime.now(ZoneInfo(settings.default_timezone)).date()
    )
    data = await _generate_lesson(
        user_id,
        _WAR_LESSON_SYSTEM,
        f"Teach Strategy {number}: {title} ({subtitle}), from the part on {part}.",
        "war_strategy_lesson_failed",
    )
    return {**data, "strategy": number, "title": title}


async def analyze_power_scenario(user_id: UUID, scenario: str, goal: str = "") -> dict:
    """Analyse a situation through the 48 laws and 33 strategies (Jarvis tool)."""
    if not _claude:
        return {"success": False, "error": "claude not configured"}
    if not scenario.strip():
        return {"success": False, "error": "scenario is empty"}

    try:
        facts_digest = await facts.load_facts_for_prompt(user_id)
    except Exception:
        facts_digest = ""

    user_prompt = (
        f"Situation:\n{scenario}\n\n"
        + (f"What the user wants out of it: {goal}\n\n" if goal else "")
        + f"What you know about them:\n{facts_digest or '(nothing yet)'}\n\n"
        "Analyse it in the exact format from the system prompt."
    )

    try:
        msg = _claude.messages.create(
            model=_MODEL,
            max_tokens=1400,
            system=_ANALYSIS_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return {"success": True, "analysis": extract_text(msg) or ""}
    except Exception as e:
        logger.error("power_scenario_failed", error=str(e))
        return {"success": False, "error": str(e)}


def format_lesson_for_telegram(data: dict) -> str:
    if not data.get("success"):
        book = "33 Strategies" if "strategy" in data else "48 Laws"
        return f"⚠️ {book} lesson failed: {data.get('error', 'unknown error')}"
    return data["message"]
