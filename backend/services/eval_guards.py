"""Process guards for an eval that runs a real model with Edward's tools
(the judgement check, the ping check, ``notice_eval --speak``).

Why: an eval runs the real speaker with the real tools, and a tool like
``lookup_contact`` runs ``open -ga Contacts`` and AppleScript against the
owner's real address book on the host Mac. The rail: **no test, harness or
eval ever sends a real message or touches the owner's Chrome or
osascript.** The evals' transport recorders hold every send; this module
holds the rest, at the process level:

1. **No osascript, no ``open``.** ``subprocess.Popen._execute_child`` (so
   ``run``/``call``/``check_*``, every ``Popen`` however imported, and
   asyncio's ``create_subprocess_exec``/``_shell``), ``os.system``,
   ``os.posix_spawn``/``posix_spawnp`` and ``os.exec*`` refuse
   ``osascript``, ``open``, ``screencapture``, ``pkill``, ``killall`` and
   ``launchctl`` in command position — behind ``env``/``nohup``/``sudo``…
   and inside ``sh -c`` scripts — and any argument mentioning
   ``Google Chrome`` or ``osascript``.
2. **Never Edward's live local services** (the backend, the frontend, the
   graph worker, the WhatsApp bridge, which sends): loopback connections
   to their ports are refused.
3. **Chrome helpers faked** (conftest's ``_never_touch_chrome``):
   ``screen_window_service.ensure_screen_tab``, ``recover_hands``,
   ``ensure_in_background``; ``browser_ext_service._schedule_recovery``.
4. **A scratch computer**: ``EDWARD_HOME`` and ``EDWARD_COMPUTER_DIR``
   under ``~/edward-scratch/evals/<run>/`` (the live state dir's sweep
   kills the process groups recorded there); ``EDWARD_COMPUTER_SANDBOX=off``
   is refused.

``install_for_eval`` is what the eval scripts call: it takes the model's
provider and key from ``.env`` when the shell set none (nothing else from
it) and installs the guards. ``Guards.uninstall()`` restores everything
(tests).
"""

from __future__ import annotations

import os
import re
import shlex
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, List, Optional, Sequence

REFUSED_COMMANDS = frozenset({"osascript", "open", "pkill", "killall", "launchctl", "screencapture"})
REFUSED_SUBSTRINGS = ("google chrome", "osascript")
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish", "csh", "tcsh"})
WRAPPERS = frozenset({"env", "nohup", "sudo", "xargs", "exec", "command", "time", "nice", "caffeinate",
                      "timeout", "arch", "stdbuf", "nocorrect", "builtin"})
SHELL_OPERATORS = frozenset({";", "&", "|", "&&", "||", "(", ")", "{", "}", "!", "\n", ";;", "|&",
                             "then", "do", "else", "elif", "if", "while", "until"})
# Edward's live local services: the backend, the frontend, the graph worker,
# the WhatsApp bridge's REST API (it sends), the UX harness.
LIVE_LOCAL_PORTS = frozenset({8000, 3000, 8001, 8080, 3100, 8100})
LIVE_STATE_DIR = Path("~/.edward-computer")
SCRATCH_ROOT = Path("~/edward-scratch/evals")
EXEC_NAMES = ("execv", "execve", "execvp", "execvpe")


class EvalGuardRefused(RuntimeError):
    """An eval tried something that could reach the owner's Chrome, their
    screen, a person or a live process."""


# ----------------------------------------------------------- the checker --

def _base(word: str) -> str:
    return os.path.basename(word.strip().strip("`'\"").rstrip("/")).lower()


def _is_assignment(word: str) -> bool:
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", word))


def _shell_commands(script: str, depth: int) -> Iterator[str]:
    """The words in command position in a shell script (every word when it
    cannot be parsed: fail closed)."""
    try:
        lex = shlex.shlex(script, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        lex.whitespace = " \t\r"
        tokens: List[str] = []
        for tok in lex:
            tokens.extend(p for p in re.split(r"(`|\$\()", tok) if p)
    except ValueError:
        for word in re.findall(r"[\w./-]+", script):
            yield _base(word)
        return
    expect, i = True, 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in SHELL_OPERATORS or tok in ("`", "$(") or tok.endswith((";", "&", "|")):
            expect = True
        elif expect:
            if not _is_assignment(tok):
                yield from _argv_commands(tokens[i:], depth + 1, stop_at_operator=True)
                expect = False
        i += 1


def _argv_commands(argv: Sequence[str], depth: int = 0, *, stop_at_operator: bool = False) -> Iterator[str]:
    """The commands an argv runs: argv[0], what a wrapper runs, and what a
    shell's ``-c`` script runs."""
    if depth > 8 or not argv:
        return
    words = list(argv)
    if stop_at_operator:
        for k, w in enumerate(words):
            if w in SHELL_OPERATORS or w in ("`", "$("):
                words = words[:k]
                break
    if not words:
        return
    name = _base(words[0])
    yield name
    rest = words[1:]
    if name in WRAPPERS:
        j = 0
        while j < len(rest) and (rest[j].startswith("-") or _is_assignment(rest[j]) or rest[j].isdigit()):
            j += 1
        yield from _argv_commands(rest[j:], depth + 1, stop_at_operator=stop_at_operator)
    elif name in SHELLS:
        for k, w in enumerate(rest):
            if w.startswith("-") and not w.startswith("--") and "c" in w[1:] and k + 1 < len(rest):
                yield from _shell_commands(rest[k + 1], depth + 1)
                break


def refusal_reason(argv: Any, executable: Any = None) -> Optional[str]:
    """Why an eval may not run ``argv``, or None. A string (or bytes) is a
    shell script. Pure."""
    if isinstance(argv, bytes):
        argv = argv.decode("utf-8", "replace")
    if isinstance(argv, str):
        parts = ["/bin/sh", "-c", argv]
    else:
        parts = [p.decode("utf-8", "replace") if isinstance(p, bytes) else str(p) for p in (argv or [])]
    text_parts = list(parts)
    if executable:
        exe = executable.decode("utf-8", "replace") if isinstance(executable, bytes) else str(executable)
        text_parts.append(exe)
        if _base(exe) in REFUSED_COMMANDS:
            return f"{_base(exe)} is refused in an eval"
    lowered = " ".join(text_parts).lower()
    for needle in REFUSED_SUBSTRINGS:
        if needle in lowered:
            return f"an eval never runs anything mentioning {needle!r}"
    for name in _argv_commands(parts):
        if name in REFUSED_COMMANDS:
            return f"{name} is refused in an eval"
    return None


def connect_refusal(family: int, address: Any) -> Optional[str]:
    """Why a socket may not connect to ``address``, or None. Pure."""
    if family == getattr(socket, "AF_UNIX", object()):
        return None
    if not (isinstance(address, tuple) and len(address) >= 2):
        return None
    host = address[0].decode("ascii", "replace") if isinstance(address[0], bytes) else str(address[0])
    try:
        port = int(address[1])
    except (TypeError, ValueError):
        return None
    loopback = host.lower() in ("localhost", "localhost.", "ip6-localhost")
    if not loopback:
        try:
            import ipaddress

            loopback = ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
        except ValueError:
            loopback = False
    if loopback and port in LIVE_LOCAL_PORTS:
        return f"an eval never talks to Edward's live local service on :{port}"
    return None


def check_state_dir(state_dir: Path) -> None:
    live = LIVE_STATE_DIR.expanduser().resolve()
    target = Path(state_dir).expanduser().resolve()
    if target == live or live in target.parents or target in live.parents:
        raise EvalGuardRefused(f"the computer state dir may not be the live {LIVE_STATE_DIR}")


# ---------------------------------------------------------------- install --

@dataclass
class Spawner:
    """What actually starts a process, beneath the guard. Tests pass a fake
    so a guard bug can never run osascript from a test."""
    execute_child: Callable[..., Any]
    system: Callable[[Any], int]
    posix_spawn: Optional[Callable[..., int]] = None
    posix_spawnp: Optional[Callable[..., int]] = None
    execs: dict = field(default_factory=dict)


def real_spawner() -> Spawner:
    return Spawner(execute_child=subprocess.Popen._execute_child,  # type: ignore[attr-defined]
                   system=os.system, posix_spawn=getattr(os, "posix_spawn", None),
                   posix_spawnp=getattr(os, "posix_spawnp", None),
                   execs={n: getattr(os, n) for n in EXEC_NAMES if hasattr(os, n)})


@dataclass
class Guards:
    run: str
    home: Path
    state_dir: Path
    source: str = "in-repo"
    refused: List[str] = field(default_factory=list)
    _undo: List[Callable[[], None]] = field(default_factory=list, repr=False)

    def uninstall(self) -> None:
        while self._undo:
            try:
                self._undo.pop()()
            except Exception:  # noqa: BLE001
                pass
        global _INSTALLED
        if _INSTALLED is self:
            _INSTALLED = None


_INSTALLED: Optional[Guards] = None


def installed() -> Optional[Guards]:
    return _INSTALLED


def _setattr(guards: Guards, obj: Any, name: str, value: Any) -> None:
    missing = object()
    old = getattr(obj, name, missing)
    setattr(obj, name, value)

    def undo() -> None:
        if old is missing:
            try:
                delattr(obj, name)
            except AttributeError:
                pass
        else:
            setattr(obj, name, old)

    guards._undo.append(undo)


def _setenv(guards: Guards, environ: Any, name: str, value: str) -> None:
    old = environ.get(name)
    environ[name] = value

    def undo() -> None:
        if old is None:
            environ.pop(name, None)
        else:
            environ[name] = old

    guards._undo.append(undo)


def _refuser(guards: Guards) -> Callable[..., None]:
    def refuse(argv: Any, executable: Any = None) -> None:
        reason = refusal_reason(argv, executable)
        if reason:
            guards.refused.append(reason)
            raise EvalGuardRefused(reason)
    return refuse


def _install_process_guard(guards: Guards, spawner: Spawner) -> None:
    refuse = _refuser(guards)
    inner_child = spawner.execute_child

    def guarded_execute_child(self, args, executable, *rest, **kw):
        # _execute_child(self, args, executable, preexec_fn, close_fds, pass_fds,
        #                cwd, env, startupinfo, creationflags, shell, …)
        shell = bool(kw["shell"]) if "shell" in kw else (bool(rest[7]) if len(rest) > 7 else False)
        if isinstance(args, (str, bytes)):
            refuse(args, executable)
        elif shell and args:
            refuse(["/bin/sh", "-c", *[a.decode() if isinstance(a, bytes) else str(a) for a in args]], executable)
        else:
            refuse(list(args), executable)
        return inner_child(self, args, executable, *rest, **kw)

    _setattr(guards, subprocess.Popen, "_execute_child", guarded_execute_child)
    inner_system = spawner.system

    def guarded_system(command):
        refuse(command)
        return inner_system(command)

    _setattr(guards, os, "system", guarded_system)
    for name, inner in (("posix_spawn", spawner.posix_spawn), ("posix_spawnp", spawner.posix_spawnp)):
        if inner is None or not hasattr(os, name):
            continue

        def make(inner_fn):
            def guarded_spawn(path, argv, env, *a, **k):
                refuse(list(argv), path)
                return inner_fn(path, argv, env, *a, **k)
            return guarded_spawn

        _setattr(guards, os, name, make(inner))
    for name, inner in (spawner.execs or {}).items():
        if not hasattr(os, name):
            continue

        def make_exec(inner_fn):
            def guarded_exec(path, args, *a, **k):
                refuse(list(args), path)
                return inner_fn(path, args, *a, **k)
            return guarded_exec

        _setattr(guards, os, name, make_exec(inner))


def _install_network_guard(guards: Guards) -> None:
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guarded_connect(self, address):
        reason = connect_refusal(self.family, address)
        if reason:
            guards.refused.append(reason)
            raise EvalGuardRefused(reason)
        return real_connect(self, address)

    def guarded_connect_ex(self, address):
        reason = connect_refusal(self.family, address)
        if reason:
            guards.refused.append(reason)
            raise EvalGuardRefused(reason)
        return real_connect_ex(self, address)

    _setattr(guards, socket.socket, "connect", guarded_connect)
    _setattr(guards, socket.socket, "connect_ex", guarded_connect_ex)


def _fake_chrome_helpers(guards: Guards) -> None:
    """conftest's ``_never_touch_chrome``, plus the extension's recovery."""
    try:
        from services import screen_window_service as sw
    except Exception:  # noqa: BLE001 — nothing to fake; the process guard still holds
        sw = None
    if sw is not None:
        async def fake_ensure(*, url=None):
            return {"ok": True, "opened": False, "faked": True, "reason": "an eval never touches Chrome"}

        async def fake_recover(*, url=None):
            return {"action": "none", "reason": "an eval never touches Chrome"}

        _setattr(guards, sw, "ensure_screen_tab", fake_ensure)
        _setattr(guards, sw, "recover_hands", fake_recover)
        _setattr(guards, sw, "ensure_in_background", lambda *a, **k: None)
    try:
        from services import browser_ext_service as be

        _setattr(guards, be, "_schedule_recovery", lambda *a, **k: None)
    except Exception:  # noqa: BLE001
        pass


def install(*, run: Optional[str] = None, spawner: Optional[Spawner] = None, scratch_root: Optional[Path] = None,
            environ: Any = None, network: bool = True, chrome: bool = True) -> Guards:
    """Every guard above; returns a handle whose ``uninstall()`` restores
    the process. Call it before the eval imports the tools."""
    global _INSTALLED
    if _INSTALLED is not None:
        return _INSTALLED
    env = os.environ if environ is None else environ
    if (env.get("EDWARD_COMPUTER_SANDBOX") or "").strip().lower() in ("off", "0", "false", "no"):
        raise EvalGuardRefused("EDWARD_COMPUTER_SANDBOX=off: an eval never runs with the sandbox off")
    run = run or time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}"
    base = (Path(scratch_root) if scratch_root is not None else SCRATCH_ROOT).expanduser() / run
    home, computer = base / "home", base / "computer"
    check_state_dir(computer)
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    computer.mkdir(parents=True, exist_ok=True, mode=0o700)
    guards = Guards(run=run, home=home, state_dir=computer)
    try:
        _setenv(guards, env, "EDWARD_HOME", str(home))
        _setenv(guards, env, "EDWARD_COMPUTER_DIR", str(computer))
        _install_process_guard(guards, spawner or real_spawner())
        if network:
            _install_network_guard(guards)
        if chrome:
            _fake_chrome_helpers(guards)
    except BaseException:
        guards.uninstall()
        raise
    _INSTALLED = guards
    return guards


PROVIDER_ENV = "EDWARD_LLM_PROVIDER"
# The only keys an eval takes from ``.env``: what it needs to reach the model
# production uses. Everything else stays the shell's.
MODEL_ENV_KEYS = (PROVIDER_ENV, "ANTHROPIC_API_KEY")


def _env_files() -> List[Path]:
    """``.env`` in this checkout, then in the live one (``EDWARD_LIVE_REPO``)
    when that is somewhere else — an eval usually runs from a worktree."""
    here = Path(__file__).resolve().parents[2]
    roots = [here]
    try:
        from services.host_config import repo_root

        live = repo_root().resolve()
        if live != here:
            roots.append(live)
    except Exception:  # noqa: BLE001 — this checkout's .env still counts
        pass
    return [r / ".env" for r in roots]


def _read_env_file(path: Path) -> dict:
    """``KEY=value`` lines from a ``.env`` file ({} when it is unreadable)."""
    try:
        text = path.read_text()
    except OSError:
        return {}
    out = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        out[key.strip()] = value
    return out


def productions_model_env(files: Optional[Sequence[Path]] = None, environ: Any = None) -> Optional[str]:
    """A gate runs on the provider production runs: ``EDWARD_LLM_PROVIDER``
    (and the Anthropic key) from ``.env`` unless the shell set them, so a
    gate run from a bare shell scores the scenarios on the model the install
    really uses (once the code default and production differed, and every
    scenario ran on the wrong one). Unset, the code default is Anthropic.
    Prints which provider it is and where it came from; returns it."""
    env = os.environ if environ is None else environ
    paths = list(files) if files is not None else _env_files()
    where = "the shell" if env.get(PROVIDER_ENV) else None
    for key in MODEL_ENV_KEYS:
        if key in env:  # the shell chose (an empty value too)
            continue
        for path in paths:
            value = (_read_env_file(path).get(key) or "").strip()
            if value:
                env[key] = value
                if key == PROVIDER_ENV:
                    where = str(path)
                break
    chosen = env.get(PROVIDER_ENV)
    print(f"(model provider: {chosen or 'anthropic'} — {where if chosen else 'the code default'})")
    return chosen


def install_for_eval(name: str, **_older_keywords: Any) -> str:
    """The eval scripts' entry: production's model settings from ``.env``
    (``productions_model_env``), then every guard above. Returns
    ``"in-repo"`` (the scripts print it). Never runs unguarded. Keywords
    older callers pass are accepted and ignored."""
    productions_model_env()
    install(run=f"{name}-" + time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}")
    return "in-repo"
