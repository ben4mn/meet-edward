"use client";

// Settings → House → Edward's servers (2026-09-26, Jarvis polish).
//
// The tool servers Edward installed for himself (npx / uvx). Each one has a
// real switch, a status dot and, when it failed, what went wrong. Restart
// is one tap; removing asks once in a ConfirmSheet (it forgets the tools).

import React, { useCallback, useEffect, useState } from "react";
import { Puzzle, RotateCw, Trash2 } from "lucide-react";
import {
  getCustomMCPServers,
  removeCustomMCPServer,
  restartCustomMCPServer,
  setCustomMCPEnabled,
  type CustomMCPServer,
} from "@/lib/api";
import { serverStatus } from "@/lib/skillView";
import { Button, Card, ConfirmSheet, EmptyState, Notice, Pill, Skeleton, Switch, useToast } from "@/components/ui";
import { StatusDot } from "./panelBits";

export interface CustomMCPPanelProps {
  isExpanded?: boolean;
  hideHeader?: boolean;
}

export function CustomMCPPanel(_props: CustomMCPPanelProps = {}) {
  const { toast } = useToast();
  const [servers, setServers] = useState<CustomMCPServer[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [removing, setRemoving] = useState<CustomMCPServer | null>(null);

  const load = useCallback(async () => {
    setFailed(false);
    try {
      setServers((await getCustomMCPServers()).servers);
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const replace = (s: CustomMCPServer) => setServers((prev) => (prev ? prev.map((x) => (x.id === s.id ? s : x)) : prev));

  const toggle = async (s: CustomMCPServer, on: boolean) => {
    setBusy(s.id);
    replace({ ...s, enabled: on, status: on ? "starting" : "disabled" });
    try {
      replace(await setCustomMCPEnabled(s.id, on));
    } catch {
      replace(s);
      toast({ title: `I couldn't turn ${s.name} ${on ? "on" : "off"}.`, tone: "bad" });
    } finally {
      setBusy(null);
    }
  };

  const restart = async (s: CustomMCPServer) => {
    setBusy(s.id);
    replace({ ...s, status: "starting" });
    try {
      replace(await restartCustomMCPServer(s.id));
    } catch {
      replace(s);
      toast({ title: `I couldn't restart ${s.name}.`, tone: "bad" });
    } finally {
      setBusy(null);
    }
  };

  const remove = async () => {
    if (!removing) return;
    const s = removing;
    try {
      await removeCustomMCPServer(s.id);
      setServers((prev) => (prev ? prev.filter((x) => x.id !== s.id) : prev));
      toast({ title: `Removed ${s.name}` });
    } catch {
      toast({ title: `I couldn't remove ${s.name}.`, tone: "bad" });
    } finally {
      setRemoving(null);
    }
  };

  if (failed && !servers) {
    return (
      <Notice tone="bad" action={<Button size="sm" variant="secondary" onClick={load}>Try again</Button>}>
        I can&apos;t read my servers right now.
      </Notice>
    );
  }
  if (!servers) {
    return (
      <Card className="p-4">
        <Skeleton lines={4} />
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      {servers.length === 0 ? (
        <EmptyState icon={<Puzzle />} title="I haven't added any tools of my own yet.">
          Ask me to find one — for example, “find a weather tool and add it.”
        </EmptyState>
      ) : (
        <>
          <p className="px-0.5 text-footnote text-text-muted">
            Tools I found and installed for myself. Turning one off hides its tools from me until you turn it back on.
          </p>
          {servers.map((s) => {
            const st = serverStatus(s.status);
            return (
              <Card key={s.id} as="article" className="space-y-3 p-4">
                <div className="flex items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="text-callout text-text-primary">{s.name}</p>
                    <p className="mt-0.5 flex items-center gap-1.5 text-caption text-text-muted">
                      <StatusDot tone={st.tone} />
                      {st.text}
                    </p>
                  </div>
                  <Switch checked={s.enabled} onChange={(on) => toggle(s, on)} label={s.name} disabled={busy === s.id} />
                </div>
                {s.description ? <p className="text-footnote text-text-secondary">{s.description}</p> : null}
                <p className="text-caption text-text-muted">
                  {s.tool_count} {s.tool_count === 1 ? "tool" : "tools"} · <span className="font-mono">{s.package_name}</span> via {s.runtime}
                  {s.args?.length ? ` · ${s.args.length} ${s.args.length === 1 ? "setting" : "settings"}` : ""}
                </p>
                {s.env_var_keys?.length ? (
                  <div className="flex flex-wrap gap-1.5">
                    {s.env_var_keys.map((k) => (
                      <Pill key={k} className="font-mono">
                        {k}
                      </Pill>
                    ))}
                  </div>
                ) : null}
                {s.error ? <Notice tone="bad">{s.error}</Notice> : null}
                <div className="-mb-1 flex items-center gap-1 border-t border-border pt-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    icon={<RotateCw aria-hidden />}
                    onClick={() => restart(s)}
                    disabled={!s.enabled}
                    loading={busy === s.id && s.status === "starting"}
                  >
                    Restart
                  </Button>
                  <div className="flex-1" />
                  <Button
                    variant="ghost"
                    size="sm"
                    icon={<Trash2 aria-hidden />}
                    aria-label={`Remove ${s.name}`}
                    className="hover:text-bad"
                    onClick={() => setRemoving(s)}
                  >
                    Remove
                  </Button>
                </div>
              </Card>
            );
          })}
        </>
      )}
      <ConfirmSheet
        open={Boolean(removing)}
        onClose={() => setRemoving(null)}
        onConfirm={remove}
        title={`Remove ${removing?.name ?? "this server"}?`}
        body="I'll stop it and forget its tools. You can ask me to add it again later."
        confirmLabel="Remove"
      />
    </div>
  );
}
