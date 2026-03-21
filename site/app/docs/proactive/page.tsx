import type { Metadata } from "next";
import Link from "next/link";
import { DocsContent } from "../../../components/docs/DocsContent";

export const metadata: Metadata = {
  title: "Proactive Intelligence — Edward Docs",
  description: "Edward's proactive background services — morning briefings, meeting prep, open loop nudges, weekly pattern detection, entity tracking, and session continuity.",
  alternates: { canonical: "/docs/proactive" },
  openGraph: {
    title: "Proactive Intelligence — Edward Docs",
    description: "Autonomous background services — briefings, meeting prep, open loop nudges, and pattern detection.",
    url: "/docs/proactive",
  },
};

export default function ProactivePage() {
  return (
    <DocsContent>
      <h1>Proactive Intelligence</h1>
      <p className="subtitle">
        Autonomous background services that act without being asked.
      </p>

      <h2>Overview</h2>
      <p>
        Proactive intelligence is a suite of background services that run on
        their own schedules. Unlike the heartbeat system (which reacts to
        incoming messages), proactive services <em>initiate</em> actions based
        on time, patterns, and context. All services are disabled by default
        and can be enabled from the Proactive panel in Settings.
      </p>

      <h2>Morning Briefing</h2>
      <p>
        A daily summary delivered at a configurable time (default 8:00 AM).
        The briefing collects:
      </p>
      <ul>
        <li>Today&apos;s calendar events (via Apple Calendar MCP)</li>
        <li>Active open loops (unresolved items)</li>
        <li>Recent heartbeat activity</li>
      </ul>
      <p>
        The raw data is summarized by the background LLM into 3-5 bullet
        points and delivered via push notification, iMessage, or in-app chat
        (configurable).
      </p>

      <h2>Meeting Prep</h2>
      <p>
        Polls Apple Calendar every 2 minutes. When a meeting is approaching
        within the configured advance window (default 30 minutes), Edward:
      </p>
      <ol>
        <li>Extracts attendee names from the calendar event</li>
        <li>Detects known entities in the meeting title and attendees</li>
        <li>Loads entity-linked memories for context</li>
        <li>Generates a prep brief via the background LLM</li>
        <li>Delivers via push notification</li>
      </ol>
      <p>
        Each event is only prepped once (dedup by event ID + title).
        Requires the Apple Services skill to be enabled.
      </p>

      <h2>Open Loop Nudges</h2>
      <p>
        A daily check for stale open loops — unresolved items that Edward
        detected in conversations. When an open loop has been active for
        longer than the configured threshold (default 7 days) and hasn&apos;t
        been nudged recently, Edward generates a gentle reminder and delivers
        it via push notification.
      </p>
      <p>
        Open loops can be resolved or snoozed from the Proactive panel in
        Settings. Snoozed loops are automatically reactivated when their
        snooze period expires.
      </p>

      <h2>Pattern Detection</h2>
      <p>
        A weekly analysis that examines the last 7 days of activity:
      </p>
      <ul>
        <li>Conversation titles and search tags</li>
        <li>Memory type distribution (facts vs. context vs. preferences)</li>
        <li>Top entities by recent mention count</li>
      </ul>
      <p>
        The background LLM identifies recurring themes, frequency changes,
        and unresolved threads. Results are stored as a document (tagged
        &quot;pattern, weekly, system_generated&quot;) and optionally delivered
        via push notification.
      </p>

      <h2>Entity Tracking</h2>
      <p>
        The entity service extracts people, projects, places, organizations,
        and topics from conversations using the background LLM. Entities are:
      </p>
      <ul>
        <li>Resolved by name and alias (case-insensitive)</li>
        <li>Linked to memories via entity mentions</li>
        <li>Tracked with mention counts and last-seen timestamps</li>
        <li>Detected at retrieval time via fast substring matching (no LLM call)</li>
      </ul>
      <p>
        Entity-linked memories are automatically merged into the conversation
        context when an entity is mentioned.
      </p>

      <h2>Session Briefing</h2>
      <p>
        When a new conversation starts, Edward generates a continuity summary
        of what happened since the last session. This is injected into the
        system prompt on the first turn and includes:
      </p>
      <ul>
        <li>New and updated memories</li>
        <li>Scheduled events that fired</li>
        <li>Completed orchestrator tasks</li>
        <li>Heartbeat triage activity</li>
        <li>Memory consolidation results</li>
      </ul>
      <p>
        This is a pure database query — no LLM call needed for structured data.
      </p>

      <h2>State Documents</h2>
      <p>
        Documents tagged with &quot;state&quot; are treated as living reference
        docs that Edward should always be aware of. They&apos;re automatically
        injected into the system prompt with a content preview. Use these for
        current goals, weekly plans, project status, or anything Edward should
        proactively reference.
      </p>

      <h2>Configuration</h2>
      <p>
        All proactive services are configured from the <strong>Proactive</strong>{" "}
        panel in the Settings page. The API endpoints are:
      </p>
      <table>
        <thead>
          <tr>
            <th>Endpoint</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>GET /api/proactive/config</code></td>
            <td>Get current configuration</td>
          </tr>
          <tr>
            <td><code>PATCH /api/proactive/config</code></td>
            <td>Update configuration (enables/disables services)</td>
          </tr>
          <tr>
            <td><code>GET /api/proactive/status</code></td>
            <td>Service status, entity count, open loop count</td>
          </tr>
          <tr>
            <td><code>GET /api/proactive/open-loops</code></td>
            <td>List open loops (filterable by status)</td>
          </tr>
          <tr>
            <td><code>PATCH /api/proactive/open-loops/:id</code></td>
            <td>Resolve or snooze an open loop</td>
          </tr>
          <tr>
            <td><code>GET /api/proactive/entities</code></td>
            <td>List tracked entities (filterable by type)</td>
          </tr>
        </tbody>
      </table>

      <h2>Environment Variables</h2>
      <p>
        The proactive system uses the <code>TIMEZONE</code> environment
        variable (default: <code>UTC</code>) for scheduling. Set this to
        your local timezone (e.g., <code>America/New_York</code>) so morning
        briefings fire at the right time.
      </p>

      <hr />
      <p>
        See the <Link href="/docs/memory">Memory System</Link> docs for how
        entity-linked memories and implications integrate with retrieval.
      </p>
    </DocsContent>
  );
}
