"""Turn a token stream into speakable sentences.

The model writes markdown for a screen; the voice reads plain sentences.
``SentenceFeed`` releases each sentence the moment the next one starts
(or the stream ends), and ``strip_for_speech`` flattens markdown so
nobody hears "asterisk asterisk".
"""

from __future__ import annotations

import re
from typing import List, Optional

# Split after . ! ? followed by whitespace and a capital/quote/digit.
# The lookahead avoids "Dr. Smith" and "U.S. Army".
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
_ABBREVIATIONS = ("Dr.", "Mr.", "Mrs.", "Ms.", "St.", "vs.", "e.g.", "i.e.", "etc.")

_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_CODE_RE = re.compile(r"`([^`]*)`")
_MD_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*|__([^_]+)__")
_MD_ITALIC_RE = re.compile(r"(?<!\w)[*_]([^*_]+)[*_](?!\w)")
_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_MD_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", re.MULTILINE)
_URL_RE = re.compile(r"https?://\S+")


def _strip_inline(text: str) -> str:
    out = _MD_LINK_RE.sub(r"\1", text)
    out = _MD_CODE_RE.sub(r"\1", out)
    out = _MD_BOLD_RE.sub(lambda m: m.group(1) or m.group(2) or "", out)
    out = _MD_ITALIC_RE.sub(r"\1", out)
    return out


def strip_for_speech(text: str) -> str:
    """Flatten markdown to plain spoken text.

    Line breaks become sentence breaks (a full stop is inserted between
    lines that lack one); nothing is appended after the final line.
    """
    if not text:
        return ""
    out = _strip_inline(text)
    out = _MD_HEADING_RE.sub("", out)
    out = _MD_BULLET_RE.sub("", out)
    out = _URL_RE.sub("a link", out)

    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    pieces: List[str] = []
    for i, ln in enumerate(lines):
        if i < len(lines) - 1 and ln[-1] not in ".!?:;,":
            ln += "."
        pieces.append(ln)
    joined = " ".join(pieces)
    joined = re.sub(r"[ \t]+", " ", joined)
    joined = re.sub(r"\s+([.,!?;:])", r"\1", joined)
    return joined.strip()


def _ends_with_abbreviation(text: str) -> bool:
    t = text.rstrip()
    return any(t.endswith(a) for a in _ABBREVIATIONS)


class SentenceFeed:
    """Accumulate tokens; hand back sentences as they close."""

    def __init__(self, max_pending_chars: int = 220):
        self._buf = ""
        self.max_pending_chars = max_pending_chars

    def push(self, token: str) -> List[str]:
        if not token:
            return []
        # Inline markdown is removed as soon as a pair closes, so "**Done.**"
        # exposes its full stop to the sentence splitter.
        self._buf = _strip_inline(self._buf + token)
        released: List[str] = []

        parts = _SENTENCE_SPLIT_RE.split(self._buf)
        while len(parts) > 1:
            head = parts.pop(0)
            if _ends_with_abbreviation(head):
                # Not a real boundary: glue it back onto the next part.
                parts[0] = head + " " + parts[0]
                if len(parts) == 1:
                    break
                continue
            cleaned = strip_for_speech(head)
            if cleaned:
                released.append(cleaned)
        self._buf = parts[0] if parts else ""

        # "…twenty. " — the stream has moved past a terminator. Waiting for
        # the next capital letter would hold speech through a tool call.
        if self._buf and self._buf[-1].isspace():
            core = self._buf.rstrip()
            if core and core[-1] in ".!?" and not _ends_with_abbreviation(core):
                cleaned = strip_for_speech(core)
                self._buf = ""
                if cleaned:
                    released.append(cleaned)

        # A model that never punctuates would otherwise sit on the whole
        # answer. Release at a soft boundary once the buffer is long.
        if len(self._buf) > self.max_pending_chars:
            cut = self._find_soft_cut(self._buf)
            if cut > 0:
                head, self._buf = self._buf[:cut].rstrip(), self._buf[cut:].lstrip()
                cleaned = strip_for_speech(head)
                if cleaned:
                    released.append(cleaned)
        return released

    def flush(self) -> Optional[str]:
        rest = strip_for_speech(self._buf)
        self._buf = ""
        return rest or None

    def _find_soft_cut(self, text: str) -> int:
        limit = min(len(text), self.max_pending_chars)
        for sep in (", ", "; ", ": ", " "):
            idx = text.rfind(sep, 0, limit)
            if idx > 0:
                return idx + len(sep)
        return 0
