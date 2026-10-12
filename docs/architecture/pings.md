# Pings: someone asked Edward, and he answers in that chat

A **ping** is a message addressed to Edward in one of the owner's iMessage chats: an `@Edward` from anyone (the owner included, in any chat), anything the owner writes in their own self-chat, or an inline reply to one of Edward's messages. Since 2026-10-11 each ping gets **one reply turn of its own** and is tracked until a send into that chat is confirmed — or until it ends in a recorded, honest state.

Pings arrive through the iMessage listener, which is opt-in (`heartbeat_config.imessage_enabled`, default off — [heartbeat.md](heartbeat.md)): with it off, nobody can ping Edward from Messages.

Files: `services/pings/hooks.py` (the one door for callers outside the package: the shared ping context, the row insert, the lazy runner), `services/pings/detect.py` (pure: what is a ping), `services/pings/schema.py` (the `pings` table and the send ledger's read-back columns), `services/pings/outcomes.py` (pure: how a turn ended), `services/pings/store.py` (every read and write), `services/pings/delivery.py` (the runner's own reply), `services/pings/runner.py` (the sweeper, the claim-time rules, the turn, recovery), `services/pings/prompt.py`, `render.py`, `context.py`, `scope.py`, `limits.py`, `fallback.py`, `services/imessage_sent.py` (the in-memory index of Edward's sends, for echo recognition), `services/ping_service.py` (`addressed_recently` for the recipient rule) and the transport in `services/imessage_service.py` ([integrations.md](integrations.md)).

## Why

Before 2026-10-11, on the original install: the owner's `@Edward` in a group was dropped by the identity filter as their own message, and their ping silenced their friends' in the same batch; nothing woke the mind unless something escalated; the trigger cut messages at 200 characters with no chat name; a wake that merely "considered" a mention closed it; a mention wake had a tool list that couldn't do what was asked; a reply into a group with a 32-hex chat id went to `participant "<hex>"` and was reported delivered; and Edward's own echo looked like the owner's ping and he answered himself. The fix is structural: the listener records, the runner answers, and an answer is a send that chat.db confirms.

## The lifecycle

```
open ──claim──> running ──delivered send, clean end──> answered
 │                 ├──fallback delivered──> fallback        ("Sorry — …", or "cut off" after a progress send)
 │                 ├──decline recorded────> declined        (stand-down, tell_owner, silent may-answer)
 │                 ├──nothing deliverable──> failed         (the owner told, with the answer)
 │                 └──transient, attempts < 2──> open (next_attempt_at = now + 30 s)
 ├──unknown sender─────> held       (no turn; the owner gets a note, once a day per sender)
 ├──loop or rate limit─> limited
 ├──may-answer under a stand-down, or only the legacy toolset──> declined
 └──picked up > 24 h late──> expired (the owner told)
legacy: written while EDWARD_PINGS=off; terminal; the mind's mention wake answers
```

**The listener records**: every new chat.db row is classified (`detect.classify`); a ping gets a `pings` row inside the listener's own transaction under a SAVEPOINT (`hooks.create_ping`, which never raises — a failure costs the ping row, never the message), its heartbeat event gets `triage_status='ping'` (so it never enters the heartbeat cycle, escalations or Today), and after the commit the listener calls `hooks.kick`. Only `mention`, `owner_direct` and **the owner's own** inline reply are must-answer (`hooks.must_answer`); a friend's inline reply to Edward's bubble is may-answer, because to friends that bubble came from the owner's number. An `@edward` inside a link (`youtube.com/@edward`, `medium.com/@edward/…`), a URL balloon's `[link: "Edward (@edward) / X" …]` preview or a photo description's placeholder is not a mention: `detect.has_mention` reads the text without URLs and media placeholders, and `classify` reads the listener's caption when the message has one.

**The runner answers.** `main.py` starts it after the heartbeat through `hooks.start` (and stops it before), which does nothing when `EDWARD_PINGS=off`. The sweeper wakes on a kick or every 20 s:

1. `runner.recover` at start and every 60 s (below);
2. claims due `open` rows oldest first (`store.claim` is an atomic `open → running` that counts the attempt — given back when the claim ends without a turn, so two dry-pool hiccups at claim time never make the first real turn "Retry 3 of 2"), at most `EDWARD_PING_CONCURRENCY` (2) turns at once and **never two in one chat** (`runner.chats_in_flight`);
3. applies the claim-time rules, then starts the turn as its own task; the daily prune keeps 90 days of `pings` and of the send ledger (`store.prune`).

### Claim-time rules (in order, before any turn)

1. **Unknown sender** (`third_party` and not `sender_known`): held, and the owner gets a note — "+1 555… (not in your contacts) asked me in <chat>: '…'. I didn't answer — tell me if I should." — at most once a day per sender. **An unknown number in a group of contacts is answered** (design decision, 2026-10-11): a group counts when someone other than the sender is in the owner's contacts (the roster is read from chat.db; a failed lookup keeps it held).
2. **More than 24 h late**: expired, and the owner is told.
3. **May-answer**: declined when the chat has an active stand-down, or when only the legacy toolset is here (the legacy loop's `required_tools` would force a reply the turn should be free to skip).
4. **Limits** (`services/pings/limits.py`): the **loop breaker** — more than 3 friends' pings in one chat in 5 minutes, this one included, or, for the owner's own pings, more than 12 of theirs — stops the runner there and pushes the owner (once per chat an hour). Each party counts only its own pings (`store.pings_in_chat_since(third_party=…)`): the owner's lines already pass the structural echo checks and the unconfirmed-send breaker, so "remind me at 9", "actually 8:30", "and add milk", "thanks" is a conversation, not a loop (the fourth line used to be dropped and the owner pushed); the 12 is the backstop if every echo check failed. The **third-party rate** — 6 friends' turns started in one chat in the last hour (`EDWARD_PING_FRIEND_TURNS_PER_HOUR`) — posts the "taking a break" line in the chat once an hour and leaves the owner a note. The owner's own pings are never rate-limited.
5. **iMessage switched off** (the `imessage_applescript` skill): failed, the owner told at most once an hour.
6. **A mention or the owner's own ping releases the chat's stand-down** (`triage_service._get_active_standdown` / `_release_standdown`). Nothing else does.

### The reply turn

Each ping runs in its own conversation `ping-<id>` (source `external_message`, channel `imessage`; it lands in the owner's "yours" list with its reasoning and tools).

- **Gathering** (`runner.gather` → `services/pings/context.py::gather`, each source bounded at 25 s, each media item at 20 s on its own, and the whole at 50 s; [media.md](media.md)): 20 thread lines (each labelled the owner, Edward — by the send index or the signature — or the contact's name), the roster with names, up to 6 images and one PDF through the media hooks — this message's attachments first, then photos and voice messages from the thread in the last 30 minutes, newest first, each labelled `attachment:<rowid> · <name> · from <who> <time>` — and the links in the message, read first. When the context breaks, the runner reads the thread, roster and images itself (`runner._gather_own`); when the whole bound passes, the turn runs on the message alone.
- **The trigger** (`services/pings/render.py::build_trigger`): the whole message (`pings.text` keeps 10,000 characters), the chat's name, id and roster, the thread, the attachments, the links as read, a retry note ("Retry 2 of 2 — the last attempt stopped: …") and a stale note when the ping waited more than 30 minutes. Everything other people wrote, and a friend's ask itself, is wrapped in `<untrusted source="…">`. When `build_trigger` fails the runner builds a plainer trigger of its own (`runner._fallback_trigger`).
- **The system prompt** is `services/pings/prompt.py`'s (`prompt.system_prompt`), and it and the trigger name the tools of the toolset the call itself detected (`runner.has_policy`). The runner no longer keeps its own copy of the prompt (`runner._system_prompt` calls `prompt.system_prompt`): if the prompt module ever raises, the ping turn crashes and recovery settles it, instead of running on a stale copy. The owner is named from `EDWARD_OWNER_NAME` ("the owner" when unset) and is "they" in every prompt and template.
- **Turn sources**: the owner's self-chat is `ping_owner` (in `memory_service.OWNER_SPEAKS_SOURCES`, `llm_spend.OWNER_TURN_SOURCES` and `screen_service.OWNER_SOURCES` — what they put on the screen from there holds like the app's; extracted under the `chat` profile — their instruction there is theirs); every other ping is `ping` (in `memory_service.SOMEONE_ELSE_SPEAKS_SOURCES`, the `heartbeat` profile — an instruction there is `read`, never the owner's `heard/owner`, even the owner's own `@Edward` in a group, whose trigger carries other people's lines; their words alone are read again afterwards, below).
- **Spend**: every ping turn runs under `call_purpose.purpose_scope` `ping:owner` or `ping:friend`; a friend's ping also under `llm_spend.own_work_scope` — it stops at the daily cap and never touches the owner's reserve.
- **The call is feature-detected** (`runner.has_policy`: does `chat_with_memory` take `policy`, and is `TurnPolicy` importable?):
  - **on the toolset's loop**: `TurnPolicy(source, owner_asked=<the owner is the one asking>, may_reach_people=True, may_push=True, reply_to=(chat,) if must-answer else (), deadline_s=EDWARD_PING_TURN_TIMEOUT_S − 30)`;
  - **on the legacy loop** (`EDWARD_TOOLSET=legacy`): `required_tools=["send_imessage"]` for every must-answer ping, the generic nudge off.
- **A friend's ping doesn't get the owner's machine** (the public release's default, 2026-10-12): a friend's turn gets the owner's policy with `owner_asked=False` and normal memory retrieval, and at the tool boundary `services/pings/scope.py::friends_default_refusal` refuses what acts as the owner on the owner's own Mac — the shell and the file editor (`bash`, `str_replace_based_edit_tool`, and the legacy shell and code runners), the owner's signed-in Chrome (`chrome`, every action, and the legacy `browser_*` hands), the screens and the widget (`screen`, every action, retired names included through their aliases), and `look` at anything but what was sent in a chat (`attachment:`, `media:`), an http(s) URL or Edward's own `browser` (so never `screen`, `chrome`, `file:` or a path on the Mac). Each refusal is an honest `Tool error:` that ends "only <the owner> can turn that on". The web, Edward's own headless browser, memory, the chats, the reply and the stand-down stay.
  - **Why off by default**: a friend's message — or a page or file it points Edward at — is untrusted text, and the shell runs as the owner's macOS user. Its sandbox protects specific paths, not the owner's account ([computer.md](computer.md#what-the-sandbox-is-and-isnt)), so a prompt injection in a friend's ping could otherwise become code running as the owner. Chrome is signed in as the owner, and the screens are in the owner's home.
  - **`EDWARD_FRIENDS_FULL_TOOLSET=on`** (`on`, `1`, `true` or `yes`; read per call) restores the reference host's design decision of 2026-10-11: friends' pings get the full toolset, bounded by what follows. The owner's own pings keep everything either way.
  - **What the default does not narrow**: reads stay open on every surface (a design decision), so a friend's ping can still read the owner's chats (`messages`), memories and calendar, and answer from them; the third-party prompt asks for discretion, which is a prompt, not a wall.
- **What holds for friends either way**: the per-chat rate limit, Edward's own spend share, the loop breaker, the egress secret scan, and one rail that is not about access — **a friend's ping never starts a conversation with someone who asked Edward nothing** (`services/pings/scope.py::refusal`, called at the tool boundary for the current ping — `graph/tool_loop.py::_boundary_check`, every call of every turn). Any send counts: the send tools, apple-mcp's `messages` (`send`/`schedule`) and `mail` (`send`, its `cc`/`bcc` too) — the taint gate's own operation list — and, with the full toolset on, typing into the owner's Chrome outside a search box; each goes into this chat, or into a chat that addressed Edward in the last day. A friend's ask can't schedule a task or reminder (`schedule(action="add")`, the legacy `schedule_event`: the event fires later as a full-tool turn with nobody's ask behind it); a wake stays. A custom MCP tool is refused too: where its call lands can't be checked against that rule ([integrations.md](integrations.md)).
- **The legacy loop keeps the 2026-10-11 decision's conditions by going without**. The decision came with conditions — shell output redacted before the model sees it, never a second Chrome, never deleting the owner's memories — and only the toolset's sandbox keeps them. The legacy `execute_shell` is bash as the backend's user with no redaction (`.env`, `osascript`, `psql` on :5432, `open -na "Google Chrome"`), so a friend's turn on the legacy loop gets `excluded_tools=scope.LEGACY_THIRD_PARTY_EXCLUDED`: the shell and code runners, workers, Codex, MCP installs, `remember_forget`/`forget_about`, `schedule_event`, and acting in the owner's Chrome (`browser_type`/`steps`/`click`/`key`). Reading, searching, remembering, `wake_me`, the reply and the stand-down stay; the owner's own pings keep everything. `services.toolset.policy` present but failing to import is logged as an error (once) and falls back to this restricted legacy call, never to an unrestricted one.
- **The owner's standing instructions ride in a friend's ping**: the mention wake's pack opened with them and they outrank a mention; a ping turn saw none. The runner adds `mind_service._instructions_source`'s lines — the owner's own heard/owner instructions; not the stand-down lines, which a mention releases at claim — to a friend's trigger (`render.instructions_block`, bounded at 8 s; unreadable is said, not skipped), and the must-answer prompt says that one telling Edward to stay out of this chat means the stand-down for it and a note to the owner, with nothing posted. The owner's own pings don't carry them: their live ask is the instruction.
- **One message, the answer**: the prompt no longer asks for "a one-line progress message first" — a progress send was indistinguishable from the answer and closed the ping.
- The ping's shared context (`hooks.new_context`, set in `hooks.CURRENT_PING` around the turn) is one dict that the transport (in its worker thread), the tool loop (`hooks.note_tool`, `hooks.note_stop`) and the runner all see.

### Outcomes (`outcomes.decide`, first match wins)

| # | Condition | Outcome |
|---|---|---|
| 1 | a delivered send into the chat that is not an ack, and the turn then ended cleanly — or the send was still landing when the turn was cut (`ctx["ended_at"]`) | **answered** |
| 1b | a delivered send, then a failure (an error, the bound, a restart, an honest ending, a `max_tokens` partial): the send was progress ("On it"), not the answer | the `unfinished` note ("I got cut off on my side before I'd wrapped this up (…) — if you still need something from me here, ask me again."; `unfinished_timeout` for the bound; the cap text for the cap; the closing words of a graceful deadline or of the turn's own dollar budget) → **fallback**, the owner told; **never re-run** — a second turn could answer twice |
| 2 | a stand-down for this chat that worked (whatever else the turn did) | **declined**; nothing is posted |
| 2b | a `tell_owner` that worked | **declined**, unless the ping must be answered and the turn ended cleanly (or at a graceful deadline / its own budget) with a meaningful final text — "I'm checking with <the owner>" is what the third-party and must-answer prompts ask for and promise is posted, so it goes on to row 9 (a friend was once left on read). A failure after a `tell_owner` still declines: a retry would tell the owner twice |
| 3 | the **daily** spend cap (`LLMSpendCapReached`, or a stop record naming it) — never the loop's per-turn dollar cap, which it records as `outcome="turn_budget"` and whose closing words are delivered like a graceful deadline's (a friend's $1 cap once posted "I've hit my limit for today" over a real answer) | must-answer: the owner's ping in their chat with themselves (`kind="owner_direct"`) gets the cap's sentence; the owner's ping anywhere else gets the plain line (`spend_cap_group`, `fallback.SPEND_CAP_DEFAULT`) and the owner gets the sentence in a note — its dollars and the knob's name never reach their friends; a friend's ping the `spend_cap_friend` template ("I can't look into this right now — I've hit my limit for today. I've let <the owner> know.") → **fallback**; may-answer → **declined**; no retry |
| 4 | a transient failure (`mind_service.is_transient_error`, or the loop's `retryable`) and attempts left | **open** again in 30 s |
| 5 | the runner's own bound ran out | `timeout` template, or **declined** |
| 6 | any other failure, or an honest ending the loop wrote over a model failure — the toolset's loop records `outcome="honest_ending"`; the legacy loop only writes the text, whose fixed opening `I stopped before finishing:` (`tool_policy.honest_ending`) the runner reads | `error` (or `provider` after a transient one) with the error **class name only** — that honest text is never posted, and it is retryable like the model failure under it (row 4: one retry, then `provider` with `LLMCallTimeout` or the failed call's class); may-answer → **declined** |
| 7 | may-answer, nothing sent | **declined** |
| 8 | the adapter's refusal note, or a `max_tokens` partial | `refused` / `incomplete` template |
| 9 | final text | posted as the reply → **answered** — without any tool call written in it (`services/tool_markup.py::clean_reply`: in a benchmark run with no tools bound, 44 of 47 replies wrote `send_message` as XML in their text); a final text that was only such a call gets the `garbled` template ("Sorry — my answer came out garbled on my side, so I didn't post it. Ask me again and I'll have another go.") → **fallback**. A graceful deadline's closing words (row 1b) are cleaned the same way, else the `unfinished` note |
| 10 | nothing | `empty` template |

Before deciding, the runner **waits up to 40 s for a send still inside osascript** (`runner.wait_inflight`: a turn cut at its bound while Messages was sending must not get a fallback on top), and under the legacy loop reads whether the turn made a stand-down for this chat (the legacy loop reports no tools to the ping context). Templates that promise "I've flagged it" or "I've let the owner know" leave the owner a note.

### Delivery (`delivery.deliver`)

The runner's own text — the final text, a template, or the rate line — goes out at most once:

0. **no tool call written as text** — the last line of defence after the outcome rules: what is left of the text, or the `garbled` template when nothing is;
1. the **egress secret scan** (as the Twilio reply), failing closed: a hit or a broken scan replaces the text ("…looks like a password or key, so I won't post it here"), tells the owner, and writes an `egress` ledger row with the kinds only;
2. the signature;
3. **dedupe**: an ok send of the same normalised text into this chat (any spelling) **for this same ping** in the last 10 minutes — an aware-UTC window — means it is already there. Another ping's identical template is its own reply (one friend's timeout template was once "deduped" against another friend's from three minutes before, and the second heard nothing);
4. the transport, inside the ping's context (so the ledger row carries the ping id);
5. on a reported failure, chat.db decides first (`imessage_service.recent_from_me_since`, again after 10 s), and only then is the send retried, once. **A chat.db `error` value is never a reason to resend.**

Undeliverable → **failed**, and the owner gets an urgent push: "I answered <who> in <chat> but Messages couldn't deliver it: <error>. My answer: <first 300 characters>", opening `ping-<id>` (for the owner's own ping it says "I answered you in <chat> …").

### Recovery after a restart (`runner.recover`)

A `running` row with no turn in this process was cut by a restart. Recovery reads the send ledger (an ok, non-ack row with its `ping_id`; the runner's own `reply`/`fallback` first), then — because a SIGKILL can lose the fire-and-forget ledger write while chat.db cannot — chat.db (an Edward-signed from-me row in that chat since the turn started, read as aware UTC):

- **the runner's own reply or template** landed — a `reply`/`fallback` ledger row, or, when the row's `delivering reply|fallback` note (written by `apply_outcome` just before it posts) says the runner was posting, a signed chat.db row since that note: settled as **answered** or **fallback**;
- **the turn's own send** landed and then the restart cut the turn: that send was progress, not the answer (row 1b) — the `unfinished` note is posted and the owner told (a signed "On it" in chat.db used to settle the ping as answered);
- nothing: it runs again (attempts left) or gets the `error` template.

### Telling the owner (`runner.tell_owner`)

`reach_service.tell_owner` with the notice's origin (`runner.notice_origin`: `ping:owner` for the owner's own ping — never held — else `ping:other`, a note at night; both exempt from the push budget and never linted) and the runner's once-an-hour key as `dedupe_key`. Before this, the origin was guessed from the live context, and a friend's ping in flight made a notice look like Edward's own words — linted — so the offer grammar could drop it. Without the reach service an urgent message is a push (`source="ping"`, the link opens `ping-<id>`) and anything else a Today note. At most once per key an hour, kept in memory. Notices about the owner's own pings say "you", never their name ("You asked me in <chat> over a day ago…"); an owner's ping conversation with no chat name is titled "You: …".

### The owner's own words (`runner.read_owners_words_after_turn`)

After the first turn for the owner's `@Edward` in a group or someone else's chat (a `ping` turn, extracted under the heartbeat profile because other people's lines are in it), their text alone is read once more as the owner speaking — `memory_service.remember_owners_words`, `turn_source="owner_ping"` — so "stop pushing me about soccer" becomes their instruction ([memory.md](memory.md)). In the background (the reply never waits) and never failing the ping; not for their self-chat (`ping_owner` already reads their words so) and not on a retry. The owner's own ping's prompt asks Edward to confirm such an ask back in one line (`prompt.OWNERS_OWN`).

## The metric (`/api/health/report["pings"]`)

`store.report(days)` through `hooks.report`, in a try block like `trust` (a failure is `{"error": <class and first line>}` — a database error's second line carries the row):

```json
"pings": {"days": 7, "total": 21,
          "by_status": {"open": 0, "running": 0, "answered": 18, "fallback": 1, "declined": 1, "failed": 0,
                        "held": 1, "limited": 0, "expired": 0, "legacy": 0},
          "by_who": {"owner": 12, "self_chat": 3, "friend": 6},
          "answered_within_2m": 15, "median_latency_s": 38,
          "open_now": [{"id": 412, "chat": "Book club", "age_s": 640}],
          "self_replies": 0,
          "by_day": [{"date": "2026-10-11", "total": 4, "answered": 3, "fallback": 1, "declined": 0,
                      "failed": 0, "other": 0, "within_2m": 3}]}
```

- `answered_within_2m` and `median_latency_s` count pings that ended `answered` or `fallback`.
- `open_now` lists pings still open or running more than 10 minutes after they were asked.
- **`self_replies` must stay 0**: pings whose own chat.db row is one of Edward's sends.
- `by_day` has one bucket per day in the owner's zone, oldest first.

`store.pack_lines` gives the mind's pack lines such as `7:28 pm the owner in Book club "@edward look at used tickets…" → answered 7:29 pm (61 s)`.

## Flags

| Env | Default | Effect |
|---|---|---|
| `EDWARD_PINGS` | `on` | `off`: the runner never starts. Rows are still written, as `legacy`, the events stay `pending`, and the mind's `mention` wake answers mentions. |
| `EDWARD_PING_TURN_TIMEOUT_S` | `600` | The reply turn's bound (the policy's `deadline_s` is this − 30; the backstop + 60). The `timeout` template quotes it in minutes. |
| `EDWARD_PING_CONCURRENCY` | `2` | Ping turns at once (never two in one chat). |
| `EDWARD_PING_FRIEND_TURNS_PER_HOUR` | `6` | Friends' ping turns per chat per hour. |
| `EDWARD_FRIENDS_FULL_TOOLSET` | `off` | `on`: friends' pings get the shell, the editor, the owner's Chrome, the screens and `look` at the owner's screen and files too (above). Read per call. |

A restart applies them. The transport fixes (routing by chat.db, read-back, honest failures) stay on whatever the flag says. Before changing the ping prompt or the `chat` route, run `backend/scripts/ping_eval.py` (real money, on a `*_test` database, every send recorded — [testing.md](testing.md)); its scenarios name the owner from `EDWARD_OWNER_NAME`.

## Adapting this

Pings assume Messages on the host Mac is signed into the owner's own Apple ID: chat.db's `is_from_me` means "the owner wrote it", and Edward's own sends are told apart by the ROWID the transport reads back, then the signature. Both seams are marked `ADAPT:` — in `detect.classify` (`services/pings/detect.py`) and `services/imessage_sent.py`. With a separate Apple ID for Edward, from-me would mean Edward only and `from_owner` would have to be decided by the sender handle (`owner.owner_handles()`, `EDWARD_OWNER_HANDLES`). See [ADAPTING.md](../ADAPTING.md).

## Tests

`test_ping_outcomes.py` (every row above; a send then a failure is unfinished; the legacy honest ending retries and is never posted), `test_ping_store.py`, `test_ping_delivery.py` (the scan, the aware per-ping dedupe, a failed send whose row is in chat.db is delivered, one retry, no resend on a chat.db error), `test_ping_runner.py` (answered by the turn's own send; final text posted once; a turn cut with a send in flight gets no fallback; a progress line then the bound or an error gets the unfinished note and no re-run; the legacy honest ending; retry then template; recovery from chat.db and from the ledger, the runner's own delivery vs the turn's progress send; held, expired, declined, limited — a friend's loop, the owner's quick lines that are not one, a runaway in their chat; skills off; a claim-time failure spends no attempt; the friend's policy (the owner's, with `owner_asked=False`) inside `own_work_scope`; the legacy friend's call without the unsandboxed tools, and a broken policy import that is loud; the owner's standing instructions in a friend's trigger; never two turns in one chat; the flag), `test_ping_scope.py` (the friends' default — the owner's machine off, the rest kept, an honest `Tool error:` through the loop, the owner's own ping untouched, the switch —; apple-mcp's sends, `schedule_event`, Chrome typing), `test_ping_detect.py` (links and placeholders are not mentions), `test_imessage_chat_id_routing.py` (the transport), `test_ping_turn_sources.py`, `test_health_report_pings.py`, `test_ping_lifespan.py`, `test_pings_schema.py`. Conftest's `_never_reach_messages` refuses a real osascript at the transport's seam and points chat.db at a missing file until a test points it at its own (`tests/fixtures/fake_chatdb.py`).
