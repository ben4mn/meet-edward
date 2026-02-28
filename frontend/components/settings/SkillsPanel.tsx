"use client";

import { useEffect, useState, useCallback } from "react";
import {
  ChevronDown,
  ChevronRight,
  Zap,
  RefreshCw,
  AlertCircle,
  MessageSquare,
  Phone,
  Search,
  Code,
  Database,
  Terminal,
  Globe,
  Smartphone,
} from "lucide-react";
import {
  getSkills,
  setSkillEnabled,
  reloadSkills,
  Skill,
  SkillStatusType,
} from "@/lib/api";
import { cn } from "@/lib/utils";

interface SkillsPanelProps {
  isExpanded?: boolean;
  hideHeader?: boolean;
}

// Status indicator component
function StatusIndicator({ status }: { status: SkillStatusType }) {
  const config = {
    connected: {
      icon: "\u25CF",
      color: "text-green-400",
      bgColor: "bg-green-400/10",
    },
    connecting: {
      icon: "\u25D0",
      color: "text-yellow-400",
      bgColor: "bg-yellow-400/10",
    },
    error: {
      icon: "\u2717",
      color: "text-red-400",
      bgColor: "bg-red-400/10",
    },
    disabled: {
      icon: "\u25CB",
      color: "text-text-muted",
      bgColor: "bg-surface",
    },
  };

  const { icon, color, bgColor } = config[status];

  return (
    <span
      className={cn(
        "inline-flex items-center justify-center w-5 h-5 rounded-full text-xs",
        bgColor,
        color
      )}
    >
      {icon}
    </span>
  );
}

// Toggle switch component
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

// Get icon for skill type
function getSkillIcon(skillId: string) {
  if (skillId.includes("imessage")) {
    return <MessageSquare className="w-4 h-4" />;
  }
  if (skillId.includes("twilio") || skillId.includes("sms")) {
    return <Phone className="w-4 h-4" />;
  }
  if (skillId.includes("search") || skillId.includes("brave")) {
    return <Search className="w-4 h-4" />;
  }
  if (skillId.includes("javascript")) {
    return <Code className="w-4 h-4" />;
  }
  if (skillId.includes("sql")) {
    return <Database className="w-4 h-4" />;
  }
  if (skillId.includes("shell")) {
    return <Terminal className="w-4 h-4" />;
  }
  if (skillId.includes("html_hosting")) {
    return <Globe className="w-4 h-4" />;
  }
  if (skillId.includes("widget")) {
    return <Smartphone className="w-4 h-4" />;
  }
  return <Zap className="w-4 h-4" />;
}

export function SkillsPanel({ isExpanded: initialExpanded = false, hideHeader = false }: SkillsPanelProps) {
  const [isExpanded, setIsExpanded] = useState(initialExpanded);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [lastReload, setLastReload] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isReloading, setIsReloading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [updatingSkill, setUpdatingSkill] = useState<string | null>(null);

  const loadSkills = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await getSkills();
      setSkills(result.skills);
      setLastReload(result.last_reload);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load skills");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isExpanded) {
      loadSkills();
    }
  }, [isExpanded, loadSkills]);

  const handleToggle = async (skillId: string, enabled: boolean) => {
    setUpdatingSkill(skillId);
    setError(null);
    try {
      const updatedSkill = await setSkillEnabled(skillId, enabled);
      setSkills((prev) =>
        prev.map((s) => (s.id === skillId ? updatedSkill : s))
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update skill");
    } finally {
      setUpdatingSkill(null);
    }
  };

  const handleReload = async () => {
    setIsReloading(true);
    setError(null);
    try {
      const result = await reloadSkills();
      setSkills(result.skills);
      setLastReload(result.last_reload);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reload skills");
    } finally {
      setIsReloading(false);
    }
  };

  const formatStatus = (status: SkillStatusType) => {
    const labels: Record<SkillStatusType, string> = {
      connected: "Connected",
      connecting: "Connecting...",
      error: "Error",
      disabled: "Disabled",
    };
    return labels[status];
  };

  const connectedCount = skills.filter((s) => s.status === "connected").length;
  const enabledCount = skills.filter((s) => s.enabled).length;

  return (
    <div className={cn("border border-input-border rounded-lg overflow-hidden", !hideHeader && "mt-8")}>
      {!hideHeader && (
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="w-full px-4 py-3 flex items-center justify-between bg-surface hover:bg-surface/80 transition-colors"
        >
          <div className="flex items-center gap-2 text-text-primary">
            <Zap className="w-4 h-4 text-terminal" />
            <span className="font-medium text-sm">Skills</span>
            {skills.length > 0 && (
              <span className="text-xs text-text-muted">
                ({connectedCount}/{enabledCount} connected)
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            {isExpanded && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  handleReload();
                }}
                disabled={isReloading}
                className="p-1.5 rounded-lg border border-input-border bg-input-bg text-text-muted hover:text-text-primary transition-colors disabled:opacity-50"
                title="Reload skills"
              >
                <RefreshCw className={cn("w-3.5 h-3.5", isReloading && "animate-spin")} />
              </button>
            )}
            {isExpanded ? (
              <ChevronDown className="w-4 h-4 text-text-muted" />
            ) : (
              <ChevronRight className="w-4 h-4 text-text-muted" />
            )}
          </div>
        </button>
      )}

      {(isExpanded || hideHeader) && (
        <div className="p-4 space-y-3 bg-primary-bg border-t border-input-border">
          {error && (
            <div className="flex items-center gap-2 text-red-500 text-sm p-3 bg-red-500/10 rounded-lg">
              <AlertCircle className="w-4 h-4" />
              {error}
            </div>
          )}

          {isLoading && skills.length === 0 ? (
            <div className="text-center py-8 text-text-muted">Loading skills...</div>
          ) : skills.length === 0 ? (
            <div className="text-center py-8 text-text-muted">No skills available</div>
          ) : (
            skills.map((skill) => (
              <div
                key={skill.id}
                className="p-4 bg-surface rounded-lg border border-input-border"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-terminal">{getSkillIcon(skill.id)}</span>
                      <span className="font-medium text-sm text-text-primary">
                        {skill.name}
                      </span>
                    </div>
                    <p className="text-xs text-text-muted mb-2">{skill.description}</p>
                    <div className="flex items-center gap-2">
                      <StatusIndicator status={skill.status} />
                      <span
                        className={cn(
                          "text-xs",
                          skill.status === "connected" && "text-green-400",
                          skill.status === "connecting" && "text-yellow-400",
                          skill.status === "error" && "text-red-400",
                          skill.status === "disabled" && "text-text-muted"
                        )}
                      >
                        {formatStatus(skill.status)}
                        {skill.status_message && skill.status !== "disabled" && (
                          <span className="text-text-muted ml-1">
                            - {skill.status_message}
                          </span>
                        )}
                      </span>
                    </div>
                  </div>
                  <Toggle
                    enabled={skill.enabled}
                    onChange={(enabled) => handleToggle(skill.id, enabled)}
                    disabled={updatingSkill === skill.id}
                  />
                </div>
              </div>
            ))
          )}

          {lastReload && (
            <div className="text-xs text-text-muted text-right pt-2">
              Last checked:{" "}
              {new Date(lastReload).toLocaleTimeString(undefined, {
                hour: "2-digit",
                minute: "2-digit",
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
