"use client";

// The Edward tab's landing — "what he knows, watches and does", answered:
//   1. Edward now: the hero (him, his status, his own words);
//   2. three answers that each open a room — Knows (with a search field
//      that goes to the Memory room), Watches, Does (his last wake and the
//      thinking behind it);
//   3. the five rooms, once, each with its health and one live line;
//   4. You (appearance, sign out — the shell's YouCard) and All settings.
// Every line is decided in edwardHomeModel from real signals.

import React, { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ChevronRight, List, Search } from "lucide-react";
import { CONTROL, Row, SectionLabel } from "@/components/ui";
import { cn } from "@/lib/utils";
import { EdwardHero } from "./EdwardHero";
import YouCard from "./YouCard";
import { HealthStrip } from "./HealthStrip";
import { SettingsColumn } from "./SettingsPageFrame";
import { SETTINGS_INDEX } from "./settingsIndex";
import { doesAnswer, knowsLine, watchesLine } from "./edwardHomeModel";
import { useEdwardTabStatus } from "./useEdwardTabStatus";

function Answer({
  label,
  href,
  title,
  detail,
  loading,
  children,
}: {
  label: string;
  href: string;
  title: string;
  detail?: string | null;
  loading?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-col">
      <Link
        href={href}
        className="group flex min-h-[64px] items-start gap-3 px-4 pb-2.5 pt-3.5 transition-colors duration-fast hover:bg-surface-3/40 active:bg-surface-3/70"
      >
        <span className="min-w-0 flex-1">
          <span className="block text-overline text-text-muted">{label}</span>
          {loading ? (
            <span aria-hidden className="mt-2 block h-3.5 w-44 max-w-full rounded-full bg-surface-3 motion-safe:animate-pulse" />
          ) : (
            <span className="mt-1 block text-callout text-text-primary">{title}</span>
          )}
          {detail ? <span className="mt-0.5 block line-clamp-2 text-footnote text-text-muted">{detail}</span> : null}
        </span>
        <ChevronRight className="mt-5 h-4 w-4 flex-none text-text-muted transition-transform duration-fast group-hover:translate-x-0.5" aria-hidden />
      </Link>
      {children ? <div className="px-4 pb-3">{children}</div> : <div className="pb-1" />}
    </div>
  );
}

function KnowsSearch() {
  const router = useRouter();
  const [q, setQ] = useState("");
  return (
    <form
      role="search"
      onSubmit={(e) => {
        e.preventDefault();
        const query = q.trim();
        router.push(query ? `/settings/room/memory?q=${encodeURIComponent(query)}` : "/settings/room/memory");
      }}
      className="relative"
    >
      <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" aria-hidden />
      <input
        type="search"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="What does he have on…"
        aria-label="Search what he knows"
        enterKeyHint="search"
        className={cn(CONTROL, "min-h-[44px] py-2.5 pl-9")}
      />
    </form>
  );
}

function Answers() {
  const { data, mindNow } = useEdwardTabStatus();
  const loading = !data;
  const does = doesAnswer(mindNow);
  // null while his mind can't be read: the line says so rather than "nothing".
  const waiting = mindNow ? mindNow.approvals_waiting ?? 0 : null;
  return (
    <section
      aria-label="What he knows, watches and does"
      className="overflow-hidden rounded-lg border border-border bg-surface shadow-e1"
    >
      <div className="grid divide-y divide-border">
        <Answer label="Knows" href="/settings/room/memory" title={knowsLine(data?.memory ?? null)} loading={loading}>
          <KnowsSearch />
        </Answer>
        <Answer
          label="Watches"
          href="/settings/room/attention"
          title={watchesLine(data?.intentions ? data.intentions.length : null, waiting)}
          loading={loading}
        />
        <Answer label="Does" href="/settings/room/work" title={does.title} detail={does.detail} loading={loading && !mindNow}>
          {does.thoughtHref ? (
            <Link
              href={does.thoughtHref}
              className="-mb-1.5 -ml-2 -mt-2 inline-flex min-h-[44px] items-center rounded-md px-2 text-footnote font-medium text-terminal-strong transition-opacity duration-fast hover:opacity-80 active:opacity-60"
            >
              Read his thinking →
            </Link>
          ) : null}
        </Answer>
      </div>
    </section>
  );
}

export function SettingsDashboard() {
  return (
    <SettingsColumn width="5xl" className="space-y-6">
      <div className="grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:gap-4">
        <EdwardHero />
        <Answers />
      </div>
      <section aria-label="His rooms">
        <SectionLabel className="mb-2">Rooms</SectionLabel>
        <HealthStrip />
      </section>
      <YouCard />
      <div className="rounded-lg border border-border bg-surface px-3 shadow-e1">
        <Row
          href="/settings/all"
          icon={<List aria-hidden />}
          title="All settings"
          detail={`Every panel, A to Z · ${SETTINGS_INDEX.length}`}
        />
      </div>
    </SettingsColumn>
  );
}
