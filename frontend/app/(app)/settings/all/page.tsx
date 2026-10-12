"use client";

// Every panel, A to Z. Each row names the room it lives in first; the
// pre-redesign category and the old names stay searchable but are never
// shown (settingsIndex keywords, settingsRooms.searchSettings).

import React, { useState } from "react";
import { Search } from "lucide-react";
import { searchSettings, settingDetail } from "@/components/settings/settingsRooms";
import { SETTINGS_INDEX, searchableIndex } from "@/components/settings/settingsIndex";
import { Breadcrumb, PageHeader, SettingsColumn } from "@/components/settings/SettingsPageFrame";
import { CONTROL, EmptyState, Row } from "@/components/ui";
import { cn } from "@/lib/utils";

export default function AllSettingsPage() {
  const [q, setQ] = useState("");
  const hits = searchSettings(searchableIndex(), q);

  return (
    <SettingsColumn>
      <Breadcrumb href="/settings">Edward</Breadcrumb>
      <PageHeader title="All settings" description={`${SETTINGS_INDEX.length} panels across his five rooms`} />
      <label className="relative mb-3 block">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" aria-hidden />
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Find a setting"
          aria-label="Find a setting"
          enterKeyHint="search"
          className={cn(CONTROL, "min-h-[44px] py-2.5 pl-9")}
        />
      </label>
      <div className="rounded-lg border border-border bg-surface px-3 shadow-e1">
        {hits.length === 0 ? (
          <EmptyState title="Nothing by that name">Try what it does — “timezone”, “push”, “tokens”.</EmptyState>
        ) : (
          <ul aria-label="All settings">
            {hits.map((i) => {
              const Icon = i.icon;
              return (
                <li key={i.slug} className="border-b border-border last:border-b-0 [&>a]:border-b-0">
                  <Row
                    href={`/settings/${i.slug}`}
                    icon={<Icon aria-hidden />}
                    title={i.title}
                    detail={settingDetail(i.slug, i.description)}
                  />
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </SettingsColumn>
  );
}
