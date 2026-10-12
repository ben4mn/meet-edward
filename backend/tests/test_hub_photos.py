"""Photo screensaver for the hub.

Two things here are worth real tests:

1. **Path safety.** The API serves a file by name out of a photos
   directory. Any endpoint shaped like that is one bad join away from
   handing out /etc/passwd or Edward's .env, so the resolver rejects
   anything that escapes the root rather than trusting the name.

2. **What a browser can actually draw.** Chromium does not render HEIC,
   which is exactly what an iPhone produces. Silently listing HEIC files
   would give Ellis a black screensaver and no clue why, so they are
   partitioned out and counted for the UI to report.
"""

from pathlib import Path

import pytest

from services import hub_photos_service as svc


# ---------------------------------------------------------------------------
# Which files a browser can draw
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["a.jpg", "b.JPEG", "c.png", "d.webp", "e.gif", "f.avif"])
def test_browser_renderable_formats_are_displayable(name):
    assert svc.is_displayable(name) is True


@pytest.mark.parametrize("name", ["shot.heic", "shot.HEIC", "raw.dng", "scan.tiff", "clip.mov"])
def test_formats_chromium_cannot_draw_are_not_displayable(name):
    assert svc.is_displayable(name) is False


@pytest.mark.parametrize("name", [".DS_Store", ".hidden.jpg", "Thumbs.db", "notes.txt"])
def test_junk_and_dotfiles_are_not_displayable(name):
    assert svc.is_displayable(name) is False


def test_partition_reports_skipped_heic_so_the_ui_can_explain_itself():
    names = ["one.jpg", "two.png", "iphone1.heic", "iphone2.HEIC", ".DS_Store"]
    shown, skipped = svc.partition(names)
    assert shown == ["one.jpg", "two.png"]
    # Junk is dropped silently; real photos in an unsupported codec are counted.
    assert skipped == 2


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------

def test_plain_filename_inside_root_resolves(tmp_path):
    (tmp_path / "cat.jpg").write_bytes(b"x")
    assert svc.safe_photo_path(tmp_path, "cat.jpg") == (tmp_path / "cat.jpg").resolve()


@pytest.mark.parametrize("name", [
    "../.env",
    "../../etc/passwd",
    "sub/../../secret.jpg",
    "/etc/passwd",
    "..",
])
def test_traversal_attempts_are_refused(tmp_path, name):
    assert svc.safe_photo_path(tmp_path, name) is None


def test_symlink_pointing_outside_the_root_is_refused(tmp_path):
    outside = tmp_path.parent / "outside-secret.jpg"
    outside.write_bytes(b"x")
    root = tmp_path / "photos"
    root.mkdir()
    (root / "sneaky.jpg").symlink_to(outside)

    assert svc.safe_photo_path(root, "sneaky.jpg") is None


def test_missing_file_is_refused(tmp_path):
    assert svc.safe_photo_path(tmp_path, "nope.jpg") is None


def test_non_displayable_file_inside_root_is_still_refused(tmp_path):
    """Being inside the root is not enough — don't serve the .env someone
    dropped in the photos folder by accident."""
    (tmp_path / ".env").write_text("SECRET=1")
    assert svc.safe_photo_path(tmp_path, ".env") is None


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------

def test_shuffle_is_deterministic_for_a_given_seed():
    names = [f"{i}.jpg" for i in range(20)]
    assert svc.shuffled(names, seed=7) == svc.shuffled(names, seed=7)


def test_different_seeds_give_different_orders():
    names = [f"{i}.jpg" for i in range(20)]
    assert svc.shuffled(names, seed=1) != svc.shuffled(names, seed=2)


def test_shuffle_keeps_every_photo():
    names = [f"{i}.jpg" for i in range(20)]
    assert sorted(svc.shuffled(names, seed=3)) == sorted(names)


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------
#
# The album used to be filled by dropping files into ~/edward-photos over
# SSH, which means nobody but Ellis could add one. Uploading from the phone
# turns the same directory into an untrusted write surface, so the name a
# browser hands us gets the same suspicion the read path already applies:
# it never becomes a path, and it never becomes a file we can't draw.

@pytest.mark.parametrize("name", ["holiday.jpg", "Maple.PNG", "a b c.webp"])
def test_ordinary_photo_names_survive_sanitising(name):
    assert svc.sanitize_upload_name(name) is not None


def test_extension_is_lowercased_so_listing_and_serving_agree():
    assert svc.sanitize_upload_name("Maple.JPG") == "Maple.jpg"


def test_spaces_and_awkward_characters_become_underscores():
    # Not for safety — the resolver handles that — but because the name
    # goes into a URL path on every slide.
    assert svc.sanitize_upload_name("beach day #2.jpg") == "beach_day__2.jpg"


@pytest.mark.parametrize(
    "name",
    [
        "../../.env.jpg",
        "/etc/passwd.jpg",
        "..\\..\\windows\\evil.jpg",
        "sub/dir/photo.jpg",
    ],
)
def test_a_path_is_reduced_to_its_leaf_and_never_escapes(name):
    safe = svc.sanitize_upload_name(name)
    assert safe is not None
    assert "/" not in safe and "\\" not in safe
    assert safe not in ("..", ".")


@pytest.mark.parametrize("name", ["", ".", "..", "notes.txt", "clip.mov", "run.sh"])
def test_names_we_will_not_store_are_refused(name):
    assert svc.sanitize_upload_name(name) is None


def test_a_leading_dot_is_stripped_rather_than_the_photo_being_refused():
    """The read path drops dotfiles. Storing one would put the photo on
    disk and then never show it — a silent black hole is worse than either
    refusing it or renaming it, and renaming keeps the photo."""
    assert svc.sanitize_upload_name(".hidden.jpg") == "hidden.jpg"
    assert svc.is_displayable(svc.sanitize_upload_name(".hidden.jpg"))


def test_heic_is_accepted_for_upload_even_though_it_is_not_displayable():
    """The whole reason to accept it: an iPhone hands you HEIC, and telling
    someone at the fridge to go run `sips` is not a feature."""
    assert svc.sanitize_upload_name("IMG_0042.HEIC") is not None
    assert svc.is_displayable("IMG_0042.heic") is False


def test_a_very_long_name_is_trimmed_but_keeps_its_extension():
    safe = svc.sanitize_upload_name("x" * 400 + ".jpg")
    assert safe is not None
    assert len(safe) <= svc.MAX_NAME_LENGTH
    assert safe.endswith(".jpg")


# ---- what it lands as ----

def test_heic_is_stored_under_a_jpg_name_because_that_is_what_we_write():
    assert svc.stored_name("IMG_0042.heic") == "IMG_0042.jpg"


def test_an_already_displayable_upload_keeps_its_name():
    assert svc.stored_name("holiday.jpg") == "holiday.jpg"


def test_needs_conversion_only_for_the_formats_chromium_cannot_draw():
    assert svc.needs_conversion("IMG_0042.heic") is True
    assert svc.needs_conversion("IMG_0042.heif") is True
    assert svc.needs_conversion("holiday.jpg") is False


# ---- collisions ----

def test_a_fresh_name_is_left_alone():
    assert svc.unique_name("holiday.jpg", set()) == "holiday.jpg"


def test_a_clash_is_suffixed_rather_than_overwriting_the_album():
    """Two people photographing the same thing both get IMG_0001.jpg from
    their phones. Silently overwriting one of them loses a photo."""
    assert svc.unique_name("IMG_0001.jpg", {"IMG_0001.jpg"}) == "IMG_0001-2.jpg"


def test_repeated_clashes_keep_counting():
    taken = {"IMG_0001.jpg", "IMG_0001-2.jpg", "IMG_0001-3.jpg"}
    assert svc.unique_name("IMG_0001.jpg", taken) == "IMG_0001-4.jpg"


def test_the_suffix_goes_before_the_extension_so_it_stays_displayable():
    out = svc.unique_name("a.jpg", {"a.jpg"})
    assert svc.is_displayable(out)


# ---------------------------------------------------------------------------
# Writing to the album (I/O)
# ---------------------------------------------------------------------------

@pytest.fixture
def album(tmp_path, monkeypatch):
    """Point the service at a throwaway album, not ~/edward-photos."""
    root = tmp_path / "album"
    root.mkdir()
    monkeypatch.setattr(svc, "PHOTOS_DIR", root)
    return root


# A 1x1 JPEG — enough for `sips` to treat as a real image.
TINY_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300ffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffc00011080001000103012200021101031101ffc4001f0000010501010101010"
    "100000000000000000102030405060708090a0bffc400b5100002010303020403"
    "050504040000017d01020300041105122131410613516107227114328191a1082"
    "342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a"
    "434445464748494a535455565758595a636465666768696a737475767778797a8"
    "38485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8"
    "b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f"
    "3f4f5f6f7f8f9faffda0008010100003f00fb00a28a2803ffd9"
)


def test_an_ordinary_upload_lands_in_the_album(album):
    out = svc.save_upload(TINY_JPEG, "holiday.jpg")
    assert out["ok"] is True
    assert (album / "holiday.jpg").read_bytes() == TINY_JPEG


def test_an_upload_is_stored_byte_for_byte_when_no_conversion_is_needed(album):
    """Re-encoding someone's photo to save a few KB is not our call."""
    svc.save_upload(TINY_JPEG, "holiday.jpg")
    assert (album / "holiday.jpg").read_bytes() == TINY_JPEG


def test_a_second_upload_of_the_same_name_does_not_overwrite_the_first(album):
    svc.save_upload(TINY_JPEG, "IMG_0001.jpg")
    second = svc.save_upload(TINY_JPEG + b"\x00", "IMG_0001.jpg")
    assert second["name"] == "IMG_0001-2.jpg"
    assert (album / "IMG_0001.jpg").exists()
    assert (album / "IMG_0001-2.jpg").exists()


def test_a_traversing_name_lands_inside_the_album_and_nowhere_else(album, tmp_path):
    out = svc.save_upload(TINY_JPEG, "../../escaped.jpg")
    assert out["ok"] is True
    assert (album / out["name"]).exists()
    assert not (tmp_path.parent / "escaped.jpg").exists()


def test_a_file_we_cannot_show_is_refused_with_a_reason(album):
    out = svc.save_upload(b"#!/bin/sh\n", "payload.sh")
    assert out["ok"] is False
    assert out["reason"]
    assert list(album.iterdir()) == []


def test_an_empty_file_is_refused(album):
    assert svc.save_upload(b"", "holiday.jpg")["ok"] is False


def test_something_far_too_big_is_refused_before_it_fills_the_disk(album):
    out = svc.save_upload(b"x" * (svc.MAX_UPLOAD_BYTES + 1), "huge.jpg")
    assert out["ok"] is False
    assert list(album.iterdir()) == []


def test_the_album_directory_is_created_if_it_is_missing(tmp_path, monkeypatch):
    root = tmp_path / "not-yet"
    monkeypatch.setattr(svc, "PHOTOS_DIR", root)
    assert svc.save_upload(TINY_JPEG, "first.jpg")["ok"] is True
    assert (root / "first.jpg").exists()


def test_an_uploaded_photo_is_immediately_listable_and_servable(album):
    name = svc.save_upload(TINY_JPEG, "holiday.jpg")["name"]
    assert name in svc.list_photos()["photos"]
    assert svc.safe_photo_path(album, name) is not None


# ---- deleting ----

def test_deleting_removes_the_photo(album):
    svc.save_upload(TINY_JPEG, "holiday.jpg")
    assert svc.delete_photo("holiday.jpg") is True
    assert not (album / "holiday.jpg").exists()


def test_deleting_something_outside_the_album_is_refused(album, tmp_path):
    outside = tmp_path / "secret.jpg"
    outside.write_bytes(TINY_JPEG)
    assert svc.delete_photo("../secret.jpg") is False
    assert outside.exists()


def test_deleting_a_missing_photo_reports_failure_rather_than_raising(album):
    assert svc.delete_photo("never-existed.jpg") is False


# ---- HEIC ----

def _png_bytes(size: int = 64) -> bytes:
    """A real PNG, built with the stdlib. `sips` refuses to encode a 1x1
    image to HEIC, so the fixture has to be big enough to be plausible."""
    import struct
    import zlib

    rows = [
        b"\x00" + bytes(v for x in range(size) for v in ((x * 4) % 256, (y * 4) % 256, 128))
        for y in range(size)
    ]

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"".join(rows)))
        + chunk(b"IEND", b"")
    )


def _make_heic(tmp_path):
    """A real HEIC on disk, or a skip if this host can't produce one."""
    import shutil
    import subprocess

    if shutil.which("sips") is None:
        pytest.skip("needs macOS sips")

    source = tmp_path / "src.png"
    source.write_bytes(_png_bytes())
    heic = tmp_path / "src.heic"
    made = subprocess.run(
        ["sips", "-s", "format", "heic", str(source), "--out", str(heic)],
        capture_output=True,
    )
    if made.returncode != 0 or not heic.exists():
        pytest.skip("this host's sips cannot write HEIC")
    return heic


def test_heic_is_converted_to_a_drawable_jpeg_on_the_way_in(album, tmp_path):
    """The reason uploads exist at all: an iPhone hands you HEIC and the
    hub's Chromium renders it as nothing."""
    heic = _make_heic(tmp_path)

    out = svc.save_upload(heic.read_bytes(), "IMG_0042.HEIC")
    assert out["ok"] is True
    assert out["converted"] is True
    assert out["name"] == "IMG_0042.jpg"
    assert svc.is_displayable(out["name"])

    written = album / "IMG_0042.jpg"
    assert written.stat().st_size > 0
    # Really a JPEG, not a HEIC wearing a .jpg — that would be a silently
    # black slide, which is the exact failure this path exists to prevent.
    assert written.read_bytes()[:2] == b"\xff\xd8"


def test_a_heic_upload_leaves_only_the_converted_file_behind(album, tmp_path):
    heic = _make_heic(tmp_path)
    svc.save_upload(heic.read_bytes(), "IMG_0042.HEIC")
    assert [p.name for p in album.iterdir()] == ["IMG_0042.jpg"]
