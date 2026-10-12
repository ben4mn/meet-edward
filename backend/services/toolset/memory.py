"""``memory`` — memories and documents in one tool (docs/architecture/memory.md).

Each action wraps today's implementation in ``services.graph.tools``:

| action | wraps |
|---|---|
| search | ``remember_search`` + ``search_documents`` (``list_documents`` for an empty documents query) |
| remember | ``remember`` |
| update | ``remember_update`` (text, horizon; ``until="now"`` retires) + ``update_memory_confidence`` |
| close | ``close_intention`` |
| why | ``how_do_i_know`` |
| forget | ``forget_about`` — the owner's own conversation only |
| doc_read / doc_write / doc_edit | ``read_document``, ``save_document`` / ``edit_document``, an exact edit |

The rails: nothing is hard-deleted from Edward's own turns
(``remember_forget`` is not reachable; retiring keeps history — the
rule "don't delete memories"); what the owner said changes only in their
own turn (``_owners_row_refusal``, now also on a confidence change); no document
is deleted (``Edward: state`` is never deleted).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from langchain_core.tools import tool

from services.owner import owner_doc, owner_name
from services.toolset import policy as policy_mod
from services.toolset.result import ToolFailure

MEMORY_ACTIONS = ("search", "remember", "update", "close", "why", "forget", "doc_read", "doc_write", "doc_edit")
KINDS = ("memories", "documents", "all")

# Replies of the wrapped tools that are failures without saying "Tool error:".
_FAILURE_HEADS = ("Tool error:", "Failed to", "Could not", "Memory with ID", "Error:")
_NOT_FOUND_TAIL = " not found."


def _legacy():
    from services.graph import tools as legacy

    return legacy


def _checked(reply: Any) -> str:
    text = str(reply if reply is not None else "")
    stripped = text.strip()
    if stripped.startswith(_FAILURE_HEADS) or (stripped.startswith("Document ") and stripped.endswith(_NOT_FOUND_TAIL)):
        raise ToolFailure(stripped.removeprefix("Tool error:").strip())
    return text


def _need(value: Any, name: str, action: str) -> Any:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ToolFailure(f"{action} needs {name}")
    return value


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


async def _search(query: Optional[str], kind: Optional[str], type_: Optional[str], tags: Optional[str]) -> str:
    legacy = _legacy()
    kind = (kind or "all").strip().lower()
    if kind not in KINDS:
        raise ToolFailure(f"kind must be one of {', '.join(KINDS)}")
    q = (query or "").strip()
    parts: List[str] = []
    if kind in ("memories", "all"):
        if not q:
            raise ToolFailure("search needs query (what to look for)")
        parts.append(_checked(await legacy.remember_search.ainvoke({"query": q, "memory_type": type_})))
    if kind in ("documents", "all"):
        if q:
            parts.append(_checked(await legacy.search_documents.ainvoke({"query": q, "tags": tags})))
        else:
            parts.append(_checked(await legacy.list_documents.ainvoke({"tags": tags})))
    return "\n\n".join(p for p in parts if p)


async def _update(id_: str, content: Optional[str], until: Optional[str], confidence: Optional[float]) -> str:
    legacy = _legacy()
    if content is None and until in (None, "") and confidence is None:
        raise ToolFailure("update needs content, until or confidence")
    replies: List[str] = []
    if confidence is not None:
        from services.memory_service import get_memory_by_id

        existing = await get_memory_by_id(id_)
        if existing is None:
            raise ToolFailure(f"no memory with id {id_}")
        refused = legacy._owners_row_refusal(existing, "change")
        if refused:
            raise ToolFailure(refused)
        try:
            value = float(confidence)
        except (TypeError, ValueError) as exc:
            raise ToolFailure("confidence must be a number from 0 to 1") from exc
        replies.append(_checked(await legacy.update_memory_confidence.ainvoke(
            {"memory_id": id_, "new_confidence": value})))
    if content is not None or until not in (None, ""):
        horizon = until
        if isinstance(horizon, str) and horizon.strip().lower() == "now":
            horizon = _now_iso()
        replies.append(_checked(await legacy.remember_update.ainvoke(
            {"memory_id": id_, "new_content": content, "valid_until": horizon})))
    return "; ".join(r for r in replies if r) or "ok"


async def _doc_edit(id_: str, old: Optional[str], new: Optional[str], append: Optional[str]) -> str:
    from services.document_service import get_document_by_id, update_document

    if old is None and append is None:
        raise ToolFailure("doc_edit needs old + new (an exact replacement) or append")
    doc = await get_document_by_id(id_)
    if doc is None:
        raise ToolFailure(f"no document with id {id_}")
    content = doc.content or ""
    if old is not None:
        count = content.count(old)
        if count != 1:
            raise ToolFailure(f"the text to replace appears {count} times in \"{doc.title}\" — it must appear "
                              "exactly once; quote more of it")
        content = content.replace(old, new or "", 1)
    if append:
        content = content.rstrip("\n") + "\n" + append
    updated = await update_document(document_id=id_, content=content)
    if not updated:
        raise ToolFailure(f"no document with id {id_}")
    return f'Document updated: "{updated.title}" (ID: {updated.id})'


async def memory_impl(action: str, query: Optional[str] = None, kind: Optional[str] = None,
                      content: Optional[str] = None, type: Optional[str] = None, about: Optional[str] = None,
                      origin: Optional[str] = None, until: Optional[str] = None,
                      confidence: Optional[float] = None, id: Optional[str] = None,
                      outcome: Optional[str] = None, claim: Optional[str] = None, title: Optional[str] = None,
                      tags: Optional[str] = None, old: Optional[str] = None, new: Optional[str] = None,
                      append: Optional[str] = None, confirm: Optional[bool] = None,
                      importance: Optional[float] = None) -> str:
    legacy = _legacy()
    act = (action or "").strip().lower()
    if act not in MEMORY_ACTIONS:
        raise ToolFailure(f"action must be one of {', '.join(MEMORY_ACTIONS)}")
    if act == "search":
        return await _search(query, kind, type, tags)
    if act == "remember":
        args: dict = {"content": _need(content, "content", act), "memory_type": type or "context",
                      "about": about or "self", "origin": origin or "thought", "valid_until": until,
                      "confidence": confidence}
        if importance is not None:
            args["importance"] = importance
        return _checked(await legacy.remember.ainvoke(args))
    if act == "update":
        return await _update(_need(id, "id", act), content, until, confidence)
    if act == "close":
        return _checked(await legacy.close_intention.ainvoke(
            {"memory_id": _need(id, "id", act), "outcome": _need(outcome, "outcome", act)}))
    if act == "why":
        if not (id or claim or query):
            raise ToolFailure("why needs id (a memory id) or claim")
        return _checked(await legacy.how_do_i_know.ainvoke({"memory_id": id, "claim": claim or (query if not id else None)}))
    if act == "forget":
        policy = policy_mod.current_policy()
        if not policy.may_forget():
            raise ToolFailure(f"forget runs only in {owner_name()}'s own conversation with you, when they ask; "
                              "to retire a memory of yours, update it with until=\"now\"")
        subject = _need(query or claim or content, "query (who or what to forget)", act)
        return _checked(await legacy.forget_about.ainvoke(
            {"subject": subject, "confirm": bool(confirm), "include_documents": (kind or "").lower() in ("documents", "all")}))
    if act == "doc_read":
        return _checked(await legacy.read_document.ainvoke({"document_id": _need(id, "id", act)}))
    if act == "doc_write":
        if id:
            if content is None and title is None and tags is None:
                raise ToolFailure("doc_write to an existing document needs content, title or tags")
            return _checked(await legacy.edit_document.ainvoke(
                {"document_id": id, "title": title, "content": content, "tags": tags}))
        return _checked(await legacy.save_document.ainvoke(
            {"title": _need(title, "title", act), "content": _need(content, "content", act), "tags": tags}))
    # doc_edit
    return await _doc_edit(_need(id, "id", act), old, new, append)


@tool
@owner_doc
async def memory(action: str, query: Optional[str] = None, kind: Optional[str] = None,
                 content: Optional[str] = None, type: Optional[str] = None, about: Optional[str] = None,
                 origin: Optional[str] = None, until: Optional[str] = None, confidence: Optional[float] = None,
                 id: Optional[str] = None, outcome: Optional[str] = None, claim: Optional[str] = None,
                 title: Optional[str] = None, tags: Optional[str] = None, old: Optional[str] = None,
                 new: Optional[str] = None, append: Optional[str] = None, confirm: Optional[bool] = None) -> str:
    """What you know: memories and documents. Search before you assume; remember what will matter later.

    action:
      search — query (+ kind memories|documents|all, type, tags); an empty documents query lists them;
      remember — content, type (fact|preference|context|instruction|intention), about (self|owner|contact|world),
        origin (thought|read|did), until (ISO time it stops applying), confidence;
      update — id + content and/or until (until="now" retires it; it stays on record) and/or confidence;
      close — id + outcome: close one of your intentions, keeping its history;
      why — id or claim: where a memory came from (answer "how do you know" only from this);
      forget — query: only when {OWNER} asks, in their own conversation; first call previews, confirm=true deletes;
      doc_read — id; doc_write — title + content (new) or id + fields; doc_edit — id + old/new (exact, once) or
        append. "Edward: state" is yours to keep current.
    What {OWNER} told you changes only in their own turn. Nothing is deleted from your own turns."""
    return await memory_impl(action=action, query=query, kind=kind, content=content, type=type, about=about,
                             origin=origin, until=until, confidence=confidence, id=id, outcome=outcome,
                             claim=claim, title=title, tags=tags, old=old, new=new, append=append, confirm=confirm)
