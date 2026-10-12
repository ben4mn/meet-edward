"use client";

// The Attention room's live view: what waits on the owner (sends held for
// their approval), what Edward is watching for (his open intentions, with
// when he'll next look), and what reached the owner's phone lately. Each source
// degrades on its own; a list that cannot be read says so in his voice.

import React, { useEffect, useState } from "react";
import { BellRing, Target } from "lucide-react";
import { formatRelative } from "@/lib/time";
import { Card, EmptyState, Row, Skeleton } from "@/components/ui";
import { PageSection } from "./SettingsPageFrame";
import { HeldSends } from "./HeldSends";
import { getPushLog, type PushLogRow } from "./edwardTabApi";
import { useEdwardTabStatus } from "./useEdwardTabStatus";

type Load<T> = { state: "loading" } | { state: "ok"; rows: T[] } | { state: "failed" };

function useLoad<T>(fn: () => Promise<T[]>): Load<T> {
  const [load, setLoad] = useState<Load<T>>({ state: "loading" });
  useEffect(() => {
    let cancelled = false;
    fn()
      .then((rows) => !cancelled && setLoad({ state: "ok", rows }))
      .catch(() => !cancelled && setLoad({ state: "failed" }));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return load;
}

function pushHref(p: PushLogRow): string | undefined {
  if (!p.url) return undefined;
  return p.url.startsWith("/") ? p.url : undefined;
}

export function AttentionRoomView() {
  const pushes = useLoad(() => getPushLog(48));
  const { data } = useEdwardTabStatus();
  const intentions = data?.intentions ?? null;

  return (
    <>
      <PageSection label="Waiting on you">
        <HeldSends />
      </PageSection>

      <PageSection label="Watching for" action="In Thoughts" actionHref="/settings/thoughts">
        <Card className="px-3 py-0">
          {intentions === null ? (
            data ? (
              <EmptyState>I can&apos;t read my intentions right now.</EmptyState>
            ) : (
              <div className="py-4">
                <Skeleton lines={2} />
              </div>
            )
          ) : intentions.length === 0 ? (
            <EmptyState>Nothing I&apos;m keeping an eye on right now.</EmptyState>
          ) : (
            intentions.slice(0, 6).map((i) => (
              <Row
                key={i.id}
                href="/settings/thoughts"
                icon={<Target aria-hidden />}
                title={i.content}
                detail={
                  i.next_fire_at
                    ? `Next look ${formatRelative(i.next_fire_at)}${i.next_fire_why ? ` · ${i.next_fire_why}` : ""}`
                    : i.valid_until
                      ? `Watching · ends ${formatRelative(i.valid_until)}`
                      : "Watching"
                }
              />
            ))
          )}
        </Card>
      </PageSection>

      <PageSection label="Reached your phone" action="Last 2 days">
        <Card className="px-3 py-0">
          {pushes.state === "loading" ? (
            <div className="py-4">
              <Skeleton lines={1} />
            </div>
          ) : pushes.state === "failed" ? (
            <EmptyState>I can&apos;t read the notification log right now.</EmptyState>
          ) : pushes.rows.length === 0 ? (
            <EmptyState>I haven&apos;t buzzed your phone in two days.</EmptyState>
          ) : (
            pushes.rows.slice(0, 8).map((p) => (
              <Row
                key={p.id}
                href={pushHref(p)}
                icon={<BellRing aria-hidden />}
                title={p.title}
                detail={`${formatRelative(p.sent_at)} · ${
                  p.held_reason ? `held — ${p.held_reason.replace(/_/g, " ")}` : p.failed ? "didn't arrive" : p.opened_at ? "opened" : "not opened"
                }`}
              />
            ))
          )}
        </Card>
      </PageSection>
    </>
  );
}
