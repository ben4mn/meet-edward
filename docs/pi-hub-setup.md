# The household hub: a touchscreen kiosk

The hub is Edward's shared screen for the house. It shows the pets' feeding and meds, today's calendar, lists, kitchen timers, a full-screen voice mode and a photo screensaver. It is a **thin client**: a browser in kiosk mode pointed at `/hub` on the host Mac. Everything runs on the Mac, and no Edward code is installed on the kiosk.

This guide sets one up on a Raspberry Pi with a touchscreen, the reference build. Any device that can run a browser full screen works, such as an old tablet or a small PC; skip the Pi-specific parts. The hub's layout targets **1280×720 landscape** (see the `ADAPT:` note in `frontend/app/(display)/hub/components/HubRoot.tsx`). Other sizes work but were not designed for. The design rules behind the hub are in [architecture/hub.md](architecture/hub.md).

---

## What the hub shows

| Page | What it is |
|---|---|
| `/hub` | Home: the time, who is at the panel, what's due |
| `/hub/pets` | Feeding and meds: tap to log a bowl or a dose |
| `/hub/today` | Today's calendar |
| `/hub/lists` | Shared lists (groceries …) |
| `/hub/timers` | Kitchen timers |
| `/hub/ask` | Full-screen voice: talk to Edward |
| `/hub/health` | The host Mac's vitals, its LaunchAgents and the screensaver settings |
| `/hub/settings` | The panel's own controls: sleep, screen off, reload, exit |
| `/hub/idle` | The photo screensaver. The other pages fall back to it after 2 minutes untouched. |

## On the Mac first

- **Edward is running**, either installed with `bash scripts/install-launchagents.sh` or started with `./restart.sh`. The kiosk talks only to the frontend on port 3000, which forwards `/api` to the backend, so the Mac has to accept LAN connections on :3000. Check from another device that `http://<your-mac>.local:3000/login` loads. If macOS asks whether to accept incoming connections for node, say yes.
- **A password is set.** Choose it on the Mac itself first (the [README](../README.md#quick-start), step 3): the first password needs a one-time code read on the Mac, and the kiosk can only sign in with a password that exists.
- **Your pets and their care.** Copy `backend/hub_seed.example.json`, describe your own pets and point `EDWARD_HUB_SEED` at the copy. The board is planted from it once; until then it starts empty. Only `timed` items ever alert. `window` items, such as a meal given "twice a day, whenever", never go overdue.
- **The people.** Set `EDWARD_HOUSEHOLD=Sam,Rowan` (you first). The who-toggle shows once there are two or more people, and each person's theme goes by position.
- **Photos for the screensaver.** Add them in Settings → Hub Photos. They are stored in `EDWARD_PHOTOS_DIR` (default `~/edward-photos`), and HEIC is converted on upload.
- **Voice (optional).** `/hub/ask` needs `ELEVENLABS_API_KEY` on the Mac and a microphone on the kiosk. A Raspberry Pi has none built in (see [Ideas](#ideas)). A turn spoken at the hub runs as `hub_voice`, never with the owner's rights, because whoever is at the panel is someone in your home, not necessarily you.

Use the **LAN address** (`http://<your-mac>.local:3000/hub`) for the kiosk even if Edward also has a public URL. Voice has lower latency on the LAN, and the hub keeps working when the internet doesn't. The cost is that plain HTTP is not a "secure context", and the Chromium flags below fix that.

---

## The Raspberry Pi

The reference build is a Raspberry Pi 4 (4 GB) with the official 7" Touch Display 2, running 64-bit Raspberry Pi OS with Desktop (Debian trixie, a Wayland/labwc session under lightdm) and booting from microSD.

1. Flash Raspberry Pi OS (64-bit, Desktop) with Raspberry Pi Imager. In its settings, set a hostname (e.g. `edward-hub`), a user, your Wi-Fi and SSH with **public-key authentication**.
2. Boot it and let it **autologin to the desktop**. This is the default with the Desktop image.
3. Reach it by hostname (`ssh <user>@edward-hub.local`): `avahi-daemon` advertises it over mDNS, so you don't need a fixed IP.
4. Install the few extras:
   ```bash
   sudo apt install fonts-noto-color-emoji wlr-randr grim
   ```
   Without an emoji font the hub's icons render as empty boxes.

### Display and touch

The Touch Display 2 is natively **720×1280 portrait**, so rotate it to landscape:

```bash
wlr-randr --output DSI-1 --transform 90      # 270 if it comes out upside down
```

**Touch scrolling needs `mouseEmulation="no"`.** Raspberry Pi OS ships `~/.config/labwc/rc.xml` with mouse emulation on. labwc then turns touches into mouse events, so taps work but **swiping to scroll silently does nothing**, which looks like a broken page rather than a broken input setting. Map the touchscreen to the rotated output and turn emulation off:

```xml
<touch deviceName="Goodix Capacitive TouchScreen" mapToOutput="DSI-1" mouseEmulation="no"/>
```

(That is the Touch Display 2's controller; `libinput list-devices` names yours.)

Apply it without a reboot with `labwc --reconfigure`. You also need Chromium's `--touch-events=enabled` (below).

The backlight may come up dim and reset on every boot. Pin it in the autostart file (next section), e.g. by writing the maximum to `/sys/class/backlight/*/brightness`.

### Screen blanking

The hub keeps the screen awake by itself: while a hub page is showing, it holds a Wake Lock (which needs the secure-context flag below). Turn the desktop's own blanking off so the two don't fight:

```bash
sudo raspi-config nonint do_blanking 1      # 1 = disable blanking
```

To let the panel sleep, use **Screen off** on `/hub/settings`. It goes black and releases the wake lock, and a tap wakes it. **Sleep now** goes straight to the photo screensaver.

### The kiosk service

Run Chromium as a **systemd user service**, not from a shell, so that a crash restarts it and a deliberate exit does not.

`~/.config/systemd/user/edward-kiosk.service`:

```ini
[Unit]
Description=Edward hub kiosk

[Service]
Environment=XDG_RUNTIME_DIR=/run/user/%U
Environment=WAYLAND_DISPLAY=wayland-0
ExecStart=/usr/bin/chromium --kiosk --noerrdialogs \
  --ozone-platform=wayland \
  --password-store=basic \
  --touch-events=enabled \
  --disk-cache-dir=/run/user/%U/chromium-cache \
  --unsafely-treat-insecure-origin-as-secure=http://<your-mac>.local:3000 \
  http://<your-mac>.local:3000/hub
Restart=on-failure
RestartSec=3
SuccessExitStatus=0 1 SIGTERM SIGHUP SIGINT
```

It has no `[Install]` section on purpose: the desktop session starts it (the autostart file below), so it never starts before Wayland is up.

```bash
systemctl --user daemon-reload
systemctl --user restart edward-kiosk        # start it now, or after the hub's code changes on the Mac
systemctl --user stop edward-kiosk           # back to the bare desktop
journalctl --user -u edward-kiosk -n 50
```

`Restart=on-failure` together with `SuccessExitStatus` gives a kiosk the behaviour you want:

| What happens | Result |
|---|---|
| Someone taps **Exit hub** on `/hub/settings` (a clean exit) | Chromium stays closed and you land on the desktop |
| Chromium crashes (a crash signal, or an exit code other than 0 and 1) | It restarts after 3 s |

`Restart=always` looks like the obvious choice but is wrong here. It makes a deliberate exit look like a crash, so Chromium reappears 3 s later and you can never reach the desktop.

To bring the hub back from the desktop, add a launcher such as `~/Desktop/Edward Hub.desktop` with `Exec=/usr/bin/systemctl --user restart edward-kiosk.service`. The desktop (pcmanfm-pi) labels it with the **filename**, not its `Name=` field, so name the file what you want the label to say.

Start it from `~/.config/labwc/autostart`, which is also the place to set the rotation and the backlight:

```sh
wlr-randr --output DSI-1 --transform 90
systemctl --user restart edward-kiosk.service
```

### The Chromium flags

Without each of these flags, something fails silently or misleadingly:

| Flag | Without it |
|---|---|
| `--ozone-platform=wayland` | Chromium tries X11, logs `Missing X server or $DISPLAY` and exits |
| `--password-store=basic` | A keyring dialog blocks the kiosk on every boot |
| `--unsafely-treat-insecure-origin-as-secure=http://<your-mac>.local:3000` | The microphone and the Wake Lock need a secure context, and `http://<lan-host>` is not one. Without the flag, **voice silently never works and the screen sleeps.** Use exactly the origin the kiosk opens: scheme, host and port. |
| `--touch-events=enabled` | Chromium treats the touchscreen as a mouse: taps work, but swipe-to-scroll does not |
| `--disk-cache-dir=/run/user/<uid>/chromium-cache` | The browser cache writes to the microSD card around the clock, the biggest single source of card wear (`/run/user` is RAM) |

---

## Signing the kiosk in

The first time the kiosk opens `/hub`, Edward sends it to `/login?redirect=/hub`. Plug in a keyboard, or use the on-screen keyboard, and type your Edward password once. The session lasts 30 days and renews itself while it is in use, and the hub polls all day, so it stays signed in.

The session cookie is `Secure`. Over plain HTTP on the LAN, the `--unsafely-treat-insecure-origin-as-secure` flag above is what lets the browser keep it. If sign-in keeps landing back on `/login`, check that the flag's origin matches the URL exactly. Failing that, set `EDWARD_COOKIE_SECURE=off` in the Mac's `.env` and restart the backend, but only if you trust your LAN, because the cookie can then travel over plain HTTP.

**Know what the kiosk holds.** It is signed in as you. The hub pages keep the panel to household things: no destructive controls, no "restart Edward" button, and voice turns that run without your rights. But the browser profile holds a full session, so anyone who leaves the kiosk for the Pi's desktop and opens Chromium is in your Edward. Put the panel somewhere you would leave an unlocked laptop, or see [Ideas](#ideas).

---

## Keeping it healthy

- **On the Mac**, set `EDWARD_HEALTH_HUB_KIOSK=1` and the daily deep health check warns when the kiosk's `/api/hub/*` polls have been silent for 6 hours (`scripts/health_check.sh`).
- **`/hub/health`** shows the host Mac's vitals and Edward's LaunchAgents. **`/hub/settings` → Reload hub** fixes a stuck page without SSH.
- **microSD wear**: keep Chromium's cache on tmpfs (the flag above) and cap journald, e.g. `/etc/systemd/journald.conf.d/10-sdcard.conf` with `SystemMaxUse=64M` and `SyncIntervalSec=5m`.
- **Screenshots over SSH** (Wayland commands need the session's environment):
  ```bash
  export XDG_RUNTIME_DIR=/run/user/$(id -u) WAYLAND_DISPLAY=wayland-0
  grim /tmp/hub.png
  ```

## Gotchas

**`~/.config/labwc/autostart` is run by `/bin/sh` (dash), not bash.** A single bashism, such as a `<<<` herestring, makes dash abort the *whole* file. Rotation and the kiosk then silently never start, and the panel comes up in portrait. Check the file after every edit:

```bash
sh -n ~/.config/labwc/autostart
```

**A loose display ribbon looks like a software bug.** When the DSI ribbon loses contact, labwc invents a headless 1920×1080 output and Chromium renders to it happily. Screenshots look perfect while the physical screen is black. Touch dies at the same moment, because the touch controller reaches the Pi over the same ribbon. The capture size is the tell: a 1280×720 `grim` capture means the panel is there, and 1920×1080 means it is gone. Check with:

```bash
ls /sys/class/drm/ | grep -i dsi || echo "no display detected"
```

To fix it, reseat both ends of the ribbon and cold-boot (pull the power for 10 s). A warm reboot does not always bring the DSI link back.

**Don't put a camera overlay on the display's I²C channel.** On the Pi 4 with the 7" display, `i2c@1` behind `i2c0mux` carries the display and the touchscreen. A camera overlay there can take both down.

---

## Ideas

These are not built yet; ask Claude ([ADAPTING.md](ADAPTING.md#4-adapting-with-claude)):

- **A PIN or device token** for the hub, so it no longer holds a full session.
- **A microphone.** A USB speakerphone with hardware echo cancellation works best for `/hub/ask`. Set it as both the default input *and* the default output, or its echo cancellation has no reference signal.
- **A wake word** (openWakeWord, Porcupine) for hands-free voice instead of tap-to-talk.
- **Another screen size.** The layout lives in `hub.css` next to the hub's pages.
