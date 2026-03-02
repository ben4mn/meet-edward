import type { Metadata } from "next";
import { DocsContent } from "../../../components/docs/DocsContent";

export const metadata: Metadata = {
  title: "Setup with AI — Edward Docs",
  description: "Set up Edward using an AI coding assistant like Claude Code, Cursor, or Windsurf. Paste one block and let the AI handle installation, configuration, and verification.",
  alternates: { canonical: "/docs/setup-with-ai" },
  openGraph: {
    title: "Setup with AI — Edward Docs",
    description: "Set up Edward using an AI coding assistant like Claude Code, Cursor, or Windsurf. Paste one block and go.",
    url: "/docs/setup-with-ai",
  },
};

export default function SetupWithAIPage() {
  return (
    <DocsContent>
      <h1>Setup with AI</h1>
      <p className="subtitle">
        Paste one block into your AI coding assistant and let it handle the
        rest.
      </p>

      <h2>Why Use an AI Assistant?</h2>
      <p>
        If terms like git, Python virtual environments, or PostgreSQL feel
        unfamiliar, let an AI assistant walk you through the setup. Tools like{" "}
        <a
          href="https://claude.com/claude-code"
          target="_blank"
          rel="noopener noreferrer"
        >
          Claude Code
        </a>
        ,{" "}
        <a
          href="https://cursor.com"
          target="_blank"
          rel="noopener noreferrer"
        >
          Cursor
        </a>
        , or similar can read the instructions, run the commands, and
        troubleshoot errors — all from a single paste.
      </p>

      <h2>What You Need</h2>
      <ul>
        <li>
          <strong>A Mac</strong> — Edward requires macOS
        </li>
        <li>
          <strong>An AI coding assistant</strong> — Claude Code, Cursor, Windsurf,
          or any tool that can run terminal commands
        </li>
        <li>
          <strong>An Anthropic API key</strong> — get one at{" "}
          <a
            href="https://console.anthropic.com"
            target="_blank"
            rel="noopener noreferrer"
          >
            console.anthropic.com
          </a>{" "}
          (the AI will help if you don&apos;t have one yet)
        </li>
      </ul>

      <h2>Paste This to Your AI</h2>
      <p>
        Copy the entire block below and paste it into your AI assistant:
      </p>
      <pre>
        <code>{`I want to set up Edward, an open-source AI assistant, on my Mac.

Here is the project documentation:
https://raw.githubusercontent.com/ben4mn/meet-edward/main/CLAUDE.md

Steps:
1. Clone: git clone https://github.com/ben4mn/meet-edward.git
2. Run ./setup.sh (installs PostgreSQL, pgvector, Python venv, Node deps)
3. I need to add my ANTHROPIC_API_KEY to .env — ask me for it or help me get one from console.anthropic.com
4. Run ./restart.sh to start frontend + backend
5. Verify http://localhost:3000 loads and responds to a test message

If anything fails, check:
- brew services list (PostgreSQL should be running)
- python3 --version (needs 3.11+)
- node --version (needs 18+)
- cat /tmp/edward-backend.log
- cat /tmp/edward-frontend.log`}</code>
      </pre>

      <h2>What Happens Next</h2>
      <p>Your AI assistant will:</p>
      <ol>
        <li>
          Clone the Edward repository to your computer
        </li>
        <li>
          Run the setup script, which installs PostgreSQL, Python dependencies,
          and the frontend
        </li>
        <li>
          Ask you for your Anthropic API key (or help you create one)
        </li>
        <li>
          Start both the backend and frontend services
        </li>
        <li>
          Verify everything is working
        </li>
      </ol>
      <p>
        If anything goes wrong, the AI has the debugging commands built into the
        prompt and can troubleshoot on the spot.
      </p>

      <h2>After Setup</h2>
      <ol>
        <li>
          Open{" "}
          <code>
            <a href="http://localhost:3000">http://localhost:3000</a>
          </code>{" "}
          in your browser
        </li>
        <li>Set a password on first visit</li>
        <li>Send a test message to make sure Edward responds</li>
      </ol>
      <p>
        Your Mac needs to stay on for Edward to run. If you restart your Mac,
        run <code>./restart.sh</code> from the project folder to start Edward
        again.
      </p>
      <p>
        Want to access Edward from your phone or set it up as a home screen app?
        See the{" "}
        <a href="/docs/beginner-guide#accessing-edward-from-your-phone">
          phone access section
        </a>{" "}
        of the Beginner Guide.
      </p>

      <hr />
      <p>
        Once Edward is running, head to the{" "}
        <a href="/docs/configuration">Configuration</a> guide to customize
        your setup.
      </p>
    </DocsContent>
  );
}
