"""Resolve memory_flag contradictions instead of letting them rot.

Consolidation has been writing contradictions to ``memory_flags`` for
months; nothing reads them. This service:

* loads an unresolved flag plus the two memories it links
* asks a background LLM (the ``llm_utils`` builders: Claude under
  ``EDWARD_LLM_PROVIDER=anthropic``, else the Codex fallback) to classify
  the conflict as ``schema_confusion`` (memory wrongly substituted Edward
  for the owner, or vice versa) vs ``real_conflict`` (genuinely
  incompatible facts)
* on ``schema_confusion``, rewrites the broken memory and resolves the
  flag
* on ``real_conflict``, asks the owner a one-sentence disambiguation
  question and leaves the flag unresolved for them to answer — **once**.
  The flag's description gets an ``[asked <date>]`` marker; later runs
  leave it alone while the owner might still answer and resolve it as
  stale after ``STALE_AFTER_DAYS``. (Before 2026-09-07 every wakeup
  re-asked the same three months-old conflicts.)

Its caller is the mind's nightly (``mind_service._nightly_triage``):
calls labelled ``purpose``, at most one question a run, and
``ask_via="note"`` — the question becomes a note on Today that needs the
owner, never a push, because the nightly runs in the middle of the night.
(From 2026-09-24 until 2026-10-11 the surplus queue called it, at
``priority="surplus"``; docs/architecture/surplus.md keeps the history.)
The push path (held in quiet hours) is unchanged for any other caller.

A pair with none of the owner's own words and a side written while the
owner was not speaking is not theirs to answer (decision (b), 2026-09-27):
the flag closes ``[kept both]`` before any LLM call, with both rows left
exactly as they are (``consolidation_service.decide_contradiction``). Only
a pair that verdict leaves ``open`` — the owner's call — reaches the model
or the owner; one the label decides is left for consolidation's next
cycle, untouched and unasked (integration review, 2026-09-27).

A triage at ``priority="surplus"`` never rewrites a memory (2026-09-25,
``may_rewrite``). Its first live run was unattended, over the oldest of
~1,100 open flags, and the owner's memories are their data: a
``schema_confusion`` becomes the same once-asked question on Today,
carrying the rewrite the model would make, and the flag stays open for
the owner's answer. Every other caller rewrites in place.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import func, select

from services.database import (
    MemoryFlagModel,
    MemoryModel,
    async_session,
)
from services.json_parsing import extract_json_payload
from services.llm_utils import build_background_llm, make_background_system_message
from services.owner import owner_text
from services import push_service

logger = logging.getLogger(__name__)


STALE_AFTER_DAYS = 7
_ASKED_RE = re.compile(r"\s*\[asked (\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]\s*$")


def _now() -> datetime:
    """Patched in tests."""
    return datetime.utcnow()


def asked_at(description: Optional[str]) -> Optional[datetime]:
    """When the owner was asked about this flag, from the description marker."""
    m = _ASKED_RE.search(description or "")
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M")


def mark_asked(description: Optional[str], now: datetime) -> str:
    base = _ASKED_RE.sub("", description or "").rstrip()
    return f"{base} [asked {now.strftime('%Y-%m-%d %H:%M')}]"


def ask_policy(description: Optional[str], now: datetime) -> str:
    """'ask' (never asked), 'wait' (asked recently), or 'stale' (asked
    over STALE_AFTER_DAYS ago with no answer — close it, stop nagging)."""
    at = asked_at(description)
    if at is None:
        return "ask"
    if now - at > timedelta(days=STALE_AFTER_DAYS):
        return "stale"
    return "wait"


TRIAGE_SYSTEM = owner_text(
    "You are Edward's contradiction triage. For each memory flag, "
    "decide whether the contradiction is real OR a schema confusion. "
    "Schema confusion = a memory wrongly names Edward (the AI) where it "
    "should name {OWNER} (the user), or vice versa, or confuses {OWNER}'s "
    "relationships with someone else's. These should be auto-rewritten. "
    "Real conflicts are genuinely contradictory facts about the same "
    "subject that need the user to disambiguate."
)


# Rendered with ``owner_text`` (``str.replace``) at import, then filled with
# ``.format`` per flag — so its doubled JSON braces stay as they are.
TRIAGE_PROMPT_TEMPLATE = owner_text("""Memory A (id={a_id}): {a_content}
Memory B (id={b_id}): {b_content}

Flag description: {flag_description}

Return STRICT JSON. For schema confusion, supply a corrected rewrite of
the wrong memory:

{{
  "classification": "schema_confusion",
  "rewrite_target": "A" | "B",
  "rewrite": "the corrected memory content",
  "rationale": "short explanation"
}}

For a real conflict, supply a one-sentence question that will let {OWNER}
disambiguate:

{{
  "classification": "real_conflict",
  "question": "Which is current — X or Y?"
}}

Return ONLY the JSON object.""")


async def _load_flag_and_memories(
    flag_id: str,
) -> tuple[Optional[MemoryFlagModel], Optional[MemoryModel], Optional[MemoryModel]]:
    async with async_session() as session:
        flag = (await session.execute(
            select(MemoryFlagModel).where(MemoryFlagModel.id == flag_id)
        )).scalar_one_or_none()
        if flag is None:
            return None, None, None
        m_a = (await session.execute(
            select(MemoryModel).where(MemoryModel.id == flag.memory_id)
        )).scalar_one_or_none()
        m_b = None
        if flag.related_memory_id:
            m_b = (await session.execute(
                select(MemoryModel).where(MemoryModel.id == flag.related_memory_id)
            )).scalar_one_or_none()
        return flag, m_a, m_b


async def _ask_llm(
    flag: MemoryFlagModel,
    m_a: MemoryModel,
    m_b: Optional[MemoryModel],
    *,
    priority: str = "background",
    purpose: Optional[str] = None,
) -> dict[str, Any]:
    from langchain_core.messages import HumanMessage

    prompt = TRIAGE_PROMPT_TEMPLATE.format(
        a_id=m_a.id,
        a_content=(m_a.content or "")[:400],
        b_id=m_b.id if m_b else "(none)",
        b_content=((m_b.content if m_b else "") or "")[:400],
        flag_description=(flag.description or "")[:300],
    )
    if priority == "background" and not purpose:
        llm = build_background_llm(max_tokens=512)
    else:
        # A labelled caller (the nightly; formerly the surplus job):
        # durable judgment on the mind's model at high effort.
        llm = build_background_llm(max_tokens=512, priority=priority, model=await _judgment_model(),
                                   effort="high")
        if purpose:
            from services.call_purpose import stamp
            stamp(llm, purpose)
    response = await llm.ainvoke([
        make_background_system_message(TRIAGE_SYSTEM),
        HumanMessage(content=prompt),
    ])
    content = response.content
    if isinstance(content, list):
        content = " ".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    decision = extract_json_payload(
        content, expected="object", subsystem="contradiction_triage"
    )
    if not isinstance(decision, dict):
        return {}
    return decision


async def _apply_rewrite(
    decision: dict[str, Any],
    m_a: MemoryModel,
    m_b: Optional[MemoryModel],
    flag: MemoryFlagModel,
) -> bool:
    target = decision.get("rewrite_target")
    rewrite = decision.get("rewrite")
    if not rewrite:
        return False
    # Default to memory A (the flagged one) when target isn't explicit.
    if target == "B" and m_b is not None:
        target_id = m_b.id
    else:
        target_id = m_a.id
    async with async_session() as session:
        m = (await session.execute(
            select(MemoryModel).where(MemoryModel.id == target_id)
        )).scalar_one_or_none()
        if m is None:
            return False
        m.content = rewrite[:2000]
        await session.execute(
            select(MemoryFlagModel).where(MemoryFlagModel.id == flag.id)
        )
        # Re-fetch flag in this session to mark resolved
        f = (await session.execute(
            select(MemoryFlagModel).where(MemoryFlagModel.id == flag.id)
        )).scalar_one_or_none()
        if f is not None:
            from datetime import datetime
            f.resolved = True
            f.resolved_at = datetime.utcnow()
        await session.commit()
    return True


async def _judgment_model() -> Optional[str]:
    try:
        from services import llm_utils
        from services.settings_service import get_settings

        return llm_utils.mind_model_for((await get_settings()).model)
    except Exception:  # noqa: BLE001
        return None


ASK_VIA = ("push", "note")


FLAG_PAGE = "/settings/dreaming"


def flag_url(flag_id: str, ask: Optional[str] = None) -> str:
    """Where a question about a flag opens: the Dreaming panel, which shows
    flags (``/settings/memories`` ignored the parameter). ``ask=rewrite``
    marks a proposed rewrite, which Today does not offer as "keep one". Pure."""
    url = f"{FLAG_PAGE}?flag={flag_id}"
    return f"{url}&ask={ask}" if ask else url


QUESTION_TITLE = "Which is right?"


def flag_id_from_url(url: Optional[str]) -> Optional[str]:
    """The flag a question note links to (``flag_url``), or None. Pure."""
    if not isinstance(url, str) or "flag=" not in url:
        return None
    try:
        return (parse_qs(urlsplit(url).query).get("flag") or [None])[0]
    except ValueError:
        return None


async def _question_waiting_on_owner() -> bool:
    """Is a "Which is right?" note live on Today with its flag still open?

    One question at a time (2026-09-27): Today shows every note sharing a
    title as one card — the newest question, and "Done" clears the rest —
    so a second live question is never seen, yet its flag was marked asked
    and closed as stale a week later. The audit found two identical ones
    written in the same minute. A note whose flag the owner has answered (or
    that has expired) no longer holds the slot."""
    from services.database import EdwardNoteModel

    async with async_session() as session:
        urls = (await session.execute(
            select(EdwardNoteModel.url).where(
                EdwardNoteModel.title == QUESTION_TITLE,
                EdwardNoteModel.expires_at > _now(),
            )
        )).scalars().all()
        if not urls:
            return False
        ids = [flag_id_from_url(u) for u in urls]
        if any(i is None for i in ids):
            return True  # a question we cannot tie to a flag still holds the card
        still_open = (await session.execute(
            select(func.count()).select_from(MemoryFlagModel).where(
                MemoryFlagModel.id.in_(ids),
                MemoryFlagModel.resolved.is_(False),
            )
        )).scalar()
    return bool(still_open)


async def _note_disambiguation(decision: dict[str, Any], flag: MemoryFlagModel) -> None:
    """The question as a note on Today that needs the owner — the note path."""
    from services import notes_service

    question = decision.get("question") or "I have two memories that conflict — which is right?"
    await notes_service.leave_note(kind="mind", title=QUESTION_TITLE, body=question[:400],
                                   url=flag_url(flag.id, decision.get("ask")), needs_you=True)


def may_rewrite(priority: str) -> bool:
    """Whether this caller may rewrite a memory in place: a triage at
    ``priority="surplus"`` asks the owner instead (see the module docstring)."""
    return priority != "surplus"


def rewrite_question(decision: dict[str, Any], m_a: MemoryModel, m_b: Optional[MemoryModel]) -> dict[str, Any]:
    """A proposed rewrite as a question for the owner: the ``real_conflict``
    shape, so it is noted and marked asked like one. Pure."""
    target = m_b if decision.get("rewrite_target") == "B" and m_b is not None else m_a
    current = " ".join(str(target.content or "").split())[:160]
    proposed = " ".join(str(decision.get("rewrite") or "").split())[:160]
    return {"question": f'I have a memory that says "{current}". Should it say "{proposed}" instead?',
            "ask": "rewrite"}


async def _push_disambiguation(decision: dict[str, Any], flag: MemoryFlagModel) -> None:
    question = decision.get("question") or "I have two memories that conflict — which is right?"
    url = flag_url(flag.id, decision.get("ask"))
    try:
        await push_service.send_push_notification(
            title="Edward — contradiction",
            body=question[:200],
            url=url,
            tag="contradiction-triage",
            source="contradiction_triage",
            respect_quiet_hours=True,
        )
    except Exception as exc:
        logger.warning("contradiction push failed: %s", exc)


# How many open flags one run reads to group them by pair.
MAX_FLAGS_SCANNED = 1000


def _pair(flag: Any) -> tuple:
    a, b = flag.memory_id or "", flag.related_memory_id or ""
    return (a, b) if a <= b else (b, a)


async def _rank_pairs(groups: list) -> list:
    """Flag groups (one per memory pair, each oldest first), ordered by
    ``consolidation_service.contradiction_priority`` of the pair's
    memories, oldest pair first on a tie.

    Oldest first used to decide what the owner was asked (2026-09-27): with
    one question on Today at a time, the order is the choice. A conflict
    with something the owner said themselves, then the weightier and
    more-used rows."""
    from services.consolidation_service import contradiction_priority, conversation_sources_for

    ids = {i for g in groups for i in _pair(g[0]) if i}
    async with async_session() as session:
        rows = (await session.execute(select(MemoryModel).where(MemoryModel.id.in_(ids)))).scalars().all() \
            if ids else []
    by_id = {m.id: m for m in rows}
    sources = await conversation_sources_for(rows)
    # A stable sort, reversed, keeps the oldest pair first on a tie.
    return sorted(groups, key=lambda g: contradiction_priority(by_id.get(g[0].memory_id),
                                                                 by_id.get(g[0].related_memory_id), sources),
                  reverse=True)


async def triage_unresolved(limit: int = 5, *, priority: str = "background", purpose: Optional[str] = None,
                            ask_via: str = "push", max_asks: Optional[int] = None) -> list[dict[str, Any]]:
    """Triage up to ``limit`` memory pairs with unresolved contradiction
    flags, the most deserving of the owner's attention first
    (``_rank_pairs``). Returns the list of action summaries.

    Called after the mind's nightly (``mind_service._nightly_triage``) —
    keeps consolidation's output flowing into actionable signal.
    ``ask_via`` is ``push`` (held in quiet hours) or ``note`` (never a
    push). ``max_asks`` stops the run once that many questions went to the
    owner; the rest stay open for the next run.

    A pair is asked about once (2026-09-27): the flags are grouped by
    memory pair (one pair had 55 duplicate flags), a pair with a flag
    already asked waits with it — the note lives a day, the flag a week —
    a pair whose question went stale closes all its flags, and otherwise
    only its oldest flag is triaged.
    """
    async with async_session() as session:
        rows = (await session.execute(
            select(MemoryFlagModel)
            .where(
                MemoryFlagModel.resolved.is_(False),
                MemoryFlagModel.flag_type == "contradiction",
            )
            .order_by(MemoryFlagModel.created_at.asc())
            .limit(MAX_FLAGS_SCANNED)
        )).scalars().all()
    pairs: dict[tuple, list] = {}
    for row in rows:
        pairs.setdefault(_pair(row), []).append(row)
    ordered = await _rank_pairs(list(pairs.values()))

    results: list[dict[str, Any]] = []
    now = _now()
    asked = 0
    for flags in ordered[:max(0, min(limit, 50))]:
        if max_asks is not None and asked >= max_asks:
            break
        try:
            policies = [(f, ask_policy(f.description, now)) for f in flags]
            waiting = next((f for f, p in policies if p == "wait"), None)
            if waiting is not None:
                results.append({"action": "waiting_for_user", "flag_id": waiting.id})
                continue
            if any(p == "stale" for _, p in policies):
                for f in flags:
                    await _resolve_flag(f.id, now)
                    results.append({"action": "stale_resolved", "flag_id": f.id})
                continue
            out = await triage_flag(flags[0].id, priority=priority, purpose=purpose, ask_via=ask_via)
            results.append(out)
            if out.get("action") == "kept_both":
                # The verdict is the pair's: its other flags close with it.
                from services.consolidation_service import mark_flag_kept_both

                for f in flags[1:]:
                    await mark_flag_kept_both(f.id, out.get("reason") or "")
                    results.append({"action": "kept_both", "flag_id": f.id})
            if out.get("action") == "asked_user":
                asked += 1
        except Exception as exc:
            logger.exception("triage_unresolved: flag %s failed: %s", flags[0].id, exc)
            results.append({"action": "error", "flag_id": flags[0].id, "reason": str(exc)})
    return results


async def _resolve_flag(flag_id: str, now: datetime) -> None:
    async with async_session() as session:
        f = (await session.execute(
            select(MemoryFlagModel).where(MemoryFlagModel.id == flag_id)
        )).scalar_one_or_none()
        if f is not None:
            f.resolved = True
            f.resolved_at = now
            await session.commit()


async def _record_asked(flag_id: str, now: datetime) -> None:
    async with async_session() as session:
        f = (await session.execute(
            select(MemoryFlagModel).where(MemoryFlagModel.id == flag_id)
        )).scalar_one_or_none()
        if f is not None:
            f.description = mark_asked(f.description, now)
            await session.commit()


async def triage_flag(flag_id: str, *, priority: str = "background", purpose: Optional[str] = None,
                      ask_via: str = "push") -> dict[str, Any]:
    """Triage a single memory_flag. Never raises."""
    flag, m_a, m_b = await _load_flag_and_memories(flag_id)
    if flag is None:
        return {"action": "missing"}
    if m_a is None:
        # Its memory was deleted since. Left open it held one of the daily
        # job's oldest-first slots for good (11 such flags on 2026-09-25);
        # closing it touches the flag only, never a memory.
        await _resolve_flag(flag_id, _now())
        return {"action": "missing_resolved"}

    if flag.related_memory_id and m_b is None:
        await _resolve_flag(flag_id, _now())
        return {"action": "missing_resolved"}
    from services.consolidation_service import memory_lapsed

    if any(memory_lapsed(m, _now()) for m in (m_a, m_b) if m is not None):
        # A side lapsed since the flag was raised (2026-09-27): time
        # answered it. Closing the flag touches no memory.
        await _resolve_flag(flag_id, _now())
        return {"action": "lapsed_resolved", "flag_id": flag_id}

    from services.consolidation_service import conversation_sources_for, decide_contradiction, mark_flag_kept_both

    verdict = decide_contradiction(m_a, m_b, now=_now(),
                                   conversation_sources=await conversation_sources_for([m_a, m_b]))
    if verdict[0] == "keep_both":
        # None of the owner's words on either side (decision (b), 2026-09-27):
        # nothing to ask them, and nothing the model may change here.
        await mark_flag_kept_both(flag_id, verdict[1])
        return {"action": "kept_both", "flag_id": flag_id, "reason": verdict[1]}
    if verdict[0] == "moot":
        # A side is gone (a flag naming one memory): nothing left to decide.
        await _resolve_flag(flag_id, _now())
        return {"action": "missing_resolved", "flag_id": flag_id}
    if verdict[0] != "open":
        # The label decides it ("auto"): consolidation's next cycle settles
        # the pair. Only a pair that is the owner's call is asked, or costs a
        # model call (integration review, 2026-09-27 — an "auto" pair with
        # none of the owner's words became a "Which is right?" note, and the cycle
        # then closed the flag under it).
        return {"action": "left_for_consolidation", "flag_id": flag_id}

    if ask_via == "note" and await _question_waiting_on_owner():
        # One question on Today at a time; this flag stays unasked for a
        # later run and costs no LLM call now.
        return {"action": "deferred", "flag_id": flag_id}

    try:
        if priority == "background" and not purpose:
            decision = await _ask_llm(flag, m_a, m_b)
        else:
            decision = await _ask_llm(flag, m_a, m_b, priority=priority, purpose=purpose)
    except Exception as exc:
        logger.exception("contradiction triage LLM failed: %s", exc)
        return {"action": "error", "reason": str(exc)}

    classification = decision.get("classification")
    if classification == "schema_confusion" and may_rewrite(priority):
        ok = await _apply_rewrite(decision, m_a, m_b, flag)
        return {
            "action": "auto_resolved" if ok else "error",
            "rewrite_target": decision.get("rewrite_target"),
            "rationale": decision.get("rationale"),
        }
    if classification == "schema_confusion":
        # A surplus triage: the rewrite is the owner's to make, asked like a
        # real conflict (once), and the memory is never touched here.
        if not decision.get("rewrite"):
            return {"action": "error", "reason": "schema_confusion without a rewrite"}
        decision = {**decision, **rewrite_question(decision, m_a, m_b)}
        classification = "real_conflict"
    if classification == "real_conflict":
        if ask_via == "note":
            if await _question_waiting_on_owner():
                # another run asked while the model was thinking
                return {"action": "deferred", "flag_id": flag_id}
            await _note_disambiguation(decision, flag)
        else:
            await _push_disambiguation(decision, flag)
        await _record_asked(flag.id, _now())
        return {
            "action": "asked_user",
            "question": decision.get("question"),
        }
    return {"action": "skipped", "classification": classification}
