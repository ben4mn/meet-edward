"use client";

// The Edward tab's rail (md+): rooms with their health, each room's panels
// beneath, an A–Z finder on top. It is the page's sibling inside the
// settings frame, so it scrolls on its own and never scrolls away with the
// content. Phones navigate landing → room → panel instead.

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { SettingsNavList } from "./SettingsNavList";
import { settingsLocation } from "./settingsRooms";
import { cn } from "@/lib/utils";

export function SettingsSidebar() {
  const pathname = usePathname();
  const here = settingsLocation(pathname);
  return (
    <aside
      aria-label="Edward's rooms"
      className="hidden md:flex w-64 lg:w-72 flex-none flex-col min-h-0 border-r border-border bg-primary-bg"
    >
      <div className="px-3 pt-5 pb-2">
        <Link
          href="/settings"
          aria-current={here.kind === "home" ? "page" : undefined}
          className={cn(
            "flex h-9 items-center rounded-md px-2 text-title3 text-text-primary transition-colors duration-fast",
            here.kind === "home" ? "bg-surface-3" : "hover:bg-surface-3/60",
          )}
        >
          Edward
        </Link>
      </div>
      <SettingsNavList />
    </aside>
  );
}
