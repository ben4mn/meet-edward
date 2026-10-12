"""How a ping turn ended, and what the runner does next (AREA 3, 2026-10-11).

Pure. ``decide`` reads the ping's shared context — the sends the transport
noted (``hooks.note_send_end``), the tools AREA 2's loop noted
(``hooks.note_tool``) and the loop's stop record (``hooks.note_stop``) —
with the turn's final text and exception. First match wins
(docs/architecture/pings.md, with the 2026-10-11 review):

1. an ok send that is not an ack, and the turn ended cleanly (or the send
   was still landing when it was cut: path (d)) → **answered**; a send and
   then a failure (an error, the bound, a restart, an honest ending, a
   ``max_tokens`` partial) was progress, not the answer: the ``unfinished``
   note follows it (the cap text for the cap; a graceful deadline's own
   text), and the turn never runs again;
2. a stand-down for this chat → **declined**; a ``tell_owner`` too, unless
   the ping must be answered and the turn ended cleanly with a meaningful
   final text — "I'm checking with <the owner>" is the reply the prompt asks for
   (THIRD_PARTY, MUST_ANSWER), so it goes to rule 9 (review 2026-10-11:
   Parker was left on read);
3. the daily spend cap → the cap text (must answer) or **declined**: the
   cap's own sentence (the owner's dollars, the knob's name) only in the
   owner's chat with themselves; anywhere else the plain line, and the owner
   gets the sentence
   (``spend_cap_group``). The loop's per-turn dollar cap
   (``turn_budget``) is not the day's: its closing words are delivered
   like a graceful deadline's (review 2026-10-11);
4. a transient failure with attempts left → **retry**;
5. a timeout → the ``timeout`` template, or **declined**;
6. any other failure, or an honest ending the loop wrote over a model
   failure (that text is never posted: AREA 2's loop says so in its stop
   record, the legacy loop only in the text's fixed opening, ``I stopped
   before finishing:``) → a template, or **declined**; such an ending is
   retryable, like the model failure under it;
7. may-answer and nothing sent → **declined**;
8. a refusal note or a ``max_tokens`` partial → a template;
9. the final text → deliver it as the reply, without any tool call written
   in it (``services.tool_markup``: with no tools bound, 44 of 47 benchmark
   replies wrote ``send_message`` as XML); a final text that was only such
   a call → the ``garbled`` template;
10. nothing → the ``empty`` template.

``deliver`` outcomes go through ``delivery.deliver``; the runner turns a
delivered reply into ``answered``, a delivered template into ``fallback``,
and anything undelivered into ``failed``.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from services import tool_markup

MAX_ATTEMPTS = 2

# AREA 4's adapter appends this when the model declined on a safety check
# (``services.claude_chat_model.REFUSAL_NOTE``); read lazily, this literal
# only when that module can't be imported.
_REFUSAL_NOTE_FALLBACK = "I can't help with that one: my model declined the request on a safety check."
# When the loop reported the cap without the exception's sentence.
CAP_SENTENCE_FALLBACK = "I've hit my spending limit for today, so I can't look into this until tomorrow."
# ``tool_policy.honest_ending``'s fixed opening, read lazily; this literal
# only when that module can't be imported.
_HONEST_PREFIX_FALLBACK = "I stopped before finishing:"
_CLASS_IN_PARENS = re.compile(r"\(([A-Za-z_][A-Za-z0-9_]*)\)")


@dataclass(frozen=True)
class Outcome:
    """``status``: answered | declined | retry | deliver. A ``deliver`` has
    ``purpose`` reply (``text``) or fallback (``template`` + ``detail``)."""

    status: str
    purpose: Optional[str] = None
    text: Optional[str] = None
    template: Optional[str] = None
    detail: str = ""
    why: str = ""
    send: Optional[Dict[str, Any]] = None


def refusal_note() -> str:
    try:
        from services.claude_chat_model import REFUSAL_NOTE

        return str(REFUSAL_NOTE)
    except Exception:  # noqa: BLE001
        return _REFUSAL_NOTE_FALLBACK


def honest_prefix() -> str:
    """The opening every honest ending shares ("I stopped before finishing:")."""
    try:
        from services.graph import tool_policy

        sample = str(tool_policy.honest_ending("\x00", []))
        head = sample.split("\x00", 1)[0].strip()
        if head:
            return head
    except Exception:  # noqa: BLE001
        pass
    return _HONEST_PREFIX_FALLBACK


def honest_ending_class(text: Optional[str], stop: Dict[str, Any]) -> Optional[str]:
    """The error class behind an honest ending the loop wrote instead of an
    answer, or None. AREA 2's loop says so (``outcome="honest_ending"``);
    the legacy loop only writes the text (a model-call deadline, a failed
    summary call). Pure but for the lazy prefix."""
    t = str(text or "").strip()
    honest_text = bool(t) and t.startswith(honest_prefix())
    if not honest_text and stop.get("outcome") != "honest_ending":
        return None
    if stop.get("error_class"):
        return str(stop["error_class"])
    m = _CLASS_IN_PARENS.search(t)
    if m:
        return m.group(1)
    if "did not answer within" in t:
        return "LLMCallTimeout"
    return "LLMStopped"


def _default_is_transient(exc: BaseException) -> bool:
    try:
        from services.mind_service import is_transient_error

        return bool(is_transient_error(exc))
    except Exception:  # noqa: BLE001
        return False


def _class_names(exc: Optional[BaseException]) -> set:
    if exc is None:
        return set()
    return {k.__name__ for k in type(exc).__mro__}


# How the loop ended short but on purpose: its closing words are meant for
# the chat. ``turn_budget`` is the turn's own dollar cap (``TurnPolicy.
# turn_cap_usd``), never the day's.
GRACEFUL_CUTS = ("deadline", "turn_budget")


def is_cap(error: Optional[BaseException], stop: Dict[str, Any]) -> bool:
    """The day's spend cap (``LLMSpendCapReached``, or a stop record that
    says so). Never the turn's own budget (``turn_budget``)."""
    return ("LLMSpendCapReached" in _class_names(error) or stop.get("outcome") == "cap"
            or stop.get("error_class") == "LLMSpendCapReached")


def private_to_owner(ctx: Dict[str, Any]) -> bool:
    """Is the ping's chat the owner's chat with themselves? Only there may a
    reply carry what is theirs alone (the cap's dollars). Pure."""
    return ctx.get("kind") == "owner_direct"


def is_timeout(error: Optional[BaseException]) -> bool:
    return isinstance(error, (asyncio.TimeoutError, TimeoutError))


def answering_send(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The first delivered send that answers (an ack never does)."""
    for send in ctx.get("sends") or []:
        if send.get("ok") and send.get("purpose") != "ack":
            return send
    return None


def landed_after_cut(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """An answering send that finished after the turn ended (``ended_at``,
    set by the runner when the turn returned or raised): it was in flight at
    the cut, so it was the turn's last act, not a progress line."""
    ended = ctx.get("ended_at")
    if ended is None:
        return None
    for send in reversed(ctx.get("sends") or []):
        if not send.get("ok") or send.get("purpose") == "ack" or send.get("at") is None:
            continue
        try:
            if send["at"] >= ended:
                return send
        except TypeError:
            continue
    return None


def declined_by_tool(ctx: Dict[str, Any]) -> Optional[str]:
    """``stand_down`` for this chat or ``tell_owner``, when either worked. A
    stand-down wins whatever the order: with one, nothing is posted."""
    from services.pings.hooks import same_chat

    told = False
    for tool in ctx.get("tools") or []:
        if not tool.get("ok"):
            continue
        name = str(tool.get("name") or "")
        if name == "tell_owner":
            told = True
            continue
        is_stand_down = (name == "messages" and tool.get("action") == "stand_down") or name == "stand_down"
        if is_stand_down and same_chat(ctx, tool.get("chat")):
            return "stand_down"
    return "tell_owner" if told else None


def _deliver_template(template: str, detail: str = "", why: str = "",
                      send: Optional[Dict[str, Any]] = None) -> Outcome:
    return Outcome(status="deliver", purpose="fallback", template=template, detail=detail, why=why or template,
                   send=send)


def _stopped_short(error: Optional[BaseException], stop: Dict[str, Any]) -> Optional[str]:
    """Why the turn did not end cleanly (an error class, ``deadline``,
    ``incomplete``), or None. Pure."""
    if error is not None:
        return type(error).__name__
    if stop.get("error_class"):
        return str(stop["error_class"])
    if stop.get("outcome") == "cap":
        return "LLMSpendCapReached"
    if stop.get("incomplete_reason"):
        return "incomplete"
    if stop.get("outcome") in GRACEFUL_CUTS:
        return str(stop["outcome"])
    return None


def _cap_outcome(error: Optional[BaseException], ctx: Dict[str, Any], *, why: str,
                 send: Optional[Dict[str, Any]] = None) -> Outcome:
    """The day's cap on a ping that must be answered."""
    if ctx.get("third_party"):
        return _deliver_template("spend_cap_friend", why=why, send=send)
    sentence = str(error).strip() if error is not None and str(error).strip() else CAP_SENTENCE_FALLBACK
    template = "spend_cap" if private_to_owner(ctx) else "spend_cap_group"
    return _deliver_template(template, detail=sentence, why=why, send=send)


def _after_progress(send: Dict[str, Any], cut: str, error: Optional[BaseException], stop: Dict[str, Any],
                    text: str, ctx: Dict[str, Any]) -> Outcome:
    """A send, then the turn stopped short: the send was progress ("On it"),
    not the answer. Never a retry (a second turn could answer twice); the
    chat hears how it ended."""
    why = f"sent in the turn, then stopped: {cut}"
    if is_cap(error, stop):
        return _cap_outcome(error, ctx, why=why, send=send)
    closing = tool_markup.clean_reply(text) if text else None
    if cut in GRACEFUL_CUTS and closing is not None and closing.meaningful:
        # AREA 2's graceful deadline or the turn's own budget: its closing
        # words are meant for the chat (never a tool call written in them).
        return Outcome(status="deliver", purpose="reply", text=closing.text, why=why, send=send)
    if is_timeout(error):
        return _deliver_template("unfinished_timeout", why=why, send=send)
    detail = "" if cut in GRACEFUL_CUTS + ("incomplete",) else cut
    return _deliver_template("unfinished", detail=detail, why=why, send=send)


def _tell_owner_then_reply(ctx: Dict[str, Any], text: str, error: Optional[BaseException],
                         stop: Dict[str, Any]) -> bool:
    """A ``tell_owner`` that is not a decline: the ping must be answered, the
    turn ended cleanly, and its final text says something ("I'm checking
    with <the owner>"). Pure."""
    if not ctx.get("must_answer") or not text or _stopped_short(error, stop) not in (None,) + GRACEFUL_CUTS:
        return False
    if text.endswith(refusal_note()):
        return False
    cleaned = tool_markup.clean_reply(text)
    return bool(cleaned.meaningful) if cleaned.found else True


def decide(ctx: Dict[str, Any], final_text: Optional[str], error: Optional[BaseException], *,
           attempts: int, is_transient: Optional[Callable[[BaseException], bool]] = None) -> Outcome:
    """What to do after one reply turn. Pure (``is_transient`` is injected;
    by default ``mind_service.is_transient_error``)."""
    stop = dict(ctx.get("stop") or {})
    must = bool(ctx.get("must_answer"))
    transient = is_transient or _default_is_transient
    text = str(final_text or "").strip()

    # An honest ending is a model failure, never an answer (rule 6).
    honest = honest_ending_class(text, stop)
    if honest is not None:
        stop.setdefault("error_class", honest)
        stop.setdefault("retryable", True)  # AREA 2's record may say otherwise

    # 1. Answered by the turn's own send, when the turn ended cleanly.
    send = answering_send(ctx)
    if send is not None:
        cut = _stopped_short(error, stop)
        if cut is None:
            return Outcome(status="answered", why="sent in the turn", send=send)
        landed = landed_after_cut(ctx)
        if landed is not None:
            # Path (d): the turn's last act, still inside osascript at the cut.
            return Outcome(status="answered", why=f"sent as the turn was cut ({cut})", send=landed)
        return _after_progress(send, cut, error, stop, text, ctx)

    # 2. The turn chose not to reply — but "I'm checking with <the owner>" after a
    # tell_owner is the reply a must-answer ping was promised.
    declined = declined_by_tool(ctx)
    if declined and not (declined == "tell_owner" and _tell_owner_then_reply(ctx, text, error, stop)):
        return Outcome(status="declined", why=declined)

    # 3. The day's spend cap: no retry.
    if is_cap(error, stop):
        if not must:
            return Outcome(status="declined", why="spend cap")
        return _cap_outcome(error, ctx, why="spend cap")

    error_class = type(error).__name__ if error is not None else stop.get("error_class")
    retryable = (error is not None and transient(error)) or bool(stop.get("retryable"))

    # 4. Transient: try again.
    if retryable and attempts < MAX_ATTEMPTS:
        return Outcome(status="retry", why=f"transient: {error_class or 'stopped'}")

    # 5. Our own bound ran out.
    if is_timeout(error):
        if not must:
            return Outcome(status="declined", why="timeout")
        return _deliver_template("timeout", why="timeout")

    # 6. Any other failure. An honest ending the loop wrote over a model
    # failure is never posted.
    if error is not None or stop.get("error_class"):
        if not must:
            return Outcome(status="declined", why=f"error: {error_class}")
        template = "provider" if retryable else "error"
        return _deliver_template(template, detail=str(error_class or "Error"), why=f"error: {error_class}")

    # 7. May-answer and silent: the turn chose.
    if not must:
        return Outcome(status="declined", why="may-answer, nothing sent")

    # 8. The adapter's refusal note or a max_tokens partial.
    if text and text.endswith(refusal_note()):
        return _deliver_template("refused", why="refused")
    if stop.get("incomplete_reason"):
        return _deliver_template("incomplete", why=f"incomplete: {stop.get('incomplete_reason')}")

    # 9. The final text is the reply — never a tool call written in it.
    if text:
        cleaned = tool_markup.clean_reply(text)
        if not cleaned.found:
            return Outcome(status="deliver", purpose="reply", text=text, why="final text")
        if not cleaned.meaningful:
            return _deliver_template("garbled", why="the final text was a tool call written as text")
        return Outcome(status="deliver", purpose="reply", text=cleaned.text,
                       why="final text (a tool call written in it was stripped)")

    # 10. Nothing at all.
    return _deliver_template("empty", why="empty")
