"""Send-review: a second set of eyes on Edward's outbound heartbeat messages.

2026-07-07 incident: the required-tool nudge forced Edward to send filler
("Perfect 👌") to a close contact after he had correctly judged that no
reply was needed. Alongside removing the forced send on follow-ups, this
critic reviews outbound drafts on heartbeat turns — a non-deterministic
quality gate (a design decision: judgment, not tool muting).

Properties:
- Advisory and fail-open: any critic failure (LLM error, timeout, garbage
  output) lets the send proceed.
- Bounded: at most one rejection per respond turn — the model's revision
  goes through even if the critic would grumble again (no revise loops).
- Scoped: active only in a turn that enables it (contextvar), so
  user-driven chat sends are never second-guessed. The legacy triage
  engine's reply turns enabled it; the mind path's wakes do not, so today
  the review is inactive unless a caller turns it on for its turn.
"""

from __future__ import annotations

import asyncio
import contextvars

from services.owner import owner_text

REVIEW_TIMEOUT_SECONDS = 20
MAX_REJECTIONS_PER_TURN = 1

# None = review off (normal chat). A mutable dict when a heartbeat respond
# turn is active; the dict is shared into child tasks (asyncio copies the
# context but not the object), so rejection counts survive wait_for wrappers.
_heartbeat_send_review: contextvars.ContextVar = contextvars.ContextVar(
    "heartbeat_send_review", default=None
)


def enable_heartbeat_send_review() -> None:
    """Turn the review on for the current turn (call before it starts)."""
    _heartbeat_send_review.set({"rejections": 0})


SEND_REVIEW_SYSTEM = owner_text(
    "You are Edward's send-review — a second set of eyes on an outgoing "
    "iMessage that Edward ({OWNER}'s AI assistant) drafted during autonomous "
    "message handling. Edward's voice is dry, anchored, useful.\n\n"
    "REJECT a draft when it is:\n"
    "- social filler: emoji-only, empty acknowledgment ('Perfect 👌', "
    "'Sounds good!'), or a reply that adds nothing the thread needs\n"
    "- an answer to a question addressed to {OWNER} — a permission ('Can I…?'), "
    "preference, plan, or feeling; those are {OWNER}'s to answer, not Edward's\n"
    "- an assertion Edward can't ground in current knowledge, or that "
    "mind-reads {OWNER} ('{OWNER} probably thinks…')\n"
    "- piling on: when Edward has already sent 3+ messages in this chat in "
    "the last hour, reject anything not clearly necessary — let the humans talk\n"
    "- speaking as if Edward IS {OWNER} — claiming {OWNER}'s actions, decisions, or "
    "feelings as Edward's own\n"
    "- a commitment of {OWNER}'s time, money, or physical presence — those are "
    "{OWNER}'s to promise, not Edward's\n"
    "- gushing, apologetic, or chirpy in a way {OWNER}'s dry assistant wouldn't be\n\n"
    "APPROVE anything genuinely useful: answers, information, coordination "
    "Edward can own, warm-but-brief where the thread calls for it.\n\n"
    'Reply with ONLY JSON: {"approved": true|false, "reason": "one short sentence"}'
)


def _default_build_llm(**kwargs):
    from services.llm_utils import build_background_llm

    return build_background_llm(**kwargs)


async def review_outgoing_message(draft: str, recipient: str, _build_llm=None) -> dict:
    """Review an outbound draft. Returns {"approved": bool, "reason": str}.

    Only active inside a turn that enabled it; fail-open everywhere else
    and on any critic failure.
    """
    state = _heartbeat_send_review.get()
    if state is None:
        return {"approved": True, "reason": "review inactive"}
    if state["rejections"] >= MAX_REJECTIONS_PER_TURN:
        return {"approved": True, "reason": "revision accepted (rejection budget spent)"}

    build_llm = _build_llm or _default_build_llm
    try:
        from langchain_core.messages import HumanMessage

        from services.json_parsing import extract_json_payload
        from services.llm_utils import make_background_system_message

        # Presence context: how much Edward has already said in this chat
        # (fail-open — a count failure must never block review or sending).
        try:
            from services.heartbeat import triage_service as _triage

            recent_sends = await _triage._count_recent_sends(recipient)
        except Exception:
            recent_sends = 0

        llm = build_llm(max_tokens=200, priority="triage")
        response = await asyncio.wait_for(
            llm.ainvoke([
                make_background_system_message(SEND_REVIEW_SYSTEM),
                HumanMessage(content=(
                    f"To: {recipient}\n"
                    f"Edward has already sent {recent_sends} message(s) in this "
                    "chat in the last hour.\n"
                    f"Draft:\n{draft}"
                )),
            ]),
            timeout=REVIEW_TIMEOUT_SECONDS,
        )
        parsed = extract_json_payload(
            getattr(response, "content", "") or "", expected="object",
            subsystem="send_review",
        )
    except Exception as exc:
        print(f"[SEND REVIEW] unavailable ({exc}) — failing open")
        return {"approved": True, "reason": "review unavailable — fail open"}

    if not isinstance(parsed, dict) or "approved" not in parsed:
        return {"approved": True, "reason": "review output unparseable — fail open"}

    approved = bool(parsed.get("approved"))
    reason = str(parsed.get("reason") or "").strip() or "no reason given"
    if not approved:
        state["rejections"] += 1
        print(f"[SEND REVIEW] rejected draft to {recipient}: {reason}")
    return {"approved": approved, "reason": reason}
