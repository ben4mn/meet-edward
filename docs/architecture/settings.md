# The Edward tab (settings)

`/settings`: "Edward now", the five rooms, every panel, the desktop two-pane layout and deep links.

## The Edward tab

- **The Edward tab** (`app/(app)/settings/*`, `components/settings/`; rebuilt 2026-09-26). `/settings` is **"Edward now"** (`SettingsDashboard`): `EdwardHero` (him, the live Edward while in view, his status and the opening of his `Edward: state` page), then Knows / Watches / Does, each opening a room, the five rooms once with their health and one live line, `YouCard` (appearance, sign out) and All settings. Every line is decided in `edwardHomeModel.ts` from real signals (`useEdwardTabStatus`: mind/now plus the state doc), never from a guess.
- **Rooms** (`settingsRooms.ts`). Every slug lives in exactly one room and a test pins the full list, so adding a panel means adding it to `ROOM_SLUGS` **and** `settingsIndex.ts`:
  - Mind: `thoughts journal model skills mcp`
  - Memory: `memories documents files databases dreaming`
  - Attention: `heartbeat notifications proactive gmail alexa`
  - Work: `events orchestrator wishlist usage`
  - House: `locale hub-photos`

  Moved on 2026-09-26: journal and mcp to Mind (what he thinks, what he can do), usage to Work ("Fuel"), locale to House.
- **Titles speak to the owner, not to the code**: `heartbeat` is **Listening**, `orchestrator` **Workers**, `mcp` **Servers**. The old and technical names stay searchable (`settingsIndex.ts` `keywords`, plus the pre-redesign category) and are never shown.
- `settingsIndex.ts` is the render-free index the rail, the landing, the rooms and the A–Z page read. Only the panel page imports `settingsConfig.tsx`, which adds the render; that keeps the markdown renderer out of the tab's first load (`settingsBundle.test.ts` guards it).
- Every panel sits in `PanelShell` (a breadcrumb back to its room); a room page shows its panels and its live view (the routes table in CLAUDE.md).
- **Attention's "Waiting on you"** (`HeldSends`) answers a held send in place, Send / Don't send, so every approval path ends in a button. It used to link to a thread that had none (`heldSends.test.tsx`). A held Codex session shows its whole task with Start / Don't start ([trust-boundary.md](trust-boundary.md)).
- **Desktop two-pane**: `settings/layout.tsx` is a height-bounded row on md+: the rooms rail (`SettingsSidebar` / `SettingsNavList`: an A–Z finder, each room with its health dot and panels, the current page in a neutral fill) and the page, each scrolling on its own. The rail used to scroll away with the content. Phones go landing → room → panel.
- **Deep links** (`lib/deepLink.ts`): `?id=` opens an item in Memories, Documents, Files, Events, Workers and Wishlist; `/settings/dreaming?flag=<id>[&ask=rewrite]` opens a memory flag (`lib/memoryFlags.ts`; a flag past the newest page is fetched by `GET /api/consolidation/flags/{id}`); `/settings/heartbeat?view=log|config`. The parameter is stripped with `history.replaceState` on close, so a refresh does not reopen it.
- Status tokens `ok/warn/bad/info` (and their `-soft` fills) are separate from the `terminal` accent on purpose: a status never borrows Edward's colour ([design-and-presence.md](design-and-presence.md)).
- **Fuel** (the Work room's `CodexUsageCard`): today's Claude spend against the daily cap (or, on the Codex fallback, the week's meter), and the top eight purposes from `GET /api/settings/codex-usage` → `spend_top`, each in Edward's words ("my wakes", "our chats", "deep memory tidy", "talking through Alexa"; `lib/codexUsage.ts::spenderLabel`). Purposes that read the same are merged into one row. Until 2026-10-11 it also showed the surplus queue's state, which is retired ([llm-and-quota.md](llm-and-quota.md), [surplus.md](surplus.md)).
- Every time on these pages reads "7:34 pm" (`lib/time`). The sidebar, Listening log, Skills, Usage, Memories and Files used Intl's "9:00 AM" until 2026-09-27.

Tests: `settingsRooms.test.ts`, `settingsIndex.test.ts`, `settingsLanding.test.tsx`, `settingsLayout.test.tsx`, `settingsPages.test.tsx`, `settingsDeepLinks.test.tsx`, `settingsNotFound.test.tsx`, `deepLink.test.ts`, `memoryFlags.test.ts`, `edwardHome.test.ts`.
