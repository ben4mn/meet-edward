"use client";

// Settings → House → Alexa (2026-09-26, Jarvis polish).
//
// Two halves: Edward speaking on the Echo devices (through Home Assistant)
// and the owner asking Edward through an Alexa skill. Status rows, the devices,
// and a test announcement. Fetches go through lib/memoryHouseApi — the
// app's relative API base; the old panel used NEXT_PUBLIC_API_URL ||
// localhost:8000, which on the phone is the phone.

import React, { useCallback, useEffect, useState } from "react";
import { Mic, Speaker, Volume2 } from "lucide-react";
import { getAlexaStatus, testAlexaAnnouncement, type AlexaStatus } from "@/lib/memoryHouseApi";
import { formatRelative } from "@/lib/time";
import { Button, Card, Field, Input, Notice, Pill, Row, SectionLabel, Select, Skeleton } from "@/components/ui";
import { StatusDot } from "./panelBits";

export interface AlexaPanelProps {
  isExpanded?: boolean;
  hideHeader?: boolean;
}

export function AlexaPanel(_props: AlexaPanelProps = {}) {
  const [status, setStatus] = useState<AlexaStatus | null>(null);
  const [failed, setFailed] = useState(false);
  const [message, setMessage] = useState("");
  const [device, setDevice] = useState("");
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [setupOpen, setSetupOpen] = useState(false);

  const load = useCallback(async () => {
    setFailed(false);
    try {
      setStatus(await getAlexaStatus());
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const announce = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!message.trim()) return;
    setSending(true);
    setResult(null);
    try {
      const res = await testAlexaAnnouncement(message.trim(), device || undefined);
      setResult(
        res.success
          ? { ok: true, text: res.message || "Announced." }
          : { ok: false, text: res.error || res.message || "The Echo didn't take it." },
      );
    } catch {
      setResult({ ok: false, text: "I couldn't reach Home Assistant just now." });
    } finally {
      setSending(false);
    }
  };

  if (failed && !status) {
    return (
      <Notice tone="bad" action={<Button size="sm" variant="secondary" onClick={load}>Try again</Button>}>
        I can&apos;t check Alexa right now.
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

  const out = status.outbound;
  const outTone = out.configured ? (out.healthy ? "ok" : "warn") : "neutral";
  const outText = out.configured
    ? out.healthy
      ? `Connected to Home Assistant`
      : `Home Assistant isn't answering (${out.ha_url})`
    : "Not set up";
  const inTone = status.inbound.configured ? "ok" : "neutral";
  const recent = status.recent_announcements ?? [];

  return (
    <div className="space-y-5">
      <Card className="px-3 py-0">
        <StatusRow icon={<Speaker />} title="Speak on your Echo devices" tone={outTone} text={outText} />
        <StatusRow
          icon={<Mic />}
          title="Ask me through Alexa"
          tone={inTone}
          text={status.inbound.configured ? "Skill connected" : "Not set up"}
        />
      </Card>

      {out.configured ? (
        <section aria-label="Echo devices" className="space-y-2">
          <SectionLabel action={String(status.devices.length)}>Echo devices</SectionLabel>
          {status.devices.length === 0 ? (
            <Notice tone="info">I can&apos;t see any Echo devices. Check the Alexa Media Player integration in Home Assistant.</Notice>
          ) : (
            <Card className="px-3 py-0">
              {status.devices.map((d) => (
                <Row
                  key={d.entity_id}
                  icon={<Volume2 />}
                  title={d.friendly_name}
                  detail={<span className="font-mono text-caption">{d.entity_id}</span>}
                  trailing={<Pill tone={d.state === "playing" ? "ok" : "neutral"}>{d.state}</Pill>}
                />
              ))}
            </Card>
          )}
        </section>
      ) : null}

      {out.configured && out.healthy ? (
        <section aria-label="Test announcement" className="space-y-2">
          <SectionLabel>Try it</SectionLabel>
          <Card className="p-4">
            <form onSubmit={announce} className="space-y-3">
              <Field label="What should I say?">
                <Input value={message} onChange={(e) => setMessage(e.target.value)} placeholder="Dinner's ready" />
              </Field>
              <Field label="Where">
                <Select value={device} onChange={(e) => setDevice(e.target.value)}>
                  <option value="">Every Echo</option>
                  {status.devices.map((d) => (
                    <option key={d.entity_id} value={d.entity_id}>
                      {d.friendly_name}
                    </option>
                  ))}
                </Select>
              </Field>
              <Button type="submit" className="w-full" loading={sending} disabled={!message.trim()}>
                Announce
              </Button>
              {result ? <Notice tone={result.ok ? "ok" : "bad"}>{result.text}</Notice> : null}
            </form>
          </Card>
        </section>
      ) : null}

      {recent.length ? (
        <section aria-label="Recent announcements" className="space-y-2">
          <SectionLabel>What I said lately</SectionLabel>
          <Card className="px-3 py-0">
            {recent.slice(0, 5).map((a) => (
              <Row
                key={a.id}
                title={a.message}
                detail={`${a.device} · ${formatRelative(a.timestamp)}`}
                trailing={a.success ? null : <Pill tone="bad">Failed</Pill>}
              />
            ))}
          </Card>
        </section>
      ) : null}

      {!out.configured || !status.inbound.configured ? (
        <Card className="px-3 py-0">
          <Row title="How to set it up" detail="Two settings on the Mac I run on" expanded={setupOpen} onClick={() => setSetupOpen((v) => !v)} />
          {setupOpen ? (
            <div className="space-y-2 pb-4 text-footnote text-text-secondary">
              <p>Add these to the backend&apos;s .env on the Mac I run on, then restart me:</p>
              <pre className="overflow-x-auto rounded-md bg-surface-3 p-3 font-mono text-caption text-text-primary">
                {`# Speak on Echo devices
HOME_ASSISTANT_URL=http://localhost:8123
HOME_ASSISTANT_TOKEN=<long-lived token>

# Ask me through Alexa
ALEXA_SKILL_ID=amzn1.ask.skill.…`}
              </pre>
            </div>
          ) : null}
        </Card>
      ) : null}
    </div>
  );
}

function StatusRow({
  icon,
  title,
  tone,
  text,
}: {
  icon: React.ReactNode;
  title: string;
  tone: "ok" | "warn" | "bad" | "neutral";
  text: string;
}) {
  return (
    <Row
      icon={icon}
      title={title}
      detail={
        <span className="inline-flex items-center gap-1.5">
          <StatusDot tone={tone} />
          {text}
        </span>
      }
    />
  );
}
