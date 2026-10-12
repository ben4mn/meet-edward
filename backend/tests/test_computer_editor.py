"""The in-process file editor: view / create / str_replace / insert, the
.trash backup, rail refusals, the view cap and redaction."""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def home_env(tmp_path, monkeypatch):
    ehome = tmp_path / "edward-home"
    repo = tmp_path / "edward"
    (ehome / ".trash").mkdir(parents=True, exist_ok=True)
    repo.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("EDWARD_HOME", str(ehome))
    monkeypatch.setenv("EDWARD_COMPUTER_DIR", str(tmp_path / ".edward-computer"))
    monkeypatch.setenv("EDWARD_LIVE_REPO", str(repo))
    monkeypatch.setenv("EDWARD_RELEASES_DIR", str(tmp_path / "edward-releases"))
    return {"home": ehome, "repo": repo}


async def _edit(**kw):
    from services.computer import editor
    return await editor.edit(**kw)


@pytest.mark.asyncio
async def test_create_and_view(home_env):
    out = await _edit(command="create", path="notes.md", file_text="hello\nworld\n")
    assert "Wrote" in out
    view = await _edit(command="view", path="notes.md")
    assert "1\thello" in view and "2\tworld" in view


@pytest.mark.asyncio
async def test_str_replace_unique(home_env):
    await _edit(command="create", path="a.txt", file_text="alpha\nbeta\ngamma\n")
    out = await _edit(command="str_replace", path="a.txt", old_str="beta", new_str="BETA")
    assert "Edited" in out
    view = await _edit(command="view", path="a.txt")
    assert "BETA" in view and "beta" not in view


@pytest.mark.asyncio
async def test_str_replace_zero_and_many(home_env):
    from services.toolset.result import ToolFailure
    await _edit(command="create", path="b.txt", file_text="x\nx\n")
    with pytest.raises(ToolFailure) as e0:
        await _edit(command="str_replace", path="b.txt", old_str="nope", new_str="z")
    assert "no match" in str(e0.value)
    with pytest.raises(ToolFailure) as e2:
        await _edit(command="str_replace", path="b.txt", old_str="x", new_str="z")
    assert "2 matches" in str(e2.value)


@pytest.mark.asyncio
async def test_insert(home_env):
    await _edit(command="create", path="c.txt", file_text="one\nthree\n")
    await _edit(command="insert", path="c.txt", insert_line=1, insert_text="two")
    view = await _edit(command="view", path="c.txt")
    assert view.index("two") < view.index("three")


@pytest.mark.asyncio
async def test_trash_backup_on_overwrite(home_env):
    await _edit(command="create", path="d.txt", file_text="original\n")
    await _edit(command="create", path="d.txt", file_text="replaced\n")
    trash = list((home_env["home"] / ".trash").glob("*-d.txt"))
    assert trash, "overwrite should leave a .trash copy"
    assert "original" in trash[0].read_text()


@pytest.mark.asyncio
async def test_write_refusal_names_the_rail(home_env):
    from services.toolset.result import ToolFailure
    target = str(home_env["repo"] / "backend" / "main.py")
    with pytest.raises(ToolFailure) as exc:
        await _edit(command="create", path=target, file_text="x")
    assert "live code" in str(exc.value)


@pytest.mark.asyncio
async def test_view_redacts_secrets(home_env):
    secret = "sk-ant-" + "A1b2C3d4E5f6G7h8J9k0L1m2N3o4"
    await _edit(command="create", path="leak.env", file_text=f"ANTHROPIC_API_KEY={secret}\n")
    view = await _edit(command="view", path="leak.env")
    assert secret not in view
    assert "[redacted:" in view


@pytest.mark.asyncio
async def test_view_binary_file(home_env):
    p = home_env["home"] / "blob.bin"
    p.write_bytes(b"\x00\x01\x02binary")
    out = await _edit(command="view", path="blob.bin")
    assert "Binary file" in out


@pytest.mark.asyncio
async def test_view_range_and_cap(home_env):
    body = "".join(f"line{i}\n" for i in range(1, 50))
    await _edit(command="create", path="big.txt", file_text=body)
    out = await _edit(command="view", path="big.txt", view_range=[5, 7])
    assert "5\tline5" in out and "line8" not in out


@pytest.mark.asyncio
@pytest.mark.parametrize("rel", [".env", "backend/.env", "backend/.jwt_secret", "backend/.setup_code"])
async def test_view_refuses_his_own_credentials(home_env, rel):
    """2026-10-12 review: the session key in backend/.jwt_secret let code in
    the shell act as the owner; the editor refuses it like the shell does."""
    from services.toolset.result import ToolFailure
    target = home_env["repo"] / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("JWT_SECRET_KEY=not-for-edward\n")
    with pytest.raises(ToolFailure) as exc:
        await _edit(command="view", path=str(target))
    assert "credentials" in str(exc.value)
    assert "not-for-edward" not in str(exc.value)


@pytest.mark.asyncio
async def test_a_symlink_to_a_credential_is_refused(home_env):
    from services.toolset.result import ToolFailure
    secret = home_env["repo"] / "backend" / ".jwt_secret"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("not-for-edward")
    os.symlink(secret, home_env["home"] / "innocent.txt")
    with pytest.raises(ToolFailure):
        await _edit(command="view", path="innocent.txt")
    with pytest.raises(ToolFailure):
        await _edit(command="str_replace", path="innocent.txt", old_str="not", new_str="now")
    with pytest.raises(ToolFailure):
        await _edit(command="create", path="innocent.txt", file_text="x")
    assert secret.read_text() == "not-for-edward"
    assert not list((home_env["home"] / ".trash").glob("*innocent*")), "no backup copy of the key either"


@pytest.mark.asyncio
async def test_env_example_is_still_readable(home_env):
    (home_env["repo"] / ".env.example").write_text("EDWARD_OWNER_NAME=\n")
    out = await _edit(command="view", path=str(home_env["repo"] / ".env.example"))
    assert "EDWARD_OWNER_NAME" in out


# ---------------------------------------------------- review 2026-10-12 --
# A case variant of a credential's path opened the real file (macOS volumes
# fold case), and ``create`` first copied the real key into .trash, where
# the shell could read it.

def _folds_case(path) -> bool:
    try:
        return os.pathconf(str(path), 11) == 0  # _PC_CASE_SENSITIVE on macOS
    except (OSError, ValueError):
        return False


@pytest.mark.asyncio
@pytest.mark.parametrize("rel", [".env", "backend/.jwt_secret", "backend/.setup_code"])
async def test_no_spelling_of_a_credential_is_viewed_replaced_or_backed_up(home_env, rel):
    from services.toolset.result import ToolFailure
    if not _folds_case(home_env["repo"]):
        pytest.skip("this volume compares names case-sensitively")
    target = home_env["repo"] / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("not-for-edward")
    spellings = [str(target).upper(), str(home_env["repo"]) + "/" + rel.upper(),
                 str(home_env["repo"]) + "/" + rel.replace("backend", "BackEnd")]
    for spelling in spellings:
        for kw in ({"command": "view"}, {"command": "create", "file_text": "attacker"},
                   {"command": "str_replace", "old_str": "not", "new_str": "now"},
                   {"command": "insert", "insert_line": 0, "insert_text": "x"}):
            with pytest.raises(ToolFailure) as exc:
                await _edit(path=spelling, **kw)
            assert "not-for-edward" not in str(exc.value)
    assert target.read_text() == "not-for-edward"
    assert not list((home_env["home"] / ".trash").iterdir()), "no copy of a credential, ever"


@pytest.mark.asyncio
async def test_a_case_variant_of_the_live_repo_is_refused(home_env):
    from services.toolset.result import ToolFailure
    if not _folds_case(home_env["repo"]):
        pytest.skip("this volume compares names case-sensitively")
    main = home_env["repo"] / "backend" / "main.py"
    main.parent.mkdir(parents=True, exist_ok=True)
    main.write_text("print('live')\n")
    with pytest.raises(ToolFailure) as exc:
        await _edit(command="create", path=str(main).upper(), file_text="x")
    assert "live code" in str(exc.value)
    with pytest.raises(ToolFailure):
        await _edit(command="str_replace", path=str(main).upper(), old_str="live", new_str="pwned")
    with pytest.raises(ToolFailure):
        await _edit(command="create", path=str(home_env["repo"]).upper() + "/backend/.SETUP_CODE",
                    file_text="123456")
    assert main.read_text() == "print('live')\n"
    assert not (home_env["repo"] / "backend" / ".setup_code").exists()
    assert not list((home_env["home"] / ".trash").iterdir())


@pytest.mark.asyncio
async def test_a_link_planted_in_trash_is_never_written_through(home_env):
    """The backend writes the .trash copy outside the sandbox, into a
    directory the shell can change: a link waiting at the copy's name must
    not carry the write into a protected file."""
    import time

    victim = home_env["repo"] / "backend" / "app.py"
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_text("live code\n")
    await _edit(command="create", path="note.txt", file_text="first\n")
    ts = time.strftime("%Y%m%d-%H%M%S")
    for t in {ts, time.strftime("%Y%m%d-%H%M%S", time.localtime(time.time() + 1))}:
        os.symlink(victim, home_env["home"] / ".trash" / f"{t}-note.txt")
    out = await _edit(command="create", path="note.txt", file_text="second\n")
    assert "Wrote" in out
    assert victim.read_text() == "live code\n"
    kept = [p for p in (home_env["home"] / ".trash").glob("*note.txt") if not p.is_symlink()]
    assert kept and kept[0].read_text() == "first\n"


@pytest.mark.asyncio
async def test_a_trash_pointed_at_the_live_repo_gets_no_copy(home_env):
    """``.trash`` replaced by a link into a protected root: the edit still
    lands in his home, the copy is skipped, nothing lands in the repo."""
    import shutil

    (home_env["repo"] / "backend").mkdir(parents=True, exist_ok=True)
    shutil.rmtree(home_env["home"] / ".trash")
    os.symlink(home_env["repo"] / "backend", home_env["home"] / ".trash")
    await _edit(command="create", path="n.txt", file_text="one\n")
    await _edit(command="create", path="n.txt", file_text="two\n")
    assert (home_env["home"] / "n.txt").read_text() == "two\n"
    assert sorted(p.name for p in (home_env["repo"] / "backend").iterdir()) == []


@pytest.mark.asyncio
async def test_an_overwrite_keeps_the_files_mode(home_env):
    p = home_env["home"] / "run.sh"
    await _edit(command="create", path=str(p), file_text="echo 1\n")
    p.chmod(0o755)
    await _edit(command="create", path=str(p), file_text="echo 2\n")
    await _edit(command="str_replace", path=str(p), old_str="2", new_str="3")
    assert p.read_text() == "echo 3\n"
    assert (p.stat().st_mode & 0o777) == 0o755
