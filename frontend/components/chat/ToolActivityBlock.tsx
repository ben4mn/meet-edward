"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import {
  Search, Globe, Brain, Send, Clock, FileText,
  Contact, Wrench, Bell, Server, Database, FolderOpen,
  Loader2, Check, X, ChevronRight, ChevronDown,
} from "lucide-react";
import type { ToolActivity } from "@/lib/api";

const TOOL_ICONS: Record<string, typeof Wrench> = {
  web_search: Search,
  fetch_page_content: Globe,
  browse_web: Globe,
  browser_click: Globe,
  browser_type: Globe,
  browser_extract: Globe,
  remember_update: Brain,
  remember_forget: Brain,
  remember_search: Brain,
  send_message: Send,
  send_sms: Send,
  send_whatsapp: Send,
  send_imessage: Send,
  schedule_event: Clock,
  list_scheduled_events: Clock,
  cancel_scheduled_event: Clock,
  save_document: FileText,
  read_document: FileText,
  edit_document: FileText,
  search_documents: FileText,
  list_documents: FileText,
  delete_document: FileText,
  lookup_contact: Contact,
  lookup_phone: Contact,
  send_push_notification: Bell,
  search_mcp_servers: Server,
  add_mcp_server: Server,
  list_custom_servers: Server,
  remove_mcp_server: Server,
  create_persistent_db: Database,
  query_persistent_db: Database,
  list_persistent_dbs: Database,
  delete_persistent_db: Database,
  save_to_storage: FolderOpen,
  list_storage_files: FolderOpen,
  get_storage_file_url: FolderOpen,
  read_storage_file: FolderOpen,
  update_widget: Wrench,
  get_widget_state_tool: Wrench,
};

function getToolIcon(toolName: string) {
  if (TOOL_ICONS[toolName]) return TOOL_ICONS[toolName];
  for (const [prefix, icon] of Object.entries(TOOL_ICONS)) {
    if (toolName.startsWith(prefix.split("_")[0])) return icon;
  }
  return Wrench;
}

function formatToolName(name: string): string {
  return name.replace(/_/g, " ");
}

function isElegantResult(result: string | undefined): boolean {
  if (!result) return false;
  const lines = result.split("\n").length;
  return lines <= 3 && result.length <= 200;
}

function ActivityRow({ activity }: { activity: ToolActivity }) {
  const [expanded, setExpanded] = useState(() => isElegantResult(activity.result));
  const Icon = getToolIcon(activity.tool_name);
  const isRunning = activity.status === "running";
  const isError = activity.status === "error";
  const hasResult = !!activity.result;

  return (
    <div className="py-1.5 first:pt-0 last:pb-0">
      <div
        className={cn(
          "flex items-center gap-2 text-xs",
          hasResult && !isRunning && "cursor-pointer",
        )}
        onClick={() => hasResult && !isRunning && setExpanded(!expanded)}
      >
        {isRunning && <Loader2 className="w-3 h-3 animate-spin text-terminal flex-shrink-0" />}
        {activity.status === "completed" && <Check className="w-3 h-3 text-text-muted flex-shrink-0" />}
        {isError && <X className="w-3 h-3 text-red-400 flex-shrink-0" />}

        <Icon className={cn(
          "w-3 h-3 flex-shrink-0",
          isRunning ? "text-terminal" : isError ? "text-red-400" : "text-text-muted",
        )} />
        <span className={cn(
          "font-medium",
          isRunning ? "text-text-secondary" : isError ? "text-red-400" : "text-text-muted",
        )}>
          {formatToolName(activity.tool_name)}
        </span>

        {activity.args_summary && (
          <span className="text-text-muted truncate">{activity.args_summary}</span>
        )}

        {hasResult && !isRunning && (
          <span className="ml-auto flex-shrink-0 text-text-muted">
            {expanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
          </span>
        )}
      </div>

      {expanded && activity.result && (
        <div className={cn(
          "mt-1 ml-5 text-xs font-mono whitespace-pre-wrap break-words max-h-32 overflow-y-auto",
          isError ? "text-red-400/80" : "text-text-muted",
        )}>
          {activity.result}
        </div>
      )}
    </div>
  );
}

interface ToolActivityBlockProps {
  activities: ToolActivity[];
}

export function ToolActivityBlock({ activities }: ToolActivityBlockProps) {
  if (activities.length === 0) return null;

  return (
    <div className="mb-2 rounded-lg border border-border bg-surface-elevated/50 px-3 py-2 divide-y divide-border/50">
      {activities.map((activity) => (
        <ActivityRow key={activity.id} activity={activity} />
      ))}
    </div>
  );
}
