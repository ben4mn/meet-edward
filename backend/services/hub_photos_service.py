"""Photo screensaver for the hub. Pure logic; the router does the I/O.

Two concerns drive this module:

**Path safety.** The API serves a file by name out of a photos directory.
Any endpoint shaped that way is one careless join away from handing out
Edward's ``.env``, so ``safe_photo_path`` resolves the candidate and
refuses anything that lands outside the root — including via symlink — and
refuses anything that isn't a browser-renderable image even when it *is*
inside the root.

**What Chromium can actually draw.** It does not render HEIC, which is
precisely what an iPhone produces. Listing HEIC files anyway would give a
black screensaver with no explanation, so they are partitioned out and
counted, and the UI says so.
"""

import random
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

# Formats Chromium will draw. Deliberately excludes HEIC/HEIF, TIFF and raw.
DISPLAYABLE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}

# Real photos we can see but can't show — worth telling the user about.
UNSUPPORTED_PHOTO_EXTENSIONS = {".heic", ".heif", ".tiff", ".tif", ".dng", ".raw", ".cr2", ".nef"}


def is_displayable(name: str) -> bool:
    """True if a browser can render this file."""
    if not name or name.startswith("."):
        return False
    return Path(name).suffix.lower() in DISPLAYABLE_EXTENSIONS


def _is_unsupported_photo(name: str) -> bool:
    if not name or name.startswith("."):
        return False
    return Path(name).suffix.lower() in UNSUPPORTED_PHOTO_EXTENSIONS


def partition(names: Iterable[str]) -> Tuple[List[str], int]:
    """Split into (displayable, count of real-but-unrenderable photos).

    Junk (.DS_Store, Thumbs.db, text files) is dropped silently — only
    things that are genuinely photos get counted as skipped, because that
    count becomes a message telling the owner to convert them.
    """
    shown: List[str] = []
    skipped = 0
    for name in names:
        if is_displayable(name):
            shown.append(name)
        elif _is_unsupported_photo(name):
            skipped += 1
    return shown, skipped


def safe_photo_path(root: Path, name: str) -> Optional[Path]:
    """Resolve ``name`` under ``root``, or None if it is not a photo we
    should serve. Refuses traversal, absolute paths, escaping symlinks,
    missing files, and non-image files."""
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return None
    if not is_displayable(name):
        return None

    try:
        root_resolved = Path(root).resolve(strict=True)
        candidate = (root_resolved / name).resolve(strict=True)
    except (OSError, RuntimeError):
        return None

    # resolve() follows symlinks, so this also catches a link pointing out.
    if not candidate.is_file():
        return None
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        return None

    return candidate


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------
#
# Reading is guarded above; writing needs its own guard. A filename from a
# browser is attacker-controlled in exactly the same way a URL segment is,
# so it is reduced to a leaf name, filtered to characters that survive a
# URL path, and refused outright unless it is a photo we are prepared to
# store. Nothing here touches the disk — the router does that.

# HEIC isn't displayable, but refusing it would be refusing what an iPhone
# actually produces. We accept it and convert on the way in.
CONVERTIBLE_EXTENSIONS = {".heic", ".heif"}
UPLOADABLE_EXTENSIONS = DISPLAYABLE_EXTENSIONS | CONVERTIBLE_EXTENSIONS

# What a converted HEIC lands as.
CONVERTED_EXTENSION = ".jpg"

# Long enough for anything a camera or a person produces; short enough that
# the whole album still fits comfortably in a filesystem and a URL.
MAX_NAME_LENGTH = 120

# One photo. A 48MP HEIC is ~8MB, so this leaves plenty of headroom while
# still refusing someone uploading a video by mistake.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# Everything else becomes an underscore.
_SAFE_NAME_CHARS = set(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "._-"
)


def sanitize_upload_name(raw: str) -> Optional[str]:
    """A browser-supplied filename reduced to a safe leaf name, or None.

    None means "don't store this": it isn't a photo we accept, or there is
    nothing left of the name once the path parts are stripped.
    """
    if not raw:
        return None

    # Windows and POSIX separators both, before Path gets a say — Path on
    # macOS treats a backslash as an ordinary character.
    leaf = raw.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if leaf in ("", ".", ".."):
        return None

    suffix = Path(leaf).suffix.lower()
    if suffix not in UPLOADABLE_EXTENSIONS:
        return None

    stem = leaf[: len(leaf) - len(Path(leaf).suffix)]
    # A leading dot would make it a dotfile, which the read path drops.
    stem = stem.lstrip(".")
    if not stem:
        return None

    stem = "".join(c if c in _SAFE_NAME_CHARS else "_" for c in stem)
    stem = stem[: MAX_NAME_LENGTH - len(suffix)]
    if not stem:
        return None

    return stem + suffix


def needs_conversion(name: str) -> bool:
    """True if we have to re-encode this before a browser can draw it."""
    return Path(name).suffix.lower() in CONVERTIBLE_EXTENSIONS


def stored_name(name: str) -> str:
    """The name the file ends up under, after any conversion."""
    if needs_conversion(name):
        return name[: len(name) - len(Path(name).suffix)] + CONVERTED_EXTENSION
    return name


def unique_name(name: str, taken: Iterable[str]) -> str:
    """``name``, or ``name-2`` / ``name-3`` if the album already has it.

    Two phones both hand you IMG_0001.jpg. Overwriting is the one outcome
    nobody wants, so the newcomer gets a suffix rather than the incumbent
    getting deleted.
    """
    existing = set(taken)
    if name not in existing:
        return name

    suffix = Path(name).suffix
    stem = name[: len(name) - len(suffix)]
    n = 2
    while f"{stem}-{n}{suffix}" in existing:
        n += 1
    return f"{stem}-{n}{suffix}"


def shuffled(names: List[str], seed: int) -> List[str]:
    """Deterministic shuffle — the same seed always yields the same order,
    so a reloading kiosk doesn't restart the album from the same photo."""
    out = list(names)
    random.Random(seed).shuffle(out)
    return out


# ---------------------------------------------------------------------------
# Collection (I/O)
# ---------------------------------------------------------------------------

import os  # noqa: E402
from typing import Any, Dict  # noqa: E402

PHOTOS_DIR = Path(
    os.environ.get("EDWARD_PHOTOS_DIR", str(Path.home() / "edward-photos"))
)


def list_photos(seed: int = 0) -> Dict[str, Any]:
    """Everything the screensaver needs to run the album."""
    try:
        names = [p.name for p in PHOTOS_DIR.iterdir() if p.is_file()]
    except (OSError, FileNotFoundError):
        return {
            "photos": [],
            "skipped_unsupported": 0,
            "dir": str(PHOTOS_DIR),
            "dir_present": False,
        }

    shown, skipped = partition(names)
    return {
        "photos": shuffled(shown, seed) if seed else sorted(shown),
        "skipped_unsupported": skipped,
        "dir": str(PHOTOS_DIR),
        "dir_present": True,
    }


# ---------------------------------------------------------------------------
# Writing (I/O)
# ---------------------------------------------------------------------------
#
# The album stopped being SSH-only when the settings panel got an upload
# button, so this is the write half of the same guard the read half has.

import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402

# Longest edge for a converted HEIC. The hub is a 7" panel and the album is
# also drawn blurred behind the photo, so 2400px is already generous — and
# re-encoding at full size just burns disk. Only conversions are resampled;
# a JPEG you upload is stored byte-for-byte as you sent it.
CONVERTED_MAX_EDGE = 2400


def _existing_names() -> set:
    try:
        return {p.name for p in PHOTOS_DIR.iterdir()}
    except (OSError, FileNotFoundError):
        return set()


def _convert_to_jpeg(data: bytes, suffix: str, dest: Path) -> bool:
    """HEIC in, JPEG at ``dest`` out, via macOS `sips`. False if it can't.

    `sips` is the one HEIC decoder guaranteed present on the host without
    adding a Python imaging dependency; on anything else we'd rather say
    so than write a file Chromium will render as a black rectangle.
    """
    if shutil.which("sips") is None:
        return False

    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
            fh.write(data)
            tmp = Path(fh.name)
        result = subprocess.run(
            ["sips", "-s", "format", "jpeg", "-Z", str(CONVERTED_MAX_EDGE),
             str(tmp), "--out", str(dest)],
            capture_output=True,
            timeout=60,
        )
        return result.returncode == 0 and dest.exists()
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def save_upload(data: bytes, raw_name: str) -> Dict[str, Any]:
    """Store one uploaded photo. Never raises — a bad file in a batch of
    twenty reports itself and the other nineteen still land."""
    safe = sanitize_upload_name(raw_name)
    if safe is None:
        return {"ok": False, "name": raw_name, "reason": "not a photo we can show"}
    if not data:
        return {"ok": False, "name": raw_name, "reason": "empty file"}
    if len(data) > MAX_UPLOAD_BYTES:
        mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        return {"ok": False, "name": raw_name, "reason": f"larger than {mb}MB"}

    try:
        PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "name": raw_name, "reason": f"cannot write album: {exc}"}

    target = unique_name(stored_name(safe), _existing_names())
    dest = PHOTOS_DIR / target
    converting = needs_conversion(safe)

    try:
        if converting:
            if not _convert_to_jpeg(data, Path(safe).suffix.lower(), dest):
                return {
                    "ok": False,
                    "name": raw_name,
                    "reason": "HEIC conversion failed (needs macOS `sips`)",
                }
        else:
            dest.write_bytes(data)
    except OSError as exc:
        return {"ok": False, "name": raw_name, "reason": str(exc)}

    return {"ok": True, "name": target, "original": raw_name, "converted": converting}


def delete_photo(name: str) -> bool:
    """Remove one photo. Goes through the same resolver as serving, so a
    delete can no more escape the album than a read can."""
    path = safe_photo_path(PHOTOS_DIR, name)
    if path is None:
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# Editing and thumbnails (the photo door, 2026-10-11)
# ---------------------------------------------------------------------------
#
# Rotating writes a new file under a new name and removes the old one, never
# re-encoding a name in place: hub rule 4 (a photo in a visible slideshow
# slot is never mutated), and the kiosk caches a name for 24 h.

import re  # noqa: E402

# What `sips` can rotate and write back in the same format. GIF (frames)
# and WebP/AVIF (no sips writer everywhere) are refused, not mangled.
ROTATABLE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
ROTATIONS = {"right": 90, "left": 270}

_ROTATED_TAIL = re.compile(r"-rotated(?:-\d+)?$")

THUMBS_DIR = Path(
    os.environ.get(
        "EDWARD_PHOTO_THUMBS_DIR",
        str(Path.home() / "Library" / "Caches" / "edward-photo-thumbs"),
    )
)
THUMB_MAX_EDGE = 480


def rotated_name(name: str, taken: Iterable[str]) -> str:
    """``cat.jpg`` → ``cat-rotated.jpg``; turning it again stays ``cat-rotated-N.jpg``."""
    suffix = Path(name).suffix
    stem = _ROTATED_TAIL.sub("", name[: len(name) - len(suffix)])
    return unique_name(f"{stem}-rotated{suffix}", set(taken) | {name})


def rotate_photo(name: str, direction: str) -> Optional[str]:
    """Turn one photo a quarter. Returns the new name, None if it can't be
    rotated (missing, outside the album, a format sips can't write)."""
    if direction not in ROTATIONS:
        raise ValueError(f"direction must be one of {sorted(ROTATIONS)}")
    path = safe_photo_path(PHOTOS_DIR, name)
    if path is None or path.suffix.lower() not in ROTATABLE_EXTENSIONS:
        return None
    if shutil.which("sips") is None:
        return None

    target = rotated_name(path.name, _existing_names())
    dest = PHOTOS_DIR / target
    try:
        result = subprocess.run(
            ["sips", "-r", str(ROTATIONS[direction]), str(path), "--out", str(dest)],
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        dest.unlink(missing_ok=True)
        return None
    if result.returncode != 0 or not dest.exists():
        dest.unlink(missing_ok=True)
        return None
    try:
        path.unlink()
    except OSError:
        pass
    return target


def thumbnail_path(name: str) -> Optional[Path]:
    """A small JPEG of one photo for a phone's grid, cached outside the
    album by name and mtime. Falls back to the photo itself if sips can't."""
    path = safe_photo_path(PHOTOS_DIR, name)
    if path is None:
        return None
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return None
    thumb = THUMBS_DIR / f"{path.name}.{mtime}.jpg"
    if thumb.exists():
        return thumb
    if shutil.which("sips") is None:
        return path
    try:
        THUMBS_DIR.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["sips", "-s", "format", "jpeg", "-Z", str(THUMB_MAX_EDGE), str(path), "--out", str(thumb)],
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return path
    if result.returncode != 0 or not thumb.exists():
        return path
    return thumb
