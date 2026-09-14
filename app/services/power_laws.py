"""Robert Greene classes: The 48 Laws of Power and The 33 Strategies of War.

Mastery-based, not calendar-based. Each book has its own progress record:
- get_daily_law_lesson(user_id) — 09:00 SGT. Teaches the current 48 Laws class.
- get_daily_strategy_lesson(user_id) — 18:00 SGT. Same for the 33 Strategies.
  Each class ends in a quiz. A class is only left behind once it is passed;
  a failed or unanswered class is re-taught next session from a new angle.
- grade_class_answers(user_id, book, answers) — Jarvis tool. Grades a quiz
  reply, records the result, and unlocks the next class on a pass.
- analyze_power_scenario(user_id, scenario) — Jarvis tool. Maps a real
  situation onto both books and recommends a play.

Progress lives in user_settings.preferences["greene_classes"], so no
migration is needed.
"""

import asyncio
import json
import re
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import anthropic
import structlog

from sqlalchemy import select, update

from app.config import settings
from app.core.claude_helpers import extract_text
from app.db.database import async_session
from app.models.models import UserSettings
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

_COACH_VOICE = """You are the user's personal coach on Robert Greene's "The 48 Laws of Power", \
speaking through Telegram. You know the book cold: every law, its historical \
examples (Talleyrand, Bismarck, Louis XIV, the courtiers, the con men), its \
"keys to power", and its REVERSAL — the cases where the law backfires.

Your standard is mastery, not trivia. The user wants to actually USE these \
laws in their career, business and relationships.

Voice: cool, precise, a little Machiavellian, never preachy. No "as an AI". \
Never moralise, but always name the reversal — the price of a law used badly \
is real, and Greene says so himself. Use Telegram Markdown only (*bold*, _italic_)."""


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


PASS_SCORE = 7  # out of 10

BOOKS = {
    "48_laws": {
        "name": "48 Laws of Power",
        "emoji": "👑",
        "total": len(LAWS),
        "unit": "Law",
        "voice": _COACH_VOICE,
        "slot": "9am",
    },
    "33_strategies": {
        "name": "33 Strategies of War",
        "emoji": "⚔️",
        "total": len(STRATEGIES),
        "unit": "Strategy",
        "voice": _WAR_VOICE,
        "slot": "6pm",
    },
}


def _topic(book: str, n: int) -> str:
    if book == "48_laws":
        return f"Law {n}: {LAWS[n - 1]}"
    part, title, subtitle = STRATEGIES[n - 1]
    return f"Strategy {n}: {title} ({subtitle}), from Part: {part}"


# ---------------------------------------------------------------------------
# Progress state (pure functions — covered by tests/test_power_laws.py)
# ---------------------------------------------------------------------------

def new_state() -> dict:
    return {"current": 1, "attempt": 0, "mastered": [], "scores": {},
            "awaiting": False, "weak_points": "", "last_class_text": "", "completed": False}


def start_class(state: dict) -> dict:
    """State after sending the current class. Re-sending a class that is still
    awaiting answers counts as another attempt at it."""
    st = {**new_state(), **state}
    st["attempt"] = st["attempt"] + 1 if (st["awaiting"] or st["attempt"]) else 1
    st["awaiting"] = True
    return st


def record_result(state: dict, total: int, score: int, weak_points: str = "") -> dict:
    """State after grading the current class's quiz."""
    st = {**new_state(), **state}
    n = st["current"]
    st["scores"] = {**st["scores"], str(n): max(score, st["scores"].get(str(n), 0))}
    st["awaiting"] = False
    if score >= PASS_SCORE:
        st["mastered"] = sorted(set(st["mastered"]) | {n})
        remaining = [i for i in range(1, total + 1) if i not in st["mastered"]]
        if remaining:
            later = [i for i in remaining if i > n]
            st["current"] = later[0] if later else remaining[0]
        else:
            st["completed"] = True
        st["attempt"] = 0
        st["weak_points"] = ""
    else:
        st["weak_points"] = weak_points
    return st


def review_pick(state: dict, day_ordinal: int) -> int | None:
    """A previously mastered class to quiz again, rotating day by day."""
    pool = [m for m in state.get("mastered", []) if m != state.get("current")]
    return pool[day_ordinal % len(pool)] if pool else None


async def _load_state(user_id: UUID, book: str) -> dict:
    async with async_session() as session:
        prefs = (await session.execute(
            select(UserSettings.preferences).where(UserSettings.id == user_id)
        )).scalar_one_or_none() or {}
    return {**new_state(), **(prefs.get("greene_classes", {}).get(book, {}))}


async def _save_state(user_id: UUID, book: str, state: dict):
    # ponytail: read-modify-write on one JSONB column; fine for one owner,
    # move to its own table if anything else starts writing preferences often.
    async with async_session() as session:
        prefs = dict((await session.execute(
            select(UserSettings.preferences).where(UserSettings.id == user_id)
        )).scalar_one_or_none() or {})
        classes = dict(prefs.get("greene_classes", {}))
        classes[book] = state
        prefs["greene_classes"] = classes
        await session.execute(
            update(UserSettings).where(UserSettings.id == user_id).values(preferences=prefs)
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Class generation
# ---------------------------------------------------------------------------

_CLASS_FORMAT = """

You are teaching a CLASS, not sending a tip. The goal is mastery: after this \
class the student should be able to explain the idea, spot it being used on \
them, and use it deliberately. Teach like the best professor they ever had: \
clear structure, real depth, no filler.

Structure (exact format, Telegram Markdown):

{emoji} *{book_name} · Class {n} of {total}*
*<title>*{take_line}
_Progress: {mastered} of {total} mastered_

🎯 *By the end of this class you can*
<2 short bullets, starting with a verb.>

📖 *The lesson*
<A proper explanation in 3-4 sentences: the idea and the psychology of WHY it works.>
<Then 3 numbered principles. Each: a bold short name, then 1-2 sentences on how to apply it.>

🏛️ *Case study*
<A historical example from the book told as a short story, 3-4 sentences. \
End with "_What to notice:_" and the one move that made it work.>

🕵️ *Spot it being used on you*
<2-3 bullets: the tells that someone is running this on the student, and the counter.>

🔄 *Reversal*
<When it backfires or should not be used. 1-2 sentences.>

🛠️ *Field exercise (before next class)*
<ONE real task using the student's actual calendar, tasks or people. Name them. \
If context is empty, a task for a normal workweek.>

📝 *Quiz: reply with your answers. {pass_score}/10 unlocks the next class.*
1. <Concept: explain the core idea or a principle in their own words.>
2. <Scenario: a short realistic situation at work, business or family. What \
is happening, and what is their move?>
3. <Application: how will they use this, or defend against it, in their own life this week?>{review_line}

Length: 450-550 words. Every section is required."""


async def _teach_class(user_id: UUID, book: str, log_key: str) -> dict:
    if not _claude:
        return {"success": False, "error": "claude not configured"}
    cfg = BOOKS[book]
    state = await _load_state(user_id, book)

    if state["completed"]:
        n = review_pick({**state, "current": None}, datetime.now().toordinal()) or 1
        teach = f"The student has mastered every class. Run a mastery review class on {_topic(book, n)}."
    else:
        n = state["current"]
        teach = f"Teach {_topic(book, n)}."

    next_state = start_class(state)
    attempt = next_state["attempt"]
    if attempt > 1:
        teach += (
            f" This is attempt {attempt} at this class. Teach it from a NEW angle with a "
            "DIFFERENT case study than the most famous one, and spend extra time on: "
            + (state["weak_points"] or "the fundamentals, since they did not answer the last quiz")
            + "."
        )
    review = review_pick(state, datetime.now().toordinal())
    review_line = (
        f"\n4. 🔁 _Review_ — <one scenario question testing {_topic(book, review)}, which they mastered earlier.>"
        if review else ""
    )
    system = cfg["voice"] + _CLASS_FORMAT.format(
        emoji=cfg["emoji"], book_name=cfg["name"], n=n, total=cfg["total"],
        take_line=f" (take {attempt})" if attempt > 1 else "",
        mastered=len(state["mastered"]), pass_score=PASS_SCORE, review_line=review_line,
    )

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
        f"Student's day:\n{context}\n\n"
        f"What you know about them:\n{facts_digest or '(nothing yet)'}\n\n"
        "Write the class in the exact format from the system prompt."
    )
    try:
        msg = _claude.messages.create(
            model=_MODEL,
            max_tokens=2000,
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = extract_text(msg) or ""
    except Exception as e:
        logger.error(log_key, error=str(e))
        return {"success": False, "error": str(e), "book": book}

    next_state["last_class_text"] = text[:6000]
    await _save_state(user_id, book, next_state)
    return {"success": True, "message": text, "book": book, "class": n, "attempt": attempt}


async def get_daily_law_lesson(user_id: UUID) -> dict:
    """Generate the 09:00 48 Laws class."""
    return await _teach_class(user_id, "48_laws", "power_law_lesson_failed")


async def get_daily_strategy_lesson(user_id: UUID) -> dict:
    """Generate the 18:00 33 Strategies class."""
    return await _teach_class(user_id, "33_strategies", "war_strategy_lesson_failed")


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

_GRADE_SYSTEM = """You are a demanding but fair professor grading a student's quiz \
answers on a Robert Greene class. You get the full class the student received \
and their reply.

Grade for UNDERSTANDING, not wording: can they explain it, recognise it in a \
new situation, and apply it? Vague or copied-back answers score low. An \
unanswered question scores 0 for that question. The review question, if \
present, counts toward the score like the others.

Grade against Greene's BOOK, not only the class text. A correct idea from the \
book that the class did not mention earns credit, never a deduction. Only mark \
down ideas that are wrong or that misread Greene.

Return STRICT JSON only:
{"score": <integer 0-10>,
 "per_question": ["<1-2 sentence verdict for Q1: what they got right, what they missed>", "..."],
 "model_answer_hint": "<the single most important thing a top answer would have said>",
 "weak_points": "<if score < 7: at most 8 words naming what to re-teach, no trailing period; else empty>"}"""


def _parse_grade(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    data = json.loads(m.group(0)) if m else {}
    score = data.get("score")
    if not isinstance(score, (int, float)):
        raise ValueError("grader returned no score")
    data["score"] = max(0, min(10, int(round(score))))
    data["weak_points"] = str(data.get("weak_points") or "").strip().rstrip(".")[:120]
    return data


async def grade_class_answers(user_id: UUID, book: str, answers: str) -> dict:
    """Grade the student's reply to the latest class quiz (Jarvis tool)."""
    if not _claude:
        return {"success": False, "error": "claude not configured"}
    if book not in BOOKS:
        return {"success": False, "error": f"unknown book {book!r}; use one of {list(BOOKS)}"}
    if not answers.strip():
        return {"success": False, "error": "answers are empty"}

    cfg = BOOKS[book]
    state = await _load_state(user_id, book)
    if not state["last_class_text"]:
        return {"success": False, "error": f"no {cfg['name']} class has been sent yet"}

    try:
        msg = _claude.messages.create(
            model=_MODEL,
            max_tokens=1200,
            system=_GRADE_SYSTEM,
            messages=[{"role": "user", "content":
                f"THE CLASS:\n{state['last_class_text']}\n\nSTUDENT'S ANSWERS:\n{answers}"}],
        )
        grade = _parse_grade(extract_text(msg) or "")
    except Exception as e:
        logger.error("class_grading_failed", book=book, error=str(e))
        return {"success": False, "error": f"grading failed: {e}"}

    n = state["current"]
    new = record_result(state, cfg["total"], grade["score"], grade.get("weak_points", ""))
    await _save_state(user_id, book, new)

    passed = grade["score"] >= PASS_SCORE
    lines = [f"{cfg['emoji']} *Class {n} graded: {grade['score']}/10* " + ("✅ Passed" if passed else "❌ Not yet"), ""]
    for i, verdict in enumerate(grade.get("per_question", []), 1):
        lines.append(f"*Q{i}.* {verdict}")
    if grade.get("model_answer_hint"):
        lines += ["", f"💡 *What a top answer says:* {grade['model_answer_hint']}"]
    lines.append("")
    if new["completed"]:
        lines.append(f"🏆 *You have mastered all {cfg['total']}.* Sessions now run as mastery reviews.")
    elif passed:
        lines.append(
            f"🔓 Class {new['current']} unlocks at {cfg['slot']}. "
            f"Mastered: {len(new['mastered'])} of {cfg['total']}."
        )
    else:
        lines.append(
            f"🔁 Class {n} runs again at {cfg['slot']} from a new angle"
            + (f", focused on {new['weak_points'][:1].lower() + new['weak_points'][1:]}" if new["weak_points"] else "")
            + ". Or reply with better answers now to retry."
        )
    return {"success": True, "passed": passed, "score": grade["score"], "result": "\n".join(lines)}


def format_lesson_for_telegram(data: dict) -> str:
    if not data.get("success"):
        name = BOOKS.get(data.get("book", ""), {}).get("name", "Greene")
        return f"⚠️ {name} class failed: {data.get('error', 'unknown error')}"
    return data["message"]
