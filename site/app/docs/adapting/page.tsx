import type { Metadata } from "next";
import Link from "next/link";
import { DocsContent } from "../../../components/docs/DocsContent";
import { GITHUB_BLOB } from "../../../lib/docsNav";

export const metadata: Metadata = {
  title: "Adapting to Your Mac — Edward Docs",
  description:
    "Edward was built for one person's Mac. What is a setting, where the ADAPT: seams are, the macOS permissions he needs, and prompts for Claude for the bigger changes: a pets editor, Linux, systemd, his own Apple ID, Alexa verification, another browser, and running him as his own macOS user.",
  alternates: { canonical: "/docs/adapting" },
  openGraph: {
    title: "Adapting to Your Mac — Edward Docs",
    description:
      "Settings, ADAPT: seams, macOS permissions, and prompts for Claude for the bigger changes.",
    url: "/docs/adapting",
  },
};

const ADAPTING = `${GITHUB_BLOB}/docs/ADAPTING.md`;

export default function AdaptingPage() {
  return (
    <DocsContent>
      <h1>Adapting to your Mac</h1>
      <p className="subtitle">
        Edward ran for months for one person in one home. Here is how to fit
        him to yours.
      </p>

      <p>
        Most of what was specific to that home is now a setting. A few places
        still assume the first host, and each is marked in the code with an{" "}
        <code>ADAPT:</code> note. The full list, with file and line, is{" "}
        <a href={ADAPTING}>docs/ADAPTING.md</a>.
      </p>

      <h2>Settings, not code</h2>
      <table>
        <thead>
          <tr>
            <th>What</th>
            <th>Where it is read</th>
            <th>Settings</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Who you are</td>
            <td><code>backend/services/owner.py</code></td>
            <td>
              <code>EDWARD_OWNER_NAME</code>, <code>_FULL_NAME</code>,{" "}
              <code>_ALIASES</code>, <code>_HANDLES</code>,{" "}
              <code>_EMAILS</code>, <code>EDWARD_HOUSEHOLD</code>
            </td>
          </tr>
          <tr>
            <td>Where Edward runs</td>
            <td><code>backend/services/host_config.py</code></td>
            <td>
              <code>EDWARD_PUBLIC_BASE_URL</code>,{" "}
              <code>EDWARD_LAUNCHD_PREFIX</code>,{" "}
              <code>EDWARD_TUNNEL_LABEL</code>, <code>EDWARD_BACKUP_DIR</code>,
              and more
            </td>
          </tr>
          <tr>
            <td>What the shell scripts see</td>
            <td><code>scripts/lib/edward-env.sh</code></td>
            <td>The environment, then <code>.env</code>, then the default</td>
          </tr>
        </tbody>
      </table>
      <p>
        See <Link href="/docs/configuration">Configuration</Link> for the
        settings themselves.
      </p>

      <h2>Finding the seams</h2>
      <pre>
        <code>{`grep -rn "ADAPT:" --include='*.py' --include='*.ts' --include='*.tsx' --include='*.sh' \\
  --include='*.swift' --include='*.js' \\
  backend frontend scripts companion chrome-extension scriptable | grep -v node_modules`}</code>
      </pre>
      <p>Each note has the same three parts:</p>
      <pre>
        <code>{`# ADAPT: <what is specific to the original host, in one line>
#   How it works here: <what the code assumes>
#   To adapt: <the setting to change, or what to build>`}</code>
      </pre>
      <p>The ones you are most likely to meet:</p>
      <ul>
        <li>
          <strong>iMessage as you.</strong> Edward sends and reads as the
          Apple ID Messages on the Mac is signed into, so “from me” means
          you. An Apple ID of his own means reworking the echo checks.
        </li>
        <li>
          <strong>Mail.app’s index</strong> lives under{" "}
          <code>~/Library/Mail/V10</code>, which changes between macOS
          releases (<code>EDWARD_MAIL_ENVELOPE_INDEX</code>).
        </li>
        <li>
          <strong>Calendar’s and Reminders’ stores</strong> are private Apple
          databases, checked on one macOS version; when they can’t be read he
          says so (the calendar falls back to apple-mcp when it is set up).
        </li>
        <li>
          <strong>The protected Homebrew kegs</strong> (python@3.12 and
          postgresql@16) and <code>HOMEBREW_PREFIX</code>, which the sandbox
          rails name.
        </li>
        <li>
          <strong>The screen</strong> is a Google Chrome tab sized over
          AppleScript when the hands are on; any browser works when they are
          off.
        </li>
        <li>
          <strong>The hub</strong> is laid out for one 7″ kiosk, and its pets
          come from a seed file with no editor in the app.
        </li>
        <li>
          <strong>Alexa</strong> inbound is not signature-verified.
        </li>
        <li>
          <strong>Ports</strong>: :8000 and :3000 are also written into the
          Chrome extension and the shell’s denied ports, so moving one means
          changing those too.
        </li>
      </ul>

      <h2>macOS permissions</h2>
      <p>
        Almost every grant goes to the <strong>backend’s python</strong>: the
        real path of the python3.12 behind <code>backend/.venv</code>, which{" "}
        <code>setup.sh</code> and the LaunchAgent installer print. To grant
        it: System Settings → Privacy &amp; Security → the list →{" "}
        <strong>+</strong> → <code>Cmd + Shift + G</code> → paste the path.
        Started by hand from a terminal, macOS charges the terminal app
        instead.
      </p>
      <table>
        <thead>
          <tr>
            <th>Permission</th>
            <th>What needs it</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Full Disk Access</td>
            <td>
              iMessage, contact names, Mail’s index, the Calendar and
              Reminders stores, and backups to a removable drive
            </td>
          </tr>
          <tr>
            <td>Automation</td>
            <td>
              Sending an iMessage; with the Chrome hands on, Chrome, Finder and
              System Events. Answer the first prompt at the Mac
            </td>
          </tr>
          <tr>
            <td>Accessibility</td>
            <td>Sizing Chrome’s window for <code>/screen</code> (hands on only)</td>
          </tr>
          <tr>
            <td>Screen Recording</td>
            <td><code>look</code> at the screen</td>
          </tr>
          <tr>
            <td>Calendars, Reminders</td>
            <td>
              Changing them when you ask:{" "}
              <code>bash backend/scripts/eventkit_request_access.sh</code>,
              at the Mac
            </td>
          </tr>
        </tbody>
      </table>
      <div className="note caution">
        <p>
          <strong>Grants follow the binary’s path.</strong>{" "}
          <code>brew upgrade python@3.12</code> moves Python and every grant
          silently stops applying: grant the new path, re-run{" "}
          <code>bash scripts/install-launchagents.sh</code> and the EventKit
          script. A <code>brew install</code> of something that depends on
          Python can upgrade it along the way, which is why Edward’s media
          handling avoids ffmpeg.
        </p>
      </div>

      <h2>Prompts for the bigger changes</h2>
      <p>
        Open Claude Code in your checkout: <code>CLAUDE.md</code> loads by
        itself and carries the house rules (a fix lands with the test that pins it, backend
        tests only on the test database, the architecture doc of anything you
        change). <a href={ADAPTING}>ADAPTING.md</a> has a prompt for each of
        these, ready to paste:
      </p>
      <table>
        <thead>
          <tr>
            <th></th>
            <th>Change</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>A</td>
            <td>A pets-and-care editor in the Edward tab, so the hub needs no seed file</td>
          </tr>
          <tr>
            <td>B</td>
            <td>Linux, starting with a sandbox for the <code>bash</code> tool (bubblewrap)</td>
          </tr>
          <tr>
            <td>C</td>
            <td>systemd units instead of LaunchAgents</td>
          </tr>
          <tr>
            <td>D</td>
            <td>A separate Apple ID (or number) for Edward, so people text him, not you</td>
          </tr>
          <tr>
            <td>E</td>
            <td>Amazon’s signature verification for inbound Alexa requests</td>
          </tr>
          <tr>
            <td>F</td>
            <td>Your own browser (Safari, Arc) for the screen</td>
          </tr>
          <tr>
            <td>G</td>
            <td>
              Edward under his own macOS user, for real isolation of his shell.
              The reference install never ran this way: treat it as a project
              (his own logged-in session, his own copies of your data, Postgres
              with passwords)
            </td>
          </tr>
        </tbody>
      </table>
      <p>
        When you add code that assumes something about your own host, mark it
        the same way, and add a row to ADAPTING.md.
      </p>
    </DocsContent>
  );
}
