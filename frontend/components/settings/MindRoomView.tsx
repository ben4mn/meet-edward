"use client";

// The Mind room's live view: his own page (the state document) and his
// last few wakes, each in his words and linking to the thinking behind it.
// Everything comes from the shared tab status (mind/now + the state doc).

import React from "react";
import { Sparkles, Moon, AlertCircle, Clock } from "lucide-react";
import { formatRelative } from "@/lib/time";
import { reasonPhrase } from "@/lib/edwardMood";
import type { MindWakeRow } from "@/lib/mindNowApi";
import { Card, EmptyState, Notice, Row, Skeleton } from "@/components/ui";
import { PageSection } from "./SettingsPageFrame";
import { StateExcerpt } from "./StateDoc";
import { useEdwardTabStatus } from "./useEdwardTabStatus";

function capitalise(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

function wakeTitle(w: MindWakeRow): string {
  if (w.outcome === "failed") return "Didn't finish";
  if (w.outcome === "skipped") return "Skipped — out of fuel";
  if (w.line) return w.line;
  return w.outcome === "acted" ? "Acted" : "Nothing needed doing";
}

function WakeRow({ w }: { w: MindWakeRow }) {
  const reason = w.reason ? capitalise(reasonPhrase(w.reason) ?? w.reason.replace(/_/g, " ")) : "A wake";
  const when = w.at ? formatRelative(w.at) : null;
  const href = w.thought_id ? `/chat?c=${encodeURIComponent(w.thought_id)}` : "/settings/thoughts";
  const icon =
    w.outcome === "acted" ? <Sparkles aria-hidden /> : w.outcome === "failed" ? <AlertCircle aria-hidden /> : w.outcome === "skipped" ? <Clock aria-hidden /> : <Moon aria-hidden />;
  return (
    <Row
      href={href}
      icon={icon}
      tone={w.outcome === "acted" ? "edward" : w.outcome === "failed" ? "warn" : undefined}
      title={wakeTitle(w)}
      detail={when ? `${reason} · ${when}` : reason}
    />
  );
}

export function MindRoomView() {
  const { data, mindNow } = useEdwardTabStatus();
  const recent = mindNow?.recent ?? [];

  return (
    <>
      <PageSection label="On his mind">
        <Card className="p-4">
          <StateExcerpt doc={data?.stateDoc ?? null} loading={!data} failed={data?.stateDocFailed} sentences={4} />
        </Card>
      </PageSection>

      <PageSection label="Lately" action="All his thoughts" actionHref="/settings/thoughts">
        <Card className="px-3 py-0">
          {!mindNow && !data ? (
            <div className="py-4">
              <Skeleton lines={3} />
            </div>
          ) : mindNow?.waking ? (
            <Row
              icon={<Sparkles aria-hidden />}
              tone="edward"
              title={`Thinking now${reasonPhrase(mindNow.waking.reason) ? ` · ${reasonPhrase(mindNow.waking.reason)}` : ""}`}
              detail="I'll write down what I decide when I'm done."
            />
          ) : null}
          {recent.length > 0 ? (
            recent.map((w, i) => <WakeRow key={`${w.at}-${i}`} w={w} />)
          ) : mindNow ? (
            !mindNow.waking && <EmptyState>I haven&apos;t woken on my own this week.</EmptyState>
          ) : data ? (
            // The tab's status came back but mind/now did not: a failure is
            // said, never shown as a quiet week (review 2026-09-26).
            <Notice tone="warn" className="my-3">
              {"I can't reach my mind right now — trying again."}
            </Notice>
          ) : null}
        </Card>
      </PageSection>
    </>
  );
}
