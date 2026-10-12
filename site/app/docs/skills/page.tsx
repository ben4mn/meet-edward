import type { Metadata } from "next";
import Link from "next/link";
import { DocsContent } from "../../../components/docs/DocsContent";
import { GITHUB_BLOB } from "../../../lib/docsNav";

export const metadata: Metadata = {
  title: "Skills & Integrations — Edward Docs",
  description:
    "Edward's skill switches and what each one gates, and his integrations: iMessage, Mail.app and Gmail, Calendar, Reminders and Contacts, Twilio, the WhatsApp bridge, apple-mcp, Alexa and Home Assistant, web search, and MCP servers you install.",
  alternates: { canonical: "/docs/skills" },
  openGraph: {
    title: "Skills & Integrations — Edward Docs",
    description:
      "The skill switches, and iMessage, mail, Apple data, Twilio, WhatsApp, Alexa, search and MCP servers.",
    url: "/docs/skills",
  },
};

export default function SkillsPage() {
  return (
    <DocsContent>
      <h1>Skills &amp; integrations</h1>
      <p className="subtitle">
        The switches, and everything Edward can connect to.
      </p>

      <h2>Skills</h2>
      <p>
        Skills are switches in the Edward tab, under{" "}
        <strong>Mind → Skills</strong>. Every one starts off on a fresh
        install. Edward’s tools are the same fourteen on every surface, and a
        skill can only take some away; each row shows a live status that says
        what is actually true (“not paired”, “credentials rejected”).
      </p>
      <table>
        <thead>
          <tr>
            <th>Skill</th>
            <th>What turning it on gives him</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><strong>Shell/Bash</strong></td>
            <td>His computer: <code>bash</code> and the file editor, in his sandbox</td>
          </tr>
          <tr>
            <td><strong>Web Search</strong></td>
            <td><code>web_search</code> and <code>web_fetch</code></td>
          </tr>
          <tr>
            <td><strong>iMessage</strong></td>
            <td>
              <code>messages</code> and <code>send_message</code>: reading and
              sending iMessages, and mail with them. Pings need it too
            </td>
          </tr>
          <tr>
            <td><strong>Push Notifications</strong></td>
            <td>An urgent <code>tell_owner</code> may buzz your phone; with it off, he leaves a note on Today instead</td>
          </tr>
          <tr>
            <td><strong>iOS Widget</strong></td>
            <td>The <code>screen</code> tool’s widget actions</td>
          </tr>
          <tr>
            <td><strong>Voice Conversations</strong></td>
            <td>Voice memos and live voice (with an ElevenLabs key)</td>
          </tr>
          <tr>
            <td>
              <strong>Code Interpreter</strong>, <strong>JavaScript</strong>,{" "}
              <strong>SQL Database</strong>
            </td>
            <td>Retired: “Edward runs this in his own shell now.” They gate nothing</td>
          </tr>
          <tr>
            <td>
              Twilio SMS and WhatsApp, WhatsApp (MCP), Contacts, Apple
              Services, HTML Hosting, Alexa, Orchestrator, Claude Code Session
            </td>
            <td>
              Status rows. Each reports whether its integration works; the
              switch doesn’t change Edward’s tools
            </td>
          </tr>
        </tbody>
      </table>
      <p>
        <code>memory</code>, <code>schedule</code>, <code>screen</code>,{" "}
        <code>look</code>, <code>plan</code>, <code>tell_owner</code>,{" "}
        <code>browser</code> and <code>chrome</code> are always present (
        <code>chrome</code> answers that the hands are off until you turn
        them on).
      </p>

      <h2>Messages and mail</h2>
      <ul>
        <li>
          <strong>iMessage</strong>: read from Messages’ own database, sent
          through AppleScript from your own Apple ID, signed as Edward. See{" "}
          <Link href="/docs/listening">Listening &amp; pings</Link>.
        </li>
        <li>
          <strong>Mail.app</strong> (iCloud or any account in it): read from
          Mail’s index on the Mac.
        </li>
        <li>
          <strong>Gmail</strong>: through Google’s API with an OAuth client of
          your own (
          <a href={`${GITHUB_BLOB}/docs/gmail-setup.md`}>gmail-setup.md</a>).
          Email he sends goes out from your Gmail through the same guarded{" "}
          <code>send_message</code>, and never to you.
        </li>
        <li>
          <strong>Twilio</strong>: SMS and WhatsApp from a number of Edward’s
          own. Signed webhooks only, answering only your numbers unless{" "}
          <code>EDWARD_TWILIO_ANSWER_ANYONE=on</code>.
        </li>
        <li>
          <strong>WhatsApp as you</strong>, through the whatsapp-mcp project’s
          Go bridge: run it yourself, scan its QR code, then{" "}
          <code>MCP_WHATSAPP_ENABLED=true</code>.
        </li>
      </ul>
      <p>
        Reading is open in every turn; what someone else wrote marks the turn
        as tainted (see <Link href="/docs/guard-rails">Guard rails</Link>).
      </p>

      <h2>Your Mac’s own data</h2>
      <ul>
        <li>
          <strong>Calendar and Reminders</strong>: read from their own
          databases (Full Disk Access); changed only when you ask, through the
          EventKit helper.
        </li>
        <li>
          <strong>Contacts</strong>: read from the AddressBook databases;
          Contacts.app only as a fallback.
        </li>
        <li>
          <strong>apple-mcp</strong> (optional, <code>MCP_APPLE_ENABLED=true</code>
          ): only a fallback for reading your calendar when Calendar’s own
          database can’t be read; its other tools are not given to Edward.
          Install bun and clone apple-mcp (its location is{" "}
          <code>MCP_APPLE_DIR</code>, default <code>~/apple-mcp</code>).
        </li>
      </ul>

      <h2>Search</h2>
      <p>
        On Claude, search and fetch are Anthropic’s server tools and need no
        other key. <strong>Brave Search</strong> (
        <code>BRAVE_SEARCH_API_KEY</code>) is used only when they are off (
        <code>EDWARD_SERVER_WEB_TOOLS=off</code>) or on the Codex fallback.
      </p>

      <h2>Your home</h2>
      <ul>
        <li>
          <strong>Alexa</strong>: a custom skill can ask Edward things (
          <a href={`${GITHUB_BLOB}/docs/alexa-setup.md`}>alexa-setup.md</a>).
          Inbound requests are not signature-verified yet, so leave{" "}
          <code>ALEXA_SKILL_ID</code> unset unless you use it.
        </li>
        <li>
          <strong>Home Assistant</strong>: announcements on Echo devices (
          <code>HOME_ASSISTANT_URL</code>, <code>HOME_ASSISTANT_TOKEN</code>).
        </li>
        <li>
          <strong>Presence</strong>: the menu-bar companion, or any script
          with your widget token, tells Edward which Mac you are at.
        </li>
      </ul>

      <h2>Custom MCP servers</h2>
      <p>
        MCP servers already installed (by a March install, for example) run
        with <code>npx</code> or <code>uvx</code>, and their tools are bound
        beside the fourteen, prefixed with the server’s name. You can switch,
        restart or remove them in <strong>Mind → Servers</strong>; this
        release has no way to add a new one from the app, and Edward can’t
        install servers himself any more (he runs <code>npx</code> in his own
        shell when he wants one for himself). Because nothing can tell what a
        custom tool does, every one is treated as outward: its
        arguments are secret-scanned, its results taint the turn and are
        redacted, a wake that uses one writes a journal entry, and it is
        refused in friends’ pings and in turns that may reach nobody.
      </p>
      <p>
        A server runs as you, unsandboxed, with an allowlisted environment
        (no keys of Edward’s). Its own <code>env_vars</code> are stored in
        plain text in the database and its backups: don’t put a write-capable
        token there without meaning to.
      </p>

      <h2>Also available</h2>
      <ul>
        <li>
          <strong>Hosted pages</strong>: <code>HTML_HOSTING_URL</code> and{" "}
          <code>HTML_HOSTING_API_KEY</code> point at a small upload service
          that speaks its API. There is no default host, and Edward’s tools
          for it were retired.
        </li>
        <li>
          <strong>The knowledge graph</strong> (Graphiti and Kuzu), off by
          default: see <Link href="/docs/memory">Memory</Link>.
        </li>
      </ul>

      <h2>Removed</h2>
      <p>
        The Python, JavaScript and SQL sandboxes, Lightpanda, LangSmith, Find
        My and USPS tracking are gone. See{" "}
        <Link href="/docs/upgrading">Upgrading from March</Link> for the full
        list.
      </p>

      <hr />
      <p>
        The long form:{" "}
        <a href={`${GITHUB_BLOB}/docs/architecture/integrations.md`}>
          integrations.md
        </a>
        .
      </p>
    </DocsContent>
  );
}
