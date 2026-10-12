"use client";

// The command palette (⌘K / Ctrl+K on desktop, the search button in the
// phone's presence bar). A bottom sheet on the phone, a dialog near the top
// of the screen on md+ (the Sheet primitive). One list, grouped and ranked
// by commandPaletteModel.ts: recent conversations and actions when nothing
// is typed; conversations (server search), settings and actions
// once the owner types. ↑↓ move, ↵ opens, Esc closes.

import React, { useEffect, useId, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AudioLines,
  ChevronRight,
  CornerDownLeft,
  LogOut,
  MessageSquare,
  Monitor,
  Moon,
  Search,
  Settings2,
  SquarePen,
  Sun,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Sheet, useToast } from "@/components/ui";
import { useIsDesktop } from "@/components/ui/motion";
import { getConversations, wakeMind, type Conversation } from "@/lib/api";
import { useChat } from "@/lib/ChatContext";
import { useEdwardPresence } from "@/lib/presence";
import { isMacPlatform, shortcutLabel } from "@/lib/hotkeys";
import { useThemeMode } from "@/lib/themeMode";
import { formatRelative } from "@/lib/time";
import { cn } from "@/lib/utils";
import { ROOMS } from "@/components/settings/settingsRooms";
import { buildPalette, moveActive, type PaletteItem } from "./commandPaletteModel";
import { openEdwardScreen, paletteStore, signOutStore, usePaletteOpen } from "./shellStores";

const SEARCH_DEBOUNCE_MS = 180;

interface SettingEntry {
  slug: string;
  title: string;
  description: string;
  icon: LucideIcon;
}

function convItem(c: Conversation, keywords?: string[]): PaletteItem {
  return {
    id: `c:${c.id}`,
    group: "conversations",
    label: c.title || "Untitled",
    detail: c.updated_at ? formatRelative(c.updated_at) : undefined,
    href: `/chat?c=${encodeURIComponent(c.id)}`,
    keywords,
  };
}

export function CommandPalette() {
  const open = usePaletteOpen();
  const inputRef = useRef<HTMLInputElement>(null);
  const desktop = useIsDesktop();
  return (
    <Sheet
      open={open}
      onClose={paletteStore.close}
      ariaLabel="Search Edward"
      hideClose
      initialFocus={inputRef as React.RefObject<HTMLElement>}
      className={cn("h-[82dvh] md:h-auto md:max-h-[70dvh] md:max-w-xl md:self-start md:mt-[10vh]")}
      // The search row and the hint footer are sticky inside the scroller:
      // no body padding at those edges, or scrolled rows show through it.
      bodyClassName="pt-0 md:pb-0"
    >
      {open ? <PaletteBody inputRef={inputRef} desktop={desktop} /> : null}
    </Sheet>
  );
}

function PaletteBody({ inputRef, desktop }: { inputRef: React.RefObject<HTMLInputElement>; desktop: boolean }) {
  const router = useRouter();
  const chat = useChat();
  const presence = useEdwardPresence();
  const { theme, setMode } = useThemeMode();
  const { toast } = useToast();
  const listId = useId();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  // On touch a pre-highlighted row reads as a selection: show the active row
  // only on desktop or once the arrow keys are in use.
  const [keyed, setKeyed] = useState(false);
  const [hits, setHits] = useState<Conversation[] | null>(null);
  const [settings, setSettings] = useState<SettingEntry[]>([]);
  const mac = typeof navigator !== "undefined" && isMacPlatform(navigator.platform);

  // The settings index, once per opening.
  useEffect(() => {
    let alive = true;
    import("@/components/settings/settingsConfig")
      .then((mod) => {
        if (!alive) return;
        setSettings(
          mod.SETTINGS_ITEMS.map((i) => ({ slug: i.slug, title: i.title, description: i.description, icon: i.icon })),
        );
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  // Server search for conversations (titles, content, tags), debounced.
  const q = query.trim();
  useEffect(() => {
    if (!q) {
      setHits(null);
      return;
    }
    let alive = true;
    const t = setTimeout(() => {
      getConversations(8, 0, false, q)
        .then((r) => alive && setHits(r.conversations ?? []))
        .catch(() => alive && setHits(null));
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [q]);

  const icons = useRef(new Map<string, LucideIcon>());

  const actions: PaletteItem[] = useMemo(() => {
    const nextTheme = theme === "dark" ? "light" : "dark";
    const list: (PaletteItem & { icon: LucideIcon; hint?: string })[] = [
      {
        id: "a:new",
        group: "actions",
        label: "New chat",
        keywords: ["start", "fresh", "compose"],
        icon: SquarePen,
        hint: shortcutLabel("new-chat", mac),
        run: () => {
          chat.startNewChat();
          router.push("/chat");
        },
      },
      {
        id: "a:voice",
        group: "actions",
        label: "Talk live",
        detail: chat.voiceEnabled ? undefined : "Voice is off",
        keywords: ["voice", "speak", "call"],
        icon: AudioLines,
        disabled: !chat.voiceEnabled,
        run: () => {
          router.push("/chat");
          setTimeout(() => chat.enterVoiceMode(), 60);
        },
      },
      {
        id: "a:wake",
        group: "actions",
        label: "Wake Edward",
        keywords: ["mind", "look", "think", "wake"],
        icon: Zap,
        run: () => {
          wakeMind("manual")
            .then(() => {
              presence.refresh();
              toast({ title: "I'm up — having a look.", tone: "edward" });
            })
            .catch(() => toast({ title: "I couldn't wake up", body: "The Mac didn't answer.", tone: "bad" }));
        },
      },
      {
        id: "a:theme",
        group: "actions",
        label: nextTheme === "light" ? "Switch to light mode" : "Switch to dark mode",
        keywords: ["theme", "appearance", "dark", "light"],
        icon: nextTheme === "light" ? Sun : Moon,
        run: () => setMode(nextTheme),
      },
      {
        id: "a:screen",
        group: "actions",
        label: "Open Edward's screen",
        keywords: ["display", "mac", "monitor", "screen"],
        icon: Monitor,
        run: openEdwardScreen,
      },
      {
        id: "a:signout",
        group: "actions",
        label: "Sign out",
        keywords: ["log out", "logout", "leave"],
        icon: LogOut,
        run: () => setTimeout(signOutStore.open, 180),
      },
    ];
    for (const a of list) icons.current.set(a.id, a.icon);
    return list;
  }, [theme, mac, chat, router, presence, toast, setMode]);

  const convItems = useMemo(() => {
    if (q && hits) return hits.map((c) => convItem(c, [q]));
    return chat.conversations.map((c) => convItem(c));
  }, [q, hits, chat.conversations]);

  const settingItems = useMemo(() => {
    const rooms = ROOMS.map<PaletteItem>((r) => ({
      id: `r:${r.id}`,
      group: "settings",
      label: r.label,
      detail: r.question,
      href: `/settings/room/${r.id}`,
    }));
    const panels = settings.map<PaletteItem>((s) => {
      icons.current.set(`s:${s.slug}`, s.icon);
      return { id: `s:${s.slug}`, group: "settings", label: s.title, detail: s.description, href: `/settings/${s.slug}` };
    });
    return [...rooms, ...panels];
  }, [settings]);

  const palette = useMemo(
    () => buildPalette({ query, actions, conversations: convItems, settings: settingItems }),
    [query, actions, convItems, settingItems],
  );
  const count = palette.flat.length;
  const activeIndex = Math.min(active, Math.max(0, count - 1));
  const activeItem = palette.flat[activeIndex];
  const optionId = (i: number) => `${listId}-opt-${i}`;

  useEffect(() => setActive(0), [query]);
  useEffect(() => {
    if (typeof document === "undefined") return;
    document.getElementById(optionId(activeIndex))?.scrollIntoView?.({ block: "nearest" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeIndex]);

  const choose = (item: PaletteItem | undefined) => {
    if (!item || item.disabled) return;
    paletteStore.close();
    if (item.run) item.run();
    else if (item.href) router.push(item.href);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.nativeEvent.isComposing) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setKeyed(true);
      setActive(moveActive(activeIndex, 1, count));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setKeyed(true);
      setActive(moveActive(activeIndex, -1, count));
    } else if (e.key === "Enter") {
      e.preventDefault();
      choose(activeItem);
    }
  };

  let index = -1;
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="sticky top-0 z-10 -mx-4 flex items-center gap-2 border-b border-border bg-surface-elevated px-4 pb-2 pt-1 md:pt-0">
        <label className="flex h-11 flex-1 items-center gap-2.5 rounded-md bg-surface-3/70 px-3">
          <Search className="h-[18px] w-[18px] flex-none text-text-muted" aria-hidden />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Search chats, settings…"
            aria-label="Search Edward"
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={activeItem ? optionId(activeIndex) : undefined}
            autoComplete="off"
            autoCorrect="off"
            spellCheck={false}
            enterKeyHint="go"
            className="min-w-0 flex-1 bg-transparent text-[16px] text-text-primary placeholder:text-text-muted focus:outline-none"
          />
        </label>
        {!desktop ? (
          <button
            type="button"
            onClick={paletteStore.close}
            className="-mr-2 inline-flex h-11 items-center rounded-md px-2 text-callout font-medium text-text-secondary active:opacity-60"
          >
            Cancel
          </button>
        ) : null}
      </div>

      <div id={listId} role="listbox" aria-label="Results" className="min-h-0 flex-1 pb-2 pt-2">
        {count === 0 ? (
          <p className="px-2 py-10 text-center text-footnote text-text-muted">
            {q ? `I couldn't find anything for “${q}”.` : "Nothing here yet."}
          </p>
        ) : (
          palette.groups.map((group) => (
            <div key={group.id} role="group" aria-label={group.label} className="pb-2">
              <div className="px-2 pb-1 pt-2 text-overline text-text-muted">{group.label}</div>
              {group.items.map((item) => {
                index += 1;
                const i = index;
                const on = i === activeIndex;
                const lit = on && (desktop || keyed);
                const Icon =
                  icons.current.get(item.id) ??
                  (item.group === "settings" ? Settings2 : MessageSquare);
                const hint = (actions.find((a) => a.id === item.id) as { hint?: string } | undefined)?.hint;
                return (
                  <div
                    key={item.id}
                    id={optionId(i)}
                    role="option"
                    aria-selected={on}
                    aria-disabled={item.disabled || undefined}
                    onMouseMove={() => desktop && i !== activeIndex && setActive(i)}
                    onClick={() => choose(item)}
                    className={cn(
                      "flex min-h-[48px] cursor-pointer items-center gap-3 rounded-md px-2 py-2 md:min-h-[42px]",
                      "transition-colors duration-fast [-webkit-tap-highlight-color:transparent] active:bg-surface-3",
                      lit && "bg-surface-3",
                      item.disabled && "cursor-default opacity-50",
                    )}
                  >
                    <span
                      className={cn(
                        "flex h-8 w-8 flex-none items-center justify-center rounded-sm",
                        item.group === "actions" ? "bg-surface-3 text-text-primary" : "bg-surface-3/60 text-text-secondary",
                      )}
                    >
                      <Icon className="h-4 w-4" aria-hidden />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-callout font-medium text-text-primary">{item.label}</span>
                      {item.detail ? (
                        <span className="block truncate text-footnote text-text-muted">{item.detail}</span>
                      ) : null}
                    </span>
                    {desktop && hint ? (
                      <kbd className="flex-none rounded-xs border border-border px-1.5 py-0.5 font-sans text-caption text-text-muted">
                        {hint}
                      </kbd>
                    ) : lit && desktop ? (
                      <CornerDownLeft className="h-4 w-4 flex-none text-text-muted" aria-hidden />
                    ) : item.href ? (
                      <ChevronRight className="h-4 w-4 flex-none text-text-muted md:hidden" aria-hidden />
                    ) : null}
                  </div>
                );
              })}
            </div>
          ))
        )}
      </div>

      {desktop ? (
        <div className="sticky bottom-0 -mx-4 flex items-center gap-4 border-t border-border bg-surface-elevated px-4 py-2.5 text-caption text-text-muted">
          <span>
            <kbd className="font-sans">↑↓</kbd> move
          </span>
          <span>
            <kbd className="font-sans">↵</kbd> open
          </span>
          <span>
            <kbd className="font-sans">esc</kbd> close
          </span>
          <span className="ml-auto">{shortcutLabel("palette", mac)} anywhere</span>
        </div>
      ) : null}
    </div>
  );
}
