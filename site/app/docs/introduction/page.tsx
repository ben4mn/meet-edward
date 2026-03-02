import type { Metadata } from "next";
import { DocsContent } from "../../../components/docs/DocsContent";

export const metadata: Metadata = {
  title: "Introduction — Edward Docs",
  description: "Meet Edward — an open-source, self-hosted AI assistant with long-term memory, extensible skills, and proactive monitoring. Built with Next.js, FastAPI, LangGraph, and PostgreSQL.",
  alternates: { canonical: "/docs/introduction" },
  openGraph: {
    title: "Introduction — Edward Docs",
    description: "Meet Edward — an open-source, self-hosted AI assistant with long-term memory, extensible skills, and proactive monitoring.",
    url: "/docs/introduction",
  },
};

export default function IntroductionPage() {
  return (
    <DocsContent>
      <h1>Introduction</h1>
      <p className="subtitle">
        Meet Edward — an open-source AI assistant that remembers everything.
      </p>

      <h2>What is Edward?</h2>
      <p>
        Edward is a full-stack AI assistant built for personal use. He runs on
        your Mac, stores memories in PostgreSQL, and gets smarter the more you
        talk to him. Think of him as a private ChatGPT that actually knows who
        you are.
      </p>
      <p>
        Edward is powered by Claude (Anthropic), orchestrated with LangGraph,
        and extended through a modular skills system that includes messaging
        (iMessage, SMS, WhatsApp), code execution, web search, Apple services
        integration, and more.
      </p>

      <h2>What Does Edward Stand For?</h2>
      <p>
        <strong>E</strong>nhanced <strong>D</strong>igital <strong>W</strong>orkflow{" "}
        <strong>A</strong>ssistant for <strong>R</strong>outine <strong>D</strong>ecisions
      </p>
      <ul>
        <li><strong>Enhanced</strong> — learns from every conversation with long-term memory</li>
        <li><strong>Digital</strong> — lives on your machine, integrates with your apps</li>
        <li><strong>Workflow</strong> — automates messaging, scheduling, research, and coding tasks</li>
        <li><strong>Assistant</strong> — proactive, not just reactive — monitors messages and acts on your behalf</li>
        <li><strong>Routine</strong> — handles the repetitive stuff so you can focus on what matters</li>
        <li><strong>Decisions</strong> — provides context-aware answers informed by your history</li>
      </ul>

      <h2>Default Personality</h2>
      <p>
        Out of the box, Edward&apos;s system prompt includes the instruction:
      </p>
      <pre><code>Be concise, friendly, helpful, and a tad cheeky when you feel like it.</code></pre>
      <p>
        You can customize this in the Settings page to match your preferred communication style.
      </p>

      <h2>Key Differentiators</h2>
      <ul>
        <li>
          <strong>Long-term memory</strong> — hybrid vector + keyword search
          across facts, preferences, context, and instructions. Edward remembers
          what you told him last week, last month, or last year.
        </li>
        <li>
          <strong>Self-hosted</strong> — your data stays on your machine. No
          third-party storage, no vendor lock-in. Just PostgreSQL and your
          Anthropic API key.
        </li>
        <li>
          <strong>Extensible skills</strong> — enable and disable integrations
          from the settings UI. Edward can send texts, search the web, execute
          code, manage your calendar, and install new MCP servers at runtime.
        </li>
        <li>
          <strong>Proactive heartbeat</strong> — monitors your iMessage, calendar,
          and email for items that need attention, triaging and responding
          autonomously when appropriate.
        </li>
        <li>
          <strong>Scheduled actions</strong> — Edward can schedule reminders,
          messages, and tasks for the future. He fires them himself using the
          same tool loop he uses in conversation.
        </li>
      </ul>

      <h2>How It Works (High Level)</h2>
      <ol>
        <li>You send a message through the web UI (or iMessage, SMS, WhatsApp)</li>
        <li>Edward retrieves relevant memories from your history</li>
        <li>The LangGraph agent processes your message with full tool access</li>
        <li>Edward responds, then extracts any new memories to store</li>
        <li>Background systems enrich, reflect on, and consolidate memories over time</li>
      </ol>

      <hr />

      <h2>Choose Your Path</h2>
      <ul>
        <li>
          <strong><a href="/docs/beginner-guide">Beginner Guide</a></strong> —
          never used a terminal? Start here.
        </li>
        <li>
          <strong><a href="/docs/setup-with-ai">Setup with AI</a></strong> —
          paste one block into an AI coding assistant and let it handle
          everything.
        </li>
        <li>
          <strong><a href="/docs/getting-started">Getting Started</a></strong> —
          comfortable with the terminal? Quick 5-minute path.
        </li>
      </ul>
    </DocsContent>
  );
}
