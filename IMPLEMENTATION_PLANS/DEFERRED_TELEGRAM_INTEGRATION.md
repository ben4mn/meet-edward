# Deferred: Telegram Integration

## Status: DEFERRED

This plan is deferred. PWA with push notifications covers the primary use case. Telegram can be revisited if push notifications prove unreliable or if outbound messaging to non-Edward users is needed.

---

## Original Plan

This plan adds Telegram as a messaging channel for Edward. It follows the exact pattern established by Twilio SMS/WhatsApp — same architecture, same conventions, same data flow. Uses long-polling (no public URL needed).

**Dependencies**: Plan 001 (Cross-Platform Foundation) completed and tested
**Estimated effort**: 2-3 days

---

## Context & Rationale

Edward currently supports messaging via:
- **Twilio SMS** — outbound/inbound via webhooks
- **Twilio WhatsApp** — outbound/inbound via webhooks
- **iMessage** — AppleScript send + heartbeat listener (macOS only)

On Windows, none of these work without additional setup (Twilio needs webhook URL, iMessage needs macOS). Telegram provides:
- Free bot API (no per-message cost)
- Works on any OS
- Long-polling mode (no public URL needed for local dev)
- Rich message support (4096 chars, inline keyboards for future use)
- 30-second bot creation via @BotFather

### Inbound Flow (New)
```
Telegram Cloud → getUpdates (long-poll) → telegram_service.py
    → access control check (TELEGRAM_ALLOWED_USER_ID)
    → get_or_create_telegram_conversation()
    → chat_with_memory() [same as Twilio flow]
    → send_telegram() response back
```

### Outbound Flow (New)
```
Scheduled event / LLM tool call
    → send_telegram(chat_id, message)
    → Telegram Bot API → user's phone
```

---

## Strict Rules

### MUST DO
- [ ] Follow the EXACT pattern of `twilio_service.py` for service structure
- [ ] Follow the EXACT pattern of `webhooks.py` for contact/conversation management
- [ ] Register in skills_service.py, tool_registry.py, and main.py (same as all other skills)
- [ ] Add access control via `TELEGRAM_ALLOWED_USER_ID` env var
- [ ] Add `python-telegram-bot>=21.0` to requirements.txt
- [ ] Add DB migration for `external_id` column in `init_db()`
- [ ] Test with a real Telegram bot token

### MUST NOT DO
- [ ] Do NOT modify existing Twilio code paths
- [ ] Do NOT change the `phone_number` column behavior for existing contacts
- [ ] Do NOT use webhooks (use long-polling only for this plan)
- [ ] Do NOT add inline keyboards or rich Telegram features (basic text first)
- [ ] Do NOT add Telegram-specific UI to the frontend (conversations appear naturally in sidebar)

---

## Phase 1: Database Schema Update

### Step 1.1: Modify ExternalContactModel

**File**: `backend/services/database.py`

Add `external_id` column and make `phone_number` nullable:

```python
class ExternalContactModel(Base):
    """External contacts for SMS/messaging/Telegram conversations."""
    __tablename__ = "external_contacts"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    phone_number = Column(String, unique=True, nullable=True, index=True)  # Changed: nullable=True
    external_id = Column(String, nullable=True, index=True)  # NEW: Telegram chat_id, Discord user_id, etc.
    conversation_id = Column(String, nullable=False)
    contact_name = Column(String, nullable=True)
    platform = Column(String, default="sms")  # sms, whatsapp, telegram, etc.
    last_channel = Column(String, default="sms")  # sms, whatsapp, telegram
    created_at = Column(DateTime, server_default=func.now())
    last_contacted = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index('ix_external_contacts_ext_platform', 'external_id', 'platform', unique=True),
    )
```

### Step 1.2: Add DB migration in init_db()

**File**: `backend/services/database.py` — inside `init_db()` function

After `Base.metadata.create_all()`, add:
```python
# Migration: add external_id column to external_contacts (for Telegram/Discord)
async with engine.begin() as conn:
    await conn.execute(text(
        "ALTER TABLE external_contacts ADD COLUMN IF NOT EXISTS external_id VARCHAR"
    ))
    await conn.execute(text(
        "ALTER TABLE external_contacts ALTER COLUMN phone_number DROP NOT NULL"
    ))
    # Create unique index on (external_id, platform) if not exists
    await conn.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_external_contacts_ext_platform
        ON external_contacts (external_id, platform)
        WHERE external_id IS NOT NULL
    """))
```

---

## Phase 2: Telegram Service

### Step 2.1: Create `backend/services/telegram_service.py`

New file. Structure mirrors `twilio_service.py`:

```python
"""
Telegram bot service for Edward.

Uses long-polling (getUpdates) to receive messages.
Access restricted to TELEGRAM_ALLOWED_USER_ID.
"""

import os
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ALLOWED_USER_ID = os.getenv("TELEGRAM_ALLOWED_USER_ID")

# Telegram API base URL
TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/{method}"

# State
_polling_task: Optional[asyncio.Task] = None
_last_update_id: int = 0
_shutdown_event: Optional[asyncio.Event] = None


def is_configured() -> bool:
    """Check if Telegram bot token is set."""
    return bool(TELEGRAM_BOT_TOKEN)


def is_allowed_user(user_id: int) -> bool:
    """Check if a Telegram user is authorized to use this bot."""
    if not TELEGRAM_ALLOWED_USER_ID:
        return True  # No restriction if env var not set
    return str(user_id) == str(TELEGRAM_ALLOWED_USER_ID)


async def send_telegram(chat_id: str, message: str) -> dict:
    """Send a message via Telegram Bot API.

    Args:
        chat_id: Telegram chat ID to send to
        message: Message text (max 4096 chars)

    Returns:
        dict with status and message_id
    """
    import httpx

    if not is_configured():
        return {"status": "error", "error": "Telegram not configured"}

    # Truncate to Telegram's limit
    if len(message) > 4096:
        message = message[:4093] + "..."

    url = TELEGRAM_API_URL.format(token=TELEGRAM_BOT_TOKEN, method="sendMessage")

    async with httpx.AsyncClient() as client:
        response = await client.post(url, json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown",  # Support basic formatting
        })

        if response.status_code == 200:
            data = response.json()
            return {
                "status": "sent",
                "message_id": data.get("result", {}).get("message_id"),
            }
        else:
            # Retry without Markdown if parse fails
            if "can't parse" in response.text.lower():
                response = await client.post(url, json={
                    "chat_id": chat_id,
                    "text": message,
                })
                if response.status_code == 200:
                    data = response.json()
                    return {
                        "status": "sent",
                        "message_id": data.get("result", {}).get("message_id"),
                    }

            return {"status": "error", "error": response.text}


async def start_polling() -> None:
    """Start the background long-polling loop."""
    global _polling_task, _shutdown_event

    if not is_configured():
        logger.info("Telegram not configured (TELEGRAM_BOT_TOKEN not set), skipping")
        return

    _shutdown_event = asyncio.Event()
    _polling_task = asyncio.create_task(_poll_loop())
    logger.info("Telegram polling started")


async def stop_polling() -> None:
    """Stop the polling loop gracefully."""
    global _polling_task, _shutdown_event

    if _shutdown_event:
        _shutdown_event.set()

    if _polling_task:
        _polling_task.cancel()
        try:
            await _polling_task
        except asyncio.CancelledError:
            pass
        _polling_task = None

    logger.info("Telegram polling stopped")


async def _poll_loop() -> None:
    """Background loop: getUpdates → process each message → respond."""
    global _last_update_id
    import httpx

    url = TELEGRAM_API_URL.format(token=TELEGRAM_BOT_TOKEN, method="getUpdates")

    while not _shutdown_event.is_set():
        try:
            async with httpx.AsyncClient() as client:
                params = {
                    "offset": _last_update_id + 1,
                    "timeout": 30,  # Long-poll timeout (seconds)
                    "allowed_updates": ["message"],
                }
                response = await client.get(url, params=params, timeout=35)

                if response.status_code != 200:
                    logger.error(f"Telegram getUpdates error: {response.status_code}")
                    await asyncio.sleep(5)
                    continue

                data = response.json()
                if not data.get("ok"):
                    logger.error(f"Telegram API error: {data}")
                    await asyncio.sleep(5)
                    continue

                for update in data.get("result", []):
                    _last_update_id = update["update_id"]
                    await _process_update(update)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Telegram polling error: {e}")
            await asyncio.sleep(5)


async def _process_update(update: dict) -> None:
    """Process a single Telegram update."""
    message = update.get("message")
    if not message:
        return

    # Extract message data
    chat_id = str(message["chat"]["id"])
    user_id = message.get("from", {}).get("id")
    text = message.get("text", "")
    from_name = message.get("from", {}).get("first_name", "Telegram User")
    username = message.get("from", {}).get("username", from_name)

    if not text:
        return  # Ignore non-text messages for now

    # Access control
    if not is_allowed_user(user_id):
        await send_telegram(chat_id, "This is a private bot. Access denied.")
        logger.info(f"Rejected unauthorized Telegram user: {user_id}")
        return

    # Process message (same flow as Twilio inbound)
    try:
        from routers.webhooks import (
            get_or_create_telegram_conversation,
            process_telegram_inbound_and_respond,
        )

        conversation_id, is_new = await get_or_create_telegram_conversation(
            chat_id=chat_id,
            username=username,
        )

        await process_telegram_inbound_and_respond(
            chat_id=chat_id,
            message_body=text,
            from_name=from_name,
            conversation_id=conversation_id,
        )
    except Exception as e:
        logger.error(f"Error processing Telegram message from {chat_id}: {e}")
        await send_telegram(chat_id, "Sorry, I encountered an error. Please try again.")


def get_status() -> dict:
    """Get the status of the Telegram service for the skills UI."""
    if not is_configured():
        return {
            "status": "error",
            "status_message": "TELEGRAM_BOT_TOKEN not set in .env",
        }

    polling_active = _polling_task is not None and not _polling_task.done()

    if polling_active:
        status_msg = "Connected and polling"
        if TELEGRAM_ALLOWED_USER_ID:
            status_msg += f" (restricted to user {TELEGRAM_ALLOWED_USER_ID})"
        return {
            "status": "connected",
            "status_message": status_msg,
        }

    return {
        "status": "error",
        "status_message": "Polling not active",
    }
```

---

## Phase 3: Contact & Conversation Management

### Step 3.1: Add helper functions in webhooks.py

**File**: `backend/routers/webhooks.py`

Add `get_or_create_telegram_conversation()` — same pattern as existing `get_or_create_contact_conversation()` but queries by `external_id` + `platform`:

```python
async def get_or_create_telegram_conversation(
    chat_id: str,
    username: str = "Telegram User",
    channel: str = "telegram"
) -> tuple[str, bool]:
    """Get or create a conversation for a Telegram contact.

    Same 24-hour rotation logic as SMS/WhatsApp contacts.
    Uses external_id instead of phone_number.
    """
    from services.database import async_session, ExternalContactModel
    from sqlalchemy import select, update, func, and_

    async with async_session() as session:
        result = await session.execute(
            select(ExternalContactModel).where(
                and_(
                    ExternalContactModel.external_id == chat_id,
                    ExternalContactModel.platform == "telegram"
                )
            )
        )
        contact = result.scalar_one_or_none()

        if contact:
            # Check if conversation is stale (>24h since last contact)
            now = datetime.now(timezone.utc)
            last_contacted = contact.last_contacted
            if last_contacted and last_contacted.tzinfo is None:
                last_contacted = last_contacted.replace(tzinfo=timezone.utc)

            is_stale = (
                last_contacted is None
                or (now - last_contacted) > timedelta(hours=24)
            )

            if is_stale:
                new_conversation_id = str(uuid.uuid4())
                await session.execute(
                    update(ExternalContactModel)
                    .where(ExternalContactModel.id == contact.id)
                    .values(
                        conversation_id=new_conversation_id,
                        last_contacted=func.now(),
                        last_channel=channel,
                    )
                )
                await session.commit()

                await create_conversation(
                    conversation_id=new_conversation_id,
                    title=f"Telegram: {contact.contact_name or username}"
                )
                print(f"Rotated Telegram conversation for {username} (stale >24h)")
                return new_conversation_id, True

            # Still fresh — update timestamps
            await session.execute(
                update(ExternalContactModel)
                .where(ExternalContactModel.id == contact.id)
                .values(last_contacted=func.now(), last_channel=channel)
            )
            await session.commit()
            return contact.conversation_id, False

        # Create new contact and conversation
        conversation_id = str(uuid.uuid4())
        contact_id = str(uuid.uuid4())

        new_contact = ExternalContactModel(
            id=contact_id,
            external_id=chat_id,
            phone_number=None,
            conversation_id=conversation_id,
            contact_name=username,
            platform="telegram",
            last_channel="telegram",
        )
        session.add(new_contact)
        await session.commit()

        await create_conversation(
            conversation_id=conversation_id,
            title=f"Telegram: {username}"
        )

        return conversation_id, True
```

### Step 3.2: Add `process_telegram_inbound_and_respond()`

```python
async def process_telegram_inbound_and_respond(
    chat_id: str,
    message_body: str,
    from_name: str,
    conversation_id: str,
):
    """Process an incoming Telegram message and send Edward's response."""
    # Push notification for inbound
    try:
        from services.push_service import send_push_notification, is_configured as push_configured
        if push_configured():
            await send_push_notification(
                title=f"Telegram from {from_name}",
                body=message_body[:100] + ("..." if len(message_body) > 100 else ""),
                url="/",
                tag=f"inbound-telegram-{chat_id}",
            )
    except Exception as e:
        print(f"Failed to send push notification for Telegram: {e}")

    try:
        from services.telegram_service import send_telegram

        settings = await get_settings()
        graph = await get_graph()

        msg_context = (
            "\n\n[CONTEXT: This message is from a Telegram conversation. "
            "You can use full message formatting. "
            f"The user is {from_name} messaging via Telegram.]"
        )
        enhanced_prompt = settings.system_prompt + msg_context

        response = await chat_with_memory(
            message=message_body,
            conversation_id=conversation_id,
            system_prompt=enhanced_prompt,
            model=settings.model,
            temperature=settings.temperature,
            graph=graph
        )

        # Telegram allows 4096 chars (much more than SMS's 1500)
        if len(response) > 4096:
            response = response[:4093] + "..."

        await send_telegram(chat_id, response)
        print(f"Sent Telegram response to {from_name}: {response[:50]}...")

    except Exception as e:
        print(f"Error processing Telegram message from {from_name}: {e}")
        try:
            from services.telegram_service import send_telegram
            await send_telegram(
                chat_id,
                "Sorry, I encountered an error processing your message. Please try again."
            )
        except Exception:
            pass
```

---

## Phase 4: Tool & Skill Registration

### Step 4.1: Add tool in tools.py

**File**: `backend/services/graph/tools.py`

```python
@tool
async def send_telegram(chat_id: str, message: str) -> str:
    """Send a Telegram message to a specific chat.

    Use this tool to send messages via Telegram. The chat_id identifies
    the Telegram conversation to send to.

    Args:
        chat_id: The Telegram chat ID to send to
        message: The message text to send (max 4096 characters)
    """
    from services.telegram_service import send_telegram as tg_send, is_configured
    if not is_configured():
        return "Telegram is not configured. Set TELEGRAM_BOT_TOKEN in .env."
    try:
        result = await tg_send(chat_id, message)
        if result.get("status") == "sent":
            return f"Telegram message sent successfully to chat {chat_id}"
        return f"Telegram send failed: {result.get('error', 'unknown error')}"
    except Exception as e:
        return f"Failed to send Telegram message: {str(e)}"
```

Update `send_message` smart routing to include `"telegram"` as a channel option.
Update `_get_contact_last_channel()` to also check `external_id` for Telegram contacts.

### Step 4.2: Register skill in skills_service.py

**File**: `backend/services/skills_service.py`

Add to `SKILL_DEFINITIONS`:
```python
"telegram": {
    "name": "Telegram",
    "description": "Send/receive Telegram messages via bot (long-polling)",
    "get_status": lambda: _get_telegram_status(),
},
```

Add status function:
```python
def _get_telegram_status() -> dict:
    from services.telegram_service import is_configured, get_status
    if not is_configured():
        return {"status": "error", "status_message": "TELEGRAM_BOT_TOKEN not set"}
    return get_status()
```

### Step 4.3: Register in tool_registry.py

**File**: `backend/services/tool_registry.py`

Add to `SKILL_TOOL_MAPPING`:
```python
"telegram": ["send_telegram"],
```

Add to `_get_skill_states()`:
```python
"telegram": await is_skill_enabled("telegram"),
```

Update `_get_messaging_tools()`:
```python
from services.graph.tools import (
    send_sms,
    send_whatsapp,
    send_imessage,
    get_recent_messages,
    send_message,
    send_telegram,  # NEW
)

# send_telegram: gated by telegram
if skill_states.get("telegram"):
    tools.append(send_telegram)

# send_message: available if ANY messaging skill is enabled
any_messaging_enabled = (
    skill_states.get("twilio_sms") or
    skill_states.get("twilio_whatsapp") or
    skill_states.get("imessage_applescript") or
    skill_states.get("telegram")  # NEW
)
```

### Step 4.4: Add lifecycle to main.py

**File**: `backend/main.py`

Startup (after tool registry init, before scheduler):
```python
# Start Telegram polling (if configured)
try:
    from services.telegram_service import start_polling as start_telegram
    await start_telegram()
except Exception as e:
    print(f"Telegram polling initialization skipped: {e}")
```

Shutdown (before MCP shutdown):
```python
try:
    from services.telegram_service import stop_polling as stop_telegram
    await stop_telegram()
except Exception as e:
    print(f"Telegram polling shutdown error: {e}")
```

### Step 4.5: Add dependency

**File**: `backend/requirements.txt`

Add: `python-telegram-bot>=21.0`

---

## Phase 5: Environment Variables

Add to `.env`:
```bash
# Telegram Bot
TELEGRAM_BOT_TOKEN=          # Get from @BotFather on Telegram
TELEGRAM_ALLOWED_USER_ID=    # Your Telegram user ID (get via @userinfobot)
```

---

## Build Verification

| Test | Expected Result | ✓ |
|------|----------------|---|
| Start backend with `TELEGRAM_BOT_TOKEN` set | "Telegram polling started" in logs | |
| Start backend WITHOUT token | "Telegram polling initialization skipped" in logs | |
| `GET /api/skills` | Shows "telegram" skill with correct status | |
| `PATCH /api/skills/telegram` enable | Skill enabled, no errors | |
| Send message to bot from allowed user | Response appears in Telegram within 5s | |
| Send message from unauthorized user | "This is a private bot" reply | |
| Check web UI sidebar | Telegram conversation appears with title | |
| Send 2nd message within 24h | Same conversation reused | |
| Send message after 24h gap | New conversation created | |
| From chat UI: "send a telegram to [chat_id]" | Message delivered via Telegram | |
| Schedule reminder: "remind me in 1 minute" | Telegram message arrives ~1 min later | |
| `send_message` with Telegram contact | Routes via Telegram (smart routing) | |

---

## Rollback Plan

1. Remove `telegram_service.py`
2. Revert changes to: `database.py`, `webhooks.py`, `tools.py`, `skills_service.py`, `tool_registry.py`, `main.py`
3. Remove `python-telegram-bot` from `requirements.txt`
4. DB migration is safe to leave (nullable column + index don't affect existing data)

---

## Implementation Notes (Post-Completion)

_To be filled in after implementation. Document any deviations from this plan._
