"use client";

// Settings → Attention → Notifications. One sentence saying whether Edward
// can reach this device, a switch for it, the last pushes (what, from
// which part of him, opened or not), and the diagnostics folded behind
// "Troubleshoot". The wording is pure: lib/pushPanelModel.ts.

import React, { useCallback, useEffect, useState } from "react";
import { Bell, BellOff, RefreshCw, Send } from "lucide-react";
import {
  clearDismissed,
  getNotificationPermission,
  getPushStatus,
  isPushSupported,
  isPWA,
  isSubscribed,
  subscribeToPush,
  unsubscribeFromPush,
} from "@/lib/notifications";
import { pushHeadline, pushLogRows, type PushLogRow } from "@/lib/pushPanelModel";
import { getPushLog, sendTestPush } from "@/lib/panelsApi";
import { Button, Card, EmptyState, Notice, Row, SectionLabel, Skeleton, Switch, cn } from "@/components/ui";
import { HeldSends } from "./HeldSends";

type Flash = { tone: "ok" | "bad"; text: string };

const STATE_CLASS: Record<PushLogRow["state"], string> = {
  opened: "text-ok",
  "not opened": "text-text-muted",
  held: "text-warn",
  "didn't arrive": "text-bad",
};

export function NotificationsPanel(_props: { isExpanded?: boolean; hideHeader?: boolean }) {
  const [supported, setSupported] = useState<boolean | null>(null);
  const [permission, setPermission] = useState<string>("unsupported");
  const [subscribed, setSubscribed] = useState(false);
  const [configured, setConfigured] = useState(false);
  const [reachable, setReachable] = useState(true);
  const [count, setCount] = useState(0);
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [flash, setFlash] = useState<Flash | null>(null);
  const [log, setLog] = useState<PushLogRow[] | null>(null);
  const [logError, setLogError] = useState(false);
  const [troubleshoot, setTroubleshoot] = useState(false);

  const refresh = useCallback(async () => {
    setSupported(isPushSupported());
    setPermission(getNotificationPermission());
    // The server answer must never wait on the service worker.
    const [sub, status] = await Promise.all([isSubscribed(), getPushStatus()]);
    setSubscribed(sub);
    setReachable(status.reachable);
    setConfigured(status.configured);
    setCount(status.subscriptionCount);
    setChecked(true);
  }, []);

  const loadLog = useCallback(async () => {
    try {
      setLog(pushLogRows(await getPushLog()));
      setLogError(false);
    } catch {
      setLogError(true);
    }
  }, []);

  useEffect(() => {
    refresh();
    loadLog();
  }, [refresh, loadLog]);

  const run = async (key: string, fn: () => Promise<Flash>) => {
    setBusy(key);
    setFlash(null);
    try {
      setFlash(await fn());
    } catch (e) {
      setFlash({ tone: "bad", text: e instanceof Error ? e.message : "That didn't work." });
    }
    await refresh();
    setBusy(null);
  };

  const turn = (on: boolean) =>
    run(on ? "subscribe" : "unsubscribe", async () => {
      if (on) {
        clearDismissed();
        const r = await subscribeToPush();
        return r.success
          ? { tone: "ok", text: "Done — I can reach you here now." }
          : { tone: "bad", text: r.error || "I couldn't turn notifications on." };
      }
      const r = await unsubscribeFromPush();
      return r.success
        ? { tone: "ok", text: "Off on this device. I'll use Today instead." }
        : { tone: "bad", text: r.error || "I couldn't turn notifications off." };
    });

  const reset = () =>
    run("reset", async () => {
      // Unsubscribe here and on the server, clear the dismissed flag, then
      // re-subscribe: fixes a browser still pointing at a subscription the
      // server no longer thinks is alive.
      await unsubscribeFromPush();
      clearDismissed();
      const r = await subscribeToPush();
      return r.success
        ? { tone: "ok", text: "Re-registered with a fresh subscription." }
        : { tone: "bad", text: r.error || "Re-registering failed." };
    });

  const test = () =>
    run("test", async () => {
      const r = await sendTestPush();
      await loadLog();
      return { tone: "ok", text: `Sent to ${r.sent ?? "?"} device${r.sent === 1 ? "" : "s"}. Nothing arrived? Re-register below.` };
    });

  const headline = pushHeadline({ supported, reachable, configured, permission, subscribed });
  const canTurnOn = supported !== false && configured && permission !== "denied";
  const ios = typeof navigator !== "undefined" && /iPad|iPhone|iPod/i.test(navigator.userAgent);

  return (
    <div className="space-y-6">
      <HeldSends hideWhenEmpty label="Waiting on you" />

      <Card className="space-y-3 p-4">
        {checked ? (
          <p className="text-title3 text-text-primary">{headline.label}</p>
        ) : (
          <Skeleton lines={1} lineClassName="h-5 w-4/5" />
        )}
        <div className="flex min-h-[44px] items-center gap-3 border-t border-border pt-3">
          <span className={cn("flex h-8 w-8 flex-none items-center justify-center rounded-sm", subscribed ? "bg-terminal-soft text-terminal-strong" : "bg-surface-3 text-text-muted")}>
            {subscribed ? <Bell className="h-4 w-4" /> : <BellOff className="h-4 w-4" />}
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-callout text-text-primary">This device</p>
            <p className="text-footnote text-text-muted">
              {subscribed ? "Pushes land here." : "Only what can't wait — the rest goes on Today."}
            </p>
          </div>
          <Switch
            label="Notifications on this device"
            checked={subscribed}
            disabled={busy !== null || (!subscribed && !canTurnOn)}
            onChange={(v) => turn(v)}
          />
        </div>
        {flash ? <Notice tone={flash.tone}>{flash.text}</Notice> : null}
        {!isPWA() && ios ? (
          <Notice tone="info">
            On an iPhone, notifications only work from the Home Screen app: Share → Add to Home Screen, then open it from there.
          </Notice>
        ) : null}
        {permission === "denied" ? (
          <Notice tone="info" title="How to allow them">
            iPhone: Settings → Notifications → Edward → Allow. Mac: System Settings → Notifications → your browser → Allow.
            Then come back and turn it on.
          </Notice>
        ) : null}
      </Card>

      <section className="space-y-2">
        <SectionLabel>What I sent you lately</SectionLabel>
        {logError && !log ? (
          <Notice
            tone="bad"
            title="I can't read what I sent right now."
            action={
              <Button variant="ghost" size="sm" onClick={loadLog}>
                Try again
              </Button>
            }
          />
        ) : !log ? (
          <Card>
            <Skeleton lines={3} />
          </Card>
        ) : log.length === 0 ? (
          <Card>
            <EmptyState>I haven&apos;t pushed anything this week.</EmptyState>
          </Card>
        ) : (
          <Card className="px-3 py-1">
            {log.map((p) => (
              <Row
                key={p.id}
                href={p.href ?? undefined}
                title={p.title}
                detail={
                  <>
                    <span>{p.meta}</span>
                    <span aria-hidden> · </span>
                    <span className={STATE_CLASS[p.state]}>{p.state}</span>
                  </>
                }
              />
            ))}
          </Card>
        )}
      </section>

      <section className="space-y-2">
        <Card className="px-3 py-0">
          <Row title="Troubleshoot" detail="What this device and the Mac report" expanded={troubleshoot} onClick={() => setTroubleshoot((v) => !v)} />
          {troubleshoot ? (
            <div className="space-y-3 pb-3 pt-2">
              <dl className="grid grid-cols-2 gap-2 text-footnote">
                <Stat label="Permission" value={permission} />
                <Stat label="This device subscribed" value={subscribed ? "yes" : "no"} />
                <Stat label="Mac configured" value={configured ? "yes" : "no"} />
                <Stat label="Active subscriptions" value={String(count)} />
                <Stat label="Home Screen app" value={isPWA() ? "yes" : "no"} />
                <Stat label="Push supported" value={supported ? "yes" : supported === false ? "no" : "…"} />
              </dl>
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" size="sm" icon={<Send />} loading={busy === "test"} disabled={busy !== null || !configured} onClick={test}>
                  Send a test
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  icon={<RefreshCw />}
                  loading={busy === "reset"}
                  disabled={busy !== null || !canTurnOn}
                  onClick={reset}
                >
                  Re-register
                </Button>
              </div>
            </div>
          ) : null}
        </Card>
      </section>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-surface-3/60 px-3 py-2">
      <dt className="text-caption text-text-muted">{label}</dt>
      <dd className="font-mono text-footnote text-text-primary">{value}</dd>
    </div>
  );
}
