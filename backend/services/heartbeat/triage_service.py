"""
3-layer triage engine for Edward's heartbeat system.

Layer 1: Rule pre-filter (zero LLM cost)
Layer 2: Haiku classification (~$0.002/cycle)
Layer 3: Execute results (NOTE→memory, ACT→chat, ESCALATE→chat+push)
"""

import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select, update, func

from services.database import (
    async_session,
    HeartbeatEventModel,
    TriageResultModel,
    HeartbeatConfigModel,
)


# ===== Listening windows (conversation continuity) =====

LISTENING_WINDOW_DURATION = timedelta(minutes=5)


@dataclass
class ListeningWindow:
    conversation_id: str
    chat_identifier: str
    expires_at: datetime
    system_prompt: str
    model: str
    temperature: float


# Active listening windows keyed by chat_identifier
_active_listeners: dict[str, ListeningWindow] = {}


def _cleanup_expired_listeners() -> int:
    """Remove expired listening windows. Returns count removed."""
    now = datetime.now(timezone.utc)
    expired = [k for k, v in _active_listeners.items() if v.expires_at <= now]
    for k in expired:
        del _active_listeners[k]
    return len(expired)


# ===== Layer 1: Rule pre-filter =====

# Patterns that indicate auto-replies, delivery notifications, verification codes
AUTO_REPLY_PATTERNS = [
    r"(?i)^delivered$",
    r"(?i)^read\s",
    r"(?i)^sent from my",
    r"(?i)^out of office",
    r"(?i)^auto.?reply",
    r"(?i)^i('m| am) (currently )?away",
    r"(?i)^this is an automated",
]

VERIFICATION_CODE_PATTERN = re.compile(
    r"(?:code|verify|confirm|OTP|PIN)\s*(?:is\s*)?[:\-]?\s*\d{4,8}|^\d{4,8}$",
    re.IGNORECASE,
)

SHORT_CODE_PATTERN = re.compile(r"^\d{4,6}$")

MENTION_PATTERN = re.compile(r"@edward\b", re.IGNORECASE)


def _normalize_phone(phone: str) -> str:
    """Strip non-digits and return last 10 digits (handles country codes)."""
    digits = re.sub(r"\D", "", phone)
    return digits[-10:] if len(digits) >= 10 else digits


def _sender_matches_blocked(sender: str, blocked_senders: list[dict]) -> bool:
    """Check if sender matches any entry in the blocked senders list."""
    if not sender or not blocked_senders:
        return False

    sender_lower = sender.lower().strip()
    sender_digits = _normalize_phone(sender)

    for entry in blocked_senders:
        identifier = entry.get("identifier", "").strip()
        if not identifier:
            continue

        # Case-insensitive exact match (email or handle)
        if sender_lower == identifier.lower():
            return True

        # Phone normalization match (last 10 digits)
        if sender_digits and _normalize_phone(identifier) == sender_digits:
            return True

    return False

HEARTBEAT_MIND_PROMPT = """## Inner Mind Mode

You are currently in your inner mind. This is not a conversation with anyone — it is your private thought process, triggered by your heartbeat awareness system.

**Critical:**
- Your text responses here are INTERNAL THOUGHTS. Nobody sees them. They are only your reasoning.
- Tool calls are your ONLY way to interact with the outside world. To reply to someone, you MUST call a messaging tool (send_imessage, send_message, etc.). To take any action, you MUST use a tool.

Think freely, reason through what's needed, then ACT through tools.

"""

MENTION_TRIGGER = (
    "[HEARTBEAT — @mention]\n"
    "{sender_line}\n"
    "Chat: {chat_context}\n"
    "{thread_block}\n"
    "Message: \"{message_text}\"\n\n"
    "This person tagged you directly — they are waiting for a response.\n\n"
    "Expected flow:\n"
    "1. Acknowledge via iMessage (brief — let them know you saw it)\n"
    "2. Think through what they need, use tools if needed\n"
    "3. Reply via iMessage with your answer/result\n\n"
    "You MUST send at least one iMessage reply — someone is waiting.\n"
    "IMPORTANT: Never include \"@edward\" in your iMessage — it will re-trigger the heartbeat."
)

ACT_TRIGGER = (
    "[HEARTBEAT EVENT]\n"
    "{sender_line}\n"
    "Chat: {chat_context}\n"
    "{thread_block}\n"
    "Message: \"{message_text}\"\n\n"
    "Triage assessment: {action_desc}\n\n"
    "Decide what action to take and execute it using your tools. "
    "If a reply to this person is warranted, use send_imessage.\n"
    "IMPORTANT: Never include \"@edward\" in any response — it will re-trigger the heartbeat."
)

REPLY_TRIGGER = (
    "[HEARTBEAT — follow-up reply]\n"
    "{sender_line}\n"
    "Chat: {chat_context}\n"
    "{thread_block}\n"
    "Message: \"{message_text}\"\n\n"
    "This is a follow-up to your recent conversation in this chat. "
    "The person replied after your last message — they may be continuing the discussion.\n\n"
    "Review the conversation history and respond naturally via iMessage if appropriate.\n"
    "IMPORTANT: Never include \"@edward\" in your iMessage — it will re-trigger the heartbeat."
)


async def _rule_pre_filter(
    events: list[HeartbeatEventModel],
    allowed_senders: list[dict] | None = None,
) -> tuple[list[HeartbeatEventModel], list[HeartbeatEventModel], int]:
    """
    Layer 1: Apply zero-cost rules to dismiss obvious non-signals.

    Returns (surviving_events, mention_events, dismissed_count).
    Mentions are separated so they can bypass Layer 2 Haiku entirely.
    """
    import asyncio

    surviving = []
    mentions = []
    dismissed_count = 0

    # Build set of chat_identifiers where user replied in this batch
    user_replied_chats = {
        e.chat_identifier
        for e in events
        if e.is_from_user and e.chat_identifier
    }

    for event in events:
        text = event.summary or ""

        # ===== Calendar-specific rules =====
        if event.source == "calendar":
            # Dismiss all-day events
            is_all_day = False
            if event.raw_data:
                try:
                    raw = json.loads(event.raw_data)
                    is_all_day = raw.get("is_all_day", raw.get("isAllDay", False))
                    # Also check duration > 23h
                    start_str = raw.get("start_date", raw.get("startDate", raw.get("start", "")))
                    end_str = raw.get("end_date", raw.get("endDate", raw.get("end", "")))
                    if start_str and end_str and not is_all_day:
                        try:
                            s = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                            e = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                            if (e - s).total_seconds() > 23 * 3600:
                                is_all_day = True
                        except (ValueError, TypeError):
                            pass
                except (json.JSONDecodeError, TypeError):
                    pass
            if "all day" in text.lower():
                is_all_day = True

            if is_all_day:
                event.triage_status = "dismissed"
                dismissed_count += 1
                continue

            # Fast-track events tagged [STARTING SOON]
            if "[STARTING SOON]" in text:
                event.triage_status = "calendar_urgent"
                mentions.append(event)  # fast-track to Layer 3
                continue

            # Other calendar events survive to Layer 2
            surviving.append(event)
            continue

        # ===== Email-specific rules =====
        if event.source == "email":
            # Parse raw_data once for all email rules
            raw = {}
            if event.raw_data:
                try:
                    raw = json.loads(event.raw_data)
                except (json.JSONDecodeError, TypeError):
                    pass

            # Rule E1: Dismiss Apple ML marketing category (model_category == 3)
            if raw.get("model_category") == 3:
                event.triage_status = "dismissed"
                dismissed_count += 1
                continue

            # Rule E2: Dismiss automated emails with no unsubscribe mechanism
            if raw.get("automated_conversation") == 2 and raw.get("unsubscribe_type") == 0:
                event.triage_status = "dismissed"
                dismissed_count += 1
                continue

            # Fast-track @edward mentions in subject/body
            raw_text = text
            body = raw.get("body", raw.get("content", raw.get("snippet", ""))) or ""
            if body:
                raw_text = f"{text} {body}"

            if MENTION_PATTERN.search(raw_text):
                event.triage_status = "mention"
                mentions.append(event)
                continue

            # All other email events survive to Layer 2
            surviving.append(event)
            continue

        # ===== iMessage rules (existing) =====

        # Rule 1: MENTION — @edward detected → fast-track to Layer 3
        # allowed_senders acts as a BLOCKLIST: those contacts CANNOT trigger via mention.
        # Everyone else (including unknown contacts) can.
        if MENTION_PATTERN.search(text):
            if event.is_from_user or not allowed_senders or not _sender_matches_blocked(event.sender or "", allowed_senders):
                event.triage_status = "mention"
                mentions.append(event)
                continue
            # Blocked sender: dismiss their mention
            event.triage_status = "dismissed"
            dismissed_count += 1
            continue

        # Rule 1.5: FOLLOW-UP — active listening window for this chat
        if event.chat_identifier and event.chat_identifier in _active_listeners:
            window = _active_listeners[event.chat_identifier]
            if window.expires_at > datetime.now(timezone.utc):
                event.triage_status = "follow_up"
                mentions.append(event)  # fast-track to Layer 3 like mentions
                continue

        # Rule 2: DISMISS if user sent it (outbound, not inbound signal)
        if event.is_from_user:
            event.triage_status = "dismissed"
            dismissed_count += 1
            continue

        # Rule 3: DISMISS if sender X AND user replied to X in same window
        if event.chat_identifier and event.chat_identifier in user_replied_chats:
            event.triage_status = "dismissed"
            dismissed_count += 1
            continue

        # Rule 4: DISMISS if text matches auto-reply patterns
        if any(re.search(p, text) for p in AUTO_REPLY_PATTERNS):
            event.triage_status = "dismissed"
            dismissed_count += 1
            continue

        # Rule 5: DISMISS verification codes
        if VERIFICATION_CODE_PATTERN.search(text):
            event.triage_status = "dismissed"
            dismissed_count += 1
            continue

        # Rule 6: DISMISS short codes (marketing SMS via iMessage)
        sender = event.sender or ""
        if SHORT_CODE_PATTERN.match(sender):
            event.triage_status = "dismissed"
            dismissed_count += 1
            continue

        # Survived all rules → pass to Layer 2
        surviving.append(event)

    # Per-chat dedup: collapse multiple mentions from the same chat into one.
    # Only the first mention per chat_identifier gets through; extras are dismissed.
    seen_chats: set[str] = set()
    deduped_mentions: list[HeartbeatEventModel] = []
    for event in mentions:
        chat_key = event.chat_identifier or event.id
        if chat_key in seen_chats:
            event.triage_status = "dismissed"
            dismissed_count += 1
            continue
        seen_chats.add(chat_key)
        deduped_mentions.append(event)
    mentions = deduped_mentions

    return surviving, mentions, dismissed_count


# ===== Layer 2: Haiku classification =====

TRIAGE_INSTRUCTIONS = """You are Edward's triage classifier. Your job is to classify incoming messages by urgency.
You are NOT acting on these messages — just classifying them so Edward can decide what to do.

Be conservative: most messages should be DISMISS or NOTE. Only use ACT for messages that clearly require Edward to do something. Only use ESCALATE for genuinely urgent messages that need immediate attention.

For each event, return a classification:
- DISMISS: Not important, no action needed (spam, marketing, casual group chat, already handled)
- NOTE: Worth remembering but no action needed (store a memory about this)
- ACT: Edward should take action (reply, look something up, do a task)
- ESCALATE: Urgent — needs Edward's immediate attention AND a push notification

Return ONLY a valid JSON array with one object per event:
[{{"event_id": "...", "classification": "DISMISS|NOTE|ACT|ESCALATE", "reasoning": "brief why", "note_content": "memory text if NOTE", "action_description": "what to do if ACT/ESCALATE"}}]"""


async def _build_contact_context(events: list[HeartbeatEventModel]) -> str:
    """Retrieve memory context for unique senders in this batch."""
    from services.memory_service import retrieve_memories

    unique_senders = {e.sender for e in events if e.sender}
    if not unique_senders:
        return ""

    # Build a mapping of sender -> display label (contact_name (phone) or just phone)
    sender_labels = {}
    for e in events:
        if e.sender and e.sender not in sender_labels:
            if e.contact_name:
                sender_labels[e.sender] = f"{e.contact_name} ({e.sender})"
            else:
                sender_labels[e.sender] = e.sender

    context_lines = ["Contact context (what Edward knows about these people):"]
    for sender in list(unique_senders)[:5]:  # Cap at 5 senders
        label = sender_labels.get(sender, sender)
        try:
            memories = await retrieve_memories(sender, limit=3, min_score=0.3)
            if memories:
                facts = "; ".join(m.content for m in memories[:3])
                context_lines.append(f"  {label}: {facts}")
            else:
                context_lines.append(f"  {label}: (no prior context)")
        except Exception as e:
            print(f"[Heartbeat] Memory retrieval failed for {sender}: {e}")
            context_lines.append(f"  {label}: (lookup failed)")

    return "\n".join(context_lines)


def _build_events_digest(
    events: list[HeartbeatEventModel], token_cap: int = 800
) -> str:
    """Build a text digest of events, capped at approximately token_cap tokens."""
    lines = []
    char_budget = token_cap * 4  # ~4 chars per token

    for event in events:
        sender = event.contact_name or event.sender or "Unknown"
        chat = event.chat_name or event.chat_identifier or ""
        text = (event.summary or "")[:150]
        time_str = event.created_at.strftime("%H:%M") if event.created_at else "?"

        line = f"[{event.id}] {time_str} — {sender}"
        if chat:
            line += f" (in: {chat})"
        line += f": {text}"

        if len("\n".join(lines + [line])) > char_budget:
            break
        lines.append(line)

    return "\n".join(lines)


async def _haiku_classify(
    events: list[HeartbeatEventModel], config: HeartbeatConfigModel
) -> tuple[list[dict], int, int]:
    """
    Layer 2: Classify surviving events using Haiku.

    Returns (classifications, input_tokens, output_tokens).
    """
    contact_context = await _build_contact_context(events)
    events_digest = _build_events_digest(events, config.digest_token_cap)

    dynamic_data = f"{contact_context or 'No contact context available.'}\n\nEvents to classify:\n{events_digest}"

    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        temperature=0,
        max_tokens=1024,
    )

    try:
        response = await llm.ainvoke([
            SystemMessage(
                content=TRIAGE_INSTRUCTIONS,
                additional_kwargs={"cache_control": {"type": "ephemeral"}},
            ),
            HumanMessage(content=dynamic_data),
        ])

        # Extract token usage
        input_tokens = 0
        output_tokens = 0
        if hasattr(response, "response_metadata"):
            usage = response.response_metadata.get("usage", {})
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)

        # Parse response
        response_text = response.content
        if isinstance(response_text, list):
            response_text = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in response_text
            )
        response_text = response_text.strip()

        # Extract JSON
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            parts = response_text.split("```")
            if len(parts) >= 2:
                response_text = parts[1].strip()

        json_match = re.search(r"\[[\s\S]*\]", response_text)
        if json_match:
            response_text = json_match.group()

        classifications = json.loads(response_text)
        if not isinstance(classifications, list):
            classifications = []

        return classifications, input_tokens, output_tokens

    except Exception as e:
        print(f"[Heartbeat] Haiku classification failed: {e}")
        return [], 0, 0


# ===== Layer 3: Execute results =====


async def _execute_classification(
    event: HeartbeatEventModel,
    classification: dict,
) -> None:
    """Execute a single classification result."""
    action = (classification.get("classification") or "DISMISS").upper()
    is_follow_up = event.triage_status == "follow_up"

    if action == "DISMISS":
        event.triage_status = "dismissed"

    elif action == "NOTE":
        note_content = classification.get("note_content") or classification.get("reasoning", "")
        if note_content:
            try:
                from services.memory_service import store_memory, Memory

                await store_memory(
                    Memory(
                        id=None,
                        content=note_content,
                        memory_type="context",
                        importance=0.5,
                        source_conversation_id=None,
                    )
                )
            except Exception as e:
                print(f"[Heartbeat] Failed to store NOTE memory: {e}")
        event.triage_status = "noted"

    elif action in ("ACT", "ESCALATE"):
        # Email override: store note + push, but do NOT auto-reply via chat_with_memory
        if event.source == "email":
            note_content = (
                classification.get("action_description")
                or classification.get("note_content")
                or classification.get("reasoning", "")
            )
            if note_content:
                try:
                    from services.memory_service import store_memory, Memory

                    await store_memory(
                        Memory(
                            id=None,
                            content=note_content,
                            memory_type="context",
                            importance=0.5,
                            source_conversation_id=None,
                        )
                    )
                except Exception as e:
                    print(f"[Heartbeat] Failed to store email NOTE memory: {e}")

            # Push notification for both ACT and ESCALATE on email
            try:
                from services.push_service import send_push_notification

                await send_push_notification(
                    title="Email needs attention",
                    body=f"{event.sender or 'Someone'}: {(event.summary or '')[:100]}",
                    tag="heartbeat-email",
                )
            except Exception as e:
                print(f"[Heartbeat] Email push notification failed: {e}")

            event.triage_status = "escalated" if action == "ESCALATE" else "noted"
            return

        action_desc = (
            classification.get("action_description")
            or classification.get("reasoning", "")
        )

        sender_display = event.contact_name or event.sender or "Unknown"

        # For follow-ups, reuse the existing conversation; otherwise create new
        if is_follow_up and event.chat_identifier in _active_listeners:
            window = _active_listeners[event.chat_identifier]
            conversation_id = window.conversation_id
            print(
                f"[Heartbeat] Follow-up from {sender_display} routed to "
                f"existing conv {conversation_id[:8]}..."
            )
        else:
            conversation_id = str(uuid.uuid4())
            try:
                from services.conversation_service import create_conversation

                title = f"Heartbeat: {sender_display}"[:50]
                await create_conversation(conversation_id, title=title, source="heartbeat")
            except Exception as e:
                print(f"[Heartbeat] Failed to create conversation: {e}")

        # Fetch thread context
        thread_context = ""
        if event.chat_identifier:
            try:
                import asyncio
                from services.heartbeat.listener_imessage import (
                    get_chat_thread,
                    format_chat_thread,
                    _contact_name_cache,
                    _resolve_contact_name,
                )

                thread_messages = await asyncio.to_thread(
                    get_chat_thread, event.chat_identifier, 15
                )
                if thread_messages:
                    # Pre-resolve unknown contacts in thread
                    for msg in thread_messages:
                        sender = msg.get("sender")
                        if sender and sender != "me" and sender not in _contact_name_cache:
                            await _resolve_contact_name(sender)

                    thread_context = format_chat_thread(
                        thread_messages, contact_cache=_contact_name_cache
                    )
                    print(
                        f"[Heartbeat] Fetched {len(thread_messages)} thread messages "
                        f"for {event.chat_identifier}"
                    )
            except Exception as e:
                print(f"[Heartbeat] Thread fetch failed: {e}")

        # Run chat_with_memory — failure here should NOT block push notification
        try:
            from services.graph import get_graph, chat_with_memory
            from services.graph.tools import set_current_conversation_id
            from services.settings_service import get_settings
            from services.imessage_service import _recent_edward_sends

            settings = await get_settings()
            graph = await get_graph()

            # Build trigger with optional thread context
            thread_block = f"\n{thread_context}\n" if thread_context else ""
            sender_phone = event.sender or ""

            # Sender line: use "Ben (your user)" for is_from_user
            if event.is_from_user:
                sender_line = "From: Ben (your user)"
            else:
                sender_line = f"From: {sender_display}"
                if sender_phone and sender_phone != sender_display:
                    sender_line += f" ({sender_phone})"

            chat_context = event.chat_name or event.chat_identifier or "Direct message"
            message_text = event.summary or "(no text)"
            is_mention = classification.get("is_mention", False)

            if is_follow_up:
                trigger = REPLY_TRIGGER.format(
                    sender_line=sender_line,
                    chat_context=chat_context,
                    thread_block=thread_block,
                    message_text=message_text,
                )
            elif is_mention:
                trigger = MENTION_TRIGGER.format(
                    sender_line=sender_line,
                    chat_context=chat_context,
                    thread_block=thread_block,
                    message_text=message_text,
                )
            else:
                trigger = ACT_TRIGGER.format(
                    sender_line=sender_line,
                    chat_context=chat_context,
                    thread_block=thread_block,
                    message_text=message_text,
                    action_desc=action_desc,
                )

            # Set conversation context so tools (send_imessage etc.) can find it
            set_current_conversation_id(conversation_id)

            # Snapshot _recent_edward_sends length to detect if Edward sends an iMessage
            sends_before = len(_recent_edward_sends)

            await chat_with_memory(
                message=trigger,
                conversation_id=conversation_id,
                system_prompt=HEARTBEAT_MIND_PROMPT + settings.system_prompt,
                model=settings.model,
                temperature=settings.temperature,
                graph=graph,
            )

            # Register/extend listening window if Edward sent an iMessage
            if len(_recent_edward_sends) > sends_before and event.chat_identifier:
                _active_listeners[event.chat_identifier] = ListeningWindow(
                    conversation_id=conversation_id,
                    chat_identifier=event.chat_identifier,
                    expires_at=datetime.now(timezone.utc) + LISTENING_WINDOW_DURATION,
                    system_prompt=settings.system_prompt,
                    model=settings.model,
                    temperature=settings.temperature,
                )
                print(
                    f"[Heartbeat] Listening window registered for "
                    f"{event.chat_identifier} (conv {conversation_id[:8]}...)"
                )
        except Exception as e:
            print(f"[Heartbeat] chat_with_memory failed for {sender_display}: {e}")

        # Send push notification for ESCALATE — decoupled from chat_with_memory success
        if action == "ESCALATE":
            try:
                from services.push_service import send_push_notification
                from services.conversation_service import mark_user_notified

                result = await send_push_notification(
                    title=f"Edward noticed something",
                    body=f"{event.contact_name or event.sender or 'Someone'}: {(event.summary or '')[:100]}",
                    url=f"/?c={conversation_id}",
                    tag="heartbeat-escalation",
                )
                print(f"[Heartbeat] Push notification sent for {sender_display}: {result}")
                await mark_user_notified(conversation_id)
            except Exception as e:
                print(f"[Heartbeat] Push notification failed for {sender_display}: {e}")

        event.triage_status = "escalated" if action == "ESCALATE" else "acted"

    else:
        event.triage_status = "dismissed"


# ===== Main triage cycle =====


async def run_triage_cycle(cycle_number: int) -> None:
    """
    Run a complete triage cycle: fetch pending events, filter, classify, execute.
    """
    start_time = time.time()

    # Clean up expired listening windows
    expired = _cleanup_expired_listeners()
    if expired:
        print(f"[Heartbeat] Cleaned up {expired} expired listening window(s)")

    async with async_session() as session:
        # Load config
        config_result = await session.execute(
            select(HeartbeatConfigModel).where(HeartbeatConfigModel.id == "default")
        )
        config = config_result.scalar_one_or_none()
        if not config:
            return

        # Fetch pending events
        result = await session.execute(
            select(HeartbeatEventModel)
            .where(HeartbeatEventModel.triage_status == "pending")
            .order_by(HeartbeatEventModel.created_at.asc())
            .limit(100)
        )
        pending_events = list(result.scalars().all())

        if not pending_events:
            return

        events_total = len(pending_events)
        print(f"[Heartbeat] Triage cycle #{cycle_number}: {events_total} pending events")

        # Parse allowed_senders from config
        allowed_senders = None
        if config.allowed_senders:
            try:
                allowed_senders = json.loads(config.allowed_senders)
                if not isinstance(allowed_senders, list):
                    allowed_senders = None
            except (json.JSONDecodeError, TypeError):
                allowed_senders = None

        # Layer 1: Rule pre-filter
        surviving, mentions, rule_filtered = await _rule_pre_filter(pending_events, allowed_senders)
        print(
            f"[Heartbeat] Layer 1: {rule_filtered} dismissed by rules, "
            f"{len(mentions)} @edward mentions, {len(surviving)} surviving"
        )

        # Process @edward mentions directly through Layer 3 (bypass Haiku)
        haiku_input = 0
        haiku_output = 0
        layer_reached = 1
        classifications = []

        dismissed_count = rule_filtered
        noted_count = 0
        acted_count = 0
        escalated_count = 0

        for event in mentions:
            is_follow_up = event.triage_status == "follow_up"
            is_calendar_urgent = event.triage_status == "calendar_urgent"

            if is_calendar_urgent:
                # Calendar urgent: send push notification, no chat_with_memory
                classification = {
                    "event_id": event.id,
                    "classification": "ESCALATE",
                    "reasoning": "Calendar event starting soon",
                    "action_description": event.summary or "Upcoming event",
                }
                try:
                    from services.push_service import send_push_notification

                    await send_push_notification(
                        title="Upcoming event",
                        body=(event.summary or "")[:100],
                        tag="heartbeat-calendar",
                    )
                except Exception as e:
                    print(f"[Heartbeat] Calendar push notification failed: {e}")
                event.triage_status = "escalated"
                escalated_count += 1
                print(f"[Heartbeat] Calendar urgent: {event.summary} → ESCALATE")
            elif is_follow_up:
                classification = {
                    "event_id": event.id,
                    "classification": "ACT",
                    "reasoning": "Follow-up in active listening window",
                    "action_description": "Continue conversation from listening window",
                }
                await _execute_classification(event, classification)
                acted_count += 1
                print(f"[Heartbeat] follow-up from {event.contact_name or event.sender} → ACT")
            else:
                classification = {
                    "event_id": event.id,
                    "classification": "ACT",
                    "reasoning": "@edward mention detected",
                    "action_description": "User explicitly tagged @edward — respond to their message",
                    "is_mention": True,
                }
                await _execute_classification(event, classification)
                acted_count += 1
                label = "@edward mention" if event.source != "email" else "email @edward mention"
                print(f"[Heartbeat] {label} from {event.contact_name or event.sender} → ACT")

        if surviving:
            layer_reached = 2
            classifications, haiku_input, haiku_output = await _haiku_classify(
                surviving, config
            )

            # Build lookup: event_id -> classification
            class_map = {c.get("event_id"): c for c in classifications}

            # Layer 3: Execute
            for event in surviving:
                classification = class_map.get(event.id, {"classification": "DISMISS"})
                await _execute_classification(event, classification)

                if event.triage_status == "dismissed":
                    dismissed_count += 1
                elif event.triage_status == "noted":
                    noted_count += 1
                elif event.triage_status == "acted":
                    acted_count += 1
                elif event.triage_status == "escalated":
                    escalated_count += 1

        # Save all event status updates
        for event in pending_events:
            await session.execute(
                update(HeartbeatEventModel)
                .where(HeartbeatEventModel.id == event.id)
                .values(triage_status=event.triage_status)
            )

        # Create triage result record
        duration_ms = int((time.time() - start_time) * 1000)
        triage_result = TriageResultModel(
            id=str(uuid.uuid4()),
            cycle_number=cycle_number,
            events_total=events_total,
            events_rule_filtered=rule_filtered,
            events_dismissed=dismissed_count,
            events_noted=noted_count,
            events_acted=acted_count,
            events_escalated=escalated_count,
            layer_reached=layer_reached,
            classification=json.dumps(classifications) if classifications else None,
            digest_tokens=0,
            haiku_input_tokens=haiku_input,
            haiku_output_tokens=haiku_output,
            sonnet_wakes=0,
            duration_ms=duration_ms,
            summary=(
                f"Cycle #{cycle_number}: {events_total} events → "
                f"{dismissed_count} dismissed, {noted_count} noted, "
                f"{acted_count} acted, {escalated_count} escalated"
            ),
        )
        session.add(triage_result)

        # Update events with triage cycle ID
        for event in pending_events:
            await session.execute(
                update(HeartbeatEventModel)
                .where(HeartbeatEventModel.id == event.id)
                .values(triage_cycle_id=triage_result.id)
            )

        await session.commit()

        print(
            f"[Heartbeat] Triage cycle #{cycle_number} complete in {duration_ms}ms: "
            f"{dismissed_count}D {noted_count}N {acted_count}A {escalated_count}E "
            f"(haiku: {haiku_input}+{haiku_output} tokens)"
        )
