"use client";

import { useRef, useState } from "react";
import { Plus, Trash2, MessageSquare, PanelLeftClose, PanelLeft, X, ChevronRight, Bot, Mic, Search, Loader2, GitBranch } from "lucide-react";
import { cn } from "@/lib/utils";
import { useChat } from "@/lib/ChatContext";
import { Conversation } from "@/lib/api";

interface ChatSidebarProps {
  className?: string;
}

function formatRelativeTime(dateString: string): string {
  const normalized = dateString.endsWith('Z') || dateString.includes('+') ? dateString : dateString + 'Z';
  const date = new Date(normalized);
  const now = new Date();

  // Compare calendar dates in local time to avoid timezone edge cases
  const dateLocal = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const nowLocal = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const diffDays = Math.round((nowLocal.getTime() - dateLocal.getTime()) / (1000 * 60 * 60 * 24));

  if (diffDays <= 0) {
    return "Today";
  } else if (diffDays === 1) {
    return "Yesterday";
  } else if (diffDays < 7) {
    return `${diffDays} days ago`;
  } else if (diffDays < 30) {
    const weeks = Math.floor(diffDays / 7);
    return `${weeks} week${weeks > 1 ? "s" : ""} ago`;
  } else {
    const months = Math.floor(diffDays / 30);
    return `${months} month${months > 1 ? "s" : ""} ago`;
  }
}

function groupConversationsByTime(conversations: Conversation[]): Record<string, Conversation[]> {
  const groups: Record<string, Conversation[]> = {};

  conversations.forEach((conv) => {
    const timeGroup = formatRelativeTime(conv.updated_at);
    if (!groups[timeGroup]) {
      groups[timeGroup] = [];
    }
    groups[timeGroup].push(conv);
  });

  return groups;
}

function ConversationItem({
  conversation,
  isActive,
  onSelect,
  onDelete,
}: {
  conversation: Conversation;
  isActive: boolean;
  onSelect: () => void;
  onDelete: () => void;
}) {
  // Icon priority: Mic (voice) > Worker (orchestrator) > Bot (scheduled) > MessageSquare (default)
  const getIcon = () => {
    if (conversation.channel === "voice") {
      return <Mic className="w-4 h-4 flex-shrink-0 text-blue-400" />;
    }
    if (conversation.source === "orchestrator_worker") {
      return <GitBranch className="w-4 h-4 flex-shrink-0 text-purple-400" />;
    }
    if (conversation.source === "scheduled_event" || conversation.source === "heartbeat") {
      return <Bot className="w-4 h-4 flex-shrink-0 text-terminal/70" />;
    }
    return <MessageSquare className="w-4 h-4 flex-shrink-0" />;
  };

  return (
    <div
      className={cn(
        "group flex items-center gap-2 px-3 py-2 rounded-md cursor-pointer transition-colors",
        isActive
          ? "bg-terminal/20 text-text-primary"
          : "text-text-secondary hover:bg-surface hover:text-text-primary"
      )}
      onClick={onSelect}
    >
      {getIcon()}
      <span className="flex-1 truncate text-sm">{conversation.title}</span>
      <button
        onClick={(e) => {
          e.stopPropagation();
          onDelete();
        }}
        className="opacity-0 group-hover:opacity-100 p-1 hover:bg-red-500/20 rounded transition-opacity"
        title="Delete conversation"
      >
        <Trash2 className="w-3.5 h-3.5 text-red-400" />
      </button>
    </div>
  );
}

function SearchInput({
  value,
  onChange,
  onClear,
}: {
  value: string;
  onChange: (value: string) => void;
  onClear: () => void;
}) {
  return (
    <div className="relative px-3 py-2">
      <Search className="absolute left-5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-text-muted pointer-events-none" />
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Search conversations..."
        className="w-full pl-7 pr-7 py-1.5 text-sm bg-surface border border-border rounded-md text-text-primary placeholder:text-text-muted focus:outline-none focus:border-terminal/50 transition-colors"
      />
      {value && (
        <button
          onClick={onClear}
          className="absolute right-5 top-1/2 -translate-y-1/2 p-0.5 rounded hover:bg-surface transition-colors"
        >
          <X className="w-3.5 h-3.5 text-text-muted" />
        </button>
      )}
    </div>
  );
}

function SearchResultsList({
  results,
  conversationId,
  onSelect,
  onDelete,
}: {
  results: Conversation[];
  conversationId: string | undefined;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  if (results.length === 0) {
    return (
      <div className="text-center text-text-muted text-sm py-8">
        No results found
      </div>
    );
  }

  return (
    <div className="space-y-1">
      {results.map((conv) => (
        <ConversationItem
          key={conv.id}
          conversation={conv}
          isActive={conv.id === conversationId}
          onSelect={() => onSelect(conv.id)}
          onDelete={() => onDelete(conv.id)}
        />
      ))}
    </div>
  );
}

function ConversationList({
  groupedConversations,
  conversationId,
  onSelect,
  onDelete,
  hasMore,
  isLoadingMore,
  onLoadMore,
}: {
  groupedConversations: Record<string, Conversation[]>;
  conversationId: string | undefined;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  hasMore: boolean;
  isLoadingMore: boolean;
  onLoadMore: () => void;
}) {
  const [olderExpanded, setOlderExpanded] = useState(false);

  const entries = Object.entries(groupedConversations);
  const todayConvs = groupedConversations["Today"] || [];
  const olderEntries = entries.filter(([group]) => group !== "Today");
  const olderCount = olderEntries.reduce((sum, [, convs]) => sum + convs.length, 0);

  return (
    <>
      {/* Today group — always expanded */}
      {todayConvs.length > 0 && (
        <div className="mb-4">
          <h3 className="text-xs font-medium text-text-muted px-3 py-1 uppercase tracking-wider">
            Today
          </h3>
          <div className="space-y-1">
            {todayConvs.map((conv) => (
              <ConversationItem
                key={conv.id}
                conversation={conv}
                isActive={conv.id === conversationId}
                onSelect={() => onSelect(conv.id)}
                onDelete={() => onDelete(conv.id)}
              />
            ))}
          </div>
        </div>
      )}

      {/* Older groups — collapsible */}
      {olderCount > 0 && (
        <div className="mb-4">
          <button
            onClick={() => setOlderExpanded((prev) => !prev)}
            className="flex items-center gap-1 w-full px-3 py-1 text-xs font-medium text-text-muted uppercase tracking-wider hover:text-text-secondary transition-colors"
          >
            <ChevronRight
              className={cn(
                "w-3 h-3 transition-transform",
                olderExpanded && "rotate-90"
              )}
            />
            <span>Older ({olderCount})</span>
          </button>
          {olderExpanded &&
            olderEntries.map(([timeGroup, convs]) => (
              <div key={timeGroup} className="mt-2">
                <h3 className="text-xs font-medium text-text-muted px-3 py-1 uppercase tracking-wider">
                  {timeGroup}
                </h3>
                <div className="space-y-1">
                  {convs.map((conv) => (
                    <ConversationItem
                      key={conv.id}
                      conversation={conv}
                      isActive={conv.id === conversationId}
                      onSelect={() => onSelect(conv.id)}
                      onDelete={() => onDelete(conv.id)}
                    />
                  ))}
                </div>
              </div>
            ))}
        </div>
      )}

      {todayConvs.length === 0 && olderCount === 0 && (
        <div className="text-center text-text-muted text-sm py-8">
          No conversations yet
        </div>
      )}

      {/* Load more button */}
      {hasMore && (
        <div className="px-3 py-2">
          <button
            onClick={onLoadMore}
            disabled={isLoadingMore}
            className="w-full py-2 text-xs font-medium text-text-muted hover:text-text-primary bg-surface hover:bg-surface/80 border border-border rounded-md transition-colors flex items-center justify-center gap-1.5 disabled:opacity-50"
          >
            {isLoadingMore ? (
              <>
                <Loader2 className="w-3 h-3 animate-spin" />
                Loading...
              </>
            ) : (
              "Load more"
            )}
          </button>
        </div>
      )}
    </>
  );
}

const SOURCE_FILTERS = [
  { value: "all", label: "All" },
  { value: "user", label: "User" },
  { value: "heartbeat", label: "Heartbeat" },
  { value: "scheduled_event", label: "Scheduled" },
  { value: "external_message", label: "Inbound" },
  { value: "orchestrator_worker", label: "Workers" },
] as const;

function SourceFilterBar({
  sourceFilter,
  setSourceFilter,
}: {
  sourceFilter: string;
  setSourceFilter: (filter: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5 px-3 py-2 border-b border-border">
      {SOURCE_FILTERS.map((f) => (
        <button
          key={f.value}
          onClick={() => setSourceFilter(f.value)}
          className={cn(
            "px-2 py-0.5 rounded-full text-xs font-medium transition-colors",
            sourceFilter === f.value
              ? "bg-terminal/20 text-terminal"
              : "text-text-muted hover:text-text-primary hover:bg-surface"
          )}
        >
          {f.label}
        </button>
      ))}
    </div>
  );
}

export function ChatSidebar({ className }: ChatSidebarProps) {
  const {
    conversations,
    conversationId,
    sidebarCollapsed,
    sourceFilter,
    searchQuery,
    setSearchQuery,
    searchResults,
    hasMore,
    isLoadingMore,
    loadMoreConversations,
    clearSearch,
    startNewChat,
    loadConversation,
    deleteConversation,
    toggleSidebar,
    setSourceFilter,
  } = useChat();

  const sidebarRef = useRef<HTMLDivElement>(null);

  // Filter conversations by source type, then group by time
  const filteredConversations = sourceFilter === "all"
    ? conversations
    : conversations.filter((c) => c.source === sourceFilter);
  const groupedConversations = groupConversationsByTime(filteredConversations);

  const isSearching = searchResults !== null;

  // Desktop sidebar
  const desktopSidebar = (
    <aside
      ref={sidebarRef}
      className={cn(
        "hidden md:flex flex-col bg-primary-bg border-r border-border transition-all duration-200",
        sidebarCollapsed ? "w-0 overflow-hidden" : "w-72",
        className
      )}
    >
      <div className="flex items-center justify-between p-3 border-b border-border gap-2">
        <button
          onClick={startNewChat}
          className="flex items-center gap-2 px-3 py-2 rounded-md bg-terminal text-white hover:opacity-80 transition-opacity flex-1 justify-center"
        >
          <Plus className="w-4 h-4" />
          <span className="text-sm font-medium">New Chat</span>
        </button>
        <button
          onClick={toggleSidebar}
          className="p-2 rounded-md hover:bg-surface transition-colors"
          title="Collapse sidebar"
        >
          <PanelLeftClose className="w-4 h-4 text-text-secondary" />
        </button>
      </div>

      <SearchInput value={searchQuery} onChange={setSearchQuery} onClear={clearSearch} />

      {!isSearching && (
        <SourceFilterBar sourceFilter={sourceFilter} setSourceFilter={setSourceFilter} />
      )}

      <div className="flex-1 overflow-y-auto p-2">
        {isSearching ? (
          <SearchResultsList
            results={searchResults}
            conversationId={conversationId}
            onSelect={(id) => loadConversation(id)}
            onDelete={(id) => deleteConversation(id)}
          />
        ) : (
          <ConversationList
            groupedConversations={groupedConversations}
            conversationId={conversationId}
            onSelect={(id) => loadConversation(id)}
            onDelete={(id) => deleteConversation(id)}
            hasMore={hasMore}
            isLoadingMore={isLoadingMore}
            onLoadMore={loadMoreConversations}
          />
        )}
      </div>

    </aside>
  );

  // Collapsed button (shows when sidebar is collapsed on desktop)
  const collapsedButton = sidebarCollapsed && (
    <button
      onClick={toggleSidebar}
      className="hidden md:flex fixed left-4 top-[4.5rem] z-30 p-2 rounded-md bg-surface border border-border hover:bg-surface/80 transition-colors"
      title="Expand sidebar"
    >
      <PanelLeft className="w-4 h-4 text-text-secondary" />
    </button>
  );

  return (
    <>
      {desktopSidebar}
      {collapsedButton}
    </>
  );
}

// Mobile drawer component (rendered separately in layout)
export function MobileChatDrawer({
  isOpen,
  onClose,
}: {
  isOpen: boolean;
  onClose: () => void;
}) {
  const {
    conversations,
    conversationId,
    sourceFilter,
    searchQuery,
    setSearchQuery,
    searchResults,
    hasMore,
    isLoadingMore,
    loadMoreConversations,
    clearSearch,
    startNewChat,
    loadConversation,
    deleteConversation,
    setSourceFilter,
  } = useChat();

  const filteredConversations = sourceFilter === "all"
    ? conversations
    : conversations.filter((c) => c.source === sourceFilter);
  const groupedConversations = groupConversationsByTime(filteredConversations);

  const isSearching = searchResults !== null;

  if (!isOpen) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="md:hidden fixed inset-0 bg-black/50 z-40"
        onClick={onClose}
      />

      {/* Drawer */}
      <aside className="md:hidden fixed inset-y-0 left-0 w-72 bg-primary-bg border-r border-border z-50 flex flex-col">
        <div className="flex items-center justify-between p-3 border-b border-border gap-2">
          <button
            onClick={() => {
              startNewChat();
              onClose();
            }}
            className="flex items-center gap-2 px-3 py-2 rounded-md bg-terminal text-white hover:opacity-80 transition-opacity flex-1 justify-center"
          >
            <Plus className="w-4 h-4" />
            <span className="text-sm font-medium">New Chat</span>
          </button>
          <button
            onClick={onClose}
            className="p-2.5 rounded-md hover:bg-surface transition-colors min-w-[44px] min-h-[44px] flex items-center justify-center"
            title="Close"
          >
            <X className="w-4 h-4 text-text-secondary" />
          </button>
        </div>

        <SearchInput value={searchQuery} onChange={setSearchQuery} onClear={clearSearch} />

        {!isSearching && (
          <SourceFilterBar sourceFilter={sourceFilter} setSourceFilter={setSourceFilter} />
        )}

        <div className="flex-1 overflow-y-auto p-2">
          {isSearching ? (
            <SearchResultsList
              results={searchResults}
              conversationId={conversationId}
              onSelect={(id) => {
                loadConversation(id);
                onClose();
              }}
              onDelete={(id) => deleteConversation(id)}
            />
          ) : (
            <ConversationList
              groupedConversations={groupedConversations}
              conversationId={conversationId}
              onSelect={(id) => {
                loadConversation(id);
                onClose();
              }}
              onDelete={(id) => deleteConversation(id)}
              hasMore={hasMore}
              isLoadingMore={isLoadingMore}
              onLoadMore={loadMoreConversations}
            />
          )}
        </div>
      </aside>
    </>
  );
}
