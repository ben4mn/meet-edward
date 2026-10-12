"use client";

// The frame of one settings panel (/settings/<slug>): a breadcrumb back to
// the room it lives in, a title1 heading in the sans face, one line saying
// what the panel is for, optional actions on the right, then the panel.
// The room comes from settingsRooms.roomFor, so a panel never names its
// own room and cannot drift from the index.

import React from "react";
import Link from "next/link";
import type { ReactNode } from "react";
import { ChevronLeft } from "lucide-react";
import { roomFor, roomLabel } from "@/components/settings/settingsRooms";
import { cn } from "./cn";

export function PanelShell({
  slug,
  title,
  description,
  actions,
  back,
  className,
  children,
}: {
  slug: string;
  /** null: no header — a sub-view that draws its own h1 (an open document). */
  title: ReactNode | null;
  description?: ReactNode;
  actions?: ReactNode;
  /** A sub-view's parent view ("‹ Listening"); default the panel's room. */
  back?: { href: string; label: string };
  className?: string;
  children: ReactNode;
}) {
  const room = roomFor(slug);
  const crumb = back ?? { href: `/settings/room/${room}`, label: roomLabel(room) };
  return (
    <div className={cn("w-full", className)}>
      <nav aria-label="Breadcrumb" className="-ml-2 mb-1">
        <Link
          href={crumb.href}
          className="inline-flex min-h-[44px] items-center gap-0.5 rounded-md px-2 text-footnote font-medium text-text-secondary transition-colors duration-fast hover:text-text-primary"
        >
          <ChevronLeft className="h-4 w-4" aria-hidden />
          {crumb.label}
        </Link>
      </nav>
      {title !== null ? (
        <header className="mb-6 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="font-sans text-title1 text-text-primary">{title}</h1>
            {description ? <p className="mt-1 line-clamp-2 text-footnote text-text-muted">{description}</p> : null}
          </div>
          {actions ? <div className="flex flex-none items-center gap-2 pt-0.5">{actions}</div> : null}
        </header>
      ) : null}
      {children}
    </div>
  );
}
