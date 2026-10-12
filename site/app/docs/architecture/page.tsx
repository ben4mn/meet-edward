import type { Metadata } from "next";
import Link from "next/link";
import { DocsContent } from "../../../components/docs/DocsContent";
import { GITHUB_BLOB, GITHUB_TREE } from "../../../lib/docsNav";

export const metadata: Metadata = {
  title: "Architecture — Edward Docs",
  description:
    "How Edward is built: a Next.js PWA, a FastAPI backend that runs natively on macOS, PostgreSQL with pgvector, a LangGraph turn with one tool loop and 14 tools, the Claude API through one adapter, background loops, the startup order, the chat stream's events, and how it is run and tested.",
  alternates: { canonical: "/docs/architecture" },
  openGraph: {
    title: "Architecture — Edward Docs",
    description:
      "Next.js PWA, FastAPI on macOS, PostgreSQL + pgvector, a LangGraph turn with 14 tools, and the Claude API.",
    url: "/docs/architecture",
  },
};

const DIAGRAM = `Next.js PWA (:3000)  ──/api──▶  FastAPI (:8000)  ──▶  PostgreSQL 16 + pgvector (:5432)
Today · Chat · Voice                  │
Edward tab · /screen · /hub           │
                                      ├─ a turn (LangGraph): preprocess → retrieve_memory → respond (tool loop) → extract_memory
                                      ├─ one toolset of 14 tools; bash runs in a Seatbelt sandbox in ~/edward-home
                                      ├─ listeners: iMessage (chat.db), Mail, Gmail → the mind (wakes, noticing, pings)
                                      ├─ scheduler (every 30 s), consolidation (hourly), the screens' pulse
                                      └─ the Claude API: ChatClaude, one route per purpose, a daily dollar cap`;

const EVENTS: [string, string][] = [
  ["user_message", "A voice memo’s transcript and audio, before the answer"],
  ["progress", "Steps such as memory_search, tool_execution, generating"],
  ["memory_match", "One per retrieved memory (debug streaming only)"],
  ["thinking", "The model is working between tool calls"],
  ["reasoning", "Summarized reasoning, as it streams"],
  ["tool_args_delta", "Partial JSON of a tool call being written"],
  ["tool_start / tool_end", "A tool call begins / finishes (result ≤ 500 chars)"],
  ["code, execution_output, execution_result", "A command, its output, its result"],
  ["plan_created, plan_updated, plan_step_update, plan_completed", "His visible plan"],
  ["cc_session_start, cc_text, cc_tool_use, cc_tool_result, cc_session_end", "A Codex session (dormant)"],
  ["content", "Text of the answer"],
  ["done", "Stream complete: the checkpoint is written, the thread is free"],
];

export default function ArchitecturePage() {
  return (
    <DocsContent>
      <h1>Architecture</h1>
      <p className="subtitle">How Edward is built, run and tested.</p>

      <h2>Overview</h2>
      <pre>
        <code>{DIAGRAM}</code>
      </pre>
      <p>
        The backend runs natively on macOS, never in a container: it reads
        Apple’s databases, talks to Messages over AppleScript, and sandboxes
        Edward’s shell with Seatbelt. The frontend is a Next.js production
        build served on :3000, which forwards <code>/api</code> to the backend.
        All state is in PostgreSQL: conversations and their LangGraph
        checkpoints, memories, documents, events, ledgers and settings.
      </p>

      <h2>A turn</h2>
      <pre>
        <code>preprocess → retrieve_memory → respond (the tool loop) → extract_memory</code>
      </pre>
      <ul>
        <li>
          <strong>One tool loop</strong> for every surface (chat, voice, pings,
          wakes, the pulse, scheduled events), with the same{" "}
          <Link href="/docs/computer">fourteen tools</Link>. What a turn may
          do is its <code>TurnPolicy</code>: whether you asked, whether it may
          reach people or push, where it may reply, its dollar cap and
          deadline. A turn you are not part of, with no policy, is fail-closed.
        </li>
        <li>
          <strong>Honest failures</strong>: every tool failure reaches the
          model as <code>Tool error: &lt;the real error&gt;</code>; the same
          failing call is refused the third time; after three failed rounds
          he is told to step back, and after six the turn stops.
        </li>
        <li>
          <strong>Bounds</strong>: 60 rounds, $3 a turn you asked for and $1
          otherwise, 300 s per model call, 600 s per streamed turn, tool
          results capped at 30K characters.
        </li>
        <li>
          <strong>One turn at a time in a thread</strong>, and Stop cancels the
          step in flight. The turn’s trace (reasoning, tools, memory hits) is
          stored with the answer, so the work rail renders the same from
          history.
        </li>
      </ul>

      <h2>The model</h2>
      <p>
        Every model call goes through one set of builders and one Claude
        adapter on the <code>anthropic</code> SDK, with a model, effort and
        prompt-cache lifetime per purpose (chat, mind, pulse, background,
        voice, judge). Before each request the adapter asks the daily spend
        cap, and after it prices the call into a ledger. Every call is
        labelled with what it was for (<code>mind:nightly</code>,{" "}
        <code>ping:friend</code>, <code>turn:chat</code>…), which is how the
        app shows where the money went. An unofficial Codex fallback exists
        behind <code>EDWARD_LLM_PROVIDER=codex</code>. Details in{" "}
        <Link href="/docs/configuration">Configuration</Link>.
      </p>

      <h2>Running in the background</h2>
      <table>
        <thead>
          <tr>
            <th>Loop</th>
            <th>What it does</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Scheduler</td>
            <td>
              Polls every 30 s; each due event runs as its own task, in your
              time zone, never the host’s. Reminders, follow-up wakes, the
              nightly and free-time ticks, a five-minute sweep, the pulse.
            </td>
          </tr>
          <tr>
            <td>Listeners</td>
            <td>iMessage every 10 s, Mail and Gmail on their own intervals; they store events and nothing else</td>
          </tr>
          <tr>
            <td>Ping runner</td>
            <td>Answers each <code>@Edward</code> in its own turn, two at a time, never two in one chat</td>
          </tr>
          <tr>
            <td>The mind</td>
            <td>Wakes, the noticer and the speak gate (<Link href="/docs/mind">The mind</Link>)</td>
          </tr>
          <tr>
            <td>The pulse</td>
            <td>Every 15 minutes, a model turn only when a surface’s inputs changed</td>
          </tr>
          <tr>
            <td>Consolidation</td>
            <td>Hourly memory upkeep (<Link href="/docs/memory">Memory</Link>)</td>
          </tr>
          <tr>
            <td>The sweep</td>
            <td>Every five minutes, with no model: the late-dose alert, noticing upkeep, snoozes</td>
          </tr>
          <tr>
            <td>Graph worker</td>
            <td>Optional, its own LaunchAgent: fills the knowledge graph</td>
          </tr>
        </tbody>
      </table>

      <h2>Startup order</h2>
      <p>
        <code>main.py</code>’s lifespan starts things in an order that
        matters: the tool registry must come after skills and every MCP
        source.
      </p>
      <ol>
        <li>Logging, the database and the LangGraph checkpointer (tables, migrations, drops)</li>
        <li>The time zone cache, the embedding model, the Codex rate gate (Codex only)</li>
        <li>The storage directory and skills</li>
        <li>Built-in MCP clients (after an orphan sweep), custom MCP servers, a second skills sweep</li>
        <li>The tool registry</li>
        <li>Edward’s computer: his home, the Seatbelt profile, a sweep of leftover shells</li>
        <li>The scheduler</li>
        <li>The listeners and the mind path, the presence probe</li>
        <li>The ping runner</li>
        <li>Browser hands (a no-op unless they are on)</li>
        <li>Consolidation (after 60 s)</li>
        <li>The orchestrator (recovers crashed worker tasks; dormant)</li>
        <li>Greeting pre-warm</li>
      </ol>
      <p>Shutdown runs the matching hooks in reverse, his computer first.</p>

      <h2>The chat stream</h2>
      <p>
        <code>POST /api/chat</code> streams Server-Sent Events,{" "}
        <code>data: {"{json}"}</code>, plus a heartbeat comment every 10 s
        when idle. Every event has a <code>type</code> and a{" "}
        <code>conversation_id</code>; the frontend folds them into one live
        bubble. A crash mid-stream becomes a spoken-style apology and{" "}
        <code>done</code>; a client that leaves gets nothing more, and the
        step in flight is cancelled.
      </p>
      <table>
        <thead>
          <tr>
            <th>Event</th>
            <th>Meaning</th>
          </tr>
        </thead>
        <tbody>
          {EVENTS.map(([name, meaning]) => (
            <tr key={name}>
              <td><code>{name}</code></td>
              <td>{meaning}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>The tree</h2>
      <pre>
        <code>{`meet-edward/
├── backend/            FastAPI: main.py, routers/, services/, tests/, scripts/
│   └── services/
│       ├── graph/      the turn: the tool loop, streaming, the prompt
│       ├── toolset/    the 14 tools and TurnPolicy
│       ├── computer/   his home, shell, editor and the Seatbelt rails
│       ├── heartbeat/  the listeners and the mind path
│       ├── pings/      the @Edward runner
│       ├── media/      photos, PDFs, voice messages, video
│       ├── voice/      live voice
│       └── …           memory, mind, noticing, reach, scheduler, hub, screen
├── frontend/           the Next.js PWA (app/, components/, lib/, __tests__/)
├── chrome-extension/   his hands in your Chrome (optional)
├── companion/          the menu-bar companion
├── scriptable/         the iPhone widget
├── scripts/            LaunchAgents, the watchdog, the test cluster
├── docs/               ADAPTING, UPGRADING, architecture/
├── site/               meet-edward.com
├── setup.sh
└── restart.sh`}</code>
      </pre>

      <h2>Running it</h2>
      <ul>
        <li>
          <strong>LaunchAgents</strong> keep the backend and frontend alive,
          and run a health check every five minutes (Postgres, backend,
          frontend, the tunnel, the graph worker and the scheduler, bottom
          up, never restarting the app onto a dead dependency), a deeper one
          at 08:00, a verified nightly backup and a daily memory report.
        </li>
        <li>
          <strong>The frontend is a production build.</strong>{" "}
          <code>frontend/deploy-build.sh</code> builds the next version beside
          the running one and swaps it in on restart;{" "}
          <code>frontend/rollback-build.sh</code> swaps back. Open tabs reload
          onto a new build only when you are idle.
        </li>
        <li>
          <strong>The live checkout is production</strong>: edits there are
          live at the next restart, so build and experiment in a git worktree.
        </li>
      </ul>

      <h2>Testing</h2>
      <ul>
        <li>
          Backend tests run only on their own Postgres cluster on :5433,
          through <code>scripts/with-test-db-lock.sh</code>, never with the
          live database exported. They can never reach a real model, Chrome,
          Messages, your calendar or the embedding model.
        </li>
        <li>
          Frontend tests are <code>node --test</code>, with guardrail tests
          that pin real contracts (every tool has a work-rail label, one
          clock format, every settings panel in a room).
        </li>
        <li>
          A UX fixture harness runs the real app against a mock API with
          invented content, for screenshots and design work.
        </li>
        <li>
          Evaluation gates run the real model over recorded situations, with
          every outward act a dry run, before the mind, pings or noticing
          change.
        </li>
        <li>
          A doc lint fails when the docs name a path, route or event that
          doesn’t exist.
        </li>
      </ul>

      <h2>Read further</h2>
      <ul>
        <li>
          <a href={`${GITHUB_BLOB}/CLAUDE.md`}>CLAUDE.md</a>: the operating
          guide, every rule that must hold, every route and setting. Written
          for Claude sessions working in the repo, and just as useful for
          people.
        </li>
        <li>
          <a href={`${GITHUB_TREE}/docs/architecture`}>docs/architecture/</a>:
          one page per subsystem, with the reasons and incidents behind each
          rule.
        </li>
        <li>
          <a href={`${GITHUB_BLOB}/docs/architecture/api.md`}>api.md</a>:
          every route and socket.
        </li>
        <li>
          <a href={`${GITHUB_BLOB}/CONTRIBUTING.md`}>CONTRIBUTING.md</a>:
          setup for contributors, the checks a change must pass, and the
          privacy rules.
        </li>
      </ul>
    </DocsContent>
  );
}
