"""
Contacts service using AppleScript.

Queries macOS Contacts.app to look up contacts by name, returning
phone numbers and emails. Useful for resolving nicknames or partial
names before sending messages.
"""

import re
import subprocess
import os
import sys
import json
from typing import List, Dict, Any, Optional


def _normalize_digits(phone: str) -> str:
    """Strip non-digits and return last 10 digits (handles country codes)."""
    digits = re.sub(r"\D", "", phone)
    return digits[-10:] if len(digits) >= 10 else digits


def is_available() -> bool:
    """Check if macOS Contacts.app is accessible."""
    # Must be on macOS
    if sys.platform != "darwin":
        return False

    # Try to verify Contacts.app is accessible
    try:
        result = subprocess.run(
            ["osascript", "-e", 'tell application "Contacts" to return name'],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def lookup_contact(query: str) -> dict:
    """
    Search Contacts.app by name.

    Supports multi-word queries: all words must appear in the contact name
    (in any order) for a match. This allows searching for "marissa buddy babe"
    to find "Marissa (Buddy Babe) Lentz".

    Args:
        query: Name or partial name to search for (case-insensitive).
               Multi-word queries match if all words are found in the name.

    Returns:
        Dict with success status and list of matching contacts:
        {
            "success": True,
            "matches": [
                {
                    "name": "John Smith",
                    "phones": ["+15551234567", "+15559876543"],
                    "emails": ["john@example.com"]
                },
                ...
            ]
        }
    """
    if not is_available():
        return {"success": False, "error": "Contacts.app not available (macOS only)"}

    # Split query into words for multi-word matching
    query_words = query.lower().split()
    if not query_words:
        return {"success": True, "matches": []}

    # Use the first word for the initial AppleScript search (most selective)
    # Then filter in Python for all words
    first_word = query_words[0]
    escaped_query = first_word.replace('\\', '\\\\').replace('"', '\\"')

    # AppleScript to search contacts by name
    # Returns JSON-like output that we parse
    script = f'''
    set queryText to "{escaped_query}"
    set outputList to {{}}

    tell application "Contacts"
        -- Search for people whose name contains the first query word (case-insensitive)
        set matchingPeople to (every person whose name contains queryText)

        repeat with p in matchingPeople
            set personName to name of p

            -- Get phone numbers
            set phoneList to {{}}
            repeat with ph in (phones of p)
                set end of phoneList to value of ph
            end repeat

            -- Get email addresses
            set emailList to {{}}
            repeat with em in (emails of p)
                set end of emailList to value of em
            end repeat

            -- Build a record for this person
            set personRecord to "{{" & quoted form of ("name:" & personName) & ","

            -- Add phones
            set phoneStr to ""
            repeat with i from 1 to count of phoneList
                if i > 1 then set phoneStr to phoneStr & "|"
                set phoneStr to phoneStr & (item i of phoneList)
            end repeat
            set personRecord to personRecord & quoted form of ("phones:" & phoneStr) & ","

            -- Add emails
            set emailStr to ""
            repeat with i from 1 to count of emailList
                if i > 1 then set emailStr to emailStr & "|"
                set emailStr to emailStr & (item i of emailList)
            end repeat
            set personRecord to personRecord & quoted form of ("emails:" & emailStr) & "}}"

            set end of outputList to personRecord
        end repeat
    end tell

    -- Join all records
    set AppleScript's text item delimiters to ";;;"
    return outputList as text
    '''

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip() or "Unknown error searching contacts"
            return {"success": False, "error": error_msg}

        # Parse the output
        output = result.stdout.strip()
        if not output:
            return {"success": True, "matches": []}

        matches = []
        for record in output.split(";;;"):
            if not record.strip():
                continue

            # Parse the record format: {'name:...',phones:...',emails:...'}
            # Remove outer braces and quotes
            record = record.strip()
            if record.startswith("{") and record.endswith("}"):
                record = record[1:-1]

            person = {"name": "", "phones": [], "emails": []}

            # Parse each field
            parts = record.split("','")
            for part in parts:
                part = part.strip("'")
                if part.startswith("name:"):
                    person["name"] = part[5:]
                elif part.startswith("phones:"):
                    phones_str = part[7:]
                    if phones_str:
                        person["phones"] = phones_str.split("|")
                elif part.startswith("emails:"):
                    emails_str = part[7:]
                    if emails_str:
                        person["emails"] = emails_str.split("|")

            if person["name"]:
                matches.append(person)

        # If multi-word query, filter matches to only those containing all words
        if len(query_words) > 1:
            filtered_matches = []
            for m in matches:
                name_lower = m["name"].lower()
                if all(word in name_lower for word in query_words):
                    filtered_matches.append(m)
            matches = filtered_matches

        return {"success": True, "matches": matches}

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Timeout searching contacts"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def lookup_phone(phone_number: str) -> dict:
    """
    Reverse lookup a contact by phone number.

    Uses Contacts.app's 'search for' command to find contacts, then filters
    results to confirm a phone number actually matches.

    Args:
        phone_number: Phone number to search for (any format)

    Returns:
        Dict with success status and list of matching contacts (same format as lookup_contact)
    """
    if not is_available():
        return {"success": False, "error": "Contacts.app not available (macOS only)"}

    search_digits = _normalize_digits(phone_number)
    if len(search_digits) < 7:
        return {"success": True, "matches": []}

    escaped_query = search_digits.replace('\\', '\\\\').replace('"', '\\"')

    # Use 'search for' which does a fast full-text search across all fields
    script = f'''
    set queryText to "{escaped_query}"
    set outputList to {{}}

    tell application "Contacts"
        set matchingPeople to (search for queryText)

        repeat with p in matchingPeople
            set personName to name of p

            -- Get phone numbers
            set phoneList to {{}}
            repeat with ph in (phones of p)
                set end of phoneList to value of ph
            end repeat

            -- Get email addresses
            set emailList to {{}}
            repeat with em in (emails of p)
                set end of emailList to value of em
            end repeat

            -- Build a record for this person
            set personRecord to "{{" & quoted form of ("name:" & personName) & ","

            -- Add phones
            set phoneStr to ""
            repeat with i from 1 to count of phoneList
                if i > 1 then set phoneStr to phoneStr & "|"
                set phoneStr to phoneStr & (item i of phoneList)
            end repeat
            set personRecord to personRecord & quoted form of ("phones:" & phoneStr) & ","

            -- Add emails
            set emailStr to ""
            repeat with i from 1 to count of emailList
                if i > 1 then set emailStr to emailStr & "|"
                set emailStr to emailStr & (item i of emailList)
            end repeat
            set personRecord to personRecord & quoted form of ("emails:" & emailStr) & "}}"

            set end of outputList to personRecord
        end repeat
    end tell

    -- Join all records
    set AppleScript's text item delimiters to ";;;"
    return outputList as text
    '''

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip() or "Unknown error searching contacts"
            return {"success": False, "error": error_msg}

        output = result.stdout.strip()
        if not output:
            return {"success": True, "matches": []}

        matches = []
        for record in output.split(";;;"):
            if not record.strip():
                continue

            record = record.strip()
            if record.startswith("{") and record.endswith("}"):
                record = record[1:-1]

            person = {"name": "", "phones": [], "emails": []}

            parts = record.split("','")
            for part in parts:
                part = part.strip("'")
                if part.startswith("name:"):
                    person["name"] = part[5:]
                elif part.startswith("phones:"):
                    phones_str = part[7:]
                    if phones_str:
                        person["phones"] = phones_str.split("|")
                elif part.startswith("emails:"):
                    emails_str = part[7:]
                    if emails_str:
                        person["emails"] = emails_str.split("|")

            if not person["name"]:
                continue

            # Filter: only include if a phone number actually matches the search digits
            phone_match = False
            for ph in person["phones"]:
                if _normalize_digits(ph) == search_digits:
                    phone_match = True
                    break
            if phone_match:
                matches.append(person)

        return {"success": True, "matches": matches}

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Timeout searching contacts"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_status() -> dict:
    """
    Get the current status of the Contacts service.

    Returns:
        Dict with status info: status, status_message, metadata
    """
    if sys.platform != "darwin":
        return {
            "status": "error",
            "status_message": "Not running on macOS",
            "metadata": None
        }

    # Try to verify Contacts.app is accessible
    try:
        result = subprocess.run(
            ["osascript", "-e", 'tell application "Contacts" to return name'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return {
                "status": "connected",
                "status_message": "Contacts.app accessible",
                "metadata": None
            }
        else:
            return {
                "status": "error",
                "status_message": result.stderr.strip() or "Contacts.app not accessible",
                "metadata": None
            }
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "status_message": "Timeout checking Contacts.app",
            "metadata": None
        }
    except Exception as e:
        return {
            "status": "error",
            "status_message": str(e),
            "metadata": None
        }
