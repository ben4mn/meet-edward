"""The name AREA 2's ``send_message`` looks pings up by (AREA 3, 2026-10-11).

``addressed_recently(chat_id, hours=24)`` is true when that chat pinged
Edward (an @mention, a reply to him, the owner's self-chat) within ``hours``; the
recipient rule (b) lets Edward answer there. Implemented in
``services.pings.hooks``.
"""

from services.pings.hooks import addressed_recently, chat_key  # noqa: F401
