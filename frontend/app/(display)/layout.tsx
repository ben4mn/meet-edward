"use client";

import { AuthProvider } from "@/lib/AuthContext";
import { AuthGuard } from "@/components/auth/AuthGuard";
import { TimezoneProvider } from "@/lib/useTimezone";
import { useBuildReload } from "@/lib/buildReload";

export default function DisplayLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // The /screen tab and the hub kiosk are never reloaded by hand: they pick
  // up a new deploy themselves, whenever nobody is touching the screen
  // (H3, 2026-09-27; lib/buildReload.ts).
  useBuildReload("display");
  return (
    <AuthProvider>
      <AuthGuard>
        <TimezoneProvider>{children}</TimezoneProvider>
      </AuthGuard>
    </AuthProvider>
  );
}
