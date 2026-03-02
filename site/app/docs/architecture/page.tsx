import { DocsContent } from "../../../components/docs/DocsContent";

export const metadata = {
  title: "Architecture — Edward Docs",
};

export default function ArchitecturePage() {
  return (
    <DocsContent>
      <h1>Architecture</h1>
      <p className="subtitle">
        How Edward is structured under the hood.
      </p>

      <h2>System Overview</h2>
      <pre><code>{`Frontend (Next.js :3000)  →  Backend (FastAPI :8000)  →  PostgreSQL (:5432)
                                      ↓
                              LangGraph Agent
                                      ↓
                              Claude API (Anthropic)`}</code></pre>
      <p>
        The frontend is a Next.js app that communicates with the FastAPI backend
        via REST and SSE (Server-Sent Events) for streaming. The backend manages
        all state in PostgreSQL, including conversation checkpoints, memories,
        documents, and configuration.
      </p>

      <h2>LangGraph Flow</h2>
      <p>
        Every message goes through a four-node LangGraph pipeline:
      </p>
      <pre><code>{`preprocess → retrieve_memory → respond → extract_memory → END`}</code></pre>
      <ol>
        <li><strong>preprocess</strong> — normalizes the input, sets up conversation metadata</li>
        <li><strong>retrieve_memory</strong> — searches pgvector for relevant memories using hybrid 70% vector + 30% BM25 scoring</li>
        <li><strong>respond</strong> — calls Claude with the full context and tool bindings, enters a tool loop (up to 5 iterations)</li>
        <li><strong>extract_memory</strong> — uses Claude Haiku to identify any memorable information from the conversation and stores it</li>
      </ol>

      <h3>Tool Loop</h3>
      <p>
        Inside the <code>respond</code> node, Edward can call tools iteratively:
      </p>
      <pre><code>{`LLM response
    ↓
tool_calls? ──yes──> execute tools
    │                     ↓
    no              add ToolMessage
    ↓                     ↓
 stream response    loop (max 5x)`}</code></pre>
      <p>
        This allows Edward to chain actions — for example, searching the web,
        reading the page content, then summarizing it — all within a single
        conversation turn.
      </p>

      <h2>Memory System</h2>
      <ul>
        <li><strong>Embedding model</strong>: sentence-transformers (<code>all-MiniLM-L6-v2</code>, 384 dimensions)</li>
        <li><strong>Search</strong>: Hybrid 70% vector similarity + 30% BM25 keyword matching</li>
        <li><strong>Memory types</strong>: <code>fact</code>, <code>preference</code>, <code>context</code>, <code>instruction</code></li>
        <li><strong>Extraction</strong>: Claude Haiku identifies memorable info after each turn</li>
        <li><strong>Deep Retrieval</strong>: For complex conversations, runs 4 parallel queries (original + 3 Haiku-rewritten) for richer context</li>
      </ul>

      <h2>Background Systems</h2>
      <p>
        Edward runs several background loops that operate independently of user
        conversations:
      </p>

      <h3>Heartbeat</h3>
      <p>
        Monitors iMessage, Apple Calendar, and Apple Mail for incoming items.
        Multi-layer triage classifies urgency: Layer 1 (zero-cost rules) →
        Layer 2 (Haiku classification) → Layer 3 (execute action). Can ignore,
        remember, respond, or push-notify based on classification.
      </p>

      <h3>Reflection</h3>
      <p>
        Post-turn enrichment that generates 3-5 Haiku queries to find related
        memories. Results are stored and loaded on the next turn for deeper
        context. Runs asynchronously — no latency impact.
      </p>

      <h3>Consolidation</h3>
      <p>
        Hourly background loop that clusters related memories via Haiku. Creates
        connections between related memories and flags quality/staleness issues.
        Disabled by default.
      </p>

      <h3>Scheduler</h3>
      <p>
        In-process asyncio loop that polls every 30 seconds for due events.
        Executes scheduled events via the same <code>chat_with_memory()</code>{" "}
        function used in conversation — meaning Edward has full tool access when
        processing scheduled actions.
      </p>

      <h3>Orchestrator</h3>
      <p>
        Spawns lightweight worker agents (mini-Edwards) that run within Edward&apos;s
        process. Workers have full tool access, memory retrieval, and state
        persistence. Worker conversations appear in the sidebar with a distinct
        source tag.
      </p>

      <h2>Key Directories</h2>
      <pre><code>{`meet-edward/
├── frontend/              # Next.js app
│   ├── app/               # Pages and routes
│   ├── components/        # React components
│   └── lib/               # API client, context, utilities
├── backend/
│   ├── main.py            # FastAPI app + lifespan
│   ├── routers/           # API route handlers
│   ├── services/
│   │   ├── graph/         # LangGraph agent (nodes, state, tools)
│   │   ├── execution/     # Code execution sandboxes
│   │   ├── heartbeat/     # Message monitoring + triage
│   │   ├── memory_service.py
│   │   ├── document_service.py
│   │   ├── tool_registry.py
│   │   ├── skills_service.py
│   │   └── ...
│   └── start.sh           # Backend startup script
├── site/                  # Marketing site (meet-edward.com)
├── setup.sh               # First-time installation
└── restart.sh             # Service management`}</code></pre>

      <h2>Startup Order</h2>
      <p>
        The backend initializes components in a specific order (defined in{" "}
        <code>main.py</code> lifespan). The tool registry must come after all
        tool sources are initialized:
      </p>
      <ol>
        <li><strong>Database + LangGraph</strong> — tables and checkpoint store</li>
        <li><strong>Skills</strong> — load enabled states</li>
        <li><strong>MCP clients</strong> — WhatsApp, Apple Services subprocesses</li>
        <li><strong>Custom MCP servers</strong> — user-added servers from DB</li>
        <li><strong>Tool registry</strong> — must be after all tool sources</li>
        <li><strong>Scheduler</strong> — polls for due events</li>
        <li><strong>Heartbeat</strong> — iMessage listener + triage loop</li>
        <li><strong>Consolidation</strong> — hourly memory clustering</li>
        <li><strong>Evolution</strong> — check for pending deploys</li>
        <li><strong>Orchestrator</strong> — recover crashed worker tasks</li>
      </ol>
      <p>All have matching shutdown hooks in reverse order.</p>

      <h2>SSE Event Protocol</h2>
      <p>
        The chat endpoint (<code>POST /api/chat</code>) streams structured events
        for real-time UI updates:
      </p>
      <table>
        <thead>
          <tr>
            <th>Event</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>thinking</code></td>
            <td>LLM is processing (between tool calls)</td>
          </tr>
          <tr>
            <td><code>tool_start</code></td>
            <td>Tool execution beginning</td>
          </tr>
          <tr>
            <td><code>code</code></td>
            <td>Code/query/command to execute</td>
          </tr>
          <tr>
            <td><code>execution_output</code></td>
            <td>stdout/stderr from code execution</td>
          </tr>
          <tr>
            <td><code>execution_result</code></td>
            <td>Code execution completed</td>
          </tr>
          <tr>
            <td><code>tool_end</code></td>
            <td>Tool execution finished</td>
          </tr>
          <tr>
            <td><code>content</code></td>
            <td>Text content from LLM response</td>
          </tr>
          <tr>
            <td><code>done</code></td>
            <td>Stream complete</td>
          </tr>
        </tbody>
      </table>
    </DocsContent>
  );
}
