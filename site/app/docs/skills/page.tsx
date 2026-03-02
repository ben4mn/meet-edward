import { DocsContent } from "../../../components/docs/DocsContent";

export const metadata = {
  title: "Skills & Integrations — Edward Docs",
};

export default function SkillsPage() {
  return (
    <DocsContent>
      <h1>Skills &amp; Integrations</h1>
      <p className="subtitle">
        Edward&apos;s modular skill system — enable what you need, disable what you don&apos;t.
      </p>

      <h2>Overview</h2>
      <p>
        Skills are managed from the <strong>Settings</strong> page in the web UI.
        Each skill can be toggled on or off independently. When a skill is enabled,
        its tools become available to Edward in conversation. When disabled, those
        tools are completely hidden from the LLM.
      </p>

      <h2>All Skills</h2>
      <table>
        <thead>
          <tr>
            <th>Skill</th>
            <th>Category</th>
            <th>Description</th>
            <th>Env Vars Required</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><strong>iMessage AppleScript</strong></td>
            <td>Messaging</td>
            <td>Send and read iMessages via AppleScript (macOS only)</td>
            <td>None</td>
          </tr>
          <tr>
            <td><strong>Twilio SMS</strong></td>
            <td>Messaging</td>
            <td>Send and receive SMS via Twilio</td>
            <td><code>TWILIO_ACCOUNT_SID</code>, <code>TWILIO_AUTH_TOKEN</code>, <code>TWILIO_PHONE_NUMBER</code></td>
          </tr>
          <tr>
            <td><strong>Twilio WhatsApp</strong></td>
            <td>Messaging</td>
            <td>Send and receive WhatsApp messages via Twilio</td>
            <td>Same as Twilio SMS</td>
          </tr>
          <tr>
            <td><strong>WhatsApp MCP</strong></td>
            <td>Messaging</td>
            <td>Read/send WhatsApp as user via whatsapp-mcp Go bridge</td>
            <td><code>MCP_WHATSAPP_ENABLED</code>, <code>MCP_WHATSAPP_SERVER_DIR</code></td>
          </tr>
          <tr>
            <td><strong>Brave Search</strong></td>
            <td>Search</td>
            <td>Web search and page content extraction via Brave Search API</td>
            <td><code>BRAVE_SEARCH_API_KEY</code></td>
          </tr>
          <tr>
            <td><strong>Code Interpreter</strong></td>
            <td>Execution</td>
            <td>Execute Python code in a sandboxed subprocess</td>
            <td>None</td>
          </tr>
          <tr>
            <td><strong>JavaScript Interpreter</strong></td>
            <td>Execution</td>
            <td>Execute JavaScript via Node.js subprocess</td>
            <td>None</td>
          </tr>
          <tr>
            <td><strong>SQL Database</strong></td>
            <td>Execution</td>
            <td>Per-conversation SQLite database for queries</td>
            <td>None</td>
          </tr>
          <tr>
            <td><strong>Shell / Bash</strong></td>
            <td>Execution</td>
            <td>Execute shell commands with blocklist safety</td>
            <td>None</td>
          </tr>
          <tr>
            <td><strong>Apple Services</strong></td>
            <td>Apple</td>
            <td>Calendar, Reminders, Notes, Mail, Contacts, Maps (macOS only)</td>
            <td><code>MCP_APPLE_ENABLED=true</code></td>
          </tr>
          <tr>
            <td><strong>HTML Hosting</strong></td>
            <td>System</td>
            <td>Create, update, and delete hosted HTML pages</td>
            <td><code>HTML_HOSTING_API_KEY</code></td>
          </tr>
        </tbody>
      </table>

      <h2>Always-Available Tools</h2>
      <p>
        Some tools are always available regardless of skill state. These are
        core to Edward&apos;s functionality:
      </p>
      <ul>
        <li><strong>Memory tools</strong> — remember, update, forget, search</li>
        <li><strong>Document tools</strong> — save, read, edit, search, list, delete</li>
        <li><strong>Scheduled event tools</strong> — schedule, list, cancel</li>
        <li><strong>File storage tools</strong> — save, list, read, download, tag, delete</li>
        <li><strong>Persistent database tools</strong> — create, query, list, delete</li>
        <li><strong>Widget tools</strong> — update widget state and code</li>
        <li><strong>Push notification tools</strong> — send web push to subscribed devices</li>
        <li><strong>Contact tools</strong> — lookup by name or phone number</li>
        <li><strong>Custom MCP tools</strong> — search, add, list, remove MCP servers</li>
      </ul>

      <h2>Custom MCP Servers</h2>
      <p>
        Beyond the built-in skills, Edward can discover, install, and use MCP
        servers at runtime. This is fully self-service — Edward can search
        GitHub for MCP server packages, install them via <code>npx</code> or{" "}
        <code>uvx</code>, and start using their tools immediately.
      </p>
      <p>Key details:</p>
      <ul>
        <li>Servers run as subprocesses — no git clone needed</li>
        <li>Each server&apos;s tools are prefixed with the server name to avoid collisions</li>
        <li>New tools are available immediately via hot-reload</li>
        <li>Manage custom servers from the &quot;Edward&apos;s Servers&quot; panel in Settings</li>
      </ul>
      <p>
        Set the <code>GITHUB_TOKEN</code> environment variable to enable GitHub
        search for MCP server discovery.
      </p>

      <h2>Enabling a Skill</h2>
      <ol>
        <li>Navigate to <strong>Settings</strong> in the web UI</li>
        <li>Find the skill in the skills list</li>
        <li>Toggle it on</li>
        <li>If the skill requires environment variables, make sure they&apos;re set in <code>backend/.env</code> and restart the backend</li>
      </ol>
      <p>
        Skills that require MCP clients (WhatsApp MCP, Apple Services) will
        initialize their subprocess when enabled. The skill status indicator
        shows whether the connection is healthy.
      </p>

      <hr />
      <p>
        Curious about how it all fits together? See the{" "}
        <a href="/docs/architecture">Architecture</a> overview.
      </p>
    </DocsContent>
  );
}
