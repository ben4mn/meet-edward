"""
Memory service for Edward's long-term memory capabilities.

Handles:
- Embedding generation using sentence-transformers (all-MiniLM-L6-v2)
- Hybrid search (70% vector similarity + 30% BM25 keyword)
- Memory extraction from conversations using LLM
"""

import asyncio
import json
import math
import uuid
import re
from typing import Any, List, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text, func, update, or_, and_
from langchain_core.messages import HumanMessage, SystemMessage

from services.llm_utils import build_background_llm, make_background_system_message
from services.json_parsing import extract_json_payload

from services.database import async_session, MemoryModel
from services.owner import owner_names, owner_text

# Lazy load the embedding model to avoid loading at import time
_embedding_model = None


def get_embedding_model():
    """Lazy load the sentence-transformers model."""
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
    return _embedding_model


@dataclass
class Memory:
    """A memory retrieved from or to be stored in the database."""
    id: Optional[str]
    content: str
    memory_type: str  # fact, preference, context, instruction
    importance: float
    source_conversation_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None  # Track when memory was last modified
    last_accessed: Optional[datetime] = None
    access_count: int = 0
    temporal_nature: str = "timeless"  # timeless, temporary, evolving
    tier: str = "observation"  # observation, belief, knowledge
    reinforcement_count: int = 0
    source_type: str = "inferred"  # user_stated, inferred, tool_derived, imported
    confidence: float = 0.5  # 0-1 scale, independent of importance
    user_id: Optional[str] = None  # For future multi-user support
    score: float = 0.0  # Relevance score during retrieval
    valid_until: Optional[datetime] = None  # naive UTC; set when the memory names its own window
    # Mind rebuild Phase 1: how the memory came to be, and whom it concerns.
    origin: str = "heard"  # heard, read, thought, did
    about: str = "owner"  # owner, contact, self, world
    # Provenance (2026-09-24): where the row came from, as the JSON
    # services/provenance_service.py builds. None for old rows.
    source_ref: Optional[str] = None


def get_embedding(text: str) -> List[float]:
    """Generate embedding vector for text using local sentence-transformers."""
    model = get_embedding_model()
    embedding = model.encode(text, convert_to_numpy=True, show_progress_bar=False)
    return embedding.tolist()


def compute_temporal_boost(
    temporal_nature: str,
    last_accessed: Optional[datetime],
    access_count: int,
) -> float:
    """
    Compute a multiplicative temporal boost in [0.7, 1.3] based on recency,
    frequency, and temporal nature.

    - timeless: boost stays ~1.0 (barely affected)
    - temporary: strong recency weight, decays to 0.7x when stale
    - evolving: moderate blend of recency + frequency
    """
    now = datetime.now(timezone.utc)

    # Recency: exponential decay with 90-day half-life
    if last_accessed:
        la = last_accessed if last_accessed.tzinfo else last_accessed.replace(tzinfo=timezone.utc)
        days_since = max((now - la).total_seconds() / 86400, 0)
    else:
        days_since = 365  # assume very old if never accessed

    recency = math.exp(-0.693 * days_since / 90)  # 0.693 = ln(2)

    # Frequency: log-normalized to [0, 1] (caps at ~150 accesses)
    frequency = min(math.log(1 + access_count) / 5.0, 1.0)

    if temporal_nature == "timeless":
        # Barely affected by recency/frequency — stays near 1.0
        boost = 1.0 + 0.05 * (recency - 0.5)
    elif temporal_nature == "temporary":
        # Strong recency weight — decays noticeably when stale
        boost = 0.7 + 0.6 * recency  # range: 0.7 (stale) to 1.3 (fresh)
    elif temporal_nature == "evolving":
        # Moderate blend of recency + frequency
        boost = 0.8 + 0.3 * recency + 0.2 * frequency
    else:
        boost = 1.0

    return max(0.7, min(1.3, boost))


# How many turns a memory must actually have been shown to the model before
# use alone counts as corroboration. These are the one pair of numbers in the
# design that cannot be calibrated from existing data — ``used_count`` starts
# at zero for every row — so they are deliberately conservative and named
# rather than inlined. Revisit once real traffic has accumulated.
BELIEF_USES = 3
KNOWLEDGE_USES = 10


# A dated episode (memory audit, 2026-09-27; docs/architecture/memory.md):
# content that names a specific past calendar date — "On July 10–12, 2026,
# a friend sent the owner…", "For the reminders on July 24, July 31 and
# August 28…". A date counts when it carries a year, is an ISO date, or
# follows "on" ("on Aug. 22", "On Monday, July 6"); a bare "March 14" is
# left alone — a birthday is a fact, not an episode.
_MONTH_RE = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
             r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_DATE_WITH_YEAR = re.compile(
    rf"\b{_MONTH_RE}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:\s*[–-]\s*\d{{1,2}})?,?\s+(20\d\d)\b", re.I)
_DATE_AFTER_ON = re.compile(
    rf"\bon\s+(?:(?:mon|tues|wednes|thurs|fri|satur|sun)day,?\s+)?{_MONTH_RE}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b",
    re.I)
_ISO_DATE = re.compile(r"\b(20\d\d)-(\d{1,2})-(\d{1,2})\b")
# A date bound to what recurs or never changes — "birthday is on June 3",
# "was adopted on June 5, 2021", "born 2019-04-02" — is a fact whatever the
# calendar says (memory audit review, 2026-09-27: these read as episodes, and
# the yearless ones flipped with the season). Only the date right after
# the predicate is set aside; "celebrating a birthday" in an episode dated
# July 29 is still an episode.
_DURABLE_DATE = re.compile(
    rf"\b(?:birthday|anniversary|born|adopted)\b(?:\s+(?:is|was|were|falls))?(?:\s+on)?\s+"
    rf"(?:{_MONTH_RE}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+(?:19|20)\d\d)?|(?:19|20)\d\d-\d{{1,2}}-\d{{1,2}})",
    re.I)
_MONTH_NUMBERS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}


def names_past_date(content: Optional[str], now: Optional[datetime] = None) -> bool:
    """True when the content names a specific calendar date before today
    (a date without a year is read in the current year), leaving out a
    date bound to a birthday, anniversary, birth or adoption. Pure."""
    if not content:
        return False
    content = _DURABLE_DATE.sub(" ", content)
    today = (now or datetime.now(timezone.utc)).date()
    found = []
    for m in _DATE_WITH_YEAR.finditer(content):
        found.append((int(m.group(3)), _MONTH_NUMBERS[m.group(1)[:3].lower()], int(m.group(2))))
    for m in _DATE_AFTER_ON.finditer(content):
        found.append((today.year, _MONTH_NUMBERS[m.group(1)[:3].lower()], int(m.group(2))))
    for m in _ISO_DATE.finditer(content):
        found.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
    for year, month, day in found:
        try:
            if datetime(year, month, day).date() < today:
                return True
        except ValueError:
            continue
    return False


def _compute_tier(reinforcement_count: int, used_count: int = 0, content: Optional[str] = None,
                  now: Optional[datetime] = None) -> str:
    """Determine memory tier from repetition AND use.

    ``reinforcement_count`` says how often the same thing was stated again.
    ``used_count`` says how often Edward actually reached for it. The ladder
    reads both, because tier drives retrieval boost and (after P2) retention
    — both questions about usefulness, which repetition alone answers badly.
    A fact the owner repeats out of habit is not the same as one Edward needs.

    Promotion is on either path; the top tier needs both. Knowledge is never
    auto-retired and carries the largest score boost, so it takes
    corroboration from two independent signals rather than a big number in
    one column.

    A dated episode (``content`` naming a past date, ``names_past_date``)
    stops at belief (memory audit, 2026-09-27): it was true of its
    day, and the most-retrieved rows in the store were July episodes that
    use alone was carrying toward the tier nothing retires.
    """
    reinforcement_count = reinforcement_count or 0
    used_count = used_count or 0

    restated = reinforcement_count >= 5
    well_used = used_count >= KNOWLEDGE_USES
    if restated and well_used and not names_past_date(content, now):
        return "knowledge"

    if reinforcement_count >= 2 or used_count >= BELIEF_USES:
        return "belief"

    return "observation"


async def retrieve_memories(
    query: str,
    limit: int = 5,
    vector_weight: float = 0.7,
    keyword_weight: float = 0.3,
    min_score: float | None = None,
    memory_types: list[str] | None = None,
    update_access: bool = True,
    min_confidence: float = 0.5,
    about: str | None = None,
    origins: list[str] | None = None,
) -> List[Memory]:
    """
    Retrieve relevant memories using hybrid search.

    Combines:
    - Vector similarity search (cosine distance)
    - BM25 keyword search (full-text)

    Args:
        query: Search query text
        limit: Maximum number of memories to return
        vector_weight: Weight for vector similarity (default 0.7)
        keyword_weight: Weight for keyword matching (default 0.3)
        min_score: Minimum combined score threshold (optional)
        memory_types: Filter to specific memory types e.g. ["fact", "preference"] (optional)
        update_access: Bump last_accessed / access_count for hit memories
        min_confidence: Hard floor on the ``confidence`` column (default 0.5).
            Low-confidence inferred memories from the GPT-5.4 silent-failure era
            pollute context; callers that want broader recall (reflection, deep
            retrieval) may lower this to 0.3. Memories with NULL confidence are
            treated as 0.5 for backward compatibility.

    Returns:
        List of Memory objects sorted by relevance
    """
    query_embedding = get_embedding(query)
    # Format embedding as PostgreSQL array literal
    embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

    # Build optional type filter clause
    type_filter = ""
    type_params = {}
    if memory_types:
        placeholders = ", ".join(f":mtype_{i}" for i in range(len(memory_types)))
        type_filter = f" AND memory_type IN ({placeholders})"
        type_params = {f"mtype_{i}": t for i, t in enumerate(memory_types)}
    # Mind rebuild Phase 1: a wake asks for its own thoughts (about="self",
    # origins=["thought", "did"]) the same way chat asks for facts about the owner.
    if about:
        type_filter += " AND about = :about"
        type_params["about"] = about
    if origins:
        placeholders = ", ".join(f":origin_{i}" for i in range(len(origins)))
        type_filter += f" AND origin IN ({placeholders})"
        type_params.update({f"origin_{i}": o for i, o in enumerate(origins)})

    # Temporal-edge filter: a memory is excluded when consolidation has
    # retired it (valid_until IS NOT NULL AND valid_until < now()). NULL
    # and future timestamps are treated as still-valid.
    #
    # The stored column is TIMESTAMP WITHOUT TIME ZONE; the convention
    # across Edward's codebase is to hold UTC-naive values there. The
    # session TZ follows the host Mac's local zone (rarely UTC), so we
    # force both sides of the comparison to UTC instead of relying on
    # PostgreSQL's session-TZ-dependent auto-conversion.
    valid_until_clause = (
        " AND (valid_until IS NULL "
        "OR valid_until > (NOW() AT TIME ZONE 'UTC'))"
    )

    vector_query = text(f"""
        SELECT
            id, content, memory_type, importance,
            source_conversation_id, created_at, updated_at, last_accessed, access_count,
            temporal_nature, tier, reinforcement_count, user_id,
            source_type, confidence, origin, about,
            1 - (embedding <=> CAST(:embedding AS vector)) as vector_score
        FROM memories
        WHERE embedding IS NOT NULL{type_filter}{valid_until_clause}
        ORDER BY embedding <=> CAST(:embedding AS vector)
        LIMIT :limit
    """)
    keyword_query = text(f"""
        SELECT
            id,
            ts_rank_cd(to_tsvector('english', content), plainto_tsquery('english', :query)) as keyword_score
        FROM memories
        WHERE to_tsvector('english', content) @@ plainto_tsquery('english', :query){type_filter}{valid_until_clause}
        ORDER BY keyword_score DESC
        LIMIT :limit
    """)

    async def _run_vector():
        async with async_session() as s:
            r = await s.execute(
                vector_query,
                {"embedding": embedding_str, "limit": limit * 2, **type_params},
            )
            return r.fetchall()

    async def _run_keyword():
        async with async_session() as s:
            r = await s.execute(
                keyword_query,
                {"query": query, "limit": limit * 2, **type_params},
            )
            return r.fetchall()

    vector_fetched, keyword_fetched = await asyncio.gather(_run_vector(), _run_keyword())
    vector_rows = {row.id: row for row in vector_fetched}
    keyword_scores = {row.id: row.keyword_score for row in keyword_fetched}

    async with async_session() as session:

        # Combine scores — union all candidate IDs from both search methods
        # This ensures keyword-only matches (names, acronyms, exact phrases)
        # surface even when embedding similarity is poor
        memories = []
        all_candidate_ids = set(vector_rows.keys()) | set(keyword_scores.keys())

        for id in all_candidate_ids:
            if id in vector_rows:
                row = vector_rows[id]
                vector_score = row.vector_score or 0
            else:
                # Keyword-only match — fetch full row for scoring
                row_result = await session.execute(
                    text(f"""SELECT id, content, memory_type, importance,
                        source_conversation_id, created_at, updated_at, last_accessed, access_count,
                        temporal_nature, tier, reinforcement_count, user_id,
                        source_type, confidence, origin, about
                        FROM memories WHERE id = :id"""),
                    {"id": id}
                )
                row = row_result.fetchone()
                if not row:
                    continue
                vector_score = 0

            keyword_score = keyword_scores.get(id, 0)
            base_score = (
                vector_weight * vector_score +
                keyword_weight * keyword_score
            ) * (1 + (row.importance or 0.5) * 0.2)

            # Confidence multiplier: scales 0.7x (confidence=0) to 1.0x (confidence=1)
            conf = getattr(row, 'confidence', 0.5) or 0.5
            base_score *= (0.7 + conf * 0.3)

            tn = getattr(row, 'temporal_nature', 'timeless') or 'timeless'
            temporal_boost = compute_temporal_boost(
                tn,
                row.last_accessed,
                row.access_count or 0,
            )
            combined_score = base_score * temporal_boost

            # Tier boost
            tier_val = getattr(row, 'tier', 'observation') or 'observation'
            tier_multiplier = {"observation": 1.0, "belief": 1.1, "knowledge": 1.2}.get(tier_val, 1.0)
            combined_score *= tier_multiplier

            # Context-observation penalty: stale context observations decay faster
            if (row.memory_type == "context" and tier_val == "observation" and temporal_boost < 0.85):
                combined_score *= 0.8

            memory = Memory(
                id=row.id,
                content=row.content,
                memory_type=row.memory_type,
                importance=row.importance or 0.5,
                source_conversation_id=row.source_conversation_id,
                created_at=row.created_at,
                updated_at=getattr(row, 'updated_at', None),
                last_accessed=row.last_accessed,
                access_count=row.access_count or 0,
                temporal_nature=tn,
                tier=tier_val,
                reinforcement_count=getattr(row, 'reinforcement_count', 0) or 0,
                source_type=getattr(row, 'source_type', 'inferred') or 'inferred',
                confidence=getattr(row, 'confidence', 0.5) or 0.5,
                origin=getattr(row, 'origin', 'heard') or 'heard',
                about=getattr(row, 'about', 'owner') or 'owner',
                user_id=getattr(row, 'user_id', None),
                score=combined_score
            )
            memories.append(memory)

        # NOTE: access is deliberately NOT bumped here. This loop runs over
        # every candidate the vector and keyword queries produced (limit * 2
        # each), most of which are about to be dropped by the score
        # threshold, the confidence floor, or the final slice. Counting a
        # scored candidate as an access is what let irrelevant memories
        # refresh their own recency on every turn and never decay. The bump
        # now happens once, at the bottom, over the memories actually
        # returned.

        # Spreading activation (in-set boost) + flag annotation, batched.
        # Both read-only lookups run concurrently via a single DB round-trip each.
        try:
            from services.consolidation_service import (
                get_connected_memory_ids_batch,
                get_memory_flags_batch,
            )
            ids = [m.id for m in memories]
            result_ids = set(ids)
            connections_by_id, flags_by_id = await asyncio.gather(
                get_connected_memory_ids_batch(ids),
                get_memory_flags_batch(ids),
            )
            for memory in memories:
                for conn_id, strength in connections_by_id.get(memory.id, []):
                    if conn_id in result_ids:
                        memory.score += 0.1 * strength
                for flag in flags_by_id.get(memory.id, []):
                    memory.content += f" [FLAG: {flag['description']}]"
        except Exception:
            pass  # Consolidation service may not exist yet

        # Sort by combined score and return top results
        memories.sort(key=lambda m: m.score, reverse=True)
        # Apply minimum relevance threshold (default 0.25 to filter noise)
        effective_min = min_score if min_score is not None else 0.25
        memories = [m for m in memories if m.score >= effective_min]

        # Confidence floor (Fix #3) — drop low-confidence inferred memories
        # that leaked in during the GPT-5.4 silent-extraction era. NULL
        # confidence is treated as 0.5 for backward compatibility.
        pre_filter_count = len(memories)
        memories = [
            m for m in memories
            if (m.confidence if m.confidence is not None else 0.5) >= min_confidence
        ]
        dropped = pre_filter_count - len(memories)
        if dropped:
            print(
                f"[DEBUG] retrieve_memories: dropped_low_confidence={dropped} "
                f"(min_confidence={min_confidence})"
            )

        # Spreading activation (union): pull in memories CONNECTED to any
        # primary hit but not already in the result set. Capped at +5 per
        # query to prevent context bloat when a memory has dozens of
        # connections. These arrive with score = 0.15 * strength — they
        # ride in as supporting context rather than competing on relevance.
        pulled_in = await _spread_connected_memories(
            primary_ids={m.id for m in memories},
            min_confidence=min_confidence,
            cap=5,
        )
        memories.extend(pulled_in)
        returned = memories[:limit]

        # Access tracking, over the returned set only (skipped for
        # reflection / deep-retrieval sweeps, which pass update_access=False).
        if update_access and returned:
            await session.execute(
                update(MemoryModel)
                .where(MemoryModel.id.in_([m.id for m in returned]))
                .values(
                    access_count=MemoryModel.access_count + 1,
                    last_accessed=func.now(),
                )
            )
            await session.commit()

        return returned


async def mark_memories_used(memory_ids: list[str]) -> int:
    """Record that these memories reached the model's context.

    ``access_count`` says retrieval returned a memory; ``used_count`` says
    the model *used* it, and feeds the tier ladder and decay's half-life.

    Use is citation, not display (memory audit, 2026-09-27).
    ``streaming`` calls this for every memory whose tag survived into the
    prompt — retrieval under another name, and it entrenched whatever
    retrieval favoured: the 100 most-retrieved rows took 70% of all
    retrievals and climbed the ladder by being shown (one dated July
    episode had 2,387 retrievals and 1,294 "uses"). Inside a
    turn the ids are now only held (``_SHOWN_BY_TURN``); post-turn
    extraction credits the ones the answer drew on
    (``credit_cited_memories``). Outside a turn — no answer will follow —
    the ids are credited at once, as before.

    Returns the number of rows credited now. Unknown ids are ignored.
    """
    ids = [i for i in (memory_ids or []) if i]
    if not ids:
        return 0
    turn_id = _current_turn_id()
    if turn_id is not None:
        _note_shown(turn_id, ids)
        return 0
    return await _credit_use(ids)


async def _credit_use(memory_ids: list[str]) -> int:
    """``used_count + 1`` and ``last_used_at`` (naive UTC from Python) for
    these rows."""
    ids = [i for i in (memory_ids or []) if i]
    if not ids:
        return 0
    async with async_session() as session:
        result = await session.execute(
            update(MemoryModel)
            .where(MemoryModel.id.in_(ids))
            .values(
                used_count=func.coalesce(MemoryModel.used_count, 0) + 1,
                last_used_at=datetime.utcnow(),
            )
        )
        await session.commit()
        return result.rowcount


# What each turn showed the model, held until its answer is known. Bounded:
# a turn that skips extraction (the pulse, skip_memory) never collects.
_SHOWN_BY_TURN: "dict[str, set[str]]" = {}
_SHOWN_BY_TURN_MAX = 256


def _current_turn_id() -> Optional[str]:
    try:
        from services.turn_ledger import current_turn

        rec = current_turn()
    except Exception:  # noqa: BLE001 — no ledger, no turn
        return None
    return rec.id if rec is not None else None


def _note_shown(turn_id: str, ids: list[str]) -> None:
    _SHOWN_BY_TURN.setdefault(turn_id, set()).update(ids)
    while len(_SHOWN_BY_TURN) > _SHOWN_BY_TURN_MAX:
        _SHOWN_BY_TURN.pop(next(iter(_SHOWN_BY_TURN)))


# An answer cites a memory when it reuses the memory's distinctive words
# that the owner's own message did not already carry: at least
# CITATION_MIN_SHARED_WORDS, and a quarter of them for a long memory, up to
# CITATION_MAX_REQUIRED_WORDS. Names shared by every row (the owner's names
# from ``services.owner``, Edward), common words and dates (month and day
# names, years) never count. Deliberately strict: a missed citation costs a
# row one step of promotion; a false one is the entrenchment this replaces.
CITATION_MIN_SHARED_WORDS = 2
CITATION_MAX_REQUIRED_WORDS = 4
CITATION_WORD_FRACTION = 0.25
_CITATION_WORD = re.compile(r"[a-z0-9]+")
# The fixed words. The owner's own names are added per call
# (``_citation_ignored``), from ``owner.owner_names()``.
_CITATION_IGNORED = frozenset("""
owner owners edward the and for that with this have has had was were are from about his her hers their
theirs they them she him you your yours our ours not but all any can will would should could what
when where which who whom how why into onto than then there here also just only very more most some
such each every other same been being did does done its let may might must one two per via out off
over under again still now today tomorrow tonight yesterday day days time times get got gets make
made like want wants know knows said says say tell told ask asked use used uses need needs see saw
going goes went come came yes okay sure thanks thank please well good great new old first last next
week weeks month months year years don doesn didn isn aren wasn won mine too back
""".split())
# Dates are not what a memory says (memory audit review, 2026-09-27): "On
# July 10-12, 2026, <a friend> sent <the owner>…" was "cited" by any answer
# naming that friend and July — the month, the year and the name were three
# of its eight words, and the dated episodes are exactly the rows that
# climbed by display.
_CITATION_DATE_WORDS = frozenset("""
january february march april june july august september sept october november december
jan feb mar apr jun jul aug sep oct nov dec
monday tuesday wednesday thursday friday saturday sunday mon tue tues wed thu thur thurs fri sat sun
""".split())
_CITATION_YEAR = re.compile(r"(?:19|20)\d\d")


def _citation_ignored() -> frozenset:
    """The fixed ignored words plus every word of the owner's configured
    names, bare and with a possessive "s" ("Sam's" tokenises as "sams")."""
    names = set()
    for name in owner_names():
        for word in _CITATION_WORD.findall(name):
            names.update((word, word + "s"))
    return _CITATION_IGNORED | names if names else _CITATION_IGNORED


def _citation_words(text: str, ignored: Optional[frozenset] = None) -> set:
    ignored = _citation_ignored() if ignored is None else ignored
    return {w for w in _CITATION_WORD.findall((text or "").lower())
            if len(w) >= 3 and w not in ignored and w not in _CITATION_DATE_WORDS
            and not _CITATION_YEAR.fullmatch(w)}


def cited_memory_ids(answer: str, memories: list, prompt: str = "") -> list[str]:
    """The ids of the memories this answer drew on, in the given order. Pure.

    ``prompt`` is what the turn was asked (the owner's message): words the
    answer only echoes from it are not evidence the memory was used — the
    owner saying "remember the dog eats salmon kibble" and Edward repeating
    it back cites nothing."""
    ignored = _citation_ignored()
    said = _citation_words(answer, ignored)
    if not said:
        return []
    asked = _citation_words(prompt, ignored)
    out: list[str] = []
    for m in memories or []:
        mid = getattr(m, "id", None)
        words = _citation_words(getattr(m, "content", "") or "", ignored)
        if not mid or not words:
            continue
        needed = max(CITATION_MIN_SHARED_WORDS,
                     min(CITATION_MAX_REQUIRED_WORDS, math.ceil(len(words) * CITATION_WORD_FRACTION)))
        if len((words & said) - asked) >= needed and mid not in out:
            out.append(mid)
    return out


def _answer_and_prompt(messages: List[dict]) -> tuple[str, str]:
    """The turn's answer (assistant text after the last human message) and
    that human message."""
    last_human = -1
    for i, msg in enumerate(messages or []):
        if (msg.get("role") or "").lower() in ("human", "user"):
            last_human = i
    prompt = messages[last_human].get("content", "") if last_human >= 0 else ""
    answer = " ".join(
        str(msg.get("content") or "") for msg in (messages or [])[last_human + 1:]
        if (msg.get("role") or "").lower() in ("assistant", "ai")
    )
    return answer, prompt if isinstance(prompt, str) else str(prompt)


async def credit_cited_memories(messages: List[dict], candidates: Optional[List["Memory"]]) -> list[str]:
    """Credit a use to each memory the turn showed and its answer drew on.
    Runs inside the turn's context (post-turn extraction); outside a turn
    display was already credited, so this does nothing."""
    turn_id = _current_turn_id()
    if turn_id is None:
        return []
    shown = _SHOWN_BY_TURN.pop(turn_id, None)
    pool = [m for m in candidates or [] if getattr(m, "id", None) and (shown is None or m.id in shown)]
    answer, prompt = _answer_and_prompt(messages)
    cited = cited_memory_ids(answer, pool, prompt=prompt)
    if cited:
        await _credit_use(cited)
    return cited


async def _spread_connected_memories(
    primary_ids: set[str],
    min_confidence: float,
    cap: int,
) -> List[Memory]:
    """Pull in memories linked to any primary hit via ``memory_connections``.

    Connected memories that weren't surfaced by the primary hybrid search are
    added as supporting context. Dedupes against primary hits, honors the
    confidence floor, and caps at ``cap`` total extras per query.

    Score is assigned as ``0.15 + 0.05 * strength`` so these ride in as
    lightweight supporting context without competing with genuine top hits.

    Live rows only (2026-09-27): retiring a row leaves its connections in
    place, so without retrieval's ``valid_until`` clause here a retired row
    — the memory audit retired ~600, many sensitive — came back through
    any live neighbour. The filter comes before the cap, and so does the
    confidence floor.
    """
    if not primary_ids or cap <= 0:
        return []

    from sqlalchemy import or_ as sql_or
    from services.database import MemoryConnectionModel

    async with async_session() as session:
        conn_result = await session.execute(
            select(MemoryConnectionModel).where(
                sql_or(
                    MemoryConnectionModel.memory_id_a.in_(primary_ids),
                    MemoryConnectionModel.memory_id_b.in_(primary_ids),
                )
            )
        )
        connections = list(conn_result.scalars().all())

    # Pick the strongest connection per candidate id (one candidate can be
    # connected to multiple primaries); then exclude already-retrieved ids.
    best_strength: dict[str, float] = {}
    for c in connections:
        for candidate in (c.memory_id_a, c.memory_id_b):
            if candidate in primary_ids:
                continue
            prev = best_strength.get(candidate, 0.0)
            if c.strength > prev:
                best_strength[candidate] = c.strength

    if not best_strength:
        return []

    # Load every live candidate, then rank by strength and take the top
    # ``cap`` — capped first, strong edges to retired rows took the slots
    # and yielded nothing.
    candidate_ids = list(best_strength)

    async with async_session() as session:
        mem_rows = await session.execute(
            text(
                """SELECT id, content, memory_type, importance, source_conversation_id,
                          created_at, updated_at, last_accessed, access_count,
                          temporal_nature, tier, reinforcement_count, user_id,
                          source_type, confidence, origin, about
                     FROM memories
                    WHERE id = ANY(:ids)
                      AND (valid_until IS NULL OR valid_until > (NOW() AT TIME ZONE 'UTC'))"""
            ),
            {"ids": candidate_ids},
        )
        rows_by_id = {row.id: row for row in mem_rows.fetchall()}

    # The confidence floor comes before the cap too (integration review,
    # 2026-09-27): applied after, a strong edge to a low-confidence row took
    # a slot and yielded nothing.
    def _confident(row) -> bool:
        return (row.confidence if row.confidence is not None else 0.5) >= min_confidence

    top_candidates = sorted(
        ((cid, s) for cid, s in best_strength.items() if cid in rows_by_id and _confident(rows_by_id[cid])),
        key=lambda kv: kv[1], reverse=True,
    )[:cap]

    results: List[Memory] = []
    for cid, strength in top_candidates:
        row = rows_by_id[cid]
        results.append(
            Memory(
                id=row.id,
                content=row.content,
                memory_type=row.memory_type,
                importance=row.importance or 0.5,
                source_conversation_id=row.source_conversation_id,
                created_at=row.created_at,
                updated_at=getattr(row, "updated_at", None),
                last_accessed=row.last_accessed,
                access_count=row.access_count or 0,
                temporal_nature=row.temporal_nature or "timeless",
                tier=row.tier or "observation",
                reinforcement_count=getattr(row, "reinforcement_count", 0) or 0,
                source_type=getattr(row, "source_type", "inferred") or "inferred",
                confidence=row.confidence if row.confidence is not None else 0.5,
                origin=getattr(row, "origin", "heard") or "heard",
                about=getattr(row, "about", "owner") or "owner",
                user_id=getattr(row, "user_id", None),
                score=0.15 + 0.05 * float(strength or 0.0),
            )
        )
    return results


# A ``temporary`` instruction with no horizon lapses after this long
# (memory audit, 2026-09-27). Decay never retires an instruction,
# so the 38 live temporary instructions without a ``valid_until`` — mostly
# one-off reminder guidance — were permanent. Thirty
# days is the horizon a recurring event's intention already gets
# (``scheduled_events_service``), and gives four Sunday reflections the
# chance to renew one that still matters; a rule the owner means for good
# is ``timeless`` or ``evolving`` and never gets one. Rows already stored
# are left as they are.
#
# The owner's own word never gets one (pre-deploy review, 2026-09-27): the
# decay pass exempts it from its own 30-day horizon (``memory_decay_service.
# owners_own_word``), but a row stored with a horizon is never looked at by
# decay again, so "for now, don't text <a friend>", said in chat, left the
# pack's standing instructions a month later with no notice. One rule for
# both. Nor does sensitive content (integration review, 2026-09-27), as
# with a preference below: a pet's medication schedule relayed from a pet
# sitter got 30 days and slipped past the pass's hold for the owner. Decay
# decides it — it holds what the owner said or Edward read, and lapses
# Edward's own one-off on its own 30-day horizon.
TEMPORARY_INSTRUCTION_DEFAULT_DAYS = 30

# A ``temporary`` preference with no horizon lapses after this long (memory
# audit, preferences, 2026-09-27). About a fifth of the 332 live preferences
# were one-offs — an order, a weekend, a trip, "that night" — and 67 of the
# 68 had no ``valid_until``; the decay curve gave a used, belief-tier one a
# half-life of 500–900 days. Unlike an instruction, whoever said it: a
# one-off is a one-off even in the owner's own words. A taste mislabelled
# as one lasts once the owner states it as lasting: inside the window their
# own turn takes the horizon off (``_owners_lasting_word``), after it their
# words are stored again. Sensitive content (``memory_decay_service.
# is_sensitive``) gets none — a row with a horizon is never looked at by
# decay again, so it would slip past the pass's hold for the owner.
TEMPORARY_PREFERENCE_DEFAULT_DAYS = 30


def default_valid_until(memory_type: Optional[str], temporal_nature: Optional[str],
                        valid_until: Optional[datetime], now: Optional[datetime] = None,
                        owners_own: bool = False, content: Optional[str] = None) -> Optional[datetime]:
    """The horizon a new row is stored with: its own, or — for a temporary
    instruction that named none and is not the owner's own word
    (``owners_own``) —
    ``now + TEMPORARY_INSTRUCTION_DEFAULT_DAYS``, or for a temporary
    preference that named none — ``now + TEMPORARY_PREFERENCE_DEFAULT_DAYS``
    (naive UTC). Neither when the ``content`` is sensitive: decay decides.
    Pure."""
    if valid_until is not None or temporal_nature != "temporary":
        return valid_until
    if memory_type == "instruction" and not owners_own:
        days = TEMPORARY_INSTRUCTION_DEFAULT_DAYS
    elif memory_type == "preference":
        days = TEMPORARY_PREFERENCE_DEFAULT_DAYS
    else:
        return valid_until
    from services.memory_decay_service import is_sensitive

    if is_sensitive(content):
        return valid_until
    base = now or datetime.now(timezone.utc)
    if base.tzinfo is not None:
        base = base.astimezone(timezone.utc).replace(tzinfo=None)
    return base + timedelta(days=days)


async def _is_owners_own_word(memory: "Memory") -> bool:
    """``memory_decay_service.owners_own_word`` for a row about to be stored:
    confirmed by the owner, or heard in their own chat — by its turn, or by its
    conversation's source when no turn was recorded (one read)."""
    from services.memory_decay_service import owners_own_word

    if owners_own_word(memory):
        return True
    conversation = getattr(memory, "source_conversation_id", None)
    if not conversation or (getattr(memory, "origin", None) or "heard") != "heard":
        return False
    from services.database import ConversationModel

    try:
        async with async_session() as session:
            source = (await session.execute(
                select(ConversationModel.source).where(ConversationModel.id == conversation)
            )).scalar_one_or_none()
    except Exception:  # noqa: BLE001 — unknown is "not theirs": the horizon applies
        return False
    return owners_own_word(memory, conversation_source=source)


async def _default_horizon(memory: Memory) -> Optional[datetime]:
    """``default_valid_until`` for a row about to be written — stored new,
    or its words written over an existing row (an UPDATE)."""
    if memory.valid_until is not None or memory.temporal_nature != "temporary" \
            or memory.memory_type not in ("instruction", "preference"):
        return memory.valid_until
    owners_own = memory.memory_type == "instruction" and await _is_owners_own_word(memory)
    return default_valid_until(memory.memory_type, memory.temporal_nature, None, owners_own=owners_own,
                               content=memory.content)


async def _update_lifetime(existing: Memory, memory: Memory, turn_source: Optional[str] = None) -> dict:
    """The lifetime fields conflict detection's UPDATE writes along with the
    new words (``update_memory`` keyword arguments).

    A preference's words set its lifetime either way (memory audit,
    preferences, 2026-09-27): a one-off takes its horizon, a lasting taste
    drops a one-off's. Any other type keeps a window it already has — the
    owner's 24-hour stand-down restated with no window, a wake's 72-hour intention
    or one a linked ``wake_me`` stretched, a two-week note from a caller
    that names no nature — and takes the new words' horizon only when it
    had none; when it had none, a one-off's words bring ``temporary`` even
    with no horizon to go with it (sensitive text, the owner's own word).

    The owner's row (``heard``/``owner``, or a ``owner_confirmed`` pin) takes
    none of it unless the new words are their own, said in their own turn
    (``_is_owners_own_heard``; integration review, 2026-09-27): Edward's
    ``thought`` instruction from the owner's chat gave a confirmed pin a
    30-day lapse with ``owner_confirmed`` still on top. ``turn_source``
    defaults to the current turn's."""
    if turn_source is None:
        turn_source = current_turn_source()
    if (_is_owners_row(existing) or is_owner_confirmed(existing)) and not _is_owners_own_heard(memory, turn_source):
        return {}
    horizon = await _default_horizon(memory)
    if memory.memory_type == "preference":
        return {"temporal_nature": memory.temporal_nature, "valid_until": horizon,
                "clear_valid_until": horizon is None}
    if existing.valid_until is None and (horizon is not None or memory.temporal_nature == "temporary"):
        # A one-off with no horizon (sensitive text, or the owner's own
        # word) still brings its nature (verification of the integration
        # fixes, 2026-09-27): kept ``evolving`` under "For this week, give
        # the dog her ear drops…", Edward's own row was an instruction held
        # for good. Decay decides it — it lapses his one-off, holds the
        # owner's.
        return {"temporal_nature": memory.temporal_nature,
                **({"valid_until": horizon} if horizon is not None else {})}
    return {}


async def store_memory(memory: Memory) -> Memory:
    """Store a single memory in the database."""
    memory.valid_until = await _default_horizon(memory)
    async with async_session() as session:
        memory_id = memory.id or str(uuid.uuid4())
        embedding = get_embedding(memory.content)

        db_memory = MemoryModel(
            id=memory_id,
            content=memory.content,
            embedding=embedding,
            memory_type=memory.memory_type,
            importance=memory.importance,
            temporal_nature=memory.temporal_nature,
            tier=memory.tier,
            reinforcement_count=memory.reinforcement_count,
            source_type=memory.source_type,
            confidence=memory.confidence,
            source_conversation_id=memory.source_conversation_id,
            valid_until=memory.valid_until,
            origin=memory.origin or "heard",
            about=memory.about or "owner",
            source_ref=memory.source_ref,
        )

        session.add(db_memory)
        await session.commit()
        await session.refresh(db_memory)

        memory.id = db_memory.id
        memory.created_at = db_memory.created_at

    # C Phase 2 — dual-write to Graphiti (Kuzu-backed) after the pgvector
    # commit succeeds. Fire-and-forget: a Graphiti glitch must never fail
    # a pgvector write. Feature-flagged; no-op when GRAPHITI_ENABLED is
    # unset.
    try:
        from services.graph_memory_service import add_episode_from_memory, is_enabled
        if is_enabled():
            asyncio.create_task(add_episode_from_memory(memory))
    except Exception as exc:
        # Intentionally swallowed — store_memory must stay resilient.
        print(f"[store_memory] graphiti dual-write skipped: {exc}")

    return memory


async def store_memories(memories: List[Memory]) -> List[Memory]:
    """Store multiple memories in the database."""
    stored = []
    for memory in memories:
        stored.append(await store_memory(memory))
    return stored


async def _llm_merge_content(content_a: str, content_b: str, model: str = None) -> str:
    """Use background LLM to merge two memory contents into one clean version."""
    llm = build_background_llm(max_tokens=256)
    prompt = f"""Merge these two related memories into one clear, concise memory:

Memory A: {content_a}
Memory B: {content_b}

Return ONLY the merged text, nothing else."""
    response = await llm.ainvoke([
        make_background_system_message(
            "Merge related memories into one clear, concise version. Return ONLY the merged text.",
        ),
        HumanMessage(content=prompt),
    ])
    text = response.content
    if isinstance(text, list):
        text = " ".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in text)
    return text.strip()


# Turns whose words are Edward's own, not a fresh restatement by anyone
# (2026-09-27): a wake's pack carries his standing instructions,
# intentions and thoughts back to him every time, the pulse is his own
# composition, and a scheduled event's trigger is a reminder he wrote.
# Re-extracting from them is an echo — one standing instruction went from
# 22 to 30 reinforcements in one night of free and morning wakes, and
# another reached knowledge at 22. From these a near-duplicate is seen
# again, not said again.
ECHO_TURN_SOURCES = ("mind", "pulse", "scheduled_event")
ECHO_PROFILES = ("mind", "scheduled_event")

_TIER_RANK = {"observation": 0, "belief": 1, "knowledge": 2}


def current_turn_source() -> Optional[str]:
    try:
        from services.turn_ledger import current_turn

        rec = current_turn()
    except Exception:  # noqa: BLE001 — a missing turn is an ordinary write
        return None
    return rec.source if rec is not None else None


def counts_as_restatement(source_ref: Optional[str], turn_source: Optional[str] = None) -> bool:
    """Whether a near-duplicate from this source may reinforce the row it
    matched. ``source_ref`` is the incoming memory's provenance JSON;
    ``turn_source`` the current turn's, used when the ref does not say. Pure."""
    from services.provenance_service import parse_source_ref

    ref = parse_source_ref(source_ref)
    if ref.get("profile") in ECHO_PROFILES:
        return False
    source = ref.get("turn_source") or turn_source
    return source not in ECHO_TURN_SOURCES


def is_owners_word(source_ref: Optional[str], turn_source: Optional[str] = None) -> bool:
    """Whether the memory being stored came from a turn in which the owner
    is speaking — the only kind of turn that may change the words of a row
    that is theirs (``heard``/``owner``). ``source_ref`` is the incoming
    memory's provenance JSON; ``turn_source`` the current turn's, used when
    the ref does not say. Pure."""
    from services.provenance_service import parse_source_ref

    ref = parse_source_ref(source_ref)
    profile = ref.get("profile") or "chat"
    if profile in NOT_OWNER_SPEAKING_PROFILES or profile == "heartbeat":
        return False
    return is_owners_own_turn(ref.get("turn_source") or turn_source, profile)


def _is_owners_row(memory: "Memory") -> bool:
    return (getattr(memory, "origin", None), getattr(memory, "about", None)) == ("heard", "owner")


def is_owner_confirmed(memory: Any) -> bool:
    """A rule the owner confirmed themselves — the memory audit's pins
    (``source_ref {"kind": "owner_confirmed"}``). Pure."""
    from services.memory_decay_service import OWNER_CONFIRMED_KIND, _source_ref

    return _source_ref(memory).get("kind") == OWNER_CONFIRMED_KIND


def _is_owners_own_heard(memory: "Memory", turn_source: Optional[str] = None) -> bool:
    """Whether the memory being stored is what the owner themselves said
    (``heard``, the default) in a turn in which they are speaking — the only
    words that may rewrite a row of theirs. A ``thought`` or ``read`` row
    from the owner's chat turn is Edward's: his ``remember``, or extraction
    of his own answer. Pure."""
    return (getattr(memory, "origin", None) or "heard") == "heard" \
        and is_owners_word(getattr(memory, "source_ref", None), turn_source)


def _owners_lasting_word(existing: "Memory", memory: "Memory", turn_source: Optional[str] = None) -> dict:
    """What a reinforcement or MERGE writes when the owner, in their own
    turn, states as lasting a taste the row holds as a one-off: its nature,
    and no horizon (``update_memory`` keyword arguments; else none).
    ``remember`` writes a preference as a thought, and a thought is
    temporary — without this, the owner saying "I love spicy food" inside
    the 30 days reinforced the one-off and the taste lapsed with it. Pure."""
    if memory.memory_type != "preference" or existing.memory_type != "preference" \
            or existing.temporal_nature != "temporary" or existing.valid_until is None \
            or memory.temporal_nature == "temporary" or memory.valid_until is not None \
            or (memory.origin or "heard") != "heard" or not is_owners_word(memory.source_ref, turn_source):
        return {}
    return {"temporal_nature": memory.temporal_nature, "clear_valid_until": True}


def _is_one_off(memory: "Memory") -> bool:
    """``temporary``, or ends at a horizon. Pure."""
    return (getattr(memory, "temporal_nature", None) or "timeless") == "temporary" \
        or getattr(memory, "valid_until", None) is not None


def _lifetimes_apart(existing: "Memory", memory: "Memory", turn_source: Optional[str] = None) -> bool:
    """Whether a reinforcement or MERGE of ``memory`` into ``existing``
    would fold a lasting statement into a one-off, or a one-off into a
    lasting statement (integration review, 2026-09-27): either keeps the
    existing row's lifetime, so a heartbeat-read "<the owner> likes oat
    milk in their latte" reinforced "…this week" and lapsed with it, and
    "for Saturday's order" merged into an open-ended "likes". Only the
    owner, in their own turn, says a one-off taste lasts, and only where
    ``_owners_lasting_word`` writes it so; their own words are otherwise
    kept apart like anyone's (verification of the integration fixes,
    2026-09-27: their "until Friday" exception, at 0.920 to the pin,
    reinforced it and was stored nowhere; merged into their lasting row, a
    one-off's words became permanent). Intentions keep their own hygiene — a wake's 72-hour
    horizon is plumbing, not what the plan says. Pure."""
    if "intention" in (existing.memory_type, memory.memory_type):
        return False
    if _owners_lasting_word(existing, memory, turn_source):
        return False
    return _is_one_off(existing) != _is_one_off(memory)


def is_passive_relift(source_ref: Optional[str], turn_source: Optional[str] = None,
                      origin: Optional[str] = None) -> bool:
    """Whether the memory being stored is post-turn extraction from a turn
    in which the owner is not speaking — words that may be an earlier pack,
    an old message in the 10-message window, a row the turn was shown — as
    opposed to the owner saying it or Edward deciding it (``remember``).
    Such a re-lift must not bring a retired row back (memory audit review,
    2026-09-27: a wake re-lifting the intention it had just closed stored
    it again, live for 72 hours).

    In the owner's own turn only what the owner said (``origin`` heard, the
    default) is a restatement (pre-deploy review, 2026-09-27): the window
    holds Edward's own earlier answers too — and a journal entry's
    narrative — so an intention, thought or record lifted from them is a
    re-lift like a wake's. One more message from the owner used to reopen
    an intention a wake had closed, open-ended. Pure."""
    from services.provenance_service import parse_source_ref

    if parse_source_ref(source_ref).get("kind") != "extraction":
        return False
    return not is_owners_word(source_ref, turn_source) or (origin or "heard") != "heard"


def _is_live(memory: "Memory", now: Optional[datetime] = None) -> bool:
    until = getattr(memory, "valid_until", None)
    if until is None:
        return True
    if until.tzinfo is not None:
        until = until.astimezone(timezone.utc).replace(tzinfo=None)
    return until > (now or datetime.utcnow())


def _higher_tier(current: Optional[str], proposed: str) -> str:
    """Reinforcement promotes, never demotes: ``_compute_tier(count)`` here
    ignores use, so a knowledge row restated once more used to drop to
    belief until consolidation promoted it back."""
    if _TIER_RANK.get(current or "observation", 0) >= _TIER_RANK.get(proposed, 0):
        return current or "observation"
    return proposed


async def touch_memory_used(memory_id: str) -> None:
    """Stamp ``last_used_at`` (naive UTC from Python — the server's now()
    writes the session zone into this naive-UTC column) and nothing else."""
    async with async_session() as session:
        await session.execute(
            update(MemoryModel).where(MemoryModel.id == memory_id)
            .values(last_used_at=datetime.utcnow())
        )
        await session.commit()


async def store_memories_with_conflict_detection(
    memories: List[Memory],
    similarity_threshold: float = 0.75
) -> List[Memory]:
    """
    Store memories with semantic conflict detection and reinforcement.

    For each memory:
    - Similarity >= 0.92: Pure reinforcement (increment count, recompute tier, no LLM)
    - Similarity 0.75-0.92: LLM conflict resolution (UPDATE/MERGE/KEEP_BOTH/DISCARD)
    - No match: Store as new observation

    From an echo source (``counts_as_restatement`` is False: a wake, the
    pulse, a scheduled event) a >= 0.92 match only refreshes
    ``last_used_at``, and an UPDATE or MERGE changes the content without
    adding a reinforcement.

    A lasting statement and a one-off are not folded together
    (``_lifetimes_apart``): a >= 0.92 reinforcement or a MERGE between the
    two keeps both — the owner's own words too, unless they are stating a
    one-off taste as lasting (``_owners_lasting_word``).

    A row that is the owner's (``heard``/``owner``) is rewritten by an
    UPDATE or MERGE only by what the owner said in a turn in which they
    speak (``_is_owners_own_heard``); anything else — another turn, or
    Edward's own thought in the owner's turn — is kept beside it
    (KEEP_BOTH). A rule the owner pinned (``is_owner_confirmed``) is never
    rewritten here, even by them: their exception is kept beside it and
    lapses on its own (above 0.92 by the lifetime rule, below it by this
    guard).

    Args:
        memories: List of memories to store
        similarity_threshold: Minimum similarity to trigger conflict check

    Returns:
        List of stored/updated Memory objects
    """
    stored = []

    for memory in memories:
        try:
            turn_src = current_turn_source()
            relift = is_passive_relift(memory.source_ref, turn_src, getattr(memory, "origin", None))
            # The owner's turns and deliberate writes meet live rows only (a fact
            # restated after decay, a decision taken again after
            # close_intention, is stored again); a re-lift meets retired
            # rows too, so it cannot bring one back.
            similar = await find_similar_memories(
                memory.content,
                threshold=similarity_threshold,
                limit=5,
                live_only=not relift,
            )
            if relift and similar and not _is_live(similar[0][0]):
                retired, score = similar[0]
                if score < 0.92:
                    known = (await resolve_memory_conflict(retired, memory, score)).action != "KEEP_BOTH"
                else:
                    known = True
                if known:
                    # Already known, and retired on purpose (closed, decayed,
                    # a contradiction the owner settled): not stored, not touched.
                    print(f"Memory already known (retired, score={score:.2f}): {retired.content[:50]}...")
                    continue
            similar = [(m, s) for m, s in similar if _is_live(m)]

            if not similar:
                # No conflicts, store as new observation
                memory.tier = "observation"
                memory.reinforcement_count = 0
                stored.append(await store_memory(memory))
                print(f"Memory stored (new observation): {memory.content[:50]}...")
                continue

            # Handle the most similar memory
            existing, similarity = similar[0]
            print(f"Found similar memory (score={similarity:.2f}): {existing.content[:50]}...")
            # Provenance (2026-09-24): a reinforced row keeps where it first
            # came from and notes this source under ``also``.
            from services.provenance_service import build_source_ref, dump_source_ref, merge_source_ref
            existing_ref = getattr(existing, "source_ref", None)
            if not existing_ref and memory.source_ref:
                # A row stored before provenance keeps "not recorded" on top
                # (one from the owner's chat came to read as a wake's).
                # merge_source_ref does that itself now; this adds the row's
                # own thread, which the helper cannot know.
                existing_ref = dump_source_ref(build_source_ref(
                    "unrecorded", conversation_id=existing.source_conversation_id))
            merged_ref = merge_source_ref(existing_ref, memory.source_ref)
            restated = counts_as_restatement(memory.source_ref, turn_src)
            new_count = (existing.reinforcement_count or 0) + 1
            reinforcement = (
                {"reinforcement_count": new_count,
                 "tier": _higher_tier(existing.tier, _compute_tier(new_count, content=existing.content))}
                if restated else {}
            )

            apart = _lifetimes_apart(existing, memory, turn_src)

            if similarity >= 0.92 and not restated:
                # An echo of what he already holds: seen again, not said again.
                await touch_memory_used(existing.id)
                stored.append(existing)
                print(f"Memory seen again (echo, not reinforced): {existing.content[:50]}...")
            elif similarity >= 0.92 and apart:
                # A lasting statement is not a one-off said again, nor the
                # reverse: each keeps its own lifetime, as its own row.
                memory.tier = "observation"
                memory.reinforcement_count = 0
                stored.append(await store_memory(memory))
                print(f"Memory stored (lifetime differs from a near-duplicate): {memory.content[:50]}...")
            elif similarity >= 0.92:
                # Pure reinforcement — no LLM needed
                updated = await update_memory(
                    existing.id,
                    importance=max(existing.importance, memory.importance),
                    source_ref=merged_ref,
                    **reinforcement,
                    **_owners_lasting_word(existing, memory, turn_src),
                )
                if updated:
                    stored.append(updated)
                    print(f"Memory reinforced (count={new_count}, tier={updated.tier}): {updated.content[:50]}...")
            else:
                # Similarity 0.75-0.92 — use LLM conflict resolution
                resolution = await resolve_memory_conflict(existing, memory, similarity)
                print(f"Conflict resolution: {resolution.action} - {resolution.reason}")
                if resolution.action in ("UPDATE", "MERGE") and is_owner_confirmed(existing):
                    # A rule the owner pinned stays as they confirmed it
                    # (integration review, 2026-09-27): the resolver sees
                    # neither side's lifetime, and chose UPDATE for their
                    # "Until Friday, don't reply to @edward mentions" over
                    # the pin — the pin took Friday's lapse. The exception is
                    # its own row and lapses on its own; the pin changes only
                    # by hand.
                    print(f"Not rewriting a rule the owner pinned ({resolution.action}): "
                          f"{existing.content[:50]}...")
                    resolution = ConflictResolution(action="KEEP_BOTH", reason=resolution.reason)
                elif resolution.action in ("UPDATE", "MERGE") and _is_owners_row(existing) \
                        and not _is_owners_own_heard(memory, turn_src):
                    # What the owner said changes only when the owner says
                    # it (2026-09-27): the row keeps its origin, about and
                    # the owner's ref on top, so a wake's paraphrase — or a
                    # contact's instruction from a heartbeat thread —
                    # written into it rendered under the pack's "Standing
                    # instructions from <owner>". Nor Edward's own thought
                    # from the owner's turn (integration review, 2026-09-27:
                    # a ``remember``d instruction put its words and its
                    # 30-day lapse on the owner's row). Kept beside it, as
                    # its own row.
                    print(f"Not rewriting the owner's words ({resolution.action} not in their own words): "
                          f"{existing.content[:50]}...")
                    resolution = ConflictResolution(action="KEEP_BOTH", reason=resolution.reason)
                elif resolution.action == "MERGE" and apart:
                    # The merged row would keep one side's lifetime for both.
                    print(f"Not merging a lasting statement with a one-off: {existing.content[:50]}...")
                    resolution = ConflictResolution(action="KEEP_BOTH", reason=resolution.reason)

                if resolution.action == "UPDATE":
                    # The new words bring their own lifetime (memory audit,
                    # preferences, 2026-09-27): "<a friend> wanted salmon
                    # nigiri tonight" written over "<a friend> likes salmon
                    # nigiri" kept the old row's open-ended life.
                    updated = await update_memory(
                        existing.id,
                        content=memory.content,
                        memory_type=memory.memory_type,
                        importance=max(existing.importance, memory.importance),
                        source_ref=merged_ref,
                        **reinforcement,
                        **(await _update_lifetime(existing, memory, turn_src)),
                    )
                    if updated:
                        stored.append(updated)
                        print(f"Memory updated (reinforced={restated}): {updated.content[:50]}...")

                elif resolution.action == "MERGE":
                    merged_content = await _llm_merge_content(existing.content, memory.content)
                    updated = await update_memory(
                        existing.id,
                        content=merged_content,
                        importance=max(existing.importance, memory.importance),
                        source_ref=merged_ref,
                        **reinforcement,
                        **_owners_lasting_word(existing, memory, turn_src),
                    )
                    if updated:
                        stored.append(updated)
                        print(f"Memory merged (reinforced={restated}): {updated.content[:50]}...")

                elif resolution.action == "KEEP_BOTH":
                    memory.tier = "observation"
                    memory.reinforcement_count = 0
                    stored.append(await store_memory(memory))
                    print(f"Memory stored (keeping both): {memory.content[:50]}...")

                elif resolution.action == "DISCARD":
                    print(f"Memory discarded: {memory.content[:50]}...")

        except Exception as e:
            print(f"Error processing memory with conflict detection: {e}")
            # Fallback: store without conflict detection
            try:
                memory.tier = "observation"
                memory.reinforcement_count = 0
                stored.append(await store_memory(memory))
            except Exception as e2:
                print(f"Fallback storage also failed: {e2}")

    return stored


# Model-facing: "{OWNER}" is rendered once at import (``owner.owner_text``),
# before ``build_extraction_prompt``'s ``.format`` fills the rest — the
# token is never doubled, and the doubled JSON braces stay as they are.
MEMORY_EXTRACTION_PROMPT = owner_text("""Analyze the following conversation and extract important information that should be remembered for future conversations.

Focus on:
1. **Personal facts** - Name, job, location, family, hobbies, etc.
2. **Preferences** - Likes, dislikes, communication style preferences
3. **Context** - Ongoing projects, situations, goals, challenges
4. **Instructions** - How the user wants the assistant to behave, format responses, etc.

For each piece of information, assess its importance (0-1 scale):
- 1.0: Critical identity info or explicit instructions
- 0.7-0.9: Important preferences or ongoing context
- 0.4-0.6: Useful background information
- 0.1-0.3: Minor details that might be relevant

Also classify each memory's temporal nature:
- "timeless": Permanent facts unlikely to change (identity, allergies, family relationships) and a taste held for years ("{OWNER} dislikes bananas")
- "temporary": Time-bound context that expires (current projects, upcoming events, temporary situations), and any preference tied to one occasion, order, meal, trip, weekend, purchase or event, or to "right now", "lately" or "this week"
- "evolving": Things that change over time and may be superseded (favorite restaurant, current job, tools in use, a taste that can change)
A preference someone else holds is theirs: "about": "contact", with their name — never {OWNER}'s.

Classify each memory's source:
- "user_stated": The user explicitly said this (e.g., "I'm allergic to shellfish", "I work night shifts")
- "inferred": You are inferring this from context (e.g., user seems to prefer morning meetings based on scheduling patterns)

Rate your confidence (0-1) in each memory's accuracy:
- 0.9-1.0: Explicitly stated by user or multiply verified
- 0.6-0.8: Strong inference from clear context
- 0.3-0.5: Reasonable inference, not confirmed
- 0.1-0.2: Speculative, weak signal
For inferences, confidence should rarely exceed 0.6 unless strongly supported by repeated context.

The current time is {now_utc} (UTC). If a memory is only valid for a stated window — "for 24 hours", "until Friday", "today", "this week" — add "valid_until" as an ISO-8601 UTC timestamp computed from the current time. Otherwise omit the field entirely. Never invent a window that was not stated.

Say how each memory came to be ("origin") and whom it concerns ("about"):
- "origin": "heard" when {OWNER} said it; "read" when you read it in a message, email, page or screen; "thought" when it is something YOU concluded or decided; "did" when it records an action you took and what came of it
- "about": "owner" for {OWNER} — their facts, preferences, plans, instructions to you; "contact" for someone else (a friend, family, a colleague); "self" for you — your own decisions, intentions, lessons; "world" for things that are about neither (a stock price, the weather, a product)
- A decision or a thing you chose to watch is "memory_type": "intention", "origin": "thought", "about": "self", with "valid_until" when it has a horizon

Return ONLY a valid JSON array. If nothing worth remembering, return [].

Example output:
[{{"content": "Jordan is a close friend from college who lives in Lisbon and prefers WhatsApp to texting", "memory_type": "fact", "importance": 0.8, "temporal_nature": "timeless", "source_type": "user_stated", "confidence": 0.95, "origin": "heard", "about": "contact"}},
{{"content": "{OWNER} is training for a half marathon in March", "memory_type": "context", "importance": 0.7, "temporal_nature": "temporary", "source_type": "user_stated", "confidence": 0.95, "origin": "heard", "about": "owner"}},
{{"content": "{OWNER} seems to prefer text-based communication over voice calls", "memory_type": "preference", "importance": 0.5, "temporal_nature": "evolving", "source_type": "inferred", "confidence": 0.4, "origin": "thought", "about": "owner"}},
{{"content": "{OWNER} wanted a low-interruption weekend on September 26-27, 2026", "memory_type": "preference", "importance": 0.5, "temporal_nature": "temporary", "source_type": "user_stated", "confidence": 0.9, "origin": "heard", "about": "owner"}},
{{"content": "{OWNER} is allergic to shellfish", "memory_type": "fact", "importance": 0.9, "temporal_nature": "timeless", "source_type": "user_stated", "confidence": 1.0, "origin": "heard", "about": "owner"}},
{{"content": "Edward should stay out of the 'Weekend plans' group thread for 24 hours ({OWNER} asked on 2026-09-04)", "memory_type": "instruction", "importance": 1.0, "temporal_nature": "temporary", "source_type": "user_stated", "confidence": 0.95, "origin": "heard", "about": "owner", "valid_until": "2026-09-05T16:00:00Z"}},
{{"content": "I decided to watch the stock of {OWNER}'s employer because their bonus depends on it; tell them if it moves more than 3%", "memory_type": "intention", "importance": 0.7, "temporal_nature": "evolving", "source_type": "inferred", "confidence": 0.8, "origin": "thought", "about": "self", "valid_until": "2026-10-18T00:00:00Z"}}]

IMPORTANT:
- Return ONLY the JSON array, no other text
- Make each memory self-contained and understandable without context
- Every memory about a person MUST include their name. Instead of "Lives in Lisbon", write "Jordan lives in Lisbon". Instead of "Communication method is WhatsApp", write "Jordan uses WhatsApp".
- Group closely related facts about the same person into a single memory when possible. Prefer fewer, richer memories over many fragmented ones.
- Don't store trivial greetings or small talk
- Don't duplicate information already known
- Be concise but complete
- {assistant_rule}
- You are Edward, an AI assistant. Never attribute other people's traits, preferences, food orders, phone numbers, or physical descriptions to yourself. If someone texts about "Edward" in a conversation you're reading, determine from context whether they mean you (the AI) or are talking about/to someone else. Only store memories about yourself if they are clearly about the AI assistant.
- When reading messages between other people, store facts about THEM, not about yourself. A message like "I want Red Robin" from a contact is THEIR preference, not yours.

{profile_addendum}Conversation:
{conversation}

Existing memories (don't duplicate these):
{existing_memories}""")

# Appended to the prompt for heartbeat contact threads only. Those threads
# are other people talking to the owner; on 2026-09-04 they produced 23 of
# the day's 53 memories and most were chatter (prices, an ETA, what a
# screen showed). Retrieval returns 5 rows per turn, so chatter crowds out
# what matters.
HEARTBEAT_EXTRACTION_ADDENDUM = owner_text("""THIS IS A MESSAGE THREAD BETWEEN {OWNER} AND A CONTACT. Keep the bar high:
- KEEP facts about {OWNER}: their plans, commitments, preferences, instructions to you.
- KEEP durable facts about the contact: who they are to {OWNER}, where they live, recurring commitments, their family and pets, health situations that will matter again.
- SKIP prices, ETAs, one-off reactions, greetings, jokes, logistics that resolve within the day, and any description of what a screen, board, or app showed at a moment.
- When unsure, skip. A missed detail costs one re-ask; a stored one is retrieved for months.

""")

# A wake's pack is the turn's "user" message, but Edward's own code wrote it
# (2026-09-27): the old "KEEP anything <the owner> said in the pack as heard"
# line turned his conclusions into standing instructions under the owner's
# name (heard/owner, profile mind), and every later wake re-extracted the
# pack's standing-instructions section and reinforced them — 22 to 30 in
# one night.
#
# The section names quoted in the SKIP line are ``mind_service``'s pack
# headings. The two that name the owner ("Standing instructions from
# {OWNER}", "{OWNER}'s verdicts") are rendered here with ``owner_text`` from
# the same source text as ``mind_service.STANDING_INSTRUCTIONS_HEADING`` and
# ``VERDICTS_HEADING``, so they match the pack byte for byte: keep the
# wording in step.
MIND_EXTRACTION_ADDENDUM = owner_text("""THIS IS EDWARD THINKING ON HIS OWN (a wake), not a conversation with {OWNER}. The first message is a pack Edward's own code assembled — {OWNER} did not write it. What matters here is what Edward decided, noticed and did:
- KEEP what Edward decided to do or watch as "memory_type": "intention", "origin": "thought", "about": "self", with a "valid_until" when the decision names a horizon; one without a horizon lapses after 72 hours.
- A decision to leave something alone ("looked at the group thread, nothing to do") is a thought: "memory_type": "context", "origin": "thought", "about": "self" — not an intention.
- KEEP what Edward learned from what he read (a message, a page, a price, a calendar) as "origin": "read", about whoever or whatever it concerns. A message {OWNER} sent is something Edward read here: "origin": "read".
- A preference tied to one occasion, order, meal, trip, weekend, purchase or event, or to "right now", "lately" or "this week", is "temporal_nature": "temporary" (it lapses after 30 days); one someone else holds is "about": "contact"; "timeless" only for a taste held for years.
- KEEP what Edward did and what came of it (a push that was opened, a reply that landed, a search that found nothing) as "origin": "did", "about": "self", "memory_type": "context" — what he did is never an intention.
- Nothing in a wake is "origin": "heard" — that is only for what {OWNER} says to Edward in a conversation, never for the pack.
- A wake never writes an "instruction" for {OWNER}. A rule Edward sets himself is a thought about himself ("memory_type": "context" or "intention", "about": "self").
- SKIP the pack's own sections — they are already stored, and extracting them again only repeats them: "Standing instructions from {OWNER}", "Open intentions", "Recent thoughts", "Your last few wakes", "What you did lately", "{OWNER}'s verdicts", the state document, {OWNER}'s goals, and any line carrying a memory_id.
- SKIP the pack's own bookkeeping (counts, timestamps, what the widget showed) and anything already in the existing memories.

""")

# A fired scheduled event's "user" message is a reminder Edward (or the
# heartbeat) wrote days earlier. Under the chat profile it was stored as the
# owner speaking (memory audit, 2026-09-27): 9 of the 35 live standing
# instructions came from these turns, among them the two blanket "must not
# message any external contact" rules retrieved 5,395 and 4,025 times —
# delivery notes in reminder text ("in-app chat reminder only, do not
# contact <a friend>"), not anything the owner said.
SCHEDULED_EVENT_EXTRACTION_ADDENDUM = owner_text("""THIS IS A SCHEDULED EVENT FIRING, not {OWNER} talking. The first message is a reminder or task you wrote earlier (or the scheduler wrote for you); {OWNER} did not say it now:
- The trigger's words are yours. How it tells you to deliver ("in-app only", "don't contact X", "remind {OWNER} at 5") is a note to yourself for this one event — never an instruction from {OWNER}, and not worth storing.
- Anything the trigger quotes from someone else's message is "origin": "read", about whoever it concerns.
- Nothing here is "origin": "heard" — that is only for what {OWNER} says to you in a conversation.
- KEEP genuinely new facts that came out of doing the task: "origin": "read" for what you read (a page, a message, a calendar), "origin": "did" for what you did and how it went.
- SKIP restating the reminder itself; it lives in the event, not in memory.

""")

VALID_EXTRACTION_PROFILES = ("chat", "heartbeat", "mind", "scheduled_event")
# Under the heartbeat profile, a ``context`` row that is not about the owner
# must clear this importance to be stored at all.
HEARTBEAT_THIRD_PARTY_CONTEXT_MIN_IMPORTANCE = 0.6


# The assistant's words are Edward's own prior output. They are never
# testimony about the owner (that is ``origin=heard`` and only the owner
# supplies it), but what Edward decided or committed to in them is his to
# remember.
ASSISTANT_TESTIMONY_RULE = owner_text(
    "The ASSISTANT's messages are YOUR prior output. Never store them as things {OWNER} said: "
    "a fact about {OWNER} is \"origin\": \"heard\" only when {OWNER} said it. If the assistant said "
    "\"I'll remember that you like sushi\", the memory is \"{OWNER} likes sushi\" from {OWNER}'s own message. "
    "What the assistant DECIDED or COMMITTED to (\"I'll keep an eye on that stock\", \"I'll text Jordan at 5\") "
    "IS worth keeping — as \"origin\": \"thought\", \"about\": \"self\", usually \"memory_type\": \"intention\"."
)


def build_extraction_prompt(
    conversation_text: str,
    existing_text: str,
    profile: str = "chat",
    now: Optional[datetime] = None,
) -> str:
    addendum = {
        "heartbeat": HEARTBEAT_EXTRACTION_ADDENDUM,
        "mind": MIND_EXTRACTION_ADDENDUM,
        "scheduled_event": SCHEDULED_EVENT_EXTRACTION_ADDENDUM,
    }.get(profile, "")
    now = now or datetime.now(timezone.utc)
    return MEMORY_EXTRACTION_PROMPT.format(
        conversation=conversation_text,
        existing_memories=existing_text,
        profile_addendum=addendum,
        assistant_rule=ASSISTANT_TESTIMONY_RULE,
        now_utc=now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def parse_valid_until(value, now: Optional[datetime] = None) -> Optional[datetime]:
    """ISO-8601 from the extractor -> naive UTC datetime, or None.

    Naive UTC matches how consolidation and the decay pass stamp
    retirement, so retrieval's ``valid_until > now()`` clause compares like
    with like. A value at or before ``now`` is rejected: the extractor
    hallucinated a date, and storing it would retire the memory on arrival.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if parsed <= now:
        return None
    return parsed.replace(tzinfo=None)


def should_store_under_profile(fields: dict, profile: str) -> bool:
    """The code-side floor behind the heartbeat addendum. The prompt asks the
    extractor to skip chatter; this catches what it lets through.

    Under ``scheduled_event`` an instruction about the owner is dropped: it
    can only be the trigger's own delivery note (memory audit, 2026-09-27)."""
    if profile == "scheduled_event":
        return not (fields.get("memory_type") == "instruction" and fields.get("about") == "owner")
    if profile != "heartbeat":
        return True
    if fields.get("memory_type") != "context" or fields.get("about_owner"):
        return True
    return float(fields.get("importance", 0.0)) >= HEARTBEAT_THIRD_PARTY_CONTEXT_MIN_IMPORTANCE


VALID_MEMORY_TYPES = ("fact", "preference", "context", "instruction", "intention")
VALID_TEMPORAL_NATURES = ("timeless", "temporary", "evolving")
VALID_SOURCE_TYPES = ("user_stated", "inferred", "tool_derived", "imported")
# Mind rebuild Phase 1. ``origin``: how a memory came to be. ``about``: whom
# it concerns. Both orthogonal to tier (observation/belief/knowledge).
VALID_ORIGINS = ("heard", "read", "thought", "did")
VALID_ABOUTS = ("owner", "contact", "self", "world")
# What Edward concludes about the owner is a belief, never testimony: an
# ``origin=thought`` memory ``about=owner`` is capped here.
THOUGHT_ABOUT_OWNER_MAX_CONFIDENCE = 0.6
# An intention a wake stores without a horizon lapses after this long
# (2026-09-25 audit: every such row stayed in "Open intentions" until a
# Sunday reflection happened to close it). One that should outlive it is
# renewed (`remember_update`) or written with `remember(valid_until=…)`.
MIND_INTENTION_DEFAULT_HOURS = 72


def default_temporal_nature(memory_type: str | None, origin: str | None = None) -> str:
    """The temporal_nature to assume when the extractor did not give one.

    A thought (``origin="thought"``) is ``temporary`` whatever its type
    unless it is an ``intention`` (Phase 9): Edward's own reflections fade
    unless they became intentions or were reinforced, so the store he has
    to hold in his head stops growing on its own.

    Defaulting everything to ``timeless`` put 690 of 1710 live rows into the
    longest-lived class by accident, including notes like "<a friend> said
    they are at the gym right now". Timeless is the worst possible fallback: it is the one
    value that never fades, so every extractor slip became permanent.

    ``context`` is situational by definition — a note about what someone is
    doing right now — so its safe default is ``temporary``. The types that
    describe things which persist keep ``timeless``.

    Defaults fail toward forgetting on purpose. A wrongly-forgotten context
    note costs one re-ask; a wrongly-immortal one pollutes every retrieval
    from here on.

    A ``preference`` is ``evolving`` (memory audit, preferences,
    2026-09-27): tastes change, and ``remember`` — which has no
    ``temporal_nature`` argument — stored every preference the owner stated
    user_stated + timeless, the one pair decay holds forever. Timeless is
    for a taste held for years, when the extractor says so.
    """
    if memory_type == "intention":
        return "evolving"
    if origin == "thought":
        return "temporary"
    if memory_type == "preference":
        return "evolving"
    if memory_type in ("fact", "instruction"):
        return "timeless"
    return "temporary"


# Profiles whose "user" message is not the owner speaking now: a wake's
# pack is assembled by Edward's own code (2026-09-27), a scheduled event's
# trigger is a reminder he wrote (memory audit, 2026-09-27). Under them
# nothing is ``heard`` — what the owner or anyone wrote in a message is
# something Edward ``read``, and what concerns Edward himself is his own
# ``thought``.
NOT_OWNER_SPEAKING_PROFILES = ("mind", "scheduled_event")


def origin_when_not_owner_speaking(about: str, profile: str = "mind", labelled: bool = True) -> str:
    """The origin a row takes under a profile where the owner is not speaking,
    when the extractor said ``heard`` (``labelled``) or said nothing. An
    unlabelled row from a scheduled event is the trigger's own words — his
    thought. Pure."""
    if about == "self" or (profile == "scheduled_event" and not labelled):
        return "thought"
    return "read"


# Turns in which the "user" message is the owner themselves. Only these can
# give the owner a standing instruction (memory audit, 2026-09-27): 28 of
# the 35 live heard/owner instructions came from other turns — reminder
# triggers, heartbeat threads, worker tasks, wake packs.
# ``ping_owner`` is the owner writing to Edward in their own self-chat
# (pings, 2026-10-11): only their and Edward's lines are there.
# ``owner_ping`` (2026-10-11) is the owner's own ``@Edward`` text from any
# chat, read alone after a ping turn (``remember_owners_words``).
OWNER_SPEAKS_SOURCES = ("chat", "voice", "widget", "alexa", "ping_owner", "owner_ping")
# Turns in which someone other than the owner or Edward wrote the "user"
# message. ``ping`` is every other ping turn — a friend's @Edward, and the
# owner's own @Edward in a group, whose trigger carries other people's
# lines too.
SOMEONE_ELSE_SPEAKS_SOURCES = ("heartbeat", "twilio", "ping")
# Turns in which anyone in the owner's home may be speaking, the owner or
# not: the household hub's live voice (2026-10-11). What is said there is
# heard, but never the owner's own statement — not ``user_stated``, and an
# instruction is
# ``read`` (someone said it; it was not Edward's own words).
HOUSEHOLD_SPEAKS_SOURCES = ("hub_voice",)


def is_owners_own_turn(turn_source: Optional[str], profile: str = "chat") -> bool:
    """Whether the turn's "user" message is the owner speaking. With no
    turn known (the pre-ledger LangGraph node, direct calls) only the chat
    profile counts as theirs. Pure."""
    if turn_source is None:
        return profile == "chat"
    return turn_source in OWNER_SPEAKS_SOURCES


async def remember_owners_words(text: str, *, conversation_id: str, ping_id: Optional[int] = None,
                              chat_identifier: Optional[str] = None) -> int:
    """The owner's own words from a ping (their ``@Edward …`` in any chat,
    or their self-chat), extracted as the owner speaking (2026-10-11):
    "stop pushing me about soccer" becomes a heard/owner instruction every
    wake and the speaker obey. Only their text is read — never the thread,
    never a friend's line — under the ``chat`` profile with
    ``turn_source="owner_ping"``. The ping runner calls it after a ping turn
    whose ping is the owner's (docs/architecture/pings.md). Returns how many
    rows were stored; never raises (it must never fail the ping)."""
    text = str(text or "").strip()
    if not text:
        return 0
    ref = json.dumps({"kind": "extraction", "profile": "chat", "turn_source": "owner_ping",
                      "conversation_id": conversation_id, "ping_id": ping_id,
                      "chat_identifier": chat_identifier})
    try:
        stored = await extract_and_store_memories([{"role": "user", "content": text}], conversation_id,
                                                  extraction_profile="chat", source_ref=ref)
    except Exception as exc:  # noqa: BLE001
        print(f"[memory] could not read the owner's words from ping {ping_id}: {type(exc).__name__}: {exc}")
        return 0
    return len(stored or [])


def someone_else_speaks(turn_source: Optional[str], profile: str = "chat") -> bool:
    """Whether the turn's "user" message is a third party's. Pure."""
    if turn_source is None:
        return profile == "heartbeat"
    return turn_source in SOMEONE_ELSE_SPEAKS_SOURCES


def default_about_for_profile(profile: str) -> str:
    """Whom a memory concerns when the extractor did not say.

    A heartbeat thread is someone else talking to the owner, so its default is
    ``contact`` — that keeps the third-party floor in front of an extractor
    that dropped the field. A scheduled event's trigger is Edward's own
    note, so its default is ``self``. Chat and the mind default to ``owner``.
    """
    return {"heartbeat": "contact", "scheduled_event": "self"}.get(profile, "owner")


def coerce_extracted_fields(
    item: dict, now: Optional[datetime] = None, default_about: str = "owner",
    profile: str = "chat", turn_source: Optional[str] = None,
) -> tuple[dict, list[str]]:
    """Validate one extracted memory, reporting every field it had to fix.

    Returns ``(fields, misses)``. A silent coercion makes a broken extractor
    look identical to a working one — these misses are what make its health
    observable.

    Intention hygiene (2026-09-25): an ``origin=did`` row is a record of an
    action, never an intention — it is coerced to ``context`` (reported as
    a ``memory_type`` miss). Under the ``mind`` profile an intention with
    no usable ``valid_until`` gets ``now + MIND_INTENTION_DEFAULT_HOURS``,
    so a wake's plans stop piling up in "Open intentions".

    Not the owner speaking (2026-09-27): under a profile in
    ``NOT_OWNER_SPEAKING_PROFILES`` an extracted ``heard`` — or a missing
    origin, which used to default to ``heard`` — becomes ``read`` (or
    ``thought`` when it is about Edward himself), reported as an
    ``origin`` miss, and a ``user_stated`` source goes with it to
    ``inferred``: a user-stated timeless row is never auto-retired.

    The owner's instructions come from the owner (memory audit, 2026-09-27):
    an ``instruction`` keeps ``heard``/``owner`` only in a turn that is the
    owner's own (``turn_source`` in ``OWNER_SPEAKS_SOURCES``; with no turn known,
    only under the chat profile). Elsewhere it becomes ``read`` when
    someone else said it (a heartbeat thread, a Twilio text, the household
    hub) and ``thought`` otherwise — the "user" message was Edward's own
    words. Nothing said at the household hub (``HOUSEHOLD_SPEAKS_SOURCES``)
    keeps ``user_stated``: whoever is at the panel may not be the owner.
    """
    misses: list[str] = []

    memory_type = item.get("memory_type")
    if memory_type not in VALID_MEMORY_TYPES:
        misses.append("memory_type")
        memory_type = "context"

    raw_nature = item.get("temporal_nature")
    temporal_nature = raw_nature if raw_nature in VALID_TEMPORAL_NATURES else None
    if temporal_nature is None:
        misses.append("temporal_nature")  # resolved below, once the origin is known

    source_type = item.get("source_type")
    if source_type not in VALID_SOURCE_TYPES:
        misses.append("source_type")
        source_type = "inferred"

    try:
        confidence = float(item.get("confidence", 0.5))
    except (TypeError, ValueError):
        misses.append("confidence")
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    try:
        importance = float(item.get("importance", 0.5))
    except (TypeError, ValueError):
        misses.append("importance")
        importance = 0.5
    importance = max(0.0, min(1.0, importance))

    about = item.get("about")
    if about not in VALID_ABOUTS:
        # Back-compat: the extraction-only ``about_owner`` flag that this
        # column replaces. A boolean still maps cleanly.
        legacy = item.get("about_owner")
        if isinstance(legacy, bool):
            about = "owner" if legacy else "contact"
        else:
            misses.append("about")
            about = default_about if default_about in VALID_ABOUTS else "owner"
    about_owner = about == "owner"

    owner_not_speaking = profile in NOT_OWNER_SPEAKING_PROFILES
    origin = item.get("origin")
    if origin not in VALID_ORIGINS:
        misses.append("origin")
        origin = origin_when_not_owner_speaking(about, profile, labelled=False) if owner_not_speaking else "heard"
    elif origin == "heard" and owner_not_speaking:
        misses.append("origin")
        origin = origin_when_not_owner_speaking(about, profile)
    if memory_type == "instruction" and (origin, about) == ("heard", "owner") \
            and not is_owners_own_turn(turn_source, profile):
        if "origin" not in misses:
            misses.append("origin")
        origin = "read" if someone_else_speaks(turn_source, profile) \
            or turn_source in HOUSEHOLD_SPEAKS_SOURCES else "thought"
        owner_not_speaking = True  # the source_type below follows
    if (owner_not_speaking or turn_source in HOUSEHOLD_SPEAKS_SOURCES) and source_type == "user_stated":
        source_type = "inferred"
    if origin == "did" and memory_type == "intention":
        # What he did is a record, not a plan.
        misses.append("memory_type")
        memory_type = "context"
    if temporal_nature is None:
        temporal_nature = default_temporal_nature(memory_type, origin=origin)

    if origin == "thought" and about == "owner":
        confidence = min(confidence, THOUGHT_ABOUT_OWNER_MAX_CONFIDENCE)

    valid_until = None
    if item.get("valid_until") not in (None, ""):
        valid_until = parse_valid_until(item.get("valid_until"), now=now)
        if valid_until is None:
            misses.append("valid_until")
    if valid_until is None and memory_type == "intention" and profile == "mind":
        base = now or datetime.now(timezone.utc)
        if base.tzinfo is not None:
            base = base.astimezone(timezone.utc).replace(tzinfo=None)
        valid_until = base + timedelta(hours=MIND_INTENTION_DEFAULT_HOURS)

    return (
        {
            "memory_type": memory_type,
            "temporal_nature": temporal_nature,
            "source_type": source_type,
            "confidence": confidence,
            "importance": importance,
            "about_owner": about_owner,
            "origin": origin,
            "about": about,
            "valid_until": valid_until,
        },
        misses,
    )


# Background tasks are kept here so the event loop does not garbage-collect
# them mid-flight (asyncio only holds weak references to tasks).
_background_tasks: set = set()


def _spawn_background(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _link_entities_in_background(messages: List[dict], stored: List[Memory], conversation_id: str) -> None:
    try:
        from services.entity_service import extract_entities, upsert_entity, link_entity_to_memory
        entities = await extract_entities(messages)
        for entity_data in entities:
            entity = await upsert_entity(
                name=entity_data["name"],
                entity_type=entity_data["entity_type"],
                aliases=entity_data.get("aliases", []),
            )
            name_lower = entity_data["name"].lower()
            for mem in stored:
                if name_lower in mem.content.lower():
                    await link_entity_to_memory(
                        entity_id=entity.id,
                        memory_id=mem.id,
                        conversation_id=conversation_id,
                        relationship=entity_data.get("relationship", "mentioned"),
                        snippet=mem.content[:200],
                    )
        if entities:
            print(f"Memory extraction: linked {len(entities)} entities")
    except Exception as e:
        print(f"Memory extraction: entity linking failed (non-fatal): {e}")


async def extract_and_store_memories(
    messages: List[dict],
    conversation_id: str,
    model: Optional[str] = None,
    existing_memories: List[Memory] = None,
    extraction_profile: str = "chat",
    source_ref: Optional[str] = None,
) -> List[Memory]:
    """
    Extract memorable information from conversation and store it.

    Uses the background model (``build_background_llm()``) to analyze the
    conversation and identify information worth remembering.

    Args:
        messages: List of message dicts with 'role' and 'content'
        conversation_id: ID of the source conversation
        model: Model to use for extraction (default: background LLM for speed/cost)
        existing_memories: Previously retrieved memories to avoid duplicates
        extraction_profile: "chat" (default) or "heartbeat" — contact threads
            keep durable facts only (HEARTBEAT_EXTRACTION_ADDENDUM + floor)
        source_ref: Provenance JSON for every stored row. None builds it from
            the current turn and ``provenance_service.source_context``.

    Returns:
        List of newly stored Memory objects
    """
    if not messages:
        print("Memory extraction: no messages, skipping")
        return []

    print(f"Memory extraction: processing {len(messages)} messages")

    # Use is what the answer drew on (memory audit, 2026-09-27): credit it now
    # that the answer exists, whatever the extractor makes of the turn.
    try:
        cited = await credit_cited_memories(messages, existing_memories)
        if cited:
            print(f"Memory extraction: {len(cited)} memories cited by the answer")
    except Exception as cite_exc:  # noqa: BLE001 — never costs the extraction
        print(f"Memory citation credit failed (non-fatal): {cite_exc}")

    # Format conversation for analysis
    conversation_text = "\n".join([
        f"{msg.get('role', 'unknown').upper()}: {msg.get('content', '')}"
        for msg in messages[-10:]  # Last 10 messages for context
    ])

    print(f"Memory extraction: formatted conversation ({len(conversation_text)} chars)")

    # Format existing memories
    existing_text = "None"
    if existing_memories:
        existing_text = "\n".join([
            f"- [{m.memory_type}] {m.content}"
            for m in existing_memories
        ])

    print(f"Memory extraction: existing memories text: {existing_text[:100]}")

    # Call LLM to extract memories
    llm = build_background_llm(max_tokens=2048)

    if extraction_profile not in VALID_EXTRACTION_PROFILES:
        print(f"Memory extraction: unknown profile {extraction_profile!r}, using chat")
        extraction_profile = "chat"
    extraction_now = datetime.now(timezone.utc)
    # Provenance (2026-09-24): every row this extraction stores records the
    # turn it came from, the profile, and whatever the caller put in
    # ``provenance_service.source_context`` (heartbeat event ids, rowids).
    if source_ref is None:
        from services.provenance_service import turn_source_ref
        source_ref = turn_source_ref("extraction", conversation_id=conversation_id, profile=extraction_profile)
    # Who spoke in this turn decides whether an instruction can be the
    # owner's (memory audit, 2026-09-27): the ref records the turn, the
    # ledger knows it.
    from services.provenance_service import parse_source_ref
    turn_source = parse_source_ref(source_ref).get("turn_source") or current_turn_source()
    prompt = build_extraction_prompt(
        conversation_text, existing_text, profile=extraction_profile, now=extraction_now
    )

    print(f"Memory extraction: prompt ready ({len(prompt)} chars)")

    try:
        print(f"Memory extraction: calling LLM with {len(messages)} messages")
        response = await llm.ainvoke([
            make_background_system_message(
                "Extract important information from conversations that should be remembered for future interactions. Return valid JSON.",
            ),
            HumanMessage(content=prompt),
        ])
        print(f"Memory extraction: got LLM response")

        # Handle both string and list content (Claude sometimes returns list of content blocks)
        if isinstance(response.content, list):
            response_text = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in response.content
            )
        else:
            response_text = response.content

        response_text = response_text.strip()
        print(f"Memory extraction raw response: {response_text[:500]}")

        # Robust JSON extraction — handles GPT-5.4's various output shapes
        # (dict-wrapped, nested arrays, preamble text, markdown fences).
        # Silent-fail was the original bug; the helper logs WARN on failure.
        extracted = extract_json_payload(
            response_text, expected="array", subsystem="memory_extraction"
        )

        if not isinstance(extracted, list):
            print(f"Memory extraction: expected list, got {type(extracted)}")
            return []

        # Convert to Memory objects and store
        memories_to_store = []
        coercion_misses: list[str] = []
        dropped_by_profile = 0
        for item in extracted:
            if not isinstance(item, dict):
                continue
            if not item.get("content") or not item.get("memory_type"):
                continue

            fields, misses = coerce_extracted_fields(
                item, now=extraction_now, default_about=default_about_for_profile(extraction_profile),
                profile=extraction_profile, turn_source=turn_source,
            )
            memory_type = fields["memory_type"]
            temporal_nature = fields["temporal_nature"]
            source_type = fields["source_type"]
            confidence = fields["confidence"]
            if misses:
                coercion_misses.extend(misses)
                print(
                    f"[Memory extraction] coerced {', '.join(misses)} for: "
                    f"{item['content'][:80]}"
                )

            # Store-side guard (Fix #3): drop low-confidence inferred memories
            # at ingestion so accumulated garbage doesn't re-pollute retrieval
            # even if the confidence filter were ever bypassed. User-stated
            # and tool-derived memories are trusted regardless of confidence.
            if source_type == "inferred" and confidence < 0.3:
                print(
                    f"[Memory guard] dropped low-confidence inferred memory "
                    f"(confidence={confidence:.2f}): {item['content'][:80]}"
                )
                continue

            if not should_store_under_profile(fields, extraction_profile):
                dropped_by_profile += 1
                print(
                    f"[Memory guard] dropped third-party {memory_type} below the "
                    f"{extraction_profile} floor (importance={fields['importance']:.2f}): "
                    f"{item['content'][:80]}"
                )
                continue

            memory = Memory(
                id=None,
                content=item["content"],
                memory_type=memory_type,
                importance=fields["importance"],
                temporal_nature=temporal_nature,
                source_type=source_type,
                confidence=confidence,
                source_conversation_id=conversation_id,
                valid_until=fields["valid_until"],
                origin=fields["origin"],
                about=fields["about"],
                source_ref=source_ref,
            )
            memories_to_store.append(memory)

        print(
            f"Memory extraction: profile={extraction_profile} found {len(memories_to_store)} "
            f"memories to store, dropped {dropped_by_profile} (floor)"
        )

        if coercion_misses:
            # An extractor that quietly stopped emitting a field used to look
            # exactly like one working perfectly. This line is the difference.
            tally = {f: coercion_misses.count(f) for f in set(coercion_misses)}
            print(
                f"[Memory extraction] WARN field coercions over "
                f"{len(extracted)} extracted items: {tally}"
            )

        if memories_to_store:
            stored = await store_memories_with_conflict_detection(memories_to_store)
            print(f"Memory extraction: stored {len(stored)} memories")

            # Entity extraction and linking — off the critical path. It used
            # to run inline with an 8 s budget and timed out 27 times in 29
            # (2026-09-05); the turn no longer waits on it.
            if stored:
                _spawn_background(_link_entities_in_background(messages, list(stored), conversation_id))

            return stored

        return []

    except json.JSONDecodeError as e:
        print(f"Memory extraction JSON parse error: {e}")
        print(f"Attempted to parse: {response_text[:200] if 'response_text' in locals() else 'N/A'}")
        return []
    except Exception as e:
        import traceback
        print(f"Memory extraction failed: {type(e).__name__}: {e}")
        print(f"Traceback: {traceback.format_exc()}")
        return []


async def get_memory_stats() -> dict:
    """Get statistics about stored memories."""
    async with async_session() as session:
        # Total count
        total_result = await session.execute(
            select(func.count(MemoryModel.id))
        )
        total = total_result.scalar() or 0

        # Count by type
        type_result = await session.execute(
            select(
                MemoryModel.memory_type,
                func.count(MemoryModel.id)
            ).group_by(MemoryModel.memory_type)
        )
        by_type = {row[0]: row[1] for row in type_result.fetchall()}

        # Count by tier
        tier_result = await session.execute(
            select(
                MemoryModel.tier,
                func.count(MemoryModel.id)
            ).group_by(MemoryModel.tier)
        )
        by_tier = {(row[0] or "observation"): row[1] for row in tier_result.fetchall()}

        # Average importance
        avg_result = await session.execute(
            select(func.avg(MemoryModel.importance))
        )
        avg_importance = float(avg_result.scalar() or 0)

        now = datetime.utcnow()
        live_clause = or_(MemoryModel.valid_until.is_(None), MemoryModel.valid_until > now)
        live = (await session.execute(
            select(func.count(MemoryModel.id)).where(live_clause)
        )).scalar() or 0
        unused_live = (await session.execute(
            select(func.count(MemoryModel.id)).where(and_(live_clause, MemoryModel.used_count == 0))
        )).scalar() or 0
        created_last_24h = (await session.execute(
            select(func.count(MemoryModel.id)).where(MemoryModel.created_at >= now - timedelta(hours=24))
        )).scalar() or 0

        return {
            "total": total,
            "live": live,
            "unused_live": unused_live,
            "created_last_24h": created_last_24h,
            "by_type": by_type,
            "by_tier": by_tier,
            "average_importance": round(avg_importance, 2)
        }


async def get_all_memories(limit: int = 100, offset: int = 0) -> List[Memory]:
    """Get all memories with pagination."""
    async with async_session() as session:
        result = await session.execute(
            select(MemoryModel)
            .order_by(MemoryModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )

        rows = result.scalars().all()
        return [
            Memory(
                id=row.id,
                content=row.content,
                memory_type=row.memory_type,
                importance=row.importance or 0.5,
                source_conversation_id=row.source_conversation_id,
                created_at=row.created_at,
                updated_at=row.updated_at,
                last_accessed=row.last_accessed,
                access_count=row.access_count or 0,
                temporal_nature=row.temporal_nature or "timeless",
                tier=getattr(row, 'tier', 'observation') or 'observation',
                reinforcement_count=getattr(row, 'reinforcement_count', 0) or 0,
                source_type=getattr(row, 'source_type', 'inferred') or 'inferred',
                confidence=getattr(row, 'confidence', 0.5) or 0.5,
                origin=getattr(row, 'origin', 'heard') or 'heard',
                about=getattr(row, 'about', 'owner') or 'owner',
                user_id=row.user_id,
                valid_until=getattr(row, 'valid_until', None),
                source_ref=getattr(row, 'source_ref', None),
            )
            for row in rows
        ]


async def get_memory_by_id(memory_id: str) -> Optional[Memory]:
    """Get a single memory by ID."""
    async with async_session() as session:
        result = await session.execute(
            select(MemoryModel).where(MemoryModel.id == memory_id)
        )
        row = result.scalar_one_or_none()
        if not row:
            return None
        return Memory(
            id=row.id,
            content=row.content,
            memory_type=row.memory_type,
            importance=row.importance or 0.5,
            source_conversation_id=row.source_conversation_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
            last_accessed=row.last_accessed,
            access_count=row.access_count or 0,
            temporal_nature=row.temporal_nature or "timeless",
            tier=getattr(row, 'tier', 'observation') or 'observation',
            reinforcement_count=getattr(row, 'reinforcement_count', 0) or 0,
            source_type=getattr(row, 'source_type', 'inferred') or 'inferred',
            confidence=getattr(row, 'confidence', 0.5) or 0.5,
            origin=getattr(row, 'origin', 'heard') or 'heard',
            about=getattr(row, 'about', 'owner') or 'owner',
            user_id=row.user_id,
            valid_until=getattr(row, 'valid_until', None),
            source_ref=getattr(row, 'source_ref', None),
        )


async def delete_memory(memory_id: str) -> bool:
    """
    Delete a memory by ID.

    Args:
        memory_id: The ID of the memory to delete

    Returns:
        True if deleted, False if not found
    """
    async with async_session() as session:
        result = await session.execute(
            select(MemoryModel).where(MemoryModel.id == memory_id)
        )
        memory = result.scalar_one_or_none()
        if not memory:
            return False

        await session.delete(memory)
        await session.commit()
        return True


async def update_memory(
    memory_id: str,
    content: Optional[str] = None,
    memory_type: Optional[str] = None,
    importance: Optional[float] = None,
    temporal_nature: Optional[str] = None,
    tier: Optional[str] = None,
    reinforcement_count: Optional[int] = None,
    source_type: Optional[str] = None,
    confidence: Optional[float] = None,
    origin: Optional[str] = None,
    about: Optional[str] = None,
    valid_until: Optional[datetime] = None,
    source_ref: Optional[str] = None,
    reembed: bool = True,
    clear_valid_until: bool = False,
) -> Optional[Memory]:
    """
    Update an existing memory. Re-generates embedding if content changes
    (``reembed=False`` keeps the old one — ``close_intention``).

    ``valid_until`` retires the memory at that (naive UTC) time; retrieval
    excludes it from then on. Until Phase 1 of the mind rebuild only decay
    and contradiction resolution could do this, by raw SQL.
    ``clear_valid_until`` makes it open-ended again (conflict detection's
    UPDATE, when the new words name no horizon).

    Args:
        memory_id: ID of the memory to update
        content: New content (optional)
        memory_type: New type (optional)
        importance: New importance score (optional)
        temporal_nature: New temporal nature (optional)
        source_type: New source type (optional)
        confidence: New confidence score (optional)

    Returns:
        Updated Memory object or None if not found
    """
    async with async_session() as session:
        result = await session.execute(
            select(MemoryModel).where(MemoryModel.id == memory_id)
        )
        memory = result.scalar_one_or_none()
        if not memory:
            return None

        # Update fields if provided
        if content is not None and content != memory.content:
            memory.content = content
            # Re-generate embedding for new content
            if reembed:
                memory.embedding = get_embedding(content)

        if memory_type is not None:
            if memory_type in VALID_MEMORY_TYPES:
                memory.memory_type = memory_type

        if importance is not None:
            memory.importance = max(0.0, min(1.0, importance))

        if temporal_nature is not None:
            valid_natures = ["timeless", "temporary", "evolving"]
            if temporal_nature in valid_natures:
                memory.temporal_nature = temporal_nature

        if tier is not None:
            valid_tiers = ["observation", "belief", "knowledge"]
            if tier == "knowledge" and memory.tier != "knowledge" and names_past_date(memory.content):
                # A dated episode stops at belief whoever asks (memory audit
                # review, 2026-09-27): consolidation's step 10 — the one
                # path where use promotes to knowledge — calls _compute_tier
                # without the content, and a dated July episode went to
                # knowledge on the next cycle. A row already there is left
                # as it is, not demoted here.
                tier = "belief"
            if tier in valid_tiers:
                memory.tier = tier

        if reinforcement_count is not None:
            memory.reinforcement_count = reinforcement_count

        if source_type is not None:
            valid_source_types = ["user_stated", "inferred", "tool_derived", "imported"]
            if source_type in valid_source_types:
                memory.source_type = source_type

        if confidence is not None:
            memory.confidence = max(0.0, min(1.0, confidence))

        if origin is not None and origin in VALID_ORIGINS:
            memory.origin = origin

        if about is not None and about in VALID_ABOUTS:
            memory.about = about

        if valid_until is not None:
            if valid_until.tzinfo is not None:
                valid_until = valid_until.astimezone(timezone.utc).replace(tzinfo=None)
            memory.valid_until = valid_until
        elif clear_valid_until:
            memory.valid_until = None

        if source_ref is not None:
            memory.source_ref = source_ref

        await session.commit()
        await session.refresh(memory)

        return Memory(
            id=memory.id,
            content=memory.content,
            memory_type=memory.memory_type,
            importance=memory.importance or 0.5,
            source_conversation_id=memory.source_conversation_id,
            created_at=memory.created_at,
            updated_at=memory.updated_at,
            last_accessed=memory.last_accessed,
            access_count=memory.access_count or 0,
            temporal_nature=memory.temporal_nature or "timeless",
            tier=getattr(memory, 'tier', 'observation') or 'observation',
            reinforcement_count=getattr(memory, 'reinforcement_count', 0) or 0,
            source_type=getattr(memory, 'source_type', 'inferred') or 'inferred',
            confidence=getattr(memory, 'confidence', 0.5) or 0.5,
            origin=getattr(memory, 'origin', 'heard') or 'heard',
            about=getattr(memory, 'about', 'owner') or 'owner',
            user_id=memory.user_id,
            valid_until=getattr(memory, 'valid_until', None),
            source_ref=getattr(memory, 'source_ref', None),
        )


# ===== CLOSING AN INTENTION =====

def closed_content(content: str, outcome: str, when: datetime) -> str:
    """An intention's text with what came of it appended. Pure."""
    return f"{(content or '').rstrip()} — Closed {when:%Y-%m-%d}: {' '.join((outcome or '').split())}"


async def close_intention(memory_id: str, outcome: str,
                          now: Optional[datetime] = None) -> tuple[Optional[Memory], Optional[str]]:
    """Close a live intention (M4, 2026-09-27): ``valid_until=now``, what
    came of it appended to the content and recorded under ``closed`` in
    its provenance. The row stays — ``remember_forget`` deleted the history
    with it, and ``remember_update`` could only change the words ("marking
    it closed in the text didn't retire it", the 07:42 free wake).

    Returns ``(memory, None)`` or ``(None, why it was refused)``.
    """
    from services.provenance_service import dump_source_ref, parse_source_ref

    outcome = " ".join((outcome or "").split())
    if not outcome:
        return None, "say what came of it (outcome) — that is what the closed row keeps."
    existing = await get_memory_by_id(memory_id)
    if existing is None:
        return None, f"no memory with id {memory_id}."
    if existing.memory_type != "intention":
        return None, (f"memory {memory_id} is a {existing.memory_type}, not an intention — "
                      "remember_update or remember_forget changes it.")
    at = now or datetime.now(timezone.utc)
    if at.tzinfo is not None:
        at = at.astimezone(timezone.utc).replace(tzinfo=None)
    until = existing.valid_until
    if until is not None:
        until = until.astimezone(timezone.utc).replace(tzinfo=None) if until.tzinfo else until
        if until <= at:
            return None, f"intention {memory_id} is already closed (since {until:%Y-%m-%d %H:%M} UTC)."
    ref = parse_source_ref(existing.source_ref)
    ref["closed"] = {"at": at.strftime("%Y-%m-%dT%H:%M:%SZ"), "outcome": outcome[:200],
                     "turn_source": current_turn_source()}
    # The embedding stays what the intention said: a wake re-lifting it
    # from a pack gathered before the close must still find it (the closed
    # text scored 0.70–0.82 against the original with the real model —
    # below the echo line — and the re-lift reopened it; review 2026-09-27).
    updated = await update_memory(
        memory_id,
        content=closed_content(existing.content, outcome, at),
        valid_until=at,
        source_ref=dump_source_ref(ref),
        reembed=False,
    )
    if updated is None:
        return None, f"no memory with id {memory_id}."
    return updated, None


async def search_memories(
    query: Optional[str] = None,
    memory_type: Optional[str] = None,
    min_importance: Optional[float] = None,
    temporal_nature: Optional[str] = None,
    tier: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    vector_weight: float = 0.7,
    keyword_weight: float = 0.3,
    live_only: bool = False,
) -> tuple[List[Memory], int]:
    """
    Search and filter memories with pagination using hybrid search.

    ``live_only`` leaves out retired rows (``valid_until`` passed): recall
    (``remember_search``, speculative recall) sets it; the memories page and
    verified forget keep seeing everything.

    Combines:
    - Vector similarity search (cosine distance)
    - BM25 keyword search (full-text)

    Args:
        query: Text search query (optional)
        memory_type: Filter by type (optional)
        min_importance: Minimum importance threshold (optional)
        temporal_nature: Filter by temporal nature (optional)
        limit: Maximum results to return
        offset: Number of results to skip
        vector_weight: Weight for vector similarity (default 0.7)
        keyword_weight: Weight for keyword matching (default 0.3)

    Returns:
        Tuple of (list of memories, total count matching filters)
    """
    async with async_session() as session:
        # Build base query for non-search case
        base_query = select(MemoryModel)
        count_query = select(func.count(MemoryModel.id))

        # Apply filters
        if memory_type:
            base_query = base_query.where(MemoryModel.memory_type == memory_type)
            count_query = count_query.where(MemoryModel.memory_type == memory_type)

        if min_importance is not None:
            base_query = base_query.where(MemoryModel.importance >= min_importance)
            count_query = count_query.where(MemoryModel.importance >= min_importance)

        if temporal_nature:
            base_query = base_query.where(MemoryModel.temporal_nature == temporal_nature)
            count_query = count_query.where(MemoryModel.temporal_nature == temporal_nature)

        if tier:
            base_query = base_query.where(MemoryModel.tier == tier)
            count_query = count_query.where(MemoryModel.tier == tier)

        if live_only:
            live = or_(MemoryModel.valid_until.is_(None), MemoryModel.valid_until > datetime.utcnow())
            base_query = base_query.where(live)
            count_query = count_query.where(live)

        # If query provided, use hybrid search (vector + keyword)
        if query:
            query_embedding = get_embedding(query)
            embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

            # Build filter conditions for SQL
            filter_conditions = "embedding IS NOT NULL"
            params = {"embedding": embedding_str, "query": query}

            if memory_type:
                filter_conditions += " AND memory_type = :memory_type"
                params["memory_type"] = memory_type

            if min_importance is not None:
                filter_conditions += " AND importance >= :min_importance"
                params["min_importance"] = min_importance

            if temporal_nature:
                filter_conditions += " AND temporal_nature = :temporal_nature"
                params["temporal_nature"] = temporal_nature

            if tier:
                filter_conditions += " AND tier = :tier"
                params["tier"] = tier

            if live_only:
                filter_conditions += " AND (valid_until IS NULL OR valid_until > (NOW() AT TIME ZONE 'UTC'))"

            # Vector search - get more results than needed for combining
            fetch_limit = (limit + offset) * 2
            params["fetch_limit"] = fetch_limit

            vector_sql = text(f"""
                SELECT
                    id, content, memory_type, importance,
                    source_conversation_id, created_at, updated_at, last_accessed, access_count,
                    temporal_nature, tier, reinforcement_count, user_id,
                    source_type, confidence, origin, about,
                    1 - (embedding <=> CAST(:embedding AS vector)) as vector_score
                FROM memories
                WHERE {filter_conditions}
                ORDER BY embedding <=> CAST(:embedding AS vector)
                LIMIT :fetch_limit
            """)

            vector_result = await session.execute(vector_sql, params)
            vector_rows = {row.id: row for row in vector_result.fetchall()}

            # Keyword search (BM25)
            keyword_sql = text(f"""
                SELECT
                    id,
                    ts_rank_cd(to_tsvector('english', content), plainto_tsquery('english', :query)) as keyword_score
                FROM memories
                WHERE {filter_conditions}
                  AND to_tsvector('english', content) @@ plainto_tsquery('english', :query)
                ORDER BY keyword_score DESC
                LIMIT :fetch_limit
            """)

            keyword_result = await session.execute(keyword_sql, params)
            keyword_scores = {row.id: row.keyword_score for row in keyword_result.fetchall()}

            # Combine scores from both searches
            all_ids = set(vector_rows.keys()) | set(keyword_scores.keys())
            scored_memories = []

            for memory_id in all_ids:
                if memory_id in vector_rows:
                    row = vector_rows[memory_id]
                    vector_score = row.vector_score or 0
                    keyword_score = keyword_scores.get(memory_id, 0)

                    # Combined score with importance multiplier
                    combined_score = (
                        vector_weight * vector_score +
                        keyword_weight * keyword_score
                    ) * (1 + (row.importance or 0.5) * 0.2)

                    scored_memories.append(Memory(
                        id=row.id,
                        content=row.content,
                        memory_type=row.memory_type,
                        importance=row.importance or 0.5,
                        source_conversation_id=row.source_conversation_id,
                        created_at=row.created_at,
                        updated_at=row.updated_at,
                        last_accessed=row.last_accessed,
                        access_count=row.access_count or 0,
                        temporal_nature=getattr(row, 'temporal_nature', 'timeless') or 'timeless',
                        tier=getattr(row, 'tier', 'observation') or 'observation',
                        reinforcement_count=getattr(row, 'reinforcement_count', 0) or 0,
                        source_type=getattr(row, 'source_type', 'inferred') or 'inferred',
                        confidence=getattr(row, 'confidence', 0.5) or 0.5,
                        origin=getattr(row, 'origin', 'heard') or 'heard',
                        about=getattr(row, 'about', 'owner') or 'owner',
                        user_id=row.user_id,
                        score=combined_score
                    ))

            # Sort by combined score and apply pagination
            scored_memories.sort(key=lambda m: m.score, reverse=True)
            total = len(scored_memories)
            paginated = scored_memories[offset:offset + limit]

            return paginated, total

        # No query - just filter and paginate
        base_query = base_query.order_by(MemoryModel.created_at.desc()).limit(limit).offset(offset)

        result = await session.execute(base_query)
        rows = result.scalars().all()

        count_result = await session.execute(count_query)
        total = count_result.scalar() or 0

        return [
            Memory(
                id=row.id,
                content=row.content,
                memory_type=row.memory_type,
                importance=row.importance or 0.5,
                source_conversation_id=row.source_conversation_id,
                created_at=row.created_at,
                updated_at=row.updated_at,
                last_accessed=row.last_accessed,
                access_count=row.access_count or 0,
                temporal_nature=row.temporal_nature or "timeless",
                tier=getattr(row, 'tier', 'observation') or 'observation',
                reinforcement_count=getattr(row, 'reinforcement_count', 0) or 0,
                source_type=getattr(row, 'source_type', 'inferred') or 'inferred',
                confidence=getattr(row, 'confidence', 0.5) or 0.5,
                origin=getattr(row, 'origin', 'heard') or 'heard',
                about=getattr(row, 'about', 'owner') or 'owner',
                user_id=row.user_id,
                valid_until=getattr(row, 'valid_until', None),
            )
            for row in rows
        ], total


# ===== SEMANTIC CONFLICT DETECTION =====

async def find_similar_memories(content: str, threshold: float = 0.85, limit: int = 5,
                                live_only: bool = False) -> List[tuple[Memory, float]]:
    """
    Find memories semantically similar to the given content.

    Args:
        content: Content to find similar memories for
        threshold: Minimum similarity score (0-1, default 0.85)
        limit: Maximum number of similar memories to return
        live_only: Skip retired rows (``valid_until`` passed). Conflict
            detection passes it for the owner's turns and deliberate writes
            (2026-09-27): matching a retired row reinforced it, so a
            decision taken again after ``close_intention`` — or a fact the owner
            restated after decay retired it — stayed retired. A passive
            re-lift (``is_passive_relift``) and verified forget leave it
            off: they must reach retired rows too. Each Memory carries its
            ``valid_until``.

    Returns:
        List of (Memory, similarity_score) tuples
    """
    query_embedding = get_embedding(content)
    embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
    live_clause = (" AND (valid_until IS NULL OR valid_until > (NOW() AT TIME ZONE 'UTC'))"
                   if live_only else "")

    async with async_session() as session:
        sql = text(f"""
            SELECT
                id, content, memory_type, importance,
                source_conversation_id, created_at, updated_at, last_accessed, access_count,
                temporal_nature, tier, reinforcement_count, user_id,
                source_type, confidence, origin, about, source_ref, valid_until,
                1 - (embedding <=> CAST(:embedding AS vector)) as similarity
            FROM memories
            WHERE embedding IS NOT NULL{live_clause}
              AND 1 - (embedding <=> CAST(:embedding AS vector)) >= :threshold
            ORDER BY similarity DESC
            LIMIT :limit
        """)

        result = await session.execute(sql, {
            "embedding": embedding_str,
            "threshold": threshold,
            "limit": limit
        })

        rows = result.fetchall()
        return [
            (
                Memory(
                    id=row.id,
                    content=row.content,
                    memory_type=row.memory_type,
                    importance=row.importance or 0.5,
                    source_conversation_id=row.source_conversation_id,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                    last_accessed=row.last_accessed,
                    access_count=row.access_count or 0,
                    temporal_nature=getattr(row, 'temporal_nature', 'timeless') or 'timeless',
                    tier=getattr(row, 'tier', 'observation') or 'observation',
                    reinforcement_count=getattr(row, 'reinforcement_count', 0) or 0,
                    source_type=getattr(row, 'source_type', 'inferred') or 'inferred',
                    confidence=getattr(row, 'confidence', 0.5) or 0.5,
                    origin=getattr(row, 'origin', 'heard') or 'heard',
                    about=getattr(row, 'about', 'owner') or 'owner',
                    user_id=row.user_id,
                    score=row.similarity,
                    source_ref=getattr(row, 'source_ref', None),
                    valid_until=getattr(row, 'valid_until', None),
                ),
                row.similarity
            )
            for row in rows
        ]


CONFLICT_RESOLUTION_PROMPT = """You are analyzing two memories to determine how to handle a potential conflict or update.

EXISTING MEMORY:
- Content: {existing_content}
- Type: {existing_type}
- Importance: {existing_importance}
- Source: {existing_source_type} (confidence: {existing_confidence:.2f})

NEW MEMORY:
- Content: {new_content}
- Type: {new_type}
- Importance: {new_importance}
- Source: {new_source_type} (confidence: {new_confidence:.2f})

Similarity score: {similarity:.2f}

Source type hierarchy (higher wins): user_stated > tool_derived > inferred > imported
If a user_stated memory conflicts with an inferred memory, prefer the user_stated one.
Consider confidence values — higher confidence memories are more trustworthy.

Determine the best action:
- UPDATE: The new memory is an update/correction to the existing one (replace old with new)
- MERGE: Both contain complementary information (combine into one comprehensive memory)
- KEEP_BOTH: They are related but distinct facts that should both be kept
- DISCARD: The new memory is redundant or less accurate (keep existing, discard new)

If MERGE, provide the merged content.

Return ONLY a valid JSON object with this structure:
{{"action": "UPDATE|MERGE|KEEP_BOTH|DISCARD", "merged_content": "...", "reason": "brief explanation"}}

The merged_content field is only required when action is MERGE."""


@dataclass
class ConflictResolution:
    """Result of conflict resolution between memories."""
    action: str  # UPDATE, MERGE, KEEP_BOTH, DISCARD
    merged_content: Optional[str] = None
    reason: str = ""


async def resolve_memory_conflict(
    existing: Memory,
    new: Memory,
    similarity: float,
    model: Optional[str] = None
) -> ConflictResolution:
    """
    Use LLM to decide how to handle conflicting/similar memories.

    Args:
        existing: The existing memory in database
        new: The new memory to be stored
        similarity: Similarity score between them
        model: Model to use for resolution

    Returns:
        ConflictResolution with action and optional merged content
    """
    llm = build_background_llm(max_tokens=512)

    prompt = CONFLICT_RESOLUTION_PROMPT.format(
        existing_content=existing.content,
        existing_type=existing.memory_type,
        existing_importance=existing.importance,
        existing_source_type=existing.source_type,
        existing_confidence=existing.confidence,
        new_content=new.content,
        new_type=new.memory_type,
        new_importance=new.importance,
        new_source_type=new.source_type,
        new_confidence=new.confidence,
        similarity=similarity
    )

    try:
        response = await llm.ainvoke([
            make_background_system_message(
                "Analyze two memories to determine how to handle a potential conflict or update. Return valid JSON.",
            ),
            HumanMessage(content=prompt),
        ])

        response_text = response.content
        if isinstance(response_text, list):
            response_text = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in response_text
            )
        response_text = response_text.strip()

        result = extract_json_payload(
            response_text, expected="object", subsystem="conflict_resolution"
        )

        action = result.get("action", "KEEP_BOTH").upper()
        if action not in ["UPDATE", "MERGE", "KEEP_BOTH", "DISCARD"]:
            action = "KEEP_BOTH"

        return ConflictResolution(
            action=action,
            merged_content=result.get("merged_content"),
            reason=result.get("reason", "")
        )

    except Exception as e:
        print(f"Conflict resolution failed: {e}")
        # Default to keeping both on error
        return ConflictResolution(action="KEEP_BOTH", reason=f"Resolution error: {e}")


# ===== TEMPORAL NATURE BACKFILL =====

BACKFILL_PROMPT = owner_text("""Classify each memory's temporal nature. The user is {OWNER}.

- "timeless": Permanent facts unlikely to change (identity, allergies, family relationships) and a taste held for years ("{OWNER} dislikes bananas")
- "temporary": Time-bound context that expires (current projects, upcoming events, temporary situations), and any preference tied to one occasion, order, meal, trip, weekend, purchase or event, or to "right now", "lately" or "this week"
- "evolving": Things that change over time and may be superseded (favorite restaurant, current job, tools in use, a taste that can change)

Return ONLY a valid JSON array of {{"id": "...", "temporal_nature": "..."}} for each memory.

Memories:
{memories_json}""")


async def backfill_temporal_nature(
    model: Optional[str] = None,
    batch_size: int = 20,
) -> dict:
    """
    Classify all existing memories that still have the default 'timeless' temporal_nature.

    Sends memories to the background LLM in batches for classification and updates the DB.

    Returns:
        Summary dict with counts of each classification.
    """
    llm = build_background_llm(max_tokens=2048)
    summary = {"total": 0, "timeless": 0, "temporary": 0, "evolving": 0, "errors": 0}

    async with async_session() as session:
        # Fetch all memories with default temporal_nature
        result = await session.execute(
            select(MemoryModel)
            .where(MemoryModel.temporal_nature == "timeless")
            .order_by(MemoryModel.created_at.asc())
        )
        all_memories = result.scalars().all()
        summary["total"] = len(all_memories)

        if not all_memories:
            return summary

        # Process in batches
        for i in range(0, len(all_memories), batch_size):
            batch = all_memories[i:i + batch_size]
            memories_for_prompt = [
                {"id": m.id, "content": m.content, "memory_type": m.memory_type}
                for m in batch
            ]

            prompt = BACKFILL_PROMPT.format(
                memories_json=json.dumps(memories_for_prompt, indent=2)
            )

            try:
                response = await llm.ainvoke([
                    make_background_system_message(
                        "Classify memories by temporal nature: timeless, temporary, or evolving. Return valid JSON.",
                    ),
                    HumanMessage(content=prompt),
                ])
                response_text = response.content
                if isinstance(response_text, list):
                    response_text = " ".join(
                        block.get("text", "") if isinstance(block, dict) else str(block)
                        for block in response_text
                    )
                response_text = response_text.strip()

                classifications = extract_json_payload(
                    response_text, expected="array", subsystem="temporal_backfill"
                )

                valid_natures = {"timeless", "temporary", "evolving"}
                for item in classifications:
                    mem_id = item.get("id")
                    nature = item.get("temporal_nature", "timeless")
                    if nature not in valid_natures:
                        nature = "timeless"

                    await session.execute(
                        update(MemoryModel)
                        .where(MemoryModel.id == mem_id)
                        .values(temporal_nature=nature)
                    )
                    summary[nature] = summary.get(nature, 0) + 1
                    print(f"Backfill: {mem_id[:8]}... -> {nature}")

                await session.commit()

            except Exception as e:
                print(f"Backfill batch error: {e}")
                summary["errors"] += 1

    return summary


# ===== TIER MIGRATION (ONE-TIME DEDUP + REINFORCE) =====

DEDUP_CLASSIFY_PROMPT = owner_text("""Review these groups of similar memories. For each group, decide the action:

- MERGE: These are duplicates or near-duplicates. Provide a single clean merged version.
- KEEP_ALL: These are genuinely distinct facts that should remain separate.

For MERGE groups, also classify the merged memory's temporal_nature:
- "timeless": Permanent facts (identity, relationships) and a taste held for years ("{OWNER} dislikes bananas")
- "temporary": Time-bound context (current projects, upcoming events), and a preference tied to one occasion, order, meal, trip, weekend, purchase or event, or to "right now", "lately" or "this week"
- "evolving": Changes over time (current job, favorite restaurant, a taste that can change)

Return ONLY valid JSON:
{{
  "groups": [
    {{
      "action": "MERGE",
      "keep_id": "id of the best/most complete memory to keep",
      "absorb_ids": ["ids to delete after merging"],
      "merged_content": "single clean merged text",
      "temporal_nature": "timeless|temporary|evolving"
    }},
    {{
      "action": "KEEP_ALL",
      "memory_ids": ["id1", "id2"]
    }}
  ]
}}

Memory groups:
{groups_json}""")


async def backfill_memory_tiers(
    similarity_threshold: float = 0.78,
    batch_size: int = 50,
    model: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """
    One-time migration: deduplicate all memories and set proper tiers.

    Scans the entire memory store for near-duplicates, merges them via the background LLM,
    sums reinforcement counts, and computes tiers.

    Args:
        similarity_threshold: Minimum similarity to consider as potential duplicate
        batch_size: Number of memories to process per iteration
        model: LLM model for merge decisions
        dry_run: If True, report what would happen without making changes

    Returns:
        Summary dict with merge/promotion stats
    """

    summary = {
        "total_memories": 0,
        "duplicates_found": 0,
        "merges_performed": 0,
        "memories_deleted": 0,
        "promotions": 0,
        "haiku_calls": 0,
        "errors": 0,
        "dry_run": dry_run,
    }

    # Step 1: Load all memory IDs and embeddings
    async with async_session() as session:
        result = await session.execute(
            select(MemoryModel)
            .where(MemoryModel.embedding.isnot(None))
            .order_by(MemoryModel.created_at.asc())
        )
        all_memories = list(result.scalars().all())

    # Only live rows that are not the owner's take part (memory audit
    # review, 2026-09-27): a merge rewrites one row and hard-deletes the
    # rest, and what the owner said changes only with their own words — the
    # rule consolidation's merge step keeps. A retired row could absorb a live one here, or have
    # its text merged into one.
    all_memories = [m for m in all_memories if _is_live(m) and not _is_owners_row(m)]
    summary["total_memories"] = len(all_memories)
    if len(all_memories) < 2:
        print(f"[Tier Migration] Only {len(all_memories)} memories, nothing to dedup")
        return summary

    print(f"[Tier Migration] Scanning {len(all_memories)} memories for duplicates...")

    # Step 2: For each memory, find similar ones via embedding search
    # Track which memories have been absorbed so we skip them
    absorbed = set()
    merge_groups = []  # List of group dicts for LLM review

    for i, mem in enumerate(all_memories):
        if mem.id in absorbed:
            continue

        # Find similar memories using SQL vector search
        similar = await find_similar_memories(
            mem.content,
            threshold=similarity_threshold,
            limit=10,
            live_only=True,
        )

        # Filter out self, already-absorbed and the owner's rows
        candidates = [
            (s_mem, sim) for s_mem, sim in similar
            if s_mem.id != mem.id and s_mem.id not in absorbed and not _is_owners_row(s_mem)
        ]

        if not candidates:
            continue

        # Pure reinforcement for very high similarity (>= 0.92)
        pure_reinforce = [(s, sim) for s, sim in candidates if sim >= 0.92]
        llm_review = [(s, sim) for s, sim in candidates if sim < 0.92]

        # Handle pure reinforcement without LLM
        for s_mem, sim in pure_reinforce:
            summary["duplicates_found"] += 1
            if not dry_run:
                new_count = (mem.reinforcement_count or 0) + (s_mem.reinforcement_count or 0) + 1
                await update_memory(
                    mem.id,
                    reinforcement_count=new_count,
                    tier=_compute_tier(new_count),
                    importance=max(mem.importance or 0.5, s_mem.importance or 0.5),
                )
                await delete_memory(s_mem.id)
                absorbed.add(s_mem.id)
                summary["merges_performed"] += 1
                summary["memories_deleted"] += 1
                print(f"[Tier Migration] Pure reinforce: {s_mem.id[:8]}... -> {mem.id[:8]}... (sim={sim:.2f})")
            else:
                absorbed.add(s_mem.id)
                print(f"[Tier Migration] [DRY RUN] Would reinforce: {s_mem.content[:50]}... into {mem.content[:50]}... (sim={sim:.2f})")

        # Collect LLM-review candidates into groups for batch processing
        if llm_review:
            group_contents = [{"id": mem.id, "content": mem.content}]
            for s_mem, sim in llm_review:
                group_contents.append({"id": s_mem.id, "content": s_mem.content, "similarity": round(sim, 2)})
            merge_groups.append(group_contents)

        # Process LLM groups in batches
        if len(merge_groups) >= batch_size or (i == len(all_memories) - 1 and merge_groups):
            await _process_merge_groups(merge_groups, summary, model, dry_run, absorbed)
            merge_groups = []

    # Process any remaining groups
    if merge_groups:
        await _process_merge_groups(merge_groups, summary, model, dry_run, absorbed)

    # Step 3: Auto-promote all memories based on final reinforcement counts
    if not dry_run:
        async with async_session() as session:
            result = await session.execute(select(MemoryModel))
            remaining = list(result.scalars().all())

        for m in remaining:
            expected = _compute_tier(m.reinforcement_count or 0)
            current = m.tier or "observation"
            if current != expected:
                await update_memory(m.id, tier=expected)
                summary["promotions"] += 1
                print(f"[Tier Migration] Promoted: {m.id[:8]}... {current} -> {expected}")

    print(f"[Tier Migration] Complete: {summary}")
    return summary


async def _process_merge_groups(
    groups: list,
    summary: dict,
    model: str,
    dry_run: bool,
    absorbed: set,
) -> None:
    """Process a batch of merge groups via background LLM."""
    llm = build_background_llm(max_tokens=4096)

    prompt = DEDUP_CLASSIFY_PROMPT.format(
        groups_json=json.dumps(groups, indent=2)
    )

    try:
        response = await llm.ainvoke([
            make_background_system_message(
                "Classify groups of similar memories as MERGE or KEEP_ALL. Return valid JSON.",
            ),
            HumanMessage(content=prompt),
        ])
        summary["haiku_calls"] += 1

        response_text = response.content
        if isinstance(response_text, list):
            response_text = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in response_text
            )
        response_text = response_text.strip()

        result_data = extract_json_payload(
            response_text, expected="object", subsystem="consolidation_conflict"
        )
        decisions = result_data.get("groups", [])

        for decision in decisions:
            action = decision.get("action", "KEEP_ALL")

            if action == "MERGE":
                keep_id = decision.get("keep_id")
                absorb_ids = decision.get("absorb_ids", [])
                merged_content = decision.get("merged_content")

                if not keep_id or not absorb_ids or not merged_content:
                    continue
                # Only rows the model was shown together: any other id it
                # names was deleted as it stood (2026-09-27).
                if not any({keep_id, *absorb_ids} <= {m["id"] for m in g} for g in groups):
                    continue

                summary["duplicates_found"] += len(absorb_ids)

                if dry_run:
                    print(f"[Tier Migration] [DRY RUN] Would merge {len(absorb_ids)} memories into {keep_id[:8]}...")
                    for aid in absorb_ids:
                        absorbed.add(aid)
                    continue

                # Get current state of surviving memory
                surviving = await get_memory_by_id(keep_id)
                if not surviving:
                    continue

                # Sum reinforcement counts from all absorbed memories
                total_reinforcement = surviving.reinforcement_count or 0
                for aid in absorb_ids:
                    absorbed_mem = await get_memory_by_id(aid)
                    if absorbed_mem:
                        total_reinforcement += (absorbed_mem.reinforcement_count or 0) + 1
                        await delete_memory(aid)
                        absorbed.add(aid)
                        summary["memories_deleted"] += 1

                new_tier = _compute_tier(total_reinforcement)
                temporal = decision.get("temporal_nature")
                update_kwargs = {
                    "content": merged_content,
                    "reinforcement_count": total_reinforcement,
                    "tier": new_tier,
                }
                if temporal and temporal in ("timeless", "temporary", "evolving"):
                    update_kwargs["temporal_nature"] = temporal

                await update_memory(keep_id, **update_kwargs)
                summary["merges_performed"] += 1
                print(f"[Tier Migration] Merged {len(absorb_ids)} -> {keep_id[:8]}... (count={total_reinforcement}, tier={new_tier})")

    except Exception as e:
        print(f"[Tier Migration] Batch error: {e}")
        summary["errors"] += 1


async def retrieve_entity_memories(query: str, limit: int = 5) -> List[Memory]:
    """Retrieve memories linked to entities detected in the query.

    Fast substring match against known entities, then loads their linked memories.
    Entity memories get a 0.1 score boost. Live rows only (2026-09-27):
    ``get_memory_by_id`` returns a retired row too, and this path feeds
    every turn that names someone; the mentions are filtered before the
    limit, so retired ones do not crowd out the live.
    """
    from services.entity_service import detect_entities_in_text, get_entity_memories

    entities = await detect_entities_in_text(query)
    if not entities:
        return []

    seen_ids = set()
    results = []

    for entity in entities[:3]:  # Cap at 3 entities
        mentions = await get_entity_memories(entity.id, limit=limit, live_only=True)
        for mention in mentions:
            mid = mention["memory_id"]
            if mid in seen_ids:
                continue
            seen_ids.add(mid)

            mem = await get_memory_by_id(mid)
            if mem and _is_live(mem):
                mem.score = getattr(mem, 'score', 0.0) + 0.1  # Entity boost
                results.append(mem)

            if len(results) >= limit:
                break
        if len(results) >= limit:
            break

    return results
