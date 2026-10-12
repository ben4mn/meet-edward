"use client";

// Settings → House → Gmail (2026-09-26, Jarvis polish).
//
// Connect the owner's Gmail; while connected, a switch for "watch my inbox" and
// how often to look, as a Segment (the old chips selected with a colour
// class that did not exist — the choice was invisible). Disconnecting asks
// once in a ConfirmSheet (it revokes access).

import React, { useCallback, useEffect, useState } from "react";
import { ExternalLink, Mail } from "lucide-react";
import {
  disconnectGmail,
  getGmailOAuthUrl,
  getGmailStatus,
  getHeartbeatStatus,
  updateHeartbeatConfig,
  type GmailStatus,
} from "@/lib/api";
import { Button, Card, ConfirmSheet, Notice, Segment, Skeleton, Switch, useToast } from "@/components/ui";
import { StatusDot } from "./panelBits";

const POLL_OPTIONS = [
  { value: "30", label: "30s" },
  { value: "60", label: "1m" },
  { value: "120", label: "2m" },
  { value: "300", label: "5m" },
  { value: "900", label: "15m" },
];

export interface GmailPanelProps {
  isExpanded?: boolean;
  hideHeader?: boolean;
}

export function GmailPanel(_props: GmailPanelProps = {}) {
  const { toast } = useToast();
  const [status, setStatus] = useState<GmailStatus | null>(null);
  const [failed, setFailed] = useState(false);
  const [watching, setWatching] = useState(false);
  const [pollSeconds, setPollSeconds] = useState(120);
  const [connecting, setConnecting] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [justConnected, setJustConnected] = useState<"connected" | "error" | null>(null);

  const load = useCallback(async () => {
    setFailed(false);
    try {
      const [gmail, hb] = await Promise.all([getGmailStatus(), getHeartbeatStatus().catch(() => null)]);
      setStatus(gmail);
      if (hb) {
        setWatching(Boolean(hb.gmail_enabled));
        setPollSeconds(hb.gmail_poll_seconds ?? 120);
      }
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Back from Google's consent screen: ?gmail=connected|error.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    const param = url.searchParams.get("gmail");
    if (param === "connected" || param === "error") {
      url.searchParams.delete("gmail");
      window.history.replaceState(window.history.state, "", url.toString());
      setJustConnected(param);
      if (param === "connected") load();
    }
  }, [load]);

  const connect = async () => {
    setConnecting(true);
    try {
      window.location.href = await getGmailOAuthUrl();
    } catch {
      setConnecting(false);
      toast({ title: "I couldn't reach Google just now.", tone: "bad" });
    }
  };

  const disconnect = async () => {
    try {
      await disconnectGmail();
      setStatus({ configured: false, email_address: null, token_valid: false });
      setWatching(false);
      toast({ title: "Gmail disconnected" });
    } catch {
      toast({ title: "I couldn't disconnect Gmail just now.", tone: "bad" });
    } finally {
      setConfirming(false);
    }
  };

  const setWatch = async (on: boolean) => {
    setWatching(on);
    try {
      await updateHeartbeatConfig({ gmail_enabled: on });
    } catch {
      setWatching(!on);
      toast({ title: "I couldn't change that just now.", tone: "bad" });
    }
  };

  const setPoll = async (v: string) => {
    const before = pollSeconds;
    setPollSeconds(Number(v));
    try {
      await updateHeartbeatConfig({ gmail_poll_seconds: Number(v) });
    } catch {
      setPollSeconds(before);
      toast({ title: "I couldn't change that just now.", tone: "bad" });
    }
  };

  if (failed && !status) {
    return (
      <Notice tone="bad" action={<Button size="sm" variant="secondary" onClick={load}>Try again</Button>}>
        I can&apos;t check Gmail right now.
      </Notice>
    );
  }
  if (!status) {
    return (
      <Card className="p-4">
        <Skeleton lines={3} />
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      {justConnected === "connected" ? <Notice tone="ok">Gmail is connected.</Notice> : null}
      {justConnected === "error" ? <Notice tone="bad">Google didn&apos;t finish connecting. Try again?</Notice> : null}

      {!status.configured ? (
        <Card className="space-y-4 p-4">
          <div className="flex items-start gap-3">
            <span className="flex h-8 w-8 flex-none items-center justify-center rounded-sm bg-surface-3 text-text-secondary">
              <Mail className="h-4 w-4" aria-hidden />
            </span>
            <p className="min-w-0 flex-1 text-body text-text-secondary">
              Connect your Gmail and I can search, read and sort your email for you.
            </p>
          </div>
          <Button className="w-full" icon={<ExternalLink aria-hidden />} loading={connecting} onClick={connect}>
            Connect Gmail
          </Button>
        </Card>
      ) : (
        <>
          <Card className="p-4">
            <div className="flex items-center gap-3">
              <span className="flex h-8 w-8 flex-none items-center justify-center rounded-sm bg-surface-3 text-text-secondary">
                <Mail className="h-4 w-4" aria-hidden />
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-callout text-text-primary">{status.email_address}</p>
                <p className="mt-0.5 flex items-center gap-1.5 text-caption text-text-muted">
                  <StatusDot tone={status.token_valid ? "ok" : "bad"} />
                  {status.token_valid ? "Connected" : "Needs reconnecting"}
                </p>
              </div>
              <Button variant="ghost" size="sm" className="text-bad hover:text-bad" onClick={() => setConfirming(true)}>
                Disconnect
              </Button>
            </div>
            {!status.token_valid ? (
              <Button variant="secondary" className="mt-3 w-full" loading={connecting} onClick={connect}>
                Reconnect
              </Button>
            ) : null}
          </Card>

          <Card className="p-4">
            <div className="flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <p className="text-callout text-text-primary">Watch my inbox</p>
                <p className="mt-0.5 text-footnote text-text-muted">
                  I look at new mail and tell you only about what matters.
                </p>
              </div>
              <Switch checked={watching} onChange={setWatch} label="Watch my inbox" />
            </div>
            {watching ? (
              <div className="mt-4 space-y-2 border-t border-border pt-4">
                <p className="text-footnote font-medium text-text-secondary">How often I look</p>
                <Segment label="How often I look" value={String(pollSeconds)} onChange={setPoll} options={POLL_OPTIONS} />
              </div>
            ) : null}
          </Card>
        </>
      )}

      <ConfirmSheet
        open={confirming}
        onClose={() => setConfirming(false)}
        onConfirm={disconnect}
        title="Disconnect Gmail?"
        body="I'll lose access to your email and forget the stored sign-in. You can connect again any time."
        confirmLabel="Disconnect"
      />
    </div>
  );
}
