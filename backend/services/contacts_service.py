"""
Contacts: the owner's address book, read from the AddressBook SQLite stores.

The SQLite stores are the primary source (2026-10-11): every
``Sources/<uuid>/AddressBook-v22.abcddb`` plus the top-level file, opened
read-only. ``lookup_contact`` searches names (every word must appear in the
first, middle, last or nick name or the organisation), ``lookup_handle``
reverse-looks-up a phone (last 10 digits) or an email (without case), and
``lookup_phone`` is ``lookup_handle``. A miss is a success with no matches,
never an error. AppleScript to Contacts.app runs only when no SQLite source
exists or every source fails to open or has drifted (``sqlite3.Error``):
it is slow, wakes the app (``open -ga Contacts``) and failed with
-600/-1708/-1712 on this macOS.
"""

import re
import subprocess
import os
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Tuple

# The AddressBook directory; tests point it at their own (conftest points it
# at a missing directory so no test reads the real address book).
ADDRESSBOOK_DIR_ENV = "EDWARD_ADDRESSBOOK_DIR"
_NAME_COLUMNS = ("ZFIRSTNAME", "ZMIDDLENAME", "ZLASTNAME", "ZNICKNAME", "ZORGANIZATION")
# Contacts.app's link between cards of one person (its unified card), when the
# store has it; cards across sources that share it are one person.
_LINK_COLUMN = "ZLINKID"
_UNREADABLE = "the AddressBook database isn't readable (Full Disk Access?)"


def _addressbook_base() -> str:
    override = (os.getenv(ADDRESSBOOK_DIR_ENV) or "").strip()
    if override:
        return os.path.expanduser(override)
    return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "AddressBook")


def _addressbook_sqlite_paths() -> list[str]:
    """Return all existing AddressBook SQLite paths.

    macOS splits contacts across:
    * ``~/Library/Application Support/AddressBook/AddressBook-v22.abcddb`` (top level — often empty)
    * ``.../AddressBook/Sources/<uuid>/AddressBook-v22.abcddb`` (per-source — where real data lives)

    Returns the list of existing files in priority order: per-source
    sub-DBs first (where the real contacts live), top-level last.
    ``EDWARD_ADDRESSBOOK_DIR`` replaces the base directory.
    """
    base = _addressbook_base()
    candidates: list[str] = []
    sources_dir = os.path.join(base, "Sources")
    if os.path.isdir(sources_dir):
        try:
            for entry in sorted(os.listdir(sources_dir)):
                candidate = os.path.join(sources_dir, entry, "AddressBook-v22.abcddb")
                if os.path.isfile(candidate):
                    candidates.append(candidate)
        except OSError:
            pass
    top = os.path.join(base, "AddressBook-v22.abcddb")
    if os.path.isfile(top):
        candidates.append(top)
    return candidates


def _addressbook_sqlite_path() -> Optional[str]:
    """First existing AddressBook SQLite path. Kept for back-compat /
    tests that monkeypatch a single path."""
    paths = _addressbook_sqlite_paths()
    return paths[0] if paths else None


def _sources() -> list[str]:
    """Every store to read: the single-path hook first (tests patch it),
    then the rest, existing files only."""
    paths: list[str] = []
    single = _addressbook_sqlite_path()
    if single and os.path.isfile(single):
        paths.append(single)
    for p in _addressbook_sqlite_paths():
        if p not in paths and os.path.isfile(p):
            paths.append(p)
    return paths


# ------------------------------------------------------------ handles ----

def _normalize_digits(phone: str) -> str:
    """Strip non-digits and return last 10 digits (handles country codes)."""
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:] if len(digits) >= 10 else digits


def _looks_like_phone(value: str) -> bool:
    text = (value or "").strip()
    digits = re.sub(r"\D", "", text)
    return len(digits) >= 7 and re.fullmatch(r"[0-9+()\-.\s]+", text) is not None


def _email_of(value: str) -> str:
    text = (value or "").strip()
    if text.lower().startswith("mailto:"):
        text = text[7:]
    if "<" in text and text.endswith(">"):
        text = text.rsplit("<", 1)[1][:-1]
    return text.strip().casefold()


def _same_phone(a: str, b: str) -> bool:
    """The same number however written: equal last 10 digits, or (for a
    short local number of 7+ digits) one a suffix of the other."""
    x, y = _normalize_digits(a), _normalize_digits(b)
    if not x or not y:
        return False
    if x == y:
        return True
    return min(len(x), len(y)) >= 7 and (x.endswith(y) or y.endswith(x))


def _handle_keys(person: dict) -> set:
    keys = {f"p:{_normalize_digits(p)}" for p in person.get("phones") or [] if _normalize_digits(p)}
    keys |= {f"e:{_email_of(e)}" for e in person.get("emails") or [] if _email_of(e)}
    return keys


# -------------------------------------------------------- the stores ----

class _Drift(sqlite3.DatabaseError):
    """A store whose schema isn't the AddressBook's any more."""


def _columns(conn: sqlite3.Connection, table: str) -> set:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    try:
        record_cols = _columns(conn, "ZABCDRECORD")
        if "Z_PK" not in record_cols or not (record_cols & set(_NAME_COLUMNS)):
            raise _Drift(f"schema changed: ZABCDRECORD lacks Z_PK or every name column ({path})")
    except Exception:
        conn.close()
        raise
    return conn


def _display_name(row: dict) -> str:
    parts = [row.get(c) for c in ("ZFIRSTNAME", "ZMIDDLENAME", "ZLASTNAME")]
    name = " ".join(str(p).strip() for p in parts if p and str(p).strip())
    return name or str(row.get("ZNICKNAME") or "").strip() or str(row.get("ZORGANIZATION") or "").strip()


def _records(conn: sqlite3.Connection, pks: Optional[Iterable[int]] = None) -> List[dict]:
    cols = _columns(conn, "ZABCDRECORD")
    select = ", ".join(c if c in cols else f"NULL AS {c}" for c in _NAME_COLUMNS + (_LINK_COLUMN,))
    sql = f"SELECT Z_PK, {select} FROM ZABCDRECORD"
    params: tuple = ()
    if pks is not None:
        pks = [int(p) for p in pks]
        if not pks:
            return []
        sql += f" WHERE Z_PK IN ({','.join('?' * len(pks))})"
        params = tuple(pks)
    out = []
    for row in conn.execute(sql, params):
        rec = dict(zip(("Z_PK",) + _NAME_COLUMNS + (_LINK_COLUMN,), row))
        if _display_name(rec):
            out.append(rec)
    return out


def _handles_by_owner(conn: sqlite3.Connection, table: str, value_col: str) -> Dict[int, List[str]]:
    cols = _columns(conn, table)
    if not cols:
        return {}
    if "ZOWNER" not in cols or value_col not in cols:
        raise _Drift(f"schema changed: {table} lacks ZOWNER or {value_col}")
    out: Dict[int, List[str]] = {}
    for owner, value in conn.execute(f"SELECT ZOWNER, {value_col} FROM {table} WHERE {value_col} IS NOT NULL"):
        if owner is not None and str(value).strip():
            out.setdefault(int(owner), []).append(str(value).strip())
    return out


def _person(rec: dict, phones: Dict[int, List[str]], emails: Dict[int, List[str]]) -> dict:
    pk = int(rec["Z_PK"])
    person: Dict[str, Any] = {"name": _display_name(rec), "phones": list(phones.get(pk, [])),
                              "emails": list(emails.get(pk, []))}
    nick, org = (str(rec.get("ZNICKNAME") or "").strip(), str(rec.get("ZORGANIZATION") or "").strip())
    if nick and nick != person["name"]:
        person["nickname"] = nick
    if org and org != person["name"]:
        person["organization"] = org
    link = str(rec.get(_LINK_COLUMN) or "").strip()
    if link:
        person["_link"] = link
    return person


def _same_person(p: dict, q: dict) -> bool:
    """Two entries are one person when Contacts.app linked their cards
    (``ZLINKID``), when they share a phone or an email and one's name words
    hold the other's, or when their full names are the same and no handle
    disagrees — both with phones and none shared, or both with emails and
    none shared, is a namesake."""
    if p.get("_link") and p.get("_link") == q.get("_link"):
        return True
    p_keys, q_keys = _handle_keys(p), _handle_keys(q)
    p_words, q_words = set(p["name"].casefold().split()), set(q["name"].casefold().split())
    if (p_words <= q_words or q_words <= p_words) and p_keys & q_keys:
        return True
    if p_words != q_words:
        return False
    for kind in ("p:", "e:"):
        mine = {k for k in p_keys if k.startswith(kind)}
        theirs = {k for k in q_keys if k.startswith(kind)}
        if mine and theirs and not mine & theirs:
            return False
    return True


def _merge(people: Iterable[dict]) -> List[dict]:
    """One entry per person across sources (``_same_person``); internal
    keys never leave."""
    out: List[dict] = []
    for p in people:
        for q in out:
            if _same_person(p, q):
                seen = _handle_keys(q)
                for kind, key_of in (("phones", lambda v: f"p:{_normalize_digits(v)}"),
                                     ("emails", lambda v: f"e:{_email_of(v)}")):
                    for v in p.get(kind) or []:
                        if key_of(v) not in seen:
                            q[kind].append(v)
                            seen.add(key_of(v))
                for extra in ("nickname", "organization", "_link"):
                    if p.get(extra) and not q.get(extra):
                        q[extra] = p[extra]
                break
        else:
            out.append({**p, "phones": list(p.get("phones") or []), "emails": list(p.get("emails") or [])})
    return [{k: v for k, v in person.items() if not k.startswith("_")} for person in out]


def _read_stores(match) -> Optional[List[dict]]:
    """``match(conn) -> [person]`` over every store, merged; None when no
    store exists or none could be read (the AppleScript fallback's cue)."""
    paths = _sources()
    if not paths:
        return None
    found: List[dict] = []
    read_any = False
    for path in paths:
        try:
            conn = _connect(path)
        except sqlite3.Error as exc:
            print(f"[Contacts] address book unreadable ({type(exc).__name__}): {exc}")
            continue
        try:
            found.extend(match(conn))
            read_any = True
        except sqlite3.Error as exc:
            print(f"[Contacts] address book read failed ({type(exc).__name__}): {exc}")
        finally:
            conn.close()
    return _merge(found) if read_any else None


def _name_matches(conn: sqlite3.Connection, words: List[str]) -> List[dict]:
    hits = [r for r in _records(conn)
            if all(w in " ".join(str(r.get(c) or "") for c in _NAME_COLUMNS).casefold() for w in words)]
    if not hits:
        return []
    phones = _handles_by_owner(conn, "ZABCDPHONENUMBER", "ZFULLNUMBER")
    emails = _handles_by_owner(conn, "ZABCDEMAILADDRESS", "ZADDRESS")
    return [_person(r, phones, emails) for r in hits]


def _handle_matches(conn: sqlite3.Connection, handle: str) -> List[dict]:
    phones = _handles_by_owner(conn, "ZABCDPHONENUMBER", "ZFULLNUMBER")
    emails = _handles_by_owner(conn, "ZABCDEMAILADDRESS", "ZADDRESS")
    owners: set = set()
    if "@" in handle:
        want = _email_of(handle)
        owners = {o for o, vals in emails.items() if any(_email_of(v) == want for v in vals)}
        if "ZADDRESSNORMALIZED" in _columns(conn, "ZABCDEMAILADDRESS"):
            owners |= {int(o) for (o,) in conn.execute(
                "SELECT ZOWNER FROM ZABCDEMAILADDRESS WHERE lower(ZADDRESSNORMALIZED) = ? AND ZOWNER IS NOT NULL",
                (want,))}
    else:
        owners = {o for o, vals in phones.items() if any(_same_phone(v, handle) for v in vals)}
    return [_person(r, phones, emails) for r in _records(conn, owners)]


def _lookup_phone_via_sqlite(phone_number: str) -> Optional[str]:
    """The first SQLite match's name for a phone, or None (no AppleScript).
    Kept for callers and tests that want just the name."""
    if len(_normalize_digits(phone_number or "")) < 7:
        return None
    people = _read_stores(lambda conn: _handle_matches(conn, phone_number)) or []
    return people[0]["name"] if people else None


def _ensure_contacts_running() -> None:
    """Wake Contacts.app silently. ``open -ga`` opens the app in the
    background without focusing it; if it's already running, this is a
    no-op. Without this, subsequent AppleScript ``tell`` blocks hit
    error -600 after system idle / first boot of a session.
    """
    try:
        subprocess.run(
            ["open", "-ga", "Contacts"],
            capture_output=True,
            timeout=3,
        )
    except Exception:
        # Best-effort — if the open fails, the subsequent osascript will
        # surface the real error.
        pass


def is_available() -> bool:
    """Check if macOS Contacts.app is accessible over AppleScript. Wakes the
    app: no lookup calls it any more (the SQLite stores come first)."""
    # Must be on macOS
    if os.uname().sysname != "Darwin":
        return False

    # Try to verify Contacts.app is accessible
    try:
        # ``open -ga`` wakes the app without bringing it to front. Without
        # this, the subsequent ``tell`` block hits error -600 ("Application
        # isn't running") every time after a system idle.
        _ensure_contacts_running()
        result = subprocess.run(
            ["osascript", "-e", 'tell application "Contacts" to return name'],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


# ------------------------------------------------------- the lookups ----

def lookup_contact(query: str, *, applescript: bool = True) -> dict:
    """
    Search the owner's contacts by name — or by a phone or email, which goes to
    ``lookup_handle``. ``applescript=False`` never falls back to Contacts.app:
    with no readable store it answers ``{"success": False, "error"}`` at once.

    Multi-word queries match when every word appears (in any order) in the
    first, middle, last or nick name or the organisation, without case:
    "rowan wren" finds Rowan Hale whose nickname is "Wren".

    Returns ``{"success": True, "matches": [{"name", "phones", "emails",
    "nickname"?, "organization"?}, ...]}``; a miss is ``matches: []``.
    ``{"success": False, "error"}`` only when no source could be read and
    the AppleScript fallback failed too.
    """
    raw = (query or "").strip()
    if "@" in raw or _looks_like_phone(raw):
        return lookup_handle(raw, applescript=applescript)
    words = raw.casefold().split()
    if not words:
        return {"success": True, "matches": []}
    people = _read_stores(lambda conn: _name_matches(conn, words))
    if people is not None:
        return {"success": True, "matches": people}
    if not applescript:
        return {"success": False, "error": _UNREADABLE}
    return _applescript_name_search(words)


def lookup_handle(handle: str, *, applescript: bool = True) -> dict:
    """
    Reverse-look-up a phone (matched on its last 10 digits) or an email
    (without case) — an iMessage sender, a chat participant, a mail sender.

    Same return shape as ``lookup_contact``: the person's name with all of
    their phones and emails; a miss is ``matches: []``.
    """
    raw = (handle or "").strip()
    if "@" in raw:
        if "@" not in _email_of(raw):
            return {"success": True, "matches": []}
    elif len(_normalize_digits(raw)) < 7:
        return {"success": True, "matches": []}
    people = _read_stores(lambda conn: _handle_matches(conn, raw))
    if people is not None:
        return {"success": True, "matches": people}
    if not applescript:
        return {"success": False, "error": _UNREADABLE}
    return _applescript_handle_search(raw)


def lookup_phone(phone_number: str) -> dict:
    """Reverse lookup by phone number: ``lookup_handle`` (kept for callers)."""
    return lookup_handle(phone_number)


# --------------------------------------------- the AppleScript fallback ----

def _person_records_script(selector: str) -> str:
    """An AppleScript that prints every person ``selector`` yields as
    ``{'name:…','phones:a|b','emails:c|d'}`` records joined by ``;;;``."""
    return f'''
    set outputList to {{}}

    tell application "Contacts" to launch
    delay 0.05

    tell application "Contacts"
        set matchingPeople to {selector}

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


def _parse_person_records(output: str) -> List[dict]:
    matches = []
    for record in (output or "").split(";;;"):
        record = record.strip()
        if not record:
            continue
        if record.startswith("{") and record.endswith("}"):
            record = record[1:-1]
        person: Dict[str, Any] = {"name": "", "phones": [], "emails": []}
        for part in record.split("','"):
            part = part.strip("'")
            if part.startswith("name:"):
                person["name"] = part[5:]
            elif part.startswith("phones:") and part[7:]:
                person["phones"] = part[7:].split("|")
            elif part.startswith("emails:") and part[7:]:
                person["emails"] = part[7:].split("|")
        if person["name"]:
            matches.append(person)
    return matches


def _run_contacts_script(script: str) -> Tuple[bool, Any]:
    """``(True, [person])`` or ``(False, error)``. Wakes Contacts first."""
    _ensure_contacts_running()
    try:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return False, "Timeout searching contacts"
    except Exception as e:
        return False, str(e)
    if result.returncode != 0:
        return False, result.stderr.strip() or "Unknown error searching contacts"
    return True, _parse_person_records(result.stdout.strip())


def _escape(text: str) -> str:
    return text.replace('\\', '\\\\').replace('"', '\\"')


def _applescript_name_search(words: List[str]) -> dict:
    """No readable SQLite store: ask Contacts.app for the first word, then
    keep the people whose name holds every word."""
    ok, found = _run_contacts_script(
        _person_records_script(f'(every person whose name contains "{_escape(words[0])}")'))
    if not ok:
        return {"success": False, "error": found}
    return {"success": True, "matches": [m for m in found if all(w in m["name"].casefold() for w in words)]}


def _applescript_handle_search(handle: str) -> dict:
    """No readable SQLite store: Contacts.app's full-text ``search for``,
    kept only where a phone or email really matches."""
    if "@" in handle:
        want = _email_of(handle)
        query = want
        keep = lambda m: any(_email_of(e) == want for e in m["emails"])  # noqa: E731
    else:
        query = _normalize_digits(handle)
        keep = lambda m: any(_same_phone(p, handle) for p in m["phones"])  # noqa: E731
    ok, found = _run_contacts_script(_person_records_script(f'(search for "{_escape(query)}")'))
    if not ok:
        return {"success": False, "error": found}
    return {"success": True, "matches": [m for m in found if keep(m)]}


def get_status() -> dict:
    """The Contacts row in Settings: how many address books the SQLite
    reader can open. Never runs osascript."""
    readable = 0
    for path in _sources():
        try:
            conn = _connect(path)
        except sqlite3.Error:
            continue
        conn.close()
        readable += 1
    if readable:
        books = "address book" if readable == 1 else "address books"
        return {"status": "connected", "status_message": f"{readable} {books} (AddressBook database)",
                "metadata": None}
    return {"status": "error", "status_message": _UNREADABLE, "metadata": None}
