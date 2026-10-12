"use client";

// One settings panel, framed by PanelShell: a breadcrumb back to the room
// it lives in ("‹ Memory"), its title and one line. The panel draws content
// only (settingsConfig renders every panel with `hideHeader`). A retired
// panel's URL goes where its work lives now (settingsRedirects); any other
// unknown slug gets the settings not-found page, in Edward's voice.

import React from "react";
import { notFound, redirect, useSearchParams } from "next/navigation";
import { panelFrame } from "@/components/settings/panelFrame";
import { getSettingsItem } from "@/components/settings/settingsConfig";
import { movedTo } from "@/components/settings/settingsRedirects";
import { SettingsColumn } from "@/components/settings/SettingsPageFrame";
import { PanelShell } from "@/components/ui";

export default function SettingsPanelPage({ params }: { params: { slug: string } }) {
  const search = useSearchParams();
  const moved = movedTo(params.slug);
  if (moved) redirect(moved);
  const item = getSettingsItem(params.slug);
  if (!item) notFound();
  // A sub-view (the heartbeat log, an open document) breadcrumbs to its
  // parent view and carries its own title — one way back, one title.
  const frame = panelFrame(item.slug, search);

  return (
    <SettingsColumn width="4xl">
      <PanelShell
        slug={item.slug}
        title={frame.title !== undefined ? frame.title : item.title}
        description={frame.title !== undefined ? undefined : item.description}
        back={frame.back}
      >
        {item.render()}
      </PanelShell>
    </SettingsColumn>
  );
}
