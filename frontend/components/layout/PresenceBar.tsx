"use client";

// The phone's presence bar (docs/design-system-2026-09-25.md §1, §5):
// "Edward is the status bar". A slim translucent bar — 48px plus the
// status-bar inset when the page runs under it — with the live Edward bust,
// his name and his one honest line (from lib/presence.tsx). Tapping him
// opens the presence sheet. The right holds one contextual slot: on chat the
// history drawer and a new chat, elsewhere search (the command palette).
//
// While a page's hero Edward is on screen (lib/presenceHero.tsx) the bar's
// Edward fades out and the hero is the live one; a screen-reader copy of his
// status stays so the line is still announced once. That live region says
// his mood, never a running-tool count (one turn read out 26 phrases), and
// on chat it holds still through a live turn — the chat view announces the
// turn itself.

import React, { useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { History, Search, SquarePen } from "lucide-react";
import { EdwardAvatar } from "@/components/EdwardAvatar";
import { useChatList } from "@/lib/ChatContext";
import { useComposing } from "@/lib/useComposing";
import { shouldHideChrome } from "@/lib/chrome";
import { useEdwardPresence, useFreshBeat } from "@/lib/presence";
import { presenceAnnouncement } from "@/lib/presenceModel";
import { useHeroOnScreen, usePresenceSheet } from "@/lib/presenceHero";
import { useIsDesktop } from "@/components/ui/motion";
import { cn } from "@/lib/utils";
import { barActions } from "./navItems";
import { paletteStore } from "./shellStores";
import { lazyComponent } from "@/lib/lazyComponent";

// The conversations drawer (only ever opened on /chat) lives with the chat
// sidebar in its own chunk (2026-09-26 perf pass; ClientLayout starts the
// fetch). It mounts closed the moment the chunk lands; the open state is
// this bar's, so a tap before then opens it when it does.
const MobileChatDrawer = lazyComponent(() =>
  import("@/components/chat/ChatSidebar").then((mod) => mod.MobileChatDrawer),
);

/** Moods that are "doing something": his line reads in his colour then. */
const ACTIVE_MOODS = new Set(["think", "work", "listen", "speak"]);

const ICON_BUTTON =
  "inline-flex h-11 w-11 flex-none items-center justify-center rounded-full text-text-secondary " +
  "transition-[background-color,color,transform] duration-press ease-out hover:bg-surface-3 hover:text-text-primary " +
  "active:scale-[.92] active:bg-surface-3 [-webkit-tap-highlight-color:transparent]";

export function PresenceBar() {
  const pathname = usePathname() ?? "/today";
  const router = useRouter();
  const { startNewChat } = useChatList();
  const composing = useComposing();
  const presence = useEdwardPresence();
  const heroOn = useHeroOnScreen();
  const sheet = usePresenceSheet();
  const isDesktop = useIsDesktop();
  const [drawerOpen, setDrawerOpen] = useState(false);

  const actions = barActions(pathname);
  const folded = shouldHideChrome(pathname, composing);
  const showEdward = !heroOn;
  // One live Edward: not on desktop (the header has him), not behind the
  // sheet (it has its own), not while a hero is on screen.
  const live = showEdward && !isDesktop && !sheet.isOpen && !folded;
  const busy = ACTIVE_MOODS.has(presence.mood);
  // Above the folded early return: this component stays mounted while the
  // avatar below unmounts, so an old beat is never replayed on unfold.
  const barBeat = useFreshBeat(presence.beat, live);
  // The polite live region: moods, not tool counts; held through a chat turn on chat.
  const spoken = presenceAnnouncement(presence.signals);
  const heldLine = useRef(spoken);
  const chatTurn = pathname === "/chat" && presence.signals.turn != null;
  if (!chatTurn) heldLine.current = spoken;

  const drawer = actions.includes("history") ? (
    <MobileChatDrawer isOpen={drawerOpen} onClose={() => setDrawerOpen(false)} />
  ) : null;

  if (folded) return drawer;

  return (
    <>
      <header
        data-edge={showEdward ? undefined : ""}
        className={cn(
          "md:hidden relative z-30 flex-none border-b pt-[env(safe-area-inset-top)]",
          "transition-[background-color,border-color] duration-base ease-out",
          // At the scroll edge (a hero on screen) the bar is just canvas, as
          // on iOS; the material and the hairline come in with Edward.
          showEdward
            ? "border-border bg-primary-bg/80 backdrop-blur-xl backdrop-saturate-150"
            : "border-transparent bg-primary-bg",
        )}
      >
        <span className="sr-only" aria-live="polite">
          {heldLine.current}
        </span>
        <div className="flex h-12 items-center gap-1 pl-2 pr-1.5">
          <button
            type="button"
            data-presence-cluster=""
            aria-hidden={showEdward ? undefined : true}
            tabIndex={showEdward ? undefined : -1}
            onClick={sheet.open}
            aria-label={`${presence.label}. What he's doing`}
            className={cn(
              "flex min-h-[44px] min-w-0 flex-1 items-center gap-2.5 rounded-md px-1.5 text-left",
              "transition-[opacity,transform] duration-base ease-out active:scale-[.98] [-webkit-tap-highlight-color:transparent]",
              "motion-reduce:translate-y-0 motion-reduce:transition-opacity motion-reduce:duration-[120ms] motion-reduce:active:scale-100",
              showEdward ? "opacity-100" : "pointer-events-none translate-y-0.5 opacity-0",
            )}
          >
            <EdwardAvatar
              size={30}
              variant="bust"
              live={live}
              animated={live}
              mood={presence.mood}
              beat={barBeat}
              beatKey={presence.beat?.key}
              className="flex-none"
            />
            <span className="flex min-w-0 flex-col">
              <span className="text-[15px] font-semibold leading-[18px] tracking-[-.01em] text-text-primary">Edward</span>
              <span
                className={cn(
                  "truncate text-caption leading-4",
                  busy ? "text-terminal-strong" : "text-text-muted",
                )}
              >
                {presence.status}
              </span>
            </span>
          </button>

          {actions.includes("history") ? (
            <button type="button" aria-label="Chat history" onClick={() => setDrawerOpen(true)} className={ICON_BUTTON}>
              <History className="h-[21px] w-[21px]" aria-hidden />
            </button>
          ) : null}
          {actions.includes("new-chat") ? (
            <button
              type="button"
              aria-label="New chat"
              onClick={() => {
                startNewChat();
                router.push("/chat");
              }}
              className={ICON_BUTTON}
            >
              <SquarePen className="h-5 w-5" aria-hidden />
            </button>
          ) : null}
          {actions.includes("search") ? (
            <button type="button" aria-label="Search" onClick={paletteStore.open} className={ICON_BUTTON}>
              <Search className="h-5 w-5" aria-hidden />
            </button>
          ) : null}
        </div>
      </header>
      {drawer}
    </>
  );
}
