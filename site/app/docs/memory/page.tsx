import type { Metadata } from "next";
import { DocsContent } from "../../../components/docs/DocsContent";

export const metadata: Metadata = {
  title: "Memory System — Edward Docs",
  description: "How Edward remembers — hybrid vector + BM25 retrieval, memory types and confidence tiers, automatic extraction via Claude Haiku, deep retrieval, reflection, and consolidation.",
  alternates: { canonical: "/docs/memory" },
  openGraph: {
    title: "Memory System — Edward Docs",
    description: "How Edward remembers — hybrid retrieval, memory types, automatic extraction, deep retrieval, and consolidation.",
    url: "/docs/memory",
  },
};

export default function MemoryPage() {
  return (
    <DocsContent>
      <h1>Memory System</h1>
      <p className="subtitle">
        How Edward remembers everything — types, retrieval, extraction, and
        background enrichment.
      </p>

      <h2>Overview</h2>
      <p>
        Edward&apos;s memory system is what makes him different from a stateless
        chatbot. Every conversation is mined for memorable information — facts,
        preferences, context, instructions — and stored in PostgreSQL with vector
        embeddings. On future turns, relevant memories are retrieved and injected
        into the LLM context so Edward can reference things you told him weeks
        ago.
      </p>

      <h2>Memory Types</h2>
      <p>
        Each memory is classified into one of four types during extraction:
      </p>
      <table>
        <thead>
          <tr>
            <th>Type</th>
            <th>Description</th>
            <th>Example</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>fact</code></td>
            <td>Objective information about the user or world</td>
            <td>&quot;User&apos;s dog is named Luna&quot;</td>
          </tr>
          <tr>
            <td><code>preference</code></td>
            <td>User likes, dislikes, or style preferences</td>
            <td>&quot;Prefers dark mode in all apps&quot;</td>
          </tr>
          <tr>
            <td><code>context</code></td>
            <td>Situational or temporal context</td>
            <td>&quot;Starting a new job at Acme Corp next Monday&quot;</td>
          </tr>
          <tr>
            <td><code>instruction</code></td>
            <td>Explicit directives from the user</td>
            <td>&quot;Always respond in bullet points&quot;</td>
          </tr>
        </tbody>
      </table>

      <h2>Temporal Nature</h2>
      <p>
        Memories also carry a temporal nature that affects how they&apos;re
        weighted over time:
      </p>
      <table>
        <thead>
          <tr>
            <th>Temporal</th>
            <th>Description</th>
            <th>Behavior</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>timeless</code></td>
            <td>Permanently relevant facts</td>
            <td>No decay — always full weight</td>
          </tr>
          <tr>
            <td><code>temporary</code></td>
            <td>Short-lived context</td>
            <td>Decays over time, eventually irrelevant</td>
          </tr>
          <tr>
            <td><code>evolving</code></td>
            <td>Facts that may change</td>
            <td>Boosted when recently updated, decays otherwise</td>
          </tr>
        </tbody>
      </table>

      <h2>Memory Tiers</h2>
      <p>
        Each memory is assigned a confidence tier:
      </p>
      <table>
        <thead>
          <tr>
            <th>Tier</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>observation</code></td>
            <td>Inferred from conversation — may not be explicitly stated</td>
          </tr>
          <tr>
            <td><code>belief</code></td>
            <td>Reasonably confident based on context</td>
          </tr>
          <tr>
            <td><code>knowledge</code></td>
            <td>Explicitly stated by the user — high confidence</td>
          </tr>
        </tbody>
      </table>

      <h2>Hybrid Retrieval</h2>
      <p>
        When Edward needs to recall memories, he uses a hybrid scoring approach:
      </p>
      <ul>
        <li><strong>70% vector similarity</strong> — pgvector cosine distance using <code>all-MiniLM-L6-v2</code> embeddings (384 dimensions)</li>
        <li><strong>30% BM25 keyword matching</strong> — traditional text search for exact term hits</li>
      </ul>
      <p>
        This combination catches both semantically similar memories and ones that
        share specific keywords. The context budget is capped at 8,000 characters
        to avoid overwhelming the LLM.
      </p>

      <h2>Memory Extraction</h2>
      <p>
        After every conversation turn, Edward runs a memory extraction step using
        Claude Haiku 4.5. The extractor analyzes the conversation and identifies
        any new memorable information. For each extracted memory, it assigns:
      </p>
      <ul>
        <li>Memory type (<code>fact</code>, <code>preference</code>, <code>context</code>, <code>instruction</code>)</li>
        <li>Importance score (0-10)</li>
        <li>Temporal nature (<code>timeless</code>, <code>temporary</code>, <code>evolving</code>)</li>
        <li>Confidence tier (<code>observation</code>, <code>belief</code>, <code>knowledge</code>)</li>
      </ul>
      <p>
        Duplicate detection prevents the same information from being stored
        multiple times. Existing memories are updated rather than duplicated.
      </p>

      <h2>Deep Retrieval</h2>
      <p>
        For complex conversations, Edward activates deep retrieval — a pre-turn
        gate that runs when the message is short or the conversation has reached
        3+ turns. It fires 4 parallel memory queries:
      </p>
      <ol>
        <li>The original user message</li>
        <li>3 Haiku-rewritten query variations targeting different angles</li>
      </ol>
      <p>
        Results are deduplicated and merged, giving the LLM a richer context
        window than a single query would provide.
      </p>

      <h2>Reflection</h2>
      <p>
        After each turn, a fire-and-forget reflection step generates 3-5
        Haiku-crafted queries to find memories related to the current
        conversation. The results are stored in the{" "}
        <code>memory_enrichments</code> table and loaded on the <em>next</em>{" "}
        turn to provide deeper context. This runs asynchronously and adds zero
        latency to the current response.
      </p>

      <h2>Consolidation</h2>
      <p>
        An hourly background loop clusters related memories via Haiku. It
        creates:
      </p>
      <ul>
        <li><strong>Memory connections</strong> — links between related memories</li>
        <li><strong>Memory flags</strong> — quality and staleness markers</li>
      </ul>
      <p>
        Consolidation is disabled by default and can be enabled via the REST
        API or settings UI.
      </p>

      <h2>Memory Tools</h2>
      <p>
        Edward has direct access to memory management tools (always available,
        not gated by skills):
      </p>
      <table>
        <thead>
          <tr>
            <th>Tool</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>remember_update</code></td>
            <td>Create or update a memory</td>
          </tr>
          <tr>
            <td><code>remember_forget</code></td>
            <td>Delete a specific memory</td>
          </tr>
          <tr>
            <td><code>remember_search</code></td>
            <td>Search memories by query</td>
          </tr>
        </tbody>
      </table>
    </DocsContent>
  );
}
