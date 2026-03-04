"""
iMessage service using AppleScript.

A simple, direct approach to send iMessages without MCP dependencies.
Requires macOS with Messages.app configured.
"""

import subprocess
import os
import sys
from collections import deque
from typing import Optional


# Configuration
IMESSAGE_ENABLED = os.getenv("IMESSAGE_ENABLED", "true").lower() == "true"

# Track recently sent messages so the heartbeat listener can skip self-triggered @edward mentions
_recent_edward_sends: deque[str] = deque(maxlen=20)


def is_available() -> bool:
    """Check if iMessage is available (macOS only)."""
    if not IMESSAGE_ENABLED:
        return False
    # Check if we're on macOS
    return sys.platform == "darwin"


def send_imessage(recipient: str, message: str) -> dict:
    """
    Send an iMessage via AppleScript.

    Args:
        recipient: Phone number, email, or contact name
        message: The message to send

    Returns:
        dict with status and details
    """
    if not is_available():
        return {"success": False, "error": "iMessage not available"}

    # Escape quotes in the message
    escaped_message = message.replace('\\', '\\\\').replace('"', '\\"')
    escaped_recipient = recipient.replace('\\', '\\\\').replace('"', '\\"')

    # AppleScript to send iMessage
    script = f'''
    tell application "Messages"
        set targetService to 1st account whose service type = iMessage
        set targetBuddy to participant "{escaped_recipient}" of targetService
        send "{escaped_message}" to targetBuddy
    end tell
    '''

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            _recent_edward_sends.append(message[:200])
            return {
                "success": True,
                "recipient": recipient,
                "message": "iMessage sent successfully"
            }
        else:
            return {
                "success": False,
                "error": result.stderr.strip() or "Unknown error sending iMessage"
            }

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Timeout sending iMessage"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_recent_messages(contact: Optional[str] = None, limit: int = 10) -> dict:
    """
    Get recent iMessages using AppleScript.

    Note: This is limited compared to MCP - AppleScript access to message
    history is restricted. For full history, would need to query the
    chat.db SQLite database directly (requires Full Disk Access).

    Args:
        contact: Optional contact to filter by
        limit: Number of messages to retrieve

    Returns:
        dict with messages or error
    """
    if not is_available():
        return {"success": False, "error": "iMessage not available"}

    # AppleScript to get recent chats (limited functionality)
    script = '''
    tell application "Messages"
        set chatList to {}
        repeat with c in (chats)
            set end of chatList to {chatName: name of c, chatId: id of c}
        end repeat
        return chatList
    end tell
    '''

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            return {
                "success": True,
                "chats": result.stdout.strip(),
                "note": "Full message history requires direct database access"
            }
        else:
            return {
                "success": False,
                "error": result.stderr.strip() or "Could not retrieve messages"
            }

    except Exception as e:
        return {"success": False, "error": str(e)}


def get_status() -> dict:
    """
    Get the current status of the iMessage AppleScript service.

    Returns:
        Dict with status info: status, status_message, metadata
    """
    if not IMESSAGE_ENABLED:
        return {
            "status": "error",
            "status_message": "Set IMESSAGE_ENABLED=true in environment",
            "metadata": None
        }

    # Check if we're on macOS
    if sys.platform != "darwin":
        return {
            "status": "error",
            "status_message": "Not running on macOS",
            "metadata": None
        }

    # Try to verify Messages.app is accessible
    try:
        result = subprocess.run(
            ["osascript", "-e", 'tell application "Messages" to return name'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return {
                "status": "connected",
                "status_message": "Messages.app accessible",
                "metadata": None
            }
        else:
            return {
                "status": "error",
                "status_message": result.stderr.strip() or "Messages.app not accessible",
                "metadata": None
            }
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "status_message": "Timeout checking Messages.app",
            "metadata": None
        }
    except Exception as e:
        return {
            "status": "error",
            "status_message": str(e),
            "metadata": None
        }
