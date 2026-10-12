"use client";

// One of Edward's five rooms: one breadcrumb back to Edward, an intro line,
// the room's live view, then its panels as rows.
//   Mind      → his own page + his last wakes (then how his work shows in chat)
//   Memory    → "What does he have on…" across everything he keeps
//   Attention → what waits on the owner, what he watches for, what reached the phone
//   Work      → Fuel (the Codex week) and what he did on his own
//   House     → the machine's health

import React from "react";
import { notFound } from "next/navigation";
import { ROOMS, type RoomId } from "@/components/settings/settingsRooms";
import { itemsInRoom } from "@/components/settings/settingsIndex";
import { Breadcrumb, PageHeader, PageSection, SettingsColumn } from "@/components/settings/SettingsPageFrame";
import { KnowledgeSearch } from "@/components/settings/KnowledgeSearch";
import { ActivityTimeline } from "@/components/settings/ActivityTimeline";
import { CodexUsageCard } from "@/components/settings/CodexUsageCard";
import { HouseVitals } from "@/components/settings/HouseVitals";
import { MindRoomView } from "@/components/settings/MindRoomView";
import { AttentionRoomView } from "@/components/settings/AttentionRoomView";
import { WorkPrefControl } from "@/components/chat/WorkPrefControl";
import { Card, Row } from "@/components/ui";

const INTRO: Record<RoomId, string> = {
  mind: "What's on his mind, what he means to do, and what he thinks with.",
  memory: "Everything he remembers, the pages and files he keeps, and the data he's gathered.",
  attention: "What he's watching for, what's waiting on you, and how he reaches you.",
  work: "What he did on his own, what's running, and how much of the week's fuel is left.",
  house: "The Mac he runs on, and the household around it.",
};

function LiveView({ room, query }: { room: RoomId; query: string }) {
  switch (room) {
    case "mind":
      return (
        <>
          <MindRoomView />
          <PageSection label="In chat">
            <Card className="p-4">
              <WorkPrefControl />
            </Card>
          </PageSection>
        </>
      );
    case "memory":
      return (
        <PageSection label="Everything he keeps">
          <KnowledgeSearch initialQuery={query} />
        </PageSection>
      );
    case "attention":
      return <AttentionRoomView />;
    case "work":
      return (
        <>
          <PageSection label="Fuel" action="Usage" actionHref="/settings/usage">
            <CodexUsageCard />
          </PageSection>
          <PageSection label="What he did" action="His journal" actionHref="/settings/journal">
            <ActivityTimeline />
          </PageSection>
        </>
      );
    case "house":
      return (
        <PageSection label="The machine">
          <HouseVitals />
        </PageSection>
      );
  }
}

export default function RoomPage({
  params,
  searchParams,
}: {
  params: { room: string };
  searchParams?: { q?: string | string[] };
}) {
  const room = ROOMS.find((r) => r.id === params.room);
  if (!room) notFound();
  const q = searchParams?.q;
  const query = (Array.isArray(q) ? q[0] : q) ?? "";
  const items = itemsInRoom(room.id);

  return (
    <SettingsColumn>
      <Breadcrumb href="/settings">Edward</Breadcrumb>
      <PageHeader title={room.label} description={INTRO[room.id]} />

      <LiveView room={room.id} query={query} />

      <PageSection label="Settings">
        <div className="rounded-lg border border-border bg-surface px-3 shadow-e1">
          <ul aria-label={`Settings in ${room.label}`}>
            {items.map((item) => {
              const Icon = item.icon;
              return (
                <li key={item.slug} className="border-b border-border last:border-b-0 [&>a]:border-b-0">
                  <Row href={`/settings/${item.slug}`} icon={<Icon aria-hidden />} title={item.title} detail={item.description} />
                </li>
              );
            })}
          </ul>
        </div>
      </PageSection>
    </SettingsColumn>
  );
}
