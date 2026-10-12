"use client";

// Settings → House → Locale (2026-09-26, Jarvis polish).
//
// The one time zone: what Edward means by "local", every timestamp in the
// app, and when his crons fire. A 16px field with the IANA list as
// suggestions, what time it is there, this device's zone one tap away, and
// Save only when something changed.

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { getSettings, updateSettings, type Settings } from "@/lib/api";
import { joinClockParts } from "@/lib/time";
import { useSetUserTimezone, useUserTimezone } from "@/lib/useTimezone";
import { Button, Card, Field, Input, Notice, Skeleton, useToast } from "@/components/ui";

export interface LocalePanelProps {
  isExpanded?: boolean;
  hideHeader?: boolean;
}

function ianaZones(): string[] {
  try {
    const fn = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf;
    if (typeof fn === "function") {
      const zones = fn("timeZone");
      if (Array.isArray(zones) && zones.length) return zones;
    }
  } catch {
    // older runtime
  }
  return [
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Phoenix",
    "America/Anchorage",
    "Pacific/Honolulu",
    "Europe/London",
    "Europe/Paris",
    "Europe/Berlin",
    "Asia/Tokyo",
    "Asia/Singapore",
    "Australia/Sydney",
    "UTC",
  ];
}

function zoneIsValid(zone: string): boolean {
  if (!zone) return false;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: zone });
    return true;
  } catch {
    return false;
  }
}

/** "Sun 7:34 pm CDT" — the app's one clock (L1, 2026-09-27). */
function nowIn(zone: string): string {
  try {
    return joinClockParts(
      new Intl.DateTimeFormat("en-US", {
        timeZone: zone,
        weekday: "short",
        hour: "numeric",
        minute: "2-digit",
        timeZoneName: "short",
      }).formatToParts(new Date()),
    );
  } catch {
    return "—";
  }
}

function deviceZone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    return null;
  }
}

export function LocalePanel(_props: LocalePanelProps = {}) {
  const setUserTz = useSetUserTimezone();
  const currentTz = useUserTimezone();
  const { toast } = useToast();
  const [settings, setSettings] = useState<Settings | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const zones = useMemo(() => ianaZones(), []);
  const device = useMemo(() => deviceZone(), []);

  const load = useCallback(async () => {
    setFailed(false);
    try {
      const s = await getSettings();
      setSettings(s);
      setDraft(s.timezone || currentTz);
    } catch {
      setFailed(true);
    }
  }, [currentTz]);

  useEffect(() => {
    load();
    // Load once; the context zone only seeds an empty setting.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const save = async () => {
    const zone = draft.trim();
    if (!zoneIsValid(zone)) {
      setError(`I don't know “${zone}”. Pick one from the list, like America/Chicago.`);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const updated = await updateSettings({ timezone: zone });
      setSettings(updated);
      setUserTz(updated.timezone);
      toast({ title: "Time zone saved", body: updated.timezone, tone: "ok" });
    } catch {
      setError("I couldn't save that just now.");
    } finally {
      setSaving(false);
    }
  };

  if (failed && !settings) {
    return (
      <Notice tone="bad" action={<Button size="sm" variant="secondary" onClick={load}>Try again</Button>}>
        I can&apos;t read my settings right now.
      </Notice>
    );
  }
  if (!settings) {
    return (
      <Card className="p-4">
        <Skeleton lines={3} />
      </Card>
    );
  }

  const changed = draft.trim() !== "" && draft.trim() !== settings.timezone;
  const shown = zoneIsValid(draft.trim()) ? draft.trim() : settings.timezone;

  return (
    <div className="space-y-4">
      <Card className="space-y-4 p-4">
        <Field
          label="Time zone"
          hint="What I mean by “local”: every time in the app, and when my reminders and wake-ups fire."
          error={error ?? undefined}
        >
          <Input
            list="iana-timezones"
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              setError(null);
            }}
            placeholder="America/Chicago"
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
          />
        </Field>
        <datalist id="iana-timezones">
          {zones.map((z) => (
            <option key={z} value={z} />
          ))}
        </datalist>
        <p className="text-footnote text-text-muted">
          It&apos;s <span className="tabular-nums text-text-primary">{nowIn(shown)}</span> there.
        </p>
        <Button className="w-full" onClick={save} loading={saving} disabled={!changed}>
          Save
        </Button>
      </Card>

      {device && device !== settings.timezone ? (
        <Card className="flex items-center gap-3 p-4">
          <div className="min-w-0 flex-1">
            <p className="text-callout text-text-primary">This device is on {device}</p>
            <p className="mt-0.5 text-footnote text-text-muted">{nowIn(device)}</p>
          </div>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              setDraft(device);
              setError(null);
            }}
            disabled={draft === device}
          >
            Use it
          </Button>
        </Card>
      ) : null}
    </div>
  );
}
