# The data model

The main Postgres tables and what each holds. `backend/services/database.py` is the source of truth; `init_db` creates tables, runs the raw migrations in place, and drops retired tables in dated batches (evolution, 2026-09-12; the engine layer, 2026-09-18). It runs at every backend start and in the graph worker, so an older install is migrated by starting the new code. Every naive timestamp column is UTC — stamp it from Python in UTC, never with the server's `now()` (it writes the session zone). `conftest.clean_db`'s truncate list is pinned against the models (`tests/test_engine_tables_dropped.py`).

## Core

- `settings` - Assistant configuration (model, system prompt — the default persona names the owner through `{OWNER}` —, timezone, `password_hash`)
- `conversations` - Conversation metadata and titles (`source`, `channel`, `notified_user`, `search_tags`, `message_count`; `context_pack` holds a thought's pack, and a journal entry's `thought` meta with `"kind": "mind_action"`)
- `checkpoints`, `checkpoint_blobs`, `checkpoint_writes` - LangGraph persistence (langgraph-checkpoint-postgres never deletes; `gc_checkpoints.py` prunes superseded checkpoints after the nightly backup and `prune_checkpoint_history.py` keeps only the latest, [ops-health-backups.md](ops-health-backups.md#maintenance-scripts))
- `memories` - Long-term memory with vector embeddings (`memory_type`, `origin`, `about`, `tier`, `temporal_nature`, `source_type`, `importance`, `confidence`, `reinforcement_count`, `used_count`, `last_used_at`, `last_accessed`, `access_count`, `valid_until`, `source_ref` JSON, `source_conversation_id`; [memory.md](memory.md))
- `documents` - Document store with vector embeddings (goals, dossiers, narratives, `Edward: state`)
- `memory_enrichments` - Reflection output (queries + results) loaded on the next turn
- `memory_connections`, `memory_flags` - Links between related memories, and quality / staleness / contradiction markers (from consolidation)
- `consolidation_cycles`, `consolidation_config` - Consolidation run metrics and settings
- `entities`, `entity_mentions` - Entity extraction
- `graph_ingest_queue` - Memories waiting for the optional Graphiti/kuzu graph worker (`GRAPHITI_ENABLED`)
- `open_loops` - Open loops surfaced in context and Today
- `external_contacts` - SMS/WhatsApp contact to conversation mapping (`last_channel` for reply routing)
- `scheduled_events` - Reminders, messages, tasks, and Edward's plumbing (the pulse, the nightly tick, `wake_me`, `proactive_sweep`; retired seeds are cancelled at boot) with `event_metadata`
- `skills` - Integration enabled state
- `custom_mcp_servers` - User-added MCP server configurations (`env_vars` in plaintext — keep tokens out of them)
- `files` - Persistent file storage metadata
- `persistent_databases` - Named PostgreSQL schema metadata (each a schema `edward_db_<name>`, owned by the Postgres superuser role — why backups run as that role)
- `widget_state`, `widget_tokens` - iOS Scriptable widget content and access tokens
- `usage_events` - Token usage per turn (`source` = the turn's source)

## The mind, the ledgers, trust

- `ledger_events` - One narrow table for what a health check wants beyond turns ([ledgers.md](ledgers.md)): `mind` (a wake: source = the reason, outcome, `detail` ≤ 600 chars with tools, surfaces, delivered, `thought_id`, `decay`), `acted` (an unattended turn that ran code), `decision`/`pulse`, `rate_gate`, `scheduled_event`, `browser`, `voice_session`, `voice_handoff`, `egress`, `taint`, `approval`, `forget`, `memory_report`, `care_alert` (the late-dose alert), and since 2026-10-11 `notice` (a noticing pass), `speak` (a speak turn), `reach` (every `reach_service.tell_owner` call; `ref_id` = its dedupe key). `surplus` / `controller` rows from the retired surplus queue age out with the 30-day retention
- `turn_log` - One row per LLM turn from every entry point (no message text)
- `self_observations`, `self_observation_hits` - Tool failures and other self-observations, collapsed by signature, with one hit row per occurrence
- `user_signals` - The owner's implicit verdicts from the UI (`today_action`, `chat_stopped`, `feedback`, `mind_verdict`, `idea_tried`, `idea_dismissed`, …)
- `llm_calls` - The pay-per-token ledger (2026-10-10): one row per Claude call priced in dollars (tokens by kind, thinking, web searches, `cost_usd`, purpose, priority, the turn); the daily spend cap and the optional credits view read it; kept 400 days (`services/llm_spend.py`)
- `codex_calls`, `llm_meter_samples` - The Codex fallback's ledger: one row per Codex HTTP request and `codex exec` turn; every quota-meter reading the rate gate adopts. Empty unless `EDWARD_LLM_PROVIDER=codex`
- `pending_approvals` - Sends and Codex spawns the taint gate held for the owner's approval (only with `EDWARD_TAINT_APPROVALS=on`)
- `chat_standdowns` - Chats Edward stays out of, with expiries
- `edward_notes` - Edward's notes on Today: the mind's notes (kind `mind`), the heartbeat's whispers (`whisper`), ideas (`idea`); `dismissed_at` when the owner pressed "Done" (2026-10-11)
- `noticings` - Edward's private noticings (2026-10-11, `services/noticing_service.py`): one row per thing the noticer (or code: a security line, a care alert) saw — `origin` (noticer / care / code / sweep), `kind`, `category`, the `claim` (never shown on a glanceable surface), `sources_json` (`hb:<event id>` refs), `chat_identifier`, `who_json`, `dedupe_key`, `closes_at`, the gate's `decision` / `decision_reason` / `floor_push`, `status` (open / held / shadow / silent / spoken / dropped / failed / expired) with `due_at` and `attempts`, the delivery (`surfaced_at`, `channel`, `push_log_id`, `note_id`, `thought_id`) and its `outcome` (opened / acted / seen / dismissed / ignored / rejected). Naive UTC; pruned at 30 days by the sweep ([noticing.md](noticing.md))
- `wishlist_items` - Edward's wishlist (status open/planned/building/done/declined, `times_wanted`)
- `orchestrator_tasks`, `orchestrator_config` - The worker task queue and settings (dormant: nothing creates tasks since the toolset, [codex-and-orchestrator.md](codex-and-orchestrator.md))
- `claude_code_sessions` - Codex session tracking (the name predates the switch to `codex exec`)
- `proactive_config` - The dossier thresholds (the proactive jobs themselves are wakes now)

## Heartbeat and messaging

- `heartbeat_events` - iMessage / calendar / mail events the listeners store (`notified_at`, `notification_batch_id`, `mind_attempts`). iMessage rows get an explicit `id` at insert (the describer and the ping row need it before the commit), `triage_status='ping'` (an open ping: never the mind path, escalations or Today), and `raw_data` keys `edward_echo` (`rowid` / `inflight` / `hash` / `signature`: Edward's own send coming back, stored `dismissed`), `ping` (`{"kind", "ping_id"}`), `message_guid`, `thread_originator_guid`; `full_text` keeps 4,000 chars
- `heartbeat_config` - Listener tracks, intervals, owner handles (`EDWARD_OWNER_HANDLES` adds to them). `imessage_enabled` defaults **off** — reading every iMessage is opt-in and needs Full Disk Access. `mind_enabled` defaults on and no longer chooses anything: every cycle runs the mind path, and the status route always reports it on
- `heartbeat_feedback` - The owner's "right call?" verdicts on what Edward did with a message; read by the mind only
- `triage_results`, `triage_decisions` - The legacy triage engine's cycle metrics and decision trail. Nothing writes them since the engine was deleted; `GET /api/heartbeat/triage` and the Listening views still read what an older install has
- `imessage_outgoing_sends` - The iMessage send ledger. Each row carries what the send's read-back found: `message_rowid` / `message_guid` (the chat.db row it became), `text_norm_hash` (the normalised text the listener's echo check compares), `route`, `chat_guid`, `ok`, `error`, `text_len`, `ping_id`, `purpose` (`tool` / `reply` / `fallback` / `limit` / `ack`), `turn_source`, `confirmed_at`. Only `ok` rows count as Edward's: the in-memory echo index (`services/imessage_sent.py`, rebuilt from 30 days of ok rows at start) and every dedupe read them. `sent_at` and `confirmed_at` are timestamptz — **bind aware UTC only** (a naive cutoff missed a row stamped "now" on a host whose zone is not UTC). Kept 90 days. An existing table gets the columns from `services/pings/schema.py`'s `ALTERS`; existing rows read `ok = true`.
- `imessage_chat_conversations` - iMessage chat → conversation mapping
- `pings` - One row per message that asked Edward something (an `@Edward` mention, the owner's self-chat, a reply to Edward's bubble): `source_ref` (`imessage:<rowid>`, unique), `kind`, `must_answer`, `from_owner`, `third_party`, `sender_known`, the chat (`chat_identifier`, `chat_guid`, `chat_name`, `is_group`), the whole `text` (≤ 10,000 chars), `status` (open → running → answered / fallback / declined / failed; held, limited, expired; `legacy` while `EDWARD_PINGS=off`), attempts and the reply's timing. The listener writes it inside its own transaction under a SAVEPOINT (`services/pings/hooks.py::create_ping`), so a failed ping row never costs the message. A SQLAlchemy Core table on its own MetaData (`services/pings/schema.py`), created by `init_db`; naive UTC; kept 90 days ([pings.md](pings.md))
- `media_items` - One row per piece of media Edward has seen (`services/media/schema.py`). Naive UTC; kept ([media.md](media.md))
- `contact_identity_aliases` - Handles that belong to the same person
- `interrupt_deliveries` - Unread since the interrupt ladder was retired (2026-10-11); kept for an install that rolls back, then to be dropped
- **The heartbeat sub-engine tables** (`heartbeat_contact_*`, `heartbeat_*_profiles`, `heartbeat_false_dismissal_signals`, `heartbeat_chat_backoffs`, `heartbeat_adaptive_dismissal_counters`, `coordination_*`, `communication_*`, `conversation_momentum_records`, `action_episode_tracker`, `admin_obligation_state`, `live_conversation_sessions`, …): the learners around the legacy triage engine, deleted with it. The models are still declared in `database.py` and nothing writes or reads the tables; they are left for an older install to drop when it no longer wants the history
- `gmail_credentials`, `email_actions`, `email_sender_prefs`, `email_unsub_queue` - Gmail and email management

## Push, presence, surfaces, hub

- `push_subscriptions` - Web Push endpoints and keys (`failed_count`, `last_seen_at`)
- `push_log` - The push ledger: one row per notification sent to the owner's devices, whichever sender, with `opened_at` / `snoozed_until`
- `presence_beacons` - Which of the owner's screens is awake
- `screen_state` - Edward's screen on the host Mac (one row, id `mini` — a name kept for existing installs: the stage's scene, the lines he said, the owner's hold (`asked_at` / `asked_until`) and what is on top; the old `layout*` columns are read only to convert a stored 6×4 layout)
- `hub_canvas`, `display_config` - The household screen canvas; the display, hub and who-is-at-the-hub settings (JSON rows by id)
- `pets` - The household's pets on the hub board, planted once into an empty board from `EDWARD_HUB_SEED` (`backend/hub_seed.example.json`); empty without a seed ([hub.md](hub.md))
- `care_items` - One dose slot each: a bowl, a pill, a supplement (hub)
- `care_events` - Logged gives, with `given_by` attribution (hub)
- `hub_list_items` - Shopping / household list lines (hub)

## Gone

The waitlist (router, service and model deleted in `ef5db5c`; `/` redirects to `/login`; an install that still has a `waitlist_signups` table can drop it), the evolution tables (`evolution_config`, `evolution_history`, `evolution_proposals`, `dream_signature_cooldowns`, dropped 2026-09-12), `wakeup_ticks` and some 80 engine-layer tables (dropped 2026-09-18, the mind rebuild), and the legacy triage engine with its sub-engines (code deleted for this release; their tables are described above).
