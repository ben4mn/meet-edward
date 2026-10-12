"""Speculative recall — pre-fetch memory queries while the LLM streams.

When Edward starts mentioning entities mid-response ("I'll ask Sasha
about the Lisbon trip"), a real human would already have associative
recall warmed up — names trigger context. We can mimic that by
extracting entity-shaped tokens from the LLM's streaming output and
firing async memory queries against pgvector + Graphiti BEFORE the LLM
explicitly tool-calls for them. Results land in the N2 per-conversation
cache, so the next ``remember_search`` returns ~10us instead of ~50ms.

Two pieces:

* ``extract_entity_candidates(text)`` — pure heuristic. Capitalized
  multi-letter words that aren't sentence-initial, common stop-words,
  or noisy acronyms.

* ``SpeculativeRecaller`` — async actor scoped to one conversation.
  ``observe(text)`` extracts entities, dedupes against ones already
  searched in this conversation, and schedules ``search_memories``
  calls. ``drain()`` waits for in-flight scheduled tasks (used by
  tests; production fire-and-forget can ignore).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Iterable, List, Optional, Set

logger = logging.getLogger(__name__)


# Words that look entity-shaped (capitalized) but are common English
# stop words / sentence starters. Conservative — we'd rather waste a
# query than miss an entity. Most entities are proper nouns the LLM
# will write mid-sentence.
_NON_ENTITY_TOKENS: Set[str] = {
    # Articles, conjunctions, prepositions that get capitalized at sentence start
    "I", "I'm", "I'll", "I've", "I'd",
    "The", "A", "An", "And", "Or", "But", "So", "Yet", "Nor", "For",
    "If", "Then", "Else", "When", "While", "As", "Because", "Since",
    "Though", "Although",
    "In", "On", "At", "By", "To", "Of", "From", "With", "Without",
    "About", "Over", "Under", "Above", "Below", "Between", "Through",
    # Common modals / verbs that get capitalized at sentence starts
    "Is", "Was", "Will", "Would", "Should", "Could", "Can", "May",
    "Might", "Must", "Have", "Had", "Has", "Do", "Does", "Did", "Be",
    "Been", "Being", "Are",
    # Time / quantity
    "Today", "Tomorrow", "Yesterday", "Now", "Later", "Soon",
    "Some", "All", "Many", "Few", "More", "Less", "Most",
    "There", "Here", "This", "That", "These", "Those",
    "You", "Your", "We", "Our", "They", "Their", "He", "She", "It",
    "His", "Her", "My", "Mine", "Yours",
    # Days / months land in sentences but rarely as the entity we want
    # to recall against — keep them as entities for travel/calendar
    # contexts so they're searchable.
    # Generic acronyms / units
    "OK", "OKAY", "FYI", "LOL", "TBH", "IDK", "SMS", "API", "URL",
    "USA", "UK", "EU", "PM", "AM",
    "Weather",  # noisy false positive in test fixture; keep generic words out
    # System/utility
    "System", "Edward", "Claude", "User",
}


# Regex: word starting with capital letter, length >= 2. Captures
# trailing possessive 's so we can strip it for canonical form.
_CAP_WORD_RE = re.compile(r"\b([A-Z][A-Za-z0-9]+)(?:'s)?\b")


def extract_entity_candidates(text: str) -> List[str]:
    """Return capitalized tokens from text that look like entity names.

    Heuristic: any word starting with a capital letter (length >= 2),
    minus a stop-word list that catches noisy sentence-initial
    starters ("The", "Today", "I'll", ...). Possessive "'s" is
    stripped so Sasha and Sasha's collapse to one entity.

    Returns deduped, in first-seen order. False positives (extra
    speculative queries) are cheap; false negatives leave latency
    on the table — so the gate is conservative.
    """
    if not text:
        return []

    seen: Set[str] = set()
    out: List[str] = []
    for m in _CAP_WORD_RE.finditer(text):
        token = m.group(1)
        if token in _NON_ENTITY_TOKENS:
            continue
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


class SpeculativeRecaller:
    """Per-conversation speculative-memory pre-fetcher.

    Usage:
        recaller = SpeculativeRecaller(conversation_id="conv-1")
        # As the LLM streams text:
        await recaller.observe("I'll check with Sasha at the Harbor Inn.")
        # ... LLM streams more
        await recaller.observe("Lisbon trip starts on Friday.")
        # When done streaming, optionally drain (for tests / shutdown):
        await recaller.drain()

    Each unique entity triggers exactly one memory query per
    conversation lifetime. Results land in the N2 cache via the
    underlying ``remember_search`` cache logic.
    """

    def __init__(self, conversation_id: str):
        self._conversation_id = conversation_id
        self._dispatched: Set[str] = set()
        self._tasks: List[asyncio.Task] = []

    async def observe(self, text: str) -> None:
        """Extract entities from text and schedule speculative queries
        for any not yet dispatched in this conversation."""
        if not text or not self._conversation_id:
            return
        for entity in extract_entity_candidates(text):
            if entity in self._dispatched:
                continue
            self._dispatched.add(entity)
            self._tasks.append(asyncio.create_task(self._fetch(entity)))

    async def _fetch(self, entity: str) -> None:
        """Run search_memories for one entity, populate the N2 cache."""
        try:
            from services.memory_service import search_memories
            from services.graph import tools as _gt

            memories, total = await search_memories(query=entity, limit=10, live_only=True)

            # Mirror the formatting that remember_search produces, so a
            # follow-up tool call returns the same shape from cache.
            if not memories:
                formatted = f"No memories found matching '{entity}'."
            else:
                lines = [f"Found {total} memories matching '{entity}':\n"]
                for m in memories[:10]:
                    score_str = f" (score: {m.score:.2f})" if getattr(m, "score", 0) > 0 else ""
                    tn = getattr(m, "temporal_nature", "timeless") or "timeless"
                    tn_tag = f" [{tn}]" if tn != "timeless" else ""
                    lines.append(
                        f"- [{m.memory_type}] {m.content}{tn_tag} (ID: {m.id}){score_str}"
                    )
                formatted = "\n".join(lines)

            # Stash directly into the N2 cache so the LLM's later
            # remember_search hits it without extra DB I/O.
            cache_key = (self._conversation_id, entity, None)
            import time as _time
            if hasattr(_gt, "_memory_search_cache"):
                _gt._memory_search_cache[cache_key] = (_time.time(), formatted)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("speculative recall failed for %r: %s", entity, e)

    async def drain(self, timeout: Optional[float] = 5.0) -> None:
        """Wait for in-flight tasks. Used by tests; production callers
        can fire-and-forget."""
        if not self._tasks:
            return
        try:
            await asyncio.wait_for(asyncio.gather(*self._tasks, return_exceptions=True), timeout=timeout)
        except asyncio.TimeoutError:
            for t in self._tasks:
                if not t.done():
                    t.cancel()
        finally:
            self._tasks.clear()
