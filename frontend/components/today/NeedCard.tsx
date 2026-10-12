"use client";

// One thing that needs the owner (UX pass 2026-09-26). The whole card opens where
// the need lives (a stretched link under the title); its buttons sit above
// that link. The owner's primary action is the neutral high-contrast button; tone
// is a small tinted chip, not a border. A held send quotes who and what; a
// contradiction is answered in place by tapping the memory that is right.

import React, { forwardRef, useEffect, useState } from "react";
import Link from "next/link";
import {
  Activity,
  CalendarClock,
  Clock,
  ListChecks,
  Mail,
  MessageCircle,
  PawPrint,
  Repeat,
  Scale,
  ShieldCheck,
  StickyNote,
} from "lucide-react";
import { Button } from "@/components/ui/Button";
import { TONE_CHIP } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";
import {
  actionLabel,
  approvalFor,
  bothFineAction,
  cardActions,
  needCountLabel,
  needDetail,
  needHref,
  needMeta,
  needTitle,
  needVisual,
  type NeedIcon,
  type TodayAction,
  type TodayNeed,
} from "./todayModel";

const ICONS: Record<NeedIcon, React.ComponentType<{ className?: string }>> = {
  paw: PawPrint,
  shield: ShieldCheck,
  calendar: CalendarClock,
  message: MessageCircle,
  loop: Repeat,
  clock: Clock,
  worker: ListChecks,
  note: StickyNote,
  question: Scale,
  mail: Mail,
  activity: Activity,
};

/** The full text of a held send, when the feed's summary was cut short. */
async function fetchApprovalText(needId: string): Promise<string | null> {
  const id = needId.replace(/^approval:/, "");
  try {
    const res = await fetch(`/api/approvals/${encodeURIComponent(id)}`, { credentials: "include" });
    if (!res.ok) return null;
    const row = (await res.json()) as { args?: Record<string, unknown> };
    const msg = row?.args?.message ?? row?.args?.text ?? row?.args?.body;
    return typeof msg === "string" && msg.trim() ? msg : null;
  } catch {
    return null;
  }
}

function ApprovalQuote({ need }: { need: TodayNeed }) {
  const preview = approvalFor(need);
  const [full, setFull] = useState<string | null>(null);
  useEffect(() => {
    if (!preview.truncated) return;
    let live = true;
    void fetchApprovalText(need.id).then((t) => {
      if (live && t) setFull(t);
    });
    return () => {
      live = false;
    };
  }, [need.id, preview.truncated]);
  if (!preview.text) {
    return need.detail ? <p className="mt-0.5 text-footnote text-text-secondary">{need.detail}</p> : null;
  }
  return (
    <figure className="mt-2">
      <figcaption className="text-caption text-text-muted">
        {preview.channel} {preview.connector ?? "to"} <span className="text-text-secondary">{preview.recipient}</span>
      </figcaption>
      <blockquote className="mt-1 whitespace-pre-wrap break-words rounded-md border border-border bg-surface-3/70 px-3 py-2 text-body text-text-primary">
        {preview.subject ? <span className="mb-1 block font-semibold">{preview.subject}</span> : null}
        {full ?? preview.text}
      </blockquote>
    </figure>
  );
}

function Choice({
  need,
  busy,
  onAction,
}: {
  need: TodayNeed;
  busy: boolean;
  onAction: (a: TodayAction) => void;
}) {
  const choice = need.choice!;
  const byId = new Map(need.actions.map((a) => [a.id, a]));
  return (
    <div className="relative z-[1] mt-3 space-y-2" role="group" aria-label="Which memory is right?">
      {choice.options.map((o) => {
        const a = byId.get(o.action_id);
        if (!a) return null;
        return (
          <button
            key={o.id}
            type="button"
            disabled={busy}
            onClick={() => onAction(a)}
            className={cn(
              "flex min-h-[44px] w-full items-center gap-3 rounded-md border border-border bg-surface-3/60 px-3 py-2.5 text-left",
              "transition-[transform,background-color,border-color] duration-press ease-out hover:border-text-muted/40 hover:bg-surface-3 active:scale-[.985]",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-terminal/60 disabled:opacity-50",
            )}
          >
            <span className="min-w-0 flex-1 text-footnote text-text-primary">“{o.text}”</span>
            {/* Each memory is its own answer, so both say the same (review 2026-09-26). */}
            <span className="flex-none text-caption font-semibold text-text-secondary">This one</span>
          </button>
        );
      })}
    </div>
  );
}

export interface NeedCardProps {
  need: TodayNeed;
  /** The action in flight (a send waits for the server before it leaves). */
  busyAction: string | null;
  error: string | null;
  onAction: (need: TodayNeed, action: TodayAction) => void;
  now: Date;
  tz: string | undefined;
}

export const NeedCard = forwardRef<HTMLAnchorElement, NeedCardProps>(function NeedCard(
  { need, busyAction, error, onAction, now, tz },
  linkRef,
) {
  const { icon, tone } = needVisual(need);
  const Icon = ICONS[icon];
  const { primary, secondary, links } = cardActions(need);
  const count = needCountLabel(need);
  const meta = needMeta(need, { now, tz });
  const busy = busyAction !== null;
  const title = needTitle(need);
  const isApproval = need.kind === "approval";
  const detail = needDetail(need);

  return (
    <article
      data-need-id={need.id}
      className={cn(
        "relative rounded-lg border border-border bg-surface p-3.5 shadow-e1",
        "transition-[transform,background-color,box-shadow] duration-press ease-out",
        "hover:bg-surface-elevated has-[.need-link:active]:scale-[.985]",
        "has-[.need-link:focus-visible]:ring-2 has-[.need-link:focus-visible]:ring-terminal/60",
      )}
    >
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className={cn("mt-px flex h-9 w-9 flex-none items-center justify-center rounded-md [&_svg]:h-[18px] [&_svg]:w-[18px]", TONE_CHIP[tone])}
        >
          <Icon />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-start gap-2">
            <Link
              ref={linkRef}
              href={needHref(need)}
              className="need-link min-w-0 flex-1 pt-0.5 text-callout text-text-primary after:absolute after:inset-0 after:rounded-lg after:content-[''] focus-visible:outline-none"
            >
              {title}
            </Link>
            {meta || count ? (
              <span className="flex-none pt-1 text-caption tabular-nums text-text-muted" suppressHydrationWarning>
                {[count, meta].filter(Boolean).join(" · ")}
              </span>
            ) : null}
          </div>
          {isApproval ? (
            <ApprovalQuote need={need} />
          ) : detail ? (
            <p className="mt-0.5 line-clamp-3 break-words text-footnote text-text-secondary">{detail}</p>
          ) : null}
          {need.choice ? <Choice need={need} busy={busy} onAction={(a) => onAction(need, a)} /> : null}
          {error ? (
            <p role="alert" className="relative z-[1] mt-2 text-footnote text-bad">
              {error}
            </p>
          ) : null}
          {primary || secondary.length || links.length || need.choice ? (
            <div className="relative z-[1] mt-3 flex flex-wrap gap-2">
              {primary ? (
                <Button variant="primary" loading={busyAction === primary.id} disabled={busy} onClick={() => onAction(need, primary)}>
                  {actionLabel(need, primary)}
                </Button>
              ) : null}
              {need.choice ? (
                <Button variant="secondary" disabled={busy} onClick={() => onAction(need, bothFineAction(need.choice!))}>
                  Both fine
                </Button>
              ) : null}
              {secondary.map((a) => (
                <Button
                  key={a.id}
                  variant={need.choice ? "ghost" : "secondary"}
                  loading={busyAction === a.id}
                  disabled={busy}
                  onClick={() => onAction(need, a)}
                >
                  {actionLabel(need, a)}
                </Button>
              ))}
              {links.map((a) => (
                <Button key={a.id} variant="ghost" href={a.url}>
                  {actionLabel(need, a)}
                </Button>
              ))}
            </div>
          ) : null}
        </div>
      </div>
    </article>
  );
});
