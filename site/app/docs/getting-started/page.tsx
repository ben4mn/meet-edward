import type { Metadata } from "next";
import Link from "next/link";
import { DocsContent } from "../../../components/docs/DocsContent";
import { GITHUB_BLOB } from "../../../lib/docsNav";

export const metadata: Metadata = {
  title: "Quick Start — Edward Docs",
  description:
    "Install Edward on a Mac with Apple silicon: setup.sh, LaunchAgents or restart.sh, the one-time setup code, skills and macOS permissions.",
  alternates: { canonical: "/docs/getting-started" },
  openGraph: {
    title: "Quick Start — Edward Docs",
    description:
      "Install Edward on a Mac with Apple silicon: setup.sh, LaunchAgents, the setup code and first steps.",
    url: "/docs/getting-started",
  },
};

export default function GettingStartedPage() {
  return (
    <DocsContent>
      <h1>Quick start</h1>
      <p className="subtitle">
        For the terminal-comfortable: install, start, choose a password.
      </p>

      <h2>You need</h2>
      <ul>
        <li>A Mac with Apple silicon that can stay on (a Mac mini makes a good host)</li>
        <li>
          The Xcode command-line tools (<code>xcode-select --install</code>)
          and <a href="https://brew.sh">Homebrew</a>
        </li>
        <li>
          An <a href="https://console.anthropic.com/">Anthropic API key</a>
        </li>
      </ul>

      <h2>1. Install</h2>
      <pre>
        <code>{`git clone https://github.com/ben4mn/meet-edward.git && cd meet-edward
./setup.sh          # installs everything, asks for your name, API key and time zone`}</code>
      </pre>
      <p>
        <code>./setup.sh</code> is safe to run again; each step checks what is
        already there. It stops before installing anything on an Intel Mac or
        in a Rosetta shell. It installs <code>python@3.12</code>,{" "}
        <code>postgresql@16</code> and <code>node</code> with Homebrew, builds
        pgvector from source for Postgres 16, creates the{" "}
        <code>edward</code> database, makes <code>backend/.venv</code> with
        pinned dependencies, installs Playwright’s headless shell, runs{" "}
        <code>npm ci</code>, builds two small Swift helpers (media and
        EventKit), and writes <code>.env</code> from{" "}
        <code>.env.example</code>.
      </p>
      <table>
        <thead>
          <tr>
            <th>Flag</th>
            <th>What it adds</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>--with-test-db</code></td>
            <td>the backend tests’ own Postgres cluster on :5433</td>
          </tr>
          <tr>
            <td><code>--launchagents</code></td>
            <td>runs <code>scripts/install-launchagents.sh</code> at the end</td>
          </tr>
          <tr>
            <td><code>--no-helpers</code></td>
            <td>skips building the Swift helpers</td>
          </tr>
          <tr>
            <td><code>--non-interactive</code></td>
            <td>
              never prompts; takes <code>EDWARD_OWNER_NAME</code>,{" "}
              <code>ANTHROPIC_API_KEY</code> and <code>TIMEZONE</code> from
              the environment
            </td>
          </tr>
        </tbody>
      </table>

      <h2>2. Start Edward, one of two ways</h2>
      <p>
        <strong>Always on</strong> (the usual choice for the Mac that hosts
        him). launchd starts him now and at every login, and restarts him if
        he stops:
      </p>
      <pre>
        <code>bash scripts/install-launchagents.sh</code>
      </pre>
      <p>
        It installs the backend (:8000), the frontend (:3000), a health check
        every five minutes plus a deeper one at 08:00, a nightly backup at
        03:30 (to <code>~/edward-backups</code> unless{" "}
        <code>EDWARD_BACKUP_DIR</code> says otherwise) and a daily memory
        report, labelled <code>com.meet-edward.*</code>. It takes{" "}
        <code>--with-graph</code>, <code>--without-backup</code>,{" "}
        <code>--dry-run</code> and <code>--uninstall</code>. If you started
        Edward by hand from this checkout, it stops that copy first.
      </p>
      <p>
        <strong>By hand.</strong> He runs until you stop him or the Mac
        restarts:
      </p>
      <pre>
        <code>./restart.sh        # backend :8000, frontend :3000</code>
      </pre>
      <p>
        With the agents loaded, <code>./restart.sh</code> (or{" "}
        <code>./restart.sh backend|frontend</code>) restarts through launchd.
        It only restarts its own checkout’s Edward: in a second clone it
        refuses (exit 78) to touch agents that run another one.
      </p>
      <p>
        The first start takes a few minutes: the backend downloads its
        embedding model and sets up the database, and the frontend runs its
        first production build. <code>./restart.sh</code> waits up to 2
        minutes for the backend and 4 for the frontend, then says “still
        starting” and exits 0. Logs: <code>/tmp/edward-backend.log</code> and{" "}
        <code>/tmp/edward-frontend.log</code>.
      </p>

      <h2>3. Choose a password, on the Mac itself</h2>
      <p>
        Open <code>http://localhost:3000</code> on the Mac that runs Edward.
        The first visit asks for a password and a one-time setup code:
      </p>
      <pre>
        <code>cat backend/.setup_code</code>
      </pre>
      <p>
        The backend writes the code when it starts with no password set; only
        your macOS user can read it, and it is deleted once the password is
        set. Until then the API answers nothing but the sign-in pages, and the
        setup request must come from a page at <code>localhost</code> on this
        Mac. No screen on the Mac? Forward the port and read the code over
        ssh:
      </p>
      <pre>
        <code>{`ssh -L 3000:localhost:3000 you@my-mac.local
ssh you@my-mac.local cat <your checkout>/backend/.setup_code`}</code>
      </pre>

      <h2>4. After you sign in</h2>
      <ol>
        <li>
          <strong>Edward tab → Mind → Skills.</strong> Every skill starts off
          on a fresh install. Turn on what Edward may use: Shell/Bash (his
          computer: <code>bash</code> and the editor), Web Search, iMessage,
          Push Notifications, iOS Widget.
        </li>
        <li>
          <strong>House → Locale.</strong> Check your time zone: reminders,
          quiet hours and “today” follow it.
        </li>
        <li>
          <strong>macOS permissions</strong> (System Settings → Privacy &amp;
          Security), only for what you use:
          <ul>
            <li>
              <strong>Full Disk Access</strong> for the python{" "}
              <code>setup.sh</code> prints at the end: iMessage, Mail,
              Contacts, Calendar and Reminders are read from their databases
              through it. Started by hand with <code>./restart.sh</code>,
              macOS charges your terminal app instead, so grant that too.
            </li>
            <li>
              <strong>Automation</strong>: macOS asks the first time Edward
              sends a message or drives Chrome.
            </li>
            <li>
              <strong>Calendars and Reminders</strong> (for changes): run{" "}
              <code>bash backend/scripts/eventkit_request_access.sh</code>{" "}
              while sitting at the Mac.
            </li>
          </ul>
        </li>
      </ol>

      <h2>Reaching him from your phone</h2>
      <p>
        Installing the PWA, push notifications and the microphone all need
        HTTPS. Put a tunnel (Cloudflare Tunnel, Tailscale, ngrok) or a
        reverse proxy in front of port 3000, set{" "}
        <code>EDWARD_PUBLIC_BASE_URL</code> to that address, and name the
        tunnel’s LaunchAgent in <code>EDWARD_TUNNEL_LABEL</code> so the health
        check watches it. The backend listens on every interface so devices
        on your network (a hub) can reach it;{" "}
        <code>EDWARD_BACKEND_HOST=127.0.0.1</code> keeps it to this Mac.
        Behind a tunnel, leave uvicorn’s <code>FORWARDED_ALLOW_IPS</code>{" "}
        unset: sign-in limits read the client from the tunnel’s own headers.
      </p>

      <h2>Is it working?</h2>
      <ul>
        <li>
          <code>GET /api/health/report</code> (signed in) says what works:
          turns and tool failures, pings, the calendar, mail, the last memory
          report, spend, and whether the app’s time zone matches the Mac’s.
        </li>
        <li>
          <code>GET /api/health/spend</code> shows today’s Claude spend against
          the cap.
        </li>
        <li>
          The watchdog writes to <code>~/.edward-health/health.log</code>.
        </li>
      </ul>

      <h2>Common issues</h2>
      <h3>Signing in loops back to the login page</h3>
      <p>
        The session cookie is <code>Secure</code>, which assumes HTTPS
        (browsers make an exception for <code>http://localhost</code>). Use
        HTTPS, or set <code>EDWARD_COOKIE_SECURE=off</code> on a plain-HTTP
        network you trust.
      </p>
      <h3>Messages, Mail or Calendar can’t be read</h3>
      <p>
        Full Disk Access is tied to the python binary’s path, so{" "}
        <code>brew upgrade python@3.12</code> silently breaks it: grant the
        new path and re-run <code>bash scripts/install-launchagents.sh</code>{" "}
        and <code>bash backend/scripts/eventkit_request_access.sh</code>.
      </p>
      <h3>pgvector is missing after a Postgres reinstall</h3>
      <p>
        <code>brew reinstall postgresql@16</code> removes the pgvector built
        from source. Run <code>./setup.sh</code> again.
      </p>

      <hr />
      <p>
        Next: <Link href="/docs/configuration">Configuration</Link> (every
        setting, and what ships off), or{" "}
        <Link href="/docs/upgrading">Upgrading from March</Link> if you ran the
        March 2026 release. The{" "}
        <a href={`${GITHUB_BLOB}/README.md`}>README</a> is the canonical
        version of this page.
      </p>
    </DocsContent>
  );
}
