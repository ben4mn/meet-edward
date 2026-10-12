import type { Metadata } from "next";
import Link from "next/link";
import { DocsContent } from "../../../components/docs/DocsContent";
import { GITHUB_BLOB } from "../../../lib/docsNav";

export const metadata: Metadata = {
  title: "Memory — Edward Docs",
  description:
    "How Edward remembers: hybrid vector and keyword search over pgvector, provenance on every memory (who said it, about whom, how sure), extraction after each turn, weekly upkeep that never deletes, contradiction questions, documents and a verified forget.",
  alternates: { canonical: "/docs/memory" },
  openGraph: {
    title: "Memory — Edward Docs",
    description:
      "Hybrid retrieval, provenance on every memory, upkeep that never deletes, and a verified forget.",
    url: "/docs/memory",
  },
};

export default function MemoryPage() {
  return (
    <DocsContent>
      <h1>Memory</h1>
      <p className="subtitle">
        What he remembers, where he heard it, and who may change it.
      </p>

      <h2>The store</h2>
      <p>
        Every memory is a row in your PostgreSQL database with an embedding
        from a small local model (<code>all-MiniLM-L6-v2</code>, 384
        dimensions, run on your Mac). Retrieval blends{" "}
        <strong>70 % vector similarity</strong> (pgvector) with{" "}
        <strong>30 % BM25 keyword matching</strong>: the first finds “dog”
        when you asked about pets, the second finds “Biscuit” when you named
        her.
      </p>
      <p>A memory has a type:</p>
      <table>
        <thead>
          <tr>
            <th>Type</th>
            <th>Example</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>fact</code></td>
            <td>“Biscuit is a beagle, born in 2019”</td>
          </tr>
          <tr>
            <td><code>preference</code></td>
            <td>“Prefers window seats”</td>
          </tr>
          <tr>
            <td><code>context</code></td>
            <td>“Is in Springfield until Friday”</td>
          </tr>
          <tr>
            <td><code>instruction</code></td>
            <td>“Don’t reply in the family chat without asking”</td>
          </tr>
          <tr>
            <td><code>intention</code></td>
            <td>Something Edward decided to do or watch, with a horizon</td>
          </tr>
        </tbody>
      </table>

      <h2>Provenance: where he heard it</h2>
      <p>
        Every row also records where it came from, so he can tell what you
        said from what he read or concluded:
      </p>
      <ul>
        <li>
          <strong>Origin</strong>: <code>heard</code> (you said it),{" "}
          <code>read</code> (he read it in a message, a page or a screen),{" "}
          <code>thought</code> (he concluded or decided it) or{" "}
          <code>did</code> (an action and its outcome).
        </li>
        <li>
          <strong>About</strong>: you, a contact, Edward himself, or the
          world.
        </li>
        <li>
          <strong>Tier</strong>: observation → belief → knowledge, climbing as
          it is confirmed. A dated episode (“the flight on 3 June 2026 was
          late”) stops at belief.
        </li>
        <li>
          <strong>Source</strong>: the conversation, turn, message or page it
          came from.
        </li>
      </ul>
      <p>
        Ask “how do you know that?” and he answers from that record, and only
        from it. The Memory room shows the same record for every row.
      </p>

      <h2>Only your words are yours</h2>
      <p>
        A memory counts as something you said only when it came from your own
        turn: your chat, your voice, the widget, Alexa, your self-chat, or
        your own words in an <code>@Edward</code> ping. A wake, a scheduled
        reminder, someone else’s message or the household hub’s voice can’t
        write a memory as yours. And only you can change or forget what you
        said: from a turn that isn’t yours, his memory tools refuse to touch
        it. An instruction you want kept for good can be pinned as confirmed,
        and nothing retires or rewrites a pin.
      </p>
      <p>
        <strong>Standing instructions</strong> are your own instructions
        about yourself. They ride along in every wake, ahead of everything
        else.
      </p>

      <h2>After each turn</h2>
      <ul>
        <li>
          <strong>Extraction.</strong> A small model (the{" "}
          <code>background</code> route, Haiku 5.5) reads the turn and files
          what is worth keeping with its type, importance, tier and source. A
          near-duplicate reinforces the existing row instead of adding one.
        </li>
        <li>
          <strong>Use is citation.</strong> A memory counts as used only when
          the answer actually drew on it, not whenever it was retrieved, so
          popular rows can’t entrench themselves.
        </li>
        <li>
          <strong>Reflection</strong> looks for related memories in the
          background and hands them to the next turn.
        </li>
        <li>
          <strong>Search tags</strong> make the conversation findable later.
        </li>
      </ul>
      <p>
        Before a turn, <strong>deep retrieval</strong> runs a few rewritten
        queries alongside yours when the message is short or the conversation
        has run on, and memories linked to the people and places you mention
        come along too.
      </p>

      <h2>Horizons</h2>
      <p>
        Some things are meant to lapse. A one-off preference (“no onions in
        tonight’s order”) or a temporary instruction gets a 30-day horizon; an
        intention from a wake gets 72 hours unless a scheduled follow-up holds
        it. Telling him a taste is lasting takes the horizon off.
      </p>

      <h2>Upkeep that never deletes</h2>
      <ul>
        <li>
          <strong>Weekly decay</strong> (Sunday’s nightly) retires stale rows
          by stamping an end date. It never deletes, and one command undoes a
          pass. It never retires anything sensitive (pets, health, family,
          money, someone in distress, and the names in{" "}
          <code>EDWARD_SENSITIVE_NAMES</code>), stated timeless facts, or
          your own instructions.
        </li>
        <li>
          <strong>Hourly consolidation</strong> links related memories and
          merges true duplicates, never your own words.
        </li>
        <li>
          <strong>Contradictions</strong>: at most five new flags an hour, one
          per pair. If neither side is your own word, he keeps both. If one
          is, he asks you, one “Which is right?” at a time, on Today.
        </li>
      </ul>

      <h2>Documents</h2>
      <p>
        Beside memories he keeps whole documents: recipes, notes, lists,
        reference pages, and his own <code>Edward: state</code> page, which
        he rewrites on Sundays. Documents are searched the same way, and their
        titles come up in conversation when relevant. Browse them in Memory →
        Documents.
      </p>

      <h2>Forgetting</h2>
      <p>
        Ask him to forget something and he first shows the plan: the memories
        that match (whole words, linked entities, close meanings) and what
        hangs off them. He deletes exactly that plan, checks, and logs only
        the counts. Only you can ask, in your own conversation, and you can’t
        forget yourself (that would take the whole store). The Memory room
        has a “Forget…” sheet that does the same.
      </p>

      <h2>The knowledge graph (optional)</h2>
      <p>
        Edward can also keep a graph of people, places and how they relate
        (Graphiti on Kuzu), filled by its own worker. It is off by default
        and takes two switches; see{" "}
        <Link href="/docs/configuration#off-by-default">Configuration</Link>.
        Each memory costs a few background model calls.
      </p>

      <hr />
      <p>
        The long form, with the reasons behind each rule, is{" "}
        <a href={`${GITHUB_BLOB}/docs/architecture/memory.md`}>memory.md</a>.
      </p>
    </DocsContent>
  );
}
