"use client";

// The desktop header (md+; docs/design-system-2026-09-25.md §5): full
// bleed, on the chat sidebar's grid (the left column is the sidebar's 20rem).
//
//   [ live Edward · his line ]   [ Today · Chat · Edward ]   [ Search ⌘K · B ]
//
// Tapping Edward opens the presence sheet. While a page's hero Edward is the
// live one (Today, the chat empty state, the Edward tab) the header keeps his
// name and a still bust — only the status line (the hero says it) and the
// breath go; the left third is never blank. The tabs are a segmented control
// with a sliding pill (framer layoutId); Chat clicked while on chat starts a
// new chat (chatTabAction). The avatar menu holds appearance, Edward's
// screen and sign out — the only Sun/Moon on the page.

import React, { useRef } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { LazyMotion, m } from "framer-motion";
import { MOTION_FEATURES } from "@/components/ui/motionFeatures";
import { Search } from "lucide-react";
import { EdwardAvatar } from "@/components/EdwardAvatar";
import { useChatList } from "@/lib/ChatContext";
import { useEdwardPresence, useFreshBeat } from "@/lib/presence";
import { presenceAnnouncement } from "@/lib/presenceModel";
import { useHeroOnScreen, usePresenceSheet } from "@/lib/presenceHero";
import { isMacPlatform, shortcutLabel } from "@/lib/hotkeys";
import { REDUCED_FADE, SPRING_PILL, useIsDesktop, usePrefersReducedMotion } from "@/components/ui/motion";
import { cn } from "@/lib/utils";
import { AvatarMenu } from "./AvatarMenu";
import { NAV_ITEMS, chatTabAction, edwardTabBadge, isNavActive } from "./navItems";
import { paletteStore } from "./shellStores";

const ACTIVE_MOODS = new Set(["think", "work", "listen", "speak"]);

export function DesktopHeader() {
  const pathname = usePathname() ?? "/today";
  const router = useRouter();
  const { startNewChat } = useChatList();
  const presence = useEdwardPresence();
  const heroOn = useHeroOnScreen();
  const sheet = usePresenceSheet();
  const isDesktop = useIsDesktop();
  const reduced = usePrefersReducedMotion();
  const mac = typeof navigator !== "undefined" && isMacPlatform(navigator.platform);

  const showEdward = !heroOn;
  const live = isDesktop && showEdward && !sheet.isOpen;
  const busy = ACTIVE_MOODS.has(presence.mood);
  const headerBeat = useFreshBeat(presence.beat, live);
  const badge = edwardTabBadge(presence.signals);
  // The polite live region (the phone bar's is display:none on md+): his
  // mood, never a running-tool count, held through a chat turn on chat —
  // the chat view announces the turn itself (review 2026-09-26).
  const spoken = presenceAnnouncement(presence.signals);
  const heldLine = useRef(spoken);
  const chatTurn = pathname === "/chat" && presence.signals.turn != null;
  if (!chatTurn) heldLine.current = spoken;

  return (
    <LazyMotion features={MOTION_FEATURES} strict>
      <header
        className={cn(
          "relative z-30 hidden h-14 flex-none grid-cols-[20rem_minmax(0,1fr)_20rem] items-center md:grid",
          "border-b border-border bg-primary-bg/80 backdrop-blur-xl backdrop-saturate-150",
        )}
      >
        <span className="sr-only" aria-live="polite">
          {heldLine.current}
        </span>
        <div className="flex min-w-0 items-center px-2">
          <button
            type="button"
            data-presence-desktop=""
            onClick={sheet.open}
            aria-label={`${presence.label}. What he's doing`}
            className={cn(
              "flex h-10 min-w-0 max-w-full items-center gap-2.5 rounded-md px-2 text-left",
              "transition-[background-color] duration-base ease-out hover:bg-surface-3/70",
            )}
          >
            {/* Still (no breath, no beats) while a hero is the live one. */}
            <EdwardAvatar
              size={28}
              variant="bust"
              live={live}
              animated={live}
              mood={presence.mood}
              beat={headerBeat}
              beatKey={presence.beat?.key}
              className="flex-none"
            />
            <span className="flex min-w-0 items-baseline gap-2">
              <span className="text-callout text-text-primary">Edward</span>
              <span
                aria-hidden={showEdward ? undefined : true}
                className={cn(
                  "truncate text-footnote transition-opacity duration-base ease-out motion-reduce:duration-[120ms]",
                  busy ? "text-terminal-strong" : "text-text-muted",
                  showEdward ? "opacity-100" : "opacity-0",
                )}
              >
                {presence.status}
              </span>
            </span>
          </button>
        </div>

        <nav aria-label="Primary" className="justify-self-center">
          <div className="flex items-center gap-0.5 rounded-full border border-border bg-surface-3/60 p-1 dark:bg-surface/70">
            {NAV_ITEMS.map((item) => {
              const active = isNavActive(pathname, item.href);
              const isNew = item.href === "/chat" && chatTabAction(pathname) === "new";
              const showPip = item.href === "/settings" && badge.show;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  aria-label={item.href === "/settings" ? badge.label : isNew ? "Chat — start a new chat" : item.label}
                  title={isNew ? `New chat (${shortcutLabel("new-chat", mac)})` : undefined}
                  onClick={(e) => {
                    if (isNew) {
                      e.preventDefault();
                      startNewChat();
                      router.push("/chat");
                    }
                  }}
                  className={cn(
                    "relative inline-flex h-8 items-center gap-1.5 rounded-full px-4 text-footnote font-medium",
                    "transition-colors duration-fast",
                    active ? "text-text-primary" : "text-text-muted hover:text-text-primary",
                  )}
                >
                  {active ? (
                    <m.span
                      // Reduced motion: the pill fades in place, it never slides across.
                      layoutId={reduced ? undefined : "nav-desktop-pill"}
                      initial={reduced ? { opacity: 0 } : false}
                      animate={{ opacity: 1 }}
                      aria-hidden
                      className="absolute inset-0 rounded-full bg-surface shadow-e1 dark:bg-surface-elevated"
                      transition={reduced ? REDUCED_FADE : SPRING_PILL}
                    />
                  ) : null}
                  <span className="relative">{item.label}</span>
                  {showPip ? <span aria-hidden className="relative h-1.5 w-1.5 rounded-full bg-terminal" /> : null}
                </Link>
              );
            })}
          </div>
        </nav>

        <div className="flex items-center justify-end gap-2 pr-3">
          <button
            type="button"
            onClick={paletteStore.open}
            className={cn(
              "inline-flex h-9 items-center gap-2 rounded-md border border-border bg-surface-3/50 pl-2.5 pr-1.5",
              "text-footnote text-text-muted transition-colors duration-fast hover:bg-surface-3 hover:text-text-primary",
            )}
          >
            <Search className="h-4 w-4" aria-hidden />
            <span className="pr-3">Search</span>
            <kbd className="rounded-xs border border-border bg-primary-bg/60 px-1.5 py-0.5 font-sans text-caption text-text-muted">
              {shortcutLabel("palette", mac)}
            </kbd>
          </button>
          <AvatarMenu />
        </div>
      </header>
    </LazyMotion>
  );
}
