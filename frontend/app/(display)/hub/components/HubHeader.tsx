"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { formatClock } from "@/lib/time";

import { WhoToggle } from "./WhoToggle";
import { useWho } from "../lib/useWho";

export function useClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 10_000);
    return () => clearInterval(id);
  }, []);
  return now;
}

/** Shared chrome: back to home, the clock, and who is logging. */
export function HubHeader({
  title,
  children,
}: {
  title?: string;
  children?: React.ReactNode;
}) {
  const clock = useClock();
  const { who, setWho } = useWho();

  return (
    <header className="hub-header">
      <Link href="/hub" className="hub-back" aria-label="Back to hub home">
        ‹
      </Link>
      <div>
        <div className="hub-clock">
          {/* The app's one clock, "7:34 pm" (L1, 2026-09-27): the browser's
              locale said "7:34 PM" here, or "19:34" on an en-GB Pi. */}
          {formatClock(clock)}
        </div>
        <div className="hub-date">
          {title ?? clock.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })}
        </div>
      </div>
      <div className="hub-alerts">{children}</div>
      <WhoToggle who={who} onPick={setWho} />
    </header>
  );
}
