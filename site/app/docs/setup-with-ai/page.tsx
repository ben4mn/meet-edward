import type { Metadata } from "next";
import Link from "next/link";
import { DocsContent } from "../../../components/docs/DocsContent";
import { GITHUB_BLOB } from "../../../lib/docsNav";

export const metadata: Metadata = {
  title: "Set Up with AI — Edward Docs",
  description:
    "Let Claude Code, or another AI coding assistant, walk you through installing Edward. The repository's CLAUDE.md carries the setup steps for it to follow.",
  alternates: { canonical: "/docs/setup-with-ai" },
  openGraph: {
    title: "Set Up with AI — Edward Docs",
    description:
      "Let Claude Code, or another AI coding assistant, walk you through installing Edward.",
    url: "/docs/setup-with-ai",
  },
};

const PROMPT = `I want to set up Edward, an open-source personal AI assistant, on this Mac.

1. Check that this Mac has Apple silicon, the Xcode command-line tools and Homebrew.
2. Clone https://github.com/ben4mn/meet-edward.git into my home folder.
3. Read the CLAUDE.md at the top of that checkout and follow its section
   "Helping someone set up Edward", one step at a time. Explain each step
   and confirm it with me before the next.

Never ask me to paste my Anthropic API key, a password or the setup code
into this chat: I will type them into ./setup.sh or .env myself.`;

export default function SetupWithAIPage() {
  return (
    <DocsContent>
      <h1>Set up with AI</h1>
      <p className="subtitle">
        Let an AI coding assistant walk you through the install, one step at a
        time.
      </p>

      <h2>Why it works</h2>
      <p>
        Edward’s repository carries its own operating guide,{" "}
        <a href={`${GITHUB_BLOB}/CLAUDE.md`}>CLAUDE.md</a>, written for AI
        assistants working in it. Its section “Helping someone set up
        Edward”, lists the steps in order: prerequisites, the setup script,
        your settings, the macOS permissions, starting him, choosing your
        password, and the optional features. An assistant that can run
        commands, such as{" "}
        <a href="https://claude.com/claude-code">Claude Code</a>, can follow
        it with you, explain each step and fix what goes wrong.
      </p>

      <h2>What you need</h2>
      <ul>
        <li>
          <strong>A Mac with Apple silicon</strong> that can stay on
        </li>
        <li>
          <strong>An AI coding assistant</strong> that can run terminal
          commands on that Mac: Claude Code, or a similar tool
        </li>
        <li>
          <strong>An Anthropic API key</strong> from{" "}
          <a href="https://console.anthropic.com/">console.anthropic.com</a>{" "}
          (your assistant can show you where to make one)
        </li>
      </ul>

      <h2>Paste this to your assistant</h2>
      <p>
        Open your assistant in a terminal on the Mac that will run Edward
        (for Claude Code, run <code>claude</code> in your home folder) and
        paste:
      </p>
      <pre>
        <code>{PROMPT}</code>
      </pre>
      <p>
        If you already cloned the repository, open the assistant inside the{" "}
        <code>meet-edward</code> folder instead: Claude Code loads{" "}
        <code>CLAUDE.md</code> by itself there, so “Help me set up Edward”
        is enough.
      </p>

      <h2>What to expect</h2>
      <ol>
        <li>
          It checks the prerequisites and installs what is missing (it will
          ask before it does).
        </li>
        <li>
          It runs <code>./setup.sh</code>, which asks <em>you</em> for your
          name, your API key and your time zone. Type them into the setup
          script, not into the chat.
        </li>
        <li>
          It helps you grant only the macOS permissions you need, and starts
          Edward, usually with{" "}
          <code>bash scripts/install-launchagents.sh</code> so he stays on.
        </li>
        <li>
          You open <code>http://localhost:3000</code> on the Mac and choose a
          password. The first visit also asks for a one-time setup code: read
          it yourself with <code>cat backend/.setup_code</code>.
        </li>
        <li>
          It checks the time zone and what works, using the app’s health
          report and the logs, then walks you through the optional features
          you want.
        </li>
      </ol>

      <div className="note caution">
        <p>
          <strong>Keep your secrets out of the chat.</strong> What you paste
          into an AI assistant is sent to its provider. Your API key belongs
          in <code>./setup.sh</code> or <code>.env</code>, and your password
          and setup code in Edward’s own sign-in page.
        </p>
      </div>

      <h2>After setup</h2>
      <p>
        In the Edward tab, turn on the skills you want under{" "}
        <strong>Mind → Skills</strong> (every skill starts off), and check{" "}
        <strong>House → Locale</strong>. To reach Edward from your phone, see{" "}
        <Link href="/docs/beginner-guide#using-edward-from-your-phone">
          Using Edward from your phone
        </Link>
        .
      </p>
      <p>
        The same assistant is a good partner for fitting Edward to your own
        home later: <Link href="/docs/adapting">Adapting to your Mac</Link>{" "}
        has prompts written for it.
      </p>
    </DocsContent>
  );
}
