import type { Metadata } from "next";
import Link from "next/link";
import { DocsContent } from "../../../components/docs/DocsContent";
import { GITHUB_BLOB } from "../../../lib/docsNav";

export const metadata: Metadata = {
  title: "His Computer — Edward Docs",
  description:
    "Edward's own computer: one set of 14 tools on every surface, a persistent shell sandboxed by macOS Seatbelt in ~/edward-home, a file editor, a headless browser of his own, optional hands in your Chrome, look, the web and your calendar. What the sandbox protects, and what it doesn't.",
  alternates: { canonical: "/docs/computer" },
  openGraph: {
    title: "His Computer — Edward Docs",
    description:
      "14 tools, a sandboxed shell in ~/edward-home, two browsers, look, the web and your calendar.",
    url: "/docs/computer",
  },
};

export default function ComputerPage() {
  return (
    <DocsContent>
      <h1>His computer</h1>
      <p className="subtitle">
        A shell, an editor and a browser of his own, on your Mac, with rails.
      </p>

      <h2>One toolset, everywhere</h2>
      <p>
        Edward has the same fourteen tools in every turn: your chat, voice,
        pings, his wakes, the screens’ pulse and scheduled events. What a turn
        may do with them is its <strong>policy</strong> (did you ask? may it
        reach people? may it push?), checked when a tool is called; a refused
        call comes back as an honest <code>Tool error:</code> with the reason.
      </p>
      <table>
        <thead>
          <tr>
            <th>Tool</th>
            <th>What it does</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>bash</code></td>
            <td>A persistent shell in his home folder, sandboxed</td>
          </tr>
          <tr>
            <td><code>str_replace_based_edit_tool</code></td>
            <td>View, create and edit files, under the same rails</td>
          </tr>
          <tr>
            <td><code>web_search</code>, <code>web_fetch</code></td>
            <td>Search the web and read pages</td>
          </tr>
          <tr>
            <td><code>browser</code></td>
            <td>His own invisible browser</td>
          </tr>
          <tr>
            <td><code>chrome</code></td>
            <td>Your Chrome, through the Edward extension (off by default)</td>
          </tr>
          <tr>
            <td><code>look</code></td>
            <td>See images, PDFs, a web page or the screen as pictures</td>
          </tr>
          <tr>
            <td><code>messages</code></td>
            <td>Read iMessage and mail</td>
          </tr>
          <tr>
            <td><code>send_message</code></td>
            <td>The one guarded way to message someone</td>
          </tr>
          <tr>
            <td><code>tell_owner</code></td>
            <td>Reach you: a note on Today, or a push</td>
          </tr>
          <tr>
            <td><code>memory</code></td>
            <td>Search, remember, close, explain or forget memories; read and write documents</td>
          </tr>
          <tr>
            <td><code>schedule</code></td>
            <td>Reminders, follow-up wakes, your calendar and Reminders</td>
          </tr>
          <tr>
            <td><code>screen</code></td>
            <td>His screen, the household screen and the iPhone widget</td>
          </tr>
          <tr>
            <td><code>plan</code></td>
            <td>A visible checklist for longer work</td>
          </tr>
        </tbody>
      </table>
      <p>
        Skill switches only take tools away: Shell/Bash gates the shell and
        editor, Web Search the web tools, iMessage the message tools, iOS
        Widget the widget, Push Notifications the urgent push.
      </p>

      <h2>His home and his shell</h2>
      <p>
        <code>~/edward-home</code> is his: <code>bin/</code> (on his PATH),{" "}
        <code>projects/</code>, <code>skills/</code>, <code>downloads/</code>,{" "}
        <code>jobs/</code> for background logs, and a Python 3.12 environment
        of his own. He may <code>pip install</code>, use <code>uv</code> and{" "}
        <code>npm</code>, and <code>brew install</code> ordinary formulae.
      </p>
      <ul>
        <li>
          One persistent <code>bash</code> per conversation, so{" "}
          <code>cd</code> and variables carry over between commands.
        </li>
        <li>
          180 seconds a command; long work is a background job plus a
          scheduled wake to check on it.
        </li>
        <li>
          A watchdog kills a session past 2 GB of memory or when the disk runs
          low; at most four sessions at once.
        </li>
        <li>
          Every output is scanned and secrets are redacted before the model
          sees it.
        </li>
        <li>
          Its environment is an allowlist: no API keys, no{" "}
          <code>.env</code> values, no database address.
        </li>
        <li>
          If the sandbox can’t apply, <code>bash</code> fails closed. It never
          silently runs unsandboxed.
        </li>
      </ul>

      <h2>The rails</h2>
      <p>
        The shell runs under a macOS Seatbelt profile rendered from one table
        of rails, which the in-process editor checks too:
      </p>
      <ul>
        <li>
          <strong>Not his own credentials</strong>: the session-signing key,
          the setup code and <code>.env</code>, however the path is spelled
          (compared by the file’s identity on disk, so case variants, symlinks
          and hard links count).
        </li>
        <li>
          <strong>Not his own running code</strong>: no writes to the checkout
          Edward runs from, his releases or his own sandbox config, and none of
          the directories above them can be renamed or moved.
        </li>
        <li>
          <strong>Not what production runs on</strong>: no writes to either
          Postgres cluster’s data or to Homebrew’s <code>python@3.12</code>{" "}
          and <code>postgresql@16</code>, and no upgrading them.
        </li>
        <li>
          <strong>Not the services that act as you</strong>: no connection to
          Edward’s own API (:8000, :3000), the WhatsApp bridge, or his
          databases (:5432, :5433).
        </li>
        <li>
          <strong>Not your other apps</strong>: no launching or scripting
          apps (messages go through <code>send_message</code>, never{" "}
          <code>{'tell application "Messages"'}</code>), never a second Google
          Chrome, and signals only to his own processes.
        </li>
      </ul>

      <div className="note caution">
        <p>
          <strong>The sandbox is not a containment boundary.</strong> The
          shell runs as your macOS user. Reads are open (except his own
          credentials), and writes are open everywhere but the protected paths,
          including your shell startup files, <code>~/Library/LaunchAgents</code>{" "}
          and Homebrew, from which programs later run as you, outside the
          sandbox. The rails stop accidents and turn misuse into deliberate
          work; they don’t make it impossible. For real isolation, run Edward
          under his own macOS user (prompt G in{" "}
          <Link href="/docs/adapting">Adapting to your Mac</Link>). Short of
          that, keep friends’ pings on their default, which gives
          them no shell.
        </p>
      </div>

      <h2>Two browsers</h2>
      <h3>
        <code>browser</code>: his own
      </h3>
      <p>
        Playwright’s bundled headless shell, never Google Chrome, with his own
        cookies (never yours). It opens http and https only, and every request
        and redirect is checked: nothing on this Mac or your network, only
        public addresses. He can open, read, find, click, type, scroll and
        download.
      </p>
      <h3>
        <code>chrome</code>: yours, if you allow it
      </h3>
      <p>
        With <code>EDWARD_CHROME_HANDS=on</code> and the unpacked extension in{" "}
        <code>chrome-extension/</code> loaded, Edward can use your own,
        signed-in Chrome. The extension connects only directly from this Mac,
        and only Edward’s own extension ID is accepted. Rails:
      </p>
      <ul>
        <li>
          Typing outside a search box, pressing Enter there, or clicking
          anything labelled send, post, delete, accept, confirm, share and
          the like acts as you, so it runs only when you asked in that turn.
        </li>
        <li>
          A payment step, or typing anything that looks like a card number,
          is refused in every turn.
        </li>
        <li>
          Taking a picture of a tab never switches your active tab.
        </li>
      </ul>
      <p>
        Chrome shows its “being debugged” bar while he works, unless you set{" "}
        <code>EDWARD_CHROME_SILENT_DEBUGGER=on</code>. With the hands on,{" "}
        <code>/screen</code> also opens as a tab in your Chrome (see{" "}
        <Link href="/docs/surfaces">Widget, screen &amp; hub</Link>).
      </p>

      <h2>
        <code>look</code>
      </h2>
      <p>
        <code>look</code> hands the model the picture itself, not another
        model’s description of it: a photo from a chat, a file, a PDF, a web
        address, his own page, your Chrome tab, or this Mac’s screen (which
        needs Screen Recording for the backend’s python). Images are scaled to
        1600 pixels at most, never up; a turn carries at most 40 images.
      </p>

      <h2>The web</h2>
      <p>
        On Claude, <code>web_search</code> and <code>web_fetch</code> are
        Anthropic’s server tools: the search and fetch run at Anthropic. With{" "}
        <code>EDWARD_SERVER_WEB_TOOLS=off</code> (or on the Codex fallback)
        they run locally: Brave Search with your key, and a local fetch that
        refuses this Mac and your network on every hop.
      </p>

      <h2>Your calendar and Reminders</h2>
      <ul>
        <li>
          <strong>Reading</strong>: straight from Calendar’s and Reminders’
          own databases on this Mac (Full Disk Access), read-only, in every
          turn. If they can’t be read he says so; he never reports an empty
          day instead. apple-mcp, if set up, is the calendar’s fallback.
        </li>
        <li>
          <strong>Changing</strong>: only when you ask, through a small
          signed EventKit helper (macOS 14+). Grant it once, at the Mac, with{" "}
          <code>bash backend/scripts/eventkit_request_access.sh</code>. Edward
          never asks macOS himself, because a dialog nobody answers would hang
          a turn.
        </li>
      </ul>

      <hr />
      <p>
        The long form, including the full list of what the sandbox does not
        prevent, is{" "}
        <a href={`${GITHUB_BLOB}/docs/architecture/computer.md`}>computer.md</a>
        .
      </p>
    </DocsContent>
  );
}
