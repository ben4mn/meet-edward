# Edward design system — "calm presence" (2026-09-25)

The brief: an assistant that feels elegant, refined, a little futuristic and
above all *trustworthy*, with Edward himself cute and full of personality but
minimal. This document is the contract every screen follows. It comes out of a
full UX audit (fixture harness, iPhone 390×844 dark, desktop 1440) and a study of
presence-led assistant designs.

**Status (2026-09-26): implemented** across the PWA, `/screen` and the hub
(three phases plus a review pass). Where the build refined the first draft
this document now says what the code does; §8 maps each part to its files
and tests. `CLAUDE.md` → "Design and presence" is the
operator's summary, and `docs/architecture/design-and-presence.md` the long form. Check designs against the fixture harness
(`frontend/scripts/mock-api/`, `http://localhost:3100`), never the live PWA.

## 1. Principles

1. **Edward is the status bar.** One live Edward on every screen, with one plain
   line saying what he is doing. Tapping him opens what he did and is doing.
2. **The animation is a claim, so it must be true.** Every mood comes from a real
   signal (a streaming turn, a running wake, a waiting approval, a dead link).
   Never "working" when nothing runs; never "fine" when his hands are down.
3. **Emerald means Edward.** The accent is reserved for Edward's presence and
   Edward's acts (his light, his "did" rows, his thinking). The owner's own primary
   buttons are a high-contrast neutral (ink on light, light on ink). Status
   colours (ok/warn/bad/info) never borrow emerald.
4. **Calm by default, alive on purpose.** Only one thing loops on a screen (his
   breath). Everything else moves once, for a reason, in under a second.
5. **Show the work, then fold it into a receipt.** Thinking and tool use stay
   visible (a firm design decision) — beautiful while live, one quiet line when done.
6. **Glass, light and restraint — not HUD clutter.** Deep ink canvas, hairlines,
   soft depth, one accent. Warmth comes from Edward's cream shell and his eyes.
7. **Thumb first.** 44pt targets, whole cards tappable, nothing destructive in
   one tap, undo instead of confirm where possible.

## 2. Tokens

All colours are CSS variables with an `-rgb` channel twin so Tailwind alpha
modifiers work (`bg-terminal/20`). Tailwind maps `name: "rgb(var(--name-rgb) / <alpha-value>)"`.
Existing token *names* are kept (tests pin them); their values change.
Why: the colours used to be `var(--x)` strings, and every alpha class (128
uses in 35 files) compiled to nothing.

Translucent tokens — `--border`, the `-soft` fills, `--glow`, `--input-border`
— also carry their resting alpha (`--border-alpha`, `--soft-alpha`, …), and
`/NN` *scales* it (`scaled` in `tailwind.config.ts`): `border-border/50` is half
a hairline, not a 50%-white line. Bare `border` / `divide` draw the hairline.
The raw `var(--x)` values stay for CSS that reads them directly (the pane
section, login). `:root` is light; `.dark` (the default, set on `<html>`
before paint) is dark. Nothing animates colour globally: a theme switch adds
`html.theme-transition` for ~300 ms, and there is no `[class*="bg-"]`
transition rule (it overrode every transition utility).

### Dark (default)
| token | value | use |
|---|---|---|
| `--primary-bg` (canvas) | `#0a0e14` | app background |
| `--surface` (surface-1) | `#121821` | cards, rows |
| `--surface-elevated` (surface-2) | `#18202b` | raised: sheets, popovers, selected |
| `--surface-3` | `#1f2835` | pressed / inset / chips |
| `--border` | `rgba(255,255,255,.08)` | hairlines. `border-border` must render this hairline by default and `border-border/50` must still compile (e.g. a Tailwind colour function over `--border-rgb` + `--border-alpha`) |
| `--text-primary` | `#e8edf4` | |
| `--text-secondary` | `#a9b4c3` | |
| `--text-muted` | `#8592a4` | ≥4.5:1 on every surface, surface-3 included (`__tests__/tokenContrast.test.ts`) |
| `--terminal` (edward) | `#3ecf9e` | Edward's accent |
| `--terminal-ink` | `#03140e` | text on an emerald fill (9.6:1) |
| `--terminal-strong` | `#6fe0b9` | accent text on dark |
| `--terminal-soft` | `rgba(62,207,158,.12)` | |
| `--glow` | `rgba(62,207,158,.35)` | presence glow only |
| `--ok` `--warn` `--bad` `--info` | `#7fd18b` `#f2c35b` `#f07a52` `#8fb4ff` | + `-soft` at .14 |

### Light
canvas `#f5f6f8`, surface `#ffffff`, surface-elevated `#ffffff` (lifted by shadow),
surface-3 `#eef1f4`, border `rgba(13,20,31,.10)`, text `#0d141f / #3b4758 / #5c6878`,
terminal `#047857` (white on it 5.5:1), terminal-ink `#ffffff`, terminal-strong `#047857`,
ok `#276c2c`, warn `#8f5500`, bad `#b23514`, info `#2459c4` (each ≥4.5:1 on its own `-soft` pill; pinned by `__tests__/tokenContrast.test.ts`), `-soft` at .12, terminal-soft .10, glow .30.

### Type (Inter, `cv11` `ss01`; tabular numbers on data)
| name | size/leading | weight | use |
|---|---|---|---|
| `text-overline` | 11/14, +0.08em, uppercase | 600 | section labels |
| `text-caption` | 12/16 | 500 | meta, pills |
| `text-footnote` | 13/18 | 400 | secondary lines |
| `text-body` | 15/22 | 400 | the default on phone |
| `text-callout` | 15/22 | 600 | row titles |
| `text-title3` | 17/22 | 600 | card/sheet titles, Edward's line |
| `text-title2` | 20/26 | 600, -0.01em | |
| `text-title1` | 28/32 | 650, -0.02em | the one H1 on Today / Chat / Edward |
| `text-display` | 34/38 | 650, -0.025em | login, empty states |
Inputs are never under 16px (iOS zoom). Mono (JetBrains) is for data and code only —
**not** for Edward's voice.

### Radius · elevation · motion
- Radii: `xs 6` chips · `sm 8` · `md 12` buttons/inputs · `lg 16` cards · `xl 22` sheets, composer, hero · `full`. Concentric rule: inner = outer − padding.
- Elevation (dark): e1 `inset 0 1px 0 rgba(255,255,255,.04), 0 1px 2px rgba(0,0,0,.35)`; e2 `0 0 0 1px var(--border), 0 12px 32px -8px rgba(0,0,0,.6)`; e3 sheets `0 -24px 48px -12px rgba(0,0,0,.7)`. Light: e1 `0 1px 2px rgba(13,20,31,.05), 0 2px 8px -2px rgba(13,20,31,.06)`; e2 `0 8px 24px -6px rgba(13,20,31,.14)`; e3 `0 -12px 40px -12px rgba(13,20,31,.18)`.
- `shadow-glow`: `0 0 0 1px rgba(62,207,158,.35), 0 0 24px -4px rgba(62,207,158,.45)` — only while Edward is thinking, listening or speaking.
- Motion: press 90ms · fast 160ms · base 240ms · slow 360ms; exits 0.7×. Curves: `--ease-out cubic-bezier(.22,1,.36,1)` (enters), `--ease-in-out cubic-bezier(.65,0,.35,1)` (moves), `--ease-in cubic-bezier(.4,0,1,1)` (exits). framer-motion springs: sheets/drawers `{stiffness:420, damping:36}`, dock pill `{stiffness:600, damping:40}`. `prefers-reduced-motion`: ≤120ms opacity fades only, no loops. Tailwind: `duration-press|fast|base|slow`, `ease-out|in-out|in`; framer: `SPRING_SHEET`, `SPRING_PILL`, `REDUCED_FADE`, `DURATION` in `components/ui/motion.ts`. Every `LazyMotion` loads `MOTION_FEATURES` (`components/ui/motionFeatures.ts`, framer's full `domMax` set): `domAnimation` has no drag or layout, so sheets could not be swiped and the dock pill snapped.

## 3. Recipes (components/ui)
- `Button` variants: `primary` (neutral high contrast: `bg-text-primary text-primary-bg`), `edward` (emerald fill + `terminal-ink` — Edward-initiated actions such as "Try it", "Wake now"), `secondary` (surface-3), `ghost`, `danger`. Heights: `md` 44 on touch / 36 on md+; `sm` 36 drawn with a 44 hit area (`::after`), 32 on md+; `icon` 44 / 36. Every pressable: `active:scale-[.97]` over `press` duration.
- `focus-visible`: 2px emerald ring at 60% + 2px canvas offset, keyboard only.
- `Card` (surface, hairline, lg radius, e1), `Row` (whole row tappable, 44+ tall, wraps to two lines, chevron only for links, rotating chevron for expanders), `Pill`, `Segment` (44 on touch — callers never squeeze it back to 36), `SectionLabel` (overline + padded action hit area), `EmptyState`, `Sheet` (framer spring, drag to dismiss on touch, Esc, focus trap/return, body scroll lock; a bottom sheet on the phone, a dialog on md+), `Switch` (spring knob), `Field` (label + hint + tokenised `Input` / `Textarea` / `Select`, `CONTROL`), `Notice` (ok/bad/info/warn inline), `ConfirmSheet`, `Meter`, `Skeleton`, `Toast` with Undo (4.5 s, 7 s with Undo, at most 3, above the dock), `PanelShell` (a settings panel: breadcrumb to its room, `title1`, one line).
- Loading is Edward, not a spinner: `Pulse` (exported as `EdwardPulse` too — a breathing emerald pip) replaces `Loader2 animate-spin` in panels; `AuthGuard`'s wait is a still Edward with it.
- Class merging is `cn` from `components/ui/cn.ts` (`lib/utils` re-exports it): `tailwind-merge` taught the type scale, `shadow-e1…e3/glow` and the duration tokens. Stock twMerge reads `text-body` as a colour, so `cn("text-body", "text-text-primary")` silently dropped the size.
- Pages that must stay light (`/login`, `AuthGuard`, `/hub`, `/screen`) import primitives by leaf path, never the `components/ui` barrel: it re-exports `Sheet` and `Switch`, which pull in framer-motion.

## 4. Edward (the character)

Same robot, the icon's palette: cream shell `#e9e4d8` with a `#c9c3b4` underside,
visor `#20262f`, pill eyes in his accent with a glint, a chest light. Geometry pins
stay (viewBox `0 0 36 38`, foot pads bottom at y=34, antenna path `M18 6.5 Q18 3 17 1.5`,
every class hub.css drives). Light mode adds a faint outline so he never vanishes.

**Moods** (a state, `data-mood`): `idle` · `think` · `work` (tools) · `listen` · `speak` ·
`attention` (wants the owner: unread thought / held approval) · `sleepy`
(23:00–06:00 idle) · `resting` (the day's budget or the quota is spent: amber tip, honest not alarming) · `offline`.
Priority: offline > speak > listen > think/work > attention > resting > sleepy > idle
(`deriveMood`). Before the first answer about his mind (no `/api/mind/now` yet and no
cached one under ten minutes old) he is plain idle with an empty line — no claim, never
a "Resting" guess while an approval may be waiting. "He acted" is a beat, not a mood.

**Beats** (one-shot, `data-beat`, ≤1s, throttled ≥1.2s apart): `nod` (happy arcs — a tool
succeeded, a turn finished), `wince` (> < eyes, antenna droop — an error), `wave` (first
open of the day, greeting), `acted` (antenna sonar + chest light — he reached outward:
a send, a push, a note; or a wake that acted), `perk` (the app returned to foreground
after ≥15s away). Of two held behind the throttle the one that matters more plays:
acted > wince > wave > nod > perk. A beat is never replayed on mount or when a hidden
avatar comes back.

**Life** (no React re-renders; DOM attributes from `useEdwardLife`): blink at random
2.2–6s, 15% double blinks, 12% long stares; occasional idle glance (left/right/down)
every 8–20s; glance down toward new content; pause everything while the tab is hidden.
**Breath**: only the one live Edward per screen breathes (5.2s, rise faster than fall,
≤0.4 viewBox units). All others are still until a beat.

**The antenna tip is his trust light**: steady accent at rest, slow pulse thinking,
shimmer working, amber waiting on the owner / resting, one distinct sonar ping when he acts
outward, grey offline.

**Placements**: header/presence bar (the live one, bust crop) · dock Edward tab (static
bust + unread pip) · Today presence hero · chat empty state (hero, wave once a day,
`listen` while the owner types) · work rail live header (16px bust mirroring the phase: nod per
tool ok, wince per error) · voice view (mood from phase; `--level` mic ring, `--mouth`
from TTS level) · Edward tab hero · login (looks away while the password is typed,
peeks when shown, wince on a wrong password, nod on success) · /screen and hub (same
component, their existing state feeds).

**Which one is live**: the presence bar (phone) or header (desktop) by default. A page
hero — Today's, chat's empty state, the Edward tab's, the voice view — registers itself
(`lib/presenceHero.tsx`); while it is at least 40% on screen the bar fades its Edward
and status out and the hero is the one who breathes, like an iOS large title collapsing
into the navigation bar. The presence sheet has its own live Edward, so nothing else
breathes while it is open.

Every mood has a one-line honest status for the presence line (`statusLine`) and a
longer `aria-label` (`statusLabel`, "Edward · …"): "Resting", "Thinking · free time",
"Working · 3 tools" (tools used this turn, not just the one running), "Wants you · 1 new
thought", "Waiting on your approval", "Resting until Tue 3:00 pm" (label: "… (today's
budget spent)"), "Offline". Raw wake reasons never show (`REASON_PHRASES`).
When his hands in Chrome are down, a resting or waiting line says so ("Waiting on your
approval · Chrome link down"); a busy line keeps what he is doing and the amber tip
(`data-hands="down"`) plus the aria-label carry it. Every clock reads "7:34 pm".

## 5. Layout
- **Phone shell**: a slim translucent presence bar (48px + safe-top) — live Edward bust +
  name/status line; a contextual action on the right (chat: history, new). Theme and
  sign-out live in the Edward tab ("You"), sign-out behind a confirm. The dock is a native
  tab bar: `max(env(safe-area-inset-bottom), 8px)`, animated active pill, labels 10–11px;
  the Edward tab is his still bust with an emerald pip while he wants the owner; the tab you
  are on scrolls to the top, pops back to its root, or (Chat) starts a new chat.
  Only the layout owns the bottom inset (never composer + dock both): the shell sets
  `--shell-inset-bottom` (0 while the dock shows, the home indicator while it folds away
  for typing) for the composer, and the dock publishes `--dock-h` for toasts and the
  notification prompt.
- **Desktop (md+)**: full-bleed header: presence (avatar + status) left, `Today · Chat ·
  Edward` segmented tabs from `NAV_ITEMS` centre, avatar menu (theme, screen, sign out)
  right. Reading columns: chat transcript and composer share `max-w-3xl`; Today goes
  two-pane at lg (needs left, context right); settings is a persistent two-pane (rooms rail
  + content) on every settings route. `⌘K` palette, `/` focuses the composer, `Esc` stops
  or closes, `⌘⇧O` new chat, `⌘\` sidebar, `g t` / `g c` / `g e` to a tab (`lib/hotkeys.ts`;
  while typing only Esc, ⌘K and the modifier combos count, while a dialog is open only Esc
  and ⌘K). On Today: `j` / `k` move between the cards that need the owner, `e` runs the focused
  card's primary action, `x` its Done — never on a held send, which is always a tap.
- iOS polish: no tap flash, `overscroll-behavior` contained, `color-scheme`, hover only
  where hover exists, `theme-color` = canvas per theme (updated on toggle), thin scrollbars.

## 6. Voice and copy
Edward speaks in first person, briefly, in sentence case ("I'm looking at Parker's thread").
Section labels speak to the owner ("For you", "What I did", "Your goals"). No internal
vocabulary in the UI (no "triage", "heartbeat", "escalated", "pulse" on owner-facing
surfaces except in diagnostics). Errors say what happened and what to do, in Edward's
voice ("I can't reach the Mac right now — trying again").

## 7. Guardrails
- Colour classes must resolve to a Tailwind colour key; no raw `zinc|gray|slate-\d`
  (`__tests__/colorTokens.test.ts`, which compiles the real Tailwind).
- The compiled CSS must contain alpha-modified token classes (catches the alpha bug;
  same test, plus `tailwindTokens.test.ts` for the keys).
- No `[class*=` selectors in global CSS (`colorTokens.test.ts`, `paneCss.test.ts`).
- Text ≥ 4.5:1 on every surface and its own soft pill (`tokenContrast.test.ts`); 44pt
  targets on touch (`touchTargets.test.ts`).
- One `.is-live` Edward per route; nothing loops inside a scrolling list; only
  transform, opacity and fill animate in the pane rig and every beat ends within
  `BEAT_MS` (`paneCss.test.ts`).
- `next build` lints and a lint error sends production back to `next dev`:
  `buildLint.test.ts` lints what the build lints.
- The Edward tab's first load never statically imports the chat's markdown renderer
  (`settingsBundle.test.ts`).
- Every change starts with a failing test (red twice, then green).

## 8. Where it lives

| Part | Code | Tests (`frontend/__tests__/`) |
|---|---|---|
| Tokens | `app/globals.css` (top), `tailwind.config.ts` | `colorTokens`, `tailwindTokens`, `tokenContrast` |
| Primitives | `components/ui/*` (`cn.ts`, `motion.ts`, `motionFeatures.ts`) | `ui`, `utilsCn`, `motionFeatures`, `touchTargets` |
| Edward | `components/pane/PaneSvg.tsx`, `components/EdwardAvatar.tsx`, `lib/edwardMood.ts`, `lib/useEdwardLife.ts`, the "Pane buddy" section of `app/globals.css` | `paneSvg`, `paneCss`, `paneTransformOrigin`, `edwardMood`, `useEdwardLife`, `edwardAvatar` |
| Presence | `lib/presence.tsx`, `lib/presenceModel.ts`, `lib/mindNowApi.ts` (backend `GET /api/mind/now`, `services/wake_state.py`), `lib/presenceHero.tsx` | `presence`, `presenceModel`, `mindNowApi`, `presenceHeroScroll` |
| Shell | `components/layout/*` (`ClientLayout`, `PresenceBar`, `DesktopHeader`, `PresenceSheet`, `CommandPalette`, `KeyboardLayer`, `AvatarMenu`), `components/chat/ThumbDock.tsx`, `lib/chrome.ts`, `lib/hotkeys.ts` | `shell`, `shellBeats`, `shellPages`, `clientLayout`, `presenceSheetModel`, `commandPaletteModel`, `hotkeys`, `chrome` |
| Login | `app/(auth)/login/page.tsx`, `lib/loginFlow.ts` | `loginFlow`, `authResilience` |
| Today | `components/today/*` (hero `heroModel.ts`, keys `todayKeys.ts`, cache `todayFeedState.ts`) | `todayModel`, `todayHero`, `todayKeys`, `todayFeedState`, `todayView` |
| Chat | `lib/turnModel.ts`, `lib/workRail.ts`, `lib/toolLabels.ts`, `lib/scrollStick.ts`, `components/chat/*` | `turnModel`, `workRail`, `workRailView`, `toolLabels`, `scrollStick`, `messageBubble`, `chatEmptyState` |
| Voice | `components/chat/ContinuousVoiceView.tsx`, `lib/voice/voicePresence.ts` | `voicePresence`, `voiceRoute` |
| Edward tab | `components/settings/*` (`settingsRooms.ts`, `settingsIndex.ts`, `SettingsDashboard`, `EdwardHero`, `SettingsSidebar`), `components/ui/PanelShell.tsx` | `settingsRooms`, `settingsIndex`, `settingsLanding`, `settingsLayout`, `settingsDeepLinks` |
| /screen (the stage, 2026-09-28), hub | `app/(display)/display/components/Stage.tsx` (`StagePiece.tsx`, `StageEdward.tsx`, `SpeechLane.tsx`, `ThoughtLane.tsx` — his live thinking and, since round 2, `MindLane`), `display/components/ComposeReply.tsx` (the reply card), `display/lib/stageModel.ts`, `stageLife.ts`, `thinkingModel.ts`, `display/hooks/useSpeakAloud.ts`, `display/hooks/useMindDrift.ts`, `display.css` (the stage section), `display/lib/screenPresence.ts`, `sharedPoll.ts`, `hub/components/GreetingEdward.tsx`, `hub/lib/walkEngine.ts` | `stageModel`, `stageLife`, `thinkingModel`, `stageEdward`, `stage`, `screenLayout`, `stageCss`, `useSpeakAloud`, `stageSettings`, `composeReply`, `composeReplyCard`, `screenPresence`, `sharedPoll`, `hubGreetingEdward`, `walkGrounded` |
