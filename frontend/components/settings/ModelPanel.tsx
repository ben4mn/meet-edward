"use client";

// Settings → Mind → Model. The model Edward chats with, as radio cards that
// save on tap (with Undo in a toast), and a read-only card saying which
// model does which kind of thinking. Only the live Codex catalog is offered
// (lib/modelCatalog.ts); Anthropic is a disabled backup and has no button.
// How much of his work the chat shows is a per-device preference that lives
// in the Mind room, not here.

import React, { useCallback, useEffect, useRef, useState } from "react";
import { Check } from "lucide-react";
import { getProviders, getSettings, updateSettings, type Settings } from "@/lib/api";
import { modelName, parseCatalog, whoThinksWhat, type Catalog, type CatalogModel } from "@/lib/modelCatalog";
import { Button, Card, Notice, Pill, SectionLabel, Skeleton, cn, useToast } from "@/components/ui";

export function ModelPanel(_props: { isExpanded?: boolean; hideHeader?: boolean }) {
  const { toast } = useToast();
  const [settings, setSettings] = useState<Settings | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState(false);
  // The toast's Undo runs long after the tap: it must see the settings as
  // they are then, not as they were when the tap's `save` was made (that
  // stale copy said "already on Astra" and sent nothing).
  const settingsRef = useRef<Settings | null>(null);
  settingsRef.current = settings;

  const load = useCallback(async () => {
    try {
      const [s, p] = await Promise.all([getSettings(), getProviders()]);
      setSettings(s);
      setCatalog(parseCatalog(p));
      setError(false);
    } catch {
      setError(true);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const save = useCallback(
    async (model: string, opts: { undoOf?: string } = {}) => {
      const current = settingsRef.current;
      if (!current || !catalog || model === current.model) return;
      const previous = current.model;
      setSaving(model);
      setSettings({ ...current, model });
      try {
        const updated = await updateSettings({ provider: catalog.provider, model });
        setSettings(updated);
        if (!opts.undoOf) {
          toast({
            title: `I'll chat with ${modelName(catalog, model)} now`,
            tone: "edward",
            undo: () => {
              void save(previous, { undoOf: model });
            },
          });
        }
      } catch (e) {
        setSettings((s) => (s ? { ...s, model: previous } : s));
        toast({
          title: "I couldn't switch models.",
          body: e instanceof Error && e.message !== "Failed to update settings" ? e.message : "Try again in a moment.",
          tone: "bad",
        });
      } finally {
        setSaving(null);
      }
    },
    [catalog, toast],
  );

  if (error && !settings) {
    return (
      <Notice
        tone="bad"
        title="I can't read my settings right now."
        action={
          <Button variant="ghost" size="sm" onClick={load}>
            Try again
          </Button>
        }
      />
    );
  }

  if (!settings || !catalog) {
    return (
      <div className="space-y-3">
        <Card>
          <Skeleton lines={2} />
        </Card>
        <Card>
          <Skeleton lines={2} />
        </Card>
      </div>
    );
  }

  const inCatalog = catalog.models.some((m) => m.id === settings.model);
  const roles = whoThinksWhat(catalog, settings.model);

  return (
    <div className="space-y-6">
      {!catalog.available ? (
        <Notice tone="warn" title="I can't reach Codex right now.">
          Run <code className="rounded-xs bg-surface-3 px-1 font-mono">codex login</code> on the Mac I run on, then come back.
        </Notice>
      ) : null}

      <section className="space-y-2">
        <SectionLabel>What I chat with</SectionLabel>
        {!inCatalog ? (
          <Notice tone="warn" title={`Saved: ${settings.model}`}>
            That model isn&apos;t in the catalog any more — pick one below.
          </Notice>
        ) : null}
        <div role="radiogroup" aria-label="Chat model" className="space-y-2">
          {catalog.models.map((m) => (
            <ModelCard
              key={m.id}
              model={m}
              checked={m.id === settings.model}
              busy={saving === m.id}
              disabled={!catalog.available || saving !== null}
              onPick={() => save(m.id)}
            />
          ))}
        </div>
      </section>

      {roles.length ? (
        <section className="space-y-2">
          <SectionLabel>Who thinks what</SectionLabel>
          <Card className="px-4 py-1">
            {roles.map((r) => (
              <div key={r.role} className="border-b border-border py-2 last:border-b-0">
                <div className="flex min-h-[28px] items-center justify-between gap-3">
                  <span className="text-footnote text-text-secondary">{r.role}</span>
                  <span className="min-w-0 truncate text-right text-footnote text-text-primary">
                    {r.model}
                    {r.effort ? <span className="text-text-muted"> · {r.effort} effort</span> : null}
                  </span>
                </div>
                {r.detail ? <p className="mt-0.5 text-caption text-text-muted">{r.detail}</p> : null}
              </div>
            ))}
          </Card>
          <p className="px-0.5 text-caption text-text-muted">
            Only chat is chosen here; the rest are set on the Mac I run on.
          </p>
        </section>
      ) : null}
    </div>
  );
}

function ModelCard({
  model: m,
  checked,
  busy,
  disabled,
  onPick,
}: {
  model: CatalogModel;
  checked: boolean;
  busy: boolean;
  disabled: boolean;
  onPick: () => void;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={checked}
      aria-busy={busy || undefined}
      disabled={disabled && !checked}
      onClick={onPick}
      className={cn(
        "flex w-full items-start gap-3 rounded-lg border bg-surface p-4 text-left shadow-e1",
        "transition-[transform,background-color,border-color,box-shadow] duration-press ease-out active:scale-[.985]",
        "disabled:cursor-default disabled:opacity-60",
        checked ? "border-text-primary/40 bg-surface-elevated" : "border-border hover:bg-surface-elevated",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "mt-0.5 flex h-5 w-5 flex-none items-center justify-center rounded-full border transition-colors duration-fast",
          checked ? "border-text-primary bg-text-primary text-primary-bg" : "border-text-muted/60",
        )}
      >
        {checked ? <Check className="h-3 w-3" strokeWidth={3} /> : null}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-callout text-text-primary">{m.name}</span>
          {m.recommended ? <Pill>Recommended</Pill> : null}
          {busy ? <span className="text-caption text-text-muted">Saving…</span> : null}
        </span>
        {m.description ? <span className="mt-0.5 block text-footnote text-text-muted">{m.description}</span> : null}
        {m.effort ? <span className="mt-1 block text-caption text-text-muted">Thinks at {m.effort} effort by default</span> : null}
      </span>
    </button>
  );
}
