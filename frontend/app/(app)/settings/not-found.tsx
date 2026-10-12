"use client";

// /settings/<something that isn't there> — an old link, a bookmark, a room
// that never existed. Said in Edward's voice, with the rooms and All
// settings one tap away, instead of Next's bare "404 | This page could not
// be found." Retired panels never get here: [slug]/page redirects them.

import React from "react";
import { List } from "lucide-react";
import { ROOMS } from "@/components/settings/settingsRooms";
import { Breadcrumb, PageHeader, SettingsColumn } from "@/components/settings/SettingsPageFrame";
import { Card, Row } from "@/components/ui";

export default function SettingsNotFound() {
  return (
    <SettingsColumn>
      <Breadcrumb href="/settings">Edward</Breadcrumb>
      <PageHeader title="I can't find that page" description="It may have moved when I rearranged my rooms. Here's where everything lives now." />
      <Card className="px-3 py-0">
        {ROOMS.map((r) => (
          <Row key={r.id} href={`/settings/room/${r.id}`} title={r.label} detail={r.question} />
        ))}
        <Row href="/settings/all" icon={<List aria-hidden />} title="All settings" detail="Every panel, A to Z" />
      </Card>
    </SettingsColumn>
  );
}
