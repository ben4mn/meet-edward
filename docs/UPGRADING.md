# Upgrading from the March 2026 release

This page is for an Edward installed from the March 2026 public release (the `main` branch up to commit `408e1d5`, or the `v1.0.0` tag before it) that you want to move onto this release without losing what he knows. A fresh install needs none of this: follow the [README](../README.md#quick-start).

The short version: stop Edward, back up the database, pull, move the old Python environment aside, run `./setup.sh`, tidy `.env`, start him. The first start migrates the database in place. Plan on half an hour, most of it installs and the first frontend build.

## What changes

Almost everything around the memory store. Your memories, documents, conversations, files, scheduled events, password and settings are kept. What Edward can do, and how he does it, is new:

- **Every model call goes through the Claude API** with one route per purpose and a **daily spend cap** ($5 by default). The model you picked in March is replaced by the route's model.
- **One toolset of 14 tools on every surface**, and his own sandboxed computer (`bash`, an editor, a headless browser). The Python/JavaScript/SQL sandboxes, Lightpanda and self-evolution are gone.
- **The heartbeat's triage is replaced by the mind**: wakes, follow-through, a silent nightly, a journal, and (opt-in) noticing.
- **@Edward pings in iMessage are answered** by default — see [step 8](#8-check-what-he-listens-to).
- **The app is new**: Today is the front door, the Edward tab replaces the old settings page, and the frontend runs as a production build.

The full list is at the end: [removed](#removed), [dormant](#dormant) and [new](#new).

## 1. Stop Edward

The March `restart.sh` started the backend on :8000 and `npm run dev` on :3000. Stop both:

```bash
lsof -t -iTCP:8000 -sTCP:LISTEN | xargs kill
lsof -t -iTCP:3000 -sTCP:LISTEN | xargs kill
```

If you made your own launchd job or login item for the March Edward, unload it first, or it will start him again.

## 2. Back up

```bash
PG="$(brew --prefix postgresql@16)/bin"
mkdir -p ~/edward-backups
"$PG/pg_dump" -Fc -d edward -f ~/edward-backups/edward-march-final.dump
"$PG/pg_restore" --list ~/edward-backups/edward-march-final.dump | head    # a readable table of contents
```

Also copy the files the database does not hold:

```bash
cp .env ~/edward-backups/env-march
cp backend/.jwt_secret ~/edward-backups/jwt_secret-march 2>/dev/null
cp -R backend/storage ~/edward-backups/storage-march 2>/dev/null     # files Edward saved and you uploaded
```

Keep the dump until you have used the new Edward for a while. The name `edward-march-final.dump` stays out of the nightly backup's rotation, which only prunes `edward-db-*.dump`.

## 3. Pull the release

```bash
git pull
```

If you changed files in the March code, put them on a branch first (`git stash`, or commit to a branch). This release rewrites most files, so expect to port your changes by hand rather than merge them.

If `git pull` will not fast-forward, a fresh clone works just as well: clone next to the old checkout, copy `.env`, `backend/.jwt_secret` and `backend/storage/` into it, and carry on from step 5 there (the database is shared, not part of the checkout).

## 4. Move the old Python environment aside

```bash
mv backend/.venv backend/.venv.march
```

The March `setup.sh` built the venv with whatever `python3` was on your PATH. This release needs Homebrew's Python 3.12 (`setup.sh` stops on any other version) and installs pinned versions from `backend/constraints.txt`; a clean environment is simplest. Delete `.venv.march` once you are happy.

## 5. Run setup

```bash
./setup.sh
```

This release runs only on a Mac with Apple silicon. On an Intel Mac, in a Rosetta (x86_64) terminal, or with an x86_64 Python, `setup.sh` stops before it installs anything: the pinned PyTorch behind the embedding model has no x86_64 macOS wheel.

It keeps your `.env` and only asks for settings that are empty: your name (`EDWARD_OWNER_NAME`), the API key if it is missing, and your time zone. It installs `python@3.12`, `postgresql@16` and `node` if needed, builds pgvector for Postgres 16 when it is missing (the March setup used Homebrew's pgvector bottle, which no longer ships for Postgres 16), creates the venv, installs Playwright's headless shell, runs `npm ci`, and builds the Swift media and EventKit helpers.

At the end it prints the path of the python that needs **Full Disk Access**. Grant it even if you granted one in March: the permission belongs to a binary's path, and the new environment may well run on a different python than March's did. Without it, Edward cannot read Messages, Mail, Contacts, Calendar or Reminders. If you start him by hand with `./restart.sh` rather than the LaunchAgents, macOS charges your terminal app instead, so grant that too ([ADAPTING.md](ADAPTING.md#3-macos-permissions)).

## 6. Update `.env`

Compare yours with [`.env.example`](../.env.example), which describes every setting with its default.

**Where it lives, and who wins.** The backend reads only the `.env` at the repo root. Earlier start scripts fell back to `backend/.env`; if yours is there, move it up (`start.sh` warns while it is the only one). The start scripts no longer export the file into the backend's start-up environment (other processes of your user can read that, Edward's shell included): the backend loads it itself, and a variable your shell or launchd sets now wins over `.env` (the start script used to source `.env` over it). A program you put only in `~/.local/bin` or `~/bin` (the `codex` CLI, a standalone `uvx`, the `claude` CLI) is not on the backend's PATH any more; install it with Homebrew or link it into Homebrew's `bin` ([ops-health-backups.md](architecture/ops-health-backups.md#the-host-mac)).

| Setting | What to do |
|---|---|
| `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT` | **Delete.** LangSmith support is gone, but LangChain itself still reads `LANGCHAIN_TRACING_V2`, and while it is set (with a key) it would keep sending traces of Edward's LangGraph runs to LangSmith. |
| `GITHUB_TOKEN` | Delete; nothing reads it now. |
| `ANTHROPIC_API_KEY` | Keep. |
| `EDWARD_LLM_PROVIDER` | `anthropic` (the default; `setup.sh` writes it). |
| `EDWARD_OWNER_NAME`, `EDWARD_OWNER_FULL_NAME`, `EDWARD_OWNER_ALIASES` | New. What Edward calls you, and the other names you go by. |
| `EDWARD_OWNER_HANDLES`, `EDWARD_OWNER_EMAILS` | New. Your phone numbers, Apple ID and email addresses: how he recognises your own messages and your self-chat, and whom he never texts or emails on his own. Set them if he reads your messages. |
| `TIMEZONE` | New. Your IANA zone (`setup.sh` asks). |
| `EDWARD_INSTRUCTION_HORIZON_SINCE` | New. **Set it to the day you upgrade** (an ISO date such as `2026-10-12`). Weekly decay lets a temporary instruction that is not provably yours lapse 30 days after it was written. A March memory is provably yours only when it came from one of your chats; the rest (the notes March's Listening saved, for one) would lapse at the first decay pass. Instructions written before this date are left alone. |
| `DATABASE_URL` | Keep. The March `postgresql://` form still works. |
| `TWILIO_*` | Inbound webhooks now check Twilio's signature and are refused without `TWILIO_AUTH_TOKEN`. **Only texts from your own numbers get an answer** (`EDWARD_OWNER_HANDLES`): March answered anyone who texted Edward's number; now anyone else's text is held in the ledger, unanswered, with no push to you. |
| `EDWARD_TWILIO_ANSWER_ANYONE` | New, default off. `on` brings back answering everyone. The risk: a turn can read your calendar, memories and messages, and its answer is texted to whoever wrote, so anyone who learns the number can try to talk Edward into texting them your data. |
| `HTML_HOSTING_URL`, `HTML_HOSTING_API_KEY` | There is no default host any more; set both if you used hosted pages. |
| `MCP_APPLE_ENABLED` | Unchanged. If bun or your apple-mcp clone are not at `~/.bun/bin/bun` and `~/apple-mcp`, set `BUN_PATH` and `MCP_APPLE_DIR`. |
| `VAPID_*` | Unchanged. |
| `EDWARD_PUBLIC_BASE_URL` | New. Where you open Edward when it is not `http://localhost:3000` (a tunnel's https address): links in pushes and texts use it. |
| `EDWARD_COOKIE_SECURE` | New. `off` only for a plain-HTTP setup on a network you trust; the cookie was always `Secure` in March. |
| `EDWARD_DAILY_SPEND_CAP_USD` | New, default 5. March had no cap. |
| `EDWARD_FRIENDS_FULL_TOOLSET` | New, default off. A friend's `@Edward` gets no shell, file editor, Chrome or screens, and `look` only at what they sent or a link; `on` gives friends everything, at the risk that their message drives a shell running as you ([pings.md](architecture/pings.md#the-reply-turn)). |
| `EDWARD_CHROME_HANDS`, `EDWARD_CHROME_EXTENSION_ID` | New, both optional. The hands in your Chrome are off until you set `EDWARD_CHROME_HANDS=on` and load `chrome-extension/`. Leave `EDWARD_CHROME_EXTENSION_ID` unset: the extension's manifest fixes its ID (`appfgnpjahkedldopocalbkoommcalae`), the only one accepted by default. If you loaded `chrome-extension/` from a copy older than that fixed ID, remove it in `chrome://extensions` and load it again (its ID changes), and update or unset an `EDWARD_CHROME_EXTENSION_ID` that names the old ID. |

## 7. Start him

Pick one of two ways.

**Always on**, started at login and restarted by launchd if he stops:

```bash
bash scripts/install-launchagents.sh
```

**Or by hand**, running until you stop him or the Mac restarts:

```bash
./restart.sh
```

Not both: once the agents are installed, `./restart.sh` restarts them through launchd, and the installer stops an Edward that `./restart.sh` started from this checkout before it loads its own.

The first start takes a few minutes: it builds the frontend (production build now, not `next dev`) and migrates the database. `./restart.sh` waits up to 2 minutes for the backend and 4 for the frontend, then says "still starting" and leaves it running (exit 0). Watch `/tmp/edward-backend.log`. Then open [http://localhost:3000](http://localhost:3000) and sign in with your March password (`backend/.jwt_secret` is unchanged, so open sessions usually survive). Your March password carries over, so there is nothing to do about the new one-time setup code: it is only for an install with no password, and the first start deletes any leftover code file. If your March Edward never had a password, this release asks for one, together with the setup code, and only on the Mac Edward runs on: run `cat backend/.setup_code` in the checkout there, then open http://localhost:3000 on that Mac (no screen: `ssh -L 3000:localhost:3000`, and read the code over ssh). Until then the API answers nothing but the sign-in pages. `./setup.sh`, `./restart.sh` and `scripts/install-launchagents.sh` print "First time? Your setup code is in backend/.setup_code (cat backend/.setup_code)" until a password is set, and also whenever the backend has not answered yet (`./setup.sh` runs while Edward is stopped, so it prints it during an upgrade too): with your March password, ignore it.

## 8. Check what he listens to

Open the Edward tab (Settings) and go through these once:

- **Mind → Skills.** Your March switches carry over, and some now cover more: Shell/Bash is his whole computer (`bash` and the editor, in his sandbox), Web Search gates both search and fetch, iMessage gates reading and sending messages. The rows for Code Interpreter, JavaScript and SQL say "Retired": his shell runs python, node and sqlite3 now.
- **Attention → Listening.** Your March Listening settings are kept. March turned Messages on by default, so if Edward read your iMessages then, he reads them now — and he now **answers @Edward in any chat, from anyone** (friends inside limits and without his shell, your Chrome or the screens, unknown numbers held for you), from your own iMessage account, signed as Edward. If you do not want that, turn Messages off here. `EDWARD_PINGS=off` alone is not enough: the mind then answers mentions itself.
- **House → Locale.** The migration stamps a time zone on your March settings (America/Chicago), and the app replaces it with your browser's zone the first time you load it. Check that it is right: reminders, quiet hours, the nightly and "today" all follow it. The backend log warns at startup when the setting and the Mac's own zone differ.
- **Work → Events.** Your March reminders and recurring tasks are kept. Check that their times look right in your zone.
- **Attention → Proactive.** The March morning briefing and its siblings are gone (see below). Noticing replaces them and is off until you set `EDWARD_PROACTIVITY`.

## 9. Optional: his persona

Your March settings keep the March system prompt ("You are Edward (Enhanced Digital Workflow Assistant for Routine Decisions)…"). It still works. To take this release's persona instead, which names you from `EDWARD_OWNER_NAME`:

```bash
cd backend
.venv/bin/python - <<'EOF'
import asyncio
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path("..") / ".env")
from models import SettingsUpdate
from models.schemas import DEFAULT_SYSTEM_PROMPT
from services.settings_service import update_settings
asyncio.run(update_settings(SettingsUpdate(system_prompt=DEFAULT_SYSTEM_PROMPT)))
print("persona updated")
EOF
```

## 10. Optional: backfills

Three one-off scripts tidy data written before this release. Each can report first; read the counts before you let it write. They connect with `DATABASE_URL` from your environment (the titles script also reads `.env`), else the `edward` database `setup.sh` made.

```bash
cd backend

# Memories: the migration marks every March memory as heard from you, about you.
# This re-marks rows it can trace to a message thread (read) or to Edward's screen pulse (his own thought).
.venv/bin/python -m scripts.backfill_memory_origin            # report
.venv/bin/python -m scripts.backfill_memory_origin --apply    # write

# Listening's iMessage events: re-decode message text the March decoder garbled.
# Reads ~/Library/Messages/chat.db, so the terminal you run it from needs Full Disk Access.
.venv/bin/python scripts/backfill_heartbeat_attributedbody.py            # report
.venv/bin/python scripts/backfill_heartbeat_attributedbody.py --apply    # write

# iMessage conversations still titled with a bare phone number. Useful after Listening
# has run for a while. Note the flag: this one writes unless you pass --dry-run.
.venv/bin/python scripts/backfill_imessage_titles.py --dry-run
.venv/bin/python scripts/backfill_imessage_titles.py
```

`backfill_memory_origin` often finds nothing on a March install: March's Listening saved its notes without a conversation, so they cannot be traced and stay marked as yours. You can see where any memory came from in Settings → Memory → Memories.

## What the first start does to the database

`init_db` (in `backend/services/database.py`) migrates in place, every start, and changes nothing it has already changed:

- **Adds columns** to the March tables. Memories gain `origin` and `about` (March rows get `heard` and `owner`), use counts and a `source_ref`. Settings gain `provider`, `debug_streaming` and `timezone`. `heartbeat_config` gains the mind's columns (`mind_enabled` is on) and `imessage_owner_handles`. Conversations gain a `source` (March conversations become `user`).
- **Creates the tables** this release added (about sixty: the mind's notices and journal, ledgers, pings, the hub, push and spend logs, Gmail and more).
- **Drops** the self-evolution tables `evolution_config` and `evolution_history`. Its other drops name tables that only existed in builds after March, so they do nothing to a March database.
- **Keeps** the tables of features that are gone or dormant (`triage_results`, `open_loops`, `proactive_config`, `orchestrator_tasks`, `claude_code_sessions`, `persistent_databases`) with their rows.

The scheduler then registers Edward's own events (the 03:00 nightly, free time at 13:00, a five-minute sweep and the screens' pulse). They are plumbing and never show on Today.

Old conversations stay in the list. Their saved LangGraph state was written by whatever versions your March install happened to have, and this release pins a known-good set. Not every March combination has been tried; if an old conversation will not open, the rest of Edward is unaffected — please open an issue with the lines from `/tmp/edward-backend.log`.

## Removed

| March feature | Now |
|---|---|
| Self-evolution (Edward proposing, testing and deploying changes to his own code) | Gone, tables dropped. He keeps a **wishlist** for you instead (Settings → Work → Wishlist), and never promises to build what he wishes for. |
| Browser automation through Lightpanda | Gone. His own invisible browser is Playwright's headless shell (`setup.sh` installs it), and with `EDWARD_CHROME_HANDS=on` he can use your Chrome through `chrome-extension/`. |
| Python, JavaScript and SQL sandboxes | Gone. His sandboxed shell (`bash`) runs python, node and sqlite3. |
| LangSmith tracing | Gone. Each turn's reasoning, tools and memory hits are stored with the answer and shown in the chat's work rail. |
| Morning briefing, meeting prep, open-loop nudges, pattern detection | Gone. Wakes (follow-through on promises, the nightly) and opt-in noticing replace them. |
| The heartbeat's triage (dismiss / note / act / escalate) and its calendar listener | Gone. Listening stores what arrives and hands it to the mind; the calendar is read when it is needed. |
| Claude Code workers through the Claude Agent SDK | Gone (see dormant workers below). |
| Self-service install of custom MCP servers | Edward can no longer install servers himself. Servers you installed in March still load at startup, and you can switch, restart or remove them in Settings → Mind → Servers. |
| The persistent-database, file-storage and hosted-page tools | Out of his toolset (he has his own home folder). Your saved files and databases are kept and still browsable in Settings → Memory. |

## Dormant

**Workers and coding sessions.** The orchestrator, its routes and the Workers panel are still in the tree, and March tasks stay in their table, but no tool in the default toolset starts a worker any more: long work is a background job in his own shell plus a scheduled wake. The coding-session code now drives the Codex CLI, inside a sandbox, and is equally dormant.

## New

- **Memory with provenance**: who said it, about whom, a tier, a source; "how do you know that?"; weekly decay that never deletes and holds sensitive things; contradiction flags; a forget that previews exactly what it removes.
- **His computer**: a persistent sandboxed shell in `~/edward-home`, an editor, web search and fetch, a headless browser, `look` for images and PDFs.
- **The mind**: wakes, follow-through, the silent nightly, one budgeted block of free time a day, a thought journal with your verdicts, and opt-in noticing that says a thing once, outside quiet hours.
- **Pings and media in iMessage**: @Edward in any chat, photos, PDFs, voice messages and videos understood.
- **The app**: Today, a chat work rail that shows his thinking and tools live, live voice (ElevenLabs), the Edward tab, `/screen`, the household hub, an iPhone widget through Scriptable, push with quiet hours.
- **Integrations**: Gmail (OAuth); Calendar and Reminders read from the Mac's own databases and changed through EventKit when you ask; Alexa and Home Assistant.
- **Guard rails**: an egress secret scan, optional approvals, per-turn and daily dollar caps; an API that answers nothing but the sign-in pages until a password is set (the first one only with a one-time code read on the host Mac); Edward's shell can't read his own credentials (the session key, the setup code, `.env`), call his own API or reach his databases, though it runs as you and is not a containment boundary ([computer.md](architecture/computer.md#what-the-sandbox-is-and-isnt)); friends' pings without the shell, Chrome or screens; Twilio texts answered only from your own numbers.
- **Running it**: LaunchAgents, a health watchdog, nightly verified backups (`backend/scripts/BACKUP.md`), a production frontend build with staged deploys and one-command rollback.

[CLAUDE.md](../CLAUDE.md) and [docs/architecture/](architecture/README.md) describe each part; [ADAPTING.md](ADAPTING.md) lists what was built for one particular setup.

## Rolling back

Stop the new Edward, put the March database back, then the March code:

```bash
# 1. Stop him: unload the agents if you installed them, else kill the listeners as in step 1
bash scripts/install-launchagents.sh --uninstall
lsof -t -iTCP:8000 -sTCP:LISTEN | xargs kill
lsof -t -iTCP:3000 -sTCP:LISTEN | xargs kill

# 2. The March database, as it was (this deletes everything written since the upgrade)
PG="$(brew --prefix postgresql@16)/bin"
"$PG/dropdb" edward
"$PG/createdb" -O edward edward
"$PG/pg_restore" -d edward ~/edward-backups/edward-march-final.dump

# 3. The March code, environment and settings
git checkout 408e1d5
rm -rf backend/.venv && mv backend/.venv.march backend/.venv
cp ~/edward-backups/env-march .env
./restart.sh
```

Run the database commands as your macOS user (Homebrew's Postgres superuser), so the restored tables keep their owner, the `edward` role the March backend connects as.
