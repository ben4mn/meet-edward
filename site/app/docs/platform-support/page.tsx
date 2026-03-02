import { DocsContent } from "../../../components/docs/DocsContent";

export const metadata = {
  title: "Platform Support — Edward Docs",
};

export default function PlatformSupportPage() {
  return (
    <DocsContent>
      <h1>Platform Support</h1>
      <p className="subtitle">
        Edward is built for macOS, but most of the stack runs on any platform.
      </p>

      <h2>What Works Everywhere</h2>
      <p>
        The core assistant — and the majority of Edward&apos;s capabilities —
        is fully cross-platform. If you can run Python, Node.js, and PostgreSQL,
        you can run Edward.
      </p>
      <table>
        <thead>
          <tr>
            <th>Feature</th>
            <th>macOS</th>
            <th>Linux</th>
            <th>Windows (WSL)</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Core chat + LangGraph agent</td>
            <td>✓</td>
            <td>✓</td>
            <td>✓</td>
          </tr>
          <tr>
            <td>Long-term memory (pgvector)</td>
            <td>✓</td>
            <td>✓</td>
            <td>✓</td>
          </tr>
          <tr>
            <td>Document store</td>
            <td>✓</td>
            <td>✓</td>
            <td>✓</td>
          </tr>
          <tr>
            <td>Scheduled events / scheduler</td>
            <td>✓</td>
            <td>✓</td>
            <td>✓</td>
          </tr>
          <tr>
            <td>Code execution (Python, JS, SQL, Shell)</td>
            <td>✓</td>
            <td>✓</td>
            <td>✓</td>
          </tr>
          <tr>
            <td>Twilio SMS &amp; WhatsApp</td>
            <td>✓</td>
            <td>✓</td>
            <td>✓</td>
          </tr>
          <tr>
            <td>WhatsApp MCP</td>
            <td>✓</td>
            <td>✓</td>
            <td>✓</td>
          </tr>
          <tr>
            <td>Brave Search</td>
            <td>✓</td>
            <td>✓</td>
            <td>✓</td>
          </tr>
          <tr>
            <td>HTML Hosting</td>
            <td>✓</td>
            <td>✓</td>
            <td>✓</td>
          </tr>
          <tr>
            <td>File storage</td>
            <td>✓</td>
            <td>✓</td>
            <td>✓</td>
          </tr>
          <tr>
            <td>Persistent databases</td>
            <td>✓</td>
            <td>✓</td>
            <td>✓</td>
          </tr>
          <tr>
            <td>Push notifications</td>
            <td>✓</td>
            <td>✓</td>
            <td>✓</td>
          </tr>
          <tr>
            <td>Orchestrator (worker agents)</td>
            <td>✓</td>
            <td>✓</td>
            <td>✓</td>
          </tr>
          <tr>
            <td>Evolution (self-coding)</td>
            <td>✓</td>
            <td>✓</td>
            <td>✓</td>
          </tr>
          <tr>
            <td>Widget</td>
            <td>✓</td>
            <td>✓</td>
            <td>✓</td>
          </tr>
          <tr>
            <td>Custom MCP servers</td>
            <td>✓</td>
            <td>✓</td>
            <td>✓</td>
          </tr>
        </tbody>
      </table>

      <h2>What You Lose on Windows / Linux</h2>
      <p>
        These features depend on macOS-specific APIs (AppleScript, system
        databases, native apps) and have no cross-platform equivalent.
      </p>
      <table>
        <thead>
          <tr>
            <th>Feature</th>
            <th>Why macOS-only</th>
            <th>Impact</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>iMessage</td>
            <td>AppleScript + Messages.app chat.db</td>
            <td>No iMessage send/receive</td>
          </tr>
          <tr>
            <td>Apple Services</td>
            <td>apple-mcp requires macOS apps</td>
            <td>No Calendar, Reminders, Notes, Mail, Contacts, or Maps integration</td>
          </tr>
          <tr>
            <td>Contacts lookup</td>
            <td>AppleScript to Contacts.app</td>
            <td>No local contact search</td>
          </tr>
          <tr>
            <td>Heartbeat (iMessage)</td>
            <td>Reads ~/Library/Messages/chat.db</td>
            <td>No iMessage monitoring</td>
          </tr>
          <tr>
            <td>Heartbeat (Email)</td>
            <td>Reads Mail.app via MCP</td>
            <td>No email monitoring</td>
          </tr>
          <tr>
            <td>Heartbeat (Calendar)</td>
            <td>Uses Apple Services MCP</td>
            <td>No calendar monitoring</td>
          </tr>
        </tbody>
      </table>

      <h2>Setup Differences</h2>

      <h3>PostgreSQL</h3>
      <p>
        On macOS, <code>setup.sh</code> installs PostgreSQL via Homebrew. On
        other platforms:
      </p>
      <ul>
        <li><strong>Linux (Debian/Ubuntu):</strong> <code>sudo apt install postgresql postgresql-contrib</code></li>
        <li><strong>Linux (Fedora/RHEL):</strong> <code>sudo dnf install postgresql-server postgresql-contrib</code></li>
        <li><strong>Windows:</strong> Install via <a href="https://www.postgresql.org/download/windows/" target="_blank" rel="noopener noreferrer">postgresql.org</a> or <code>winget install PostgreSQL.PostgreSQL</code></li>
      </ul>
      <p>
        You&apos;ll also need the <strong>pgvector</strong> extension. On most Linux
        distros: <code>sudo apt install postgresql-16-pgvector</code> (adjust
        version as needed). On Windows, follow the{" "}
        <a href="https://github.com/pgvector/pgvector" target="_blank" rel="noopener noreferrer">pgvector install guide</a>.
      </p>

      <h3>Python &amp; Node.js</h3>
      <ul>
        <li><strong>Python 3.11+:</strong> Install via <code>apt</code>, <code>pyenv</code>, or the <a href="https://www.python.org/downloads/" target="_blank" rel="noopener noreferrer">Python installer</a></li>
        <li><strong>Node.js 18+:</strong> Install via <code>nvm</code>, <code>fnm</code>, or the <a href="https://nodejs.org" target="_blank" rel="noopener noreferrer">Node.js installer</a></li>
      </ul>

      <h3>Setup Scripts</h3>
      <p>
        The <code>setup.sh</code> and <code>restart.sh</code> scripts use
        Homebrew and assume macOS. On other platforms:
      </p>
      <ul>
        <li><strong>Linux:</strong> The bash scripts run natively — you just need to install PostgreSQL, Python, and Node.js manually first, then run <code>./setup.sh</code> (it will skip Homebrew steps gracefully)</li>
        <li><strong>Windows:</strong> Use WSL2 (recommended) to get a Linux environment, or run the setup steps manually in PowerShell</li>
      </ul>

      <h2>Alternative Messaging via MCP</h2>
      <p>
        Edward&apos;s{" "}
        <a href="/docs/skills">Custom MCP system</a> lets you add messaging
        integrations at runtime — no code changes needed. Edward can discover
        and install MCP servers himself via the <code>search_mcp_servers</code>{" "}
        tool, or you can add them from the Settings page.
      </p>
      <p>Available messaging MCP servers include:</p>
      <table>
        <thead>
          <tr>
            <th>Platform</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><strong>Discord</strong></td>
            <td>MCP servers for Discord bot integration</td>
          </tr>
          <tr>
            <td><strong>Telegram</strong></td>
            <td>MCP servers for the Telegram Bot API</td>
          </tr>
          <tr>
            <td><strong>Signal</strong></td>
            <td>MCP servers for Signal messaging</td>
          </tr>
          <tr>
            <td><strong>Slack</strong></td>
            <td>MCP servers for Slack workspaces</td>
          </tr>
        </tbody>
      </table>
      <p>
        Just ask Edward to &quot;search for a Discord MCP server&quot; in chat,
        or browse the{" "}
        <strong>Edward&apos;s Servers</strong> panel in Settings to add one
        manually.
      </p>

      <h2>Running in Docker</h2>
      <p>
        On Linux, the backend <em>can</em> run in Docker since you don&apos;t
        have Apple features to lose. The frontend runs fine in Docker on any
        platform. This is useful for server deployments or containerized
        environments.
      </p>

      <h2>Windows: WSL2 Recommended</h2>
      <p>
        For Windows users, <strong>WSL2</strong> is the recommended approach. It
        gives you a full Linux environment where everything except Apple
        features works natively — PostgreSQL, Python, Node.js, and all bash
        scripts run without modification.
      </p>
      <ol>
        <li>Install WSL2: <code>wsl --install</code></li>
        <li>Install Ubuntu (default) or your preferred distro</li>
        <li>Follow the Linux setup steps inside WSL</li>
        <li>Access Edward at <code>localhost:3000</code> from your Windows browser</li>
      </ol>

      <hr />
      <p>
        For full setup instructions, see the{" "}
        <a href="/docs/getting-started">Getting Started</a> guide. For
        integration details, see{" "}
        <a href="/docs/skills">Skills &amp; Integrations</a>.
      </p>
    </DocsContent>
  );
}
