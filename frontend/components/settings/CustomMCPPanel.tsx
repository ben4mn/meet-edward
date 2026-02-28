"use client";

import { useEffect, useState, useCallback } from "react";
import {
  ChevronDown,
  ChevronRight,
  Puzzle,
  Trash2,
  AlertCircle,
  RotateCw,
} from "lucide-react";
import {
  getCustomMCPServers,
  setCustomMCPEnabled,
  restartCustomMCPServer,
  removeCustomMCPServer,
  CustomMCPServer,
} from "@/lib/api";
import { cn } from "@/lib/utils";

function RuntimeBadge({ runtime }: { runtime: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono font-medium",
        runtime === "npx"
          ? "bg-yellow-400/10 text-yellow-400"
          : "bg-blue-400/10 text-blue-400"
      )}
    >
      {runtime}
    </span>
  );
}

function StatusDot({ status }: { status: string }) {
  const color = {
    connected: "bg-green-400",
    starting: "bg-yellow-400 animate-pulse",
    stopped: "bg-gray-400",
    disabled: "bg-gray-500",
    error: "bg-red-400",
  }[status] || "bg-gray-400";

  return <span className={cn("inline-block w-2 h-2 rounded-full", color)} />;
}

function Toggle({
  enabled,
  onChange,
  disabled,
}: {
  enabled: boolean;
  onChange: (enabled: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={() => !disabled && onChange(!enabled)}
      disabled={disabled}
      className={cn(
        "relative w-10 h-6 rounded-full transition-colors duration-200",
        enabled ? "bg-terminal" : "bg-input-border",
        disabled && "opacity-50 cursor-not-allowed"
      )}
    >
      <span
        className={cn(
          "absolute top-1 left-1 w-4 h-4 rounded-full bg-white transition-transform duration-200",
          enabled && "translate-x-4"
        )}
      />
    </button>
  );
}

interface CustomMCPPanelProps {
  isExpanded?: boolean;
  hideHeader?: boolean;
}

export function CustomMCPPanel({ isExpanded: initialExpanded = false, hideHeader = false }: CustomMCPPanelProps) {
  const [isExpanded, setIsExpanded] = useState(initialExpanded || hideHeader);
  const [servers, setServers] = useState<CustomMCPServer[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [updatingId, setUpdatingId] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);

  const loadServers = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await getCustomMCPServers();
      setServers(result.servers);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load servers");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isExpanded) {
      loadServers();
    }
  }, [isExpanded, loadServers]);

  const handleToggle = async (serverId: string, enabled: boolean) => {
    setUpdatingId(serverId);
    setError(null);
    try {
      const updated = await setCustomMCPEnabled(serverId, enabled);
      setServers((prev) =>
        prev.map((s) => (s.id === serverId ? updated : s))
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update server");
    } finally {
      setUpdatingId(null);
    }
  };

  const handleRestart = async (serverId: string) => {
    setUpdatingId(serverId);
    setError(null);
    try {
      const updated = await restartCustomMCPServer(serverId);
      setServers((prev) =>
        prev.map((s) => (s.id === serverId ? updated : s))
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to restart server");
    } finally {
      setUpdatingId(null);
    }
  };

  const handleRemove = async (serverId: string) => {
    setUpdatingId(serverId);
    setError(null);
    try {
      await removeCustomMCPServer(serverId);
      setServers((prev) => prev.filter((s) => s.id !== serverId));
      setConfirmRemove(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to remove server");
    } finally {
      setUpdatingId(null);
    }
  };

  const connectedCount = servers.filter((s) => s.status === "connected").length;

  return (
    <div className={cn("border border-input-border rounded-lg overflow-hidden", !hideHeader && "mt-8")}>
      {!hideHeader && (
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="w-full px-4 py-3 flex items-center justify-between bg-surface hover:bg-surface/80 transition-colors"
        >
          <div className="flex items-center gap-2 text-text-primary">
            <Puzzle className="w-4 h-4 text-terminal" />
            <span className="font-medium text-sm">Edward&apos;s Servers</span>
            {servers.length > 0 && (
              <span className="text-xs text-text-muted">
                ({connectedCount}/{servers.length} connected)
              </span>
            )}
          </div>
          {isExpanded ? (
            <ChevronDown className="w-4 h-4 text-text-muted" />
          ) : (
            <ChevronRight className="w-4 h-4 text-text-muted" />
          )}
        </button>
      )}

      {(isExpanded || hideHeader) && (
        <div className="p-4 space-y-3 bg-primary-bg border-t border-input-border">
          {error && (
            <div className="flex items-center gap-2 text-red-500 text-sm p-3 bg-red-500/10 rounded-lg">
              <AlertCircle className="w-4 h-4 flex-shrink-0" />
              {error}
            </div>
          )}

          {isLoading && servers.length === 0 ? (
            <div className="text-center py-8 text-text-muted">
              Loading servers...
            </div>
          ) : servers.length === 0 ? (
            <div className="text-center py-8 text-text-muted">
              <p>Edward hasn&apos;t added any MCP servers yet.</p>
              <p className="text-xs mt-1">
                Ask Edward to find and install MCP servers to extend his capabilities.
              </p>
            </div>
          ) : (
            servers.map((server) => (
              <div
                key={server.id}
                className="p-4 bg-surface rounded-lg border border-input-border"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-medium text-sm text-text-primary">
                        {server.name}
                      </span>
                      <RuntimeBadge runtime={server.runtime} />
                      <StatusDot status={server.status} />
                    </div>
                    {server.description && (
                      <p className="text-xs text-text-muted mb-1.5">
                        {server.description}
                      </p>
                    )}
                    <div className="flex items-center gap-3 text-xs text-text-muted">
                      <span className="font-mono">{server.package_name}</span>
                      <span>{server.tool_count} tools</span>
                      {server.args && server.args.length > 0 && (
                        <span>{server.args.length} arg{server.args.length !== 1 ? "s" : ""}</span>
                      )}
                    </div>
                    {server.env_var_keys && server.env_var_keys.length > 0 && (
                      <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
                        {server.env_var_keys.map((key) => (
                          <span
                            key={key}
                            className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono font-medium bg-green-400/10 text-green-400"
                          >
                            {key}
                          </span>
                        ))}
                      </div>
                    )}
                    {server.error && (
                      <p className="text-xs text-red-400 mt-1">{server.error}</p>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    {confirmRemove === server.id ? (
                      <div className="flex items-center gap-1">
                        <button
                          onClick={() => handleRemove(server.id)}
                          disabled={updatingId === server.id}
                          className="px-2 py-1 text-xs bg-red-500/20 text-red-400 rounded hover:bg-red-500/30 transition-colors disabled:opacity-50"
                        >
                          Confirm
                        </button>
                        <button
                          onClick={() => setConfirmRemove(null)}
                          className="px-2 py-1 text-xs bg-surface text-text-muted rounded hover:text-text-primary transition-colors"
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <>
                        <button
                          onClick={() => handleRestart(server.id)}
                          disabled={updatingId === server.id || !server.enabled}
                          className="p-1.5 rounded-lg text-text-muted hover:text-terminal transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                          title="Restart server"
                        >
                          <RotateCw className={cn("w-3.5 h-3.5", updatingId === server.id && "animate-spin")} />
                        </button>
                        <button
                          onClick={() => setConfirmRemove(server.id)}
                          className="p-1.5 rounded-lg text-text-muted hover:text-red-400 transition-colors"
                          title="Remove server"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </>
                    )}
                    <Toggle
                      enabled={server.enabled}
                      onChange={(enabled) => handleToggle(server.id, enabled)}
                      disabled={updatingId === server.id}
                    />
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
