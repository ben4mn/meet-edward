# EdwardBar: a menu-bar companion

EdwardBar puts Edward in the menu bar of any Mac you use, whether or not it is the Mac that runs Edward. It tells Edward that you are at this Mac and whether you are active, idle or locked, and it shows what Edward knows about where you are.

What it does, and nothing more:

- **Every 30 s, a presence beacon** (`POST /api/presence/beacon`). The beacon carries this Mac's name, the seconds since your last input, and `active`, `idle` (after 2 minutes without input) or `locked`.
- **A live stream** (`GET /api/presence/stream`) that keeps the menu's first line current: "You're here · <surface>" or "You're away". The icon shows whether the stream is connected.
- **Open Edward** opens the chat in your browser.
- **Share frontmost app with Edward** is **off** by default. When it is on, the beacon also carries the name of the app in front, and nothing about what is in its windows.

The app also understands two older frames that the server no longer sends. "Edward wants a word" (a speech bubble, cleared with a click that acknowledges it through `POST /api/push/ack`) went with the interrupt ladder on 2026-10-11. Edward now reaches you through notes on Today and pushes. "Watching" (an eye) went with the old triage engine, and the stream always reports 0 for it.

## Build

The Command Line Tools are enough; you don't need Xcode. It needs macOS 13 or later.

```bash
cd companion/EdwardBar
swift build -c release
swift test                       # the pure logic: presence rules, SSE parsing, icon states
mkdir -p ~/bin && cp .build/release/EdwardBar ~/bin/
```

## Configure

EdwardBar reads `~/.config/edward/presence.env`, or the file named in `EDWARD_PRESENCE_ENV`. It shares that file with the shell-only beacon, `companion/scripts/edward-presence.sh`:

```bash
EDWARD_URL=https://edward.example.com
EDWARD_TOKEN=<your widget token>
```

`EDWARD_URL` is where you open Edward (`EDWARD_PUBLIC_BASE_URL` on the host). Keep each line plain `KEY=value`, with no comment after the value, because EdwardBar reads the file line by line rather than through a shell.

The token is Edward's widget token. Treat it like your password: besides the widget and presence, it can chat with Edward as you (`POST /api/widget/chat` runs a turn with your full trust, so it can read what Edward reads and send messages from your accounts). Keep it private, put it only on Macs you own, and keep `presence.env` readable by you alone (`chmod 600 ~/.config/edward/presence.env`). While signed in to Edward in a browser, open `<EDWARD_URL>/api/widget/token` to see it. If it leaks, `POST /api/widget/token/regenerate` replaces it at once, after which you update the file on every Mac that uses it. On a Mac at home, a LAN address works too (`http://my-mac.local:3000`).

Without the file, EdwardBar exits and says what is missing.

## Run at login

`companion/scripts/com.meet-edward.edwardbar.plist` runs `/Users/USERNAME/bin/EdwardBar`. Replace `USERNAME` with your macOS short name (`whoami`), then:

```bash
cp companion/scripts/com.meet-edward.edwardbar.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.meet-edward.edwardbar.plist
```

This agent is installed by hand on each Mac you want it on. `scripts/install-launchagents.sh` installs only the host's own agents. Its label uses the default prefix; rename it if you keep your own agents under another `EDWARD_LAUNCHD_PREFIX`. The app logs to `/tmp/edwardbar.log`.

**No menu-bar icon wanted?** `companion/scripts/edward-presence.sh` sends the same beacon (minus the frontmost app) from a LaunchAgent every 30 s. Copy it to `~/bin/`, and install `companion/scripts/com.meet-edward.presence.plist` the same way.

## Icon states

| Symbol | Meaning |
|---|---|
| dashed circle | Not connected to Edward (it retries every 5 s) |
| circle | Connected |
| eye | "Watching": no longer sent by the server |
| speech bubble | "Edward wants a word": no longer sent by the server |
