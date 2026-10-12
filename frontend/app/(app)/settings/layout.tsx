// The frame of every /settings/* page.
//
// ClientLayout's <main> is `overflow-hidden` because the chat page owns
// its own scrolling, so this layout provides the scroll container for the
// landing, the rooms, the A–Z index and every panel (on a phone the list
// used to be clipped at the bottom).
//
// On md+ it is a persistent two-pane layout: the rooms rail on the left,
// the page on the right. The frame is a height-bounded flex row
// (`flex-1 min-h-0 overflow-hidden`), so the rail and the page each scroll
// on their own — the rail used to sit inside the page's scroll container
// and scrolled away with the content.
import React from "react";
import { SettingsSidebar } from "@/components/settings/SettingsSidebar";

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex flex-1 min-h-0 overflow-hidden">
      <SettingsSidebar />
      <div className="flex-1 overflow-y-auto min-h-0 overscroll-contain">{children}</div>
    </div>
  );
}
