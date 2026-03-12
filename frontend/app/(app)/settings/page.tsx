"use client";

import { useState, useEffect, useCallback } from "react";
import {
  Settings2,
  Zap,
  Puzzle,
  HeartPulse,
  Brain,
  FileText,
  Calendar,
  Database,
  FolderOpen,
  ArrowLeft,
  Dna,
  GitBranch,
  LucideIcon,
} from "lucide-react";
import { GeneralPanel } from "@/components/settings/GeneralPanel";
import { MemoryBrowser } from "@/components/settings/MemoryBrowser";
import { DocumentBrowser } from "@/components/settings/DocumentBrowser";
import { SkillsPanel } from "@/components/settings/SkillsPanel";
import { CustomMCPPanel } from "@/components/settings/CustomMCPPanel";
import { EventsBrowser } from "@/components/settings/EventsBrowser";
import { HeartbeatPanel } from "@/components/settings/HeartbeatPanel";
import { DatabaseBrowser } from "@/components/settings/DatabaseBrowser";
import { FileBrowser } from "@/components/settings/FileBrowser";
import { EvolutionPanel } from "@/components/settings/EvolutionPanel";
import { OrchestratorPanel } from "@/components/settings/OrchestratorPanel";

interface TileConfig {
  id: string;
  title: string;
  description: string;
  icon: LucideIcon;
}

const TILES: TileConfig[] = [
  { id: "general", title: "General", description: "Model, temperature, and providers", icon: Settings2 },
  { id: "skills", title: "Skills", description: "Enable and disable integrations", icon: Zap },
  { id: "mcp", title: "Edward's Servers", description: "Manage custom MCP servers", icon: Puzzle },
  { id: "heartbeat", title: "Heartbeat", description: "Monitor iMessage listener", icon: HeartPulse },
  { id: "memories", title: "Memories", description: "Search long-term memory", icon: Brain },
  { id: "documents", title: "Documents", description: "Store and search documents", icon: FileText },
  { id: "events", title: "Events", description: "Scheduled reminders and tasks", icon: Calendar },
  { id: "databases", title: "Databases", description: "Inspect conversation databases", icon: Database },
  { id: "files", title: "Files", description: "Browse stored files", icon: FolderOpen },
  { id: "evolution", title: "Evolution", description: "Self-coding and auto-deployment", icon: Dna },
  { id: "orchestrator", title: "Orchestrator", description: "Manage parallel worker agents", icon: GitBranch },
];

function renderPanel(id: string) {
  switch (id) {
    case "general": return <GeneralPanel isExpanded hideHeader />;
    case "skills": return <SkillsPanel isExpanded hideHeader />;
    case "mcp": return <CustomMCPPanel isExpanded hideHeader />;
    case "heartbeat": return <HeartbeatPanel isExpanded hideHeader />;
    case "memories": return <MemoryBrowser isExpanded hideHeader />;
    case "documents": return <DocumentBrowser isExpanded hideHeader />;
    case "events": return <EventsBrowser isExpanded hideHeader />;
    case "databases": return <DatabaseBrowser isExpanded hideHeader />;
    case "files": return <FileBrowser isExpanded hideHeader />;
    case "evolution": return <EvolutionPanel isExpanded hideHeader />;
    case "orchestrator": return <OrchestratorPanel isExpanded hideHeader />;
    default: return null;
  }
}

export default function SettingsPage() {
  const [activePanel, setActivePanel] = useState<string | null>(null);

  // Read hash on mount
  useEffect(() => {
    const hash = window.location.hash.slice(1);
    if (hash && TILES.some((t) => t.id === hash)) {
      setActivePanel(hash);
    }
  }, []);

  // Listen for browser back/forward
  useEffect(() => {
    const onPopState = () => {
      const hash = window.location.hash.slice(1);
      if (hash && TILES.some((t) => t.id === hash)) {
        setActivePanel(hash);
      } else {
        setActivePanel(null);
      }
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const openPanel = useCallback((id: string) => {
    setActivePanel(id);
    window.history.pushState(null, "", `#${id}`);
    window.scrollTo(0, 0);
  }, []);

  const goBack = useCallback(() => {
    setActivePanel(null);
    window.history.pushState(null, "", window.location.pathname);
  }, []);

  const activeTile = activePanel ? TILES.find((t) => t.id === activePanel) : null;

  return (
    <div className="container mx-auto px-4 py-8 bg-primary-bg min-h-full overflow-y-auto">
      <div className={activePanel ? "max-w-2xl mx-auto" : "max-w-4xl mx-auto"}>
        {activePanel && activeTile ? (
          <>
            <button
              onClick={goBack}
              className="flex items-center gap-2 text-text-muted hover:text-text-primary transition-colors mb-4"
            >
              <ArrowLeft className="w-4 h-4" />
              <span className="text-sm font-medium">Settings</span>
            </button>
            <h1 className="text-2xl font-bold mb-6 font-mono text-text-primary">
              {activeTile.title}
            </h1>
            {renderPanel(activePanel)}
          </>
        ) : (
          <>
            <h1 className="text-2xl font-bold mb-6 font-mono text-text-primary">Settings</h1>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
              {TILES.map((tile) => {
                const Icon = tile.icon;
                return (
                  <button
                    key={tile.id}
                    onClick={() => openPanel(tile.id)}
                    className="flex flex-col items-center gap-3 p-5 rounded-lg border border-input-border bg-surface hover:border-terminal/50 transition-colors text-center group"
                  >
                    <div className="w-10 h-10 rounded-full bg-terminal/10 flex items-center justify-center group-hover:bg-terminal/20 transition-colors">
                      <Icon className="w-5 h-5 text-terminal" />
                    </div>
                    <div>
                      <div className="font-medium text-sm text-text-primary">{tile.title}</div>
                      <div className="text-xs text-text-muted mt-1">{tile.description}</div>
                    </div>
                  </button>
                );
              })}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
