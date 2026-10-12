"""The docs say only what is true: CLAUDE.md and docs/architecture/*.md.

Why (followups M7, 2026-09-27): CLAUDE.md loads into every Claude session
and every subagent in this repo, and it had drifted to ~200 KB (~50K
tokens) while naming modules, routes and tests that were gone — the waitlist
router, service and table (deleted in ef5db5c), the LangSmith trace
inspection and four debug routes (c3d553e). A reader trusts what the guide
names, so a name that no longer exists sends the next session looking for
code that is not there. The rewrite moved the long form into
docs/architecture/; this lint keeps both honest:

(a) every backticked (or fenced ``bash``) token that looks like a repo path
    exists — ``backend/…``, ``frontend/…``, ``scripts/…``, ``docs/…``,
    ``chrome-extension/…`` and the shorthands ``services/…``, ``routers/…``,
    ``tests/…``, ``graph/…`` (backend), ``lib/…``, ``components/…``,
    ``app/…``, ``hooks/…``, ``__tests__/…``, ``public/…``, ``hub/…``,
    ``display/…`` (frontend; the last two under ``app/(display)/``). Globs,
    ``<placeholders>`` and ``{a,b}`` alternatives are allowed; ``file::symbol``
    and ``module.symbol`` also check that the symbol is still in the file.
    Bare test names (``test_x.py``, ``x.test.ts``) and bare module names
    (``x_service.py``, ``Foo.tsx``) must exist somewhere in the tree, and
    relative markdown links (with their ``#anchor``) must resolve;
(b) every ``METHOD /api/…`` route the docs name is served by the FastAPI
    app, and docs/architecture/api.md — which claims to be the full list —
    names every route the app serves;
(c) CLAUDE.md stays under a size budget so it does not bloat back;
(d) the SSE event tables (CLAUDE.md and api.md) name exactly the event types
    the chat stream can emit — the old table listed 8 of 23.

Names that are deliberately historical (a deleted module named in an
incident note, a route listed as gone) live in the allowlists below, each
with its reason and the documents allowed to say it. An allowlisted name
must really be gone — the lint fails when one comes back, so the allowlist
cannot rot into a blanket pass.

Fix drift in the docs, not by allowlisting. To lint a draft or an old
version of CLAUDE.md, point ``EDWARD_DOC_LINT_CLAUDE_MD`` at the file (its
paths still resolve against this repo).
"""

from __future__ import annotations

import glob
import itertools
import os
import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
FRONTEND = REPO / "frontend"
ARCH_DIR = REPO / "docs" / "architecture"


def _claude_md() -> Path:
    override = os.environ.get("EDWARD_DOC_LINT_CLAUDE_MD")
    return Path(override) if override else REPO / "CLAUDE.md"


# CLAUDE.md is the short operating guide (~48 KB after the 2026-09-27
# rewrite). 64 KiB is ~16K tokens in every session; past it, move the long
# form into docs/architecture/ instead of raising this number.
CLAUDE_MD_BUDGET_BYTES = 64 * 1024

# main's CLAUDE.md just before the rewrite: ~200 KB with the waitlist and
# LangSmith drift. The lint must keep catching what that version got wrong.
BLOATED_CLAUDE_MD_COMMIT = "49153d5"

# ---------------------------------------------------------------------------
# Allowlists — names the docs mention on purpose although they are gone.
# token (as written, after cleaning) -> the documents allowed to say it.
# ---------------------------------------------------------------------------

HISTORICAL_PATHS: dict[str, tuple[str, ...]] = {
    # LangSmith trace inspection, removed in c3d553e; the docs say it is gone.
    "langsmith_service.py": ("CLAUDE.md", "api.md"),
    # The old claude-agent-sdk runner, deleted as dead code.
    "claude_code_service.py": ("codex-and-orchestrator.md",),
    # The mind rebuild's deletions (Phases 3, 5, 7, 8), named where mind.md
    # says what each wake replaced.
    "wakeup_service.py": ("mind.md",),
    "wakeup_signals.py": ("mind.md",),
    "routers/wakeup.py": ("mind.md",),
    "notification_dispatcher.py": ("mind.md",),
    "services/proactive/morning_briefing.py": ("mind.md",),
    "meeting_prep.py": ("mind.md",),
    "open_loop_nudge.py": ("mind.md",),
    "pattern_detection.py": ("mind.md",),
    "relationship_maintenance_job.py": ("mind.md",),
    "daily_execution_planner.py": ("mind.md",),
    "meeting_context_engine.py": ("mind.md",),
    # The surplus queue, retired by the proactivity rebuild (2026-10-11);
    # surplus.md is its retirement page.
    "services/surplus_service.py": ("surplus.md",),
    "surplus_jobs.py": ("surplus.md",),
}

# Built or written at runtime and gitignored, so whether they exist depends on
# the checkout, not on the docs. Never asserted either way.
GENERATED_PATHS: frozenset[str] = frozenset(
    {
        "sw.js",  # next-pwa's service worker, generated per build
        "public/sw.js",
        "backend/.jwt_secret",  # auth_service writes it on first start
    }
)

HISTORICAL_ROUTES: dict[tuple[str, str], tuple[str, ...]] = {
    # api.md's "Gone, and not to be documented again" line.
    ("POST", "/api/waitlist"): ("api.md",),  # deleted in ef5db5c
    ("POST", "/api/orchestrator/tasks"): ("api.md",),
    ("GET", "/api/consolidation/config"): ("api.md",),  # the config is in /status
}

# The backend's namespace. The docs also name routes that are not this app's —
# the Next app's `GET /build-id`, the UX harness's `/__mock/*`, Codex's
# `/backend-api/wham/usage` — and those are not linted here.
OUTSIDE_API_ROUTES = frozenset({"/", "/health"})

# Routes FastAPI adds itself; api.md need not list them.
FRAMEWORK_ROUTES = frozenset(
    {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
)

# ---------------------------------------------------------------------------
# Reading the docs
# ---------------------------------------------------------------------------

EXPLICIT_PREFIXES: dict[str, tuple[Path, ...]] = {
    "backend/": (REPO,),
    "frontend/": (REPO,),
    "docs/": (REPO,),
    "chrome-extension/": (REPO,),
    # scripts/ is the repo's own, backend/scripts or frontend/scripts,
    # depending on the directory a command runs from.
    "scripts/": (REPO, BACKEND, FRONTEND),
}
SHORTHAND_PREFIXES: dict[str, tuple[Path, ...]] = {
    "services/": (BACKEND,),
    "routers/": (BACKEND,),
    "tests/": (BACKEND,),
    "lib/": (FRONTEND, BACKEND),
    "components/": (FRONTEND,),
    "app/": (FRONTEND,),
    "hooks/": (FRONTEND,),
    "__tests__/": (FRONTEND,),
    "public/": (FRONTEND,),
    "graph/": (BACKEND / "services",),
    # relative to the display route group: `hub/lib/*.ts`, `display/lib/…`
    "hub/": (FRONTEND / "app" / "(display)",),
    "display/": (FRONTEND / "app" / "(display)",),
}
PREFIXES = {**EXPLICIT_PREFIXES, **SHORTHAND_PREFIXES}

FILE_EXTENSIONS = {
    "py", "ts", "tsx", "js", "jsx", "mjs", "cjs", "sh", "md", "json", "css",
    "plist", "txt", "sql", "yml", "yaml", "html", "svg", "png", "ini", "toml",
}
TRY_EXTENSIONS = (".py", ".ts", ".tsx", ".js", ".jsx", ".sh", ".md", ".json", ".css")
SKIP_DIRS = {
    "node_modules", ".git", ".venv", "venv", "__pycache__", ".pytest_cache",
    "storage", ".mypy_cache", ".ruff_cache",
}
_METHOD = "(?:GET|POST|PUT|PATCH|DELETE|WS)"
# `GET/PATCH /api/x/{id}`, `POST …/{id}/cancel` (… = the row's last full path)
ROUTE_SPAN = re.compile(rf"^({_METHOD}(?:/{_METHOD})*) ((?:/|…|\.\.\.)\S*)")
FENCE = re.compile(r"^\s*```")
INLINE_CODE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
MD_LINK = re.compile(r"\[(?:[^\]\n]*)\]\(([^)\s]+)\)")


@dataclass(frozen=True)
class Finding:
    doc: str
    line: int
    token: str
    problem: str

    def __str__(self) -> str:
        return f"{self.doc}:{self.line}: `{self.token}` — {self.problem}"


@dataclass(frozen=True)
class Doc:
    name: str  # how findings and allowlists name it: CLAUDE.md, api.md, …
    text: str
    base: Path  # what a relative markdown link resolves against


def _docs() -> list[Doc]:
    docs = [Doc("CLAUDE.md", _claude_md().read_text(), REPO)]
    for path in sorted(ARCH_DIR.glob("*.md")):
        docs.append(Doc(path.name, path.read_text(), path.parent))
    return docs


def split_fences(text: str) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """(prose lines, fenced ``bash``/``sh`` lines), each as (line number, text).
    Other fenced blocks (diagrams, log formats, env files) are neither."""
    prose, shell = [], []
    in_fence, fence_is_shell = False, False
    for number, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            if not in_fence:
                lang = line.strip().strip("`").strip().lower()
                in_fence, fence_is_shell = True, lang in {"bash", "sh", "shell", "zsh"}
            else:
                in_fence = False
            continue
        if not in_fence:
            prose.append((number, line))
        elif fence_is_shell:
            shell.append((number, re.sub(r"\s#.*$", "", line)))
    return prose, shell


def code_spans(text: str) -> list[tuple[int, str]]:
    prose, _ = split_fences(text)
    return [(n, m.group(1)) for n, line in prose for m in INLINE_CODE.finditer(line)]


# ---------------------------------------------------------------------------
# (a) paths
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PathRef:
    token: str  # as cleaned — what an allowlist names
    pattern: str  # repo-relative, prefix kept
    roots: tuple[Path, ...]
    symbol: str | None


def _clean_word(word: str) -> tuple[str, str | None]:
    """Strip what surrounds a path in prose: quotes, sentence punctuation,
    ``./`` and ``../``, a ``::symbol`` or ``:line`` suffix."""
    w = word.strip("\"'")
    symbol = None
    if "::" in w:
        w, rest = w.split("::", 1)
        m = re.match(r"\w+", rest)
        symbol = m.group(0) if m else None
    w = re.sub(r"[.,;:]+$", "", w)
    w = re.sub(r":\d+(?:-\d+)?$", "", w)
    while w.startswith(("./", "../")):
        w = w.split("/", 1)[1]
    return w, symbol


def path_refs(word: str) -> PathRef | None:
    token, symbol = _clean_word(word)
    for prefix, roots in PREFIXES.items():
        if token.startswith(prefix):
            break
    else:
        return None
    head, _, last = token.rpartition("/")
    # `foo.ts(x)` = foo.ts or foo.tsx
    last = re.sub(r"(?<=[A-Za-z0-9])\((\w+)\)$", r"{,\1}", last)
    # `module.fn(args` in prose: the call is not part of the path
    if "(" in last and not last.startswith("("):
        last = last[: last.index("(")]
    return PathRef(token, f"{head}/{last}" if head else last, roots, symbol)


def _expand_braces(pattern: str) -> list[str]:
    m = re.search(r"\{([^{}]*,[^{}]*)\}", pattern)
    if not m:
        return [pattern]
    out = []
    for choice in m.group(1).split(","):
        out.extend(_expand_braces(pattern[: m.start()] + choice + pattern[m.end():]))
    return out


def _to_glob(pattern: str) -> str:
    """`<placeholder>`, `{param}` and `…` stand for any name."""
    pattern = pattern.replace("…", "*")
    pattern = re.sub(r"<[^<>/]+>", "*", pattern)
    pattern = re.sub(r"\{[^{},/]*\}", "*", pattern)
    return pattern


def _glob_matches(root: Path, pattern: str) -> list[Path]:
    if "*" not in pattern and "?" not in pattern:
        path = root / pattern
        return [path] if path.exists() else []
    escaped = re.sub(r"([\[\]])", r"[\1]", pattern)
    return [Path(p) for p in glob.glob(str(root / escaped), recursive=True)]


def _symbol_in(path: Path, symbol: str) -> bool:
    try:
        text = path.read_text(errors="replace")
    except (IsADirectoryError, OSError):
        return True
    return re.search(rf"(?<![\w$]){re.escape(symbol)}(?![\w$])", text) is not None


def resolve_path(ref: PathRef) -> str | None:
    """None when the reference holds, else what is wrong."""
    for variant in _expand_braces(ref.pattern):
        problem = _resolve_one(_to_glob(variant), ref.roots, ref.symbol)
        if problem:
            return problem if variant == ref.pattern else f"{variant}: {problem}"
    return None


def _resolve_one(pattern: str, roots: tuple[Path, ...], symbol: str | None) -> str | None:
    for root in roots:
        hits = _glob_matches(root, pattern)
        if hits:
            if symbol and len(hits) == 1 and hits[0].is_file() and not _symbol_in(hits[0], symbol):
                return f"no `{symbol}` in {hits[0].relative_to(REPO)}"
            return None
    head, _, last = pattern.rpartition("/")
    # an extensionless module: `lib/time`, `lib/utils`
    if "." not in last and "*" not in last:
        for root in roots:
            for ext in TRY_EXTENSIONS:
                if (root / f"{pattern}{ext}").is_file():
                    return None
    # `routers/chat.CLIENT_GONE`, `lib/edwardMood.formatClock`: module.symbol
    stem, dot, attr = last.partition(".")
    if dot and attr.split(".")[-1] not in FILE_EXTENSIONS and "*" not in last:
        name = re.match(r"\w+", attr)
        for root in roots:
            for ext in TRY_EXTENSIONS:
                path = root / (f"{head}/{stem}{ext}" if head else f"{stem}{ext}")
                if path.is_file():
                    if name and not _symbol_in(path, name.group(0)):
                        return f"no `{name.group(0)}` in {path.relative_to(REPO)}"
                    return None
    return "does not exist"


@lru_cache(maxsize=1)
def _basename_index() -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    for top in ("backend", "frontend", "scripts", "chrome-extension", "docs"):
        for dirpath, dirnames, filenames in os.walk(REPO / top):
            dirnames[:] = [
                d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".next")
            ]
            for name in filenames:
                index.setdefault(name, []).append(Path(dirpath))
    for name in os.listdir(REPO):
        if (REPO / name).is_file():
            index.setdefault(name, []).append(REPO)
    return index


BARE_BACKEND_TEST = re.compile(r"^test_\w+\.py$")
BARE_FRONTEND_TEST = re.compile(r"^[\w.-]+\.test\.tsx?$")
BARE_MODULE = re.compile(r"^[A-Za-z_][\w.-]*\.(?:py|ts|tsx|js|sh)$")


def resolve_bare(word: str) -> tuple[str, str | None] | None:
    """(token, problem) for a bare file name, None when the word is not one."""
    token, symbol = _clean_word(word)
    if "/" in token or not BARE_MODULE.match(token):
        return None
    dirs = _basename_index().get(token, [])
    if BARE_BACKEND_TEST.match(token):
        dirs = [d for d in dirs if d == BACKEND or BACKEND / "tests" in (d, *d.parents)]
        where = "backend/tests"
    elif BARE_FRONTEND_TEST.match(token):
        dirs = [d for d in dirs if FRONTEND / "__tests__" in (d, *d.parents)]
        where = "frontend/__tests__"
    else:
        where = "the repo"
    if not dirs:
        return token, f"no such file in {where}"
    if symbol and len(dirs) == 1 and not _symbol_in(dirs[0] / token, symbol):
        return token, f"no `{symbol}` in {(dirs[0] / token).relative_to(REPO)}"
    return token, None


def _heading_slugs(path: Path) -> set[str]:
    slugs: set[str] = set()
    seen: dict[str, int] = {}
    prose, _ = split_fences(path.read_text())
    for _, line in prose:
        m = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if not m:
            continue
        text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", m.group(1))
        slug = re.sub(r"[^\w\- ]", "", text.replace("`", "").lower()).replace(" ", "-")
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        slugs.add(slug if n == 0 else f"{slug}-{n}")
    return slugs


def link_problem(doc: Doc, target: str) -> str | None:
    if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
        return None
    file_part, _, anchor = target.partition("#")
    path = (doc.base / file_part).resolve()
    if not path.exists():
        return "link target does not exist"
    if anchor and path.suffix == ".md" and anchor not in _heading_slugs(path):
        return f"no heading #{anchor} in {path.relative_to(REPO)}"
    return None


def path_findings(docs: list[Doc]) -> list[Finding]:
    findings: list[Finding] = []

    def check_word(doc: Doc, line: int, word: str) -> None:
        ref = path_refs(word)
        if ref is not None:
            token, problem = ref.token, (
                None if ref.token in GENERATED_PATHS else resolve_path(ref)
            )
        else:
            bare = resolve_bare(word)
            if bare is None:
                return
            token, problem = bare
            if token in GENERATED_PATHS:
                problem = None
        if problem and doc.name not in HISTORICAL_PATHS.get(token, ()):
            findings.append(Finding(doc.name, line, token, problem))

    for doc in docs:
        prose, shell = split_fences(doc.text)
        for line, span in code_spans(doc.text):
            if ROUTE_SPAN.match(span):
                continue
            for word in span.split():
                check_word(doc, line, word)
        for line, text in shell:
            for word in text.split():
                check_word(doc, line, word)
        for line, text in prose:
            for m in MD_LINK.finditer(INLINE_CODE.sub("", text)):
                problem = link_problem(doc, m.group(1))
                if problem:
                    findings.append(Finding(doc.name, line, m.group(1), problem))
    return findings


# ---------------------------------------------------------------------------
# (b) routes
# ---------------------------------------------------------------------------


def is_backend_route(path: str) -> bool:
    return path == "/api" or path.startswith("/api/") or path in OUTSIDE_API_ROUTES


def _norm_route(path: str) -> str:
    path = re.sub(r"\{[^{}]*\}", "{}", path)
    return path.rstrip("/") or "/"


def _expand_segment(segment: str) -> list[str]:
    segment = segment.replace("\\|", "|")
    m = re.fullmatch(r"\{([^{}]*,[^{}]*)\}", segment)
    if m:
        return m.group(1).split(",")
    return segment.split("|")


def expand_route(span: str, anchor: str | None) -> tuple[list[str], list[str], str]:
    """(methods, concrete paths, the full path to anchor the next `…/`)."""
    m = ROUTE_SPAN.match(span)
    assert m
    methods = m.group(1).split("/")
    path = m.group(2).split("?", 1)[0]
    if path.startswith(("…", "...")):
        suffix = path.lstrip(".…")
        segments = [s for s in suffix.split("/") if s]
        base = [s for s in (anchor or "").split("/") if s]
        if segments and segments[0] in base:
            base = base[: len(base) - 1 - base[::-1].index(segments[0])]
        path = "/" + "/".join(base + segments)
    parts = path.split("/")[1:]
    concrete = [
        "/" + "/".join(combo)
        for combo in itertools.product(*(_expand_segment(p) for p in parts))
    ] if parts != [""] else ["/"]
    return methods, concrete, path


def documented_routes(doc: Doc) -> list[tuple[int, str, str, str]]:
    """Every (line, span, method, normalised path) a document names."""
    out = []
    anchors: dict[int, str] = {}
    for line, span in code_spans(doc.text):
        if not ROUTE_SPAN.match(span):
            continue
        methods, paths, full = expand_route(span, anchors.get(line))
        if not ROUTE_SPAN.match(span).group(2).startswith(("…", "...")):
            anchors[line] = full
        for method in methods:
            for path in paths:
                out.append((line, span, method, _norm_route(path)))
    return out


@lru_cache(maxsize=1)
def app_routes() -> frozenset[tuple[str, str]]:
    from fastapi.routing import APIRoute, APIWebSocketRoute
    from starlette.routing import WebSocketRoute

    from main import app

    routes = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path:
            continue
        if isinstance(route, (APIWebSocketRoute, WebSocketRoute)):
            routes.add(("WS", _norm_route(path)))
        elif isinstance(route, APIRoute) or getattr(route, "methods", None):
            for method in route.methods or ():
                if method not in ("HEAD", "OPTIONS"):
                    routes.add((method, _norm_route(path)))
    return frozenset(routes)


def route_findings(docs: list[Doc], served: frozenset[tuple[str, str]]) -> list[Finding]:
    findings = []
    for doc in docs:
        for line, span, method, path in documented_routes(doc):
            if not is_backend_route(path) or (method, path) in served:
                continue
            if doc.name in HISTORICAL_ROUTES.get((method, path), ()):
                continue
            findings.append(Finding(doc.name, line, span, f"{method} {path} is not served"))
    return findings


# ---------------------------------------------------------------------------
# (d) the chat stream's event types
# ---------------------------------------------------------------------------

SSE_SECTIONS = {"CLAUDE.md": "SSE event protocol", "api.md": "The chat stream (SSE)"}


def _section(text: str, title: str) -> list[str]:
    out, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line[3:].strip().lower() == title.lower()
            continue
        if inside:
            out.append(line)
    return out


def sse_table_types(text: str, title: str) -> set[str]:
    """The event types in the first column of the section's table."""
    types: set[str] = set()
    for line in _section(text, title):
        if line.startswith("|") and not set(line) <= set("|-: "):
            types |= set(re.findall(r"`(\w+)`", line.split("|")[1]))
    return types


def stream_event_types() -> set[str]:
    """What the stream can carry: every `EventType` plus the types the chat
    router writes itself (the voice memo's `user_message`)."""
    from services.graph.streaming import EventType

    declared = {v for k, v in vars(EventType).items() if k.isupper() and isinstance(v, str)}
    router = (BACKEND / "routers" / "chat.py").read_text()
    return declared | set(re.findall(r'"type":\s*"(\w+)"', router))


# ---------------------------------------------------------------------------
# The tests
# ---------------------------------------------------------------------------


def _report(findings: list[Finding]) -> str:
    return "\n".join(str(f) for f in findings)


def test_every_path_the_docs_name_exists():
    findings = path_findings(_docs())
    assert not findings, (
        "The docs name paths that do not exist — fix the doc, or, for a name "
        "that is deliberately historical, add it to HISTORICAL_PATHS with its "
        "reason:\n" + _report(findings)
    )


def test_every_route_the_docs_name_is_served():
    findings = route_findings(_docs(), app_routes())
    assert not findings, (
        "The docs name routes the app does not serve:\n" + _report(findings)
    )


def test_api_md_lists_every_route_the_app_serves():
    api = next(d for d in _docs() if d.name == "api.md")
    listed = {(method, path) for _, _, method, path in documented_routes(api)}
    missing = sorted(
        f"{method} {path}"
        for method, path in app_routes()
        if (method, path) not in listed and path not in FRAMEWORK_ROUTES
    )
    assert not missing, (
        "docs/architecture/api.md says it lists every route; add these:\n"
        + "\n".join(missing)
    )


def test_claude_md_stays_under_its_size_budget():
    size = _claude_md().stat().st_size
    assert size <= CLAUDE_MD_BUDGET_BYTES, (
        f"CLAUDE.md is {size:,} bytes (budget {CLAUDE_MD_BUDGET_BYTES:,}). It loads "
        "into every session: move the long form into docs/architecture/."
    )


@pytest.mark.parametrize("doc_name", sorted(SSE_SECTIONS))
def test_the_sse_tables_name_every_event_the_stream_carries(doc_name):
    doc = next(d for d in _docs() if d.name == doc_name)
    documented = sse_table_types(doc.text, SSE_SECTIONS[doc_name])
    emitted = stream_event_types()
    assert documented == emitted, (
        f"{doc_name}'s SSE table is out of step with EventType and routers/chat.py — "
        f"missing {sorted(emitted - documented)}, not emitted {sorted(documented - emitted)}"
    )


def test_the_architecture_index_links_every_file():
    readme = (ARCH_DIR / "README.md").read_text()
    linked = {m.group(1).partition("#")[0] for m in MD_LINK.finditer(readme)}
    unlisted = sorted(
        p.name for p in ARCH_DIR.glob("*.md") if p.name != "README.md" and p.name not in linked
    )
    assert not unlisted, f"docs/architecture/README.md does not link: {unlisted}"


def test_allowlisted_names_are_really_gone():
    back = [t for t in HISTORICAL_PATHS if _is_live_path(t)]
    served = app_routes()
    routes_back = [f"{m} {p}" for (m, p) in HISTORICAL_ROUTES if (m, p) in served]
    assert not back and not routes_back, (
        "These allowlisted names exist again — drop them from the allowlist: "
        f"{back + routes_back}"
    )


def _is_live_path(token: str) -> bool:
    ref = path_refs(token)
    if ref is not None:
        return resolve_path(ref) is None
    bare = resolve_bare(token)
    return bare is not None and bare[1] is None


# --- the lint itself ---------------------------------------------------------


def test_the_lint_reads_the_shorthand_the_docs_use():
    doc = Doc(
        "probe.md",
        "\n".join(
            [
                "`services/graph/streaming.py::create_event` `lib/time.ts::parseIso`",
                "`routers/chat.CLIENT_GONE` `lib/edwardMood.formatClock` `lib/time`",
                "`__tests__/**/*.test.ts(x)` `services/voice/*_protocol.py`",
                "`app/(app)/settings/*` `docs/architecture/<name>.md` `tests/test_<name>.py`",
                "`scripts/test-pg-cluster.sh ensure` `cd backend && ../scripts/with-test-db-lock.sh`",
                "`test_doc_lint.py` `toolLabels.test.ts` `mind_service.py` [doc](docs/UPGRADING.md)",
                "```bash",
                "bash frontend/deploy-build.sh   # frontend/nothing-here.sh is a comment",
                "```",
            ]
        ),
        REPO,
    )
    assert path_findings([doc]) == []


def test_the_lint_catches_what_is_gone():
    doc = Doc(
        "probe.md",
        "\n".join(
            [
                "`backend/routers/waitlist.py` `services/langsmith_service.py`",
                "`lib/time.ts::noSuchFunction` `routers/chat.NO_SUCH_THING`",
                "`test_no_such_thing.py` `noSuchThing.test.ts` `no_such_module.py`",
                "[gone](docs/architecture/no-such-file.md) [bad anchor](docs/architecture/api.md#no-such-heading)",
                "```bash",
                "bash frontend/no-such-script.sh",
                "```",
            ]
        ),
        REPO,
    )
    problems = {f.token: f.problem for f in path_findings([doc])}
    assert set(problems) == {
        "backend/routers/waitlist.py",
        "services/langsmith_service.py",
        "lib/time.ts",
        "routers/chat.NO_SUCH_THING",
        "test_no_such_thing.py",
        "noSuchThing.test.ts",
        "no_such_module.py",
        "docs/architecture/no-such-file.md",
        "docs/architecture/api.md#no-such-heading",
        "frontend/no-such-script.sh",
    }
    assert "noSuchFunction" in problems["lib/time.ts"]


def test_the_lint_expands_the_route_shorthand():
    doc = Doc(
        "probe.md",
        "| Workers | `GET /api/orchestrator/tasks/{id}`, `GET …/{id}/events`, "
        "`POST …/{id}/cancel`, `GET /api/databases/{name}/tables`, "
        "`GET …/tables/{table}/columns`, `POST /api/push/subscribe\\|unsubscribe`, "
        "`GET /api/display/{signals,alerts}`, `GET /api/widget?token=`, `WS /api/voice/ws` |",
        REPO,
    )
    routes = {(m, p) for _, _, m, p in documented_routes(doc)}
    assert routes == {
        ("GET", "/api/orchestrator/tasks/{}"),
        ("GET", "/api/orchestrator/tasks/{}/events"),
        ("POST", "/api/orchestrator/tasks/{}/cancel"),
        ("GET", "/api/databases/{}/tables"),
        ("GET", "/api/databases/{}/tables/{}/columns"),
        ("POST", "/api/push/subscribe"),
        ("POST", "/api/push/unsubscribe"),
        ("GET", "/api/display/signals"),
        ("GET", "/api/display/alerts"),
        ("GET", "/api/widget"),
        ("WS", "/api/voice/ws"),
    }


def test_the_lint_still_catches_the_bloated_claude_md():
    """The version the rewrite replaced: ~200 KB, naming the deleted waitlist
    and LangSmith code. Pinned so a loosened lint shows up here."""
    try:
        old = subprocess.run(
            ["git", "-C", str(REPO), "show", f"{BLOATED_CLAUDE_MD_COMMIT}:CLAUDE.md"],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git history is not available in this checkout")
    assert len(old.encode()) > CLAUDE_MD_BUDGET_BYTES
    doc = Doc("CLAUDE.md", old, REPO)
    paths = {f.token for f in path_findings([doc])}
    assert {
        "backend/services/langsmith_service.py",
        "backend/services/waitlist_service.py",
        "backend/routers/waitlist.py",
    } <= paths
    routes = {f.token for f in route_findings([doc], app_routes())}
    assert {
        "POST /api/waitlist",
        "GET /api/debug/langsmith/status",
        "GET /api/debug/traces/{conversation_id}",
    } <= routes
    old_sse = sse_table_types(old, SSE_SECTIONS["CLAUDE.md"])
    assert old_sse and not {"reasoning", "progress", "user_message"} & old_sse
