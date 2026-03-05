import { ReactNode } from "react";
import Link from "next/link";

export interface BlogPost {
  slug: string;
  title: string;
  description: string;
  publishDate: string;
  readingTime: string;
  tags: string[];
  content: () => ReactNode;
}

export type BlogPostMeta = Omit<BlogPost, "content">;

export const posts: BlogPost[] = [
  {
    slug: "why-your-ai-forgets-you",
    title: "Why Your AI Forgets You",
    description:
      "Every AI conversation starts from scratch. Here's why that's broken and what a real memory system looks like.",
    publishDate: "2026-03-04",
    readingTime: "5 min read",
    tags: ["AI memory", "personal AI assistant", "long-term memory AI"],
    content: () => (
      <>
        <p>
          Open ChatGPT. Tell it your name, your job, what you&apos;re working on.
          Close the tab. Open it again tomorrow. It has no idea who you are.
        </p>
        <p>
          This is the default state of every AI assistant: stateless. Each conversation
          is an island. The model might be brilliant at reasoning, but it can&apos;t
          remember that you told it your dog&apos;s name last Tuesday.
        </p>

        <h2>The Problem Isn&apos;t Intelligence</h2>
        <p>
          Large language models don&apos;t have persistent memory by design. They process
          a context window — a fixed-size buffer of text — and generate a response.
          When the conversation ends, that context is gone. There&apos;s no mechanism to
          carry information forward.
        </p>
        <p>
          Some products paper over this with conversation history. They feed previous
          messages back into the context window. But context windows have limits, and
          this approach doesn&apos;t scale. You can&apos;t fit six months of conversations
          into 200K tokens.
        </p>

        <h2>What Real Memory Looks Like</h2>
        <p>
          When I built <Link href="/">Edward</Link>, the first problem I wanted to solve
          was memory. Not &ldquo;save the last 10 messages&rdquo; memory — real, long-term,
          searchable memory that works the way yours does.
        </p>
        <p>
          Every conversation is mined for memorable information. Edward identifies facts
          (&ldquo;user&apos;s dog is named Luna&rdquo;), preferences (&ldquo;prefers dark mode&rdquo;),
          context (&ldquo;starting a new job Monday&rdquo;), and instructions (&ldquo;always
          respond in bullet points&rdquo;). These get stored with vector embeddings in
          PostgreSQL using pgvector.
        </p>
        <p>
          When you start a new conversation, Edward runs a{" "}
          <Link href="/docs/memory">hybrid retrieval</Link> — 70% vector similarity, 30%
          BM25 keyword matching — to pull in relevant memories. The result is an AI that
          actually knows you. Not because it memorized your chat logs, but because it
          extracted and indexed what matters.
        </p>

        <h2>Memory Types Matter</h2>
        <p>
          Not all information is created equal. A fact about your allergies is more
          durable than the context that you&apos;re &ldquo;on vacation this week.&rdquo; Edward
          classifies memories into four types — <code>fact</code>, <code>preference</code>,{" "}
          <code>context</code>, and <code>instruction</code> — each with different
          extraction patterns and retrieval weights.
        </p>
        <p>
          This matters for long-term accuracy. An AI that treats &ldquo;I&apos;m feeling tired
          today&rdquo; the same as &ldquo;I&apos;m allergic to shellfish&rdquo; will eventually
          surface stale context when you need critical facts.
        </p>

        <h2>Beyond Retrieval</h2>
        <p>
          Memory retrieval is step one. Edward also runs background{" "}
          <Link href="/docs/memory">consolidation</Link> — an hourly process that
          clusters related memories, flags stale information, and creates connections
          between facts. Think of it as the AI equivalent of sleeping on a problem.
        </p>
        <p>
          There&apos;s also a reflection system that generates follow-up queries after
          each conversation turn, enriching future context with memories you didn&apos;t
          explicitly ask about but that are relevant.
        </p>

        <h2>The Takeaway</h2>
        <p>
          An AI without memory is a tool. An AI with memory is an assistant. The
          difference isn&apos;t in the model — it&apos;s in the infrastructure around it.
          If you want an AI that actually knows you, memory can&apos;t be an afterthought.
          It has to be the foundation.
        </p>
      </>
    ),
  },
  {
    slug: "the-heartbeat-system",
    title: "The Heartbeat: An AI That Pays Attention",
    description:
      "Most AI assistants wait for you to talk first. Edward's heartbeat system monitors your messages and calendar so it can act before you ask.",
    publishDate: "2026-03-11",
    readingTime: "5 min read",
    tags: ["proactive AI", "AI monitoring", "heartbeat system", "AI automation"],
    content: () => (
      <>
        <p>
          Every AI assistant I&apos;ve used has the same interaction model: you talk,
          it responds. That&apos;s it. Close the app and it goes dormant until you
          come back.
        </p>
        <p>
          I wanted something different. I wanted an AI that could notice things on
          its own — an urgent message while I&apos;m busy, a calendar conflict I
          missed, an email that needs a response. That&apos;s why I built the{" "}
          <Link href="/docs/heartbeat">heartbeat system</Link>.
        </p>

        <h2>How It Works</h2>
        <p>
          The heartbeat is a multi-track background listener. It monitors three
          sources continuously:
        </p>
        <ul>
          <li>
            <strong>iMessage</strong> — polls the local Messages database every 10
            seconds for new messages
          </li>
          <li>
            <strong>Apple Calendar</strong> — watches for upcoming events with
            configurable lookahead
          </li>
          <li>
            <strong>Apple Mail</strong> — checks for unread emails that mention Edward
          </li>
        </ul>
        <p>
          Each track runs independently with its own polling interval and enable/disable
          toggle. The system is designed to be lightweight — it&apos;s not processing
          every message through a large language model.
        </p>

        <h2>The Triage Layer</h2>
        <p>
          Raw monitoring generates noise. The real work happens in the triage
          pipeline, which runs in three layers:
        </p>
        <ol>
          <li>
            <strong>Layer 1: Rules</strong> — zero-cost pattern matching. Known
            contacts, keyword filters, time-based rules. Most messages get classified
            here without touching an LLM.
          </li>
          <li>
            <strong>Layer 2: Classification</strong> — messages that pass Layer 1 go
            to Claude Haiku for urgency classification. Fast and cheap.
          </li>
          <li>
            <strong>Layer 3: Action</strong> — urgent items trigger real actions:
            store a memory, draft a response, send a push notification.
          </li>
        </ol>
        <p>
          This layered approach keeps costs near zero for routine traffic while
          still catching things that matter.
        </p>

        <h2>Proactive, Not Intrusive</h2>
        <p>
          The goal isn&apos;t to create an AI that buzzes you constantly. It&apos;s to
          create one that pays attention so you don&apos;t have to. When you open your
          next conversation, Edward already knows about the message you missed and the
          meeting that got rescheduled. It&apos;s briefed before you say a word.
        </p>
        <p>
          This context gets injected into the system prompt as a briefing. Edward
          doesn&apos;t say &ldquo;I noticed you got a message&rdquo; unprompted — it weaves
          the awareness naturally into the conversation when relevant.
        </p>

        <h2>Why This Matters</h2>
        <p>
          A personal AI assistant that only responds when spoken to is really just a
          chatbot with extra steps. The heartbeat is what makes Edward feel like an
          actual assistant — something running in the background, keeping track of
          things, ready with context when you need it.
        </p>
      </>
    ),
  },
  {
    slug: "self-evolving-ai",
    title: "Self-Evolving AI: Teaching Edward to Rewrite Himself",
    description:
      "What happens when you give an AI assistant the ability to modify its own source code? Edward's evolution system finds out.",
    publishDate: "2026-03-18",
    readingTime: "6 min read",
    tags: [
      "self-evolving AI",
      "AI self-improvement",
      "automated code generation",
      "Claude Code",
    ],
    content: () => (
      <>
        <p>
          Here&apos;s a question I couldn&apos;t stop thinking about: what if your AI
          assistant could improve itself? Not through fine-tuning or manual updates,
          but by actually writing and deploying its own code changes.
        </p>
        <p>
          Edward&apos;s evolution system does exactly that. It&apos;s a self-coding
          pipeline that creates branches, writes code, runs validation, and merges
          changes — all without human intervention.
        </p>

        <h2>The Pipeline</h2>
        <p>
          Evolution runs as a managed cycle with clear stages:
        </p>
        <ol>
          <li>
            <strong>Branch</strong> — creates a feature branch from main
          </li>
          <li>
            <strong>Code</strong> — Claude Code writes the implementation based on
            an objective
          </li>
          <li>
            <strong>Validate</strong> — runs linting, type checking, and basic
            sanity checks
          </li>
          <li>
            <strong>Test</strong> — executes the test suite against the changes
          </li>
          <li>
            <strong>Review</strong> — a separate Claude instance reviews the diff
            for quality
          </li>
          <li>
            <strong>Merge</strong> — clean changes get merged to main
          </li>
        </ol>
        <p>
          When a merge hits main, <code>uvicorn --reload</code> picks up the changes
          automatically. Edward is running the new code within seconds.
        </p>

        <h2>Why Not Just Ship Updates Manually?</h2>
        <p>
          Because I wanted to see what happens when the feedback loop is tight enough.
          Edward can identify patterns in how it&apos;s being used — tools that fail
          often, edge cases in memory retrieval, missing capabilities — and propose
          fixes for itself.
        </p>
        <p>
          It&apos;s not AGI. It&apos;s closer to a CI pipeline where the developer is
          also an AI. The constraints are important: evolution operates within defined
          boundaries, changes go through validation, and there&apos;s a rollback
          mechanism if something breaks.
        </p>

        <h2>The Safety Model</h2>
        <p>
          Giving an AI write access to its own codebase sounds dangerous. In practice,
          the guardrails make it manageable:
        </p>
        <ul>
          <li>Changes happen on branches, not directly on main</li>
          <li>Validation must pass before merge</li>
          <li>A separate review step catches issues the author might miss</li>
          <li>
            One-click rollback reverts to the previous known-good state
          </li>
          <li>The evolution config controls what kinds of changes are allowed</li>
        </ul>

        <h2>What I&apos;ve Learned</h2>
        <p>
          The most interesting result isn&apos;t the code it writes — it&apos;s the
          feedback cycle. Edward surfaces its own limitations through usage, proposes
          improvements, implements them, and then operates with the improvements in
          place. It&apos;s a closed loop between operation and development.
        </p>
        <p>
          Is it perfect? No. The code quality varies. Some proposed changes are
          brilliant; others get caught in review. But the system improves over time,
          and that&apos;s the whole point.
        </p>
      </>
    ),
  },
  {
    slug: "how-ai-memory-works",
    title: "How AI Memory Actually Works",
    description:
      "Vector search, BM25, memory types, consolidation — a technical walkthrough of the architecture behind an AI that remembers.",
    publishDate: "2026-03-25",
    readingTime: "7 min read",
    tags: [
      "AI memory architecture",
      "vector search",
      "memory consolidation",
      "memory types",
    ],
    content: () => (
      <>
        <p>
          In my <Link href="/blog/why-your-ai-forgets-you">first post</Link>, I talked
          about why AI memory matters. This one gets into the details — how Edward&apos;s
          memory system actually works under the hood.
        </p>

        <h2>The Embedding Model</h2>
        <p>
          Every memory starts as text and gets converted to a 384-dimensional vector
          using <code>all-MiniLM-L6-v2</code> from sentence-transformers. This is a
          small, fast model that runs locally — no API calls, no latency. The vectors
          get stored in PostgreSQL using the pgvector extension.
        </p>
        <p>
          I chose a local embedding model deliberately. Memory operations happen on
          every conversation turn, sometimes multiple times. Sending each one to an
          external API would add latency and cost that compounds fast.
        </p>

        <h2>Hybrid Retrieval</h2>
        <p>
          Vector similarity alone isn&apos;t enough. If you ask Edward &ldquo;what&apos;s
          my dog&apos;s name?&rdquo;, the vector for that question might be close to
          memories about pets in general, but miss the specific memory that says
          &ldquo;user&apos;s dog is named Luna&rdquo; because the word &ldquo;Luna&rdquo;
          doesn&apos;t have a strong vector signal.
        </p>
        <p>
          That&apos;s why Edward uses hybrid retrieval: <strong>70% vector similarity +
          30% BM25 keyword matching</strong>. Vector search handles semantic similarity
          (&ldquo;pet&rdquo; matches &ldquo;dog&rdquo;), while BM25 handles exact terms
          (&ldquo;Luna&rdquo; matches &ldquo;Luna&rdquo;). The weighted combination
          consistently outperforms either approach alone.
        </p>

        <h2>Extraction Pipeline</h2>
        <p>
          Memories don&apos;t appear out of nowhere. After each conversation turn,
          Edward runs an extraction step using Claude Haiku. The model receives the
          conversation and identifies information worth remembering, classified into
          four types:
        </p>
        <ul>
          <li><code>fact</code> — objective information (&ldquo;works at Acme Corp&rdquo;)</li>
          <li><code>preference</code> — likes and dislikes (&ldquo;prefers Python over JS&rdquo;)</li>
          <li><code>context</code> — situational info (&ldquo;traveling next week&rdquo;)</li>
          <li><code>instruction</code> — behavioral directives (&ldquo;always use metric units&rdquo;)</li>
        </ul>
        <p>
          The extraction model is cheap and fast. Using Haiku instead of a larger model
          keeps the per-turn cost negligible while still catching the important stuff.
        </p>

        <h2>Deep Retrieval</h2>
        <p>
          Basic retrieval runs a single query against the memory store. But some
          conversations need more context — especially longer ones where the topic
          has drifted from the original question.
        </p>
        <p>
          Edward&apos;s <Link href="/docs/memory">deep retrieval</Link> system kicks in
          when the message is short (likely a follow-up) or the turn count exceeds 3.
          It runs 4 parallel queries: the original message plus 3 Haiku-rewritten
          variants that reframe the question. The results are deduplicated and merged
          within a context budget of 8,000 characters.
        </p>

        <h2>Consolidation</h2>
        <p>
          Over time, memories accumulate. Some become stale. Others overlap. The
          consolidation service runs hourly in the background, using Haiku to:
        </p>
        <ul>
          <li>Cluster related memories into connection groups</li>
          <li>Flag memories that might be outdated</li>
          <li>Identify contradictions between memories</li>
        </ul>
        <p>
          It&apos;s conceptually similar to how your brain consolidates memories during
          sleep — reorganizing, pruning, and strengthening connections.
        </p>

        <h2>The Full Picture</h2>
        <p>
          Put it together and you get a four-stage memory lifecycle: <strong>extract</strong>{" "}
          (after each turn) → <strong>retrieve</strong> (before each turn) →{" "}
          <strong>reflect</strong> (after each turn, async) → <strong>consolidate</strong>{" "}
          (hourly background). Each stage uses the cheapest model that gets the job done,
          keeping the system fast and affordable to run.
        </p>
      </>
    ),
  },
  {
    slug: "agents-that-work-while-you-sleep",
    title: "Agents That Work While You Sleep",
    description:
      "Edward's orchestrator spawns worker agents that run tasks autonomously — scheduling, research, multi-step workflows — without you being online.",
    publishDate: "2026-04-01",
    readingTime: "5 min read",
    tags: [
      "AI orchestrator",
      "multi-agent AI",
      "scheduled AI tasks",
      "autonomous AI",
    ],
    content: () => (
      <>
        <p>
          Most AI assistants are synchronous. You send a message, you wait for a
          response. If the task takes five minutes of real work — research, multiple
          API calls, file processing — you&apos;re sitting there watching a spinner.
        </p>
        <p>
          Edward&apos;s <Link href="/docs/orchestrator">orchestrator</Link> changes
          this. It spawns lightweight worker agents that execute tasks independently,
          with full access to Edward&apos;s tools and memory.
        </p>

        <h2>Worker Agents</h2>
        <p>
          A worker is a mini-Edward. It runs within the same process, has its own
          conversation thread, and can use any tool the main Edward can — search the
          web, send messages, execute code, read documents, schedule events.
        </p>
        <p>
          When you ask Edward to do something complex, it can break the work into
          tasks and delegate them to workers. Each worker reports back when done,
          and their conversations appear in the sidebar so you can review what
          happened.
        </p>

        <h2>Scheduled Tasks</h2>
        <p>
          The orchestrator pairs naturally with Edward&apos;s{" "}
          <Link href="/docs/skills">scheduling system</Link>. You can tell Edward
          to do something tomorrow morning, next Tuesday, or every Monday at 9am.
          The scheduler fires the event, the orchestrator picks it up, and a worker
          executes it.
        </p>
        <p>
          This is where it gets interesting. Edward can schedule his own follow-ups.
          &ldquo;Remind me to check the deployment tomorrow&rdquo; becomes an actual
          scheduled event that, when it fires, gives Edward full tool access to check
          the deployment and message you with the results.
        </p>

        <h2>Crash Recovery</h2>
        <p>
          Workers can fail. The server can restart. The orchestrator handles this with
          atomic task pickup using <code>SELECT ... FOR UPDATE</code> in PostgreSQL.
          On startup, it scans for tasks that were in progress when the system went
          down and resumes them.
        </p>

        <h2>Why Not Just Use a Queue?</h2>
        <p>
          You could bolt on Celery or Redis queues. But Edward&apos;s workers aren&apos;t
          running arbitrary functions — they&apos;re having conversations with tools.
          The unit of work is a <code>chat_with_memory()</code> call, which means each
          worker gets memory retrieval, tool access, and conversation persistence for
          free. The orchestrator is thin because it delegates to infrastructure that
          already exists.
        </p>

        <h2>The Result</h2>
        <p>
          You can ask Edward to research a topic, compile the results, and email you
          a summary — then close your laptop. The workers handle it asynchronously,
          and you get a push notification when it&apos;s done. It&apos;s not just a
          chatbot anymore. It&apos;s an assistant that actually works in the background.
        </p>
      </>
    ),
  },
  {
    slug: "why-i-built-edward",
    title: "Why I Built Edward",
    description:
      "The story behind building a self-hosted AI assistant from scratch — what I wanted, what existed, and why I ended up writing it myself.",
    publishDate: "2026-04-08",
    readingTime: "6 min read",
    tags: [
      "building AI assistants",
      "open source AI",
      "personal AI project",
      "self-hosted AI",
    ],
    content: () => (
      <>
        <p>
          I didn&apos;t set out to build a full AI assistant. I started with a simple
          question: can I make an AI that remembers things about me between
          conversations?
        </p>
        <p>
          That was late 2024. ChatGPT was everywhere, Claude was getting good, and I
          was using both daily. But every conversation started from zero. I&apos;d
          explain my tech stack, my preferences, my current projects — over and over.
          It felt like having a brilliant colleague with amnesia.
        </p>

        <h2>What Existed</h2>
        <p>
          I looked at the options. ChatGPT had just launched its memory feature — a
          handful of bullet points it could store and reference. It was a start, but
          crude. You couldn&apos;t search it, you couldn&apos;t categorize it, and you
          had no control over what it remembered.
        </p>
        <p>
          Open-source options were either too simple (save chat history to a file) or
          too complex (full enterprise RAG pipelines that needed a team to operate).
          Nothing hit the sweet spot of &ldquo;personal AI that actually knows me.&rdquo;
        </p>

        <h2>The First Version</h2>
        <p>
          Edward v1 was just memory. A FastAPI backend, PostgreSQL with pgvector, and
          a simple extraction pipeline that pulled facts out of conversations. It
          worked surprisingly well. Within a week, Edward knew my dog&apos;s name, my
          coding preferences, and which projects I was working on.
        </p>
        <p>
          That success made me greedy. If it can remember things, can it also send me
          messages? Can it check my calendar? Can it run code? Each question led to a
          new feature, and each feature made Edward more useful, which generated more
          questions.
        </p>

        <h2>The Architecture</h2>
        <p>
          The stack evolved into what it is today: Next.js frontend, FastAPI backend,
          PostgreSQL for everything, and LangGraph for conversation orchestration. The
          backend runs natively on macOS — not in a container — because it needs access
          to AppleScript for iMessage, Calendar, and other system integrations.
        </p>
        <p>
          I chose LangGraph over a simple prompt chain because I needed a real tool
          loop. Edward can call tools, get results, decide to call more tools, and
          keep going for up to 5 iterations per turn. That&apos;s hard to do cleanly
          with a linear pipeline.
        </p>

        <h2>What Makes It Different</h2>
        <p>
          Edward isn&apos;t trying to be a platform. It&apos;s a personal AI — built for
          one user, running on their machine, with their data staying local. The key
          differences from hosted AI products:
        </p>
        <ul>
          <li>
            <strong>Real memory</strong> — hybrid vector + keyword retrieval, not a
            list of bullet points
          </li>
          <li>
            <strong>Proactive awareness</strong> — the heartbeat monitors messages
            and calendar without being asked
          </li>
          <li>
            <strong>Self-evolution</strong> — Edward can write and deploy improvements
            to its own code
          </li>
          <li>
            <strong>Full tool access</strong> — code execution, web search, messaging,
            file storage, scheduled tasks
          </li>
          <li>
            <strong>Self-hosted</strong> — your data stays on your machine, not on
            someone else&apos;s server
          </li>
        </ul>

        <h2>Open Source</h2>
        <p>
          I open-sourced Edward because the ideas are more interesting than the
          implementation. Memory extraction, heartbeat triage, self-evolution — these
          are patterns that any AI project can use. The code is under Apache 2.0 on{" "}
          <a href="https://github.com/ben4mn/meet-edward" target="_blank" rel="noopener noreferrer">
            GitHub
          </a>.
        </p>
        <p>
          If you&apos;re building something similar, or if you just want an AI that
          remembers you,{" "}
          <Link href="/docs/getting-started">the setup guide takes about 10
          minutes</Link>.
        </p>
      </>
    ),
  },
];

function getBuildDate(): string {
  return new Date().toISOString().split("T")[0];
}

export function getPublishedPosts(): BlogPost[] {
  const buildDate = getBuildDate();
  return posts
    .filter((p) => p.publishDate <= buildDate)
    .sort((a, b) => b.publishDate.localeCompare(a.publishDate));
}

export function getPublishedPost(slug: string): BlogPost | undefined {
  const buildDate = getBuildDate();
  return posts.find((p) => p.slug === slug && p.publishDate <= buildDate);
}

export function getAllSlugs(): string[] {
  return posts.map((p) => p.slug);
}
