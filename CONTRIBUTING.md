# Contributing to Edward

Thanks for your interest. Edward is one person's assistant that runs on their own Mac, so a change here lands in someone's daily life: it can text their friends, push to their phone, or forget what they told it. The habits below exist because each of them once went wrong.

Open an issue first to talk about what you would like to change.

## Setup

You need a Mac with Apple silicon (`setup.sh` stops on an Intel Mac or in a Rosetta shell), the Xcode command-line tools, [Homebrew](https://brew.sh) and an [Anthropic API key](https://console.anthropic.com/) if you want to run Edward itself. The tests need no key.

```bash
git clone https://github.com/ben4mn/meet-edward.git && cd meet-edward
./setup.sh --with-test-db     # everything, plus the tests' own Postgres on :5433
```

**If this Mac does not run an Edward yet**, start this one as the README says (`./restart.sh` by hand, or the LaunchAgents) and open http://localhost:3000 on this Mac to choose a password, with the one-time setup code from `cat backend/.setup_code`.

**If you already run Edward on this Mac**, don't start a second one from your development clone: no `./restart.sh`, no `scripts/install-launchagents.sh`, no `./setup.sh --launchagents` there. The LaunchAgent labels belong to your macOS user, not to a checkout, so the installer in a second clone would unload your running Edward's agents and load agents that run the clone instead; ports 8000 and 3000 are your Edward's too. (`./restart.sh` itself refuses, exit 78, to restart agents that run another checkout.) Develop against the test cluster (:5433, below) and the UX harness (ports 3100 and 8100, [below](#frontend-checks)), which never touch the Edward you run.

Keep development out of the checkout that serves you: that checkout's code is live at the next restart, and a build there replaces what your phone loads. Work in a git worktree or a second clone. A worktree has no `backend/.venv` of its own; use the serving checkout's python by its path.

## Project layout

```
meet-edward/
├── backend/            FastAPI + LangGraph (Python 3.12)
│   ├── main.py         startup order, routers
│   ├── services/       memory, the turn and its toolset, the mind, heartbeat, pings, media, the computer …
│   ├── routers/        the API
│   ├── scripts/        backups, evals, backfills, maintenance
│   └── tests/          pytest (runs on the :5433 test cluster)
├── frontend/           Next.js PWA (TypeScript): Today, Chat, the Edward tab, /screen, /hub
│   └── __tests__/      node --test
├── chrome-extension/   Edward's optional hands in your Chrome
├── companion/          EdwardBar, a menu-bar companion
├── scriptable/         the iPhone widget scripts
├── scripts/            test cluster, LaunchAgents, health check, shared shell helpers
├── docs/architecture/  one page per subsystem
├── site/               meet-edward.com (Next.js static export)
├── setup.sh            first-time setup (safe to re-run)
└── restart.sh          restart this checkout's backend and frontend
```

[CLAUDE.md](CLAUDE.md) is the operating guide and [docs/architecture/](docs/architecture/README.md) has the long form. Read the page for the subsystem you are changing before you change it.

## Red, then green

Start every change with a test that fails:

1. Write the test that pins the behaviour you want (or the bug you are fixing).
2. Run it and watch it fail, for the reason you expect. A test that passes before the fix proves nothing.
3. Make the change, run the test green, then run the neighbouring tests.

Name the tests in your commit message, so a reader can find what pins the change.

## Backend tests

The backend suite runs on its **own Postgres cluster on :5433**, never on the database Edward uses. The fixtures truncate tables before each test, and a full run on the live cluster once stalled it and starved the running backend.

```bash
scripts/test-pg-cluster.sh ensure          # create or start the test cluster (setup.sh --with-test-db does this)
cd backend
../scripts/with-test-db-lock.sh .venv/bin/python -m pytest tests/test_<name>.py -q
```

- **Always go through `scripts/with-test-db-lock.sh`.** It starts the cluster, creates the database, drops `DATABASE_URL` from the test's environment, refuses anything on :5432 (exit 78) and takes a per-database lock so two runs never truncate each other (exit 75 when it gives up waiting).
- **Never run the suite with your live `DATABASE_URL` exported.** The conftest never uses a database whose name does not end in `_test` and refuses a `*_test` database on :5432, but don't lean on that.
- For a parallel run, export `TEST_DATABASE_URL=postgresql+asyncpg://edward:edward@localhost:5433/<name>_test`; the wrapper creates it.
- Tests can never reach a real model (the conftest refuses Anthropic's and OpenAI's hosts at the transport; `@pytest.mark.real_llm` is the only way through), Chrome, `osascript`, Messages, Mail, EventKit, the display or a real Codex login. Stub a model where the code looks it up, not only where it is defined.
- `EDWARD_FAKE_EMBEDDINGS=1` swaps the embedding model for deterministic fakes, for machines that cannot download it.
- Every test is bounded at 120 s (`pytest.ini`). A full run takes a few minutes; on a small Mac that also runs Edward, run one full suite at a time.
- A handful of tests may fail on a clean `main` too. Before blaming your change, run the same tests in a worktree of `main` and compare.

The doc lint (`tests/test_doc_lint.py`) is part of the suite: every path, symbol, route and stream event named in `CLAUDE.md` and `docs/architecture/*.md` must exist, `docs/architecture/api.md` must list every route the app serves, and `CLAUDE.md` must stay under 64 KiB. When you change a subsystem, update its page in `docs/architecture/` in the same change.

## Frontend checks

```bash
cd frontend
npm test                 # node --test over __tests__/
npx tsc --noEmit -p .
npm run lint
```

All three must pass. Lint matters more than it looks: the production build lints, and a failed build falls back to `next dev`, so one unescaped apostrophe in JSX quietly turns a production install into a dev server (`__tests__/buildLint.test.ts` catches it; `tsc` does not). Some tests are guardrails that pin real contracts, for example every backend tool needs a label in `lib/toolLabels.ts`, nothing may print the "AM/PM" clock format, and every settings panel belongs to exactly one room.

Never run `npm run build` in a checkout that is serving Edward. Use `frontend/deploy-build.sh` there, or build in a worktree with its own `NEXT_DIST_DIR`.

**The UX harness** runs the real frontend against a mock API with invented data: no login, no database, no model, nothing leaves the machine. Run it from a worktree (it refuses a checkout that is serving :3000):

```bash
(cd frontend && npm ci)                                 # the worktree's own node_modules, once
bash frontend/scripts/mock-api/run-harness.sh          # start; then open http://localhost:3100/today
```

The mock API runs under the backend's python. A worktree has no venv of its own, so point `EDWARD_HARNESS_PYTHON` at one that has FastAPI, uvicorn and Playwright, such as your main checkout's `backend/.venv/bin/python`. `shoot.py` in the same folder takes screenshots through Playwright's headless shell.

## Shell scripts

`setup.sh`, the LaunchAgent installer, `restart.sh`, the health check and the test cluster have their own tests. They stub `launchctl`, the port listeners and Homebrew, so they never touch the Edward you run. Run the one for what you changed:

```bash
bash scripts/test_setup.sh
bash scripts/test_install_launchagents.sh
bash scripts/test_restart.sh
bash scripts/test_health_check.sh
bash scripts/test_test_pg_cluster.sh
bash scripts/test_pin_requirements.sh
bash frontend/test_ensure_build.sh
```

If you change backend dependencies, keep `backend/constraints.txt` in step (`scripts/pin_requirements.sh` writes it from a working install). An unpinned rebuild once broke reading older conversations. `psycopg-binary` stays pinned at `psycopg`'s version: Postgres 16 is keg-only in Homebrew, so without the bundled libpq the backend cannot import on a fresh Mac, and `pin_requirements.sh` refuses to write a constraints file that drops it or pins a different version.

## The evals cost money

Three checks run the real model on the test database, with every outward act recorded and never sent:

| Script | Run it when you change | Cost |
|---|---|---|
| `backend/scripts/judgement_eval.py` | the mind's prompt, its tools or the `mind` route | about $1–4 a run |
| `backend/scripts/ping_eval.py` | the ping turn (its trigger, prompt or scope) or the `chat` route | about $1.50 |
| `backend/scripts/notice_eval.py` | the noticer or the gate that decides what to say, and before `EDWARD_PROACTIVITY` goes past `off` | cents; about $0.40 more with `--speak` |

They need `ANTHROPIC_API_KEY` and spend it. Each script's docstring has its exact command; exit code 2 from the judgement check is a safety failure. Say in your pull request which ones you ran and what they reported. A maintainer can run them if you would rather not spend the money.

## ADAPT notes

Edward was built for one setup: Messages signed into the owner's Apple ID, Full Disk Access for one python binary, a kiosk at a pet station. Where code assumes something like that, it carries a comment:

```python
# ADAPT: <what is bespoke, in one line>.
#   How it works here: <the assumption, concretely>.
#   To adapt: <what someone with a different setup changes or sets>.
```

When you add a seam like that, mark it the same way and add it to [docs/ADAPTING.md](docs/ADAPTING.md). When you remove one, remove its entry. Find them all with `grep -rn "ADAPT:" --exclude-dir=node_modules --exclude-dir=.venv backend frontend scripts companion chrome-extension scriptable`.

## Privacy

Edward reads someone's messages, mail and calendar. Nothing from a real life belongs in this repo.

- **Never commit personal data**: no real names, phone numbers, email addresses, street addresses, message text, photos, calendar entries, hostnames or paths from your machine. Not in code, tests, fixtures, docs, screenshots or commit messages.
- **Fixtures are invented.** Use made-up people and places: a partner Rowan, a friend Parker, a dog Biscuit, the host `my-mac.local`, the domain `edward.example.com`; places like Riverside Fest at Riverside Park, the Lantern Cafe on Oak Street, links on `tickets.example`.
- **Phone numbers** come from the fictional 555-01xx range (`+15555550100`, `+12015550123`), never your own area code. **IP addresses** come only from the documentation ranges, `192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24` and `2001:db8::/32` (private and loopback addresses are fine too); `backend/tests/test_public_no_routable_ips.py` fails on any other routable address in the tree.
- **The owner is a setting.** Code that names the owner reads `backend/services/owner.py` (`EDWARD_OWNER_NAME` and friends) and writes model-facing text with the `{OWNER}` token; host facts come from `backend/services/host_config.py`. Never hard-code a name or a path.
- `.env`, `backend/.jwt_secret`, `backend/.setup_code`, `backend/hub_seed.json` and `storage/` are gitignored. Keep it that way, and check `git diff --staged` before you commit.
- Screenshots come from the UX harness, never from your own Edward.
- Found something personal in the repo or its history? Open an issue that says where, without quoting it.

## Pull requests

- One change per pull request, with the tests that pin it.
- Say what changed and why, which checks you ran, and which evals (if any).
- Code style: follow the file you are in. Python has no enforced formatter; TypeScript is linted by `eslint-config-next`. For the site, `cd site && npm run lint`.
- Commit messages say what changed (`fix(pings): …`, `feat(hub): …`) and name the tests.

Questions? Open an issue, or read the [docs](https://meet-edward.com/docs).
