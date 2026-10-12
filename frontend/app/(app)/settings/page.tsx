"use client";

import React, { useEffect } from "react";
import { useRouter } from "next/navigation";
import { SettingsDashboard } from "@/components/settings/SettingsDashboard";
import { SETTINGS_INDEX } from "@/components/settings/settingsIndex";

export default function SettingsPage() {
  const router = useRouter();

  // Legacy hash redirect: /settings#heartbeat → /settings/heartbeat
  useEffect(() => {
    const hash = window.location.hash.slice(1);
    if (hash && SETTINGS_INDEX.some((i) => i.slug === hash)) {
      router.replace(`/settings/${hash}`);
    }
  }, [router]);

  return <SettingsDashboard />;
}
