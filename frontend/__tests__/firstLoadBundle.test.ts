/**
 * What each route's first load must not carry (2026-09-26 perf pass).
 *
 * The branch had grown First Load JS well past main (/today 112 → 213 kB,
 * /hub/ask 102 → 194, /chat 398 → 492). The fixes all move code that no
 * first paint needs behind an `import()`: framer-motion's feature bundle
 * (domMax), Prism with every language, Today's sections below the fold, the
 * shell's overlays and the desktop chat sidebar, the hub's voice view and
 * greeting Edward, the login form — and leaf imports keep providers and the
 * components/ui barrel (Sheet, Switch, PanelShell) off pages that only need
 * a hook or a pip. A single static import undoes any of
 * them without failing anything else, so this walks the *static* import
 * graph (what the bundler puts in a route's first-load chunks; `import()` is
 * skipped, as are `import type`s) from each entry and pins what must stay out.
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const root = path.resolve(__dirname, "..");

const STATIC_IMPORT = /^\s*(?:import|export)\s+(?!type\b)([^'";]*?)\bfrom\s*["']([^"']+)["']|^\s*import\s*["']([^"']+)["']/gm;

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

interface Graph {
  /** Local files and `pkg:<specifier>` entries, each with the trail that reached it. */
  nodes: Map<string, string[]>;
  /** Every static import statement's bindings, per specifier ("{ Prism as X }" from "react-syntax-highlighter"). */
  bindings: Array<{ file: string; spec: string; what: string }>;
}

function staticGraph(entry: string): Graph {
  const nodes = new Map<string, string[]>();
  const bindings: Graph["bindings"] = [];
  const start = path.join(root, entry);
  const queue: Array<[string, string[]]> = [[start, [entry]]];
  while (queue.length) {
    const [file, trail] = queue.shift()!;
    if (nodes.has(file)) continue;
    nodes.set(file, trail);
    if (!/\.(tsx?|jsx?)$/.test(file)) continue;
    const src = fs.readFileSync(file, "utf8");
    for (const m of src.matchAll(STATIC_IMPORT)) {
      const spec = m[2] ?? m[3];
      const what = (m[1] ?? "").trim();
      const local = resolveLocal(spec, file);
      if (local) {
        queue.push([local, [...trail, path.relative(root, local)]]);
      } else if (!spec.startsWith(".") && !spec.startsWith("@/")) {
        bindings.push({ file: path.relative(root, file), spec, what });
        const key = `pkg:${spec}`;
        if (!nodes.has(key)) nodes.set(key, [...trail, key]);
      }
    }
  }
  return { nodes, bindings };
}

const at = (rel: string) => path.join(root, rel);

/** The trails (as text) by which `entry` statically reaches any of `targets`. */
function reached(entry: string, targets: string[]): string[] {
  const { nodes } = staticGraph(entry);
  return targets.filter((t) => nodes.has(t)).map((t) => nodes.get(t)!.join(" → "));
}

const APP_LAYOUT = "app/(app)/layout.tsx";
const ENTRIES = [
  APP_LAYOUT,
  "app/(app)/today/page.tsx",
  "app/(app)/chat/page.tsx",
  "app/(app)/settings/page.tsx",
  "app/(display)/layout.tsx",
  "app/(display)/hub/page.tsx",
  "app/(display)/hub/ask/page.tsx",
  "app/(auth)/login/page.tsx",
];

describe("first-load bundles", () => {
  it("the walker sees static imports and skips import() (it would catch a regression)", () => {
    const { nodes } = staticGraph("components/today/TodayView.tsx");
    assert.ok(nodes.has(at("components/today/NeedsSection.tsx")), "a static import is followed");
    assert.ok(!nodes.has(at("components/today/belowFold.ts")), "an import() is not");
  });

  it("framer-motion's feature bundle (domMax) is never imported statically", () => {
    for (const entry of ENTRIES) {
      const { nodes, bindings } = staticGraph(entry);
      assert.deepEqual(reached(entry, [at("components/ui/motionFeaturesMax.ts")]), [], entry);
      const direct = bindings.filter((b) => b.spec === "framer-motion" && /\bdom(Max|Animation|Min)\b/.test(b.what));
      assert.deepEqual(direct, [], `${entry} imports a framer feature bundle: ${JSON.stringify(direct)}`);
      assert.ok(nodes.size > 1);
    }
    const loader = fs.readFileSync(at("components/ui/motionFeatures.ts"), "utf8");
    assert.match(loader, /import\("\.\/motionFeaturesMax"\)/, "MOTION_FEATURES loads domMax with import()");
  });

  it("/chat does not carry Prism with every language (refractor); the async highlighter does", () => {
    const { bindings, nodes } = staticGraph("app/(app)/chat/page.tsx");
    const sync = bindings.filter(
      (b) =>
        (b.spec === "react-syntax-highlighter" && /\bPrism\b(?!Async)/.test(b.what)) ||
        /^react-syntax-highlighter\/dist\/(esm|cjs)\/prism(\.js)?$/.test(b.spec) ||
        b.spec.startsWith("refractor"),
    );
    assert.deepEqual(sync, []);
    assert.ok(nodes.has(at("components/chat/MarkdownContent.tsx")), "the markdown renderer is still on /chat");
    const md = fs.readFileSync(at("components/chat/MarkdownContent.tsx"), "utf8");
    assert.match(md, /\bPrismAsync\b/);
  });

  it("Today's sections below the fold are an import(), with skeleton stand-ins", () => {
    assert.deepEqual(
      reached("app/(app)/today/page.tsx", [
        at("components/today/belowFold.ts"),
        at("components/today/DidSection.tsx"),
        at("components/today/ConversationsSection.tsx"),
      ]),
      [],
    );
    const view = fs.readFileSync(at("components/today/TodayView.tsx"), "utf8");
    assert.match(view, /import\("\.\/belowFold"\)/);
    for (const [section, skeleton] of [
      ["DidSection", "DidSkeleton"],
      ["ConversationsSection", "ConversationsSkeleton"],
    ]) {
      assert.match(view, new RegExp(`const ${section} = lazyComponent\\([^;]*mod\\.${section}\\), ${skeleton}\\)`), section);
    }
  });

  it("Today reads presence, timezone and toasts without the providers (and ChatContext)", () => {
    assert.deepEqual(
      reached("app/(app)/today/page.tsx", [
        at("lib/ChatContext.tsx"),
        at("lib/presence.tsx"),
        at("lib/useTimezone.tsx"),
        at("components/ui/Toast.tsx"),
        at("components/ui/index.ts"),
        at("components/ui/Sheet.tsx"),
      ]),
      [],
    );
  });

  it("the shell's overlays and the desktop chat sidebar are off the (app) layout's first load", () => {
    assert.deepEqual(
      reached(APP_LAYOUT, [
        at("components/layout/shellOverlays.ts"),
        at("components/layout/PresenceSheet.tsx"),
        at("components/layout/CommandPalette.tsx"),
        at("components/layout/SignOutSheet.tsx"),
        at("components/NotificationPrompt.tsx"),
        at("components/chat/ChatSidebar.tsx"),
      ]),
      [],
    );
    const shell = fs.readFileSync(at("components/layout/ClientLayout.tsx"), "utf8");
    assert.match(shell, /import\("\.\/shellOverlays"\)/);
    assert.match(shell, /import\("@\/components\/chat\/ChatSidebar"\)/);
  });

  it("the hub's voice view, greeting Edward and the login form are their own chunks", () => {
    assert.deepEqual(
      reached("app/(display)/hub/ask/page.tsx", [at("components/chat/ContinuousVoiceView.tsx"), at("lib/ChatContext.tsx")]),
      [],
    );
    assert.deepEqual(
      reached("app/(display)/hub/page.tsx", [
        at("app/(display)/hub/components/HubEdward.tsx"),
        at("app/(display)/display/hooks/useEdwardState.ts"),
      ]),
      [],
    );
    assert.deepEqual(reached("app/(auth)/login/page.tsx", [at("app/(auth)/login/LoginForm.tsx"), "pkg:next/link"]), []);
  });

  it("the auth wait, the login door and the hub's voice page draw Edward's pulse without the rest of ui/Feedback", () => {
    for (const entry of ["app/(app)/layout.tsx", "app/(display)/layout.tsx", "app/(auth)/login/page.tsx", "app/(display)/hub/ask/page.tsx", "app/(app)/today/page.tsx"]) {
      assert.deepEqual(reached(entry, [at("components/ui/Feedback.tsx"), at("components/ui/index.ts")]), [], entry);
    }
    assert.deepEqual(reached(APP_LAYOUT, [at("components/ui/Sheet.tsx")]), [], "no sheet is open at first paint");
  });

  it("the voice view never imports ChatContext (the chat hands it setVoicePhase)", () => {
    assert.deepEqual(reached("components/chat/ContinuousVoiceView.tsx", [at("lib/ChatContext.tsx")]), []);
    const win = fs.readFileSync(at("components/chat/ChatWindow.tsx"), "utf8");
    assert.match(win, /onPhase=\{setVoicePhase\}/);
  });
});
