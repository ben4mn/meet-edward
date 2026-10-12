"""A persistent, sandboxed ``bash`` per conversation.

One ``/bin/bash --noprofile --norc`` runs under ``sandbox-exec -f
profile.sb`` with :func:`services.computer.home.shell_env`. It is spawned
with ``start_new_session=True`` so the harness owns its process group and
can kill the whole tree on timeout, restart or shutdown — from outside the
sandbox, so the ``(deny signal (target others))`` rail does not block the
harness.

Each command runs between sentinel markers so the harness knows exactly
when it finished and with what exit code. A non-zero exit is a *normal*
result (the command ran); a tool error is only a harness outcome: a
timeout, an out-of-memory kill, a refused rail, or the sandbox failing to
start.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from services import secret_scan
from services.computer import home, profile, rails
from services.toolset.result import ToolFailure

logger = logging.getLogger(__name__)

try:  # psutil powers the RSS watchdog; degrade gracefully without it.
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None  # type: ignore

# --------------------------------------------------------------- knobs --
DEFAULT_TIMEOUT_S = float(os.getenv("EDWARD_SHELL_TIMEOUT_S", "180"))
MAX_SESSIONS = int(os.getenv("EDWARD_SHELL_MAX_SESSIONS", "4"))
MAX_RSS_MB = int(os.getenv("EDWARD_SHELL_MAX_RSS_MB", "2048"))
OUTPUT_CAP = 30000
_HEAD = 8000
_TAIL = 20000
_WATCH_SLICE_S = 5.0
_KILL_GRACE_S = 3.0
_INIT_TIMEOUT_S = 60.0

_SANDBOX_EXEC = "/usr/bin/sandbox-exec"
_BASH = "/bin/bash"


@dataclass
class ShellResult:
    text: str
    exit_code: int
    cwd: str
    timed_out: bool = False
    killed: bool = False
    duration_ms: int = 0
    rail_hit: Optional[str] = None
    # Why the harness killed the shell (memory, disk, a dead shell) — a
    # tool error; ``exited``: the command itself ended the shell (``exit``),
    # which is a normal result.
    kill_reason: Optional[str] = None
    exited: bool = False


def _sandbox_enabled() -> bool:
    return os.getenv("EDWARD_COMPUTER_SANDBOX", "on").strip().lower() != "off"


def _sessions_dir() -> Path:
    d = home.computer_dir() / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------- the session --

class ShellSession:
    """One live bash. Not safe for concurrent commands — the per-thread turn
    lock upstream already serialises a thread's calls."""

    def __init__(self, conversation_id: str):
        self.conversation_id = conversation_id
        self.sentinel = "__EDWARD_SENTINEL_" + uuid.uuid4().hex
        self.session_id = uuid.uuid4().hex
        self._proc: Optional[asyncio.subprocess.Process] = None
        self.pgid: Optional[int] = None
        self.cwd = str(home.edward_home())
        self.created_at = time.time()
        self.last_used = time.time()
        self._sentinel_re = re.compile(
            rf"(?m)^{re.escape(self.sentinel)} (-?\d+) (.*)$")
        self._pidfile = _sessions_dir() / f"{self.session_id}.pid"
        self._kill_reason: Optional[str] = None
        self._exited = False
        # Commands the pool has handed this session and not yet seen back:
        # a session in use is never evicted (review 2026-10-11).
        self.in_use = 0

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        env = home.shell_env()
        env["__EDWARD_SHELL_SESSION"] = self.session_id
        if _sandbox_enabled():
            prof = profile.profile_path()
            if not prof.exists():
                profile.write_profile()
            if not os.path.exists(_SANDBOX_EXEC):
                raise ToolFailure("my computer is unavailable (sandbox-exec is missing)")
            argv = [_SANDBOX_EXEC, "-f", str(prof), _BASH, "--noprofile", "--norc"]
        else:
            logger.warning("[computer] UNSANDBOXED shell (EDWARD_COMPUTER_SANDBOX=off)")
            argv = [_BASH, "--noprofile", "--norc"]
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(home.edward_home()),
                env=env,
                start_new_session=True,
                limit=8 * 1024 * 1024,
            )
        except (OSError, ValueError) as exc:
            raise ToolFailure(f"my computer is unavailable (shell failed to start: {exc})")
        try:
            self.pgid = os.getpgid(self._proc.pid)
        except OSError:
            self.pgid = self._proc.pid
        self._write_pidfile()
        await self._init()

    def _write_pidfile(self) -> None:
        try:
            info = f"{self.pgid}\n{self.session_id}\n{self.created_at}\n"
            self._pidfile.write_text(info, encoding="utf-8")
        except OSError:
            pass

    async def _init(self) -> None:
        venv = home.edward_home() / ".venv"
        init = "\n".join([
            "exec 2>&1",
            "exec 9>&1",
            f"export __EDWARD_SENTINEL='{self.sentinel}'",
            f"cd {_q(str(home.edward_home()))} 2>/dev/null || cd ~",
            # Lazily create his venv (Homebrew's python3.12, else any python3
            # on PATH); later sessions skip it.
            f'if [ ! -x {_q(str(venv / "bin" / "python"))} ]; then '
            f'for __py in {_q(home.homebrew_prefix() + "/bin/python3.12")} python3.12 python3; do '
            f'if command -v "$__py" >/dev/null 2>&1; then "$__py" -m venv {_q(str(venv))} >/dev/null 2>&1; break; fi; '
            f'done; unset __py; true; fi',
        ])
        try:
            await self._exchange(init, _INIT_TIMEOUT_S)
        except Exception as exc:  # the shell is unusable
            await self.kill()
            raise ToolFailure(f"my computer is unavailable (shell init failed: {exc})")

    # -- running a command ----------------------------------------------

    async def run(self, command: str, timeout_s: float = DEFAULT_TIMEOUT_S) -> ShellResult:
        self.last_used = time.time()
        if not self.alive:
            await self.start()
        started = time.time()
        self._kill_reason, self._exited = None, False
        text, rc, timed_out, killed = await self._exchange(command, timeout_s)
        duration_ms = int((time.time() - started) * 1000)
        return ShellResult(
            text=text, exit_code=rc, cwd=self.cwd,
            timed_out=timed_out, killed=killed, duration_ms=duration_ms,
            kill_reason=self._kill_reason, exited=self._exited,
        )

    async def _exchange(self, command: str, timeout_s: float):
        """Write a command block, read until the sentinel. Returns
        ``(output, rc, timed_out, killed)``; ``rc`` is -1 when the shell
        died or was killed."""
        proc = self._proc
        assert proc is not None and proc.stdin is not None and proc.stdout is not None
        block = command if command.endswith("\n") else command + "\n"
        payload = (
            "{ " + block + "\n} < /dev/null\n"
            f'__rc=$?; printf \'\\n%s %d %s\\n\' "$__EDWARD_SENTINEL" $__rc "$PWD" >&9\n'
        )
        try:
            proc.stdin.write(payload.encode("utf-8", errors="replace"))
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError):
            self._kill_reason = "the shell had died before the command ran"
            return ("", -1, False, True)

        buf = bytearray()
        deadline = time.time() + timeout_s
        last_watch = time.time()
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                await self.kill()
                return (self._strip_output(buf), -1, True, True)
            slice_s = min(_WATCH_SLICE_S, remaining)
            try:
                chunk = await asyncio.wait_for(proc.stdout.read(65536), timeout=slice_s)
            except asyncio.TimeoutError:
                reason = self._over_resource()
                if reason:
                    self._kill_reason = reason
                    await self.kill()
                    return (self._strip_output(buf), -1, False, True)
                continue
            except asyncio.CancelledError:
                await self.kill()
                raise
            if chunk == b"":
                if self._proc is not proc or self._kill_reason:
                    # The harness killed it (``kill``), not the command.
                    self._kill_reason = self._kill_reason or "the harness stopped the shell"
                    return (self._strip_output(buf), -1, False, True)
                self._exited = True  # EOF: the shell exited (``exit`` in the command)
                return (self._strip_output(buf), -1, False, True)
            buf += chunk
            m = self._sentinel_re.search(buf.decode("utf-8", errors="replace"))
            if m:
                text = buf.decode("utf-8", errors="replace")
                rc = int(m.group(1))
                self.cwd = (m.group(2) or self.cwd).strip() or self.cwd
                out = text[: m.start()]
                if out.endswith("\n"):
                    out = out[:-1]
                return (out, rc, False, False)
            if time.time() - last_watch >= _WATCH_SLICE_S:
                last_watch = time.time()
                reason = self._over_resource()
                if reason:
                    self._kill_reason = reason
                    await self.kill()
                    return (self._strip_output(buf), -1, False, True)

    def _strip_output(self, buf: bytearray) -> str:
        text = buf.decode("utf-8", errors="replace")
        m = self._sentinel_re.search(text)
        return text[: m.start()] if m else text

    def _over_resource(self) -> Optional[str]:
        """Why this session's tree must die — over the RSS ceiling, or the
        disk below the floor — else None. Either triggers a group kill."""
        free = rails._disk_is_low()
        if free is not None:
            logger.warning("[computer] killing shell %s: disk low", self.conversation_id)
            return (f"the disk is nearly full ({free:.1f} GB free) — Postgres and the backups need it; "
                    f"clean ~/edward-home first")
        if psutil is None or self._proc is None:
            return None
        try:
            parent = psutil.Process(self._proc.pid)
            rss = parent.memory_info().rss
            for child in parent.children(recursive=True):
                try:
                    rss += child.memory_info().rss
                except Exception:
                    pass
            if rss > MAX_RSS_MB * 1024 * 1024:
                logger.warning("[computer] killing shell %s: RSS %dMB > %dMB",
                               self.conversation_id, rss // (1024 * 1024), MAX_RSS_MB)
                return (f"it used over {MAX_RSS_MB} MB of memory — this Mac has 8 GB shared with Edward "
                        f"and Postgres")
        except Exception:
            pass
        return None

    # -- teardown --------------------------------------------------------

    async def kill(self, reason: Optional[str] = None) -> None:
        """Kill the session's process group. ``reason`` says why, so a
        command it cuts off reads as a tool error, never as the command
        having exited."""
        if reason and not self._kill_reason:
            self._kill_reason = reason
        proc, self._proc = self._proc, None
        if proc is None:
            return
        pgid = self.pgid
        if pgid is not None:
            _killpg(pgid, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), timeout=_KILL_GRACE_S)
            except asyncio.TimeoutError:
                _killpg(pgid, signal.SIGKILL)
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except Exception:
            pass
        try:
            self._pidfile.unlink()
        except OSError:
            pass


def _q(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def _killpg(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


# ----------------------------------------------------------- the pool --

class SessionPool:
    def __init__(self):
        self._sessions: Dict[str, ShellSession] = {}
        self._lock = asyncio.Lock()

    async def run(self, conversation_id: str, command: Optional[str] = None,
                  restart: bool = False, timeout_s: float = DEFAULT_TIMEOUT_S) -> ShellResult:
        if not conversation_id:
            raise ToolFailure("no conversation context for the shell")
        async with self._lock:
            session = self._sessions.get(conversation_id)
            if restart:
                if session is not None:
                    await session.kill()
                    self._sessions.pop(conversation_id, None)
                session = None
            if session is None or not session.alive:
                session = await self._spawn(conversation_id)
            session.last_used = time.time()
            session.in_use += 1

        try:
            if restart and not command:
                return ShellResult(text="(shell restarted — cwd and env reset)",
                                   exit_code=0, cwd=session.cwd)
            if command is None:
                raise ToolFailure("bash needs either command or restart")

            # Mistake guard before the command.
            refusal = rails.precheck(command)
            if refusal:
                raise ToolFailure(refusal)

            # Syntax check so a stray brace/quote can't hang the session.
            syntax = await _syntax_error(command)
            if syntax:
                raise ToolFailure(f"bash syntax error: {syntax}")

            result = await session.run(command, timeout_s=timeout_s)
            if not session.alive and self._sessions.get(conversation_id) is session:
                self._sessions.pop(conversation_id, None)
            return result
        finally:
            session.in_use -= 1

    async def _spawn(self, conversation_id: str) -> ShellSession:
        # At capacity: evict the least recently used session that is not
        # running anything. A session mid-command is never killed for
        # another thread (review 2026-10-11: a build cut off mid-way read as
        # the command having exited).
        if len(self._sessions) >= MAX_SESSIONS:
            idle = [k for k, s in self._sessions.items() if s.in_use == 0]
            if not idle:
                raise ToolFailure(f"all {MAX_SESSIONS} shells are busy with other threads' commands — "
                                  f"try again in a minute")
            victim_id = min(idle, key=lambda k: self._sessions[k].last_used)
            victim = self._sessions.pop(victim_id)
            await victim.kill(reason="evicted for another thread")
        session = ShellSession(conversation_id)
        await session.start()
        self._sessions[conversation_id] = session
        return session

    async def shutdown_all(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for s in sessions:
            await s.kill()

    def snapshot(self) -> List[dict]:
        return [
            {"conversation_id": s.conversation_id, "alive": s.alive,
             "pgid": s.pgid, "cwd": s.cwd, "idle_s": int(time.time() - s.last_used)}
            for s in self._sessions.values()
        ]


async def _syntax_error(command: str) -> Optional[str]:
    """Run ``bash -n`` on the command (unsandboxed, no execution) to catch an
    unbalanced quote or brace before it hangs the session."""
    try:
        proc = await asyncio.create_subprocess_exec(
            _BASH, "--noprofile", "--norc", "-n", "-c", command,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
    except Exception:
        return None  # can't check — let the real shell try
    if proc.returncode and proc.returncode != 0:
        msg = (out or b"").decode("utf-8", errors="replace").strip()
        return msg.splitlines()[-1] if msg else "unbalanced command"
    return None


# ---------------------------------------------------------- the module --

_pool = SessionPool()


async def run(conversation_id: str, command: Optional[str] = None,
              restart: bool = False, timeout_s: float = DEFAULT_TIMEOUT_S) -> ShellResult:
    """Run one command in ``conversation_id``'s persistent shell, or restart
    it. Applies the mistake-guard precheck and the syntax check; the Seatbelt
    rails are enforced by the kernel."""
    return await _pool.run(conversation_id, command=command, restart=restart, timeout_s=timeout_s)


def format_for_model(result: ShellResult) -> str:
    """The text the model sees: redacted, capped (full spilled to
    ``~/edward-home/.out``), with the exit-line footer and any rail hint.
    """
    body, _ = secret_scan.redact_secrets(result.text or "", secret_scan.known_secrets())
    hint = rails.rail_hint(body)
    if len(body) > OUTPUT_CAP:
        spill = _spill(body)
        body = (body[:_HEAD] + f"\n…[{len(body) - _HEAD - _TAIL} chars cut — full output: {spill}]…\n"
                + body[-_TAIL:])
    secs = result.duration_ms / 1000.0
    footer = f"[exit {result.exit_code} · {secs:.1f}s · {result.cwd}]"
    # A harness outcome is a tool error, and its line leads: the executor
    # (``toolset.result.normalize``) reads the start of the text.
    if result.timed_out:
        error = (f"Tool error: the command ran past {int(DEFAULT_TIMEOUT_S)}s and was killed; the shell "
                 f"restarted (cwd and env reset). For long work: nohup CMD > ~/edward-home/jobs/NAME.log "
                 f"2>&1 & — then check the log (schedule a wake to come back).")
        return f"{error}\n{body}" if body else error
    if result.killed and not result.exited:
        error = (f"Tool error: the command was killed — {result.kill_reason or 'the shell died'}; "
                 f"the shell restarted (cwd and env reset).")
        return f"{error}\n{body}" if body else error
    out = f"{body}\n{footer}" if body else footer
    if result.exited:
        out += "\n[the shell exited; the next command starts a fresh shell in ~/edward-home]"
    if hint:
        out += f"\n{hint}"
    return out


def _spill(text: str) -> str:
    """Keep a long output in ``~/edward-home/.out``: always a new file, never
    through a link and never inside a protected root (the backend writes it,
    outside the sandbox, into a directory the shell can change)."""
    out_dir = home.edward_home() / ".out"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex[:12]}.txt"
        fd, _ = rails.open_checked(out_dir / name, write=True, create=True, exclusive=True, mode=0o600)
        try:
            rails.write_fd(fd, text.encode("utf-8"))
        finally:
            os.close(fd)
        return str(out_dir / name)
    except OSError:  # RailRefused included
        return "(unavailable)"


# ------------------------------------------------- startup/shutdown ----

def sweep_stale_sessions() -> None:
    """Kill process groups recorded by a previous run — but only when the
    group leader is still one of Edward's shells (its command line names the
    profile or the session marker), so a reused PID that now belongs to
    Postgres or Chrome is never killed (the pidfile is just dropped)."""
    sdir = _sessions_dir()
    for pf in sdir.glob("*.pid"):
        try:
            parts = pf.read_text(encoding="utf-8").split("\n")
            pgid = int(parts[0].strip())
            session_id = parts[1].strip() if len(parts) > 1 else ""
        except (OSError, ValueError, IndexError):
            _safe_unlink(pf)
            continue
        if _is_edward_shell(pgid, session_id):
            _killpg(pgid, signal.SIGTERM)
            time.sleep(0.2)
            _killpg(pgid, signal.SIGKILL)
        _safe_unlink(pf)


def _is_edward_shell(pgid: int, session_id: str) -> bool:
    if psutil is None:
        return False
    try:
        proc = psutil.Process(pgid)  # the group leader's pid == pgid
    except Exception:
        return False
    try:
        cmdline = " ".join(proc.cmdline())
        env_marker = ""
        try:
            env_marker = (proc.environ() or {}).get("__EDWARD_SHELL_SESSION", "")
        except Exception:
            env_marker = ""
        prof = str(profile.profile_path())
        return (prof in cmdline) or (session_id and env_marker == session_id)
    except Exception:
        return False


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


async def shutdown_all() -> None:
    await _pool.shutdown_all()


def session_snapshot() -> List[dict]:
    return _pool.snapshot()
