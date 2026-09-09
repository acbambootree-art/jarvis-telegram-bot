"""48 Laws of Power (Robert Greene) coach.

Two public functions:
- get_daily_law_lesson(user_id) — fired at 09:00 SGT. Walks the 48 laws one
  per day in order, then repeats. Ties the law to the user's real day.
- analyze_power_scenario(user_id, scenario) — Jarvis tool. Reads a situation
  the user posts, names the laws in play, recommends a play, and grades any
  move the user already made.
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

The user posts a real situation. Analyse it through the 48 laws.

Structure (exact format):

♟️ *The board*
<2-3 sentences: who holds power here, what each party actually wants, what \
the user's real position is. Be blunt.>

📖 *Laws in play*
<3-4 bullets. Each: "*Law N — Title*: how it applies HERE, and who is using \
it (or failing to)." Include laws being used AGAINST the user, not only ones \
they could use.>

🎯 *Recommended play*
<Numbered steps, 2-4 of them. Concrete: what to say, when, to whom. Anchor \
each step to a law.>

⚠️ *Reversal / risk*
<What could blow up, which law's reversal applies, and the tell that it is \
going wrong.>

If the user describes something they ALREADY did or said, add before the play:

📊 *Your move, graded: <score>/10*
<Which laws they used well, which they broke, and the one change that would \
have raised the score most.>

Length cap: 350 words. Use the user's persistent facts (people, roles, \
projects) if given — that is what makes this analysis theirs and not a \
book summary."""


def law_for_date(d: date) -> tuple[int, str]:
    """Return (law_number, title) served on date *d*."""
    idx = (d - _CURRICULUM_START).days % 48
    return idx + 1, LAWS[idx]


async def get_daily_law_lesson(user_id: UUID) -> dict:
    """Generate today's 09:00 law lesson."""
    if not _claude:
        return {"success": False, "error": "claude not configured"}

    tz = ZoneInfo(settings.default_timezone)
    today = datetime.now(tz).date()
    number, title = law_for_date(today)

    try:
        context, facts_digest = await asyncio.gather(
            _gather_user_context(user_id),
            facts.load_facts_for_prompt(user_id),
        )
    except Exception:
        context, facts_digest = "", ""

    user_prompt = (
        f"Today is {today.strftime('%A %d %B %Y')}. Teach Law {number}: {title}.\n\n"
        f"User's day:\n{context}\n\n"
        f"What you know about them:\n{facts_digest or '(nothing yet)'}\n\n"
        "Write the lesson in the exact format from the system prompt."
    )

    try:
        msg = _claude.messages.create(
            model=_MODEL,
            max_tokens=1000,
            system=_LESSON_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return {"success": True, "message": extract_text(msg) or "", "law": number, "title": title}
    except Exception as e:
        logger.error("power_law_lesson_failed", error=str(e))
        return {"success": False, "error": str(e)}


async def analyze_power_scenario(user_id: UUID, scenario: str, goal: str = "") -> dict:
    """Analyse a situation the user posted through the 48 laws (Jarvis tool)."""
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
        return f"⚠️ 48 Laws lesson failed: {data.get('error', 'unknown error')}"
    return data["message"]
