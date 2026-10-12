"use client";

// The shared frame of the Edward tab's own pages (a room, the A–Z index):
// the reading column, one breadcrumb back to the landing, and the page's
// title1 heading with one line under it. Panels use PanelShell (Phase A),
// whose breadcrumb goes to their room instead.

import React, { type ReactNode } from "react";
import Link from "next/link";
import { ChevronLeft } from "lucide-react";
import { cn } from "@/lib/utils";

const WIDTH = {
  "3xl": "max-w-3xl",
  "4xl": "max-w-4xl",
  "5xl": "max-w-3xl lg:max-w-5xl",
} as const;

export function SettingsColumn({
  width = "4xl",
  className,
  children,
}: {
  width?: keyof typeof WIDTH;
  className?: string;
  children: ReactNode;
}) {
  return <div className={cn("mx-auto w-full px-4 pb-16 pt-1 md:px-8 md:pt-6", WIDTH[width], className)}>{children}</div>;
}

export function Breadcrumb({ href, children }: { href: string; children: ReactNode }) {
  return (
    <nav aria-label="Breadcrumb" className="-ml-2 mb-1">
      <Link
        href={href}
        className="inline-flex min-h-[44px] items-center gap-0.5 rounded-md px-2 text-footnote font-medium text-text-secondary transition-colors duration-fast hover:text-text-primary active:opacity-70"
      >
        <ChevronLeft className="h-4 w-4" aria-hidden />
        {children}
      </Link>
    </nav>
  );
}

export function PageHeader({ title, description }: { title: ReactNode; description?: ReactNode }) {
  return (
    <header className="mb-6">
      <h1 className="font-sans text-title1 text-text-primary">{title}</h1>
      {description ? <p className="mt-1 text-footnote text-text-muted">{description}</p> : null}
    </header>
  );
}

/** A labelled block of a page ("Waiting on you", "Settings"). */
export function PageSection({
  label,
  action,
  actionHref,
  className,
  children,
}: {
  label: ReactNode;
  action?: ReactNode;
  actionHref?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={cn("mt-7 first:mt-0", className)}>
      <div className="mb-2 flex min-h-[20px] items-center justify-between gap-3 px-0.5">
        <h2 className="text-overline text-text-muted">{label}</h2>
        {action ? (
          actionHref ? (
            <Link
              href={actionHref}
              className="-my-3 -mr-2 inline-flex min-h-[44px] items-center rounded-md px-2 text-footnote font-medium text-text-secondary transition-colors duration-fast hover:text-text-primary"
            >
              {action}
            </Link>
          ) : (
            <span className="text-caption text-text-muted">{action}</span>
          )
        ) : null}
      </div>
      {children}
    </section>
  );
}
