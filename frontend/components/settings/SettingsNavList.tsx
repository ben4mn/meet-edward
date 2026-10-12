"use client";

// The rail's body (md+): an A–Z finder, then each room with its health dot
// and its panels beneath. The current page is highlighted — the panel on a
// panel page, the room on a room page — in a neutral fill (emerald is
// Edward's, not navigation's). Typing in the finder swaps the tree for the
// matching panels, each named with its room; Enter opens the first, Escape
// clears.

import React, { useMemo, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Search, List } from "lucide-react";
import { ROOMS, roomFor, roomLabel, searchSettings, settingsLocation } from "./settingsRooms";
import { itemsInRoom, searchableIndex } from "./settingsIndex";
import { RoomDot } from "./HealthStrip";
import { useEdwardTabStatus } from "./useEdwardTabStatus";
import { useIsDesktop } from "@/components/ui";
import { cn } from "@/lib/utils";

const ITEM =
  "flex min-h-[32px] items-center gap-2.5 rounded-md px-2 text-footnote transition-colors duration-fast [@media(pointer:coarse)]:min-h-[44px]";
const ITEM_IDLE = "text-text-secondary hover:bg-surface-3/60 hover:text-text-primary";
const ITEM_ON = "bg-surface-3 font-medium text-text-primary";

export function SettingsNavList({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const router = useRouter();
  const here = settingsLocation(pathname);
  // Hidden on a phone: read the rooms' health only where the rail shows.
  const wide = useIsDesktop();
  const { lines } = useEdwardTabStatus({ enabled: wide });
  const [q, setQ] = useState("");

  const hits = useMemo(() => (q.trim() ? searchSettings(searchableIndex(), q) : null), [q]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && hits && hits[0]) {
      e.preventDefault();
      router.push(`/settings/${hits[0].slug}`);
      setQ("");
      onNavigate?.();
    } else if (e.key === "Escape" && q) {
      e.preventDefault();
      setQ("");
    }
  };

  return (
    <>
      <div className="px-3 pb-2">
        <label className="relative block">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" aria-hidden />
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Find a setting"
            aria-label="Find a setting"
            enterKeyHint="go"
            className={cn(
              "block h-9 w-full rounded-md border border-input-border bg-input-bg pl-8 pr-2 text-footnote text-text-primary",
              "placeholder:text-text-muted transition-[border-color,box-shadow] duration-fast",
              "focus:border-terminal/70 focus:outline-none focus:ring-2 focus:ring-terminal/25",
              "[@media(pointer:coarse)]:h-11 [@media(pointer:coarse)]:text-[16px]",
            )}
          />
        </label>
      </div>

      <nav aria-label="Rooms and panels" className="flex-1 min-h-0 overflow-y-auto overscroll-contain px-3 pb-6">
        {hits ? (
          hits.length === 0 ? (
            <p className="px-2 py-3 text-footnote text-text-muted">No setting matches “{q.trim()}”.</p>
          ) : (
            <ul aria-label="Matching settings" className="space-y-0.5">
              {hits.map((i) => {
                const Icon = i.icon;
                return (
                  <li key={i.slug}>
                    <Link
                      href={`/settings/${i.slug}`}
                      onClick={() => {
                        setQ("");
                        onNavigate?.();
                      }}
                      className={cn(ITEM, ITEM_IDLE)}
                    >
                      <Icon className="h-4 w-4 flex-none" aria-hidden />
                      <span className="min-w-0 flex-1 truncate">{i.title}</span>
                      <span className="flex-none text-caption text-text-muted">{roomLabel(roomFor(i.slug))}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )
        ) : (
          <>
            {ROOMS.map((room) => {
              const line = lines?.[room.id];
              const roomOn = here.kind === "room" && here.room === room.id;
              return (
                <div key={room.id} className="mt-4 first:mt-1">
                  <Link
                    href={`/settings/room/${room.id}`}
                    onClick={onNavigate}
                    aria-current={roomOn ? "page" : undefined}
                    title={line?.text}
                    className={cn(
                      ITEM,
                      "justify-between font-semibold",
                      roomOn ? ITEM_ON : "text-text-primary hover:bg-surface-3/60",
                    )}
                  >
                    <span>{room.label}</span>
                    <RoomDot tone={line?.tone ?? "neutral"} />
                  </Link>
                  <ul className="mt-0.5 space-y-0.5">
                    {itemsInRoom(room.id).map((item) => {
                      const on = here.kind === "panel" && here.slug === item.slug;
                      const Icon = item.icon;
                      return (
                        <li key={item.slug}>
                          <Link
                            href={`/settings/${item.slug}`}
                            onClick={onNavigate}
                            aria-current={on ? "page" : undefined}
                            className={cn(ITEM, on ? ITEM_ON : ITEM_IDLE)}
                          >
                            <Icon className="h-4 w-4 flex-none opacity-80" aria-hidden />
                            <span className="truncate">{item.title}</span>
                          </Link>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              );
            })}
            <div className="mt-5 border-t border-border pt-3">
              <Link
                href="/settings/all"
                onClick={onNavigate}
                aria-current={here.kind === "all" ? "page" : undefined}
                className={cn(ITEM, here.kind === "all" ? ITEM_ON : ITEM_IDLE)}
              >
                <List className="h-4 w-4 flex-none opacity-80" aria-hidden />
                <span>All settings, A–Z</span>
              </Link>
            </div>
          </>
        )}
      </nav>
    </>
  );
}
