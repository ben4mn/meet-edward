// R/G red for "What does Edward have on…" (Memory room's live view).
//
// Files have no server search, so the old view fetched the newest 8 and
// filtered those — a file from last month could never be found. Now the
// whole list (up to the API's 100) is matched word by word on name,
// description, tags and category. Every hit deep-links to its panel with
// ?id= (databases, addressed by name, with ?name=).

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  queryTokens,
  matchesQuery,
  fileHits,
  dbHits,
  memoryHits,
  docHits,
  hitHref,
  kindCounts,
  FILE_FETCH_LIMIT,
} from "../components/settings/knowledgeModel.ts";

function file(id: string, filename: string, extra: Partial<{ description: string | null; tags: string | null; category: string; created_at: string | null; size_bytes: number }> = {}) {
  return {
    id,
    filename,
    description: null,
    tags: null,
    category: "document",
    created_at: "2026-09-20T10:00:00Z",
    size_bytes: 2048,
    ...extra,
  };
}

describe("queryTokens / matchesQuery", () => {
  it("splits on whitespace, lowercases, and needs every word", () => {
    assert.deepEqual(queryTokens("  Biscuit  LABS "), ["biscuit", "labs"]);
    assert.ok(matchesQuery("biscuit-labs.jpg Biscuit bloodwork photo", "biscuit photo"));
    assert.ok(!matchesQuery("biscuit-labs.jpg", "biscuit photo"));
  });
  it("matches everything for an empty query", () => {
    assert.ok(matchesQuery("anything", "   "));
  });
});

describe("fileHits", () => {
  const files = Array.from({ length: 30 }, (_, i) =>
    file(`f-${i}`, `note-${i}.md`, { created_at: `2026-09-${String(i + 1).padStart(2, "0")}T10:00:00Z` }),
  );
  files.push(file("f-old", "sitter-sheet.pdf", { description: "Pet sitter instructions", tags: "pets,biscuit", created_at: "2026-08-01T10:00:00Z" }));

  it("finds an old file the newest-eight view could never reach", () => {
    const hits = fileHits(files, "sitter");
    assert.deepEqual(hits.map((h) => h.id), ["f:f-old"]);
    assert.equal(hits[0].href, "/settings/files?id=f-old");
    assert.equal(hits[0].kind, "file");
  });
  it("searches description, tags and category too", () => {
    assert.deepEqual(fileHits(files, "biscuit instructions").map((h) => h.id), ["f:f-old"]);
  });
  it("shows the newest few with no query", () => {
    const hits = fileHits(files, "", 5);
    assert.equal(hits.length, 5);
    assert.equal(hits[0].id, "f:f-29");
  });
  it("fetches as many as the API allows", () => {
    assert.equal(FILE_FETCH_LIMIT, 100);
  });
});

describe("dbHits", () => {
  const dbs = [
    { id: "db-runs", name: "runs", description: "Ellis's training runs from Strava", updated_at: "2026-09-20T10:00:00Z" },
    { id: "db-pets", name: "pet_weights", description: "Monthly pet weights", updated_at: null },
  ];
  it("matches name and description and links by name", () => {
    const hits = dbHits(dbs, "strava");
    assert.equal(hits.length, 1);
    assert.equal(hits[0].href, "/settings/databases?name=runs");
    assert.equal(hits[0].title, "runs");
  });
});

describe("memoryHits / docHits", () => {
  it("carry the id into the link", () => {
    const m = memoryHits([{ id: "m-1", content: "Ellis's partner is Rowan", memory_type: "fact", tier: "knowledge", updated_at: null, created_at: "2026-09-20T10:00:00Z" }]);
    assert.equal(m[0].href, "/settings/memories?id=m-1");
    assert.equal(m[0].detail, "Fact · Known");
    const d = docHits([{ id: "d-state", title: "Edward: state", tags: "state,self", updated_at: "2026-09-26T10:00:00Z" }]);
    assert.equal(d[0].href, "/settings/documents?id=d-state");
  });
  it("speak the Memories and Documents panels' words, not the backend's", () => {
    const m = memoryHits([
      { id: "m-1", content: "a", memory_type: "fact", tier: "belief", updated_at: null, created_at: null },
      { id: "m-2", content: "b", memory_type: "intention", tier: "observation", updated_at: null, created_at: null },
    ]);
    assert.deepEqual(m.map((h) => h.detail), ["Fact · Believed", "Intention · Noticed"]);
    const d = docHits([
      { id: "d-state", title: "Edward: state", tags: "state,self", updated_at: null },
      { id: "d-goal", title: "Goal: Run the Springfield half", tags: "goal,active", updated_at: null },
      { id: "d-cabin", title: "Cabin weekend", tags: "trip,plans", updated_at: null },
    ]);
    assert.deepEqual(d.map((h) => h.title), ["My state", "Run the Springfield half", "Cabin weekend"]);
    assert.deepEqual(d.map((h) => h.detail), ["His own page", "Goal", "trip, plans"]);
  });
});

describe("hitHref", () => {
  it("escapes the key", () => {
    assert.equal(hitHref("doc", "a b/c"), "/settings/documents?id=a%20b%2Fc");
    assert.equal(hitHref("db", "pet weights"), "/settings/databases?name=pet%20weights");
  });
});

describe("kindCounts", () => {
  it("counts hits per kind for the segment", () => {
    const counts = kindCounts([
      { id: "m:1", kind: "memory", title: "", detail: "", href: "" },
      { id: "m:2", kind: "memory", title: "", detail: "", href: "" },
      { id: "f:1", kind: "file", title: "", detail: "", href: "" },
    ]);
    assert.deepEqual(counts, { all: 3, memory: 2, doc: 0, file: 1, db: 0 });
  });
});
