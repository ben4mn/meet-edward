"use client";

// "Waiting on you": sends Edward held at the boundary for the owner's OK
// (EDWARD_TAINT_APPROVALS=on). Each one is answered right here — Send /
// Don't send, the same words as Today — and the row waits for the server:
// a send is only "sent" when POST /api/approvals/{id} says so, and it cannot
// be undone, so there is no undo toast, only a receipt. "Why I held it"
// opens the thread the held send came from. Used by the Attention room and,
// when anything is waiting, the Notifications panel (where a search for
// "approval" lands).

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Hourglass } from "lucide-react";
import { formatRelative } from "@/lib/time";
import { Button, Card, EmptyState, Row, SectionLabel, Skeleton, TONE_CHIP, useToast } from "@/components/ui";
import { cn } from "@/lib/utils";
import { approvalPreview, codexHold } from "@/components/today/todayModel";
import { decideApproval, getPendingApprovals, type PendingApproval } from "./edwardTabApi";
import { edwardTabStore } from "./useEdwardTabStatus";

type Load = { state: "loading" } | { state: "ok"; rows: PendingApproval[] } | { state: "failed" };
type Decision = "approve" | "deny";

function HeldSend({
  a,
  busy,
  error,
  onDecide,
}: {
  a: PendingApproval;
  busy: Decision | null;
  error: string | null;
  onDecide: (a: PendingApproval, d: Decision) => void;
}) {
  // A Codex session is not a message (review 2026-09-27): its whole task
  // from the stored call, where it runs, and Start / Don't start.
  const codex = codexHold(a.tool_name, a.args?.task, a.args?.cwd);
  const preview = codex ?? approvalPreview(a.summary ?? "");
  const who = preview.recipient;
  const heading = codex
    ? `Codex in ${codex.recipient}`
    : who
      ? `${preview.channel ?? "Message"} to ${who}`
      : a.summary || "A message I want to send";
  return (
    <div role="group" aria-label={`Held for your OK: ${heading}`} className="flex gap-3 border-b border-border py-3 last:border-b-0">
      <span className={cn("flex h-8 w-8 flex-none items-center justify-center rounded-sm [&_svg]:h-4 [&_svg]:w-4", TONE_CHIP.warn)}>
        <Hourglass aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-callout text-text-primary">{heading}</p>
        {preview.text ? (
          <blockquote className="mt-1 whitespace-pre-wrap rounded-md border border-border bg-surface-3/70 px-3 py-2 text-body text-text-primary">
            {preview.text}
          </blockquote>
        ) : null}
        <p className="mt-1 text-footnote text-text-muted">
          Held {a.created_at ? `${formatRelative(a.created_at)} ` : ""}for your OK
          {a.conversation_id ? (
            <>
              {" · "}
              <Link
                href={`/chat?c=${encodeURIComponent(a.conversation_id)}`}
                className="font-medium text-text-secondary underline-offset-2 hover:underline"
              >
                Why I held it
              </Link>
            </>
          ) : null}
        </p>
        {error ? (
          <p role="alert" className="mt-1 text-footnote text-bad">
            {error}
          </p>
        ) : null}
        <div className="mt-2 flex flex-wrap gap-2">
          <Button size="sm" variant="primary" loading={busy === "approve"} disabled={busy !== null} onClick={() => onDecide(a, "approve")}>
            {codex ? "Start" : "Send"}
          </Button>
          <Button size="sm" variant="secondary" loading={busy === "deny"} disabled={busy !== null} onClick={() => onDecide(a, "deny")}>
            {codex ? "Don't start" : "Don't send"}
          </Button>
        </div>
      </div>
    </div>
  );
}

export function HeldSends({ hideWhenEmpty = false, label }: { hideWhenEmpty?: boolean; label?: string }) {
  const { toast } = useToast();
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [busy, setBusy] = useState<Record<number, Decision>>({});
  const [errors, setErrors] = useState<Record<number, string>>({});

  useEffect(() => {
    let cancelled = false;
    getPendingApprovals()
      .then((rows) => !cancelled && setLoad({ state: "ok", rows }))
      .catch(() => !cancelled && setLoad({ state: "failed" }));
    return () => {
      cancelled = true;
    };
  }, []);

  const decide = useCallback(
    async (a: PendingApproval, d: Decision) => {
      setBusy((b) => ({ ...b, [a.id]: d }));
      setErrors((e) => {
        const next = { ...e };
        delete next[a.id];
        return next;
      });
      try {
        const decided = await decideApproval(a.id, d);
        setLoad((l) => (l.state === "ok" ? { state: "ok", rows: l.rows.filter((r) => r.id !== a.id) } : l));
        const who = approvalPreview(a.summary ?? "").recipient;
        if (codexHold(a.tool_name, a.args?.task)) {
          if (d === "deny") toast({ title: "Not started", tone: "neutral" });
          else if (decided?.delivered === false)
            toast({ title: "Codex didn't start", body: "The reason is in the thread.", tone: "bad" });
          else toast({ title: "Started Codex", tone: "ok" });
        } else if (d === "approve") {
          // "Sent" only on the server's word: the send tool's own reply
          // (a failed, held or duplicate send is delivered: false).
          if (decided?.delivered === false) {
            toast({
              title: who ? `It didn't go out to ${who}` : "It didn't go out",
              body: "Nothing was sent — the reason is in the thread.",
              tone: "bad",
            });
          } else toast({ title: who ? `Sent to ${who}` : "Sent", tone: "ok" });
        } else toast({ title: "Not sent", body: who ? `${who} won't get it.` : undefined, tone: "neutral" });
        // The presence line and the landing's "waiting on you" count.
        void edwardTabStore.refresh({ maxAgeMs: 0 });
      } catch (e) {
        const reason = e instanceof Error && e.message ? e.message : "";
        setErrors((x) => ({ ...x, [a.id]: `That didn't go through. ${reason}`.trim() }));
      } finally {
        setBusy((b) => {
          const next = { ...b };
          delete next[a.id];
          return next;
        });
      }
    },
    [toast],
  );

  if (hideWhenEmpty && (load.state !== "ok" || load.rows.length === 0)) return null;

  const card = (
    <Card className="px-3 py-0">
      {load.state === "loading" ? (
        <div className="py-4">
          <Skeleton lines={1} />
        </div>
      ) : load.state === "failed" ? (
        <EmptyState>I can&apos;t check what I&apos;m holding for you right now.</EmptyState>
      ) : load.rows.length === 0 ? (
        <Row icon={<Hourglass aria-hidden />} title="Nothing waiting on you" detail="When I hold a message for your OK, it shows here and on Today." />
      ) : (
        load.rows.map((a) => (
          <HeldSend key={a.id} a={a} busy={busy[a.id] ?? null} error={errors[a.id] ?? null} onDecide={decide} />
        ))
      )}
    </Card>
  );
  if (!label) return card;
  return (
    <section className="space-y-2" aria-label={label}>
      <SectionLabel>{label}</SectionLabel>
      {card}
    </section>
  );
}
