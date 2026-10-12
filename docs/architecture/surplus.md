# The surplus queue (retired 2026-10-11)

How Edward used to spend model quota nobody else was using, when he ran on the Codex fallback's weekly window. Retired by the proactivity rebuild; its code is not part of this release. This page keeps the history, what replaced each job, and the links from other docs.

## Why it went

- **Nothing to spend.** On the Claude API (`EDWARD_LLM_PROVIDER=anthropic`, the default since this release) every token is paid for and nothing expires at a weekly reset. The surplus decision already refused every tick there (code `pay_per_token`), so the queue had become dead code that the scheduler still woke every 10 minutes.
- **Its outputs went unread.** An audit of the reference install (2026-10-10) found that free time, research and the nightly `reflect_lite` produced notes and documents nobody opened. Meanwhile the jobs kept the pulse on a 30-minute floor and kept a free-time thread busy on the stage.
- **Proactivity is noticing, not a schedule.** Edward now acts when something happened, not when quota is spare ([mind.md](mind.md), [noticing.md](noticing.md)).

## What replaced each job

| Job | Now |
|---|---|
| `reflect_lite` (01–05 local) and the Sunday `reflect` tick | The silent 03:00 `nightly` wake, with the week's reflection and decay on Sundays ([scheduler.md](scheduler.md)) |
| `contradictions` | The nightly runs contradiction triage (`ask_via="note"`, at most one question) |
| `research`, `meeting_prep` | A follow-up Edward schedules for himself (`wake_me` → a `self` wake) |
| `free_time` | One budgeted `free` wake a day at 13:00 (design decision, 2026-10-11: one run a day for his stage on the host Mac's screen and his own projects — no push, no send), ≤ ≈ $0.50 a run (rounds stop at $0.30, ≤ 8K output tokens a call), reaching nobody ([mind.md](mind.md)); otherwise the stage changes when something changes ([screen-and-browser.md](screen-and-browser.md)) |
| `consolidation_deep`, `dossiers`, `summaries` | Nothing on a timer. Hourly consolidation runs its base budget |

## What went with it

- The modules: `services/surplus_service.py`, `surplus_jobs.py` and their tests.
- The `surplus_tick` seed. It is in `RETIRED_CRON_EVENTS` (`services/scheduler_service.py`), so a row left by an older install is cancelled at boot, and a leftover row that fires is cancelled, never run.
- The pulse's surplus floor.
- The usage card's `surplus` and `spend_top` blocks.
- The health report's `surplus` section.

What stays:
- `ledger_events` rows of kind `surplus` from an older install stay in history until the 30-day retention removes them.
- `today_service.PLUMBING_EVENT_TYPES` keeps `surplus_tick`, so an old row never shows on Today.
- `llm_rate_gate.surplus_decision` and `priority="surplus"` still exist on the Codex fallback (refused under Claude); they go with the rate gate's next cleanup.
