import type { Metadata } from "next";
import Link from "next/link";
import { DocsContent } from "../../../components/docs/DocsContent";
import { Screenshot } from "../../../components/docs/Screenshot";
import { GITHUB_BLOB } from "../../../lib/docsNav";

export const metadata: Metadata = {
  title: "Widget, Screen & Hub — Edward Docs",
  description:
    "Where Edward shows himself beyond chat: an iPhone widget through Scriptable, his own stage on the host Mac's screen, and an optional household hub for a kitchen or pet station, all composed by his own judgment.",
  alternates: { canonical: "/docs/surfaces" },
  openGraph: {
    title: "Widget, Screen & Hub — Edward Docs",
    description:
      "An iPhone widget, his stage on the Mac's screen, and an optional household hub.",
    url: "/docs/surfaces",
  },
};

export default function SurfacesPage() {
  return (
    <DocsContent>
      <h1>Widget, screen &amp; hub</h1>
      <p className="subtitle">
        Where Edward shows himself, and decides for himself what to show.
      </p>

      <h2>The pulse</h2>
      <p>
        Edward decides what his surfaces show, the way you would decide what
        to pin to a fridge: there is no fixed layout. Every 15 minutes a
        check in code looks at what feeds them (the pet board, your calendar,
        the widget, the screens, who is at the hub). Only when something
        changed does he take a turn to decide what to show; most ticks cost
        nothing. The pulse reaches nobody and never pushes. If it fails or
        the day’s budget is spent, a plain fallback card stands in.
      </p>

      <h2>The iPhone widget</h2>
      <p>
        A home-screen widget through the{" "}
        <a href="https://scriptable.app">Scriptable</a> app. Edward fills it
        with sections (a title, lines, stats, a countdown) or, when he wants
        full control, a small Scriptable script of his own. A section can
        carry an end time and disappears by itself when it passes. Until he
        writes anything, the widget shows your upcoming events in your time
        zone.
      </p>
      <h3>Setting it up</h3>
      <ol>
        <li>
          Turn on the <strong>iOS Widget</strong> skill (Edward tab → Mind →
          Skills).
        </li>
        <li>
          Install Scriptable and copy{" "}
          <a href={`${GITHUB_BLOB}/scriptable/edward-widget.js`}>
            <code>scriptable/edward-widget.js</code>
          </a>{" "}
          into a new script.
        </li>
        <li>
          Fill in your public address and your widget token: while signed
          in, open <code>/api/widget/token</code> on your Edward.
        </li>
        <li>Add a Scriptable widget to your home screen and pick the script.</li>
      </ol>
      <div className="note caution">
        <p>
          <strong>Treat the widget token like your password.</strong> Besides
          the widget, it can chat with Edward as you (
          <code>POST /api/widget/chat</code>, which{" "}
          <code>scriptable/edward-chat.js</code> uses). Put it only on your
          own devices; if it leaks, replace it with{" "}
          <code>POST /api/widget/token/regenerate</code> while signed in.
        </p>
      </div>

      <h2>His screen</h2>
      <p>
        <code>/screen</code> is Edward’s stage, meant for a display near the
        host Mac (or any browser you like). It is a scene of up to eight
        pieces he places himself (words, notes, lists, a countdown or timer,
        a picture, a video, your calendar, the pet board, a clock), with
        Edward standing in it, the lines he says, and, while a wake or the
        pulse is running, what he is thinking: tool names and scrubbed
        reasoning, never their arguments or results.
      </p>
      <Screenshot
        src="/screenshots/screen-desktop.png"
        alt="Edward's screen: a headline, a countdown to a landing, the weather for Saturday, a thought he wrote, tonight's pet care, and Edward standing on the stage"
        width={1600}
        height={1000}
        caption="A scene he arranged on a quiet evening."
      />
      <ul>
        <li>
          <strong>It is his.</strong> He is encouraged to change it, and his
          free time each afternoon is partly for it.
        </li>
        <li>
          <strong>Your ask holds it.</strong> Ask him to put something up and
          it stays until the hold lapses; his own turns can’t replace it,
          though he can still say a line.
        </li>
        <li>
          <strong>His lines</strong> can be spoken aloud, never during quiet
          hours.
        </li>
        <li>
          <strong>Opening it</strong>: open <code>/screen</code> yourself in
          any browser. With the Chrome hands on, Edward finds or opens it as
          a tab in your Chrome, sized to the desktop (never a full-screen
          Space).
        </li>
      </ul>

      <h2>The household hub (optional)</h2>
      <p>
        <code>/hub</code> is a touchscreen page for a kitchen or a pet
        station: a thin client, with all the logic on the host Mac. The
        reference install ran it on a Raspberry Pi with a 7″ touchscreen in
        Chromium’s kiosk mode (
        <a href={`${GITHUB_BLOB}/docs/pi-hub-setup.md`}>pi-hub-setup.md</a>
        ); any kiosk-mode browser on <code>/hub</code> works.
      </p>
      <Screenshot
        src="/screenshots/hub-kiosk.png"
        alt="The household hub: the time, two late doses, a who-toggle, Edward's greeting, tiles for pets, calendar, lists, voice, timers, health, photos and settings, and each pet's feeding status"
        width={1600}
        height={960}
        caption="The hub on a 7″ kiosk: two doses late, everything else calm."
      />
      <table>
        <thead>
          <tr>
            <th>Page</th>
            <th>What it does</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>/hub</code></td>
            <td>Home: the day at a glance, and what Edward put up for the house</td>
          </tr>
          <tr>
            <td><code>/hub/pets</code></td>
            <td>Feeding and medication: tap to log a dose</td>
          </tr>
          <tr>
            <td><code>/hub/today</code>, <code>/hub/lists</code>, <code>/hub/timers</code></td>
            <td>The family calendar, shared lists, kitchen timers</td>
          </tr>
          <tr>
            <td><code>/hub/ask</code></td>
            <td>Full-screen voice. It runs as someone in your home, not as you</td>
          </tr>
          <tr>
            <td><code>/hub/health</code>, <code>/hub/settings</code>, <code>/hub/idle</code></td>
            <td>Vitals, the panel’s own controls, the photo screensaver</td>
          </tr>
        </tbody>
      </table>
      <h3>Built so alerts stay rare</h3>
      <ul>
        <li>
          Food given “twice daily, whenever” <strong>never</strong> goes
          overdue. Only timed doses (a dog’s 06:00 and 18:00 medication) can.
        </li>
        <li>
          A dose more than four hours late stops alerting, but is still
          recorded.
        </li>
        <li>
          Status colours never change with a person’s theme: “overdue” looks
          the same whoever is at the screen.
        </li>
        <li>
          An item copied from, say, a pet-sitter’s note shows a “needs
          review” badge until someone confirms it.
        </li>
        <li>
          A timed dose still unlogged 45 minutes after its slot pushes you
          once per pet and slot, from code, outside quiet hours and the spend
          cap, unless someone is standing at the hub.
        </li>
      </ul>
      <h3>Setting it up</h3>
      <ul>
        <li>
          <strong>Pets and care</strong> come from a seed file: copy{" "}
          <code>backend/hub_seed.example.json</code> (a fictional dog and a
          cat), describe your own, and set <code>EDWARD_HUB_SEED</code>.
        </li>
        <li>
          <strong>People</strong>: <code>EDWARD_HOUSEHOLD</code>,
          comma-separated, you first. With two or more, the hub shows a
          who-toggle and themes by person.
        </li>
        <li>
          <strong>Photos</strong> for the screensaver live in{" "}
          <code>~/edward-photos</code> on the host Mac; upload them from the
          Edward tab (House → Hub Photos). A photos-only second login for
          someone else in the house (<code>/photos</code>) is off unless{" "}
          <code>PHOTO_DOOR_PASSWORD_HASH</code> is set.
        </li>
        <li>
          <strong>Over plain HTTP on your network</strong>, the kiosk browser
          needs{" "}
          <code>--unsafely-treat-insecure-origin-as-secure=http://my-mac.local:3000</code>
          , or the microphone, the wake lock and the sign-in cookie silently
          fail.
        </li>
      </ul>
      <p>
        The hub signs in with your normal password, so it deliberately has no
        controls that change Edward himself (no restart button, no skill
        switches).
      </p>

      <hr />
      <p>
        The long form:{" "}
        <a href={`${GITHUB_BLOB}/docs/architecture/screen-and-browser.md`}>
          screen-and-browser.md
        </a>{" "}
        and <a href={`${GITHUB_BLOB}/docs/architecture/hub.md`}>hub.md</a>.
        For the pulse’s place among his wakes, see{" "}
        <Link href="/docs/mind">The mind</Link>.
      </p>
    </DocsContent>
  );
}
