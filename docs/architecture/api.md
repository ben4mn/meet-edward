# The HTTP API, the chat stream and the sockets

Every route the backend serves (built from `main.app.routes`; `tests/test_doc_lint.py` fails when a served route is missing here or a listed one is not served), the chat stream's SSE events as `services/graph/streaming.py` and `routers/chat.py` emit them and `frontend/lib/api.ts` types them, and the two WebSockets. Everything is under `/api` and behind the owner's session cookie unless marked public ([auth-and-pwa.md](auth-and-pwa.md)), also before a password is set: until then only `/health`, `/`, `GET /api/auth/status`, `POST /api/auth/setup` (with the one-time setup code, from the host Mac) and the public routes answer. The frontend also serves `GET /build-id` (Next, not the backend). FastAPI's own `/docs`, `/redoc` and `/openapi.json` are public and not listed.

## Routes

| Endpoint | Description |
|----------|-------------|
| **Chat and conversations** | |
| `POST /api/chat` | Streaming chat (SSE, below); multipart for attachments and voice memos |
| `POST /api/chat/simple` | Non-streaming chat |
| `GET /api/chat/{conversation_id}/processing` | Whether a turn is running in that thread |
| `GET /api/greeting/stream?fresh=1`, `POST /api/greeting?fresh=1` | The greeting line (stale-while-revalidate; `fresh=1` rebuilds a line older than ~20 s, at most once per 20 s) |
| `GET /api/conversations` | List (`?group=yours\|edwards`, search, paging); journal entries carry `thought {kind, ledger_id, reason, acted_at, outward, verdict, reached}` |
| `GET/PATCH/DELETE /api/conversations/{id}` | One conversation (messages with `trace`; an entry first when the thread doesn't hold it), rename, delete |
| **Today and notes** | |
| `GET /api/today` | Today feed: ranked needs (with actions), digest, presence, the owner's conversations |
| `POST /api/today/heartbeat/{event_id}/done` | "Done" on a message or calendar card: `{also: [ids]}` stamps them seen (`notified_at`, batch `owner:done`) |
| `GET /api/notes?hours=&limit=` | Edward's live notes, newest first (ideas excluded) — Today's "and N more" |
| `POST /api/notes/{id}/dismiss` | "Done" on a note card: `{also: [ids]}` expires them now (rows kept for the pack) |
| `GET /api/journal/week` | The week's observations (the Journal panel) |
| **The mind** | |
| `GET /api/mind/now` | Edward's live state (the wake or pulse running, last five wakes, next wake, approvals and unread thoughts waiting, quota, hands); never a 500. `quota.period` is `day` (the Claude spend cap, reset at the owner's local midnight) or `week` (the Codex fallback's window) |
| `POST /api/mind/wake` | `{reason, extra?}` → **202** `{accepted, reason, joined}`; runs as a tracked task (joins one pending or running) |
| `GET /api/mind/recent` | The mind's ledger rows |
| `GET /api/mind/log?days=` | Every wake with its pushes, verdict and journal entry |
| `GET /api/mind/intentions` | Live intentions with the event waiting on each |
| `POST /api/mind/verdict` | `{ledger_id, verdict: right_call\|wrong_call, note?}` — the owner's "Right call?" (mind and `acted` rows) |
| **Approvals and signals** | |
| `GET /api/approvals?status=`, `GET/POST /api/approvals/{id}` | Sends and Codex spawns held by the taint gate (only with `EDWARD_TAINT_APPROVALS=on`); `{decision}` runs the call once (`delivered` from the tool's own reply; an approved Codex spawn answers at once) |
| `GET/POST /api/signals` | The owner's implicit verdicts from the UI (allowlisted kinds) |
| **Memory and documents** | |
| `GET /api/memories` | List/search memories (filters) |
| `GET/PATCH/DELETE /api/memories/{id}` | Memory CRUD |
| `GET /api/memories/{id}/provenance` | Where a memory came from, from the record |
| `POST /api/memories/forget` | Verified forget: preview, then confirm; returns counts |
| `GET /api/memories/context` | Contact memories for a context view |
| `POST /api/memories/backfill-temporal`, `POST /api/memories/backfill-tiers` | Operator backfills for an install migrated from an older Edward |
| `GET/POST /api/documents`, `GET/PATCH/DELETE /api/documents/{id}` | Document store |
| `GET /api/consolidation/status` | Consolidation state, including its config (`enabled`, budget) |
| `PATCH /api/consolidation/config` | Change consolidation config (`{"enabled": false}` pauses it) |
| `GET /api/consolidation/cycles`, `POST /api/consolidation/trigger` | Past cycles; run one now |
| `GET /api/consolidation/connections?with_content=true` | Memory connections with both memories' text |
| `GET /api/consolidation/flags`, `GET/PATCH /api/consolidation/flags/{id}` | Flags; one flag with both texts |
| `POST /api/consolidation/flags/{id}/resolve` | `{keep}` — keep one memory, retire the other |
| `POST /api/memory/graph/backfill`, `GET /api/memory/graph/backfill/status`, `GET /api/memory/graph/status` | The optional Graphiti graph worker (`GRAPHITI_ENABLED` queues memories; the worker fills the graph only with `GRAPHITI_INGEST_ENABLED`) |
| **Events, workers, wishlist** | |
| `GET/POST /api/events`, `GET/PATCH/DELETE /api/events/{id}` | Scheduled events (items carry `event_type`, parsed `event_metadata`) |
| `GET /api/orchestrator/tasks`, `GET /api/orchestrator/tasks/{id}` | Worker and Codex tasks (dormant: nothing creates them since the toolset, [codex-and-orchestrator.md](codex-and-orchestrator.md)) |
| `GET /api/orchestrator/tasks/{id}/events`, `POST /api/orchestrator/tasks/{id}/message`, `POST /api/orchestrator/tasks/{id}/cancel` | A task's event stream; message it; cancel it |
| `GET /api/orchestrator/status`, `GET/PATCH /api/orchestrator/config` | Orchestrator state and config |
| `GET /api/wishlist?status=`, `POST /api/wishlist`, `PATCH/DELETE /api/wishlist/{id}` | Edward's wishlist |
| **Settings, spend, health** | |
| `GET/POST /api/settings` | Settings (a model id the provider doesn't serve → 400) |
| `GET /api/settings/models` | The models and what each kind of turn runs on (`mind_model`, `mind_effort(s)`, `pulse_*`, `chat_*`) |
| `GET /api/settings/codex-usage` | The Fuel card's data: `provider`, the `claude` spend block, and — only on the Codex fallback — the Codex quota meter and pace decision |
| `GET /api/health/spend?days=7` | `claude`: today's Claude spend by purpose and model, the daily cap, credits left (only when `EDWARD_CREDITS_START_USD` and `_DATE` are set), a per-day series; on the Codex fallback, who spent the week, burn rate, projection and meter cost per model |
| `GET /api/health/email` | Public (the watchdog has no cookie): `{"verdict", "summary"}` (verdict `ok`, `warn` or `unknown`) — mail's warnings only, no address or subject; kept 60 s ([ops-health-backups.md](ops-health-backups.md)) |
| `GET /api/health/report?days=7` | Where a health check starts: turns, tools, observations, signals, events, wakes, workers, `memory_report` (the newest daily report row), `proactivity` (mode, trust line, verdict), `email` (Mail.app, Mail's index, the mail listeners, Gmail — a verdict and warnings), trust, log, storage, `spend.claude` (warns past 80 % of the daily cap), `pings` (how every ping ended: by status and who asked, answered within 2 min, median latency, open too long, self-replies, a bucket per day — [pings.md](pings.md#the-metric-apihealthreportpings)) |
| `GET /api/usage` | Token usage summary (the Usage panel) |
| `GET /api/skills`, `PATCH /api/skills/{id}`, `POST /api/skills/reload` | Integrations |
| `GET /api/custom-mcp`, `PATCH/DELETE /api/custom-mcp/{id}`, `POST /api/custom-mcp/{id}/restart` | Custom MCP servers |
| `GET /api/databases`, `GET/DELETE /api/databases/{name}`, `GET /api/databases/{name}/tables`, `GET /api/databases/{name}/tables/{table}/columns` | Persistent databases |
| `GET /api/files`, `POST /api/files/upload`, `GET/PATCH/DELETE /api/files/{id}`, `GET /api/files/{id}/download` | File storage |
| `GET /api/proactive/status`, `GET/PATCH /api/proactive/config`, `GET /api/proactive/entities`, `GET /api/proactive/open-loops`, `PATCH /api/proactive/open-loops/{id}` | The wakes list, dossier thresholds, entities, open loops |
| `GET /api/debug/graph`, `GET /api/debug/health`, `GET /api/debug/memories`, `GET /api/debug/memories/stats` | Debug (graph structure, component health, memory browser) |
| `GET /health`, `GET /` | Liveness (outside `/api`) |
| **Heartbeat, email, Alexa, presence** | |
| `GET /api/heartbeat/status`, `PATCH /api/heartbeat/config` | Listener status and config (which listeners run — iMessage is off until you turn it on —, intervals, owner handles; `mind_enabled` is always reported on: every cycle runs the mind path) |
| `GET /api/heartbeat/events`, `GET /api/heartbeat/triage`, `GET /api/heartbeat/act-queue`, `GET /api/heartbeat/recent-senders`, `GET /api/heartbeat/jarvis-metrics` | Stored events; the legacy triage engine's cycle results (history only, nothing writes them now); the act queue (retired with that engine: always the empty shape, kept for old clients); recent senders for autocomplete; how often Edward acted on a thread the owner was already answering |
| `POST /api/heartbeat/triage/run` | Ask the running heartbeat loop for an immediate cycle (operator) |
| `POST /api/heartbeat/events/{id}/feedback`, `GET /api/heartbeat/feedback` | The owner's verdict on what Edward did with a message (stored in `heartbeat_feedback`, read by the mind) |
| `GET /api/email/actions`, `GET /api/email/preferences`, `PUT/DELETE /api/email/preferences/{domain}`, `GET /api/email/sender-stats`, `GET /api/email/unsub-queue`, `POST /api/email/unsub-queue/{id}/approve\|skip` | Email management |
| `GET /api/gmail/status`, `GET /api/gmail/oauth/url`, `GET /api/gmail/oauth/callback` (public, state-checked), `POST /api/gmail/disconnect` | Gmail OAuth |
| `POST /api/alexa` (public; only the skill id is checked, no Amazon signature verification), `GET /api/alexa/status`, `POST /api/alexa/test` | Alexa skill ([integrations.md](integrations.md)) |
| `POST /api/presence/beacon` (public, token in body), `GET /api/presence/state`, `GET /api/presence/stream` (public, token-checked) | Which of the owner's devices is awake (the token is the widget token, owner-equivalent) |
| `POST /api/webhook/twilio`, `POST /api/webhook/twilio/whatsapp` (both public; 403 unless the `X-Twilio-Signature` is valid for `TWILIO_AUTH_TOKEN` — no token, every inbound refused), `GET /api/webhook/twilio/status` | Twilio SMS and WhatsApp inbound (processed async, answered via the API, not TwiML). Only the owner's handles get a turn; anyone else's text is held unanswered (ledger `twilio_inbound`) unless `EDWARD_TWILIO_ANSWER_ANYONE=on` |
| **Push, widget, voice** | |
| `GET /api/push/vapid-key` (public), `GET /api/push/status` (public), `POST /api/push/subscribe`, `POST /api/push/unsubscribe`, `POST /api/push/test` | Web Push |
| `GET /api/push/log`, `POST /api/push/ack`, `POST /api/push/snooze` | The push ledger; a tap acks; "Snooze 1h" |
| `GET /api/widget?token=` (public), `POST /api/widget/chat` (public, token-checked), `GET /api/widget/token`, `POST /api/widget/token/regenerate`, `POST /api/widget/refresh`, `POST /api/widget/pulse` | The iPhone widget (Scriptable). The token is owner-equivalent: `POST /api/widget/chat` runs a turn as the owner; regenerate a leaked one |
| `GET /api/voice/status`, `POST /api/voice/transcribe`, `POST /api/voice/synthesize` | Voice (batch STT, TTS as MP3) |
| **Screens, browser, hub** | |
| `GET /api/screen` | Edward's screen on this Mac, the stage (2026-09-28): the placed scene (pieces with their cells, where he stands, why, who set it), the owner's hold (`asked`, null unless one is active and something they asked for is on the page; `held_until`, the hold whatever is showing), the lines he said in the last 10 min, `owner_at_mini` (whether the owner is at this Mac), and what is on top; the old 6×4 `grid`/`layout*` keys for one release ([screen-and-browser.md](screen-and-browser.md)) |
| `POST/DELETE /api/screen/show`, `PUT/DELETE /api/screen/scene`, `GET /api/screen/look?at=page\|display`, `POST /api/screen/window` | Put something on top (a POST is the owner's and holds the screen; the page's DELETE is a tap, which neither obeys nor ends a hold); set the scene as the owner (`{pieces, me?, why?, hold_minutes?}`, holds the screen, 422 when no piece is usable) / back to the stock scene (ends the hold); a screenshot described in words; bring up the /screen tab (needs Chrome hands) |
| `GET /api/screen/live` | His thinking while a mind wake or the pulse really runs (`services/screen_thoughts.py`, in memory), and between turns his recent thoughts for the lane: `{thinking: {turn, source, about, since, lines, receipts} \| null, folded, recent: [{text, at, about, kind}], now}`, tool names only. `recent` is the closing lines of his wakes from the last 12 hours (`ledger_events` kind `mind`, acted or left alone; the nightly reflection and free time are wakes) — never the pulse's decisions or the owner's chat — cleaned and scrubbed like live lines, newest last, at most 12; `at` is aware UTC, `about` the wake reason; read from the database at most every 30 s. Never a 500: a failure reads as not thinking and as `recent: []` |
| `GET /api/display/{signals,alerts,calendar,memory_ticker,now_thinking,recents,vitals,workers}`, `GET/PATCH /api/display/settings` | The /screen and hub panels' data (`now_thinking` has had no /screen caller since the stage). Settings: `accent`, `time_of_day`, `density`, `avatar_size`, `show_panels` (legacy, ignored), and the owner's two stage switches `show_thinking` / `speak_aloud` (bools, default on) |
| `GET /api/browser/status`, `GET /api/browser/ext/status`, `POST /api/browser/act` | Edward's hands in the owner's Chrome: off unless `EDWARD_CHROME_HANDS=on` (status says so; `act` answers 503). Operator actions: tabs, open, use, done, close, read, find, click, type, key, scroll, back, wait, fullscreen, steps, look |
| `GET /api/hub/board`, `POST/DELETE /api/hub/care/{item_id}/log` | The pet board (planted from `EDWARD_HUB_SEED`, else empty); log / undo a give (409 when `for_care_day` is not today in the settings timezone) |
| `GET /api/hub/lists`, `POST /api/hub/lists/{name}/items`, `PATCH/DELETE /api/hub/lists/items/{id}` | Household lists |
| `GET/POST/DELETE /api/hub/canvas` | The household screen canvas |
| `GET/POST /api/hub/who` | Who is standing at the hub (clears after 30 min idle); `GET` also returns `people`, the household from `EDWARD_HOUSEHOLD`, owner first |
| `GET /api/hub/health`, `GET/PATCH /api/hub/settings` | Hub vitals (the LaunchAgents by `EDWARD_LAUNCHD_PREFIX`, the tunnel when `EDWARD_TUNNEL_LABEL` is set, the backup folder); screensaver settings |
| `GET/POST /api/hub/photos`, `GET/DELETE /api/hub/photos/{name}` | The screensaver album (upload converts HEIC) |
| `GET /api/photo-door/auth/status`, `POST /api/photo-door/auth/login`, `POST /api/photo-door/auth/logout` (public) | The household photo door's own session (`photo_door_token`); status returns `{authenticated, enabled}`, and a login while the door is off (no `PHOTO_DOOR_PASSWORD_HASH`) answers 403 |
| `GET/POST /api/photo-door/photos`, `GET/DELETE /api/photo-door/photos/{name}`, `GET /api/photo-door/photos/{name}/thumb`, `POST /api/photo-door/photos/{name}/rotate`, `GET/PATCH /api/photo-door/settings` | The photo door (`/photos`): the hub album and screensaver settings, nothing else; a door session or the owner's opens them; 404 while the door is off |
| **Auth** | |
| `GET /api/auth/status`, `POST /api/auth/setup`, `POST /api/auth/login` (all three public), `POST /api/auth/logout`, `POST /api/auth/change-password` | Single-user password auth. Status returns `{configured, authenticated, owner_name, setup_code_required}`; `setup_code_required` is true only while no password exists. Setup takes `{password, setup_code}` as JSON, with the one-time code from backend/.setup_code on the host Mac: 415 without `Content-Type: application/json`; 403 for an `Origin` that is not a loopback page, for a client that is not on the host Mac (`client_ip.is_local_client`), or for a missing or wrong code; 429 after 10 wrong codes per client in 15 minutes; 422 for a malformed body; 400 "A password is already set. Sign in with it."; 503 when the code file can't be written ([auth-and-pwa.md](auth-and-pwa.md#backend-auth)). Login is limited to 5 failures per client (`client_ip.client_ip`) and 100 across all clients per 15 minutes; change-password to 5 per signed-in session, outside that ceiling. Every 429 carries `Retry-After` |

Gone, and not to be documented again: `POST /api/waitlist` (router, service and model deleted in `ef5db5c`), the LangSmith debug routes (`/api/debug/langsmith/status`, `/api/debug/traces/*`, `/api/debug/trace/*`, removed with `langsmith_service.py` in `c3d553e`), `/api/wakeup/*`, the evolution routes, the USPS tracking router, `POST /api/orchestrator/tasks`, `GET /api/consolidation/config`.

## The chat stream (SSE)

`POST /api/chat` answers `text/event-stream`: each event is `data: {json}\n\n`, and an idle gap of 10 s gets an invisible `: heartbeat\n\n` comment so a tunnel and Next don't time the stream out (`routers/chat._yield_with_heartbeats`). That wrapper runs the whole turn in one context of its own (a copy of the router's, taken as the stream starts), so the turn record holds in every step and in the post-turn work — since 2026-09-27; before, a PWA chat turn lost it after its first step, and the ten-minute "Already sent…" check never applied to PWA chat sends ([tool-loop.md](tool-loop.md#stop-leaving-and-a-send-in-flight-2026-09-27)). Every event carries `type` and `conversation_id` (`streaming.create_event`); the first event of a new chat assigns the id, which the page `replaceState`s into the URL. A crash mid-stream becomes an LLM-voiced `content` event plus `done` (`_wrap_chat_stream`), never a raw error — except when the client left (Stop, a closed tab): then the step in flight is cancelled with `CLIENT_GONE` and nothing is written back. The frontend parses the stream in `lib/api.ts::streamChatEvents()` and folds every event through `lib/turnModel.ts::reduceTurn`.

| Event type | Fields | Emitted when |
|---|---|---|
| `user_message` | `content`, `attachments[{file_id, filename, mime_type, size, transcript}]` | First, for a posted voice memo: what the owner actually said (the router, before the turn) |
| `progress` | `step`, `status` (`started`/`completed`/`error`/`exhausted`/`retrying`), `message`, `count?`, `tool_name?` | `step` is `memory_search` (retrieval), `dream` (speculative recall), `tool_execution`, `search` (web search / page fetch), `generating` (the final answer; `retrying` when a tool call cut off by `max_tokens` is asked again — the PWA ignores it), or `tool_loop` + `exhausted` (ran out of tool rounds) |
| `memory_match` | `memory_id`, `memory_type`, `content_preview`, `score` | One per retrieved memory, only with `settings.debug_streaming` |
| `thinking` | `content` | The model is working between tool calls |
| `reasoning` | `content` | Reasoning-summary deltas (Claude's summarized thinking, or Codex's reasoning summary on the fallback) |
| `tool_args_delta` | `tool_name`, `call_id`, `delta` | Partial JSON of a tool call the model is still writing |
| `tool_start` | `tool_name`, `args_summary?` | A tool is about to run (`args_summary` for non-execution tools, e.g. a URL, a query, a recipient, a closed intention's outcome) |
| `code` | `code`, `language` | The code / query / command an execution tool runs (python, javascript, sql, bash) |
| `execution_output` | `output`, `stream` | stdout/stderr from code execution |
| `execution_result` | `success`, `duration_ms` | Code execution finished |
| `tool_end` | `tool_name`, `result` (≤ 500 chars) | A tool finished (a boundary refusal ends here too, with the refusal as its result) |
| `plan_created` | `plan_steps[{id, title, status, result}]` | The `plan` tool made a plan |
| `plan_step_update` | `step_id`, `step_status`, `step_result` | A plan step moved |
| `plan_updated` | `plan_steps` | The plan was revised |
| `plan_completed` | `plan_steps`, `plan_summary` | The plan finished |
| `cc_session_start` | `task_id`, `task_description` | An inline Codex session began (dormant: no tool starts one since the toolset) |
| `cc_text` | `task_id`, `text` | Codex output |
| `cc_tool_use` | `task_id`, `cc_tool_name`, `tool_input` | Codex used a tool |
| `cc_tool_result` | `task_id`, `text` | That tool's result |
| `cc_session_end` | `task_id`, `status`, `result_summary` (≤ 500) | The session ended |
| `content` | `content` | Answer text deltas (and the whole answer, or an early-stop summary, in the non-streamed paths) |
| `done` | — | The turn is over: the checkpoint is written and the thread is free (extraction continues in the background) |
| `interrupted` | `content` | Declared in `EventType` and typed in `lib/api.ts`, not emitted by the backend today |

A stream that closes without `done` (a hidden tab, a dropped connection) is `cut` in the turn model: the server may still be finishing and the thread reloads when visible.

## Sockets

- `WS /api/voice/ws` — live voice (Scribe realtime + streaming TTS). The protocol is documented at the top of `routers/voice_ws.py`; client→server `start_session`, `0x01`+PCM, `commit`, `interrupt`, `end_session`; server→client `session_started`, `partial_transcript`, `transcript`, `state`, `bridge`, `response_text`, `tool_activity`, `0x02`+PCM, `response_done`, `handoff`, `handoff_done`, `error`. It checks the `edward_token` cookie itself, with or without a password set ([voice.md](voice.md)).
- `WS /api/browser/ext` — the Edward Chrome extension's link; the auth middleware never sees WebSockets. Refused (close code 4004) unless `EDWARD_CHROME_HANDS=on`, then (4003) unless the connection is direct from this Mac (a loopback peer, no `X-Forwarded-For` / `X-Forwarded-Host` / `X-Real-IP` / `Forwarded` / `CF-Connecting-IP`) with `Origin: chrome-extension://<id>` for Edward's own extension: the ID its manifest's public `key` fixes (`routers/browser_ext.EDWARD_EXTENSION_ID`), or the ones `EDWARD_CHROME_EXTENSION_ID` names instead. The log names a refused ID. A local program can still forge all of it ([screen-and-browser.md](screen-and-browser.md#edwards-hands-in-the-owners-chrome)).
