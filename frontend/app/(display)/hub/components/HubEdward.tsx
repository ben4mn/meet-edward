"use client";

// The hub greeting's Edward with his presence (2026-09-26 perf pass: its own
// chunk, so the Pi hydrates the home screen without the presence model —
// page.tsx loads it with lib/lazyComponent and starts the fetch as it runs).
//
// Edward's real presence (the same signals /screen reads). The hub is the
// household's screen: the owner's unread thoughts, held approvals and Chrome link
// are not shown here — an amber light at the pet station means a late dose,
// nothing else. He waves whenever someone new taps in (`waveKey`).

import React from "react";

import { useUserTimezone } from "@/lib/useTimezone";

import { useEdwardState } from "../../display/hooks/useEdwardState";
import { GreetingEdward } from "./GreetingEdward";

export function HubEdward({ waveKey }: { waveKey: number }) {
  const edward = useEdwardState(useUserTimezone(), { audience: "household" });
  return (
    <GreetingEdward
      mood={edward.presence.mood}
      label={edward.presence.known ? edward.presence.label : "Edward"}
      waveKey={waveKey}
    />
  );
}
