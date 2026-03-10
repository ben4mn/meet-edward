"""
Tools for Edward's LLM capabilities.

Memory tools allow Edward to:
- Update existing memories with new information
- Delete/forget memories when asked
- Search memories to find specific information

Messaging tools allow Edward to:
- Send SMS via Twilio (Edward's phone number)
- Send iMessage via MCP (as the user)
- Smart routing between channels

Code execution tools allow Edward to:
- Write and execute Python code in a sandbox
- See execution results and iterate

Search tools allow Edward to:
- Search the web using Brave Search
- Fetch and extract content from web pages
"""

from typing import Any, Optional, List, Dict
from services.graph.tool_decorator import tool
from utils.message_signature import ensure_message_signature


@tool
async def remember_update(memory_id: str, new_content: str) -> str:
    """
    Update an existing memory with new content.

    Use this when the user provides updated information that should replace
    or modify an existing memory. The memory's embedding will be regenerated.

    Args:
        memory_id: The ID of the memory to update (from the memory context)
        new_content: The new content for this memory

    Returns:
        Confirmation message
    """
    from services.memory_service import update_memory, get_memory_by_id

    # Verify memory exists
    existing = await get_memory_by_id(memory_id)
    if not existing:
        return f"Memory with ID {memory_id} not found. Cannot update."

    updated = await update_memory(memory_id=memory_id, content=new_content)
    if updated:
        return "ok"
    return "Failed to update memory."


@tool
async def remember_forget(memory_id: str) -> str:
    """
    Delete/forget a specific memory.

    Use this when the user explicitly asks to forget something, or when
    information is no longer accurate or relevant.

    Args:
        memory_id: The ID of the memory to delete (from the memory context)

    Returns:
        Confirmation message
    """
    from services.memory_service import delete_memory, get_memory_by_id

    # Get memory content for confirmation
    existing = await get_memory_by_id(memory_id)
    if not existing:
        return f"Memory with ID {memory_id} not found. Nothing to delete."

    deleted = await delete_memory(memory_id)
    if deleted:
        return "ok"
    return "Failed to delete memory."


@tool
async def remember_search(query: str, memory_type: Optional[str] = None) -> str:
    """
    Search through memories for specific information.

    Use this to find memories related to a topic, or to check what
    information is stored about something specific.

    Args:
        query: Search query describing what to find
        memory_type: Optional filter: 'fact', 'preference', 'context', or 'instruction'

    Returns:
        List of matching memories with their IDs
    """
    from services.memory_service import search_memories

    memories, total = await search_memories(
        query=query,
        memory_type=memory_type,
        limit=10
    )

    if not memories:
        return f"No memories found matching '{query}'."

    results = [f"Found {total} memories matching '{query}':\n"]
    for m in memories[:10]:
        score_str = f" (score: {m.score:.2f})" if m.score > 0 else ""
        tn = getattr(m, 'temporal_nature', 'timeless') or 'timeless'
        tn_tag = f" [{tn}]" if tn != "timeless" else ""
        results.append(f"- [{m.memory_type}] {m.content}{tn_tag} (ID: {m.id}){score_str}")

    return "\n".join(results)


# List of all memory tools for binding to LLM
MEMORY_TOOLS = [remember_update, remember_forget, remember_search]


# ============================================================================
# MESSAGING TOOLS
# ============================================================================

@tool
async def send_sms(phone_number: str, message: str) -> str:
    """
    Send an SMS message via Twilio (from Edward's phone number).

    Use this when you need to text someone as Edward. The recipient will see
    the message coming from Edward's dedicated phone number.

    Args:
        phone_number: The recipient's phone number (can be formatted like +1234567890 or 123-456-7890)
        message: The message content to send

    Returns:
        Confirmation message with status
    """
    from services.twilio_service import send_sms as twilio_send, is_configured
    from services.conversation_service import mark_user_notified

    if not is_configured():
        return "SMS not available. Twilio is not configured."

    message = ensure_message_signature(message)

    try:
        result = await twilio_send(phone_number, message)
        # Mark this conversation as having notified the user
        conversation_id = get_current_conversation_id()
        if conversation_id:
            await mark_user_notified(conversation_id)
        return f"SMS sent successfully to {result['to']} (Status: {result['status']})"
    except Exception as e:
        return f"Failed to send SMS: {str(e)}"


@tool
async def send_imessage(contact: str, message: str) -> str:
    """
    Send an iMessage as the user (not as Edward).

    Use this when the user wants to send a message as themselves via iMessage.
    Only use when the user explicitly requests to send as themselves or for
    personal contacts.

    Args:
        contact: Contact name or phone number
        message: The message content to send

    Returns:
        Confirmation message with status
    """
    from services.imessage_service import send_imessage as applescript_send, is_available
    from services.conversation_service import mark_user_notified

    if not is_available():
        return "iMessage not available. Only works on macOS with Messages.app configured."

    message = ensure_message_signature(message)

    try:
        result = applescript_send(contact, message)
        if result["success"]:
            # Mark this conversation as having notified the user
            conversation_id = get_current_conversation_id()
            if conversation_id:
                await mark_user_notified(conversation_id)
            return f"iMessage sent to {contact}"
        else:
            return f"Failed to send iMessage: {result.get('error', 'Unknown error')}"
    except Exception as e:
        return f"Failed to send iMessage: {str(e)}"


@tool
async def get_recent_messages(contact: Optional[str] = None, hours: int = 24) -> str:
    """
    Get recent iMessage conversations.

    Use this to check the user's recent messages or conversations with a
    specific contact.

    Args:
        contact: Optional - specific contact name or number to filter by
        hours: How many hours back to look (default 24)

    Returns:
        Formatted message history
    """
    from services.imessage_service import get_recent_messages as applescript_get, is_available

    if not is_available():
        return "iMessage not available. Only works on macOS with Messages.app configured."

    try:
        result = applescript_get(contact, limit=20)
        if result["success"]:
            return f"Recent chats: {result.get('chats', 'No chats found')}\n\nNote: {result.get('note', '')}"
        else:
            return f"Failed to get messages: {result.get('error', 'Unknown error')}"
    except Exception as e:
        return f"Failed to get messages: {str(e)}"


@tool
async def send_whatsapp(phone_number: str, message: str) -> str:
    """
    Send a WhatsApp message via Twilio (from Edward's phone number).

    Use this when you need to message someone on WhatsApp as Edward.
    The recipient will see the message coming from Edward's WhatsApp number.

    Args:
        phone_number: The recipient's phone number (can be formatted like +1234567890 or 123-456-7890)
        message: The message content to send

    Returns:
        Confirmation message with status
    """
    from services.twilio_service import send_whatsapp as twilio_send_wa, is_configured
    from services.conversation_service import mark_user_notified

    if not is_configured():
        return "WhatsApp not available. Twilio is not configured."

    message = ensure_message_signature(message)

    try:
        result = await twilio_send_wa(phone_number, message)
        # Mark this conversation as having notified the user
        conversation_id = get_current_conversation_id()
        if conversation_id:
            await mark_user_notified(conversation_id)
        return f"WhatsApp sent successfully to {result['to']} (Status: {result['status']})"
    except Exception as e:
        return f"Failed to send WhatsApp: {str(e)}"


@tool
async def send_message(
    recipient: str,
    message: str,
    channel: Optional[str] = None
) -> str:
    """
    Send a message via the best available channel.

    This is the preferred way to send messages. It will automatically choose
    the right channel based on context:
    - Default: Twilio SMS (sends as Edward), or WhatsApp if the contact last used WhatsApp
    - iMessage: When user explicitly requests or for personal contacts
    - WhatsApp: When user requests or contact last used WhatsApp

    Args:
        recipient: Phone number or contact name
        message: The message content to send
        channel: Optional - force a specific channel: 'twilio', 'whatsapp', 'imessage', or None (auto)

    Returns:
        Confirmation message with status
    """
    from services.twilio_service import (
        send_sms as twilio_send,
        send_whatsapp as twilio_send_wa,
        is_configured as twilio_configured,
    )
    from services.imessage_service import send_imessage as applescript_send, is_available as imessage_available
    from services.skills_service import is_skill_enabled
    from services.conversation_service import mark_user_notified

    # Enforce signature before any routing
    message = ensure_message_signature(message)

    # Helper to mark conversation as notified on successful send
    async def mark_notified():
        conversation_id = get_current_conversation_id()
        if conversation_id:
            await mark_user_notified(conversation_id)

    # Check skill enabled states
    twilio_sms_enabled = await is_skill_enabled("twilio_sms")
    twilio_wa_enabled = await is_skill_enabled("twilio_whatsapp")
    imessage_enabled = await is_skill_enabled("imessage_applescript")

    # If channel explicitly specified, use it
    if channel == "imessage":
        if not imessage_enabled:
            return "iMessage is disabled. Enable it in Settings to use this feature."
        if not imessage_available():
            return "iMessage not available. Only works on macOS with Messages.app configured."
        try:
            result = applescript_send(recipient, message)
            if result["success"]:
                await mark_notified()
                return f"iMessage sent to {recipient}"
            return f"Failed to send iMessage: {result.get('error', 'Unknown error')}"
        except Exception as e:
            return f"Failed to send iMessage: {str(e)}"

    if channel == "whatsapp":
        if not twilio_wa_enabled:
            return "WhatsApp is disabled. Enable it in Settings to use this feature."
        if not twilio_configured():
            return "WhatsApp not available. Twilio is not configured."
        try:
            result = await twilio_send_wa(recipient, message)
            await mark_notified()
            return f"WhatsApp sent to {result['to']} (Status: {result['status']})"
        except Exception as e:
            return f"Failed to send WhatsApp: {str(e)}"

    if channel == "twilio":
        if not twilio_sms_enabled:
            return "Twilio SMS is disabled. Enable it in Settings to use this feature."
        if not twilio_configured():
            return "SMS not available. Twilio is not configured."
        try:
            result = await twilio_send(recipient, message)
            await mark_notified()
            return f"SMS sent successfully to {result['to']} (Status: {result['status']})"
        except Exception as e:
            return f"Failed to send SMS: {str(e)}"

    # Auto-routing: Check if this contact has a last_channel preference
    auto_channel = await _get_contact_last_channel(recipient)

    if auto_channel == "whatsapp" and twilio_wa_enabled and twilio_configured():
        try:
            result = await twilio_send_wa(recipient, message)
            await mark_notified()
            return f"WhatsApp sent from Edward to {result['to']} (Status: {result['status']})"
        except Exception as e:
            # Fall through to SMS
            pass

    # Default to SMS via Twilio
    if twilio_sms_enabled and twilio_configured():
        try:
            result = await twilio_send(recipient, message)
            await mark_notified()
            return f"SMS sent from Edward to {result['to']} (Status: {result['status']})"
        except Exception as e:
            if imessage_enabled and imessage_available():
                try:
                    result = applescript_send(recipient, message)
                    if result["success"]:
                        await mark_notified()
                        return f"Sent via iMessage (Twilio failed): iMessage sent to {recipient}"
                except Exception:
                    pass
            return f"Failed to send message: {str(e)}"

    # If Twilio not available/enabled, try iMessage
    if imessage_enabled and imessage_available():
        try:
            result = applescript_send(recipient, message)
            if result["success"]:
                await mark_notified()
                return f"iMessage sent to {recipient}"
            return f"Failed to send iMessage: {result.get('error', 'Unknown error')}"
        except Exception as e:
            return f"Failed to send iMessage: {str(e)}"

    return "No messaging channels available. Enable Twilio SMS, WhatsApp, or iMessage in Settings."


async def _get_contact_last_channel(recipient: str) -> Optional[str]:
    """Look up the last_channel for a contact by phone number."""
    from services.twilio_service import normalize_phone_number
    from services.database import async_session, ExternalContactModel
    from sqlalchemy import select

    try:
        normalized = normalize_phone_number(recipient)
        async with async_session() as session:
            result = await session.execute(
                select(ExternalContactModel.last_channel).where(
                    ExternalContactModel.phone_number == normalized
                )
            )
            row = result.scalar_one_or_none()
            return row  # "sms", "whatsapp", or None
    except Exception:
        return None


# List of all messaging tools for binding to LLM
MESSAGING_TOOLS = [send_sms, send_whatsapp, send_imessage, get_recent_messages, send_message]


# ============================================================================
# CONTACTS TOOLS
# ============================================================================

@tool
def lookup_contact(query: str) -> str:
    """
    Look up a contact in Contacts.app by name.

    Use this before sending messages to resolve nicknames, partial names,
    or informal names to phone numbers. Example: lookup_contact("john doe")
    finds "John A. Doe" with their phone number.

    Args:
        query: Name or partial name to search for (case-insensitive)

    Returns:
        JSON with matching contacts including names, phone numbers, and emails
    """
    from services.contacts_service import lookup_contact as contacts_lookup, is_available

    if not is_available():
        return "Contacts lookup not available. Only works on macOS with Contacts.app."

    try:
        result = contacts_lookup(query)
        if result["success"]:
            matches = result.get("matches", [])
            if not matches:
                return f"No contacts found matching '{query}'."

            output = [f"Found {len(matches)} contact(s) matching '{query}':\n"]
            for m in matches:
                output.append(f"- **{m['name']}**")
                if m.get("phones"):
                    output.append(f"  Phones: {', '.join(m['phones'])}")
                if m.get("emails"):
                    output.append(f"  Emails: {', '.join(m['emails'])}")
                output.append("")

            return "\n".join(output)
        else:
            return f"Failed to search contacts: {result.get('error', 'Unknown error')}"
    except Exception as e:
        return f"Failed to search contacts: {str(e)}"


@tool
def lookup_phone(phone_number: str) -> str:
    """
    Reverse lookup a contact by phone number.

    Use this when you have a phone number (e.g. from an iMessage sender) and need
    to find who it belongs to. Returns the contact name, phone numbers, and emails.

    Args:
        phone_number: Phone number to search for (any format, e.g. +15551234567)

    Returns:
        Matching contact info or 'no match' message
    """
    from services.contacts_service import lookup_phone as contacts_lookup_phone, is_available

    if not is_available():
        return "Contacts lookup not available. Only works on macOS with Contacts.app."

    try:
        result = contacts_lookup_phone(phone_number)
        if result["success"]:
            matches = result.get("matches", [])
            if not matches:
                return f"No contacts found with phone number '{phone_number}'."

            output = [f"Found {len(matches)} contact(s) matching '{phone_number}':\n"]
            for m in matches:
                output.append(f"- **{m['name']}**")
                if m.get("phones"):
                    output.append(f"  Phones: {', '.join(m['phones'])}")
                if m.get("emails"):
                    output.append(f"  Emails: {', '.join(m['emails'])}")
                output.append("")

            return "\n".join(output)
        else:
            return f"Failed to search contacts: {result.get('error', 'Unknown error')}"
    except Exception as e:
        return f"Failed to search contacts: {str(e)}"


# List of all contacts tools
CONTACTS_TOOLS = [lookup_contact, lookup_phone]


# ============================================================================
# CODE EXECUTION TOOLS
# ============================================================================

# Context variable for current conversation_id — safe for concurrent workers
import contextvars
_current_conversation_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    '_current_conversation_id', default=None
)


def set_current_conversation_id(conversation_id: str) -> None:
    """Set the current conversation ID for code execution context."""
    _current_conversation_id.set(conversation_id)


def get_current_conversation_id() -> Optional[str]:
    """Get the current conversation ID for code execution context."""
    return _current_conversation_id.get()


@tool
async def execute_code(code: str) -> str:
    """
    Execute Python code and return the results.

    Use this when you need to:
    - Perform calculations or data analysis
    - Generate visualizations or charts
    - Process or transform data
    - Test algorithms or logic

    The code runs in a sandboxed environment with access to common packages
    like numpy, pandas, matplotlib, requests, and pillow.

    To save files (like plots), use save_file(filename, content):
    - For matplotlib: plt.savefig('plot.png'); save_file('plot.png', open('plot.png', 'rb').read())
    - Or simply: plt.savefig('plot.png') and it will be saved to the sandbox

    Args:
        code: Python code to execute

    Returns:
        The output from the code execution, including any print statements,
        errors, and information about files created.
    """
    from services.code_execution_service import execute_code as exec_code, is_available

    if not is_available():
        return "Code execution is not available. Python interpreter not found."

    conversation_id = get_current_conversation_id()
    if not conversation_id:
        return "Error: No conversation context available for code execution."

    try:
        result = await exec_code(code, conversation_id)

        # Build response
        response_parts = []

        if result.output:
            response_parts.append(result.output)

        if result.error:
            response_parts.append(f"\nError:\n{result.error}")

        if result.files_created:
            response_parts.append(f"\nFiles created: {', '.join(result.files_created)}")

        if result.truncated:
            response_parts.append("\n[Output was truncated due to length]")

        response_parts.append(f"\n[Execution completed in {result.duration_ms}ms]")

        if not result.success and not result.error:
            response_parts.append("\n[Execution failed with no error output]")

        return "\n".join(response_parts) if response_parts else "[No output]"

    except Exception as e:
        return f"Code execution failed: {str(e)}"


@tool
async def list_sandbox_files() -> str:
    """
    List files in the current conversation's sandbox directory.

    Use this to see what files have been created by previous code executions
    in this conversation.

    Returns:
        List of files in the sandbox directory.
    """
    from services.code_execution_service import list_sandbox_files as list_files

    conversation_id = get_current_conversation_id()
    if not conversation_id:
        return "Error: No conversation context available."

    try:
        files = await list_files(conversation_id)
        if not files:
            return "No files in sandbox directory."
        return "Files in sandbox:\n" + "\n".join(f"- {f}" for f in files)
    except Exception as e:
        return f"Error listing files: {str(e)}"


@tool
async def read_sandbox_file(filename: str) -> str:
    """
    Read the contents of a file from the sandbox directory.

    Use this to view the contents of files created by code execution.

    Args:
        filename: Name of the file to read

    Returns:
        Contents of the file, or an error message if not found.
    """
    from services.code_execution_service import read_sandbox_file as read_file

    conversation_id = get_current_conversation_id()
    if not conversation_id:
        return "Error: No conversation context available."

    try:
        content = await read_file(conversation_id, filename)
        if content is None:
            return f"File '{filename}' not found in sandbox."
        # Truncate very long files
        if len(content) > 10000:
            return content[:10000] + "\n... [file truncated]"
        return content
    except Exception as e:
        return f"Error reading file: {str(e)}"


# List of all code execution tools
CODE_EXECUTION_TOOLS = [execute_code, list_sandbox_files, read_sandbox_file]

# Note: Tool binding now handled by services.tool_registry which filters
# tools based on skill enabled state. Use get_available_tools() from
# tool_registry instead of directly binding these lists.


# ============================================================================
# JAVASCRIPT EXECUTION TOOLS
# ============================================================================

@tool
async def execute_javascript(code: str) -> str:
    """
    Execute JavaScript code using Node.js and return the results.

    Use this when you need to:
    - Run JavaScript/Node.js code
    - Test JavaScript logic or algorithms
    - Process data with JavaScript
    - Use Node.js built-in modules (fs, path, crypto, etc.)

    The code runs in a sandboxed environment. Network and child process
    modules are blocked. Use console.log() for output.

    To save files, use the global saveFile(filename, content) function.

    Args:
        code: JavaScript code to execute

    Returns:
        The output from the code execution, including console.log output,
        errors, and information about files created.
    """
    from services.execution.javascript_execution import execute_javascript as exec_js, is_available

    if not is_available():
        return "JavaScript execution is not available. Node.js not found."

    conversation_id = get_current_conversation_id()
    if not conversation_id:
        return "Error: No conversation context available for code execution."

    try:
        result = await exec_js(code, conversation_id)

        response_parts = []
        if result.output:
            response_parts.append(result.output)
        if result.error:
            response_parts.append(f"\nError:\n{result.error}")
        if result.files_created:
            response_parts.append(f"\nFiles created: {', '.join(result.files_created)}")
        if result.truncated:
            response_parts.append("\n[Output was truncated due to length]")
        response_parts.append(f"\n[Execution completed in {result.duration_ms}ms]")
        if not result.success and not result.error:
            response_parts.append("\n[Execution failed with no error output]")

        return "\n".join(response_parts) if response_parts else "[No output]"
    except Exception as e:
        return f"JavaScript execution failed: {str(e)}"


JAVASCRIPT_EXECUTION_TOOLS = [execute_javascript, list_sandbox_files, read_sandbox_file]


# ============================================================================
# SQL EXECUTION TOOLS
# ============================================================================

@tool
async def execute_sql(query: str) -> str:
    """
    Execute a SQL query against a per-conversation SQLite database.

    Use this when you need to:
    - Create tables and insert data
    - Run analytical queries
    - Demonstrate SQL concepts
    - Work with structured data

    Each conversation gets its own SQLite database that persists across turns.
    Standard SQL syntax is supported. Results are formatted as aligned tables.

    Args:
        query: SQL query to execute

    Returns:
        Formatted query results or error message.
    """
    from services.execution.sql_execution import execute_sql as exec_sql

    conversation_id = get_current_conversation_id()
    if not conversation_id:
        return "Error: No conversation context available for SQL execution."

    try:
        result = await exec_sql(query, conversation_id)

        response_parts = []
        if result.output:
            response_parts.append(result.output)
        if result.error:
            response_parts.append(f"\nError:\n{result.error}")
        if result.truncated:
            response_parts.append("\n[Output was truncated due to length]")
        response_parts.append(f"\n[Query completed in {result.duration_ms}ms]")
        if not result.success and not result.error:
            response_parts.append("\n[Query failed with no error output]")

        return "\n".join(response_parts) if response_parts else "[No output]"
    except Exception as e:
        return f"SQL execution failed: {str(e)}"


SQL_EXECUTION_TOOLS = [execute_sql, list_sandbox_files, read_sandbox_file]


# ============================================================================
# PERSISTENT DATABASE TOOLS
# ============================================================================

@tool
async def create_persistent_db(name: str, description: Optional[str] = None) -> str:
    """
    Create a named persistent database that survives across conversations.

    Use this when you need to track data long-term across multiple conversations,
    such as:
    - Medication tracking over weeks/months
    - Habit tracking or daily logs
    - Project data that spans multiple sessions
    - Personal records (expenses, workouts, etc.)

    The database persists forever until explicitly deleted. Each database is
    isolated and can contain multiple tables.

    Args:
        name: A short, descriptive name (lowercase, alphanumeric + underscores).
            Examples: "workout_log", "expense_tracker", "project_tasks"
        description: Optional description of what this database tracks

    Returns:
        Confirmation with database details
    """
    from services.persistent_db_service import create_database

    try:
        result = await create_database(name, description)
        return (
            f"Created persistent database '{result['name']}'.\n"
            f"You can now use query_persistent_db('{result['name']}', 'CREATE TABLE ...') "
            "to create tables and store data."
        )
    except ValueError as e:
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Failed to create database: {str(e)}"


@tool
async def query_persistent_db(db_name: str, query: str) -> str:
    """
    Execute SQL against a persistent database.

    Use this to create tables, insert data, and query persistent databases.
    The database retains all data across conversations.

    Args:
        db_name: Name of the persistent database (created with create_persistent_db)
        query: SQL query to execute (CREATE TABLE, INSERT, SELECT, UPDATE, DELETE)

    Returns:
        Query results formatted as a table, or error message
    """
    from services.persistent_db_service import execute_query

    try:
        result = await execute_query(db_name, query)

        response_parts = []
        if result.get("output"):
            response_parts.append(result["output"])
        if result.get("error"):
            response_parts.append(f"Error: {result['error']}")

        duration = result.get("duration_ms", 0)
        if not result.get("success") and not result.get("error"):
            response_parts.append("Query failed with no error output")

        # Always ensure non-empty response
        if not response_parts:
            response_parts.append("Query executed successfully")

        response_parts.append(f"[Completed in {duration}ms]")
        return "\n".join(response_parts)
    except Exception as e:
        return f"Query failed: {str(e)}"


@tool
async def list_persistent_dbs() -> str:
    """
    List all persistent databases.

    Use this to see what databases exist and their purposes. Always check this
    before creating a new database to avoid duplicates.

    Returns:
        List of databases with names, descriptions, and creation dates
    """
    from services.persistent_db_service import list_databases

    try:
        databases = await list_databases()

        if not databases:
            return "No persistent databases found. Use create_persistent_db() to create one."

        lines = [f"Found {len(databases)} persistent database(s):\n"]
        for db in databases:
            desc = f" - {db['description']}" if db.get('description') else ""
            lines.append(f"- **{db['name']}**{desc}")
            lines.append(f"  Created: {db['created_at']}")

        return "\n".join(lines)
    except Exception as e:
        return f"Failed to list databases: {str(e)}"


@tool
async def delete_persistent_db(db_name: str) -> str:
    """
    Delete a persistent database and ALL its data permanently.

    Use this when:
    - A tracking goal is complete (e.g., recovery period ended)
    - The user explicitly asks to clean up old data
    - The database is no longer needed

    WARNING: This permanently deletes all tables and data in the database.
    Always confirm with the user before deleting.

    Args:
        db_name: Name of the database to delete

    Returns:
        Confirmation or error message
    """
    from services.persistent_db_service import delete_database

    try:
        success = await delete_database(db_name)
        if success:
            return f"Database '{db_name}' and all its data have been permanently deleted."
        else:
            return f"Database '{db_name}' not found. Use list_persistent_dbs() to see available databases."
    except Exception as e:
        return f"Failed to delete database: {str(e)}"


# List of all persistent database tools
PERSISTENT_DB_TOOLS = [create_persistent_db, query_persistent_db, list_persistent_dbs, delete_persistent_db]


# ============================================================================
# SHELL EXECUTION TOOLS
# ============================================================================

@tool
async def execute_shell(command: str) -> str:
    """
    Execute a shell (Bash) command and return the results.

    Use this when you need to:
    - Run file operations (ls, cat, grep, awk, sed, sort, uniq, wc, cut, etc.)
    - Fetch URLs or download files (curl, wget)
    - Run Python or Node.js scripts (python3, node)
    - Install packages (pip, npm, brew)
    - Manage containers (docker, kubectl)
    - Network utilities (ssh, scp, rsync, nc, nmap)
    - Process management (kill, pkill)
    - System administration (chmod, chown, mount, launchctl, crontab)
    - Any standard Unix/macOS command

    The command runs in a per-conversation working directory with a 120-second
    timeout. Only catastrophic commands are blocked (sudo, shutdown, dd, mkfs,
    etc.). API keys are NOT available in the shell environment.

    Args:
        command: Shell command to execute

    Returns:
        The output from the command execution.
    """
    from services.execution.shell_execution import execute_shell as exec_shell, is_available

    if not is_available():
        return "Shell execution is not available. Bash not found."

    conversation_id = get_current_conversation_id()
    if not conversation_id:
        return "Error: No conversation context available for shell execution."

    try:
        result = await exec_shell(command, conversation_id)

        response_parts = []
        if result.output:
            response_parts.append(result.output)
        if result.error:
            response_parts.append(f"\nError:\n{result.error}")
        if result.files_created:
            response_parts.append(f"\nFiles created: {', '.join(result.files_created)}")
        if result.truncated:
            response_parts.append("\n[Output was truncated due to length]")
        response_parts.append(f"\n[Execution completed in {result.duration_ms}ms]")
        if not result.success and not result.error:
            response_parts.append("\n[Execution failed with no error output]")

        return "\n".join(response_parts) if response_parts else "[No output]"
    except Exception as e:
        return f"Shell execution failed: {str(e)}"


SHELL_EXECUTION_TOOLS = [execute_shell, list_sandbox_files, read_sandbox_file]


# ============================================================================
# SEARCH TOOLS
# ============================================================================

@tool
async def web_search(query: str, count: int = 5) -> str:
    """
    Search the web using Brave Search.

    Use this to find current information, news, or answers to questions
    that require up-to-date knowledge.

    Args:
        query: Search query string
        count: Number of results to return (1-20, default 5)

    Returns:
        Formatted search results with titles, URLs, and descriptions
    """
    from services.brave_search_service import search, is_configured

    if not is_configured():
        return "Web search not available. Brave Search API key is not configured."

    try:
        results = await search(query, count)

        if not results:
            return f"No results found for '{query}'."

        output = [f"Search results for '{query}':\n"]
        for i, result in enumerate(results, 1):
            output.append(f"{i}. **{result['title']}**")
            output.append(f"   URL: {result['url']}")
            output.append(f"   {result['description']}\n")

        return "\n".join(output)
    except Exception as e:
        return f"Search failed: {str(e)}"


@tool
async def fetch_page_content(url: str) -> str:
    """
    Fetch and extract the main content from a web page.

    Use this after web_search to get the full text of a promising result.
    Returns clean article text, stripped of navigation, ads, etc.

    Args:
        url: The URL to fetch content from

    Returns:
        Extracted text content from the page
    """
    from services.brave_search_service import fetch_page_content as fetch, is_configured

    if not is_configured():
        return "Page fetching not available. Brave Search API key is not configured."

    try:
        content = await fetch(url)
        return content
    except Exception as e:
        return f"Failed to fetch page content: {str(e)}"


# List of all search tools
SEARCH_TOOLS = [web_search, fetch_page_content]


# ============================================================================
# PLAN TOOLS
# ============================================================================

# Module-level plan state, keyed by conversation_id
_active_plans: Dict[str, List[dict]] = {}

# Tracks plan events emitted by tools so streaming layer can pick them up
_pending_plan_events: Dict[str, List[dict]] = {}


def get_active_plan(conversation_id: str) -> Optional[List[dict]]:
    """Get the active plan for a conversation."""
    return _active_plans.get(conversation_id)


def get_plan_status(conversation_id: str) -> Optional[Dict[str, Any]]:
    """Get completion stats for the active plan.

    Returns None if no plan is active, otherwise a dict with:
        total, completed, pending, in_progress, error,
        incomplete_titles (list of step titles not yet completed)
    """
    plan = _active_plans.get(conversation_id)
    if not plan:
        return None

    by_status: Dict[str, int] = {"completed": 0, "pending": 0, "in_progress": 0, "error": 0}
    incomplete_titles: List[str] = []
    for step in plan:
        status = step.get("status", "pending")
        by_status[status] = by_status.get(status, 0) + 1
        if status != "completed":
            incomplete_titles.append(step["title"])

    return {
        "total": len(plan),
        "completed": by_status["completed"],
        "pending": by_status["pending"],
        "in_progress": by_status["in_progress"],
        "error": by_status["error"],
        "incomplete_titles": incomplete_titles,
    }


def get_pending_plan_events(conversation_id: str) -> List[dict]:
    """Get and clear pending plan events for a conversation."""
    events = _pending_plan_events.pop(conversation_id, [])
    return events


def _emit_plan_event(conversation_id: str, event: dict) -> None:
    """Queue a plan event for the streaming layer to pick up."""
    if conversation_id not in _pending_plan_events:
        _pending_plan_events[conversation_id] = []
    _pending_plan_events[conversation_id].append(event)


@tool
async def create_plan(steps: List[str]) -> str:
    """
    Create a task plan with ordered steps for a complex request.

    Use this when you receive a request that involves multiple distinct steps.
    The plan will be visible to the user and you should update each step as you
    work through it.

    Args:
        steps: List of step descriptions in order of execution

    Returns:
        Confirmation with step IDs
    """
    conversation_id = get_current_conversation_id()
    if not conversation_id:
        return "Error: No conversation context available."

    plan_steps = []
    for i, title in enumerate(steps, 1):
        step = {
            "id": f"step-{i}",
            "title": title,
            "status": "in_progress" if i == 1 else "pending",
            "result": None,
        }
        plan_steps.append(step)

    _active_plans[conversation_id] = plan_steps

    _emit_plan_event(conversation_id, {
        "event_type": "plan_created",
        "plan_steps": plan_steps,
    })

    step_list = "\n".join(f"  {s['id']}: {s['title']}" for s in plan_steps)
    return f"Plan created with {len(plan_steps)} steps:\n{step_list}\n\nFirst step is now in progress."


@tool
async def update_plan_step(step_id: str, status: str, result: Optional[str] = None) -> str:
    """
    Update the status of a plan step.

    Call this as you complete each step to show progress to the user.

    Args:
        step_id: The step ID (e.g. "step-1")
        status: New status - "completed", "error", or "in_progress"
        result: Optional brief summary of what was done/found

    Returns:
        Confirmation message
    """
    conversation_id = get_current_conversation_id()
    if not conversation_id:
        return "Error: No conversation context available."

    plan = _active_plans.get(conversation_id)
    if not plan:
        return "No active plan found."

    # Find and update the step
    step_found = False
    for step in plan:
        if step["id"] == step_id:
            step["status"] = status
            if result is not None:
                step["result"] = result
            step_found = True
            break

    if not step_found:
        return f"Step {step_id} not found in plan."

    _emit_plan_event(conversation_id, {
        "event_type": "plan_step_update",
        "step_id": step_id,
        "step_status": status,
        "step_result": result,
    })

    # Auto-advance: if completed, set next pending step to in_progress
    if status == "completed":
        for step in plan:
            if step["status"] == "pending":
                step["status"] = "in_progress"
                _emit_plan_event(conversation_id, {
                    "event_type": "plan_step_update",
                    "step_id": step["id"],
                    "step_status": "in_progress",
                    "step_result": None,
                })
                break

    return f"Step {step_id} updated to {status}."


@tool
async def edit_plan(
    add_steps: Optional[List[str]] = None,
    remove_step_ids: Optional[List[str]] = None
) -> str:
    """
    Edit the current plan by adding or removing steps.

    Use this to adapt the plan mid-execution when you discover new requirements
    or determine some steps aren't needed.

    Args:
        add_steps: New step descriptions to add at the end
        remove_step_ids: Step IDs to remove from the plan

    Returns:
        Updated plan summary
    """
    conversation_id = get_current_conversation_id()
    if not conversation_id:
        return "Error: No conversation context available."

    plan = _active_plans.get(conversation_id)
    if not plan:
        return "No active plan found."

    # Remove steps
    if remove_step_ids:
        plan[:] = [s for s in plan if s["id"] not in remove_step_ids]

    # Add new steps
    if add_steps:
        max_num = max((int(s["id"].split("-")[1]) for s in plan), default=0)
        for i, title in enumerate(add_steps, max_num + 1):
            plan.append({
                "id": f"step-{i}",
                "title": title,
                "status": "pending",
                "result": None,
            })

    _emit_plan_event(conversation_id, {
        "event_type": "plan_updated",
        "plan_steps": plan,
    })

    step_list = "\n".join(f"  {s['id']} [{s['status']}]: {s['title']}" for s in plan)
    return f"Plan updated ({len(plan)} steps):\n{step_list}"


@tool
async def complete_plan(summary: Optional[str] = None) -> str:
    """
    Mark the entire plan as complete.

    Call this when all steps are done to signal completion to the user.

    Args:
        summary: Optional overall summary of what was accomplished

    Returns:
        Confirmation message
    """
    conversation_id = get_current_conversation_id()
    if not conversation_id:
        return "Error: No conversation context available."

    plan = _active_plans.get(conversation_id)
    if not plan:
        return "No active plan found."

    # Reject if any steps were never started
    pending_steps = [s for s in plan if s["status"] == "pending"]
    if pending_steps:
        titles = ", ".join(s["title"] for s in pending_steps)
        return (
            f"Cannot complete plan: {len(pending_steps)} step(s) were never started: {titles}. "
            "Complete them first or use edit_plan(remove_step_ids=[...]) to remove steps that are no longer needed."
        )

    # Allow marking in_progress steps as completed (LLM likely just forgot to call update_plan_step)
    for step in plan:
        if step["status"] == "in_progress":
            step["status"] = "completed"

    _emit_plan_event(conversation_id, {
        "event_type": "plan_completed",
        "plan_steps": plan,
        "plan_summary": summary,
    })

    # Clean up
    del _active_plans[conversation_id]

    return f"Plan completed.{' Summary: ' + summary if summary else ''}"


# List of all plan tools
PLAN_TOOLS = [create_plan, update_plan_step, edit_plan, complete_plan]

PLAN_TOOL_NAMES = {"create_plan", "update_plan_step", "edit_plan", "complete_plan"}


def get_memory_tools_description() -> str:
    """Get a description of available memory tools for the system prompt."""
    return """
## Memory Management

Tools for managing your long-term memory: **remember_update**, **remember_forget**, **remember_search**.

When relevant memories appear in your context, use their IDs to update or delete them.

Memories are short snippets. For full text, use documents. For multi-source research, use notebooks.
"""


def get_contacts_tools_description() -> str:
    """Get a description of available contacts tools for the system prompt."""
    return """
## Contacts Lookup

- **lookup_contact**: Search Contacts.app by name. Always look up contacts before messaging if you only have a nickname or partial name.
- **lookup_phone**: Reverse lookup by phone number. Use when you see a number in heartbeat events or messages.

Use the phone number from lookup results (not contact name) when sending messages. If multiple matches, ask the user which one.
"""


def get_messaging_tools_description() -> str:
    """Get a description of available messaging tools for the system prompt."""
    return """
## Messaging

- **send_message**: Preferred for most messages. Auto-routes: SMS by default, WhatsApp if contact last used it, iMessage if requested.
- **send_sms / send_whatsapp**: Direct send from Edward's number.
- **send_imessage**: Send as the user. Only when user explicitly says "send as me."
- **get_recent_messages**: Check user's recent iMessage history.

Default to send_message (auto-detects best channel). Use iMessage only when user explicitly requests sending as themselves.
"""


def get_search_tools_description() -> str:
    """Get a description of available search tools for the system prompt."""
    return """
## Web Search

- **web_search**: Search via Brave Search. Returns titles, URLs, snippets.
- **fetch_page_content**: Get full text from a URL found via search.

After finding reusable results, consider saving as a document or adding to a notebook.
"""


def get_code_execution_tools_description() -> str:
    """Get a description of available code execution tools for the system prompt."""
    return """
## Code Execution

- **execute_code**: Execute Python in sandbox. 30s timeout.
- **list_sandbox_files / read_sandbox_file**: Browse sandbox outputs.

Save plots with plt.savefig(). Use save_to_storage to persist important outputs.
"""


def get_javascript_execution_tools_description() -> str:
    """Get a description of available JavaScript execution tools for the system prompt."""
    return """
## JavaScript Execution

- **execute_javascript**: Execute JS via Node.js. Network/child_process blocked. 30s timeout.
- **list_sandbox_files / read_sandbox_file**: Browse sandbox outputs.
"""


def get_sql_execution_tools_description() -> str:
    """Get a description of available SQL execution tools for the system prompt."""
    return """
## SQL Database

### Temporary (per-conversation SQLite)
- **execute_sql**: Full SQLite syntax. Data lost when conversation ends. Good for one-time analysis.

### Persistent (named PostgreSQL schemas)
- **create_persistent_db / query_persistent_db / list_persistent_dbs / delete_persistent_db**
- Data persists across ALL conversations. Use for long-term tracking (meds, habits, expenses).
- Always check list_persistent_dbs before creating to avoid duplicates.

**Key distinction**: Use temporary for throwaway analysis, persistent for anything the user will want later. When creating persistent databases, store a memory about the database name and table structure so your future self can find it.
"""


def get_shell_execution_tools_description() -> str:
    """Get a description of available shell execution tools for the system prompt."""
    return """
## Shell/Bash

- **execute_shell**: Execute shell commands. Destructive commands blocked. API keys NOT available in shell env. 120s timeout.
- **list_sandbox_files / read_sandbox_file**: Browse sandbox outputs.
"""


def get_plan_tools_description() -> str:
    """Get a description of available plan tools for the system prompt."""
    return """
## Task Planning

**Create a plan whenever a request involves 3+ tool calls or multiple steps.** Plans give users visibility into your progress and unlock extra tool iterations.

- **create_plan(steps)**: Define ordered steps (first auto-starts)
- **update_plan_step(step_id, status, result?)**: Mark steps completed/error with optional result note
- **edit_plan(add_steps?, remove_step_ids?)**: Add/remove steps mid-execution
- **complete_plan(summary?)**: Finalize when done

**Completion discipline**: Always call complete_plan when done -- never leave a plan hanging. If a step fails, mark it "error" with a note and continue with remaining steps.
"""


# ============================================================================
# SCHEDULED EVENT TOOLS
# ============================================================================

@tool
async def schedule_event(
    description: str,
    scheduled_at: str,
    recurrence_pattern: Optional[str] = None,
    delivery_channel: Optional[str] = None,
) -> str:
    """
    Schedule a future event, reminder, or action.

    Use this when the user asks you to:
    - Set a reminder for a specific time
    - Schedule a message to be sent later
    - Create a recurring task or check-in
    - Do something at a future date/time

    The event will fire automatically at the scheduled time. Edward will
    receive the event description and execute the described action.

    Args:
        description: What Edward should do when the event fires. Be specific
            and include concrete details like contact names/numbers.
            (e.g. "Send Ben at +15551234567 a text reminding him about his dentist appointment"
            or "Tell the user it's time to take a break").
        scheduled_at: ISO 8601 datetime in the USER'S LOCAL TIME
            (e.g. "2025-01-15T14:30:00"). Do NOT convert to UTC — the system
            handles that automatically. Just use the same timezone as the
            current date/time shown in your context.
        recurrence_pattern: Optional cron pattern for recurring events
            (e.g. "0 9 * * *" for daily at 9 AM, "*/5 * * * *" for every 5 min).
            Cron times are also in the user's local timezone.
            Leave empty for one-time events.
        delivery_channel: Optional preferred channel: "sms", "imessage", "chat", or null.
            Use "sms" or "imessage" if the action involves sending a message.
            Use "chat" for in-app only (no external message). Defaults to null
            (auto-infer from description).

    Returns:
        Confirmation with event details
    """
    from services.scheduled_events_service import create_event
    from datetime import datetime, timezone

    conversation_id = get_current_conversation_id()

    try:
        # Parse the datetime
        dt = datetime.fromisoformat(scheduled_at)
        if dt.tzinfo is None:
            # Treat as local time — attach local timezone then convert to UTC
            local_dt = dt.astimezone()  # interprets naive as local
            dt = local_dt.astimezone(timezone.utc)
    except ValueError:
        return f"Invalid datetime format: {scheduled_at}. Use ISO 8601 format (e.g. 2025-01-15T14:30:00)."

    try:
        event = await create_event(
            description=description,
            scheduled_at=dt,
            recurrence_pattern=recurrence_pattern,
            conversation_id=conversation_id,
            delivery_channel=delivery_channel,
            created_by="edward",
        )
    except ValueError as e:
        return f"Error: {str(e)}"

    recurrence_info = f" (recurring: {recurrence_pattern})" if recurrence_pattern else " (one-time)"
    from services.scheduled_events_service import _format_local
    fire_time = _format_local(event.next_fire_at) if event.next_fire_at else scheduled_at
    return f"Event scheduled{recurrence_info}. Next fire: {fire_time}. ID: {event.id}"


@tool
async def list_scheduled_events(status: Optional[str] = None) -> str:
    """
    List scheduled events.

    Use this when the user asks about their reminders, scheduled events,
    or upcoming tasks.

    Args:
        status: Optional filter: "pending", "completed", "cancelled", "failed".
            Defaults to showing pending events.

    Returns:
        Formatted list of events
    """
    from services.scheduled_events_service import list_events

    events = await list_events(
        status=status,  # None = show all statuses (user can filter explicitly)
        conversation_id=None,  # Show events from ALL conversations
        limit=20,
    )

    if not events:
        filter_desc = f" with status '{status}'" if status else ""
        return f"No scheduled events found{filter_desc}."

    lines = [f"Found {len(events)} event(s):\n"]
    for e in events:
        from services.scheduled_events_service import _format_local
        fire_time = _format_local(e.next_fire_at, '%b %d, %Y %I:%M %p') if e.next_fire_at else "N/A"
        recurrence = f" [recurring: {e.recurrence_pattern}]" if e.recurrence_pattern else ""
        lines.append(f"- [{e.status}] {e.description} — fires: {fire_time}{recurrence} (ID: {e.id})")

    return "\n".join(lines)


@tool
async def cancel_scheduled_event(event_id: str) -> str:
    """
    Cancel a scheduled event.

    Use this when the user wants to cancel a reminder or scheduled action.

    Args:
        event_id: The ID of the event to cancel

    Returns:
        Confirmation message
    """
    from services.scheduled_events_service import cancel_event

    success = await cancel_event(event_id)
    if success:
        return f"Event {event_id} cancelled."
    return f"Could not cancel event {event_id}. It may already be completed or cancelled."


# List of all scheduled event tools
SCHEDULED_EVENT_TOOLS = [schedule_event, list_scheduled_events, cancel_scheduled_event]

SCHEDULED_EVENT_TOOL_NAMES = {"schedule_event", "list_scheduled_events", "cancel_scheduled_event"}


# =============================================================================
# Heartbeat tools
# =============================================================================

@tool
async def review_heartbeat(
    sender: Optional[str] = None,
    status_filter: Optional[str] = None,
    limit: int = 15,
) -> str:
    """
    Review recent messages and events you've noticed via your heartbeat system.

    Use this when the user asks what you've noticed, what messages came in,
    or wants to know about recent activity from contacts.

    Args:
        sender: Optional — filter by sender phone/email (partial match)
        status_filter: Optional — "pending", "dismissed", "noted", "acted", "escalated"
        limit: Max events to return (default 15)

    Returns:
        Formatted list of recent heartbeat events
    """
    from services.database import HeartbeatEventModel, async_session
    from sqlalchemy import select, desc

    async with async_session() as session:
        query = select(HeartbeatEventModel).order_by(desc(HeartbeatEventModel.created_at)).limit(limit)

        if sender:
            query = query.where(HeartbeatEventModel.sender.ilike(f"%{sender}%"))
        if status_filter:
            query = query.where(HeartbeatEventModel.triage_status == status_filter)

        result = await session.execute(query)
        events = result.scalars().all()

    if not events:
        filter_desc = ""
        if sender:
            filter_desc += f" from '{sender}'"
        if status_filter:
            filter_desc += f" with status '{status_filter}'"
        return f"No heartbeat events found{filter_desc}."

    lines = [f"Found {len(events)} recent event(s):\n"]
    for e in events:
        time_str = e.created_at.strftime("%b %d, %I:%M %p") if e.created_at else "unknown"
        direction = "outgoing" if e.is_from_user else "incoming"
        sender_str = e.sender or "unknown"
        summary_str = e.summary or "(no summary)"
        chat_str = f" in {e.chat_name}" if e.chat_name else ""
        lines.append(f"- [{e.triage_status}] {time_str} — {direction} from {sender_str}{chat_str}: {summary_str}")

    return "\n".join(lines)


HEARTBEAT_TOOLS = [review_heartbeat]

HEARTBEAT_TOOL_NAMES = {"review_heartbeat"}


def get_heartbeat_tools_description() -> str:
    """Get a description of available heartbeat tools for the system prompt."""
    return """
## Heartbeat (Background Awareness)

You have a heartbeat system that passively monitors incoming and outgoing iMessages.
Messages are triaged automatically (dismiss/note/act/escalate).

1. **review_heartbeat(sender?, status_filter?, limit?)**: Review recent messages you've noticed.
   - Filter by sender (partial match on phone/email)
   - Filter by triage status: "pending", "dismissed", "noted", "acted", "escalated"
   - Use this when the user asks what you've noticed or about recent messages from someone
"""




def get_scheduled_event_tools_description() -> str:
    """Get a description of available scheduled event tools for the system prompt."""
    return """
## Scheduled Events

Schedule future actions, reminders, and recurring tasks that fire automatically.

- **schedule_event**: Set a future action. Use user's LOCAL time (not UTC). Cron syntax for recurring.
- **list_scheduled_events / cancel_scheduled_event**: Manage existing events.

**CRITICAL**: Events run in an EPHEMERAL conversation with NO memory of the original context. The description must contain EVERYTHING needed: exact message text, database names, table and column names, phone numbers, calculations.

Bad: "Check on the dog's meds and track the response."
Good: "Send push notification 'Did you give the morning meds?' If YES, log to pet_tracker DB, medication_log table: INSERT INTO medication_log (date, given) VALUES (CURRENT_DATE, true)."

Events do NOT appear in the original chat. Use local time, standard cron syntax for recurring.
"""


# ============================================================================
# PUSH NOTIFICATION TOOLS
# ============================================================================

@tool
async def send_push_notification(
    title: str,
    body: str,
    url: Optional[str] = None,
) -> str:
    """
    Send a push notification to the user's devices.

    Use this when you need to proactively alert the user about something
    important, even when they're not actively using the app. Push notifications
    appear on the user's phone or computer.

    Best used for:
    - Urgent alerts that need immediate attention
    - Reminders when the user might not be checking the app
    - Important status updates

    Args:
        title: Short notification title (keep under 50 chars)
        body: Notification body text (keep under 100 chars for best display)
        url: Optional URL to open when notification is clicked

    Returns:
        Confirmation with delivery status
    """
    from services.push_service import send_push_notification as send_push, is_configured
    from services.conversation_service import mark_user_notified

    if not is_configured():
        return "Push notifications not configured. VAPID keys are not set."

    # If no explicit URL provided, link to the current conversation
    conversation_id = get_current_conversation_id()
    if not url and conversation_id:
        url = f"/?c={conversation_id}"

    try:
        result = await send_push(
            title=title,
            body=body,
            url=url or "/",
            tag="edward-proactive",
        )

        if result.get("error"):
            return f"Push notification failed: {result['error']}"

        sent = result.get("sent", 0)
        failed = result.get("failed", 0)
        total = result.get("total", 0)

        if total == 0:
            return "No active push subscriptions. User has not enabled notifications."

        if sent > 0:
            # Mark this conversation as having notified the user (for filtering)
            if conversation_id:
                await mark_user_notified(conversation_id)
            return f"Push notification sent to {sent} device(s)."
        else:
            return f"Push notification failed to deliver to {failed} device(s)."

    except Exception as e:
        return f"Push notification error: {str(e)}"


# List of all push notification tools
PUSH_NOTIFICATION_TOOLS = [send_push_notification]


def get_push_notification_tools_description() -> str:
    """Get a description of available push notification tools for the system prompt."""
    return """
## Push Notifications

- **send_push_notification**: Send push notification to user's devices. Use sparingly -- only for important alerts that need immediate attention. Works even when user isn't in the app.
"""


def get_all_tools_description() -> str:
    """Get description of all available tools."""
    return (
        get_memory_tools_description() +
        get_document_tools_description() +
        get_file_storage_tools_description() +
        get_plan_tools_description() +
        get_scheduled_event_tools_description() +
        get_contacts_tools_description() +
        get_messaging_tools_description() +
        get_search_tools_description() +
        get_code_execution_tools_description() +
        get_javascript_execution_tools_description() +
        get_sql_execution_tools_description() +
        get_shell_execution_tools_description() +
        get_push_notification_tools_description() +
        get_html_hosting_tools_description()
    )


# ============================================================================
# DOCUMENT STORE TOOLS
# ============================================================================

@tool
async def save_document(title: str, content: str, tags: Optional[str] = None) -> str:
    """
    Save a new document to the persistent document store.

    Use this when the user wants to store a full document for long-term reference,
    such as recipes, meeting notes, reference guides, pet records, instructions,
    or any text that's too large or structured for a simple memory.

    Args:
        title: A descriptive title for the document
        content: The full document content (markdown supported)
        tags: Optional comma-separated tags for categorization (e.g. "recipe,cooking,italian")

    Returns:
        Confirmation with the document ID
    """
    from services.document_service import save_document as _save, Document

    conversation_id = get_current_conversation_id()

    doc = Document(
        id=None,
        title=title,
        content=content,
        tags=tags,
        source_conversation_id=conversation_id,
    )

    saved = await _save(doc)
    tag_info = f" (tags: {tags})" if tags else ""
    return f"Document saved: \"{saved.title}\"{tag_info} (ID: {saved.id})"


@tool
async def read_document(document_id: str) -> str:
    """
    Read the full content of a document from the store.

    Use this when you see a relevant document title in your context and need
    to read the full content. Also use when the user asks to see a stored document.

    Args:
        document_id: The document ID

    Returns:
        Full document content with title and tags
    """
    from services.document_service import get_document_by_id

    doc = await get_document_by_id(document_id)
    if not doc:
        return f"Document {document_id} not found."

    parts = [f"# {doc.title}"]
    if doc.tags:
        parts.append(f"Tags: {doc.tags}")
    parts.append("")
    parts.append(doc.content)
    return "\n".join(parts)


@tool
async def edit_document(
    document_id: str,
    title: Optional[str] = None,
    content: Optional[str] = None,
    tags: Optional[str] = None,
) -> str:
    """
    Edit an existing document. Only provide fields you want to change.

    Use this when the user wants to update a stored document's content, title, or tags.

    Args:
        document_id: The document ID to edit
        title: New title (optional)
        content: New content (optional)
        tags: New tags, comma-separated (optional)

    Returns:
        Confirmation message
    """
    from services.document_service import update_document

    updated = await update_document(
        document_id=document_id,
        title=title,
        content=content,
        tags=tags,
    )

    if not updated:
        return f"Document {document_id} not found."

    return f"Document updated: \"{updated.title}\" (ID: {updated.id})"


@tool
async def search_documents(query: str, tags: Optional[str] = None) -> str:
    """
    Search for documents by semantic similarity and keywords.

    Use this when looking for stored documents on a topic, or when the user
    asks about documents they've saved.

    Args:
        query: Search query describing what to find
        tags: Optional comma-separated tags to filter by

    Returns:
        List of matching documents with titles and IDs
    """
    from services.document_service import search_documents as _search

    docs, total = await _search(query=query, tags=tags, limit=10)

    if not docs:
        return f"No documents found matching '{query}'."

    lines = [f"Found {total} document(s) matching '{query}':\n"]
    for d in docs:
        tag_info = f" [{d.tags}]" if d.tags else ""
        score_info = f" (score: {d.score:.2f})" if d.score > 0 else ""
        preview = d.content[:100].replace("\n", " ") + ("..." if len(d.content) > 100 else "")
        lines.append(f"- **{d.title}**{tag_info}{score_info} (ID: {d.id})")
        lines.append(f"  {preview}")

    return "\n".join(lines)


@tool
async def list_documents(tags: Optional[str] = None) -> str:
    """
    List all documents in the store.

    Use this when the user wants to see what documents are saved,
    or to browse by tag.

    Args:
        tags: Optional comma-separated tags to filter by

    Returns:
        List of all documents with titles, tags, and IDs
    """
    from services.document_service import list_documents as _list

    docs, total = await _list(limit=20, tags=tags)

    if not docs:
        filter_info = f" with tags '{tags}'" if tags else ""
        return f"No documents found{filter_info}."

    lines = [f"Found {total} document(s):\n"]
    for d in docs:
        tag_info = f" [{d.tags}]" if d.tags else ""
        date_info = d.updated_at.strftime("%b %d, %Y") if d.updated_at else "N/A"
        lines.append(f"- **{d.title}**{tag_info} — updated {date_info} (ID: {d.id})")

    return "\n".join(lines)


@tool
async def delete_document(document_id: str) -> str:
    """
    Delete a document from the store.

    Use this when the user explicitly asks to remove a stored document.

    Args:
        document_id: The document ID to delete

    Returns:
        Confirmation message
    """
    from services.document_service import delete_document as _delete

    deleted = await _delete(document_id)
    if deleted:
        return f"Document {document_id} deleted."
    return f"Document {document_id} not found."


# List of all document tools
DOCUMENT_TOOLS = [save_document, read_document, edit_document, search_documents, list_documents, delete_document]

DOCUMENT_TOOL_NAMES = {"save_document", "read_document", "edit_document", "search_documents", "list_documents", "delete_document"}


def get_document_tools_description() -> str:
    """Get a description of available document tools for the system prompt."""
    return """
## Document Store

Persistent storage for full documents -- recipes, notes, guides, reference material.

- **save_document / read_document / edit_document / search_documents / list_documents / delete_document**

When relevant documents appear in context (titles only), use read_document to fetch full content.

**Knowledge layers**: Memories = short facts (auto-extracted). Documents = full text (explicit save). Notebooks = multi-source research (cross-referenced with citations).
"""


# ============================================================================
# HTML HOSTING TOOLS
# ============================================================================

@tool
async def create_hosted_page(
    html: str,
    slug: Optional[str] = None,
    description: Optional[str] = None,
    duration: Optional[str] = None,
) -> str:
    """
    Publish an HTML page to html.zyroi.com.

    Use this when the user asks you to create a web page, landing page,
    progress tracker, or any visual content that should be accessible via URL.

    The HTML should be complete and valid with inline CSS for single-page sites.
    Include <!DOCTYPE html>, <html>, <head>, and <body> tags. Use inline styles
    or <style> blocks — external stylesheets won't load.

    Args:
        html: Complete HTML content (must include proper HTML structure)
        slug: Optional custom URL slug (e.g. "my-project"). Auto-generated if not provided.
        description: Optional description of the page
        duration: Optional expiry: "1day", "30days", "6months", or "permanent" (default: permanent)

    Returns:
        URL of the published page with slug and expiry info
    """
    from services.html_hosting_service import create_page, is_configured

    if not is_configured():
        return "HTML hosting not available. HTML_HOSTING_API_KEY is not set."

    try:
        result = await create_page(
            html=html,
            slug=slug,
            description=description,
            duration=duration or "permanent",
        )

        if not result.get("success"):
            return f"Failed to create page: {result.get('error', 'Unknown error')}"

        expiry_info = "permanent" if result.get("permanent") else f"expires {result.get('expiresAt', 'unknown')}"
        return f"Page published!\n\nURL: {result['url']}\nSlug: {result['slug']}\nExpiry: {expiry_info}"
    except Exception as e:
        return f"Failed to create hosted page: {str(e)}"


@tool
async def update_hosted_page(
    slug: str,
    html: str,
    description: Optional[str] = None,
    duration: Optional[str] = None,
) -> str:
    """
    Update an existing hosted page on html.zyroi.com.

    Use this to update the content of a page you previously created.
    You must be the owner of the page (created with the same API key).

    Args:
        slug: The URL slug of the page to update
        html: New complete HTML content
        description: Optional new description
        duration: Optional new expiry: "1day", "30days", "6months", or "permanent"

    Returns:
        Confirmation with updated page details
    """
    from services.html_hosting_service import update_page, is_configured

    if not is_configured():
        return "HTML hosting not available. HTML_HOSTING_API_KEY is not set."

    try:
        result = await update_page(
            slug=slug,
            html=html,
            description=description,
            duration=duration,
        )

        if not result.get("success"):
            return f"Failed to update page: {result.get('error', 'Unknown error')}"

        return f"Page updated!\n\nURL: {result['url']}\nSlug: {result['slug']}"
    except Exception as e:
        return f"Failed to update hosted page: {str(e)}"


@tool
async def delete_hosted_page(slug: str) -> str:
    """
    Delete a hosted page from html.zyroi.com.

    Use this to remove a page you previously created. This action is permanent.

    Args:
        slug: The URL slug of the page to delete

    Returns:
        Confirmation message
    """
    from services.html_hosting_service import delete_page, is_configured

    if not is_configured():
        return "HTML hosting not available. HTML_HOSTING_API_KEY is not set."

    try:
        result = await delete_page(slug)

        if not result.get("success"):
            return f"Failed to delete page: {result.get('error', 'Unknown error')}"

        return f"Page '{slug}' has been deleted."
    except Exception as e:
        return f"Failed to delete hosted page: {str(e)}"


@tool
async def check_hosted_slug(slug: str) -> str:
    """
    Check if a URL slug is available on html.zyroi.com.

    Use this before creating a page with a specific slug to verify availability.

    Args:
        slug: The desired URL slug to check

    Returns:
        Whether the slug is available
    """
    from services.html_hosting_service import check_slug, is_configured

    if not is_configured():
        return "HTML hosting not available. HTML_HOSTING_API_KEY is not set."

    try:
        result = await check_slug(slug)

        if result.get("available"):
            return f"Slug '{slug}' is available!"
        else:
            reason = result.get("reason", "unknown reason")
            return f"Slug '{slug}' is not available: {reason}"
    except Exception as e:
        return f"Failed to check slug: {str(e)}"


# List of all HTML hosting tools
HTML_HOSTING_TOOLS = [create_hosted_page, update_hosted_page, delete_hosted_page, check_hosted_slug]

HTML_HOSTING_TOOL_NAMES = {"create_hosted_page", "update_hosted_page", "delete_hosted_page", "check_hosted_slug"}


# ============================================================================
# iOS WIDGET TOOLS
# ============================================================================

@tool
async def update_widget(
    sections: List[Dict],
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
    background_color: Optional[str] = None,
    text_color: Optional[str] = None,
    secondary_text_color: Optional[str] = None,
    accent_color: Optional[str] = None,
) -> str:
    """
    Update the user's iOS home screen widget content.

    This saves structured data to the database. A fixed Scriptable renderer on the
    user's iPhone fetches this data and displays it. You are NOT generating JavaScript
    or modifying the renderer — you can only use the section types listed below.

    Widget sizes in points (width × height) on a typical iPhone:
      - small: ~170 × 170, medium: ~338 × 170, large: ~338 × 354
    The large widget is compact — roughly full screen width by half screen height.

    Available section types (these are ALL you can use — no custom UI):
    - header: {type, title, icon?} — icon: named ("calendar","star","bell","clock","check","pin","heart","fire","brain","rocket","memo","chart","flag","bolt","target") or any emoji
    - text: {type, content} — plain text, max 3 lines displayed
    - list: {type, items: [{label, detail?, icon?}]} — max 3 items shown
    - stat: {type, label, value, icon?}
    - stats_row: {type, stats: [{label, value}]} — horizontal stat columns
    - progress: {type, label, value} — value 0.0-1.0, shows percentage + bar
    - countdown: {type, label, target_date} — "YYYY-MM-DD", auto-calculates days remaining
    - divider: {type} — horizontal line
    - spacer: {type} — small vertical gap

    Args:
        sections: List of section dicts to display (only the types above are supported)
        title: Widget title (default: "Edward")
        subtitle: Subtitle text below title (hidden on small widgets)
        background_color: Hex color for background (e.g. "#1a1a2e"). No transparency.
        text_color: Hex color for primary text (e.g. "#ffffff")
        secondary_text_color: Hex color for secondary text (e.g. "#a0a0a0")
        accent_color: Hex color for accents and highlights (e.g. "#00d4aa")

    Returns:
        Confirmation message
    """
    from services.widget_service import update_widget_state

    theme = None
    if any([background_color, text_color, secondary_text_color, accent_color]):
        theme = {}
        if background_color:
            theme["backgroundColor"] = background_color
        if text_color:
            theme["textColor"] = text_color
        if secondary_text_color:
            theme["secondaryTextColor"] = secondary_text_color
        if accent_color:
            theme["accentColor"] = accent_color

    try:
        state = await update_widget_state(
            sections=sections,
            title=title,
            subtitle=subtitle,
            theme=theme,
        )
        section_count = len(state.get("sections", []))
        return f"Widget updated with {section_count} section(s). Title: \"{state['title']}\""
    except Exception as e:
        return f"Failed to update widget: {str(e)}"


@tool
async def get_widget_state_tool() -> str:
    """
    Get the current state of the iOS home screen widget.

    Use this to check what's currently displayed on the widget before making changes.

    Returns:
        JSON representation of the current widget state
    """
    import json
    from services.widget_service import get_widget_state

    try:
        state = await get_widget_state()
        return json.dumps(state, indent=2)
    except Exception as e:
        return f"Failed to get widget state: {str(e)}"


@tool
async def update_widget_code(code: str) -> str:
    """
    Update the iOS widget by providing raw Scriptable JavaScript code.

    Use this instead of update_widget when you need full creative control — custom
    drawing, gradients, SF Symbols, transparency, images, complex layouts, etc.

    Your code runs inside the Scriptable app on iOS. You must create and configure
    a ListWidget, then call Script.setWidget(widget) and Script.complete().

    IMPORTANT rules:
    - You MUST assign your widget to a variable called `widget` at the top level.
    - You MUST call Script.setWidget(widget) and Script.complete() at the end.
    - The variable `config.widgetFamily` tells you the size: "small", "medium", or "large".
    - Actual widget sizes in points (width × height) on a typical iPhone:
        - small: ~170 × 170
        - medium: ~338 × 170
        - large: ~338 × 354
      These are compact — the large widget is roughly the full screen width by half the screen height.
      Design accordingly with appropriately sized fonts and spacing.
    - Do NOT use `import` or `require` — Scriptable has its own built-in APIs.
    - Do NOT call any alert/prompt dialogs — they block the widget.
    - Avoid network requests that might be slow — the widget has limited execution time.
    - If your code throws an error, the widget falls back to showing "Edward" with the error message.

    Key Scriptable APIs you can use:
    - ListWidget: Main widget container. Methods: addText(), addStack(), addImage(), addSpacer(), addDate()
      Properties: backgroundColor, backgroundGradient, backgroundImage, setPadding(top,left,bottom,right),
      refreshAfterDate, url (tap URL)
    - WidgetStack: Layout container from addStack(). Same methods as ListWidget plus:
      layoutHorizontally(), layoutVertically(), centerAlignContent(), topAlignContent(),
      bottomAlignContent(), spacing, size, cornerRadius, borderWidth, borderColor,
      backgroundColor, backgroundGradient, url
    - WidgetText: From addText(string). Properties: font, textColor, textOpacity, lineLimit,
      minimumScaleFactor, centerAlignText(), leftAlignText(), rightAlignText()
    - WidgetDate: From addDate(date). Same styling as WidgetText plus:
      .applyTimerStyle(), .applyRelativeStyle(), .applyOffsetStyle(), .applyDateStyle(), .applyTimeStyle()
    - WidgetImage: From addImage(image). Properties: imageSize, imageOpacity, cornerRadius,
      resizable, tintColor, containerRelativeShape, contentMode (fitted/filling)
    - Font: Font.boldSystemFont(size), Font.systemFont(size), Font.thinSystemFont(size),
      Font.lightSystemFont(size), Font.regularSystemFont(size), Font.mediumSystemFont(size),
      Font.semiboldSystemFont(size), Font.heavySystemFont(size), Font.blackSystemFont(size),
      Font.italicSystemFont(size), Font.ultraLightSystemFont(size)
    - Color: new Color(hex, alpha), Color.clear(), Color.white(), Color.black(),
      Color.red(), Color.green(), Color.blue(), Color.yellow(), Color.orange(), Color.purple(),
      Color.gray(), Color.lightGray(), Color.darkGray(), Color.cyan(), Color.magenta(), Color.brown()
    - LinearGradient: let g = new LinearGradient();
      g.colors = [new Color("ff0000"), new Color("0000ff")];
      g.locations = [0, 1]; g.startPoint = new Point(0, 0); g.endPoint = new Point(1, 1);
      widget.backgroundGradient = g;
    - DrawContext: For custom 2D drawing. new DrawContext(), .size, .opaque, .respectScreenScale,
      .setFillColor(), .fillRect(), .fillEllipse(), .setStrokeColor(), .strokeRect(),
      .setFont(), .drawText(), .drawTextInRect(), .getImage()
    - SFSymbol: SFSymbol.named("symbolname"), then use .image property with addImage()
    - Image: Image.fromFile(path), Image.fromData(data)
    - Size: new Size(width, height)
    - Point: new Point(x, y)
    - Rect: new Rect(x, y, width, height)

    Example — transparent widget with gradient text:
    ```
    let w = new ListWidget()
    w.backgroundColor = Color.clear()
    let title = w.addText("Edward")
    title.font = Font.boldSystemFont(24)
    title.textColor = new Color("ffffff")
    w.addSpacer()
    let sub = w.addText("Your AI assistant")
    sub.font = Font.systemFont(12)
    sub.textColor = new Color("aaaaaa")
    Script.setWidget(w)
    Script.complete()
    ```

    Args:
        code: Complete Scriptable JavaScript code that creates and sets a ListWidget.

    Returns:
        Confirmation message
    """
    from services.widget_service import update_widget_script

    try:
        state = await update_widget_script(code)
        return f"Widget code updated. The widget will show your custom code on next refresh (~15 min)."
    except Exception as e:
        return f"Failed to update widget code: {str(e)}"


@tool
async def clear_widget_code() -> str:
    """
    Clear custom widget code and revert to structured data mode.

    Use this if the custom code is broken or you want to go back to using
    update_widget with structured sections instead.

    Returns:
        Confirmation message
    """
    from services.widget_service import clear_widget_script

    try:
        await clear_widget_script()
        return "Custom widget code cleared. Widget will revert to structured data or default content."
    except Exception as e:
        return f"Failed to clear widget code: {str(e)}"


# List of all widget tools
WIDGET_TOOLS = [update_widget, update_widget_code, clear_widget_code, get_widget_state_tool]

WIDGET_TOOL_NAMES = {"update_widget", "update_widget_code", "clear_widget_code", "get_widget_state_tool"}


# ============================================================================
# FILE STORAGE TOOLS
# ============================================================================

@tool
async def save_to_storage(
    source_file: str,
    filename: Optional[str] = None,
    category: str = "generated",
    description: Optional[str] = None,
    tags: Optional[str] = None,
) -> str:
    """
    Save a file from the code execution sandbox to persistent storage.

    Use this after generating files (plots, reports, processed data) to make
    them available across conversations. The file will get a permanent download URL.

    Args:
        source_file: Filename in the sandbox (e.g. "plot.png", "report.csv")
        filename: Optional custom name for the stored file (defaults to source_file name)
        category: File category: "generated" (default), "artifact", "processed", or "general"
        description: Optional description of what the file contains
        tags: Optional comma-separated tags (e.g. "chart,analysis,weekly")

    Returns:
        Confirmation with file ID and download URL
    """
    from services.file_storage_service import move_sandbox_file_to_storage

    conversation_id = get_current_conversation_id()
    if not conversation_id:
        return "Error: No conversation context available."

    stored = await move_sandbox_file_to_storage(
        conversation_id=conversation_id,
        sandbox_filename=source_file,
        category=category,
        description=description,
        tags=tags,
    )

    if not stored:
        return f"File '{source_file}' not found in sandbox. Use list_sandbox_files() to see available files."

    return (
        f"File saved to storage: \"{stored.filename}\" ({stored.mime_type}, {stored.size_bytes} bytes)\n"
        f"ID: {stored.id}\n"
        f"Download: [{stored.filename}](/api/files/{stored.id}/download)"
    )


@tool
async def list_storage_files(
    category: Optional[str] = None,
    tags: Optional[str] = None,
) -> str:
    """
    List files in persistent storage.

    Use this to browse stored files (uploaded, generated, artifacts).

    Args:
        category: Optional filter: "upload", "generated", "artifact", "processed", "general"
        tags: Optional comma-separated tags to filter by

    Returns:
        List of stored files with IDs and details
    """
    from services.file_storage_service import list_files

    files, total = await list_files(category=category, tags=tags, limit=20)

    if not files:
        filter_info = ""
        if category:
            filter_info += f" in category '{category}'"
        if tags:
            filter_info += f" with tags '{tags}'"
        return f"No files found{filter_info}."

    lines = [f"Found {total} file(s):\n"]
    for f in files:
        size_str = f"{f.size_bytes / 1024:.1f} KB" if f.size_bytes < 1024 * 1024 else f"{f.size_bytes / (1024*1024):.1f} MB"
        tag_info = f" [{f.tags}]" if f.tags else ""
        desc_info = f" - {f.description}" if f.description else ""
        date_info = f.created_at.strftime("%b %d, %Y") if f.created_at else "N/A"
        lines.append(f"- **{f.filename}** ({f.mime_type}, {size_str}){tag_info}{desc_info}")
        lines.append(f"  Source: {f.source} | Created: {date_info} | ID: {f.id}")

    return "\n".join(lines)


@tool
async def get_storage_file_url(file_id: str) -> str:
    """
    Get the download URL for a stored file.

    Use this to provide the user with a link to download a file.

    Args:
        file_id: The file ID

    Returns:
        Download URL or error message
    """
    from services.file_storage_service import get_file

    f = await get_file(file_id)
    if not f:
        return f"File {file_id} not found."

    return f"Download: [{f.filename}](/api/files/{f.id}/download)"


@tool
async def read_storage_file(file_id: str) -> str:
    """
    Read the text content of a stored file.

    Only works for text-based files (plain text, CSV, JSON, HTML, etc.).
    For binary files (images, PDFs), use get_storage_file_url instead.

    Args:
        file_id: The file ID

    Returns:
        File contents as text, or error message
    """
    from services.file_storage_service import read_text_file, get_file

    f = await get_file(file_id)
    if not f:
        return f"File {file_id} not found."

    content = await read_text_file(file_id)
    if content is None:
        return f"File \"{f.filename}\" ({f.mime_type}) is not a text file. Use get_storage_file_url to get a download link."

    # Truncate very long files
    if len(content) > 10000:
        return content[:10000] + "\n... [file truncated]"
    return content


@tool
async def tag_storage_file(
    file_id: str,
    description: Optional[str] = None,
    tags: Optional[str] = None,
    category: Optional[str] = None,
) -> str:
    """
    Update metadata (description, tags, category) on a stored file.

    Use this to annotate uploaded files so they can be found later.
    Prefer tagging the original file over creating a separate document copy.

    Args:
        file_id: The file ID to update
        description: Human-readable description of the file
        tags: Comma-separated tags (e.g. "medical,pet,buddy")
        category: Optional category: "upload", "generated", "artifact", "processed", "general"

    Returns:
        Confirmation message with updated fields
    """
    from services.file_storage_service import update_file_metadata, get_file

    f = await get_file(file_id)
    if not f:
        return f"File {file_id} not found."

    updated = await update_file_metadata(
        file_id=file_id,
        description=description,
        tags=tags,
        category=category,
    )
    if not updated:
        return f"Failed to update file {file_id}."

    parts = [f"Updated \"{updated.filename}\":"]
    if description is not None:
        parts.append(f"  Description: {description}")
    if tags is not None:
        parts.append(f"  Tags: {tags}")
    if category is not None:
        parts.append(f"  Category: {category}")
    return "\n".join(parts)


@tool
async def delete_storage_file(file_id: str) -> str:
    """
    Delete a file from persistent storage.

    Use this when the user explicitly asks to remove a stored file.

    Args:
        file_id: The file ID to delete

    Returns:
        Confirmation message
    """
    from services.file_storage_service import delete_file

    deleted = await delete_file(file_id)
    if deleted:
        return f"File {file_id} deleted."
    return f"File {file_id} not found."


# List of all file storage tools
FILE_STORAGE_TOOLS = [save_to_storage, list_storage_files, get_storage_file_url, read_storage_file, delete_storage_file, tag_storage_file]

FILE_STORAGE_TOOL_NAMES = {"save_to_storage", "list_storage_files", "get_storage_file_url", "read_storage_file", "delete_storage_file", "tag_storage_file"}


def get_file_storage_tools_description() -> str:
    """Get a description of available file storage tools for the system prompt."""
    return """
## File Storage

Persistent file storage across conversations. Sandbox files are ephemeral -- use save_to_storage to persist them.

- **save_to_storage**: Move sandbox file to permanent storage. Returns download URL.
- **list_storage_files / get_storage_file_url / read_storage_file / delete_storage_file / tag_storage_file**

When users upload files, tag them with description and tags so they can be found later. Files can be pushed to NotebookLM notebooks as sources via nlm_push_file.
"""


def get_html_hosting_tools_description() -> str:
    """Get a description of available HTML hosting tools for the system prompt."""
    return """
## HTML Hosting

Create, update, and delete hosted HTML pages on html.zyroi.com.

- **create_hosted_page**: Publish self-contained HTML. Use descriptive slugs (e.g. "my-project-tracker").
- **update_hosted_page**: Update an existing page by slug.
- **delete_hosted_page / check_hosted_slug**: Manage pages and check slug availability.

Pages must be complete, valid HTML with inline CSS (external stylesheets won't load). Default: permanent hosting.
"""


def get_widget_tools_description() -> str:
    """Get a description of available widget tools for the system prompt."""
    return """
## iOS Widget (Scriptable)

Controls what appears on the user's iOS home screen widget. Two modes:

**Structured mode** (update_widget): Set content via sections (header, text, list, stat, progress, countdown, etc.). Good for quick info.

**Raw code mode** (update_widget_code): Write Scriptable JavaScript for full creative control -- gradients, SF Symbols, custom drawing. **Preferred for most updates.**

- **get_widget_state_tool**: Read current widget state.
- **clear_widget_code**: Revert to structured mode.

Widget is LARGE (~360x376pt). Fill the entire area. Refreshes every ~15 min. Update proactively after scheduling events, completing tasks, or when something interesting is worth surfacing.
"""


# ============================================================================
# CLAUDE CODE TOOLS
# ============================================================================

# ============================================================================
# EVOLUTION TOOLS
# ============================================================================

@tool
async def trigger_self_evolution(description: str) -> str:
    """
    Trigger a self-evolution cycle to modify Edward's own codebase.

    This launches a full evolution pipeline:
    1. Creates a git branch
    2. Uses Claude Code to implement the change
    3. Validates no protected files were modified
    4. Runs tests (lint + import check)
    5. Reviews the diff with a separate Claude Code session
    6. Merges to main (triggers auto-reload via uvicorn --reload)

    IMPORTANT: Evolution must be enabled in config before use.
    Protected files (auth, evolution service, .env) cannot be modified.

    Args:
        description: Detailed description of the change to make.
            Be specific about what to modify and the expected behavior.

    Returns:
        Result summary of the evolution cycle
    """
    from services.evolution_service import evolve, can_evolve

    ok, reason = await can_evolve()
    if not ok:
        return f"Cannot evolve: {reason}"

    conversation_id = get_current_conversation_id()

    try:
        result = await evolve(
            description=description,
            trigger="llm",
            conversation_id=conversation_id,
        )
        return result
    except Exception as e:
        return f"Evolution failed: {str(e)}"


@tool
async def get_evolution_status() -> str:
    """
    Get the current status of the self-evolution engine.

    Shows whether evolution is enabled, the current active cycle (if any),
    recent history, and configuration.

    Returns:
        Formatted status information
    """
    from services.evolution_service import get_status, get_history

    try:
        status = await get_status()
        history = await get_history(limit=5)

        parts = [
            f"Evolution Engine: {'ENABLED' if status['config']['enabled'] else 'DISABLED'}",
            f"Auto-trigger: {'ON' if status['config']['auto_trigger'] else 'OFF'}",
            f"Require tests: {'YES' if status['config']['require_tests'] else 'NO'}",
            f"Rate limit: {status['config']['min_interval_seconds']}s between cycles",
        ]

        if status.get("current_cycle"):
            cycle = status["current_cycle"]
            parts.append(f"\nActive cycle: {cycle['id'][:8]}... [{cycle['status']}]")
            parts.append(f"  Step: {cycle.get('step', 'unknown')}")
            parts.append(f"  Description: {cycle['description'][:100]}")

        if history:
            parts.append(f"\nRecent history ({len(history)} cycles):")
            for h in history:
                dur = f" ({h['duration_ms']}ms)" if h.get('duration_ms') else ""
                parts.append(f"  - [{h['status']}] {h['description'][:80]}{dur}")

        return "\n".join(parts)
    except Exception as e:
        return f"Failed to get evolution status: {str(e)}"


EVOLUTION_TOOLS = [trigger_self_evolution, get_evolution_status]

EVOLUTION_TOOL_NAMES = {"trigger_self_evolution", "get_evolution_status"}


def get_evolution_tools_description() -> str:
    """Get a description of evolution tools for the system prompt."""
    return """
## Self-Evolution

- **trigger_self_evolution**: Modify your own codebase. Creates branch, implements via Claude Code, validates, tests, reviews, merges. Must be enabled in config first.
- **get_evolution_status**: Check config and recent history.

Consider evolution when you encounter recurring limitations that a code change could fix.
"""


# ============================================================================
# ORCHESTRATOR TOOLS
# ============================================================================

@tool
async def spawn_worker(
    task: str,
    model: Optional[str] = None,
    context_mode: str = "scoped",
    context_data: Optional[str] = None,
    wait: bool = False,
) -> str:
    """
    Spawn a background worker agent to handle a sub-task in parallel.

    Use this when you have a complex goal that can be decomposed into independent
    sub-tasks. Each worker is a mini-Edward with full tool access (memory, messaging,
    search, code execution, etc.) but cannot spawn its own workers.

    Workers run in their own conversation (visible in sidebar with purple icon).
    Prefer wait=False when the task is likely to take more than one quick turn,
    especially for multi-step research, long analysis, or follow-up polling.
    Use check_worker() or wait_for_workers() to get results later.

    Args:
        task: Clear description of what the worker should do. Be specific!
        model: Model to use — "haiku" (fast/cheap), "sonnet" (balanced), "opus" (powerful).
               Defaults to config setting. Use haiku for simple lookups, sonnet for analysis.
        context_mode: "full" (all memory + system prompt), "scoped" (minimal + context_data),
                      "none" (just the task). Default "scoped".
        context_data: Extra context to provide in scoped mode (e.g., relevant facts, data)
        wait: If True, block until worker completes and return result inline.
              If False (default), return task ID immediately for async checking.
              Prefer False for long-running or iterative work.

    Returns:
        If wait=True: the worker's result summary
        If wait=False: task ID and status for later checking
    """
    conversation_id = get_current_conversation_id()
    if not conversation_id:
        return "Error: No conversation context available."

    from services.orchestrator_service import spawn_worker as _spawn

    result = await _spawn(
        parent_conversation_id=conversation_id,
        task_description=task,
        model=model,
        context_mode=context_mode,
        context_data=context_data,
        wait=wait,
    )

    if result.get("error"):
        return f"Error: {result['error']}"

    if wait:
        # Return the result directly
        status = result.get("status", "unknown")
        if status == "completed":
            return result.get("result_summary", "Worker completed with no output.")
        elif status == "failed":
            return f"Worker failed: {result.get('error', 'Unknown error')}"
        else:
            return f"Worker ended with status: {status}"

    return f"Worker spawned. Task ID: {result['id']}\nStatus: {result['status']}\nUse check_worker('{result['id']}') to check progress."


@tool
async def check_worker(task_id: str) -> str:
    """
    Check the status and result of a spawned worker.

    Args:
        task_id: The task ID returned by spawn_worker

    Returns:
        Status and result summary of the worker
    """
    from services.orchestrator_service import get_task

    result = await get_task(task_id)
    if result.get("error"):
        return f"Error: {result['error']}"

    status = result["status"]
    desc = result["task_description"][:80]
    output = []
    output.append(f"Task: {desc}")
    output.append(f"Status: {status}")
    output.append(f"Model: {result['model']}")

    if result.get("result_summary"):
        output.append(f"Result: {result['result_summary']}")
    if result.get("error"):
        output.append(f"Error: {result['error']}")
    if result.get("worker_conversation_id"):
        output.append(f"Worker conversation: {result['worker_conversation_id']}")

    return "\n".join(output)


@tool
async def list_workers(status: Optional[str] = None) -> str:
    """
    List workers spawned from the current conversation.

    Args:
        status: Optional filter — "pending", "running", "completed", "failed", "cancelled"

    Returns:
        Formatted list of workers with their status
    """
    conversation_id = get_current_conversation_id()
    if not conversation_id:
        return "Error: No conversation context available."

    from services.orchestrator_service import list_tasks

    tasks = await list_tasks(parent_conversation_id=conversation_id, status=status)

    if not tasks:
        filter_desc = f" with status '{status}'" if status else ""
        return f"No workers found{filter_desc}."

    lines = [f"Found {len(tasks)} worker(s):\n"]
    for t in tasks:
        desc = t["task_description"][:60]
        summary = ""
        if t.get("result_summary"):
            summary = f" → {t['result_summary'][:80]}"
        elif t.get("error"):
            summary = f" → Error: {t['error'][:80]}"
        lines.append(f"- [{t['status']}] {desc}{summary} (ID: {t['id'][:8]}...)")

    return "\n".join(lines)


@tool
async def cancel_worker(task_id: str) -> str:
    """
    Cancel a running worker.

    Args:
        task_id: The task ID of the worker to cancel

    Returns:
        Confirmation message
    """
    from services.orchestrator_service import cancel_task

    result = await cancel_task(task_id)
    if result.get("error"):
        return f"Error: {result['error']}"
    return f"Worker {task_id[:8]}... cancelled. Status: {result['status']}"


@tool
async def wait_for_workers(task_ids: str) -> str:
    """
    Wait for one or more workers to complete and return their results.

    Args:
        task_ids: Comma-separated task IDs to wait for

    Returns:
        Results from all workers
    """
    ids = [tid.strip() for tid in task_ids.split(",") if tid.strip()]
    if not ids:
        return "Error: No task IDs provided."

    from services.orchestrator_service import wait_for_tasks

    results = await wait_for_tasks(ids)

    lines = []
    for r in results:
        if r.get("error") and not r.get("status"):
            lines.append(f"- Error: {r['error']}")
            continue
        desc = r.get("task_description", "unknown")[:60]
        status = r.get("status", "unknown")
        if status == "completed":
            summary = r.get("result_summary", "No output")
            lines.append(f"- [completed] {desc}\n  Result: {summary}")
        elif status == "failed":
            error = r.get("error", "Unknown error")
            lines.append(f"- [failed] {desc}\n  Error: {error}")
        else:
            lines.append(f"- [{status}] {desc}")

    return "\n".join(lines)


@tool
async def send_to_worker(task_id: str, message: str) -> str:
    """
    Send a follow-up message to a completed worker's conversation.

    Use this to ask follow-up questions or request additional work from a
    worker that has already completed its initial task.

    Args:
        task_id: The task ID of the completed worker
        message: The follow-up message to send

    Returns:
        The worker's response
    """
    from services.orchestrator_service import send_message_to_worker

    result = await send_message_to_worker(task_id, message)
    if result.get("error"):
        return f"Error: {result['error']}"
    return result.get("response", "No response")


@tool
async def spawn_cc_worker(task: str, cwd: Optional[str] = None, wait: bool = True) -> str:
    """
    Spawn a Claude Code session tracked by the orchestrator.

    PREFERRED METHOD for all coding and file system tasks. Use this for any task
    involving file editing, script writing, test running, debugging, refactoring,
    or codebase exploration.

    Always uses the configured coding agent. Runs as a separate coding session with its own
    concurrency limit (independent from internal workers). Creates an orchestrator-tracked
    task visible in the sidebar and task list.
    Prefer wait=False for multi-file edits, build/test/debug loops, or any task likely
    to take more than ~45 seconds.

    Args:
        task: Clear description of the coding task. Include file paths, expected
              behavior, and any relevant context.
        cwd: Working directory for Claude Code (defaults to Edward's project root)
        wait: If True (default), block until CC completes and return result.
              If False, return task ID for async checking via check_worker().
              Prefer False for long-running coding tasks.

    Returns:
        If wait=True: the CC session's result summary
        If wait=False: task ID and status for later checking
    """
    conversation_id = get_current_conversation_id()
    if not conversation_id:
        return "Error: No conversation context available."

    from services.orchestrator_service import spawn_cc_task

    result = await spawn_cc_task(
        parent_conversation_id=conversation_id,
        task_description=task,
        cwd=cwd,
        wait=wait,
    )

    if result.get("error"):
        return f"Error: {result['error']}"

    if wait:
        status = result.get("status", "unknown")
        if status == "completed":
            return result.get("result_summary", "CC session completed with no output.")
        elif status == "failed":
            return f"CC session failed: {result.get('error', 'Unknown error')}"
        else:
            return f"CC session ended with status: {status}"

    return f"CC worker spawned. Task ID: {result['id']}\nStatus: {result['status']}\nUse check_worker('{result['id']}') to check progress."


# List of all orchestrator tools
ORCHESTRATOR_TOOLS = [spawn_worker, check_worker, list_workers, cancel_worker, wait_for_workers, send_to_worker, spawn_cc_worker]

ORCHESTRATOR_TOOL_NAMES = {"spawn_worker", "check_worker", "list_workers", "cancel_worker", "wait_for_workers", "send_to_worker", "spawn_cc_worker"}


def get_orchestrator_tools_description() -> str:
    """Get a description of orchestrator tools for the system prompt."""
    return """
## Orchestrator (Parallel Workers)

Spawn agents for parallel sub-tasks. Workers have full tool access but cannot spawn sub-workers.

### Coding tasks
- **spawn_cc_worker**: **Always use for file/code tasks** (editing, scripts, debugging). Prefer `wait=false` for multi-file edits, build/test/debug loops, or tasks likely to exceed ~45 seconds.

### Non-coding tasks
- **spawn_worker**: Research, analysis, messaging, memory ops. Choose model: haiku (fast), sonnet (balanced), opus (powerful). Prefer `wait=false` for long multi-step work.

### Management
- **check_worker / list_workers / cancel_worker / wait_for_workers / send_to_worker**

Write self-contained task descriptions (workers have no conversation history). Interactive turns should finish explicitly; if work will take a while, hand it off in background and return a status summary instead of waiting inline.
"""


# ============================================================================
# NOTEBOOKLM TOOLS
# ============================================================================

@tool
async def nlm_list_notebooks() -> str:
    """
    List all Google NotebookLM notebooks.

    Use this to see what notebooks exist before querying or adding sources.

    Returns:
        List of notebook names
    """
    from services.notebooklm_service import is_configured, list_notebooks

    if not is_configured():
        return "NotebookLM not configured. Run: nlm login"

    try:
        notebooks = await list_notebooks()
        if not notebooks:
            return "No notebooks found. Create one with nlm_create_notebook."

        return "Notebooks:\n" + "\n".join(
            f"- {nb['name']}" for nb in notebooks
        )
    except Exception as e:
        return f"Error listing notebooks: {str(e)}"


@tool
async def nlm_create_notebook(name: str) -> str:
    """
    Create a new Google NotebookLM notebook.

    Use this to create a knowledge base before adding sources.

    Args:
        name: Notebook name (descriptive, e.g. "Dog Training Research")

    Returns:
        Confirmation with notebook name
    """
    from services.notebooklm_service import is_configured, create_notebook

    if not is_configured():
        return "NotebookLM not configured. Run: nlm login"

    try:
        notebook = await create_notebook(name)
        return f"Created notebook '{notebook['name']}'"
    except Exception as e:
        return f"Error creating notebook: {str(e)}"


@tool
async def nlm_delete_notebook(notebook_name: str) -> str:
    """
    Delete a Google NotebookLM notebook permanently.

    Use with caution — this is permanent and deletes all sources.

    Args:
        notebook_name: Name of the notebook to delete

    Returns:
        Confirmation message
    """
    from services.notebooklm_service import is_configured, delete_notebook

    if not is_configured():
        return "NotebookLM not configured. Run: nlm login"

    try:
        deleted = await delete_notebook(notebook_name)
        if not deleted:
            return f"Notebook '{notebook_name}' not found"
        return f"Deleted notebook '{notebook_name}'"
    except Exception as e:
        return f"Error deleting notebook: {str(e)}"


@tool
async def nlm_add_source(
    notebook_name: str,
    source_type: str,
    content: str,
    title: Optional[str] = None,
) -> str:
    """
    Add a source to a Google NotebookLM notebook.

    Supports URLs, YouTube videos, text snippets, and file paths (PDFs).

    Args:
        notebook_name: Notebook name
        source_type: Type of source ("url", "youtube", "text", "file")
        content: URL, YouTube link, text content, or file path
        title: Optional title (for text sources only)

    Returns:
        Confirmation with source title and status
    """
    from services.notebooklm_service import (
        is_configured,
        add_url_source,
        add_youtube_source,
        add_text_source,
        add_file_source,
    )

    if not is_configured():
        return "NotebookLM not configured. Run: nlm login"

    try:
        if source_type == "url":
            result = await add_url_source(notebook_name, content)
        elif source_type == "youtube":
            result = await add_youtube_source(notebook_name, content)
        elif source_type == "text":
            result = await add_text_source(notebook_name, content, title=title)
        elif source_type == "file":
            result = await add_file_source(notebook_name, content)
        else:
            return f"Invalid source type: {source_type}. Use: url, youtube, text, file"

        return (
            f"Added source '{result['title']}' to notebook '{notebook_name}' "
            f"(status: {result['status']})"
        )
    except Exception as e:
        return f"Error adding source: {str(e)}"


@tool
async def nlm_list_sources(notebook_name: str) -> str:
    """
    List all sources in a Google NotebookLM notebook.

    Use this to see what sources are available for querying.

    Args:
        notebook_name: Notebook name

    Returns:
        List of source titles with IDs and types
    """
    from services.notebooklm_service import is_configured, list_sources

    if not is_configured():
        return "NotebookLM not configured. Run: nlm login"

    try:
        sources = await list_sources(notebook_name)
        if not sources:
            return f"No sources in notebook '{notebook_name}'. Add sources with nlm_add_source."

        lines = [f"Sources in '{notebook_name}':"]
        for s in sources:
            lines.append(
                f"- {s['title']} ({s['type']}, ID: {s['source_id']}, status: {s['status']})"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing sources: {str(e)}"


@tool
async def nlm_delete_source(notebook_name: str, source_id: str) -> str:
    """
    Delete a source from a Google NotebookLM notebook.

    Use nlm_list_sources first to find source IDs.

    Args:
        notebook_name: Notebook name
        source_id: Source ID to delete (from nlm_list_sources)

    Returns:
        Confirmation message
    """
    from services.notebooklm_service import is_configured, delete_source

    if not is_configured():
        return "NotebookLM not configured. Run: nlm login"

    try:
        result = await delete_source(notebook_name, source_id)
        return f"Deleted source '{result['title']}' from notebook '{notebook_name}'"
    except Exception as e:
        return f"Error deleting source: {str(e)}"


@tool
async def nlm_get_source_text(notebook_name: str, source_id: str) -> str:
    """
    Get the indexed fulltext content of a source.

    Use this to read the actual text that was indexed from a source.
    Useful for extracting content back out of NotebookLM.

    Args:
        notebook_name: Notebook name
        source_id: Source ID (from nlm_list_sources)

    Returns:
        Fulltext content
    """
    from services.notebooklm_service import is_configured, get_source_fulltext

    if not is_configured():
        return "NotebookLM not configured. Run: nlm login"

    try:
        fulltext = await get_source_fulltext(notebook_name, source_id)
        if not fulltext:
            return f"No text content available for source {source_id}"

        if len(fulltext) > 8000:
            return fulltext[:8000] + "\n\n[Content truncated — use nlm_ask for specific queries]"
        return fulltext
    except Exception as e:
        return f"Error retrieving source text: {str(e)}"


@tool
async def nlm_ask(notebook_name: str, question: str) -> str:
    """
    Ask a question grounded in notebook sources with citations.

    Responses include source citations from the notebook's knowledge base.

    Args:
        notebook_name: Notebook name
        question: Question to ask

    Returns:
        Answer with source citations
    """
    from services.notebooklm_service import is_configured, ask_notebook

    if not is_configured():
        return "NotebookLM not configured. Run: nlm login"

    try:
        result = await ask_notebook(notebook_name, question)
        answer = result["answer"]
        sources = result.get("sources", [])

        if sources:
            source_list = "\n\nSources:\n" + "\n".join(
                f"- {s}" for s in sources
            )
            return answer + source_list
        return answer
    except Exception as e:
        return f"Error asking notebook: {str(e)}"


@tool
async def nlm_research(
    notebook_name: str, query: str, mode: str = "fast"
) -> str:
    """
    Run web research and auto-import discovered sources to notebook.

    Sources are automatically imported after research completes.

    Args:
        notebook_name: Notebook name
        query: Research query
        mode: "fast" (5-10 sources) or "deep" (15-25 sources)

    Returns:
        Research results summary
    """
    from services.notebooklm_service import is_configured, web_research

    if not is_configured():
        return "NotebookLM not configured. Run: nlm login"

    if mode not in ("fast", "deep"):
        return "Invalid mode. Use 'fast' or 'deep'"

    try:
        result = await web_research(notebook_name, query, mode=mode)
        task_id = result.get("task_id")
        if task_id:
            return (
                f"Research started for '{query}' ({mode} mode, task_id: {task_id}). "
                f"Use nlm_poll_research to check status, then nlm_import_research to import sources."
            )
        return (
            f"Research complete for '{query}' ({mode} mode). "
            f"{result.get('result', 'Sources imported to notebook.')}"
        )
    except Exception as e:
        return f"Error running research: {str(e)}"


@tool
async def nlm_generate_artifact(
    notebook_name: str,
    artifact_type: str,
    instructions: Optional[str] = None,
) -> str:
    """
    Generate an artifact from notebook sources.

    Creates audio overviews (podcasts), videos, quizzes, flashcards, slide decks,
    infographics, mind maps, data tables, or reports from your knowledge base.

    Args:
        notebook_name: Notebook name
        artifact_type: Type — "audio", "video", "quiz", "flashcards", "slide_deck",
            "infographic", "mind_map", "data_table", "report"
        instructions: Optional generation instructions (for audio/data_table)

    Returns:
        Task ID for polling with nlm_wait_artifact
    """
    from services.notebooklm_service import is_configured, generate_artifact

    if not is_configured():
        return "NotebookLM not configured. Run: nlm login"

    valid_types = [
        "audio", "video", "quiz", "flashcards", "slide_deck",
        "infographic", "mind_map", "data_table", "report",
    ]
    if artifact_type not in valid_types:
        return f"Invalid artifact type. Use: {', '.join(valid_types)}"

    try:
        result = await generate_artifact(
            notebook_name, artifact_type, instructions=instructions
        )
        task_id = result["task_id"]
        return (
            f"Artifact generation started (type: {artifact_type}, task_id: {task_id}). "
            f"Use nlm_wait_artifact to check status."
        )
    except Exception as e:
        return f"Error generating artifact: {str(e)}"


@tool
async def nlm_wait_artifact(notebook_name: str, task_id: str) -> str:
    """
    Wait for artifact generation to complete.

    Use after nlm_generate_artifact to check if the artifact is ready.

    Args:
        notebook_name: Notebook name
        task_id: Task ID from nlm_generate_artifact

    Returns:
        Status message
    """
    from services.notebooklm_service import is_configured, wait_artifact

    if not is_configured():
        return "NotebookLM not configured. Run: nlm login"

    try:
        result = await wait_artifact(notebook_name, task_id)
        if result["ready"]:
            return f"Artifact ready (status: {result['status']}). Access it via the NotebookLM web UI."
        return f"Artifact still processing (status: {result['status']}). Check again in a moment."
    except Exception as e:
        return f"Error checking artifact status: {str(e)}"


@tool
async def nlm_push_document(document_id: str, notebook_name: str) -> str:
    """
    Push an Edward document to a NotebookLM notebook as a text source.

    Bridges Edward's document store with NotebookLM knowledge bases.

    Args:
        document_id: Edward document ID
        notebook_name: Notebook name

    Returns:
        Confirmation message
    """
    from services.notebooklm_service import is_configured, add_text_source
    from services.document_service import get_document_by_id

    if not is_configured():
        return "NotebookLM not configured. Run: nlm login"

    try:
        doc = await get_document_by_id(document_id)
        if not doc:
            return f"Document {document_id} not found in Edward's store"

        result = await add_text_source(notebook_name, doc.content, title=doc.title)
        return (
            f"Pushed document '{doc.title}' to notebook '{notebook_name}' "
            f"(source_id: {result['source_id']})"
        )
    except Exception as e:
        return f"Error pushing document: {str(e)}"


@tool
async def nlm_push_file(file_id: str, notebook_name: str) -> str:
    """
    Push an Edward stored file (PDF) to a NotebookLM notebook as a file source.

    Bridges Edward's file storage with NotebookLM knowledge bases.

    Args:
        file_id: Edward file ID
        notebook_name: Notebook name

    Returns:
        Confirmation message
    """
    from services.notebooklm_service import is_configured, add_file_source
    from services.file_storage_service import get_file, get_file_path

    if not is_configured():
        return "NotebookLM not configured. Run: nlm login"

    try:
        file_meta = await get_file(file_id)
        if not file_meta:
            return f"File {file_id} not found in Edward's storage"

        file_path = await get_file_path(file_id)
        if not file_path:
            return f"File {file_id} path not found on disk"

        result = await add_file_source(notebook_name, str(file_path))
        return (
            f"Pushed file '{file_meta.filename}' to notebook '{notebook_name}' "
            f"(source_id: {result['source_id']})"
        )
    except Exception as e:
        return f"Error pushing file: {str(e)}"


# --- Notebook management (3 new) ---

@tool
async def nlm_get_notebook(notebook_name: str) -> str:
    """
    Get notebook details including sources and settings.

    Args:
        notebook_name: Notebook name

    Returns:
        Notebook details
    """
    from services.notebooklm_service import is_configured, get_notebook

    if not is_configured():
        return "NotebookLM not configured. Run: nlm login"

    try:
        result = await get_notebook(notebook_name)
        return f"Notebook '{notebook_name}': {result}"
    except Exception as e:
        return f"Error getting notebook: {str(e)}"


@tool
async def nlm_describe_notebook(notebook_name: str) -> str:
    """
    Get an AI-generated summary and suggested topics for a notebook.

    Useful for understanding what a notebook covers at a glance.

    Args:
        notebook_name: Notebook name

    Returns:
        Summary and suggested topics
    """
    from services.notebooklm_service import is_configured, describe_notebook

    if not is_configured():
        return "NotebookLM not configured. Run: nlm login"

    try:
        result = await describe_notebook(notebook_name)
        summary = result.get("summary", "No summary available")
        topics = result.get("suggested_topics", [])
        text = f"Summary of '{notebook_name}':\n{summary}"
        if topics:
            text += "\n\nSuggested topics:\n" + "\n".join(f"- {t}" for t in topics)
        return text
    except Exception as e:
        return f"Error describing notebook: {str(e)}"


@tool
async def nlm_rename_notebook(notebook_name: str, new_title: str) -> str:
    """
    Rename a notebook.

    Args:
        notebook_name: Current notebook name
        new_title: New notebook name

    Returns:
        Confirmation message
    """
    from services.notebooklm_service import is_configured, rename_notebook

    if not is_configured():
        return "NotebookLM not configured. Run: nlm login"

    try:
        success = await rename_notebook(notebook_name, new_title)
        if success:
            return f"Renamed notebook '{notebook_name}' to '{new_title}'"
        return f"Failed to rename notebook '{notebook_name}'"
    except Exception as e:
        return f"Error renaming notebook: {str(e)}"


# --- Source management (3 new) ---

@tool
async def nlm_add_drive_source(
    notebook_name: str,
    document_id: str,
    title: str,
    mime_type: str = "application/vnd.google-apps.document",
) -> str:
    """
    Add a Google Drive document as a source to a notebook.

    Args:
        notebook_name: Notebook name
        document_id: Google Drive document ID
        title: Display title for the source
        mime_type: MIME type (default: Google Doc). Use "application/vnd.google-apps.spreadsheet" for Sheets.

    Returns:
        Confirmation with source details
    """
    from services.notebooklm_service import is_configured, add_drive_source

    if not is_configured():
        return "NotebookLM not configured. Run: nlm login"

    try:
        result = await add_drive_source(notebook_name, document_id, title, mime_type)
        return (
            f"Added Drive source '{result['title']}' to notebook '{notebook_name}' "
            f"(source_id: {result['source_id']}, status: {result['status']})"
        )
    except Exception as e:
        return f"Error adding Drive source: {str(e)}"


@tool
async def nlm_rename_source(notebook_name: str, source_id: str, new_title: str) -> str:
    """
    Rename a source in a notebook.

    Use nlm_list_sources to find source IDs.

    Args:
        notebook_name: Notebook name
        source_id: Source ID
        new_title: New source title

    Returns:
        Confirmation message
    """
    from services.notebooklm_service import is_configured, rename_source

    if not is_configured():
        return "NotebookLM not configured. Run: nlm login"

    try:
        success = await rename_source(notebook_name, source_id, new_title)
        if success:
            return f"Renamed source {source_id} to '{new_title}'"
        return f"Failed to rename source {source_id}"
    except Exception as e:
        return f"Error renaming source: {str(e)}"


@tool
async def nlm_describe_source(notebook_name: str, source_id: str) -> str:
    """
    Get an AI-generated summary and keywords for a source.

    Args:
        notebook_name: Notebook name (for validation)
        source_id: Source ID (from nlm_list_sources)

    Returns:
        Source summary and keywords
    """
    from services.notebooklm_service import is_configured, describe_source

    if not is_configured():
        return "NotebookLM not configured. Run: nlm login"

    try:
        result = await describe_source(source_id)
        summary = result.get("summary", "No summary available")
        keywords = result.get("keywords", [])
        text = f"Source summary:\n{summary}"
        if keywords:
            text += "\n\nKeywords: " + ", ".join(str(k) for k in keywords)
        return text
    except Exception as e:
        return f"Error describing source: {str(e)}"


# --- Chat configuration (1 new) ---

@tool
async def nlm_configure_chat(
    notebook_name: str,
    goal: str = "default",
    custom_prompt: str = "",
    response_length: str = "default",
) -> str:
    """
    Configure notebook chat settings (persona, response length).

    Args:
        notebook_name: Notebook name
        goal: Chat goal — "default", "learning_guide", or "custom"
        custom_prompt: Custom system prompt (only when goal="custom")
        response_length: "default", "shorter", or "longer"

    Returns:
        Confirmation message
    """
    from services.notebooklm_service import is_configured, configure_chat

    if not is_configured():
        return "NotebookLM not configured. Run: nlm login"

    try:
        result = await configure_chat(
            notebook_name, goal=goal,
            custom_prompt=custom_prompt or None,
            response_length=response_length,
        )
        return f"Chat configured for '{notebook_name}': {result}"
    except Exception as e:
        return f"Error configuring chat: {str(e)}"


# --- Research (2 new) ---

@tool
async def nlm_poll_research(notebook_name: str, task_id: str = "") -> str:
    """
    Check research progress and get results when complete.

    Use after nlm_research to check if research has finished.

    Args:
        notebook_name: Notebook name
        task_id: Research task ID (from nlm_research). Empty string polls all.

    Returns:
        Research status and results
    """
    from services.notebooklm_service import is_configured, poll_research

    if not is_configured():
        return "NotebookLM not configured. Run: nlm login"

    try:
        result = await poll_research(notebook_name, task_id=task_id or None)
        return f"Research status for '{notebook_name}': {result}"
    except Exception as e:
        return f"Error polling research: {str(e)}"


@tool
async def nlm_import_research(
    notebook_name: str,
    task_id: str,
    source_indices: str = "",
) -> str:
    """
    Import discovered research sources into the notebook.

    After research completes, use this to import some or all discovered sources.

    Args:
        notebook_name: Notebook name
        task_id: Research task ID
        source_indices: Comma-separated indices to import (e.g. "0,1,3"). Empty imports all.

    Returns:
        List of imported sources
    """
    from services.notebooklm_service import is_configured, import_research_sources

    if not is_configured():
        return "NotebookLM not configured. Run: nlm login"

    try:
        indices = None
        if source_indices.strip():
            indices = [int(i.strip()) for i in source_indices.split(",")]

        result = await import_research_sources(notebook_name, task_id, source_indices=indices)
        return f"Imported {len(result)} sources into '{notebook_name}': {result}"
    except Exception as e:
        return f"Error importing research sources: {str(e)}"


# --- Studio / Artifacts (2 new) ---

@tool
async def nlm_delete_artifact(notebook_name: str, artifact_id: str) -> str:
    """
    Delete a studio artifact permanently.

    Args:
        notebook_name: Notebook name
        artifact_id: Artifact ID to delete

    Returns:
        Confirmation message
    """
    from services.notebooklm_service import is_configured, delete_artifact

    if not is_configured():
        return "NotebookLM not configured. Run: nlm login"

    try:
        success = await delete_artifact(notebook_name, artifact_id)
        if success:
            return f"Deleted artifact {artifact_id} from '{notebook_name}'"
        return f"Failed to delete artifact {artifact_id}"
    except Exception as e:
        return f"Error deleting artifact: {str(e)}"


@tool
async def nlm_revise_slides(
    notebook_name: str,
    artifact_id: str,
    slide_instructions: str,
) -> str:
    """
    Revise individual slides in a slide deck artifact.

    Args:
        notebook_name: Notebook name
        artifact_id: Slide deck artifact ID
        slide_instructions: JSON array of revisions, e.g. [{"slide_number": 1, "instruction": "Add more detail"}]

    Returns:
        Revision status
    """
    import json
    from services.notebooklm_service import is_configured, revise_slides

    if not is_configured():
        return "NotebookLM not configured. Run: nlm login"

    try:
        instructions = json.loads(slide_instructions)
        if not isinstance(instructions, list):
            return "slide_instructions must be a JSON array of {slide_number, instruction} objects"

        result = await revise_slides(notebook_name, artifact_id, instructions)
        return f"Slides revised for artifact {artifact_id}: {result}"
    except json.JSONDecodeError:
        return 'Invalid JSON in slide_instructions. Format: [{"slide_number": 1, "instruction": "..."}]'
    except Exception as e:
        return f"Error revising slides: {str(e)}"


# --- Sharing (3 new) ---

@tool
async def nlm_share_status(notebook_name: str) -> str:
    """
    Get notebook sharing settings, collaborators, and public link status.

    Args:
        notebook_name: Notebook name

    Returns:
        Sharing status details
    """
    from services.notebooklm_service import is_configured, get_share_status

    if not is_configured():
        return "NotebookLM not configured. Run: nlm login"

    try:
        result = await get_share_status(notebook_name)
        is_public = result.get("is_public", False)
        url = result.get("public_url", "N/A")
        collaborators = result.get("collaborators", [])
        text = f"Sharing for '{notebook_name}':\n"
        text += f"- Public: {'Yes' if is_public else 'No'}"
        if is_public and url:
            text += f" ({url})"
        text += "\n"
        if collaborators:
            text += "- Collaborators:\n"
            for c in collaborators:
                if isinstance(c, dict):
                    text += f"  - {c.get('email', 'unknown')} ({c.get('role', 'viewer')})\n"
                else:
                    text += f"  - {c}\n"
        else:
            text += "- No collaborators\n"
        return text
    except Exception as e:
        return f"Error getting share status: {str(e)}"


@tool
async def nlm_share_public(notebook_name: str, is_public: bool = True) -> str:
    """
    Enable or disable public link access for a notebook.

    Args:
        notebook_name: Notebook name
        is_public: True to enable public access, False to disable

    Returns:
        Public URL if enabled, confirmation if disabled
    """
    from services.notebooklm_service import is_configured, share_public

    if not is_configured():
        return "NotebookLM not configured. Run: nlm login"

    try:
        result = await share_public(notebook_name, is_public=is_public)
        if is_public:
            url = result.get("public_url", "")
            return f"Public access enabled for '{notebook_name}'. URL: {url}"
        return f"Public access disabled for '{notebook_name}'"
    except Exception as e:
        return f"Error updating share settings: {str(e)}"


@tool
async def nlm_share_invite(
    notebook_name: str,
    email: str,
    role: str = "viewer",
) -> str:
    """
    Invite a collaborator to a notebook by email.

    Args:
        notebook_name: Notebook name
        email: Collaborator's email address
        role: "viewer" or "editor"

    Returns:
        Confirmation message
    """
    from services.notebooklm_service import is_configured, share_invite

    if not is_configured():
        return "NotebookLM not configured. Run: nlm login"

    if role not in ("viewer", "editor"):
        return "Invalid role. Use 'viewer' or 'editor'"

    try:
        success = await share_invite(notebook_name, email, role=role)
        if success:
            return f"Invited {email} as {role} to '{notebook_name}'"
        return f"Failed to invite {email} to '{notebook_name}'"
    except Exception as e:
        return f"Error inviting collaborator: {str(e)}"


# --- Notes (1 new) ---

@tool
async def nlm_note(
    notebook_name: str,
    action: str,
    note_id: str = "",
    content: str = "",
    title: str = "",
) -> str:
    """
    Manage notes in a notebook. Actions: create, list, update, delete.

    Args:
        notebook_name: Notebook name
        action: "create", "list", "update", or "delete"
        note_id: Note ID (required for update/delete, from action="list")
        content: Note content (required for create, optional for update)
        title: Note title (optional)

    Returns:
        Action result
    """
    from services.notebooklm_service import is_configured, manage_note

    if not is_configured():
        return "NotebookLM not configured. Run: nlm login"

    if action not in ("create", "list", "update", "delete"):
        return "Invalid action. Use: create, list, update, delete"

    try:
        result = await manage_note(
            notebook_name, action,
            note_id=note_id or None,
            content=content or None,
            title=title or None,
        )

        if action == "list":
            notes = result.get("notes", [])
            if not notes:
                return f"No notes in '{notebook_name}'"
            lines = [f"Notes in '{notebook_name}':"]
            for n in notes:
                if isinstance(n, dict):
                    lines.append(f"- {n.get('title', 'Untitled')} (ID: {n.get('id', 'unknown')})")
                else:
                    lines.append(f"- {n}")
            return "\n".join(lines)
        elif action == "delete":
            return f"Deleted note {note_id} from '{notebook_name}'"
        elif action == "create":
            return f"Created note in '{notebook_name}': {result}"
        else:  # update
            return f"Updated note {note_id} in '{notebook_name}': {result}"
    except Exception as e:
        return f"Error managing note: {str(e)}"


# Tool group constants
NOTEBOOKLM_TOOLS = [
    # Existing 13
    nlm_list_notebooks, nlm_create_notebook, nlm_delete_notebook,
    nlm_add_source, nlm_list_sources, nlm_delete_source, nlm_get_source_text,
    nlm_ask, nlm_research, nlm_generate_artifact, nlm_wait_artifact,
    nlm_push_document, nlm_push_file,
    # New 15
    nlm_get_notebook, nlm_describe_notebook, nlm_rename_notebook,
    nlm_add_drive_source, nlm_rename_source, nlm_describe_source,
    nlm_configure_chat,
    nlm_poll_research, nlm_import_research,
    nlm_delete_artifact, nlm_revise_slides,
    nlm_share_status, nlm_share_public, nlm_share_invite,
    nlm_note,
]

NOTEBOOKLM_TOOL_NAMES = {t.name for t in NOTEBOOKLM_TOOLS}


def get_notebooklm_tools_description() -> str:
    """Get a description of NotebookLM tools for the system prompt."""
    return """
## Google NotebookLM (Knowledge Bases)

Curated, source-grounded knowledge bases. Unlike documents (standalone text), notebooks cross-reference multiple sources with citations.

**Core workflow**: Create notebook -> add sources (URLs, YouTube, text, PDFs, Drive docs) -> query with nlm_ask for cited answers.

**Research workflow**: nlm_research (returns task_id) -> nlm_poll_research (check progress) -> nlm_import_research (import sources). Use mode='deep' for comprehensive research.

**Artifacts**: nlm_generate_artifact creates audio summaries, quizzes, reports, slides, mind maps, and more. Use nlm_wait_artifact to check status.

**Edward bridge**: nlm_push_document and nlm_push_file move your stored documents/files into notebooks as sources.

Reference notebooks by name (case-insensitive). Use nlm_ask for grounded Q&A with citations. Use web_search for real-time but unverified results. Build notebooks proactively when topics accumulate multiple sources.
"""
