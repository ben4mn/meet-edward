"use client";

// The nodes of the work rail's timeline (2026-09-26). One flat surface — no
// card inside a card: each node is a small icon chip on a hairline, a label
// in plain words (lib/toolLabels.ts), its status on the right, and the raw
// details one tap away. Prose is Inter; mono only for code and JSON.
//
//   running  → a still emerald dot         done  → duration + a muted ✓
//              (nothing loops inside the transcript — design system §7)
//   error    → ✕ in the bad tone, the error line under the label
//   stopped  → a neutral dash               outward act → Edward's emerald chip

import React, { useId, useState, type ReactNode } from "react";
import {
  AlarmClock,
  AlertTriangle,
  Archive,
  Bell,
  Bot,
  Brain,
  Calendar,
  CalendarPlus,
  CalendarX,
  Check,
  ChevronRight,
  Code2,
  Database,
  Eye,
  FilePen,
  FilePlus,
  FileSearch,
  FileText,
  Files,
  Folder,
  Globe,
  Hand,
  History,
  Image as ImageIcon,
  Inbox,
  Keyboard,
  LayoutGrid,
  Lightbulb,
  Link2,
  ListChecks,
  Mail,
  MapPin,
  MessagesSquare,
  Minus,
  Monitor,
  MousePointerClick,
  Search,
  Send,
  Server,
  Sparkles,
  Speaker,
  StickyNote,
  Tag,
  Target,
  Terminal,
  Trash2,
  User,
  Users,
  Wrench,
  X,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { ToolActivity } from "@/lib/ChatContext";
import { describeTool, pendingArgsSummary, type ToolIcon } from "@/lib/toolLabels";
import { isOutwardAct, isToolFailure } from "@/lib/turnModel";
import { formatElapsed } from "@/lib/workRail";

export const TOOL_ICONS: Record<ToolIcon, LucideIcon> = {
  alarm: AlarmClock,
  archive: Archive,
  bell: Bell,
  bot: Bot,
  brain: Brain,
  calendar: Calendar,
  "calendar-plus": CalendarPlus,
  "calendar-x": CalendarX,
  code: Code2,
  database: Database,
  eye: Eye,
  file: FileText,
  "file-pen": FilePen,
  "file-plus": FilePlus,
  "file-search": FileSearch,
  files: Files,
  folder: Folder,
  globe: Globe,
  hand: Hand,
  history: History,
  image: ImageIcon,
  inbox: Inbox,
  keyboard: Keyboard,
  layout: LayoutGrid,
  lightbulb: Lightbulb,
  link: Link2,
  "list-checks": ListChecks,
  mail: Mail,
  "map-pin": MapPin,
  messages: MessagesSquare,
  monitor: Monitor,
  note: StickyNote,
  pointer: MousePointerClick,
  search: Search,
  send: Send,
  server: Server,
  sparkles: Sparkles,
  speaker: Speaker,
  tag: Tag,
  target: Target,
  terminal: Terminal,
  trash: Trash2,
  user: User,
  users: Users,
  wrench: Wrench,
};

export type NodeState = "running" | "done" | "error" | "stopped";
export type ChipTone = "neutral" | "running" | "error" | "outward" | "muted" | "warn" | "bare";

const CHIP_TONE: Record<ChipTone, string> = {
  neutral: "bg-surface-3 text-text-secondary",
  running: "bg-terminal-soft text-terminal-strong",
  error: "bg-bad-soft text-bad",
  outward: "bg-terminal text-terminal-ink",
  muted: "bg-surface-3 text-text-muted",
  warn: "bg-warn-soft text-warn",
  // A thought, not an act: no chip, only its dot on the line.
  bare: "",
};

/** One node: a chip on the hairline, then its content. */
export function RailNode({
  chip,
  tone = "neutral",
  last = false,
  testId,
  children,
}: {
  chip: ReactNode;
  tone?: ChipTone;
  last?: boolean;
  testId?: string;
  children: ReactNode;
}) {
  return (
    <li className={cn("relative pl-[34px]", last ? "pb-0.5" : "pb-3")} data-testid={testId}>
      {!last && <span aria-hidden className="absolute bottom-0.5 left-[10.5px] top-[26px] w-px bg-border" />}
      <span
        aria-hidden
        className={cn(
          "absolute left-0 top-0 flex h-[22px] w-[22px] items-center justify-center rounded-xs [&_svg]:h-3 [&_svg]:w-3",
          CHIP_TONE[tone],
        )}
      >
        {chip}
      </span>
      <div className="min-w-0">{children}</div>
    </li>
  );
}

/** The right-hand status: a live dot, a duration and ✓, ✕, or a dash. */
export function NodeStatus({ state, ms, label }: { state: NodeState; ms?: number | null; label?: string }) {
  if (state === "running") {
    return (
      <span className="flex h-[22px] flex-none items-center" aria-label={label ?? "Running"} role="img">
        <span className="h-1.5 w-1.5 rounded-full bg-terminal" />
      </span>
    );
  }
  return (
    <span className="flex h-[22px] flex-none items-center gap-1 text-caption tabular-nums text-text-muted">
      {typeof ms === "number" && ms > 0 && state === "done" ? <span>{ms < 1000 ? `${Math.round(ms)}ms` : formatElapsed(ms)}</span> : null}
      {state === "done" && <Check className="h-3 w-3" aria-label="Done" />}
      {state === "error" && <X className="h-3 w-3 text-bad" aria-label="Failed" />}
      {state === "stopped" && (
        <>
          <span>stopped</span>
          <Minus className="h-3 w-3" aria-hidden />
        </>
      )}
    </span>
  );
}

/** A label row; a button with a chevron when it opens details. */
export function NodeRow({
  title,
  titleClass,
  status,
  expandable,
  open,
  onToggle,
  controls,
}: {
  title: ReactNode;
  titleClass?: string;
  status?: ReactNode;
  expandable?: boolean;
  open?: boolean;
  onToggle?: () => void;
  controls?: string;
}) {
  const inner = (
    <>
      <span className={cn("min-w-0 flex-1 truncate text-footnote leading-[22px]", titleClass ?? "text-text-primary")}>{title}</span>
      {status}
      {expandable ? (
        <ChevronRight
          aria-hidden
          className={cn("mt-1 h-3.5 w-3.5 flex-none text-text-muted transition-transform duration-fast", open && "rotate-90")}
        />
      ) : null}
    </>
  );
  if (!expandable) return <div className="flex min-h-[22px] items-start gap-2">{inner}</div>;
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      aria-controls={open ? controls : undefined}
      className="relative flex min-h-[22px] w-full items-start gap-2 rounded-xs text-left after:absolute after:-inset-y-2.5 after:inset-x-0 after:content-[''] focus:outline-none focus-visible:ring-2 focus-visible:ring-terminal/60"
    >
      {inner}
    </button>
  );
}

/** Argument chips under a label. */
export function ArgChips({ chips }: { chips: string[] }) {
  if (!chips.length) return null;
  return (
    <div className="mt-1 flex flex-wrap gap-1">
      {chips.map((c, i) => (
        <span
          key={i}
          className="max-w-full truncate rounded-xs bg-surface-3 px-1.5 py-0.5 text-caption font-normal text-text-secondary"
          title={c}
        >
          {c}
        </span>
      ))}
    </div>
  );
}

function looksLikeCode(text: string): boolean {
  const t = text.trim();
  if (/^[[{]/.test(t)) return true;
  const lines = t.split("\n");
  return lines.length > 2 && lines.filter((l) => /^(\s{2,}|\t|def |import |const |function |SELECT |\$ )/.test(l)).length >= 2;
}

/** Raw details: prose in Inter, code and JSON in mono. */
export function DetailText({ label, text, tone }: { label: string; text: string; tone?: "bad" }) {
  const code = looksLikeCode(text);
  return (
    <div className="mt-1.5">
      <p className="text-overline text-text-muted">{label}</p>
      <div
        className={cn(
          "mt-1 max-h-48 overflow-auto rounded-sm bg-surface-3 px-2.5 py-2 whitespace-pre-wrap break-words [overflow-wrap:anywhere]",
          code ? "font-mono text-[12px] leading-[18px]" : "text-footnote",
          tone === "bad" ? "text-bad" : "text-text-secondary",
        )}
      >
        {text}
      </div>
    </div>
  );
}

function errorLine(result: string | undefined): string | null {
  if (!result) return null;
  const first = result.split("\n")[0].replace(/^(Tool error|Unknown tool|BLOCKED):\s*/i, "").trim();
  return first ? first.charAt(0).toUpperCase() + first.slice(1) : null;
}

export function toolState(a: ToolActivity): NodeState {
  if (a.status === "running") return "running";
  if (a.status === "stopped") return "stopped";
  if (a.status === "error" || isToolFailure(a.result)) return "error";
  return "done";
}

/** A tool call in the timeline. */
export function ToolNode({ activity, last }: { activity: ToolActivity; last?: boolean }) {
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  const d = describeTool(activity.tool_name, activity.args_summary);
  const state = toolState(activity);
  const acted = state === "done" && isOutwardAct(activity.tool_name, activity.result);
  const Icon = TOOL_ICONS[d.icon] ?? Wrench;
  const err = state === "error" ? errorLine(activity.result) : null;
  const hasDetails = Boolean(activity.args_summary || activity.result);
  const tone: ChipTone = state === "running" ? "running" : state === "error" ? "error" : acted ? "outward" : state === "stopped" ? "muted" : "neutral";

  return (
    <RailNode chip={<Icon />} tone={tone} last={last} testId="rail-tool">
      <NodeRow
        title={state === "running" ? d.live : state === "error" ? d.failed : d.title}
        titleClass={cn(
          acted ? "font-medium text-terminal-strong" : state === "stopped" ? "text-text-muted" : "text-text-primary",
        )}
        status={<NodeStatus state={state} ms={activity.duration_ms} />}
        expandable={hasDetails && state !== "running"}
        open={open}
        onToggle={() => setOpen((o) => !o)}
        controls={detailsId}
      />
      {acted ? <p className="text-caption text-terminal-strong/80">Edward acted</p> : null}
      <ArgChips chips={d.chips} />
      {err ? <p className="mt-0.5 text-caption text-bad [overflow-wrap:anywhere]">{err}</p> : null}
      {open && hasDetails ? (
        <div id={detailsId}>
          {activity.args_summary ? <DetailText label="Asked" text={activity.args_summary} /> : null}
          {activity.result ? <DetailText label="Result" text={activity.result} tone={state === "error" ? "bad" : undefined} /> : null}
        </div>
      ) : null}
    </RailNode>
  );
}

/** The memory search. */
export function MemoryNode({ status, count, last }: { status: NodeState; count?: number; last?: boolean }) {
  const found = typeof count === "number" ? (count ? `${count} found` : "nothing relevant") : null;
  return (
    <RailNode chip={<Brain />} tone={status === "running" ? "running" : status === "error" ? "error" : "neutral"} last={last} testId="rail-memory">
      <NodeRow
        title={status === "running" ? "Searching memory…" : "Searched memory"}
        status={
          status === "done" ? (
            <span className="flex h-[22px] flex-none items-center text-caption tabular-nums text-text-muted">{found}</span>
          ) : (
            <NodeStatus state={status} />
          )
        }
      />
    </RailNode>
  );
}

/** Inline **bold**, *em* and `code` in reasoning text — no full markdown parse per token. */
function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\*[^*\s\n][^*\n]*\*)/g;
  let last = 0;
  let i = 0;
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) out.push(<strong key={i++} className="font-semibold text-text-secondary">{tok.slice(2, -2)}</strong>);
    else if (tok.startsWith("`")) out.push(<code key={i++} className="rounded-xs bg-surface-3 px-1 font-mono text-[0.92em]">{tok.slice(1, -1)}</code>);
    else out.push(<em key={i++}>{tok.slice(1, -1)}</em>);
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** One reasoning step: its heading and a light-markdown body. */
export function ReasoningNode({
  title,
  body,
  active,
  last,
}: {
  title: string | null;
  body: string;
  /** The step he is on right now. */
  active?: boolean;
  last?: boolean;
}) {
  const paragraphs = body.split(/\n{2,}/).map((p) => p.trim()).filter(Boolean);
  return (
    <RailNode
      chip={<span className={cn("h-[7px] w-[7px] rounded-full ring-[3px] ring-primary-bg", active ? "bg-terminal" : "bg-text-muted/60")} />}
      tone="bare"
      last={last}
      testId="rail-reasoning"
    >
      {title ? <p className="text-footnote font-medium leading-[22px] text-text-secondary">{title}</p> : null}
      {paragraphs.length ? (
        <div className={cn("space-y-1.5 text-footnote text-text-muted [overflow-wrap:anywhere]", !title && "pt-[2px]")}>
          {paragraphs.map((p, i) => (
            <p key={i} className="whitespace-pre-wrap">
              {inline(p)}
            </p>
          ))}
        </div>
      ) : null}
    </RailNode>
  );
}

/** A tool call being composed (its arguments streaming in): what they say, never the raw JSON. */
export function PendingNode({ name, args, last }: { name: string; args: string; last?: boolean }) {
  const d = describeTool(name, pendingArgsSummary(name, args));
  const Icon = TOOL_ICONS[d.icon] ?? Wrench;
  return (
    <RailNode chip={<Icon />} tone="running" last={last} testId="tool-args-stream">
      <NodeRow title={d.live} status={<NodeStatus state="running" label="Preparing" />} />
      <ArgChips chips={d.chips} />
    </RailNode>
  );
}

/** He ran out of tool steps. */
export function ExhaustedNode({ message, last }: { message: string; last?: boolean }) {
  return (
    <RailNode chip={<AlertTriangle />} tone="warn" last={last} testId="rail-exhausted">
      <NodeRow title="I ran out of steps before finishing" titleClass="text-warn" />
      {message ? <p className="text-caption text-text-muted">{message}</p> : null}
    </RailNode>
  );
}
