# The ledgers and the health report

How a health check sees Edward think: `turn_log`, tool-failure observations, `user_signals`, `ledger_events`, retention, and `GET /api/health/report`.

## The ledgers

(`backend/services/turn_ledger.py`, `health_report_service.py`, `user_signals_service.py`; routers `health_report.py`, `signals.py`; added 2026-09-12)

- **Why**: heartbeat decisions, the push ledger and the owner's verdicts were recorded, but nothing about Edward's *turns* survived a restart. Tool failures, exhausted tool loops, voice latency and retrieval yield lived only in the SSE stream and a log that resets. `@observed_tool` had zero callers; `tool_failure` had never been written.
- **`turn_log`**: one row per LLM turn, from every entry point. `stream_with_memory_events` and `chat_with_memory` are thin wrappers around their `_…_impl`; both take `turn_source` (`chat`, `voice`, `hub_voice`, `heartbeat`, `scheduled_event`, `worker`, `pulse`, `mind`, `ping`, `ping_owner`, `twilio`, `widget`, `alexa`, …; an unlabelled non-streaming caller lands in `background`). No message or response text is stored. Fields:
  - model, provider, `fast_context`, `is_worker`, `ttft_ms`, `total_ms`, `iterations`;
  - `tool_calls` / `tool_failures` plus a compact `tools_json` (`{n, ok, ms, err}`);
  - memories retrieved / used / extracted;
  - `repeat_of_previous` (the owner asked the same thing again), `abandoned_previous` (a new chat within 60 s of an answer);
  - `outcome`: `running` (the process died mid-turn), `completed`, `empty`, `stopped` (Stop pressed, or the phone hung up), `error` with `error_class`, or `exhausted` (it ran out of tool iterations, marked by the `progress step=tool_loop status=exhausted` event). A consumer that breaks *after* `done` (the voice socket does) records `completed`, or `exhausted` if the loop ran out; before 2026-09-27 every voice turn read `stopped`.

  The row is inserted at turn start and updated at the end. A task-local `current_turn()` lets code deep inside a turn annotate it (used-memory credit, the non-streaming tool loop, post-turn extraction via `note_extraction`).
- **Tool failures are observations**: every `Tool error:` / `Unknown tool:` result writes `self_observations` kind `tool_failure`, signature `tool_failure.<tool>.<error_class>` (`classify_tool_result` strips ids, numbers and URLs so a failure collapses to one class). `BLOCKED:` circuit-breaker results are counted on the turn but not re-observed.
- **`self_observation_hits`**: one row per `record_observation` call (evidence capped at 500 characters). The observation row keeps collapsing by signature; the hits keep *when*, so the report can show a pattern stopping after a fix.
- **`user_signals`**: the owner's implicit verdicts from the UI, in allowlisted kinds (`today_action`, `today_action_failed`, `chat_stopped`, `response_retry`, `feedback`), detail capped at 1000 characters. Today posts one per action via `lib/signals.ts` and `postSignal` (fire-and-forget, `keepalive`). `POST /api/signals`, `GET /api/signals?days=`.
- **`ledger_events`** (`services/event_ledger.py`): one narrow table (kind / source / outcome / reason ≤ 300 / detail ≤ 600) for what a health check wants beyond turns:
  - `decision` / `pulse`: `changed` | `left_alone` | `failed`; reason = Edward's note; the surfaces touched, from the turn's tools via `turn_ledger.last_finished_turn()`;
  - `rate_gate` / `<caller>` / `deferred` (the Codex fallback's gate walks the stack for the first `services.` / `routers.` frame outside the LLM plumbing — `pick_caller`);
  - `scheduled_event` / `scheduler`: `completed` | `failed`, attempts, `delivered` (a successful send or push in the turn), `turn_id`, recurring;
  - `browser` / `<action>` / `failed`: from the hands' replies and the `run_steps` failure branch (reason = the error class), and since 2026-10-11 from the `chrome` and `browser` tools: source `<action>` for the owner's Chrome, `headless:<action>` for Edward's own browser, `look:<target>` for a picture that could not be taken (`services/toolset/_compat.py::record_failure`). Browser successes are visible in `turn_log.tools_json`;
  - `voice_session` / `<sensitivity>` / `ended`: turns, bridges, barge-ins, interrupts, noise restarts, errors, duration, mode.

  Workers already had a table (`orchestrator_tasks`), and the legacy heartbeat triage kept its decisions in `triage_decisions` until it was removed; the report summarizes those directly rather than duplicating rows.
- **Kinds added since**:
  - `mind` (one row per wake; its `detail` stays parseable under the 600-character cap — `mind_service.wake_ledger_detail` shortens `tools` first, with `tools_omitted`);
  - `acted` (an unattended turn that started Codex or installed an MCP server, with its journal entry — [mind.md](mind.md));
  - `memory_report` (the daily memory report, `source="script"`: the verdict as `reason`, the summary counts — [ops-health-backups.md](ops-health-backups.md#the-memory-report));
  - `care_alert` (`care_watch`, keyed `ref_id=care:<pet>:<care day>:<HH:MM>`: `pushed` (only a push that landed) / `downgraded` (a note instead; the push is retried) / `failed` / `refused` — [hub.md](hub.md));
  - `egress` (`blocked` / `withheld`), `taint` (`would_gate`), `approval` (`approvals_service`: `pending`, `expired`, `approved` / `denied`), `forget`, `voice_handoff`;
  - `twilio_inbound` (2026-10-12; source `sms` / `whatsapp`, outcome `held`, reason "not one of the owner's handles"): a text to Edward's Twilio number from someone who is not the owner, which got no turn and no reply; `detail` keeps the sender, the first 400 characters of the text and the media count, since the log line leaves the text out ([integrations.md](integrations.md#messaging-and-the-tool-lists));
  - `surplus` (the surplus queue, retired 2026-10-11: old rows only — [surplus.md](surplus.md)).
- **Proactivity kinds** (2026-10-11; [noticing.md](noticing.md)):
  - `notice`: one row per noticing pass. `source` is `stub` in `off` mode or `noticer`; `outcome` `ok` | `failed` | `deferred`; `ref_id` = the pass id; `detail` = events, noticed, invalid, ms, `error_class`. A parse failure is `failed`, never an empty `[]`.
  - `reach`: every `reach_service.tell_owner` call. `source` = the origin; `outcome` `push` | `note` | `downgraded` | `refused` | `skipped` | `failed`; `ref_id` = the dedupe key the 24-hour one-follow-up rule reads ([push-and-notes.md](push-and-notes.md)).
  - `speak`: one row per speak turn, written when the gate reserves it so the speak budget counts it at once. `source` = the allowed channel; `outcome` `started` → `spoken` | `dropped` | `silent` | `skipped` | `failed`; `ref_id` = the noticing; `detail.chat` = the chat key the one-per-chat-an-hour rule reads.
  - The noticing calls' purposes are `mind:notice` (the noticer), `mind:speak` and `mind:nightly`. `care_alert` rows are counted as care alerts once; the `origin='care'` noticing beside each is never counted twice.
- `turn_log.memories_used` counts the memories shown in the prompt; a memory's own `used_count` grows only when an answer cites it ([memory.md](memory.md)).
- `turn_log.memories_extracted` is NULL when no count was reported (extraction skipped, the streaming loop's 30 s timeout fired, or an exception escaped `extract_and_store_memories`) and `0` when extraction found nothing *or failed inside it* (a model error, bad JSON, a rate-gate deferral: `extract_and_store_memories` returns `[]` for all of them), so `turns_extracting_nothing` counts those too. `note_extraction` writes the row and, while the turn is still the task's `current_turn()`, the record; the end-of-turn update leaves the column alone when the record has no count. Until 2026-09-27 that update wrote `None` over the count: the non-streaming loop extracts *before* the turn ends, so every pulse, mind, scheduled-event and heartbeat row read NULL (0 of 1,573 in one week), and a streaming turn kept its count only because extraction usually landed after the update.

## The health report

**`GET /api/health/report?days=7`**. Start a health check here, then `GET /api/health/spend`, then psql. Each section fails on its own (`{"error": …}`), never the whole report.

- `turns`: by source and outcome, with p50/p95 first-token and total latency, iteration stats, the exhausted count, error classes.
- `tools`: calls and failures ranked by tool and class. `observations`: hits per day per kind, plus the open observations. `signals`: signal counts.
- `events`, `wakes`, `mind`, `workers`, `memory_report`: scheduled events, the wakes and their journal verdicts, workers, and the last memory report.
- `log`: tracebacks in the last 20 MB of `/tmp/edward-backend.log`, ranked by innermost Edward frame and error type, plus `HTTP 429` / `CODEX_LIMIT` / `LLMRateLimitDeferred` counts. The log itself is rotated by `scripts/health_check.sh` at 100 MB, five gzip archives kept ([ops-health-backups.md](ops-health-backups.md)).
- `storage`: the footprint of the ledger tables (including `llm_calls`), the database and free disk.
- `config`: the settings timezone against the host's (with them apart, every tick fires at the wrong hour).
- `trust`: tainted turns, what the gate would have held, egress it blocked, approvals ([trust-boundary.md](trust-boundary.md)).
- `calendar` (2026-10-11): which source served the owner's calendar last (`sqlite` — Calendar's own database, `mcp` — apple-mcp, or `unavailable`) and why not, the EventKit helper's access and how Reminders were read (`health_report_service.calendar_section`; [computer.md](computer.md)).
- `proactivity` (`noticing_service.proactivity_report`): the mode and push scope; the trust line (pings answered against proactive noticed / spoken / pushed / ignored); a headline; per-day counts (passes, failed, parse failures, deferred, noticed, held, silent, shadow, spoken by channel, outcomes, speak failures, care alerts, fallback and autonomous pushes); the category levels the ladder advises (`categories`, with probation dates); stalls (inbound against passes in 6 h, held past due, ambient past expiry, the last sweep and care check); and a verdict: `off`, `stalled` (no sweep in 15 min, or ≥ 10 inbound non-ping events in 6 h and no pass), `failing` (half of ≥ 4 passes failed in 6 h, a held item 15 min past due, an ambient item past expiry, or ≥ 2 speak failures in a day) or `ok`.
- `pings` (2026-10-11): how every ping ended, by status and by who asked (the owner, their self-chat, a friend); answered within 2 minutes; the median latency; pings still open or running 10 minutes after they were asked (`open_now`); `self_replies` (Edward answering his own echo: must stay 0); one bucket per day in the owner's zone (`pings.store.report`, [pings.md](pings.md#the-metric-apihealthreportpings)). A failing section is `{"error": <class and first line>}`.
- `email` (2026-10-11): Mail.app running, Mail's index fresh, each listener's last clean poll, Gmail signed in ([integrations.md](integrations.md)).
- `spend.claude` (2026-10-10): today's Claude spend against the daily cap (spent, cap, background share and stop, reserve, remaining today, state, calls and errors, the top purposes; credits remaining and days left only when `EDWARD_CREDITS_START_USD` and `EDWARD_CREDITS_START_DATE` are set) and a `warning` once today passes 80 % of the cap (`llm_spend.headline`).

## Retention

`turn_ledger.prune()` deletes `turn_log`, `self_observation_hits`, `user_signals` and `ledger_events` older than `RETENTION_DAYS` (30). The Claude ledger `llm_calls` is kept for `llm_spend.RETENTION_DAYS` (400), because the credits remaining are a sum since the purchase date ([llm-and-quota.md](llm-and-quota.md#pay-per-token-the-claude-spend-ledger-and-the-daily-cap)). `maybe_prune()` runs it after a ledger write at most once every 6 h per process; there is no separate loop. Rows are a few hundred bytes; expect single-digit MB a month.

## Tests

`tests/test_turn_ledger.py` (the pure record, the event wrapper including stopped / cancelled / error / exhausted, the task-local turn, persistence, abandonment, prune, caller labels), `test_event_ledger.py` (the pulse decision, delivered, caller picking, the rate-gate / browser / scheduler / pulse hooks, prune), `test_health_report.py`, `test_user_signals.py`, `test_voice_ws_router.py::test_session_end_records_a_voice_session_summary`, `test_self_observations.py::test_record_observation_writes_a_hit_per_call`; frontend `__tests__/signals.test.ts`.
