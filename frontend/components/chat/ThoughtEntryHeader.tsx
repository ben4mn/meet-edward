"use client";

// The bar above a thought entry — a conversation one wake of Edward's mind
// left because it took an action (2026-09-25). It makes the action obvious
// ("Edward acted · messages wake · 2h ago", "reached out" when a message
// went out or a push reached the owner — thought.reached, not every outward act)
// and asks "Right call?" without asking permission: the verdict goes to
// POST /api/mind/verdict, the same loop as the Mind panel, and lands in
// his next wake's pack. The reply box below stays — the owner can ask "why?".
// After a "No", one optional line asks what he should have done; the note
// goes with the verdict, so the next wake learns more than "wrong call"
// (2026-09-26 review).
// Wording and the optimistic verdict: lib/thoughtEntry.ts.

import React, { useState } from "react";
import { Check, X, Zap } from "lucide-react";
import { postMindVerdict } from "@/lib/api";
import {
  beginVerdict,
  describeThought,
  initialVerdict,
  settleVerdict,
  verdictView,
  type ThoughtMeta,
  type ThoughtVerdict,
} from "@/lib/thoughtEntry";
import { Pill, Pulse } from "@/components/ui";
import { cn } from "@/lib/utils";
import { reachedOut } from "./sidebarGroups";

const VERDICT_BUTTON =
  "relative inline-flex h-8 items-center gap-1 rounded-full border border-border bg-surface px-3 text-footnote font-medium text-text-primary shadow-e1 " +
  "transition-[transform,border-color,color] duration-press active:scale-[.97] after:absolute after:-inset-1.5 after:content-[''] " +
  "focus:outline-none focus-visible:ring-2 focus-visible:ring-terminal/60";

type Post = (ledgerId: number, verdict: ThoughtVerdict, note?: string) => Promise<unknown>;

type NoteState = "hidden" | "asking" | "sending" | "sent" | "failed";

export function ThoughtEntryHeader({ thought, post = postMindVerdict }: { thought: ThoughtMeta; post?: Post }) {
  const [state, setState] = useState(() => initialVerdict(thought));
  const [editing, setEditing] = useState(false);
  const [note, setNote] = useState("");
  const [noteState, setNoteState] = useState<NoteState>("hidden");
  const view = verdictView(state, editing);

  const judge = async (v: ThoughtVerdict) => {
    setEditing(false);
    setNoteState("hidden");
    setState((s) => beginVerdict(s, v));
    try {
      await post(thought.ledger_id, v);
      setState((s) => settleVerdict(s, true));
      if (v === "wrong_call") setNoteState("asking");
    } catch (e) {
      setState((s) => settleVerdict(s, false, e instanceof Error ? e.message : undefined));
    }
  };

  const sendNote = async () => {
    const text = note.trim();
    if (!text || noteState === "sending") return;
    setNoteState("sending");
    try {
      await post(thought.ledger_id, "wrong_call", text);
      setNoteState("sent");
      setNote("");
    } catch {
      setNoteState("failed");
    }
  };

  return (
    <div className="border-b border-border bg-terminal-soft px-4 py-2.5 md:px-6">
      <div className="mx-auto flex w-full max-w-3xl flex-wrap items-center gap-x-3 gap-y-2">
        <div className="flex min-w-0 items-center gap-2">
          <Zap className="h-4 w-4 flex-none text-terminal-strong" aria-hidden />
          <span className="min-w-0 text-footnote font-medium text-text-primary">{describeThought(thought)}</span>
          {reachedOut(thought) ? <Pill tone="edward">reached out</Pill> : null}
        </div>
        <div className="ml-auto flex items-center gap-2">
          {view.mode === "ask" ? (
            <>
              <span className="text-footnote text-text-secondary">Right call?</span>
              <button type="button" onClick={() => judge("right_call")} aria-label="Yes, right call" className={cn(VERDICT_BUTTON, "hover:border-ok/60 hover:text-ok")}>
                <Check className="h-3.5 w-3.5" aria-hidden />
                Yes
              </button>
              <button type="button" onClick={() => judge("wrong_call")} aria-label="No, wrong call" className={cn(VERDICT_BUTTON, "hover:border-bad/60 hover:text-bad")}>
                <X className="h-3.5 w-3.5" aria-hidden />
                No
              </button>
            </>
          ) : (
            <>
              <span className="text-footnote text-text-secondary">{view.label}</span>
              {view.saving ? <Pulse size="sm" label="Saving" /> : null}
              {!view.saving ? (
                <button
                  type="button"
                  onClick={() => setEditing(true)}
                  aria-label="Change your verdict"
                  className="relative rounded-xs px-1 text-footnote text-text-muted underline-offset-2 after:absolute after:-inset-2.5 after:content-[''] hover:text-text-primary hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-terminal/60"
                >
                  Change
                </button>
              ) : null}
            </>
          )}
        </div>
      </div>
      {state.error ? (
        <p role="alert" className="mx-auto mt-1.5 w-full max-w-3xl text-footnote text-bad">
          {state.error}
        </p>
      ) : null}
      {noteState === "asking" || noteState === "sending" || noteState === "failed" ? (
        <form
          className="mx-auto mt-2 flex w-full max-w-3xl items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void sendNote();
          }}
        >
          <input
            type="text"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="What should I have done? (optional)"
            aria-label="What should I have done?"
            enterKeyHint="send"
            autoComplete="off"
            disabled={noteState === "sending"}
            className="h-9 min-w-0 flex-1 rounded-md border border-border bg-surface px-3 text-[16px] text-text-primary shadow-e1 outline-none placeholder:text-text-muted focus-visible:ring-2 focus-visible:ring-terminal/60"
          />
          <button type="submit" disabled={!note.trim() || noteState === "sending"} className={cn(VERDICT_BUTTON, "disabled:opacity-50")}>
            Send
          </button>
          <button
            type="button"
            onClick={() => setNoteState("hidden")}
            className="relative rounded-xs px-1 text-footnote text-text-muted after:absolute after:-inset-2.5 after:content-[''] hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-terminal/60"
          >
            Skip
          </button>
        </form>
      ) : null}
      {noteState === "failed" ? (
        <p role="alert" className="mx-auto mt-1 w-full max-w-3xl text-footnote text-bad">
          I couldn&rsquo;t save that note. Try again?
        </p>
      ) : null}
      {noteState === "sent" ? (
        <p className="mx-auto mt-1.5 w-full max-w-3xl text-footnote text-text-secondary">Thanks — I&rsquo;ll read that next time I wake.</p>
      ) : null}
    </div>
  );
}
