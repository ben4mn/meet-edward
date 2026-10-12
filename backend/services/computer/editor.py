"""The file editor behind the ``str_replace_based_edit_tool``
(Anthropic ``text_editor_20250728``). It runs **in-process** — outside the
Seatbelt sandbox — so it applies the same rails itself: ``rails.can_write``
gates writes, ``rails.can_read`` gates reads (only Edward's own
credentials are refused), both compared by what is on disk rather than how
the path is spelled, and ``view`` output is redacted before the model
reads it.

Every file is opened through ``rails.open_checked`` (review 2026-10-12): the
path is checked, its directory opened by descriptor and checked again, the
file opened with ``O_NOFOLLOW`` and its own inode and canonical path checked
before a byte is read or written. A case variant, a link, or a link swapped
in between the check and the open never reaches a credential or a protected
root. A write reads and replaces the content through the one descriptor it
checked, never truncating before the check; the ``.trash`` copy is taken
from that same descriptor, so a refused file is never copied anywhere, and
is itself created only as a new file (never through a link the shell
planted in ``.trash``).

Commands: ``view`` (``cat -n`` lines or a directory listing), ``create``
(writes, backing up any existing file to ``.trash``), ``str_replace``
(exactly one match) and ``insert``.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Optional, Tuple

from services import secret_scan
from services.computer import home, rails
from services.toolset.result import ToolFailure

MAX_CHARACTERS = 30000


def _resolve(path: str) -> str:
    """Absolute real path; ``~`` and relatives resolve against edward-home."""
    return rails._real(path)


def _redact(text: str) -> str:
    red, _ = secret_scan.redact_secrets(text, secret_scan.known_secrets())
    return red


def _backup(name: str, content: bytes) -> None:
    """Keep ``content`` (what is about to be overwritten) in
    ``~/edward-home/.trash``. Best effort: never blocks the edit. The copy
    is always a new file (``O_EXCL``, no link followed) and never lands in
    a protected root, wherever ``.trash`` has been pointed."""
    trash = home.edward_home() / ".trash"
    try:
        trash.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    ts = time.strftime("%Y%m%d-%H%M%S")
    for attempt in range(3):
        dest = trash / (f"{ts}-{name}" if attempt == 0 else f"{ts}-{uuid.uuid4().hex[:8]}-{name}")
        try:
            fd, _ = rails.open_checked(dest, write=True, create=True, exclusive=True, mode=0o600)
        except (FileExistsError, rails.RailRefused):
            continue  # something (a planted link, say) holds that name: another name
        except OSError:
            return
        try:
            rails.write_fd(fd, content)
        except OSError:
            pass
        finally:
            os.close(fd)
        return


def _require_writable(real: str) -> None:
    if not rails.can_write(real):
        raise ToolFailure(rails.write_refusal(real))


def _require_readable(real: str) -> None:
    if not rails.can_read(real):
        raise ToolFailure(rails.read_refusal(real))


def _open(real: str, *, write: bool = False, create: bool = False) -> Tuple[int, str]:
    """``rails.open_checked`` with its refusals as tool errors."""
    try:
        return rails.open_checked(real, write=write, create=create)
    except rails.RailRefused as exc:
        raise ToolFailure(str(exc)) from None
    except FileNotFoundError:
        raise ToolFailure(f"{real} does not exist") from None
    except IsADirectoryError:
        raise ToolFailure(f"{real} is a directory") from None
    except OSError as exc:
        verb = "write" if write else "read"
        raise ToolFailure(f"could not {verb} {real}: {exc}") from None


def _decode(raw: bytes, real: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ToolFailure(f"{real} is not UTF-8 text — the editor changes text files only") from None


def view(path: str, view_range: Optional[list] = None) -> str:
    real = _resolve(path)
    _require_readable(real)
    if os.path.isdir(real):
        return _view_dir(real)
    if not os.path.exists(real):
        raise ToolFailure(f"{real} does not exist")
    fd, canon = _open(real)
    try:
        raw = rails.read_fd(fd)
    except OSError as exc:
        raise ToolFailure(f"could not read {real}: {exc}")
    finally:
        os.close(fd)
    if b"\x00" in raw[:4096]:
        return f"Binary file — use look to see {os.path.basename(canon)}"
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    start = 1
    if view_range:
        try:
            lo, hi = int(view_range[0]), int(view_range[1])
        except (ValueError, IndexError, TypeError):
            raise ToolFailure("view_range must be [start, end] line numbers")
        start = max(1, lo)
        hi = len(lines) if hi in (-1, None) else hi
        lines = lines[start - 1: hi]
    numbered = "\n".join(f"{i + start:6d}\t{line}" for i, line in enumerate(lines))
    numbered = _redact(numbered)
    if len(numbered) > MAX_CHARACTERS:
        numbered = numbered[:MAX_CHARACTERS] + f"\n…[cut at {MAX_CHARACTERS} chars — view a range]"
    return numbered or "(empty file)"


def _view_dir(real: str) -> str:
    out = [f"{real}/"]
    try:
        entries = sorted(os.listdir(real))
    except OSError as exc:
        raise ToolFailure(f"could not list {real}: {exc}")
    for name in entries:
        if name.startswith("."):
            continue
        full = os.path.join(real, name)
        if os.path.isdir(full):
            out.append(f"  {name}/")
            try:
                for sub in sorted(os.listdir(full)):
                    if not sub.startswith("."):
                        out.append(f"    {sub}" + ("/" if os.path.isdir(os.path.join(full, sub)) else ""))
            except OSError:
                pass
        else:
            out.append(f"  {name}")
    return "\n".join(out)


def create(path: str, file_text: str) -> str:
    real = _resolve(path)
    _require_readable(real)
    _require_writable(real)
    try:
        os.makedirs(os.path.dirname(real) or ".", exist_ok=True)
    except OSError as exc:
        raise ToolFailure(f"could not write {real}: {exc}")
    fd, canon = _open(real, write=True, create=True)
    try:
        old = rails.read_fd(fd)
        if old:
            _backup(os.path.basename(canon), old)
        rails.write_fd(fd, file_text.encode("utf-8"))
    except OSError as exc:
        raise ToolFailure(f"could not write {real}: {exc}")
    finally:
        os.close(fd)
    return f"Wrote {real} ({len(file_text)} chars)"


def _rewrite(real: str, change) -> Tuple[str, str, str]:
    """Read ``real`` and write ``change(content)`` back through one checked
    descriptor, keeping the old content in ``.trash``. Returns ``(old,
    new, canonical path)``; ``change`` raises ``ToolFailure`` to leave the
    file as it was."""
    _require_readable(real)
    _require_writable(real)
    if not os.path.isfile(real):
        raise ToolFailure(f"{real} does not exist")
    fd, canon = _open(real, write=True)
    try:
        try:
            raw = rails.read_fd(fd)
        except OSError as exc:
            raise ToolFailure(f"could not read {real}: {exc}")
        content = _decode(raw, real)
        updated = change(content)
        _backup(os.path.basename(canon), raw)
        try:
            rails.write_fd(fd, updated.encode("utf-8"))
        except OSError as exc:
            raise ToolFailure(f"could not write {real}: {exc}")
    finally:
        os.close(fd)
    return content, updated, canon


def str_replace(path: str, old_str: str, new_str: str) -> str:
    real = _resolve(path)

    def change(content: str) -> str:
        count = content.count(old_str)
        if count == 0:
            raise ToolFailure("str_replace found no match for old_str — it must match the file exactly, "
                              "including whitespace")
        if count > 1:
            raise ToolFailure(f"str_replace found {count} matches for old_str — make it unique "
                              f"(add surrounding lines)")
        return content.replace(old_str, new_str, 1)

    content, updated, _ = _rewrite(real, change)
    # Show ±4 lines around the edit.
    before = content[: content.index(old_str)].count("\n")
    lines = updated.splitlines()
    lo = max(0, before - 4)
    hi = min(len(lines), before + new_str.count("\n") + 5)
    snippet = "\n".join(f"{i + lo + 1:6d}\t{line}" for i, line in enumerate(lines[lo:hi]))
    return f"Edited {real}\n{_redact(snippet)}"


def insert(path: str, insert_line: int, insert_text: str) -> str:
    real = _resolve(path)

    def change(content: str) -> str:
        lines = content.splitlines(keepends=True)
        if insert_line < 0 or insert_line > len(lines):
            raise ToolFailure(f"insert_line {insert_line} is out of range (0..{len(lines)})")
        body = insert_text if insert_text.endswith("\n") else insert_text + "\n"
        lines.insert(insert_line, body)
        return "".join(lines)

    _rewrite(real, change)
    return f"Inserted at line {insert_line} in {real}"


async def edit(command: str, path: str, **kw) -> str:
    """Dispatch a ``text_editor`` command. ``new_str`` is accepted as an
    alias for ``insert``'s ``insert_text`` (older tool shapes)."""
    if not path:
        raise ToolFailure("path is required")
    if command == "view":
        return view(path, kw.get("view_range"))
    if command == "create":
        text = kw.get("file_text")
        if text is None:
            raise ToolFailure("create needs file_text")
        return create(path, text)
    if command == "str_replace":
        old = kw.get("old_str")
        if old is None:
            raise ToolFailure("str_replace needs old_str")
        return str_replace(path, old, kw.get("new_str") or "")
    if command == "insert":
        line = kw.get("insert_line")
        if line is None:
            raise ToolFailure("insert needs insert_line")
        text = kw.get("insert_text")
        if text is None:
            text = kw.get("new_str")
        if text is None:
            raise ToolFailure("insert needs insert_text")
        return insert(path, int(line), text)
    raise ToolFailure(f"unknown editor command: {command}")
