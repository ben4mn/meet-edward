import { DocsContent } from "../../../components/docs/DocsContent";

export const metadata = {
  title: "Orchestrator & Claude Code — Edward Docs",
};

export default function OrchestratorPage() {
  return (
    <DocsContent>
      <h1>Orchestrator &amp; Claude Code</h1>
      <p className="subtitle">
        Worker agents and Claude Code sessions for autonomous, parallel task
        execution.
      </p>

      <h2>Overview</h2>
      <p>
        The orchestrator lets Edward spawn lightweight worker agents that run
        autonomously within his process. Each worker is a mini-Edward with full
        tool access, memory retrieval, and conversation persistence. For coding
        tasks, Edward can also delegate to Claude Code sessions via the{" "}
        <code>claude-agent-sdk</code>.
      </p>

      <h2>Worker Agents</h2>
      <p>
        Workers are spawned via <code>chat_with_memory()</code> — the same
        function used for normal conversations. This means workers have:
      </p>
      <ul>
        <li>Full access to all enabled tools (messaging, search, code execution, etc.)</li>
        <li>Memory retrieval and extraction (they can remember and recall)</li>
        <li>Their own conversation thread, visible in the sidebar</li>
        <li>State persistence via LangGraph checkpoints</li>
      </ul>
      <p>
        Worker conversations are tagged with <code>source: &quot;orchestrator_worker&quot;</code>{" "}
        so they appear distinctly in the sidebar.
      </p>

      <h2>Context Modes</h2>
      <p>
        When spawning a worker, you can control how much context it receives:
      </p>
      <table>
        <thead>
          <tr>
            <th>Mode</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>full</code></td>
            <td>Worker gets the complete parent conversation as context</td>
          </tr>
          <tr>
            <td><code>scoped</code></td>
            <td>Worker gets only the task description and relevant snippets</td>
          </tr>
          <tr>
            <td><code>none</code></td>
            <td>Worker starts with a blank slate — task description only</td>
          </tr>
        </tbody>
      </table>

      <h2>Claude Code Sessions</h2>
      <p>
        For coding tasks that require file editing, test running, and iterative
        development, Edward delegates to Claude Code via the{" "}
        <code>claude-agent-sdk</code>. Claude Code sessions:
      </p>
      <ul>
        <li>Run as separate processes with their own context</li>
        <li>Can read, write, and execute code in the repository</li>
        <li>Stream events back to the frontend in real-time</li>
        <li>Are tracked in the <code>claude_code_sessions</code> database table</li>
      </ul>
      <p>
        The frontend renders Claude Code session events inline in the chat UI
        via the <code>CCSessionBlock</code> component.
      </p>

      <h2>Concurrency</h2>
      <p>
        Workers and Claude Code sessions use separate concurrency semaphores to
        prevent either from starving the other:
      </p>
      <table>
        <thead>
          <tr>
            <th>Pool</th>
            <th>Default Limit</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Workers</td>
            <td>5</td>
            <td>Internal mini-Edward agents</td>
          </tr>
          <tr>
            <td>Claude Code</td>
            <td>2</td>
            <td>External Claude Code sessions</td>
          </tr>
        </tbody>
      </table>

      <h2>Task Lifecycle</h2>
      <pre><code>{`pending → running → completed
                  → failed
                  → cancelled`}</code></pre>
      <p>
        Tasks start as <code>pending</code> and move to <code>running</code>{" "}
        when picked up by a worker or Claude Code session. They resolve to{" "}
        <code>completed</code>, <code>failed</code>, or <code>cancelled</code>.
        Failed tasks include error details. Cancelled tasks are stopped
        gracefully when possible.
      </p>

      <h2>Configuration</h2>
      <p>
        Orchestrator settings are managed via{" "}
        <code>PATCH /api/orchestrator/config</code> or the settings UI:
      </p>
      <table>
        <thead>
          <tr>
            <th>Field</th>
            <th>Default</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>enabled</code></td>
            <td><code>true</code></td>
            <td>Master switch for the orchestrator</td>
          </tr>
          <tr>
            <td><code>max_concurrent_workers</code></td>
            <td><code>5</code></td>
            <td>Maximum parallel worker agents</td>
          </tr>
          <tr>
            <td><code>max_concurrent_cc</code></td>
            <td><code>2</code></td>
            <td>Maximum parallel Claude Code sessions</td>
          </tr>
          <tr>
            <td><code>auto_recover</code></td>
            <td><code>true</code></td>
            <td>Recover crashed tasks on startup</td>
          </tr>
        </tbody>
      </table>
    </DocsContent>
  );
}
