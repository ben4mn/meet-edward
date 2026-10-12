# Push notifications and Edward's notes

How Edward reaches the owner's phone and Today: the one door every reach goes through (`reach_service.tell_owner`), Web Push with VAPID, the push ledger, action buttons, quiet hours, and the notes the mind leaves on Today.

## Reaching the owner: `reach_service.tell_owner` (2026-10-11)

(`backend/services/reach_service.py`, `backend/services/offer_grammar.py`)

- **Why**: twelve code paths used to ring the owner's phone, and none could see what the others had said. Not one of 33 pushes in the evidence window was opened, and quiet hours were held only for the templated senders. Every reach now goes through one function, and the rules that make it bearable are code, not prompt.
- **`tell_owner(message, title=None, urgent=False, link=None, *, origin=None, noticing_id=None, dedupe_key=None, note_fallback=True) -> str`**: a note on Today, or a push when `urgent`. It is called by the `tell_owner` tool, the ping runner's failure notices, `care_watch`, the speaker's templated fallback and the nightly's note. The legacy `leave_note` and `send_push_notification` tools also go through it whenever they run inside a wake (a `mind:*` purpose), a speak scope or a household hub turn (`hub_voice`) (`graph/tools._reach_routes_this_turn`). Under `EDWARD_TOOLSET=legacy` those tools were how a wake reached the owner, and before this they bypassed the origin rules, quiet hours, the lint, one follow-up, the nightly's one note and the speaker's scope. Outside a wake they behave as before (the owner's chat, a reminder they set).
- `note_fallback=False` (care_watch's retry) returns `Not delivered — …` instead of leaving a second note when the push fails.
- It returns a success sentence (a downgrade says why: "Left it as a note — quiet hours (10 pm–7 am); it's on Today for the morning."), or a string starting `Tool error:` that the model should act on (lint, a repeat, a secret, high-risk text, a turn that cannot reach the owner). It raises only when neither a push nor a note could be delivered; a push that fails or reaches no device becomes a note.
- **The origin** is explicit, else derived (`derive_origin`), in this order:
  1. a live `speak_scope` → `speak`;
  2. `TurnPolicy.owner_asked` → `owner`;
  3. the current ping (the owner's own → `owner`, a friend's → `ping`);
  4. the `mind:<reason>` purpose (`self`, `manual`, `mention`, `nightly`, `free`, …; `pulse` and `surplus:*` → `surfaces`; `mind:notice*` → `other`);
  5. the turn source (`chat`, `voice`, `widget`, `alexa`, `ping_owner` → `owner` — the owner's self-chat is their own turn, the same list as `toolset.policy.OWNER_SOURCES`; the household hub's `hub_voice` → `hub`);
  6. a caller in `services.pings` that passes no origin → `ping:other`.

  The ping runner passes `origin` (`ping:owner` / `ping:other`, `runner.notice_origin`) and its own once-an-hour key as `dedupe_key` (`held:<id>`, `failed:<id>`, …), so the reach row names the notice. Nothing at all → `other`, refused: a code caller passes its `origin`.

  | Origin | Lint | Push budget | Quiet hours (urgent) | Other |
  |---|---|---|---|---|
  | `owner` | no | exempt | exempt | never deduped |
  | `ping:owner` | no | exempt | exempt | code: secrets redacted, repeats skipped |
  | `ping:other` | no | exempt | → note | code |
  | `care` | no | exempt | exempt | code; the caller dedupes per dose |
  | `fallback` | no | counts (when a budget is set) | trusts the gate | code |
  | `speak` | yes | counts | trusts the gate | the scope's channel is the ceiling; health/family or sensitive → a private push ("Something needs you before 7:30 pm — tap to read") and the full offer in a note |
  | `self`, `manual`, `mention`, `scheduled_event`, `ping`, `worker`, any other wake reason | yes | counts | → note | — |
  | `hub` (the household hub's voice: someone in the owner's home, not necessarily the owner) | no | counts | → note | the secret scan and high-risk gate as a model origin; one follow-up |
  | `nightly` | yes | never pushes | — | at most one note a night |
  | `free`, `surfaces`, `other` | — | — | — | `Tool error: this turn can't reach <owner>` |

- **The checks, in order**:
  1. The egress secret scan (`EDWARD_EGRESS_SECRET_SCAN`): a model origin is refused ("that would put a secret on <owner>'s lock screen"); a code origin has the spans redacted.
  2. The high-risk gate (`push_safety_gate`) on an urgent reach the owner did not ask for: refused to a model; a code notice goes out private.
  3. The **offer grammar** (`offer_grammar.lint`): whose message and when, no greeting or filler, exactly one closing question, "not sent" when something was prepared; a push body ≤ 180 characters, a note ≤ 600, a title ≤ 60. It runs on a push and on anything the speaker says (`kind="offer"`). A note from Edward's other turns (`kind="note"`: a follow-through's report, the nightly's one note, a friend's relay) keeps only the length, the no-greeting rule and "not sent": it may simply tell. (Review, 2026-10-11: the grammar is for can't-wait reaches; applied to every note it refused statements that ask nothing.)
  4. **One follow-up**: the same key delivered in the last 24 h is refused to a model ("you told <owner> this at 7:34 pm — once is enough…") and skipped for code. The key is the explicit one, the scope's, the noticing's, else the normalized message — never the title alone, which is often generic (every ping notice is titled "Edward"). Ping notices (`ping:owner`, `ping:other`: each failed ping is its own thing, and the runner throttles each key per hour) and care alerts (`care_watch` dedupes per dose and retries a push that did not land) are never deduped here.
  5. The channel: **quiet hours** in the owner's zone, then the **push budget, only when one is set**.
- **Quiet hours** are `EDWARD_QUIET_HOURS`, `start-end` in local hours (default `22-7`; the window wraps past midnight when start > end, equal hours mean no quiet hours, and an unparseable value falls back to 22–7). `speak_gate.quiet_hours()` and `reach_service` read it per call; `push_ledger.QUIET_HOURS` reads it at import (it backs `push_ledger.hold_reason` and the screen's quiet lines).
- **No daily push cap by default** (design decision, 2026-10-11: "push, with no limit"). `EDWARD_PUSH_BUDGET=<n>` caps Edward's counted pushes in the owner's local day (`push_ledger.count_sent_today("reach:", exclude=EXEMPT_SOURCES)`), and with a cap set an unreadable count is treated as spent. Quiet hours, the grammar and one follow-up hold either way.
- **Delivery**: a push goes out with `source="reach:<origin>"` and `respect_quiet_hours=False` (the rules were already applied), `tag="edward-reach"`. A note is `leave_note(kind="mind")`, marked `needs_you` when it ends in a question. The link defaults to the wake's journal entry, else `/today`. Each call writes one `ledger_events` row (`kind="reach"`, `source` = the origin, `outcome` `push` | `note` | `downgraded` | `refused` | `skipped`, `ref_id` = the dedupe key) and, with a noticing, stamps its `surfaced_at`, `channel`, `push_log_id` / `note_id`.
- **The owner's channel** (`backend/services/owner_channel.py`): an urgent reach goes out on `EDWARD_OWNER_CHANNEL`, read at call time:
  - `push` (the default);
  - `imessage_self`: a text to the owner's first handle, signed "Edward:". The handles are the heartbeat config's `imessage_owner_handles`, then `EDWARD_OWNER_HANDLES`. Links point at `EDWARD_PUBLIC_BASE_URL`, else the origin of `EDWARD_PUBLIC_SCREEN_URL`, else `http://localhost:3000`. It works only because the iMessage transport recognises its own sends by ROWID (`services.imessage_sent`); otherwise the owner's text would read back as a self-chat ping;
  - `edward_number`: an SMS from `TWILIO_PHONE_NUMBER` to the owner's phone handle.

  Every channel writes `push_log` with `source="reach:<origin>"`, so a budget counts them alike. Any failure (no handle, no Twilio, a failed send) falls back to a push and says why. Test: `tests/test_owner_channel.py` (both transports stubbed).
- **`speak_scope(noticing_id, channel, floor_push, who, sensitive, dedupe_key, *, category, closes_at)`** is set around a speak wake; `tell_owner` reads the origin, the ceiling, `who` (for the lint) and the key from it, and appends each delivery to `scope.delivered`.
- **`edward_notes.dismissed_at`**: "Done" on a note (`dismiss_notes`) stamps it beside the expiry. The noticing ladder demotes a category only on the owner's explicit dismissals ([noticing.md](noticing.md)).
- Tests: `tests/test_reach_service.py`, `test_push_budget.py`, `test_offer_grammar.py`, `test_push_ledger.py`, `test_notes_service.py`.

## Push notifications

(`backend/services/push_service.py`, `backend/routers/push.py`)

- Self-hosted Web Push with VAPID. Generate a key pair once and set `VAPID_PUBLIC_KEY` and `VAPID_PRIVATE_KEY`; `VAPID_CONTACT_EMAIL` is the `sub` claim push services may use to reach you (an address, a `mailto:` or an `https:` URL; default `mailto:admin@localhost`).
- Failed deliveries are tracked, and a subscription is deactivated after 3 failures. Table: `push_subscriptions` (endpoint, keys, is_active, failed_count, last_seen_at).
- REST: `GET /api/push/vapid-key`, `POST /api/push/subscribe`, `POST /api/push/unsubscribe`, `GET /api/push/status`, `POST /api/push/test`, `GET /api/push/log`, `POST /api/push/ack`, `POST /api/push/snooze` (`vapid-key` and `status` are public).
- In the toolset, Edward pushes with `tell_owner(urgent=True)`. The legacy `send_push_notification(title, body, url?)` tool refuses high-risk text (`Tool error:`) and takes `urgency="high"`. The interrupt ladder that used to take it (screen, then phone, then a text) was retired on 2026-10-11, so it is a plain push. A push from a wake links to its journal entry (`/chat?c=<entry>`), and opening the entry acks it ([mind.md](mind.md)).
- **A note is the default.** Pushes are for what can't wait until the owner next looks at Today (at night, only what can't wait until morning).
- The memory report never pushes (2026-10-11): it writes a `memory_report` ledger row that `GET /api/health/report` shows ([ops-health-backups.md](ops-health-backups.md#the-memory-report)). The late-dose alert is code and pushes as `reach:care` ([hub.md](hub.md)).

## The push ledger

(`backend/services/push_ledger.py`, `push_service.py`, `frontend/public/sw-push.js`, `frontend/lib/notificationNav.ts`; audit 2026-09-12)

- Every push passes `source=` and is written to `push_log` (`GET /api/push/log`). `POST /api/push/ack` (a service-worker click, a display tap, or opening the conversation: `loadConversation` acks by `conversation_id`) stamps `opened_at`. The mind's pack renders the recent ledger (`push_ledger.recent_text`), so every wake sees what was already said.
- **Every push carries a URL, and the tap goes there** (`resolveNotificationNav` → `router.push`): a wake's push → `/chat?c=<id>`, a Today item → `/today`, a system alert → the owning settings room. **Do not go back to loading chat state from the click handler**: that was the dead notification tap fixed on 2026-09-12.
- `respect_quiet_hours=True` is the hard quiet-hours hold for the templated senders that remain (a held send's approval request, a contradiction question). The wakes go through `reach_service`, which applies quiet hours itself.
- **Whispers never push.** A whisper was the legacy heartbeat triage's quiet tier: a thought conversation, an unread dot (`mark_user_notified`) and a note, never the phone. The legacy triage is gone ([heartbeat.md](heartbeat.md)); the `whisper` note kind remains for old rows.
- **Edward's notes** (`services/notes_service.py`, table `edward_notes`): the mind's notes (kind `mind`, through `tell_owner` / `leave_note`) and, historically, whispers (kind `whisper`); `_TTL_HOURS` lists the kinds and their lifetimes (24 h, ideas a week). The templated services (morning briefing, meeting prep, open-loop nudge, weekly patterns) are gone; the wakes carry their inputs. Today shows a `needs_you` note as a need (kind `note`, with an Open action) until it expires, and the rest in the digest; every wake's pack carries the last 24 h of notes. **Do not turn notes back into pushes.**
- **Action buttons**: every payload carries `kind`, `ledger_id`, `delivery_id` and Open / Done / Snooze 1h (`build_payload`). Done acks; Snooze posts `POST /api/push/snooze`, and the `*/5` `proactive_sweep` re-sends the ledger row once when it is due ("Again: …", `push_ledger.resend_due_snoozes`). The service worker is `public/sw-push.js`.
- A presence beacon refreshes the device's `push_subscriptions.last_seen_at` (in `_persist_beacon`); a targeted push picks a device by id or `kind:<kind>` (`services/push_targeting.py`).
- **Heartbeat verdicts** (`heartbeat_feedback_service`): "right call / should have told me / should have noted / not worth noting" are stored in `heartbeat_feedback` and read by the mind only (its pack and the nightly). Until the legacy triage was removed they were also copied into `heartbeat_false_dismissal_signals` for its learners; nothing reads that table now.
