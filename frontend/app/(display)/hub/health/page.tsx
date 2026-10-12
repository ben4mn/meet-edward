"use client";

import "../hub.css";
import { HubRoot } from "../components/HubRoot";

import { useEffect, useState } from "react";

import { usePoll } from "../../display/hooks/usePoll";
import { useWakeLock } from "../../display/hooks/useWakeLock";
import { HubHeader } from "../components/HubHeader";
import { Meter } from "../components/Meter";
import { StatTile } from "../components/StatTile";
import { useIdleRedirect } from "../lib/useIdleRedirect";
import {
  fetchHealth,
  fetchHubSettings,
  fetchModels,
  fetchSettings,
  fetchVitals,
  saveHubSettings,
  saveSettings,
} from "../lib/hubApi";
import type {
  EdwardSettings,
  HubSettings,
  SlideTransition,
  HealthComponent,
  HealthResponse,
  HealthStatus,
  ModelsResponse,
  VitalsResponse,
} from "../lib/types";

const POLL_MS = 30_000;

const HEADLINE: Record<HealthStatus, string> = {
  healthy: "All good",
  repaired: "Fixed itself",
  stale: "Watchdog is quiet",
  unknown: "Can't tell",
  problem: "Needs a human",
};

// Status is never carried by colour alone — each state ships a glyph.
const GLYPH: Record<HealthStatus, string> = {
  healthy: "✓",
  repaired: "↻",
  stale: "?",
  unknown: "?",
  problem: "!",
};

/** A LaunchAgent's service name: the server's, else the label without the
 * host's prefix, else its last part ("com.example.tunnel" -> "tunnel"). */
function shortLabel(item: HealthComponent, prefix?: string): string {
  if (item.name) return item.name;
  const label = item.label;
  if (prefix && label.startsWith(`${prefix}.`)) return label.slice(prefix.length + 1);
  return label.includes(".") ? label.slice(label.lastIndexOf(".") + 1) : label;
}

function fmtUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  return d ? `${d}d ${h}h` : `${h}h`;
}

function fmtCount(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

function Row({ item, prefix }: { item: HealthComponent; prefix?: string }) {
  const detail =
    item.detail ??
    (item.pid ? `pid ${item.pid}` : item.exit !== null && item.exit !== undefined ? `exit ${item.exit}` : "");
  return (
    <div className="hub-health-row">
      <span className="hub-health-glyph" data-status={item.status}>
        {GLYPH[item.status]}
      </span>
      <span className="hub-health-name">{shortLabel(item, prefix)}</span>
      <span className="hub-health-detail">{detail}</span>
    </div>
  );
}

export default function HubHealthPage() {
  useWakeLock(true);
  useIdleRedirect();

  const { data: health, error } = usePoll<HealthResponse>(fetchHealth, POLL_MS);
  const { data: vitals } = usePoll<VitalsResponse>(fetchVitals, POLL_MS);

  const overall: HealthStatus = health?.overall ?? "unknown";
  const wd = health?.watchdog;

  // ---- settings ----
  const [settings, setSettings] = useState<EdwardSettings | null>(null);
  const [models, setModels] = useState<ModelsResponse | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const [hub, setHub] = useState<HubSettings | null>(null);

  useEffect(() => {
    fetchSettings().then(setSettings).catch(() => {});
    fetchModels().then(setModels).catch(() => {});
    fetchHubSettings().then(setHub).catch(() => {});
  }, []);

  // Screensaver settings save immediately — each is a single tap or drag
  // with an obvious visible result, so a Save button would just be a step
  // between you and seeing the change.
  const patchHub = (p: Partial<HubSettings>) => {
    setHub((h) => (h ? { ...h, ...p } : h));
    saveHubSettings(p).then(setHub).catch(() => {});
  };

  const patch = (p: Partial<EdwardSettings>) => {
    setSettings((s) => (s ? { ...s, ...p } : s));
    setDirty(true);
    setSaved(false);
  };

  const commit = async () => {
    if (!settings || saving) return;
    setSaving(true);
    try {
      await saveSettings({
        model: settings.model,
        provider: settings.provider,
        temperature: settings.temperature,
      });
      setDirty(false);
      setSaved(true);
    } finally {
      setSaving(false);
    }
  };

  const modelOptions = models
    ? Object.entries(models.providers).flatMap(([provider, p]) =>
        p.available ? p.models.map((m) => ({ ...m, provider })) : [],
      )
    : [];

  return (
    <HubRoot>
      <HubHeader title="Health" />

      {/* KPI row — headline numbers, no plots. */}
      <section className="hub-kpis">
        <StatTile value={vitals ? fmtUptime(vitals.uptime_seconds) : "—"} label="Uptime" />
        <StatTile
          value={vitals ? `$${vitals.cost_today.toFixed(2)}` : "—"}
          label="Cost today"
          sub={vitals ? `${fmtCount(vitals.tokens_in_today + vitals.tokens_out_today)} tokens` : undefined}
        />
        <StatTile value={vitals ? fmtCount(vitals.memory_count) : "—"} label="Memories" sub={vitals ? `${vitals.docs_count} docs` : undefined} />
        <StatTile value={vitals ? String(vitals.convos_today) : "—"} label="Convos today" />
        <div className="hub-stat hub-stat-status" data-status={overall}>
          <div className="hub-stat-value">
            <span className="hub-health-glyph" data-status={overall}>
              {GLYPH[overall]}
            </span>{" "}
            {HEADLINE[overall]}
          </div>
          <div className="hub-stat-label">{health?.host ?? "…"}</div>
          <div className="hub-stat-sub">
            {wd?.checked_at
              ? `checked ${wd.age_minutes ?? "?"}m ago`
              : "no watchdog report"}
          </div>
        </div>
      </section>

      <main className="hub-two-col">
        <section className="hub-card">
          <h2 className="hub-card-title">Machine</h2>

          <div className="hub-meters">
            <Meter label="CPU" pct={vitals?.cpu_pct ?? 0} />
            <Meter label="Memory" pct={vitals?.mem_pct ?? 0} />
            <Meter
              label="Disk"
              pct={health?.components.find((c) => c.label === "disk")?.pct ?? vitals?.disk_pct ?? 0}
              caption={health?.components.find((c) => c.label === "disk")?.detail ?? undefined}
            />
          </div>

          {wd?.problems?.length ? (
            <div className="hub-rows">
              {wd.problems.map((p) => (
                <div className="hub-health-problem" key={p}>
                  ! {p}
                </div>
              ))}
            </div>
          ) : null}
          {wd?.repairs?.length ? (
            <div className="hub-rows">
              {wd.repairs.map((r) => (
                <div className="hub-health-repair" key={r}>
                  ↻ repaired · {r}
                </div>
              ))}
            </div>
          ) : null}
          {error && <div className="hub-health-problem">! Can&apos;t reach the backend</div>}

          <div className="hub-rows hub-rows-scroll hub-services">
            {health?.agents.map((a) => <Row item={a} prefix={health?.label_prefix} key={a.label} />)}
            {health?.components.map((c) => <Row item={c} key={c.label} />)}
            {!health && <div className="hub-empty-lg">Checking…</div>}
          </div>
        </section>

        <section className="hub-card">
          <h2 className="hub-card-title">Edward</h2>

          <div className="hub-setting">
            <label className="hub-setting-label" htmlFor="hub-model">
              Model
            </label>
            <select
              id="hub-model"
              className="hub-select"
              value={settings?.model ?? ""}
              onChange={(e) => {
                const opt = modelOptions.find((m) => m.id === e.target.value);
                patch({ model: e.target.value, provider: opt?.provider ?? settings?.provider });
              }}
            >
              {modelOptions.map((m) => (
                <option key={`${m.provider}-${m.id}`} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          </div>

          <div className="hub-setting">
            <label className="hub-setting-label" htmlFor="hub-temp">
              Temperature
              <span className="hub-setting-value">{(settings?.temperature ?? 0).toFixed(2)}</span>
            </label>
            <input
              id="hub-temp"
              className="hub-slider"
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={settings?.temperature ?? 0.7}
              onChange={(e) => patch({ temperature: Number(e.target.value) })}
            />
            <div className="hub-setting-hint">precise ← → creative</div>
          </div>

          {/* Deliberately no skill toggles here yet — a wall panel with no
              PIN gate is the wrong place to disable Edward's integrations. */}
          <div className="hub-setting-note">
            Skills, integrations, and anything destructive stay in the full settings
            page until the hub has a PIN gate.
          </div>

          <h2 className="hub-card-title" style={{ marginTop: 14 }}>
            Screensaver
          </h2>

          <div className="hub-setting">
            <label className="hub-setting-label" htmlFor="hub-slide">
              Seconds per photo
              <span className="hub-setting-value">{hub?.slide_seconds ?? "—"}s</span>
            </label>
            <input
              id="hub-slide"
              className="hub-slider"
              type="range"
              min={5}
              max={180}
              step={5}
              value={hub?.slide_seconds ?? 30}
              onChange={(e) => patchHub({ slide_seconds: Number(e.target.value) })}
            />
            <div className="hub-setting-hint">quick ← → lingering</div>
          </div>

          <div className="hub-setting">
            <span className="hub-setting-label">Transition</span>
            <div className="hub-segmented">
              {(["crossfade", "kenburns", "slide"] as SlideTransition[]).map((t) => (
                <button
                  key={t}
                  data-active={hub?.transition === t}
                  onClick={() => patchHub({ transition: t })}
                >
                  {t === "kenburns" ? "Ken Burns" : t === "crossfade" ? "Fade" : "Slide"}
                </button>
              ))}
            </div>
          </div>

          <div className="hub-setting">
            <span className="hub-setting-label">Edward pushes the slides</span>
            <div className="hub-segmented">
              <button
                data-active={hub?.edward_presents === true}
                onClick={() => patchHub({ edward_presents: true })}
              >
                On
              </button>
              <button
                data-active={hub?.edward_presents === false}
                onClick={() => patchHub({ edward_presents: false })}
              >
                Off
              </button>
            </div>
          </div>

          <div className="hub-setting-actions">
            <button className="hub-add-btn" onClick={commit} disabled={!dirty || saving}>
              {saving ? "Saving…" : saved ? "Saved" : "Save"}
            </button>
            <span className="hub-setting-hint">
              {settings ? `${settings.provider} · ${settings.model}` : "loading…"}
            </span>
          </div>
        </section>
      </main>
    </HubRoot>
  );
}
