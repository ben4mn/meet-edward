import type { Metadata } from "next";
import { DocsContent } from "../../../components/docs/DocsContent";

export const metadata: Metadata = {
  title: "Configuration — Edward Docs",
  description: "Complete environment variable reference for Edward — API keys, database settings, messaging (Twilio, WhatsApp, iMessage), search, observability, and push notifications.",
  alternates: { canonical: "/docs/configuration" },
  openGraph: {
    title: "Configuration — Edward Docs",
    description: "Complete environment variable reference for Edward — API keys, database, messaging, search, and more.",
    url: "/docs/configuration",
  },
};

export default function ConfigurationPage() {
  return (
    <DocsContent>
      <h1>Configuration</h1>
      <p className="subtitle">
        Environment variables and integration setup for Edward.
      </p>

      <h2>Required</h2>
      <table>
        <thead>
          <tr>
            <th>Variable</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>ANTHROPIC_API_KEY</code></td>
            <td>Your Anthropic API key. Get one at <a href="https://console.anthropic.com" target="_blank" rel="noopener noreferrer">console.anthropic.com</a></td>
          </tr>
        </tbody>
      </table>
      <p>
        Everything else is optional. Edward works out of the box with just
        the API key — additional variables unlock integrations.
      </p>

      <h2>Database</h2>
      <table>
        <thead>
          <tr>
            <th>Variable</th>
            <th>Default</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>DB_USER</code></td>
            <td><code>edward</code></td>
            <td>PostgreSQL username</td>
          </tr>
          <tr>
            <td><code>DB_PASSWORD</code></td>
            <td><code>edward</code></td>
            <td>PostgreSQL password</td>
          </tr>
          <tr>
            <td><code>DB_NAME</code></td>
            <td><code>edward</code></td>
            <td>PostgreSQL database name</td>
          </tr>
          <tr>
            <td><code>DB_HOST</code></td>
            <td><code>localhost</code></td>
            <td>Database host</td>
          </tr>
          <tr>
            <td><code>DB_PORT</code></td>
            <td><code>5432</code></td>
            <td>Database port</td>
          </tr>
        </tbody>
      </table>

      <h2>Authentication</h2>
      <table>
        <thead>
          <tr>
            <th>Variable</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>JWT_SECRET_KEY</code></td>
            <td>Secret for signing JWT tokens. Defaults to a dev key — <strong>change this in production</strong></td>
          </tr>
        </tbody>
      </table>

      <h2>Messaging</h2>
      <h3>Twilio (SMS &amp; WhatsApp)</h3>
      <table>
        <thead>
          <tr>
            <th>Variable</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>TWILIO_ACCOUNT_SID</code></td>
            <td>Twilio account SID</td>
          </tr>
          <tr>
            <td><code>TWILIO_AUTH_TOKEN</code></td>
            <td>Twilio auth token</td>
          </tr>
          <tr>
            <td><code>TWILIO_PHONE_NUMBER</code></td>
            <td>Your Twilio phone number (e.g. <code>+1234567890</code>)</td>
          </tr>
          <tr>
            <td><code>TWILIO_WEBHOOK_URL</code></td>
            <td>Public URL for SMS webhooks</td>
          </tr>
          <tr>
            <td><code>TWILIO_WHATSAPP_WEBHOOK_URL</code></td>
            <td>Public URL for WhatsApp webhooks</td>
          </tr>
        </tbody>
      </table>

      <h3>WhatsApp MCP Bridge</h3>
      <table>
        <thead>
          <tr>
            <th>Variable</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>MCP_WHATSAPP_ENABLED</code></td>
            <td>Set to <code>true</code> to enable the whatsapp-mcp Go bridge</td>
          </tr>
          <tr>
            <td><code>MCP_WHATSAPP_SERVER_DIR</code></td>
            <td>Path to the whatsapp-mcp-server directory</td>
          </tr>
        </tbody>
      </table>

      <h3>iMessage</h3>
      <p>
        No environment variables needed — iMessage works via AppleScript on
        macOS. Enable the <code>imessage_applescript</code> skill in Settings.
      </p>

      <h2>Apple Services</h2>
      <table>
        <thead>
          <tr>
            <th>Variable</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>MCP_APPLE_ENABLED</code></td>
            <td>Set to <code>true</code> to enable Calendar, Reminders, Notes, Mail, Contacts, and Maps</td>
          </tr>
        </tbody>
      </table>
      <p>
        Apple Services runs as an MCP subprocess and requires macOS with the
        relevant apps configured (Calendar, Mail, etc.).
      </p>

      <h2>Web Search</h2>
      <table>
        <thead>
          <tr>
            <th>Variable</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>BRAVE_SEARCH_API_KEY</code></td>
            <td>Brave Search API key for web search and page content extraction</td>
          </tr>
        </tbody>
      </table>

      <h2>HTML Hosting</h2>
      <table>
        <thead>
          <tr>
            <th>Variable</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>HTML_HOSTING_API_KEY</code></td>
            <td>API key for html.zyroi.com hosting service</td>
          </tr>
          <tr>
            <td><code>HTML_HOSTING_URL</code></td>
            <td>Hosting URL (defaults to <code>https://html.zyroi.com</code>)</td>
          </tr>
        </tbody>
      </table>

      <h2>Custom MCP Servers</h2>
      <table>
        <thead>
          <tr>
            <th>Variable</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>GITHUB_TOKEN</code></td>
            <td>GitHub personal access token — used for searching MCP server packages</td>
          </tr>
        </tbody>
      </table>

      <h2>Observability</h2>
      <table>
        <thead>
          <tr>
            <th>Variable</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>LANGCHAIN_TRACING_V2</code></td>
            <td>Set to <code>true</code> to enable LangSmith tracing</td>
          </tr>
          <tr>
            <td><code>LANGCHAIN_API_KEY</code></td>
            <td>LangSmith API key</td>
          </tr>
          <tr>
            <td><code>LANGCHAIN_PROJECT</code></td>
            <td>LangSmith project name (e.g. <code>edward</code>)</td>
          </tr>
        </tbody>
      </table>

      <h2>Push Notifications</h2>
      <table>
        <thead>
          <tr>
            <th>Variable</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>VAPID_PUBLIC_KEY</code></td>
            <td>VAPID public key for Web Push</td>
          </tr>
          <tr>
            <td><code>VAPID_PRIVATE_KEY</code></td>
            <td>VAPID private key for Web Push</td>
          </tr>
        </tbody>
      </table>
      <p>
        Generate VAPID keys with: <code>npx web-push generate-vapid-keys</code>
      </p>

      <h2>File Storage</h2>
      <table>
        <thead>
          <tr>
            <th>Variable</th>
            <th>Default</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>FILE_STORAGE_ROOT</code></td>
            <td><code>./storage</code></td>
            <td>Directory for persistent file storage</td>
          </tr>
        </tbody>
      </table>

      <hr />
      <p>
        See all available integrations in the{" "}
        <a href="/docs/skills">Skills &amp; Integrations</a> guide.
      </p>
    </DocsContent>
  );
}
