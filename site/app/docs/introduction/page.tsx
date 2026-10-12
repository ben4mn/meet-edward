import type { Metadata } from "next";
import Link from "next/link";
import { DocsContent } from "../../../components/docs/DocsContent";
import { Screenshot } from "../../../components/docs/Screenshot";
import { GITHUB_BLOB, GITHUB_URL } from "../../../lib/docsNav";

export const metadata: Metadata = {
  title: "Introduction — Edward Docs",
  description:
    "Edward is an open-source AI assistant for one person. He remembers where he heard things, follows through on what he promised, works in a sandboxed computer of his own and runs on your Mac with the Claude API.",
  alternates: { canonical: "/docs/introduction" },
  openGraph: {
    title: "Introduction — Edward Docs",
    description:
      "An open-source AI assistant that remembers, notices what matters and works on your Mac, for one person: you.",
    url: "/docs/introduction",
  },
};

export default function IntroductionPage() {
  return (
    <DocsContent>
      <h1>Introduction</h1>
      <p className="subtitle">
        An open-source AI assistant that remembers, notices what matters and
        works on your Mac, for one person: you.
      </p>

      <h2>What Edward is</h2>
      <p>
        Edward is a personal assistant you run yourself, on a Mac that stays
        on. You talk to him in an app you can install on your phone, by voice,
        and in iMessage, where anyone can write <code>@Edward</code> in a chat.
        He keeps what he learns in your own PostgreSQL database, with a record
        of where each memory came from. He has a small computer of his own (a
        sandboxed shell, a file editor and a headless browser), he schedules
        his own follow-ups when he promises to check back, and he keeps a
        journal of what he did on his own and why.
      </p>
      <p>
        Every model call goes to the Claude API, with a model chosen per
        purpose and a daily spend cap you set ($5 by default). The code is
        Apache-2.0 on <a href={GITHUB_URL}>GitHub</a>.
      </p>
      <p>
        This release is a snapshot of a build that ran every day for one
        person for months. The parts that were bespoke to that home are
        marked <code>ADAPT:</code> in the code and collected in{" "}
        <Link href="/docs/adapting">Adapting to your Mac</Link>.
      </p>

      <Screenshot
        src="/screenshots/today-desktop.png"
        alt="Edward's Today page: late pet doses to log, a call starting soon, a reply held for approval, and a list of what he did today"
        width={1600}
        height={1000}
        caption="Today: what needs you, with the action on each card, and what he did. (Invented data from the repository's UX harness.)"
      />

      <h2>What makes him different</h2>
      <ul>
        <li>
          <strong>He remembers, and knows where he heard it.</strong> Every
          memory records who said it (you, someone in your messages, or
          Edward himself), what it is about and how sure he is. Ask him
          “how do you know that?” and he answers from the record. Only your
          own words make a standing instruction, and only you can change what
          you said.
        </li>
        <li>
          <strong>He works for one person.</strong> Edward runs on your Mac
          and keeps everything he knows in your Postgres. The one outside
          service he needs is the Claude API, which sees what each model call
          sends it.
        </li>
        <li>
          <strong>He has his own computer.</strong> A persistent shell, a file
          editor and a headless browser of his own, with rails against
          accidents: he can’t write his own running code, read his own keys,
          reach your database or launch other apps. The sandbox guards those
          things, not your whole account (see{" "}
          <Link href="/docs/guard-rails">Guard rails</Link>).
        </li>
        <li>
          <strong>He follows through.</strong> When he says he will check
          back, he schedules it and comes back. Whatever he does on his own
          goes in a journal entry, and you can mark each one right or wrong.
        </li>
        <li>
          <strong>He speaks up once, or not at all.</strong> Noticing what
          matters in your messages is opt-in. When it is on, he says a thing
          once, through one door, outside your quiet hours.
        </li>
        <li>
          <strong>He has limits you set.</strong> A daily spend cap in
          dollars, a per-turn cap, a secret scan on everything that leaves,
          and approvals when you want them.
        </li>
      </ul>

      <h2>How a conversation works</h2>
      <ol>
        <li>
          You write (or say) something in the app, in iMessage, or through the
          iPhone widget.
        </li>
        <li>
          Edward searches his memories (vector similarity and keywords,
          blended 70/30) and the documents he keeps for you.
        </li>
        <li>
          Claude answers with the same fourteen tools on every surface: his
          shell and editor, web search and fetch, his own browser, your Chrome
          if you allow it, <code>look</code> for pictures and PDFs, your
          messages, one guarded send, a way to tell you something, memory,
          scheduling, his screens and a visible plan. The app shows his
          reasoning and every tool call as it happens.
        </li>
        <li>
          After the answer, a small model extracts what is worth remembering
          and files it with its source.
        </li>
      </ol>

      <h2>When nobody is talking to him</h2>
      <ul>
        <li>
          <strong>Follow-through.</strong> A promise to check back is a
          scheduled wake; when it fires, he does the checking.
        </li>
        <li>
          <strong>A silent nightly</strong> at 03:00 tidies his memory and his
          state, and is skipped when nothing happened.
        </li>
        <li>
          <strong>Free time</strong>: one small, budgeted run a day at 13:00
          for his own screen and projects. It reaches nobody.
        </li>
        <li>
          <strong>Noticing</strong> (off until you turn it on): he reads new
          messages and mail for what matters to you, decides whether it is
          worth a word, and says it once.
        </li>
      </ul>
      <p>
        More in <Link href="/docs/mind">The mind</Link>.
      </p>

      <h2>What leaves your Mac</h2>
      <p>
        Edward’s memories, conversations, documents and files live in your
        Postgres and your home folder. What goes out:
      </p>
      <ul>
        <li>
          <strong>The Claude API</strong> receives what each model call sends:
          the conversation, the memories retrieved for it and the tool results.
        </li>
        <li>
          Only if you set them up: ElevenLabs (voice), Google (Gmail), Twilio
          (SMS), Brave Search, Home Assistant, and whatever a page or a send
          he makes for you reaches.
        </li>
      </ul>

      <h2>What you need</h2>
      <ul>
        <li>A Mac with Apple silicon that can stay on (a Mac mini makes a good host)</li>
        <li>
          The Xcode command-line tools, <a href="https://brew.sh">Homebrew</a>{" "}
          and an{" "}
          <a href="https://console.anthropic.com/">Anthropic API key</a>
        </li>
      </ul>
      <p>
        Intel Macs, Linux and Windows are not supported. See{" "}
        <Link href="/docs/platform-support">Platform support</Link> for why.
      </p>

      <hr />

      <h2>Choose your path</h2>
      <ul>
        <li>
          <strong>
            <Link href="/docs/beginner-guide">Beginner guide</Link>
          </strong>{" "}
          — never used a terminal? Start here.
        </li>
        <li>
          <strong>
            <Link href="/docs/setup-with-ai">Set up with AI</Link>
          </strong>{" "}
          — let Claude Code (or another coding assistant) walk you through it.
        </li>
        <li>
          <strong>
            <Link href="/docs/getting-started">Quick start</Link>
          </strong>{" "}
          — comfortable in a terminal? Four commands.
        </li>
        <li>
          <strong>
            <Link href="/docs/upgrading">Upgrading from March</Link>
          </strong>{" "}
          — already running the March 2026 release? Move to this one without
          losing what he knows.
        </li>
      </ul>
      <p>
        The repository’s own docs go deeper: the{" "}
        <a href={`${GITHUB_BLOB}/README.md`}>README</a>,{" "}
        <a href={`${GITHUB_BLOB}/CLAUDE.md`}>CLAUDE.md</a> (the operating
        guide) and one page per subsystem in{" "}
        <a href={`${GITHUB_BLOB}/docs/architecture/README.md`}>
          docs/architecture
        </a>
        .
      </p>
    </DocsContent>
  );
}
