"use client";

// The five rooms, once, each with its question, a health dot and one live
// line (the old health strip and the rooms card, merged — 2026-09-26). The
// lines come from edwardHomeModel.roomLines via the shared tab status, so
// the landing and the rail always agree. On a phone it is one list; from
// lg it becomes a grid of room cards that also name each room's panels.

import React from "react";
import Link from "next/link";
import { Brain, Briefcase, ChevronRight, Eye, Home, Library, type LucideIcon } from "lucide-react";
import { ROOMS, type RoomId } from "./settingsRooms";
import { itemsInRoom } from "./settingsIndex";
import type { LineTone, RoomLine } from "./edwardHomeModel";
import { useEdwardTabStatus } from "./useEdwardTabStatus";
import { cn } from "@/lib/utils";

export const ROOM_ICON: Record<RoomId, LucideIcon> = {
  mind: Brain,
  memory: Library,
  attention: Eye,
  work: Briefcase,
  house: Home,
};

const DOT: Record<LineTone, string> = {
  ok: "bg-ok",
  warn: "bg-warn",
  bad: "bg-bad",
  info: "bg-info",
  neutral: "bg-text-muted/40",
};

/** A room's health: a small fixed-colour dot (status colours never borrow emerald). */
export function RoomDot({ tone, className }: { tone: LineTone; className?: string }) {
  return (
    <span
      aria-hidden
      data-tone={tone}
      className={cn("inline-block h-2 w-2 flex-none rounded-full transition-colors duration-base", DOT[tone], className)}
    />
  );
}

export function HealthStrip({ lines: given }: { lines?: Record<RoomId, RoomLine> | null }) {
  const status = useEdwardTabStatus();
  const lines = given ?? status.lines;

  return (
    <ul
      aria-label="Rooms"
      className={cn(
        "overflow-hidden rounded-lg border border-border bg-surface shadow-e1",
        "lg:grid lg:grid-cols-2 lg:gap-3 lg:overflow-visible lg:rounded-none lg:border-0 lg:bg-transparent lg:shadow-none xl:grid-cols-3",
      )}
    >
      {ROOMS.map((room) => {
        const line = lines?.[room.id];
        const Icon = ROOM_ICON[room.id];
        const panels = itemsInRoom(room.id).map((i) => i.title).join(" · ");
        return (
          <li key={room.id} data-room-row={room.id} className="border-b border-border last:border-b-0 lg:border-b-0">
            <Link
              href={`/settings/room/${room.id}`}
              className={cn(
                "group flex min-h-[64px] items-center gap-3 px-3.5 py-3 transition-[background-color,transform] duration-press ease-out",
                "hover:bg-surface-3/50 active:bg-surface-3/80",
                "lg:h-full lg:flex-col lg:items-start lg:gap-2 lg:rounded-lg lg:border lg:border-border lg:bg-surface lg:p-4 lg:shadow-e1 lg:hover:bg-surface-elevated lg:active:scale-[.985]",
              )}
            >
              <span className="flex h-9 w-9 flex-none items-center justify-center rounded-md bg-surface-3 text-text-secondary [&_svg]:h-[18px] [&_svg]:w-[18px]">
                <Icon aria-hidden />
              </span>
              <span className="min-w-0 flex-1 lg:w-full">
                <span className="flex items-baseline gap-2">
                  <span className="text-callout text-text-primary">{room.label}</span>
                  <span className="min-w-0 truncate text-footnote text-text-muted lg:hidden">{room.question}</span>
                </span>
                <span className="mt-0.5 hidden text-footnote text-text-muted lg:block">{room.question}</span>
                <span className="mt-1 flex items-start gap-2 text-footnote text-text-secondary lg:mt-3">
                  <RoomDot tone={line?.tone ?? "neutral"} className="mt-[5px]" />
                  {line ? (
                    <span className="line-clamp-2 lg:line-clamp-3">{line.text}</span>
                  ) : (
                    <span className="h-3 w-40 max-w-full self-center rounded-full bg-surface-3 motion-safe:animate-pulse" aria-hidden />
                  )}
                </span>
                <span className="mt-3 hidden truncate text-caption text-text-muted lg:block">{panels}</span>
              </span>
              <ChevronRight className="h-4 w-4 flex-none text-text-muted lg:hidden" aria-hidden />
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
