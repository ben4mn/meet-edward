import type { Metadata } from "next";
import { DocsContent } from "../../../components/docs/DocsContent";

export const metadata: Metadata = {
  title: "Getting Started — Edward Docs",
  description: "Quick start guide for developers — prerequisites, setup script, environment variables, and troubleshooting for running Edward on macOS, Linux, or Windows (WSL).",
  alternates: { canonical: "/docs/getting-started" },
  openGraph: {
    title: "Getting Started — Edward Docs",
    description: "Quick start guide for developers — prerequisites, setup script, environment variables, and troubleshooting.",
    url: "/docs/getting-started",
  },
};

export default function GettingStartedPage() {
  return (
    <DocsContent>
      <h1>Getting Started</h1>
      <p className="subtitle">
        From zero to a running Edward instance in about five minutes.
      </p>

      <h2>Prerequisites</h2>
      <ul>
        <li><strong>macOS</strong> — required for iMessage, Apple Services, and the scheduler</li>
        <li><strong>Homebrew</strong> — used by the setup script to install PostgreSQL</li>
        <li><strong>Node.js 18+</strong> — for the Next.js frontend</li>
        <li><strong>Python 3.11+</strong> — for the FastAPI backend</li>
        <li><strong>Anthropic API key</strong> — get one at <a href="https://console.anthropic.com" target="_blank" rel="noopener noreferrer">console.anthropic.com</a></li>
      </ul>

      <h2>Quick Start</h2>
      <pre><code>{`# Clone the repository
git clone https://github.com/ben4mn/meet-edward.git
cd meet-edward

# Run the setup script
./setup.sh

# Add your Anthropic API key to backend/.env
# ANTHROPIC_API_KEY=sk-ant-...

# Start both services
./restart.sh`}</code></pre>
      <p>
        That&apos;s it. Visit <code>http://localhost:3000</code> and you&apos;re live.
      </p>

      <h2>What setup.sh Does</h2>
      <p>The setup script handles the full first-time installation:</p>
      <ol>
        <li>Installs <strong>PostgreSQL 16</strong> and the <strong>pgvector</strong> extension via Homebrew</li>
        <li>Creates the <code>edward</code> database and user (default: <code>edward/edward/edward</code>)</li>
        <li>Creates a Python virtual environment in <code>backend/venv/</code> and installs dependencies</li>
        <li>Installs frontend npm packages</li>
        <li>Generates a <code>backend/.env</code> template with required variables</li>
      </ol>

      <h2>What restart.sh Does</h2>
      <p>The restart script manages both services with graceful stop/start:</p>
      <pre><code>{`./restart.sh              # Restart both frontend + backend
./restart.sh frontend     # Restart only frontend
./restart.sh backend      # Stop and restart only backend`}</code></pre>
      <p>
        Logs are written to <code>backend/logs/</code> and the frontend console.
        The backend also supports <code>./start.sh</code> for direct startup
        (auto-activates the venv and installs any new dependencies).
      </p>

      <h2>Setting Your Password</h2>
      <p>
        The first time you visit <code>localhost:3000</code>, you&apos;ll be
        prompted to set a password. This is stored as a bcrypt hash in the
        database — Edward uses single-user JWT auth with HttpOnly cookies.
      </p>
      <p>
        After setup, you can change your password anytime from the Settings page.
      </p>

      <h2>Verifying It Works</h2>
      <ol>
        <li>Open <code>http://localhost:3000</code> in your browser</li>
        <li>Set your password on first visit</li>
        <li>Send a message — Edward should respond with full context</li>
        <li>Check the debug panel (bottom of chat) for health status</li>
      </ol>

      <h2>Common Issues</h2>

      <h3>PostgreSQL not running</h3>
      <pre><code>{`brew services start postgresql@16`}</code></pre>

      <h3>Missing API key</h3>
      <p>
        Make sure <code>ANTHROPIC_API_KEY</code> is set in <code>backend/.env</code>.
        Edward won&apos;t start the LangGraph agent without it.
      </p>

      <h3>Port conflicts</h3>
      <p>
        The backend runs on <code>:8000</code> and the frontend on{" "}
        <code>:3000</code>. If either port is in use, check for existing
        processes:
      </p>
      <pre><code>{`lsof -i :8000
lsof -i :3000`}</code></pre>

      <h3>Python version mismatch</h3>
      <p>
        Edward requires Python 3.11+. Check with <code>python3 --version</code>.
        If you have multiple Python versions, the setup script uses whichever{" "}
        <code>python3</code> resolves to in your PATH.
      </p>

      <hr />
      <p>
        Next up: configure your environment variables in the{" "}
        <a href="/docs/configuration">Configuration</a> guide.
      </p>
    </DocsContent>
  );
}
