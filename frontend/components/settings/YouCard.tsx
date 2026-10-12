"use client";

// "You" — the owner's own corner of the Edward tab (docs/design-system-2026-09-25.md
// §5: theme and sign-out live here on the phone, not in the header).
// Appearance is Light / Dark / System (System follows the device); signing
// out asks first (the shell's SignOutSheet). Props-less; the settings page
// mounts it.

import React from "react";
import { LogOut } from "lucide-react";
import { Card, Segment, SectionLabel } from "@/components/ui";
import { useThemeMode, type ThemeMode } from "@/lib/themeMode";
import { signOutStore } from "@/components/layout/shellStores";
import { cn } from "@/lib/utils";

const MODES: { value: ThemeMode; label: string }[] = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
  { value: "system", label: "System" },
];

export default function YouCard() {
  const { mode, setMode } = useThemeMode();
  return (
    <section aria-labelledby="you-card-title" className="space-y-2">
      <SectionLabel>
        <span id="you-card-title">You</span>
      </SectionLabel>
      <Card className="p-0">
        <div className="flex flex-col gap-2.5 px-3.5 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-callout text-text-primary">Appearance</p>
            <p className="text-footnote text-text-muted">
              {mode === "system" ? "Follows your device" : mode === "light" ? "Light, always" : "Dark, always"}
            </p>
          </div>
          <Segment<ThemeMode>
            label="Appearance"
            value={mode}
            onChange={setMode}
            options={MODES}
            className="w-full sm:w-[240px]"
          />
        </div>
        <div className="border-t border-border" />
        <button
          type="button"
          onClick={signOutStore.open}
          className={cn(
            "flex min-h-[48px] w-full items-center gap-3 px-3.5 py-3 text-left",
            "transition-colors duration-fast hover:bg-surface-3/50 active:bg-surface-3/80",
          )}
        >
          <LogOut className="h-[18px] w-[18px] flex-none text-text-secondary" aria-hidden />
          <span className="flex-1 text-callout text-text-primary">Sign out</span>
          <span className="text-footnote text-text-muted">This device</span>
        </button>
      </Card>
    </section>
  );
}
