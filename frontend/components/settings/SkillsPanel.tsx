"use client";

// Settings → House → Skills (2026-09-26, Jarvis polish).
//
// What Edward can do, grouped by what it lets him do and said in plain
// words (lib/skillView.ts). Each skill is a row with a real switch (spring
// knob), a status dot and, when something is wrong, what is wrong. Toggling
// is optimistic and undone if the server refuses. "Check again" reconnects
// everything.

import React, { useCallback, useEffect, useState } from "react";
import {
  Bell,
  Code,
  Database,
  Globe,
  MessageSquare,
  Mic,
  Phone,
  Search,
  Smartphone,
  Speaker,
  Terminal,
  Users,
  Workflow,
  Zap,
} from "lucide-react";
import { getSkills, reloadSkills, setSkillEnabled, type Skill } from "@/lib/api";
import { groupSkills, skillCopy, skillStatus, skillsSummary } from "@/lib/skillView";
import { formatClock } from "@/lib/time";
import { useUserTimezone } from "@/lib/useTimezone";
import { Button, Card, EmptyState, Notice, SectionLabel, Skeleton, Switch, cn, useToast } from "@/components/ui";
import { StatusDot } from "./panelBits";

export interface SkillsPanelProps {
  isExpanded?: boolean;
  hideHeader?: boolean;
}

function skillIcon(id: string) {
  if (id.includes("imessage") || id.includes("whatsapp")) return <MessageSquare />;
  if (id.includes("twilio") || id.includes("sms")) return <Phone />;
  if (id.includes("search") || id.includes("brave")) return <Search />;
  if (id.includes("javascript") || id === "code_interpreter") return <Code />;
  if (id.includes("sql")) return <Database />;
  if (id.includes("shell") || id.includes("claude")) return <Terminal />;
  if (id.includes("html")) return <Globe />;
  if (id.includes("widget")) return <Smartphone />;
  if (id.includes("push")) return <Bell />;
  if (id.includes("voice")) return <Mic />;
  if (id.includes("alexa")) return <Speaker />;
  if (id.includes("contacts")) return <Users />;
  if (id.includes("orchestrator")) return <Workflow />;
  return <Zap />;
}

export function SkillsPanel(_props: SkillsPanelProps = {}) {
  const tz = useUserTimezone();
  const { toast } = useToast();
  const [skills, setSkills] = useState<Skill[] | null>(null);
  const [lastReload, setLastReload] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [reloading, setReloading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setFailed(false);
    try {
      const res = await getSkills();
      setSkills(res.skills);
      setLastReload(res.last_reload);
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const toggle = async (s: Skill, on: boolean) => {
    const before = s;
    setBusy(s.id);
    setSkills((prev) =>
      prev ? prev.map((x) => (x.id === s.id ? { ...x, enabled: on, status: on ? "connecting" : "disabled" } : x)) : prev,
    );
    try {
      const updated = await setSkillEnabled(s.id, on);
      setSkills((prev) => (prev ? prev.map((x) => (x.id === s.id ? updated : x)) : prev));
    } catch {
      setSkills((prev) => (prev ? prev.map((x) => (x.id === s.id ? before : x)) : prev));
      toast({ title: `I couldn't turn ${skillCopy(s).name} ${on ? "on" : "off"}.`, tone: "bad" });
    } finally {
      setBusy(null);
    }
  };

  const reload = async () => {
    setReloading(true);
    try {
      const res = await reloadSkills();
      setSkills(res.skills);
      setLastReload(res.last_reload);
    } catch {
      toast({ title: "I couldn't check my connections just now.", tone: "bad" });
    } finally {
      setReloading(false);
    }
  };

  if (failed && !skills) {
    return (
      <Notice tone="bad" action={<Button size="sm" variant="secondary" onClick={load}>Try again</Button>}>
        I can&apos;t read my skills right now.
      </Notice>
    );
  }
  if (!skills) {
    return (
      <Card className="p-4">
        <Skeleton lines={5} />
      </Card>
    );
  }
  if (skills.length === 0) {
    return <EmptyState title="No skills yet.">When I can do more, it will show up here.</EmptyState>;
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-callout text-text-primary">{skillsSummary(skills)}</p>
          {lastReload ? <p className="text-caption text-text-muted">Checked at {formatClock(lastReload, { tz })}</p> : null}
        </div>
        <Button variant="secondary" size="sm" onClick={reload} loading={reloading}>
          Check again
        </Button>
      </div>

      {groupSkills(skills).map((g) => (
        <section key={g.key} aria-label={g.label} className="space-y-2">
          <SectionLabel>{g.label}</SectionLabel>
          <Card className="px-3 py-0">
            {g.skills.map((s) => (
              <SkillRow key={s.id} skill={s} busy={busy === s.id} onToggle={(on) => toggle(s, on)} />
            ))}
          </Card>
        </section>
      ))}
    </div>
  );
}

function SkillRow({ skill, busy, onToggle }: { skill: Skill; busy: boolean; onToggle: (on: boolean) => void }) {
  const copy = skillCopy(skill);
  const status = skillStatus(skill);
  return (
    <div className="flex min-h-[56px] items-center gap-3 border-b border-border py-3 last:border-b-0">
      <span
        className={cn(
          "flex h-8 w-8 flex-none items-center justify-center self-start rounded-sm [&_svg]:h-4 [&_svg]:w-4",
          skill.enabled ? "bg-surface-3 text-text-secondary" : "bg-surface-3/60 text-text-muted",
        )}
      >
        {skillIcon(skill.id)}
      </span>
      <div className="min-w-0 flex-1">
        <p className={cn("text-callout", skill.enabled ? "text-text-primary" : "text-text-secondary")}>{copy.name}</p>
        <p className="mt-0.5 text-footnote text-text-muted">{copy.description}</p>
        {skill.enabled ? (
          <p className={cn("mt-1 flex items-center gap-1.5 text-caption", status.tone === "bad" ? "text-bad" : "text-text-muted")}>
            <StatusDot tone={status.tone} />
            <span className="min-w-0 [overflow-wrap:anywhere]">{status.text}</span>
          </p>
        ) : null}
      </div>
      <Switch checked={skill.enabled} onChange={onToggle} label={copy.name} disabled={busy} />
    </div>
  );
}
