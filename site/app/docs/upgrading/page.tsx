import type { Metadata } from "next";
import Link from "next/link";
import { DocsContent } from "../../../components/docs/DocsContent";
import { GITHUB_BLOB } from "../../../lib/docsNav";

export const metadata: Metadata = {
  title: "Upgrading from March — Edward Docs",
  description:
    "Move an Edward installed from the March 2026 release onto the October release without losing his memories: back up, pull, run setup, tidy .env, start. Plus what was removed, what is dormant and what is new.",
  alternates: { canonical: "/docs/upgrading" },
  openGraph: {
    title: "Upgrading from March — Edward Docs",
    description:
      "Move a March 2026 Edward onto the October release without losing what he knows.",
    url: "/docs/upgrading",
  },
};

const UPGRADING = `${GITHUB_BLOB}/docs/UPGRADING.md`;

export default function UpgradingPage() {
  return (
    <DocsContent>
      <h1>Upgrading from March</h1>
      <p className="subtitle">
        Move a March 2026 Edward onto this release without losing what he
        knows.
      </p>

      <p>
        This page is for an Edward installed from the March 2026 public
        release (<code>main</code> up to commit <code>408e1d5</code>, or the{" "}
        <code>v1.0.0</code> tag). A fresh install needs none of it: start at{" "}
        <Link href="/docs/getting-started">Quick start</Link>. The full
        version, with every command and edge case, is{" "}
        <a href={UPGRADING}>docs/UPGRADING.md</a> in the repository.
      </p>
      <p>
        <strong>The short version:</strong> stop Edward, back up the
        database, pull, move the old Python environment aside, run{" "}
        <code>./setup.sh</code>, tidy <code>.env</code>, start him. The first
        start migrates the database in place. Plan on half an hour, most of it
        installs and the first frontend build.
      </p>

      <h2>What changes</h2>
      <p>
        Almost everything around the memory store. Your memories, documents,
        conversations, files, scheduled events, password and settings are
        kept. What Edward can do, and how, is new:
      </p>
      <ul>
        <li>
          <strong>Every model call goes through the Claude API</strong>, one
          route per purpose, under a <strong>daily spend cap</strong> ($5 by
          default). The model you picked in March is replaced by the route’s.
        </li>
        <li>
          <strong>One toolset of 14 tools on every surface</strong>, and a
          sandboxed computer of his own. The Python/JavaScript/SQL sandboxes,
          Lightpanda and self-evolution are gone.
        </li>
        <li>
          <strong>The heartbeat’s triage is replaced by the mind</strong>:
          wakes, follow-through, a silent nightly, a journal and (opt-in)
          noticing.
        </li>
        <li>
          <strong>@Edward pings in iMessage are answered</strong> by default
          if he reads your messages (step 8).
        </li>
        <li>
          <strong>The app is new</strong>: Today is the front door, the Edward
          tab replaces the old settings page, and the frontend runs as a
          production build.
        </li>
      </ul>

      <h2>1. Stop Edward</h2>
      <p>
        The March <code>restart.sh</code> ran the backend on :8000 and{" "}
        <code>npm run dev</code> on :3000:
      </p>
      <pre>
        <code>{`lsof -t -iTCP:8000 -sTCP:LISTEN | xargs kill
lsof -t -iTCP:3000 -sTCP:LISTEN | xargs kill`}</code>
      </pre>
      <p>
        If you made your own launchd job or login item for him, unload it
        first.
      </p>

      <h2>2. Back up</h2>
      <pre>
        <code>{`PG="$(brew --prefix postgresql@16)/bin"
mkdir -p ~/edward-backups
"$PG/pg_dump" -Fc -d edward -f ~/edward-backups/edward-march-final.dump
"$PG/pg_restore" --list ~/edward-backups/edward-march-final.dump | head

cp .env ~/edward-backups/env-march
cp backend/.jwt_secret ~/edward-backups/jwt_secret-march 2>/dev/null
cp -R backend/storage ~/edward-backups/storage-march 2>/dev/null`}</code>
      </pre>
      <p>
        Keep the dump until you have used the new Edward for a while; the
        nightly backup’s rotation never touches that name.
      </p>

      <h2>3. Pull the release</h2>
      <pre>
        <code>git pull</code>
      </pre>
      <p>
        Put any changes of your own on a branch first: this release rewrites
        most files, so expect to port them by hand. A fresh clone works just
        as well (copy <code>.env</code>, <code>backend/.jwt_secret</code> and{" "}
        <code>backend/storage/</code> into it); the database is shared, not
        part of the checkout.
      </p>

      <h2>4. Move the old Python environment aside</h2>
      <pre>
        <code>mv backend/.venv backend/.venv.march</code>
      </pre>
      <p>
        This release needs Homebrew’s Python 3.12 and installs pinned versions,
        so a clean environment is simplest.
      </p>

      <h2>5. Run setup</h2>
      <pre>
        <code>./setup.sh</code>
      </pre>
      <p>
        It keeps your <code>.env</code> and asks only for what is empty: your
        name, the API key if missing, your time zone. It builds pgvector for
        Postgres 16 when it is missing (the March setup used a Homebrew bottle
        that no longer ships), creates the venv, installs Playwright’s headless
        shell, runs <code>npm ci</code> and builds the Swift helpers. It stops
        on an Intel Mac or under Rosetta.
      </p>
      <p>
        At the end it prints the python that needs <strong>Full Disk
        Access</strong>. Grant it even if you granted one in March: the
        permission belongs to a binary’s path, and the new environment may
        run on a different python.
      </p>

      <h2>6. Update .env</h2>
      <p>
        Compare yours with{" "}
        <a href={`${GITHUB_BLOB}/.env.example`}>.env.example</a>. The backend
        reads only the <code>.env</code> at the repo root (move a{" "}
        <code>backend/.env</code> up), and a variable set in your shell or by
        launchd now wins over the file. The changes that matter:
      </p>
      <table>
        <thead>
          <tr>
            <th>Setting</th>
            <th>What to do</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>
              <code>LANGCHAIN_TRACING_V2</code>, <code>LANGCHAIN_API_KEY</code>,{" "}
              <code>LANGCHAIN_PROJECT</code>
            </td>
            <td>
              <strong>Delete.</strong> LangSmith support is gone, but LangChain
              would keep sending traces while they are set.
            </td>
          </tr>
          <tr>
            <td><code>GITHUB_TOKEN</code></td>
            <td>Delete; nothing reads it.</td>
          </tr>
          <tr>
            <td>
              <code>EDWARD_OWNER_NAME</code>, <code>EDWARD_OWNER_HANDLES</code>,{" "}
              <code>EDWARD_OWNER_EMAILS</code>, <code>TIMEZONE</code>
            </td>
            <td>
              New. Who you are, and how he recognises your own messages.
            </td>
          </tr>
          <tr>
            <td><code>EDWARD_INSTRUCTION_HORIZON_SINCE</code></td>
            <td>
              New. <strong>Set it to the day you upgrade</strong> (e.g.{" "}
              <code>2026-10-12</code>), so the weekly memory upkeep leaves the
              instructions March stored alone instead of letting the ones it
              can’t trace to you lapse.
            </td>
          </tr>
          <tr>
            <td><code>TWILIO_*</code></td>
            <td>
              Webhooks now check Twilio’s signature and need{" "}
              <code>TWILIO_AUTH_TOKEN</code>; only your own numbers get an
              answer (<code>EDWARD_TWILIO_ANSWER_ANYONE=on</code> brings back
              answering everyone, at a risk).
            </td>
          </tr>
          <tr>
            <td><code>HTML_HOSTING_URL</code>, <code>HTML_HOSTING_API_KEY</code></td>
            <td>No default host any more; set both if you used hosted pages.</td>
          </tr>
          <tr>
            <td><code>EDWARD_PUBLIC_BASE_URL</code>, <code>EDWARD_COOKIE_SECURE</code></td>
            <td>
              New. Your tunnel’s https address, if you use one; the cookie
              setting only for a plain-HTTP network you trust.
            </td>
          </tr>
          <tr>
            <td><code>EDWARD_DAILY_SPEND_CAP_USD</code></td>
            <td>New, default 5. March had no cap.</td>
          </tr>
        </tbody>
      </table>

      <h2>7. Start him</h2>
      <pre>
        <code>{`bash scripts/install-launchagents.sh   # always on
# or
./restart.sh                           # by hand`}</code>
      </pre>
      <p>
        The first start builds the frontend and migrates the database; watch{" "}
        <code>/tmp/edward-backend.log</code>. Sign in with your March password.
        The one-time setup code is only for an install with no password; if
        your March Edward never had one, set it on the Mac with{" "}
        <code>cat backend/.setup_code</code>.
      </p>

      <h2>8. Check what he listens to</h2>
      <ul>
        <li>
          <strong>Mind → Skills.</strong> Your March switches carry over.
          Shell/Bash is now his whole computer, Web Search gates search and
          fetch, iMessage gates reading and sending. Code Interpreter,
          JavaScript and SQL say “Retired”.
        </li>
        <li>
          <strong>Attention → Listening.</strong> March turned Messages on by
          default. If Edward read your iMessages then, he reads them now, and
          he now <strong>answers @Edward in any chat, from anyone</strong>{" "}
          (friends inside limits, unknown numbers held for you), from your own
          iMessage account. If you don’t want that, turn Messages off in its
          Configuration view;{" "}
          <code>EDWARD_PINGS=off</code> alone is not enough.
        </li>
        <li>
          <strong>House → Locale.</strong> Check the time zone: reminders,
          quiet hours, the nightly and “today” follow it.
        </li>
        <li>
          <strong>Work → Events.</strong> Your reminders and recurring tasks
          are kept; check their times in your zone.
        </li>
      </ul>
      <p>
        Two optional steps (adopting this release’s persona, and backfills
        that tidy March data) are in{" "}
        <a href={UPGRADING}>UPGRADING.md</a>, as is{" "}
        <strong>rolling back</strong> to March.
      </p>

      <h2 id="removed">What is gone</h2>
      <table>
        <thead>
          <tr>
            <th>March feature</th>
            <th>Now</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Self-evolution (Edward proposing, testing and deploying changes to his own code)</td>
            <td>
              Gone, its tables dropped. He keeps a <strong>wishlist</strong>{" "}
              for you instead (Work → Wishlist) and never promises to build
              what he wishes for.
            </td>
          </tr>
          <tr>
            <td>Browser automation through Lightpanda</td>
            <td>
              Gone. His own browser is Playwright’s headless shell, and with{" "}
              <code>EDWARD_CHROME_HANDS=on</code> he can use your Chrome.
            </td>
          </tr>
          <tr>
            <td>Python, JavaScript and SQL sandboxes</td>
            <td>Gone. His sandboxed shell runs python, node and sqlite3.</td>
          </tr>
          <tr>
            <td>LangSmith tracing</td>
            <td>
              Gone. Each turn’s reasoning, tools and memory hits are stored
              with the answer and shown in the chat’s work rail.
            </td>
          </tr>
          <tr>
            <td>Morning briefing, meeting prep, open-loop nudges, pattern detection</td>
            <td>
              Gone. Wakes (follow-through, the nightly) and opt-in noticing
              replace them.
            </td>
          </tr>
          <tr>
            <td>The heartbeat’s triage and its calendar listener</td>
            <td>
              Gone. Listening stores what arrives and hands it to the mind; the
              calendar is read when needed.
            </td>
          </tr>
          <tr>
            <td>Self-service install of MCP servers</td>
            <td>
              Edward can’t install servers himself any more. Servers you
              installed still load, and you manage them in Mind → Servers.
            </td>
          </tr>
          <tr>
            <td>The persistent-database, file-storage and hosted-page tools</td>
            <td>
              Out of his toolset (he has his own home folder). Your saved
              files and databases are kept and browsable in the Memory room.
            </td>
          </tr>
        </tbody>
      </table>

      <h2 id="dormant">What is dormant</h2>
      <p>
        <strong>Workers and coding sessions.</strong> The orchestrator, its
        routes and the Workers panel are still in the tree, and March tasks
        stay in their table, but no tool in the default toolset starts a
        worker any more: long work is a background job in his own shell plus
        a scheduled wake. The coding-session code now drives the Codex CLI
        inside a sandbox, and is equally dormant.
      </p>

      <h2>What is new</h2>
      <ul>
        <li>
          <strong>Memory with provenance</strong>: who said it, about whom, a
          tier, a source; weekly upkeep that never deletes; one question at a
          time about contradictions; a forget that previews exactly what it
          removes. <Link href="/docs/memory">Memory</Link>
        </li>
        <li>
          <strong>His computer</strong>: a sandboxed shell, an editor, web
          search and fetch, a headless browser, <code>look</code>.{" "}
          <Link href="/docs/computer">His computer</Link>
        </li>
        <li>
          <strong>The mind</strong>: wakes, follow-through, the nightly, free
          time, a journal with your verdicts, opt-in noticing.{" "}
          <Link href="/docs/mind">The mind</Link>
        </li>
        <li>
          <strong>Pings and media in iMessage</strong>: @Edward in any chat;
          photos, PDFs, voice messages and videos understood.{" "}
          <Link href="/docs/listening">Listening &amp; pings</Link>
        </li>
        <li>
          <strong>The app</strong>: Today, a work rail that shows his thinking
          live, live voice, the Edward tab, <code>/screen</code>, the household
          hub, push with quiet hours. <Link href="/docs/app">The app</Link>
        </li>
        <li>
          <strong>Guard rails</strong>: a secret scan on everything that
          leaves, spend caps, a one-time setup code, a sandbox for his shell,
          friends’ pings without your machine, Twilio answering only you.{" "}
          <Link href="/docs/guard-rails">Guard rails</Link>
        </li>
        <li>
          <strong>Running it</strong>: LaunchAgents, a health watchdog,
          nightly verified backups, staged frontend deploys with a one-command
          rollback.
        </li>
      </ul>
    </DocsContent>
  );
}
