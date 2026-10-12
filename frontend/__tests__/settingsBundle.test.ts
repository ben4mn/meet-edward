/**
 * The Edward tab must not ship the chat's markdown renderer up front.
 *
 * MarkdownContent pulls in react-markdown, remark and react-syntax-highlighter
 * (every Prism language): ~266 kB gzipped. The landing and the rooms only show
 * a two-sentence excerpt of his page, so the renderer is loaded lazily when
 * "Read all" opens the sheet. This walks the *static* import graph (what the
 * bundler puts in the route's first-load chunks; `import()` is skipped) from
 * the settings layout, the landing and the room page.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const root = path.resolve(__dirname, "..");

const STATIC_IMPORT = /^\s*(?:import|export)\s+(?!type\b)[^'";]*?\bfrom\s*["']([^"']+)["']|^\s*import\s*["']([^"']+)["']/gm;

function resolveLocal(spec: string, fromFile: string): string | null {
  let base: string;
  if (spec.startsWith("@/")) base = path.join(root, spec.slice(2));
  else if (spec.startsWith(".")) base = path.resolve(path.dirname(fromFile), spec);
  else return null;
  for (const cand of [base, `${base}.tsx`, `${base}.ts`, path.join(base, "index.tsx"), path.join(base, "index.ts")]) {
    if (fs.existsSync(cand) && fs.statSync(cand).isFile()) return cand;
  }
  return null;
}

function packageName(spec: string): string {
  const parts = spec.split("/");
  return spec.startsWith("@") ? parts.slice(0, 2).join("/") : parts[0];
}

/** Every file and package statically reachable from `entry`, with the path that reached it. */
function staticGraph(entry: string): Map<string, string[]> {
  const seen = new Map<string, string[]>();
  const queue: Array<[string, string[]]> = [[entry, [path.relative(root, entry)]]];
  while (queue.length) {
    const [file, trail] = queue.shift()!;
    if (seen.has(file)) continue;
    seen.set(file, trail);
    if (!/\.(tsx?|jsx?)$/.test(file)) continue;
    const src = fs.readFileSync(file, "utf8");
    for (const m of src.matchAll(STATIC_IMPORT)) {
      const spec = m[1] ?? m[2];
      const local = resolveLocal(spec, file);
      if (local) {
        queue.push([local, [...trail, path.relative(root, local)]]);
      } else if (!spec.startsWith(".") && !spec.startsWith("@/")) {
        const pkg = `pkg:${packageName(spec)}`;
        if (!seen.has(pkg)) seen.set(pkg, [...trail, pkg]);
      }
    }
  }
  return seen;
}

const HEAVY = [
  path.join(root, "components/chat/MarkdownContent.tsx"),
  "pkg:react-markdown",
  "pkg:react-syntax-highlighter",
  "pkg:remark-gfm",
];

for (const entry of [
  "app/(app)/settings/layout.tsx",
  "app/(app)/settings/page.tsx",
  "app/(app)/settings/room/[room]/page.tsx",
]) {
  test(`${entry} does not statically import the markdown renderer`, () => {
    const graph = staticGraph(path.join(root, entry));
    const reached = HEAVY.filter((h) => graph.has(h)).map((h) => graph.get(h)!.join(" → "));
    assert.deepEqual(reached, []);
  });
}

test("the walker does see a static import (it would catch the regression)", () => {
  const graph = staticGraph(path.join(root, "components/chat/MessageBubble.tsx"));
  assert.ok(graph.has(path.join(root, "components/chat/MarkdownContent.tsx")));
  assert.ok(graph.has("pkg:react-markdown"));
});
