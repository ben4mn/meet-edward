# Adapting Edward to your Mac

Edward was built for one person's Mac and household. This page maps what you will need to adapt.

Before this release, Edward ran for months as one owner's assistant on an always-on Mac (a Mac mini makes a good host). Most of what was specific to that home is now a setting: `.env.example` describes every one, `backend/services/owner.py` covers who the owner is, and `backend/services/host_config.py` covers where Edward runs. A few places still assume that first host, and each one is marked in the code with an `ADAPT:` note. This page covers:

1. [How to find the seams](#1-finding-the-seams)
2. [Every `ADAPT:` note, by subsystem](#2-every-adapt-note)
3. [The macOS permissions Edward needs](#3-macos-permissions)
4. [Prompts for Claude for the bigger gaps](#4-adapting-with-claude)
5. [What ships switched off, and the switch for each](#5-off-by-default)

---

## 1. Finding the seams

From the checkout:

```bash
grep -rn "ADAPT:" --include='*.py' --include='*.ts' --include='*.tsx' --include='*.sh' \
  --include='*.swift' --include='*.js' \
  backend frontend scripts companion chrome-extension scriptable | grep -v node_modules
```

Every note has the same three parts:

```
# ADAPT: <what is specific to the original host, in one line>
#   How it works here: <what the code assumes>
#   To adapt: <the setting to change, or what to build>
```

The line numbers below are from this release. If they drift, the grep is the authority. If you add code that assumes something about your own host, mark it the same way and add a row here.

Some things are settings, so you configure them rather than adapt them:

| What | Where it is read | Settings |
|---|---|---|
| Who the owner is | `backend/services/owner.py` | `EDWARD_OWNER_NAME`, `EDWARD_OWNER_FULL_NAME`, `EDWARD_OWNER_ALIASES`, `EDWARD_OWNER_HANDLES` (your phone numbers, emails and Apple ID), `EDWARD_OWNER_EMAILS`, `EDWARD_HOUSEHOLD` (the people at the hub, you first) |
| Where Edward runs | `backend/services/host_config.py` | `EDWARD_LIVE_REPO`, `EDWARD_PUBLIC_BASE_URL`, `EDWARD_PUBLIC_SCREEN_URL`, `EDWARD_CORS_ORIGINS`, `EDWARD_LAUNCHD_PREFIX` (default `com.meet-edward`), `EDWARD_TUNNEL_LABEL`, `EDWARD_BACKUP_DIR` (default `~/edward-backups`) |
| What the shell scripts see | `scripts/lib/edward-env.sh` | The environment first, then the checkout's `.env`, then the default. `restart.sh`, the health check, the backups and the LaunchAgent installer all read it. |

With `EDWARD_OWNER_NAME` unset, Edward says "the owner". Set it, and he uses your name.

---

## 2. Every ADAPT note

### The model provider

The default provider is the Claude API (`EDWARD_LLM_PROVIDER=anthropic` with `ANTHROPIC_API_KEY`). These two notes apply only if you opt into the Codex fallback.

| Where | What is specific | What to change |
|---|---|---|
| `backend/services/codex_chat_model.py:1` | The Codex fallback calls ChatGPT's private backend (`chatgpt.com/backend-api/codex`) with the Codex CLI's OAuth login. It is unofficial, may break at any time and may conflict with OpenAI's terms. | Nothing on the default provider. To use it anyway, set `EDWARD_LLM_PROVIDER=codex`. `OPENAI_API_KEY` switches the fallback to the public OpenAI API. |
| `backend/services/codex_auth_service.py:1` | Borrows the Codex CLI's login (`~/.codex/auth.json`, or `CODEX_AUTH_PATH`) and its OAuth client id to refresh tokens. | Same as above. |

### iMessage and the owner's identity

| Where | What is specific | What to change |
|---|---|---|
| `backend/services/imessage_sent.py:13` | Edward sends iMessages as the owner, from the Apple ID that Messages on this Mac is signed into. Edward's own sends are told apart from yours by the chat.db ROWID the send reads back, then by a signature. | Sign Messages into your Apple ID and set `EDWARD_OWNER_HANDLES`. A separate Apple ID for Edward means reworking the echo checks ([prompt D](#d-a-separate-apple-id-or-number-for-edward)). |
| `backend/services/pings/detect.py:92` | chat.db's `is_from_me` is read as "the owner wrote it". | With a separate Apple ID for Edward, `is_from_me` means Edward. Decide `from_owner` by the sender's handle instead (`owner.owner_handles()`), as in [prompt D](#d-a-separate-apple-id-or-number-for-edward). |
| `backend/services/heartbeat/listener_imessage.py:110` | Reads `~/Library/Messages/chat.db` (and the AddressBook stores, for names) every 10 s. | Grant Full Disk Access ([§3](#3-macos-permissions)), then turn on **Messages** in Settings → Listening → Configuration. Without the grant, the listener logs the error and stops. |

### Mail

| Where | What is specific | What to change |
|---|---|---|
| `backend/services/heartbeat/listener_email.py:39` | iCloud mail, and mail from any other Mail.app account, is read from Mail.app's own Envelope Index under `~/Library/Mail/V10`. Mail.app stays open so the index stays fresh. | Grant Full Disk Access and keep Mail.app running. If your macOS keeps the index somewhere else (the `V10` folder changes between releases), set `EDWARD_MAIL_ENVELOPE_INDEX`. Gmail goes through Google's API instead: [gmail-setup.md](gmail-setup.md). |

### Calendar and Reminders

| Where | What is specific | What to change |
|---|---|---|
| `backend/services/calendar_service.py:50` | The calendar is read straight from Calendar's private SQLite store. Its schema is undocumented and was checked on one macOS version. | Grant Full Disk Access, or set `EDWARD_CALENDAR_SQLITE=off` to use apple-mcp only. `EDWARD_CALENDAR_DB` points at another copy of the store. If the store can't be read, Edward says so; he never reports an empty day instead. |
| `backend/services/reminders_service.py:48` | Reminders are read from Reminders' private Core Data stores (`Data-*.sqlite`). | Grant Full Disk Access, or set `EDWARD_REMINDERS_SQLITE=off`. `EDWARD_REMINDERS_DIR` points at another stores directory. |
| `backend/services/eventkit_service.py:92` | Calendar and Reminders writes go through a small Swift EventKit helper, and macOS has to grant that access to the backend's python. | `setup.sh` builds the helper (or run `bash backend/scripts/build_eventkit.sh`). Then, sitting at the Mac, run `bash backend/scripts/eventkit_request_access.sh` and click Allow. |

### Integrations

| Where | What is specific | What to change |
|---|---|---|
| `backend/services/mcp_client.py:24` | WhatsApp goes through the whatsapp-mcp project's Go bridge. The bridge runs outside Edward on 127.0.0.1:8080 and is paired once by scanning a QR code from its log. | Install whatsapp-mcp and run its bridge, either in a terminal or as a LaunchAgent of your own. Scan the QR code, then set `MCP_WHATSAPP_ENABLED=true` and `MCP_WHATSAPP_SERVER_DIR`, plus `MCP_WHATSAPP_BRIDGE_LOG` if the bridge logs somewhere other than `/tmp/whatsapp-bridge.log`. |
| `backend/services/mcp_client.py:37` | apple-mcp is the jxnl/apple-mcp fork, run with bun from a local clone. Calendar, Contacts and Reminders are read from their own databases first; apple-mcp is only the fallback. | Install bun, clone the fork, run `bun install` there, then set `MCP_APPLE_ENABLED=true`. Set `BUN_PATH` (default `~/.bun/bin/bun`) and `MCP_APPLE_DIR` (default `~/apple-mcp`) if they live elsewhere. |
| `backend/services/html_hosting_service.py:9` | The "publish a page" calls, which are retired, speak one hosting service's small upload API. | Point `HTML_HOSTING_URL` and `HTML_HOSTING_API_KEY` at a host that speaks it, or rewrite the calls. While both are unset, the calls say hosting is not configured. |
| `backend/routers/alexa.py:18` | **A security gap.** Inbound Alexa requests are not signature-verified. Only the skill id is checked, and an Alexa turn runs as the owner speaking. Anyone who learns the skill id and the URL can run a turn as you. | Leave `ALEXA_SKILL_ID` unset unless you use the skill (the route then answers 503), or add Amazon's request verification ([prompt E](#e-alexa-request-signature-verification)). |

### Edward's computer, browsers and screen

| Where | What is specific | What to change |
|---|---|---|
| `backend/services/computer/rails.py:94` | The protected Homebrew kegs are the reference host's: python@3.12, whose path holds the privacy grants, and postgresql@16, which carries a pgvector built from source. Edward's shell may not write to or upgrade either keg, or Postgres's data directory. | If Homebrew is not at `/opt/homebrew` or `/usr/local`, set `HOMEBREW_PREFIX`. Edit `PROTECTED_FORMULAE` to the Python and Postgres formulae your production runs on. The rails protect those paths and a few others; they don't contain the shell, which runs as you ([below](#not-marked-but-worth-knowing), [prompt G](#g-edward-under-his-own-macos-user)). |
| `backend/services/toolset/manual.py:27` | The Rails paragraph of the tool manual names those kegs and Postgres on :5432. | Change it together with `rails.py`. `tests/test_manual_rails_are_enforced.py` checks that the manual claims only what is enforced. |
| `backend/services/browser_ext_service.py:39` | Edward's hands drive Google Chrome on this Mac through an unpacked extension that connects to `ws://127.0.0.1:8000/api/browser/ext`. The socket takes only a direct loopback connection (no proxy or tunnel headers) with the `chrome-extension://` Origin of Edward's own extension: `chrome-extension/manifest.json` carries a fixed public `key`, so its ID is always `appfgnpjahkedldopocalbkoommcalae`, and by default no other ID is accepted. | Off by default. To turn it on, set `EDWARD_CHROME_HANDS=on` and load `chrome-extension/` ([its README](../chrome-extension/README.md)). A fork that changes or removes the manifest's `key` gets another ID: set `EDWARD_CHROME_EXTENSION_ID` to it (comma-separated for several; it replaces the default). The key is public, so the pin keeps out your other extensions, not one built on purpose with the same key. Leave it off on a host where Edward must never touch your browser. |
| `backend/services/screen_window_service.py:73` | The `/screen` stage is a tab in Google Chrome, found and sized over AppleScript (Finder, System Events). | Keep the hands off and open `/screen` yourself, in any browser on any display. Or have Claude write an equivalent for your browser (Safari, Arc) behind the same `ensure_screen_tab()` seam. |
| `backend/services/browser_hands.py:692` | `look("screen")` captures this Mac's whole display with `screencapture`. | Grant Screen Recording to the backend's python. Without the grant, `look` answers that it could not capture the display. |
| `backend/services/media/mediatool.py:25` | Photos, voice messages and videos rely on macOS tools (`sips`, `qlmanage`, `afconvert`, `avconvert`) and a Swift helper compiled on this Mac. | `setup.sh` builds the helper (it needs `xcode-select --install`), or point `EDWARD_MEDIATOOL` at a build. Without it, a video gets a poster frame only and a GIF its first frame. |

### The household hub and pet care

| Where | What is specific | What to change |
|---|---|---|
| `backend/services/hub_service.py:44` | Pets and their care schedule come from a seed file, which is planted once into an empty board. The app has no editor for them. | Copy `backend/hub_seed.example.json`, describe your pets and set `EDWARD_HUB_SEED` to the file. A later edit takes effect only after you clear the `pets` and `care_items` rows. Or build an editor ([prompt A](#a-a-pets-and-care-editor)). |
| `backend/services/care_watch.py:256` | The late-dose alert watches the timed meds on the hub's board, and pushes once per pet and slot when a dose is 45 min late. | Enter your pets' timed doses in the seed, or set `EDWARD_CARE_WATCH=off` if you have none. |
| `frontend/app/(display)/hub/components/HubRoot.tsx:8` | The hub is laid out for one device: a 1280×720 touchscreen kiosk (a Raspberry Pi with a 7" display running Chromium against `/hub`). | Any kiosk-mode browser on `/hub` works ([pi-hub-setup.md](pi-hub-setup.md)). For another screen size, have Claude rework `hub.css`. |
| `scripts/health_check.sh:441` | The deep check assumes a dedicated kiosk polls `/api/hub/*` all day, and reports a kiosk that has been silent for 6 h. | Set `EDWARD_HEALTH_HUB_KIOSK=1` to turn the check on. If your kiosk polls other routes, change `HUB_KIOSK_REQUEST`. |

### The phone widget

| Where | What is specific | What to change |
|---|---|---|
| `backend/services/graph/tools.py:3948` | The widget manual assumes one large Scriptable widget on an iPhone (`scriptable/edward-widget.js` fetching `GET /api/widget?token=…`). | Edit the "LARGE home screen widget" line for the widget size you use, or drop the widget tools. |

### Sign-in and the network

| Where | What is specific | What to change |
|---|---|---|
| `backend/services/auth_service.py:155` | The session cookie is `Secure`, which assumes HTTPS in front of Edward (a tunnel or a reverse proxy). Browsers also accept it from `http://localhost`. | Serve Edward over HTTPS, or set `EDWARD_COOKIE_SECURE=off` for a plain-HTTP LAN you trust. A kiosk on the LAN can use a browser flag instead ([pi-hub-setup.md](pi-hub-setup.md#signing-the-kiosk-in)). |

### Operations

| Where | What is specific | What to change |
|---|---|---|
| `scripts/install-launchagents.sh:82` | The nightly backup runs under the real path of the python3.12 behind `backend/.venv`, so one Full Disk Access grant covers both chat.db and a removable backup drive. | Grant that path Full Disk Access (the installer prints it) if backups go to a `/Volumes` drive. `EDWARD_FDA_PYTHON` picks another interpreter. Re-run the installer after `brew upgrade python@3.12`. |
| `backend/scripts/run_backup_with_fda.py:15` | The same grant, seen from the backup script's side. | As above. Details are in [BACKUP.md](../backend/scripts/BACKUP.md). |
| `backend/start-graph-worker.sh:10` | The knowledge graph (Graphiti + Kuzu) is optional and off. While `GRAPHITI_ENABLED` is unset, the worker sleeps 30 s and exits, so launchd backs off instead of looping. Ingestion is a second switch: the reference host kept it off while it chased Kuzu crashes during ingestion. | Set `GRAPHITI_ENABLED=true` (every new memory is queued, and the worker starts) and `GRAPHITI_INGEST_ENABLED=true` (the worker drains the queue into the graph), then run `bash scripts/install-launchagents.sh --with-graph`. Without the second, the worker answers searches from an empty graph and the queue only grows. Each memory takes the worker 80–130 s and a few calls on the `background` route, inside the daily spend cap; a row that crashes the worker three times is set aside as failed. `POST /api/memory/graph/backfill` queues the memories you already have. |

### Not marked, but worth knowing

- **macOS only.** Edward depends on AppleScript, chat.db, Seatbelt (`sandbox-exec`) for his shell, launchd and the macOS privacy grants. Linux would be a port, not a setting ([prompts B and C](#b-linux-a-sandbox-for-the-bash-tool-first)).
- **Apple silicon only.** `setup.sh` stops before installing anything on an Intel Mac, in a Rosetta (x86_64) shell, or with an x86_64 python@3.12 or `backend/.venv`: the pinned PyTorch (`backend/constraints.txt`, pulled in by sentence-transformers for the local embedding model) publishes no x86_64 macOS wheel. That pin, not the Swift helpers, is what an Intel port would have to change.
- **macOS 14 or later for the EventKit helper.** Both Swift helpers build for the host's architecture (`uname -m`). `backend/scripts/build_eventkit.sh` targets macOS 14.0, because the helper uses EventKit's full-access APIs (`requestFullAccessToEvents`, `requestFullAccessToReminders`) that macOS 14 introduced. On an older macOS its build or self-test fails, `setup.sh` only warns, and Edward runs without it: he can read Calendar and Reminders but not change them.
- **Ports.** The backend is on :8000 and the frontend on :3000 (the frontend's LaunchAgent pins `next start -p 3000`). The extension's socket address is written into `chrome-extension/background.js` (`BACKEND_WS`), and Edward's shell is refused TCP to both ports on any address (`APP_PORTS` in `backend/services/computer/rails.py`), so moving either port means changing those two as well. His shell is also refused the WhatsApp bridge's port (`WHATSAPP_BRIDGE_ADDR` in `backend/services/mcp_client.py`, default 8080) and both of Edward's Postgres clusters (:5432, :5433). The backend listens on every interface so a hub on your network can reach it; to keep it to this Mac, set `EDWARD_BACKEND_HOST=127.0.0.1`.

- **Edward runs as your macOS user, and his sandbox is not a wall.** On the reference host the backend and Edward's shell ran as the owner. The Seatbelt profile protects specific things (the live checkout, his own credentials, Postgres' files and the protected kegs, his own API, the databases and the WhatsApp bridge) and stops accidents, but the shell can still write the rest of your home and Homebrew's prefix: shell startup files, `~/Library/LaunchAgents`, `node`, caches the backend loads from. Code planted there runs as you, outside the sandbox, at the next login or restart. The full list is in [computer.md](architecture/computer.md#what-the-sandbox-does-not-prevent). For real isolation, run Edward under his own macOS user ([prompt G](#g-edward-under-his-own-macos-user)); short of that, keep friends' pings on their default (no shell, no Chrome, no screens; `EDWARD_FRIENDS_FULL_TOOLSET` stays off) and point him only at pages and files you'd run yourself.
- **The launchers ignore `~/.local/bin` and `~/bin`.** The LaunchAgents and the start scripts search `/usr/bin:/bin:/usr/sbin:/sbin` first, then Postgres 16's tools and Homebrew (`edward_service_path` in `scripts/lib/edward-env.sh`), so a program Edward's shell planted in your home never runs in their place. A tool you installed only in `~/.local/bin` (the `codex` CLI, `uvx` from its standalone installer, the `claude` CLI) is not found by the backend: install it with Homebrew, or link it into Homebrew's `bin`.
- **The backend reads `.env` itself.** The start scripts no longer export `.env` into the backend's start-up environment, which other processes of your user can read; a setting in your shell's environment now wins over `.env`, and a `.env` in `backend/` is not read (move it to the repo root).

---

## 3. macOS permissions

**Who gets the grants.** Almost every grant goes to the *backend's python*: the real path of the python3.12 behind `backend/.venv`. `setup.sh` and `scripts/install-launchagents.sh` both print it at the end, or you can ask for it directly:

```bash
backend/.venv/bin/python -c 'import os, sys; print(os.path.realpath(getattr(sys, "_base_executable", sys.executable)))'
```

To grant it, open System Settings → Privacy & Security, pick the list, click **+**, press **⌘⇧G** and paste that path.

macOS charges each access to the process responsible for it. Under the LaunchAgents, that is the python above. If you start Edward by hand from a terminal (`./restart.sh` without the agents installed), the terminal app is charged instead, so grant the terminal too, or install the agents.

| Permission | What needs it | Without it |
|---|---|---|
| **Full Disk Access** | Reading iMessage (`~/Library/Messages/chat.db` and its attachments), contact names (the AddressBook stores), Mail.app's Envelope Index, Calendar's and Reminders' SQLite stores, and writing backups to a removable volume (`/Volumes/…`) | The iMessage and Mail listeners log the error and stop. Calendar and Reminders reads say they are unreadable and fall back to apple-mcp. A backup to a removable drive refuses to run. |
| **Automation** | Messages (sending an iMessage); Google Chrome, Finder and System Events (the `/screen` tab, relaunching a stuck Chrome; only with the Chrome hands on) | macOS asks the first time each app is scripted. Answer at the Mac: a dialog nobody answers stalls that call. |
| **Accessibility** | System Events placing and sizing Chrome's window for `/screen` (only with the Chrome hands on) | The tab opens but is not sized or raised. |
| **Screen Recording** | `look("screen")`, which captures the whole display | `look` answers that it could not capture the display. |
| **Calendars / Reminders** (EventKit) | Creating and changing events and reminders when you ask | Edward says he can't change them yet, and names the script below. |

**EventKit.** `setup.sh` builds the helper; `bash backend/scripts/build_eventkit.sh` builds it by hand. Then, sitting at the Mac (not over SSH), run:

```bash
bash backend/scripts/eventkit_request_access.sh            # both; or: events / reminders
```

macOS grants EventKit to the responsible process, so the script asks through a one-shot launchd job (`<prefix>.eventkit-request`) running under the backend's python, then removes the job. Click **Allow** (Full Access) on each dialog. If no dialog appears because of an earlier "Don't Allow", turn the python on under Privacy & Security → Calendars (Full Access) and → Reminders yourself. Edward never asks by himself, because a dialog nobody answers would hang a turn.

**Grants are tied to the binary's path.** `brew upgrade python@3.12` installs Python at a new Cellar path, and every grant above silently stops applying. After an upgrade:

1. Grant the new path (print it with the command above).
2. Re-run `bash scripts/install-launchagents.sh`, because the backup agent embeds the path.
3. Re-run `bash backend/scripts/eventkit_request_access.sh`.

This is why Edward's own shell may not write to or upgrade the python@3.12 keg. Be careful with `brew install` too: installing a formula that depends on Python can upgrade python@3.12 along the way (which is why media handling uses the macOS tools rather than ffmpeg). In the same way, `brew reinstall postgresql@16` drops the pgvector built from source; re-run `./setup.sh` afterwards.

---

## 4. Adapting with Claude

Open Claude Code in your checkout. `CLAUDE.md` loads by itself, and it carries the house rules:

- Every change starts with a failing test.
- Backend tests run only through `scripts/with-test-db-lock.sh` on the test cluster (:5433), never against the live database.
- Update the `docs/architecture/` file of any subsystem you change. `backend/tests/test_doc_lint.py` fails when a doc names a path or route that does not exist.

Paste one of the prompts below as written, or edit it first. Each one says where to start, what must hold, and how to prove the change works.

### A. A pets-and-care editor

```text
The household hub's pets and care schedule come only from a seed file
(EDWARD_HUB_SEED, planted once into an empty board by backend/services/hub_service.py;
see its ADAPT note and backend/hub_seed.example.json). Build an editor so the
owner can add, change and retire pets and care items without touching the database.

- Backend: add owner-only routes under /api/hub for pets and care items (create,
  update, retire; never hard-delete an item that has care logs, so history is kept).
  Validate exactly what the seed loader validates: schedule_type window|timed|prn,
  at_time required for timed, window am|pm, kind feeding|med|supplement,
  needs_review, bundle_with pointing at a real slot.
- Frontend: put the editor in the owner's Edward tab (the House room), not on
  /hub/settings. The hub is a shared panel with no PIN, and nothing that changes
  data belongs there until it has one. Add the new panel slug to ROOM_SLUGS in
  frontend/components/settings/settingsRooms.ts and to settingsIndex.ts.
- Keep the hub's design rules (docs/architecture/hub.md): window items never go
  overdue, only timed items alert, missed (>4 h) stops alerting, needs_review items
  show a badge. An edit must not make care_watch push for a slot that was already
  logged today.
- Tests first: backend tests next to tests/test_hub_pet_board.py and
  tests/test_public_hub_seed.py, run through ../scripts/with-test-db-lock.sh.
  Add a frontend test for the panel.
- Docs: list the new routes in docs/architecture/api.md (the doc lint checks it),
  describe the editor in docs/architecture/hub.md, and update the ADAPT note in
  hub_service.py and the row in docs/ADAPTING.md.
```

### B. Linux: a sandbox for the bash tool first

```text
I want to run Edward's backend on Linux. Start with the one piece that must not
regress: Edward's shell. On macOS, services/computer/shell.py runs every command
under Seatbelt (sandbox-exec) with a profile rendered by services/computer/profile.py
from the rails table in services/computer/rails.py, and if the sandbox can't be
applied, bash fails closed.

- Add a Linux backend for the same rails using bubblewrap (bwrap), or firejail if
  bwrap isn't available. Reads stay allowed everywhere but Edward's own
  credentials (rails.read_deny_files, compared by inode, not spelling). Writes are
  allowed only in EDWARD_HOME and the scratch dirs, never in the live checkout
  (host_config.repo_root()), the releases dir, Postgres's data directories or the
  directories above any of them. No access to Edward's Postgres ports (:5432, :5433) or
  the ports in rails.denied_ports() (his own API, the WhatsApp bridge). Signals
  only to the shell's own processes. Keep the 180 s timeout and the RSS/disk
  watchdog.
- Choose the backend by platform behind one interface, so rails.py stays the single
  table that both the macOS profile and the Linux arguments are rendered from.
- Fail closed on Linux exactly as on macOS: no bwrap/firejail, no shell.
- Port tests/test_computer_rails.py, tests/test_computer_profile_sandbox.py and
  tests/test_manual_rails_are_enforced.py so each rail is proven on both platforms
  (skip the other platform's cases cleanly).
- Then list, in a new section of docs/architecture/computer.md, every other
  macOS-only piece the backend touches (AppleScript sends, chat.db, Mail's
  Envelope Index, the Calendar/Reminders stores, EventKit, screencapture, launchd)
  and how each one already degrades when it's missing. Don't port those yet.
```

### C. A systemd unit instead of LaunchAgents

```text
Edward is kept alive by LaunchAgents that scripts/install-launchagents.sh writes
(labels <EDWARD_LAUNCHD_PREFIX>.backend, .frontend, .healthcheck, .healthcheck-deep,
.backup, .memory-report, optional .graph-worker). I want the same on a host with
systemd.

- Write scripts/install-systemd-units.sh mirroring the installer's options
  (--with-graph, --without-backup, --dry-run, --uninstall). Use user units:
  backend (backend/start.sh) and frontend (frontend/run-launchd.sh) with
  Restart=always and RestartSec=10, plus timers for health_check.sh every 5 min,
  health_check.sh --deep at 08:00, the backup at 03:30
  (backend/scripts/backup_memories.sh) and memory_health_report.py --push at 09:05.
  Paths come from where the checkout is, as in the existing installer.
- The scripts that restart things go through scripts/lib/edward-env.sh
  (edward_label, edward_launchd_loaded) and call launchctl directly in restart.sh
  and scripts/health_check.sh (its kickstart function). Put a service-manager
  abstraction in edward-env.sh (launchd on macOS, systemd elsewhere) and use it
  in all three.
- Tests first: extend scripts/test_install_launchagents.sh's approach (render
  to a scratch dir, never touch the real manager) for the new installer, and keep
  bash scripts/test_health_check.sh green.
- Document it in docs/architecture/ops-health-backups.md.
```

### D. A separate Apple ID or number for Edward

```text
Edward currently sends and reads iMessages as me: Messages on this Mac is signed
into my Apple ID, so chat.db's is_from_me means "I wrote it", and Edward's own
sends are told apart from mine by ROWID and signature
(backend/services/imessage_sent.py, backend/services/pings/detect.py — read both
ADAPT notes). I want Edward to have his own Apple ID on this Mac, so people text
him, not me.

- Add a setting (e.g. EDWARD_IMESSAGE_IDENTITY=owner|edward, default owner, so
  nothing changes for existing installs).
- In edward mode: is_from_me means Edward. Decide from_owner by the sender's
  handle against owner.owner_handles() (EDWARD_OWNER_HANDLES), and treat my
  1:1 chat with Edward's Apple ID as the owner's direct channel instead of the
  self-chat. Echo recognition simplifies: every is_from_me row is Edward's.
- Keep every send rule (docs/architecture/pings.md, tool-loop.md): send_message
  never reaches the owner from a turn the owner isn't part of (tell_owner does), the
  recipient rule, the 10-min dedupe, and "Sent" only when delivered.
- Check what else reads is_from_me or the owner's handles: the iMessage listener
  (services/heartbeat/listener_imessage.py), the ping runner, owner_channel.py
  (EDWARD_OWNER_CHANNEL=imessage_self) and the mind's never-text-the-owner guard.
- Tests first: tests/test_ping_detect.py, tests/test_imessage_echo.py,
  tests/test_public_imessage_owner.py and tests/test_mind_never_texts_owner.py
  must pass in both modes; add edward-mode cases.
- Docs: pings.md, integrations.md, the two ADAPT notes and docs/ADAPTING.md.

(For SMS rather than iMessage, Twilio already gives Edward a number of his own:
TWILIO_* in .env.example; the webhooks refuse every request until
TWILIO_AUTH_TOKEN is set, and answer only the owner's own numbers unless
EDWARD_TWILIO_ANSWER_ANYONE=on.)
```

### E. Alexa request signature verification

```text
POST /api/alexa (backend/routers/alexa.py) is public and checks only the skill id,
and an Alexa turn runs as the owner speaking: anyone who learns the skill id and
the URL can run a turn as me. Read the ADAPT note there, then add Amazon's
request verification before process_alexa_request runs:

- The SignatureCertChainUrl header: https, host s3.amazonaws.com (case-insensitive),
  path starting /echo.api/ (after normalising), port 443 if given.
- Download the PEM chain from it (cache it by URL until it expires; bounded
  timeout, httpx). Check the leaf is in date, that its SANs include
  echo-api.amazon.com, and that the chain verifies to a trusted root.
- Verify the Signature-256 header (base64 RSA-SHA256) over the raw request body
  bytes, read before any JSON parsing.
- Reject a request.timestamp more than 150 seconds from now.
- Any failure answers 400 and runs nothing. Keep the 503 when ALEXA_SKILL_ID is unset.
- Use the cryptography package (already installed through python-jose[cryptography]).
- Tests first, in a new tests/test_alexa_signature.py: a locally generated CA and
  leaf stand in for Amazon's (inject the trust root), plus cases for a bad URL, a
  tampered body, an expired cert and a stale timestamp. No network in tests.
- Then update docs/alexa-setup.md ("Known limitations"), the ADAPT note,
  docs/architecture/integrations.md and docs/ADAPTING.md.
```

### F. Your own browser for the screen

```text
/screen is opened and sized in Google Chrome over AppleScript
(backend/services/screen_window_service.py, the ensure_screen_tab() seam; see its
ADAPT note). I use <Safari / Arc>. Write the equivalent behind the same seam,
chosen by a setting, with the same rules: reuse an open /screen tab before
opening one, never launch a second browser instance, never take over a
fullscreen Space. Tests must never run osascript (conftest already refuses it).
Stub the script runner.
```

### G. Edward under his own macOS user

Edward's sandbox protects named paths, not your account: his shell runs as you and can write your shell startup files, your LaunchAgents and Homebrew's prefix, from which unsandboxed programs run ([computer.md](architecture/computer.md#what-the-sandbox-is-and-isnt)). Running all of Edward (backend, frontend, LaunchAgents, shell) as a separate standard macOS user puts what his shell can write, and anything that escapes it, in that user's home instead of yours. The reference install never ran this way, so treat it as a project, not a setting. What it costs:

- **His own logged-in session.** LaunchAgents and AppleScript are per user, and Messages sends only from a logged-in session: keep both users logged in with Fast User Switching, or, on a headless Mac, log his user in automatically (FileVault rules out automatic login).
- **His own copies of your data.** He can't read your `~/Library`, so Messages, Mail, Calendar, Reminders and Contacts come from what his account is signed into: your iCloud services, or an Apple ID of his own ([prompt D](#d-a-separate-apple-id-or-number-for-edward)). Signing his user into your Apple ID hands it your messages, but not your files, shell, Keychain, SSH keys or browser.
- **No `brew install` for him.** Homebrew stays yours; he runs its programs but can't write the prefix (pip, uv and npm into his own home still work).
- **Postgres with passwords.** Your Homebrew cluster trusts every local connection, so any process of his could connect as your superuser.

```text
I want Edward to run as his own standard (non-admin) macOS user instead of as me,
so that his shell, and anything it plants, can't write my home folder, my shell
startup files, my LaunchAgents or Homebrew. Read docs/architecture/computer.md
("What the sandbox is (and isn't)"), docs/architecture/ops-health-backups.md and
section 3 of docs/ADAPTING.md first. Plan the steps, then do them one at a time and
check each on this Mac. Stop and tell me whenever a step needs me at the keyboard
(creating the user, logging in as it, permission dialogs, an administrator's
password). Never run sudo yourself.

1. I create a standard user (System Settings > Users & Groups), e.g. "edward",
   never an administrator.
2. Homebrew stays mine. As me: brew install python@3.12 postgresql@16 node.
   Check what ./setup.sh does as a user who can't write the Homebrew prefix; if it
   tries to install or start formulae that are already there, make it skip them
   (tests first, in scripts/test_setup.sh).
3. Postgres. My cluster (brew services, as me) accepts local connections without a
   password. Either:
   (a) give him a cluster of his own: as edward, initdb a data directory in his
       home with --auth=scram-sha-256 and a password file only he can read, run it
       on its own port from his own LaunchAgent, and set EDWARD_PG_BIN,
       EDWARD_PGDATA and DATABASE_URL in his .env; add that port to the shell's
       denied ports in backend/services/computer/rails.py, with its parity test; or
   (b) keep mine, switch every line of its pg_hba.conf to scram-sha-256, and give
       the edward role a long random password (never a superuser, never 'edward')
       that appears only in his DATABASE_URL.
   Keep the test cluster (scripts/test-pg-cluster.sh, which trusts its OS user)
   stopped on this Mac, or run the backend tests elsewhere.
4. As edward: clone the repo into his home, run ./setup.sh, keep .env mode 600 and
   put in it only what he needs. Nothing of mine (keys, tokens) beyond what I hand
   him for Edward.
5. As edward, sign his Messages, Calendar, Reminders and Mail into what he should
   read (prompt D if he gets his own Apple ID) and set EDWARD_OWNER_HANDLES to my
   handles. Then the permissions in section 3 of docs/ADAPTING.md for the python
   setup.sh prints, granted from his session, and
   bash backend/scripts/eventkit_request_access.sh at the Mac.
6. As edward: bash scripts/install-launchagents.sh. If my own user still has
   Edward's LaunchAgents from an earlier install, I uninstall them as me
   (bash scripts/install-launchagents.sh --uninstall in my checkout).
7. Prove it, from Edward's bash tool and from a plain shell as edward: writing my
   ~/.zshrc, my ~/Library/LaunchAgents and the Homebrew prefix fails; listing my
   ~/Library and ~/.ssh fails; psql to Postgres without his password fails; the
   app, a ping and his calendar still work. Record what you checked, and the
   steps, in docs/architecture/ops-health-backups.md.
```

---

## 5. Off by default

Everything below ships switched off (or does nothing until you configure it), so a fresh install reads nothing private and reaches no one.

| Feature | Default | Switch |
|---|---|---|
| Reading your iMessages (and with them, `@Edward` pings) | Off | Settings → Listening → Configuration → **Messages** (`heartbeat_config.imessage_enabled`), plus Full Disk Access. Pings then follow `EDWARD_PINGS` (default `on`). |
| Reading Mail.app mail | Off | Settings → Listening → Configuration → **Email**, plus Full Disk Access |
| Watching Gmail | Off | Connect Gmail ([gmail-setup.md](gmail-setup.md)), then **Watch my inbox** in Settings → Gmail |
| Noticing and speaking up unasked | `EDWARD_PROACTIVITY=off`: messages are stored, pings answered, promises kept, nothing noticed | `shadow` (notice and decide, never speak), then `notice` |
| Edward's hands in your Chrome | Off: the extension's socket is refused and the `chrome` tool says so | `EDWARD_CHROME_HANDS=on`, and load `chrome-extension/` |
| Hiding Chrome's "being debugged" bar | Off | `EDWARD_CHROME_SILENT_DEBUGGER=on` (with the hands on) |
| Holding outward calls from tainted turns for approval | `EDWARD_TAINT_APPROVALS=off` | `shadow` (log what would be held), then `on` |
| Edward's shell, editor, your Chrome and the screens in a friend's ping | Off: a friend's ping gets an honest `Tool error:` for them, and `look` only at what was sent, a link or his own browser (your own pings keep everything) | `EDWARD_FRIENDS_FULL_TOOLSET=on`. A friend's message is untrusted text and the shell runs as you ([pings.md](architecture/pings.md#the-reply-turn)) |
| The hub's pets and care | The board starts empty | `EDWARD_HUB_SEED` (copy `backend/hub_seed.example.json`) |
| The hub's people | Just you | `EDWARD_HOUSEHOLD`, comma-separated, you first. The who-toggle shows from two people. |
| The household photo door (`/photos`, a photos-only second login) | Off: login 403, other door routes 404 | `PHOTO_DOOR_PASSWORD_HASH` (a bcrypt hash, single-quoted in `.env`) |
| Counting down prepaid credits | Off | Set both `EDWARD_CREDITS_START_USD` and `EDWARD_CREDITS_START_DATE` |
| The Codex fallback | Off: the Claude API is the provider | `EDWARD_LLM_PROVIDER=codex` (unofficial, at your own risk) |
| The knowledge graph | Off | `GRAPHITI_ENABLED=true` and `GRAPHITI_INGEST_ENABLED=true` (both: the first queues memories, the second fills the graph from the queue), then `bash scripts/install-launchagents.sh --with-graph` |
| apple-mcp (Notes, Maps, a fallback for Calendar, Contacts and Reminders) | Off | `MCP_APPLE_ENABLED=true` (see its row above) |
| WhatsApp | Off | `MCP_WHATSAPP_ENABLED=true` and a running bridge |
| Twilio SMS/WhatsApp (a number of Edward's own) | Unconfigured. The webhooks refuse every request until `TWILIO_AUTH_TOKEN` is set, and then accept only correctly signed ones. Even then only your own numbers (`EDWARD_OWNER_HANDLES`) get an answer; anyone else's text is held in the ledger (`twilio_inbound`), unanswered. | `TWILIO_*`; `EDWARD_TWILIO_ANSWER_ANYONE=on` answers everyone, at a risk ([.env.example](../.env.example)) |
| The Alexa skill (inbound) | Off: the route answers 503 | `ALEXA_SKILL_ID` ([alexa-setup.md](alexa-setup.md); mind the security gap) |
| Echo announcements through Home Assistant | Off | `HOME_ASSISTANT_URL` and `HOME_ASSISTANT_TOKEN` |
| Hosted pages | Off | `HTML_HOSTING_URL` and `HTML_HOSTING_API_KEY` |
| Voice (listening and speaking) | Off | `ELEVENLABS_API_KEY` (and `ELEVENLABS_VOICE_ID`) |
| Web Push to your phone | Off | `VAPID_PUBLIC_KEY` and `VAPID_PRIVATE_KEY` (`npx web-push generate-vapid-keys`), then allow notifications in the app |
| A daily cap on Edward's own pushes | None | `EDWARD_PUSH_BUDGET=<n>` |
| Brave search | Not used while Anthropic's server web tools are on | `BRAVE_SEARCH_API_KEY` with `EDWARD_SERVER_WEB_TOOLS=off` |
| A tunnel in the health check | Not checked | `EDWARD_TUNNEL_LABEL` (the tunnel's LaunchAgent label) and `EDWARD_PUBLIC_BASE_URL` |
| Deep health checks for a hub kiosk and for mail | Off | `EDWARD_HEALTH_HUB_KIOSK=1`, `EDWARD_HEALTH_EMAIL=1` |
| Coding sessions and workers (Codex) | Dormant: their tools are not in the toolset | None in this release (see `docs/architecture/codex-and-orchestrator.md`) |

These are **on** by default. Know them before you leave Edward running:

- **The daily spend cap**, $5 over your local day (`EDWARD_DAILY_SPEND_CAP_USD`). Background work stops at 60 % of it, Edward's own turns at the cap, and your own turns may run up to $3 past it.
- **Quiet hours**, 22:00–07:00 (`EDWARD_QUIET_HOURS=22-7`; equal hours mean none). Pushes and spoken lines are held during them.
- **Two scheduled wakes a day.** A silent nightly at 03:00, skipped when nothing happened, and one budgeted free-time run at 13:00 (about $0.50 at most) for his screen and his own projects. The free-time run reaches nobody.
- **The surfaces pulse.** A check every 15 minutes that runs a model turn only when the board, the calendar, a screen or the widget changed.
- **The nightly backup** at 03:30 to `~/edward-backups`, on the same disk, unless you install with `--without-backup` or point `EDWARD_BACKUP_DIR` elsewhere ([BACKUP.md](../backend/scripts/BACKUP.md)). The folder is 700 and every dump 600: readable by your macOS user alone.
- **The late-dose alert** (`EDWARD_CARE_WATCH=on`). It has nothing to watch until the hub has timed care items.
- **The egress secret scan** (`EDWARD_EGRESS_SECRET_SCAN=on`) and **the shell sandbox** (`EDWARD_COMPUTER_SANDBOX=on`). Both are safety rails. Their `off` values are emergency switches only. The sandbox stops accidents and protects named paths; it is not a containment boundary ([prompt G](#g-edward-under-his-own-macos-user)).
