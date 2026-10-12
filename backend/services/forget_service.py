"""A verified forget: a plan the owner sees, a delete of exactly that, and counts.

Why (2026-09-24): another assistant's "forget" searches memories and
files and removes matches "to the best of its ability", and deleting a
chat does not remove what it learned. "Forget about Alex" deserves better
than best effort: the owner should see what will go, it should be exactly that,
and afterwards Edward should be able to say how much went and that nothing
still matches.

1. ``plan_forget(subject)`` (a dry run) finds
   * memories that name the subject as whole words (``matches_subject``:
     "Alex" hits "Alex's", never "Alexa" or "Alexandra"), memories linked
     to an entity with that name, and memories within ``SEMANTIC_MIN``
     cosine of it;
   * the rows that copy or point at them: ``memory_enrichments``,
     ``memory_connections``, ``memory_flags``, ``entity_mentions``,
     ``graph_ingest_queue`` (and the matched entities themselves);
   * documents whose title or content name it — listed apart, deleted only
     with ``include_documents`` — except the state document, which is
     edited, never deleted;
   * conversations whose title or search tags name it — counted, not
     deleted (the owner deletes threads from the sidebar).
2. ``apply_forget(plan_id | subject)`` deletes the plan in one transaction,
   clears the in-process caches that hold memory text, writes a
   ``ledger_events`` row (kind ``forget``, counts only — never the subject
   or content), and plans again to report what still matches.

A subject that reduces to nothing specific ("everything", the owner's own
name, "me") is refused: forgetting everything about the owner is not a thing
a sentence should be able to do.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from services.owner import owner_name, owner_names

SEMANTIC_MIN = 0.7          # cosine for a memory that matches by meaning alone
SEMANTIC_LIMIT = 25
PLAN_TTL_S = 3600
MAX_PREVIEW_MEMORIES = 30
MAX_PREVIEW_CONVERSATIONS = 10
STATE_DOC_TITLE = "Edward: state"

STOPWORDS = frozenset("""
a an the my our his her their its your you me i we us he she they it this that these those
about regarding re on of to and or with for in at by from as is are was were be
forget forgetting remove delete erase everything anything all any stuff things thing info information
related memories memory memory's know knowledge what which who whom whose please just also
owner owners mr mrs ms
""".split())

_TOKEN = re.compile(r"[a-z0-9][a-z0-9'’\-]*")


def _owner_stopwords() -> frozenset:
    """Every word of the owner's own names (``EDWARD_OWNER_NAME``, the full
    name, aliases) and its plain plural: a subject that is only the owner
    means everything. Read per call."""
    words = set()
    for name in owner_names():
        for tok in _TOKEN.findall(name):
            tok = tok.strip("'’-")
            if tok:
                words.update({tok, tok + "s"})
    return frozenset(words)


# ---------------------------------------------------------------- pure --

def subject_terms(subject: Optional[str]) -> List[str]:
    """The words that must all appear. Raises ``ValueError`` when nothing
    specific is left."""
    terms: List[str] = []
    owner_words = _owner_stopwords()
    for tok in _TOKEN.findall((subject or "").lower()):
        tok = re.sub(r"['’]s$", "", tok).strip("'’-")
        if len(tok) < 2 or tok in STOPWORDS or tok in owner_words or tok in terms:
            continue
        terms.append(tok)
    if not terms:
        raise ValueError(f"say who or what to forget — {subject!r} is too broad")
    return terms


def _term_re(term: str) -> "re.Pattern[str]":
    return re.compile(rf"(?<![a-z0-9]){re.escape(term)}(?:['’]s|s|es)?(?![a-z0-9])", re.IGNORECASE)


def matches_subject(text: Optional[str], terms: Iterable[str]) -> bool:
    """Every term appears as a whole word (a possessive or plural allowed)."""
    body = text or ""
    terms = list(terms)
    return bool(terms) and all(_term_re(t).search(body) for t in terms)


@dataclass
class ForgetPlan:
    id: str
    subject: str
    terms: List[str]
    created_at: float
    conversation_id: Optional[str] = None
    turn_id: Optional[str] = None
    memories: List[Dict[str, Any]] = field(default_factory=list)       # {id, content, why}
    documents: List[Dict[str, Any]] = field(default_factory=list)      # {id, title, why}
    protected_documents: List[Dict[str, Any]] = field(default_factory=list)
    enrichment_ids: List[str] = field(default_factory=list)
    connection_ids: List[str] = field(default_factory=list)
    flag_ids: List[str] = field(default_factory=list)
    mention_ids: List[str] = field(default_factory=list)
    entity_ids: List[str] = field(default_factory=list)
    entity_names: List[str] = field(default_factory=list)
    queue_ids: List[str] = field(default_factory=list)
    conversations: List[Dict[str, Any]] = field(default_factory=list)  # {id, title}

    @property
    def memory_ids(self) -> List[str]:
        return [m["id"] for m in self.memories]


def plan_dict(plan: ForgetPlan) -> Dict[str, Any]:
    """The preview for the Memory room."""
    return {
        "id": plan.id,
        "subject": plan.subject,
        "terms": plan.terms,
        "memories": plan.memories,
        "documents": plan.documents,
        "protected_documents": plan.protected_documents,
        "related": {
            "enrichments": len(plan.enrichment_ids),
            "connections": len(plan.connection_ids),
            "flags": len(plan.flag_ids),
            "entity_mentions": len(plan.mention_ids),
            "entities": len(plan.entity_ids),
            "graph_queue": len(plan.queue_ids),
        },
        "conversations": {"count": len(plan.conversations),
                          "titles": [c["title"] for c in plan.conversations[:MAX_PREVIEW_CONVERSATIONS]]},
    }


def _plural(n: int, word: str) -> str:
    if n == 1:
        return f"1 {word}"
    return f"{n} {word[:-1] + 'ies' if word.endswith('y') else word + 's'}"


def format_plan_preview(plan: ForgetPlan) -> str:
    """What the tool shows Edward to show the owner."""
    owner = owner_name()
    lines = [f"Forget preview for \"{plan.subject}\" (matching: {', '.join(plan.terms)}) — nothing deleted yet.", ""]
    lines.append(f"{_plural(len(plan.memories), 'memory')} would be deleted:")
    for m in plan.memories[:MAX_PREVIEW_MEMORIES]:
        lines.append(f"  - {m['content'][:160]} ({m['why']})")
    if len(plan.memories) > MAX_PREVIEW_MEMORIES:
        lines.append(f"  … and {len(plan.memories) - MAX_PREVIEW_MEMORIES} more")
    related = plan_dict(plan)["related"]
    extra = [f"{v} {k.replace('_', ' ')}" for k, v in related.items() if v]
    if extra:
        lines.append("With them: " + ", ".join(extra) + ".")
    if plan.entity_names:
        lines.append("Entities removed: " + ", ".join(plan.entity_names) + ".")
    if plan.documents:
        lines.append("")
        lines.append(f"{_plural(len(plan.documents), 'document')} mention it (kept unless {owner} says to delete them too):")
        for d in plan.documents:
            lines.append(f"  - {d['title']} (document {d['id']})")
    if plan.protected_documents:
        lines.append("Your state document mentions it; it is never deleted — edit_document that line out instead.")
    if plan.conversations:
        titles = "; ".join(f"\"{c['title']}\"" for c in plan.conversations[:MAX_PREVIEW_CONVERSATIONS])
        lines.append("")
        lines.append(f"{_plural(len(plan.conversations), 'conversation')} mention it ({titles}). They are not "
                     f"deleted — {owner} can delete those threads from the sidebar.")
    lines.append("")
    lines.append(f"Show {owner} this list and ask them to confirm. On their reply call forget_about again with "
                 "confirm=true (and include_documents=true only if they want those documents gone too).")
    return "\n".join(lines)


def format_result(result: Dict[str, Any]) -> str:
    d = result.get("deleted") or {}
    still = result.get("still_matching") or {}
    parts = [_plural(int(d.get("memories", 0)), "memory")]
    if d.get("documents"):
        parts.append(_plural(int(d["documents"]), "document"))
    related = sum(int(d.get(k, 0)) for k in ("enrichments", "connections", "flags", "entity_mentions",
                                             "entities", "graph_queue"))
    if related:
        parts.append(f"{related} linked rows (enrichments, connections, flags, entity links, graph queue)")
    lines = [f"Forgot \"{result.get('subject')}\": deleted " + ", ".join(parts) + "."]
    lines.append(f"Checked again: {still.get('memories', 0)} memories and {still.get('documents', 0)} documents "
                 "still match.")
    if result.get("protected_documents"):
        lines.append("Your state document still mentions it — edit_document it.")
    if result.get("conversations_mentioning"):
        lines.append(f"{result['conversations_mentioning']} conversation(s) still mention it; {owner_name()} "
                     "can delete them from the sidebar. Message history (iMessage, the heartbeat log) is not touched.")
    if result.get("graph_note"):
        lines.append(result["graph_note"])
    return "\n".join(lines)


# ------------------------------------------------------------ plan store --

_plans: Dict[str, ForgetPlan] = {}


def _prune_plans(now: Optional[float] = None) -> None:
    now = now or time.time()
    for pid in [p for p, plan in _plans.items() if now - plan.created_at > PLAN_TTL_S]:
        _plans.pop(pid, None)


def get_plan(plan_id: str) -> Optional[ForgetPlan]:
    _prune_plans()
    return _plans.get(plan_id)


def latest_plan_for(subject: str, conversation_id: Optional[str]) -> Optional[ForgetPlan]:
    """The newest live plan for the same subject (same terms) made in the
    same conversation."""
    _prune_plans()
    try:
        terms = subject_terms(subject)
    except ValueError:
        return None
    candidates = [p for p in _plans.values() if p.terms == terms and p.conversation_id == conversation_id]
    return max(candidates, key=lambda p: p.created_at) if candidates else None


def reset_plans() -> None:
    """Tests."""
    _plans.clear()


# ------------------------------------------------------------------ I/O --

def _ilike_all(column, terms: List[str]):
    """SQL prefilter: every term as a substring (the whole-word check is
    ``matches_subject``, in Python). Terms are ``[a-z0-9'’-]`` only, so no
    LIKE wildcards can ride in."""
    from sqlalchemy import and_

    return and_(*[column.ilike(f"%{t}%") for t in terms])


async def _memory_matches(session, terms: List[str]) -> Dict[str, Dict[str, Any]]:
    from sqlalchemy import select

    from services.database import MemoryModel

    rows = (await session.execute(
        select(MemoryModel.id, MemoryModel.content).where(_ilike_all(MemoryModel.content, terms))
    )).all()
    out: Dict[str, Dict[str, Any]] = {}
    for mid, content in rows:
        if matches_subject(content, terms):
            out[mid] = {"id": mid, "content": content, "why": f"mentions {' + '.join(terms)}"}
    return out


async def _entity_matches(session, terms: List[str]):
    from sqlalchemy import select

    from services.database import EntityModel

    rows = (await session.execute(select(EntityModel))).scalars().all()
    hits = []
    for e in rows:
        names = [e.name or ""]
        try:
            names += [str(a) for a in (json.loads(e.aliases or "[]") or [])]
        except (TypeError, ValueError):
            pass
        if any(matches_subject(n, terms) for n in names):
            hits.append(e)
    return hits


async def plan_forget(subject: str, *, conversation_id: Optional[str] = None,
                      turn_id: Optional[str] = None, remember: bool = True) -> ForgetPlan:
    """What forgetting ``subject`` would delete. Deletes nothing."""
    from sqlalchemy import or_, select

    from services.database import (
        ConversationModel,
        DocumentModel,
        EntityMentionModel,
        GraphIngestQueueModel,
        MemoryConnectionModel,
        MemoryEnrichmentModel,
        MemoryFlagModel,
        MemoryModel,
        async_session,
    )

    terms = subject_terms(subject)
    plan = ForgetPlan(id=str(uuid.uuid4()), subject=" ".join((subject or "").split())[:200], terms=terms,
                      created_at=time.time(), conversation_id=conversation_id, turn_id=turn_id)

    async with async_session() as session:
        found = await _memory_matches(session, terms)

        entities = await _entity_matches(session, terms)
        plan.entity_ids = [e.id for e in entities]
        plan.entity_names = [e.name for e in entities]
        if entities:
            linked = (await session.execute(
                select(EntityMentionModel.memory_id, EntityMentionModel.entity_id)
                .where(EntityMentionModel.entity_id.in_(plan.entity_ids))
                .where(EntityMentionModel.memory_id.isnot(None))
            )).all()
            name_by_id = {e.id: e.name for e in entities}
            missing = [mid for mid, _ in linked if mid not in found]
            if missing:
                rows = (await session.execute(
                    select(MemoryModel.id, MemoryModel.content).where(MemoryModel.id.in_(missing))
                )).all()
                content_by_id = dict(rows)
                for mid, eid in linked:
                    if mid in content_by_id and mid not in found:
                        found[mid] = {"id": mid, "content": content_by_id[mid],
                                      "why": f"linked to {name_by_id.get(eid)}"}

    # By meaning alone, strictly: a short name rarely clears this, a
    # paraphrase ("separated" for "divorce") can.
    try:
        from services.memory_service import find_similar_memories

        for m, sim in await find_similar_memories(" ".join(terms), threshold=SEMANTIC_MIN, limit=SEMANTIC_LIMIT):
            if m.id not in found:
                found[m.id] = {"id": m.id, "content": m.content, "why": f"close in meaning ({sim:.2f})"}
    except Exception as exc:  # noqa: BLE001 — keyword matches still stand
        print(f"[forget] semantic pass skipped: {type(exc).__name__}: {exc}")

    plan.memories = sorted(found.values(), key=lambda m: m["content"].lower())
    ids = plan.memory_ids

    async with async_session() as session:
        by_id = [MemoryEnrichmentModel.memory_id.in_(ids)] if ids else []
        enrich = (await session.execute(
            select(MemoryEnrichmentModel.id, MemoryEnrichmentModel.memory_id, MemoryEnrichmentModel.memory_content)
            .where(or_(*by_id, _ilike_all(MemoryEnrichmentModel.memory_content, terms)))
        )).all()
        plan.enrichment_ids = [eid for eid, mid, text in enrich
                               if mid in found or matches_subject(text, terms)]
        if ids:
            plan.connection_ids = list((await session.execute(
                select(MemoryConnectionModel.id).where(or_(MemoryConnectionModel.memory_id_a.in_(ids),
                                                           MemoryConnectionModel.memory_id_b.in_(ids)))
            )).scalars().all())
            plan.flag_ids = list((await session.execute(
                select(MemoryFlagModel.id).where(or_(MemoryFlagModel.memory_id.in_(ids),
                                                     MemoryFlagModel.related_memory_id.in_(ids)))
            )).scalars().all())
        mention_q = select(EntityMentionModel.id)
        conds = []
        if ids:
            conds.append(EntityMentionModel.memory_id.in_(ids))
        if plan.entity_ids:
            conds.append(EntityMentionModel.entity_id.in_(plan.entity_ids))
        if conds:
            plan.mention_ids = list((await session.execute(mention_q.where(or_(*conds)))).scalars().all())
        by_id = [GraphIngestQueueModel.memory_id.in_(ids)] if ids else []
        queue = (await session.execute(
            select(GraphIngestQueueModel.id, GraphIngestQueueModel.memory_id, GraphIngestQueueModel.content)
            .where(or_(*by_id, _ilike_all(GraphIngestQueueModel.content, terms)))
        )).all()
        plan.queue_ids = [qid for qid, mid, text in queue if mid in found or matches_subject(text, terms)]

        docs = (await session.execute(
            select(DocumentModel.id, DocumentModel.title, DocumentModel.content)
            .where(or_(_ilike_all(DocumentModel.title, terms), _ilike_all(DocumentModel.content, terms)))
        )).all()
        for did, title, content in docs:
            if matches_subject(title, terms) or matches_subject(content, terms):
                entry = {"id": did, "title": title,
                         "why": "title" if matches_subject(title, terms) else "content"}
                if (title or "").strip().lower() == STATE_DOC_TITLE.lower():
                    plan.protected_documents.append(entry)
                else:
                    plan.documents.append(entry)
        plan.documents.sort(key=lambda d: (d["title"] or "").lower())

        convs = (await session.execute(
            select(ConversationModel.id, ConversationModel.title, ConversationModel.search_tags)
            .where(or_(_ilike_all(ConversationModel.title, terms), _ilike_all(ConversationModel.search_tags, terms)))
        )).all()
        plan.conversations = [{"id": cid, "title": title or "Untitled"} for cid, title, tags in convs
                              if matches_subject(title, terms) or matches_subject(tags, terms)]

    if remember:
        _prune_plans()
        _plans[plan.id] = plan
    return plan


def _clear_caches(memory_ids: Iterable[str]) -> None:
    """In-process copies of memory text: the tool's search cache, the
    dream query cache, the hot-memory set."""
    try:
        from services.graph.tools import _clear_memory_search_cache
        _clear_memory_search_cache()
    except Exception:  # noqa: BLE001
        pass
    try:
        from services.dream import context as dream_context
        getattr(dream_context, "_cache", {}).clear()
    except Exception:  # noqa: BLE001
        pass
    try:
        from services.dream import hot_memories
        for mid in memory_ids:
            hot_memories._entries.pop(mid, None)
    except Exception:  # noqa: BLE001
        pass


async def apply_forget(
    plan_id: Optional[str] = None,
    subject: Optional[str] = None,
    *,
    include_documents: bool = False,
    source: str = "chat",
) -> Dict[str, Any]:
    """Delete a plan (by id, or a fresh one for ``subject``) and return
    the counts plus what still matches. Raises ``ValueError`` for an
    unknown or expired plan id or a too-broad subject."""
    from sqlalchemy import delete

    from services.database import (
        DocumentModel,
        EntityMentionModel,
        EntityModel,
        GraphIngestQueueModel,
        MemoryConnectionModel,
        MemoryEnrichmentModel,
        MemoryFlagModel,
        MemoryModel,
        async_session,
    )

    if plan_id:
        plan = get_plan(plan_id)
        if plan is None:
            raise ValueError("that forget preview has expired — preview again")
    else:
        plan = await plan_forget(subject or "", remember=False)

    doc_ids = [d["id"] for d in plan.documents] if include_documents else []
    deleted: Dict[str, int] = {}

    async def _delete(session, model, column, ids: List[Any], key: str) -> None:
        if not ids:
            deleted[key] = 0
            return
        res = await session.execute(delete(model).where(column.in_(ids)))
        deleted[key] = int(res.rowcount or 0)

    async with async_session() as session:
        await _delete(session, MemoryModel, MemoryModel.id, plan.memory_ids, "memories")
        await _delete(session, DocumentModel, DocumentModel.id, doc_ids, "documents")
        await _delete(session, MemoryEnrichmentModel, MemoryEnrichmentModel.id, plan.enrichment_ids, "enrichments")
        await _delete(session, MemoryConnectionModel, MemoryConnectionModel.id, plan.connection_ids, "connections")
        await _delete(session, MemoryFlagModel, MemoryFlagModel.id, plan.flag_ids, "flags")
        await _delete(session, EntityMentionModel, EntityMentionModel.id, plan.mention_ids, "entity_mentions")
        await _delete(session, EntityModel, EntityModel.id, plan.entity_ids, "entities")
        await _delete(session, GraphIngestQueueModel, GraphIngestQueueModel.id, plan.queue_ids, "graph_queue")
        await session.commit()
    _plans.pop(plan.id, None)
    _clear_caches(plan.memory_ids)

    after = await plan_forget(plan.subject, remember=False)
    result: Dict[str, Any] = {
        "subject": plan.subject,
        "plan_id": plan.id,
        "deleted": deleted,
        "still_matching": {"memories": len(after.memories), "documents": len(after.documents)},
        "protected_documents": [d["title"] for d in plan.protected_documents],
        "conversations_mentioning": len(after.conversations),
    }
    try:
        from services.graph_memory_service import is_enabled

        if is_enabled():
            result["graph_note"] = ("The knowledge graph (Graphiti) keeps what it already ingested; it has no "
                                    "delete yet. Its pending queue rows were removed.")
    except Exception:  # noqa: BLE001
        pass

    # Counts only: the subject and the content are exactly what the owner asked
    # to be rid of, so they never reach a ledger.
    from services.event_ledger import record_event_now

    await record_event_now(kind="forget", source=source, outcome="applied", ref_id=plan.id,
                           detail={**deleted, "still_matching": result["still_matching"]["memories"]})
    print(f"[forget] applied plan {plan.id[:8]}: " + ", ".join(f"{k}={v}" for k, v in deleted.items()))
    return result
