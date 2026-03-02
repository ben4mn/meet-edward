import { DocsContent } from "../../../components/docs/DocsContent";

export const metadata = {
  title: "Heartbeat — Edward Docs",
};

export default function HeartbeatPage() {
  return (
    <DocsContent>
      <h1>Heartbeat</h1>
      <p className="subtitle">
        Edward&apos;s proactive monitoring system — listening for messages,
        calendar events, and emails without being asked.
      </p>

      <h2>Overview</h2>
      <p>
        The heartbeat system lets Edward act on incoming information before
        you open the chat. It monitors iMessage, Apple Calendar, and Apple
        Mail, triaging each event through a multi-layer classification
        pipeline to decide whether to ignore, remember, respond, or alert you.
      </p>

      <h2>Architecture</h2>
      <pre><code>{`┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│  iMessage   │  │  Calendar   │  │    Email    │
│  Listener   │  │  Listener   │  │  Listener   │
└──────┬──────┘  └──────┬──────┘  └──────┬──────┘
       │                │                │
       └────────────────┼────────────────┘
                        ↓
              ┌───────────────────┐
              │   Triage Engine   │
              │  L1 → L2 → L3    │
              └────────┬──────────┘
                       ↓
        ┌──────────────┼──────────────┐
        ↓              ↓              ↓
   [DISMISS]      [NOTE/ACT]    [ESCALATE]
                  memory/reply   reply + push`}</code></pre>

      <h2>Listeners</h2>

      <h3>iMessage</h3>
      <p>
        Polls the macOS Messages database (<code>~/Library/Messages/chat.db</code>)
        every 10 seconds for new incoming messages. Uses Apple&apos;s timestamp
        format (nanoseconds since 2001-01-01). Only picks up messages from other
        people — Edward&apos;s own outbound messages are ignored.
      </p>

      <h3>Calendar</h3>
      <p>
        Polls Apple Calendar via MCP tools at a configurable interval (default
        300 seconds). Looks ahead a configurable number of minutes for upcoming
        events. Useful for proactive reminders about meetings or appointments.
      </p>

      <h3>Email</h3>
      <p>
        Polls Apple Mail for unread emails. Filters for messages that mention{" "}
        <code>@edward</code> to avoid noise. Only processes emails that
        explicitly invoke Edward.
      </p>

      <h2>3-Layer Triage</h2>
      <p>
        Every incoming event passes through up to three triage layers, stopping
        as soon as a definitive action is determined:
      </p>

      <h3>Layer 1 — Rules (Zero Cost)</h3>
      <p>
        Fast pattern matching with no LLM calls. Catches obvious cases like
        known spam senders, automated notifications, or messages from Edward
        himself. Events that pass Layer 1 move to Layer 2.
      </p>

      <h3>Layer 2 — Haiku Classification</h3>
      <p>
        Claude Haiku classifies the event&apos;s urgency and determines what
        action to take. This is the primary decision point for most messages.
        Classification outputs one of the action types below.
      </p>

      <h3>Layer 3 — Execute</h3>
      <p>
        Carries out the determined action — storing a memory, sending a response
        via <code>chat_with_memory()</code>, or pushing a notification.
      </p>

      <h2>Actions</h2>
      <table>
        <thead>
          <tr>
            <th>Action</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>DISMISS</code></td>
            <td>Ignore the event — no further processing</td>
          </tr>
          <tr>
            <td><code>NOTE</code></td>
            <td>Store the information as a memory for future reference</td>
          </tr>
          <tr>
            <td><code>ACT</code></td>
            <td>Respond to the message using Edward&apos;s full tool access</td>
          </tr>
          <tr>
            <td><code>ESCALATE</code></td>
            <td>Respond <em>and</em> send a push notification to alert you</td>
          </tr>
        </tbody>
      </table>

      <h2>@edward Mentions</h2>
      <p>
        Messages containing <code>@edward</code> bypass the normal triage flow
        entirely and are routed directly to Layer 3 for immediate response. This
        lets anyone in a group chat summon Edward by name.
      </p>

      <h2>Listening Windows</h2>
      <p>
        When Edward responds to a message, a 5-minute follow-up window opens for
        that conversation. Any replies during this window are treated as
        continuations rather than new events, keeping the conversation coherent.
      </p>

      <h2>Active Chat Gating</h2>
      <p>
        When you&apos;re actively chatting with Edward in the web UI, the triage
        system pauses to avoid duplicate responses. It resumes automatically when
        the chat goes idle.
      </p>

      <h2>Briefing Injection</h2>
      <p>
        Pending heartbeat events that require attention are injected into
        Edward&apos;s system prompt. When you open the chat, Edward can
        proactively brief you on things that happened while you were away.
      </p>

      <h2>Configuration</h2>
      <p>
        Heartbeat settings are managed via <code>PATCH /api/heartbeat/config</code>{" "}
        or the settings UI:
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
            <td>Master switch for the heartbeat system</td>
          </tr>
          <tr>
            <td><code>poll_seconds</code></td>
            <td><code>10</code></td>
            <td>iMessage polling interval</td>
          </tr>
          <tr>
            <td><code>calendar_enabled</code></td>
            <td><code>true</code></td>
            <td>Enable calendar listener</td>
          </tr>
          <tr>
            <td><code>calendar_poll_seconds</code></td>
            <td><code>300</code></td>
            <td>Calendar polling interval</td>
          </tr>
          <tr>
            <td><code>calendar_lookahead_minutes</code></td>
            <td><code>30</code></td>
            <td>How far ahead to look for events</td>
          </tr>
          <tr>
            <td><code>email_enabled</code></td>
            <td><code>true</code></td>
            <td>Enable email listener</td>
          </tr>
          <tr>
            <td><code>email_poll_seconds</code></td>
            <td><code>300</code></td>
            <td>Email polling interval</td>
          </tr>
        </tbody>
      </table>
    </DocsContent>
  );
}
