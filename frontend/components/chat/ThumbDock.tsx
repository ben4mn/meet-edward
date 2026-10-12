"use client";

// The phone's native tab bar (docs/design-system-2026-09-25.md §5).
//
//   Today · Chat · Edward — 56px drawn, plus the home indicator
//   (`max(env(safe-area-inset-bottom), 8px)`; the layout owns the bottom
//   inset, so the composer adds none while the dock is shown). An active
//   pill slides between tabs (framer layoutId, the pill spring; a fade in
//   place under reduced motion). Tapping
//   the tab you are on does what iOS does: Today scrolls to the top
//   (window "edward:scroll-top"), Edward pops a sub-page to its root, Chat
//   starts a new chat (SquarePen). The Edward tab is his still bust with an
//   emerald pip while he wants the owner. The dock folds away while the
//   owner types on chat, and tells the toasts its height through --dock-h.

import React, { useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { LazyMotion, m } from "framer-motion";
import { MOTION_FEATURES } from "@/components/ui/motionFeatures";
import { MessageSquare, SquarePen, Sun } from "lucide-react";
import { EdwardAvatar } from "@/components/EdwardAvatar";
import { cn } from "@/lib/utils";
import {
  NAV_ITEMS,
  SCROLL_TOP_EVENT,
  chatTabAction,
  dockTapAction,
  edwardTabBadge,
  isNavActive,
} from "@/components/layout/navItems";
import { useChatList } from "@/lib/ChatContext";
import { useComposing } from "@/lib/useComposing";
import { shouldHideChrome } from "@/lib/chrome";
import { useEdwardPresence } from "@/lib/presence";
import { REDUCED_FADE, SPRING_PILL, usePrefersReducedMotion } from "@/components/ui/motion";

/** The dock's drawn height above the home-indicator padding. */
export const DOCK_HEIGHT_PX = 56;

interface ThumbDockProps {
  /** Optional handler for "new chat" (the Chat tab, tapped while already on
   *  /chat). If omitted, the dock starts a fresh /chat session itself. */
  onNewChat?: () => void;
}

function setDockHeight(px: number) {
  if (typeof document === "undefined") return;
  document.documentElement.style.setProperty("--dock-h", `${px}px`);
}

export function ThumbDock({ onNewChat }: ThumbDockProps) {
  const pathname = usePathname() ?? "/chat";
  const router = useRouter();
  const { startNewChat } = useChatList();
  const composing = useComposing();
  const presence = useEdwardPresence();
  const reduced = usePrefersReducedMotion();
  const hidden = shouldHideChrome(pathname, composing);

  // Toasts sit above the dock (globals.css .toast-viewport reads --dock-h).
  useEffect(() => {
    setDockHeight(hidden ? 0 : DOCK_HEIGHT_PX);
  }, [hidden]);
  useEffect(() => () => setDockHeight(0), []);

  if (hidden) return null;

  const badge = edwardTabBadge(presence.signals);

  const newChat = () => {
    if (onNewChat) onNewChat();
    else {
      startNewChat();
      router.push("/chat");
    }
  };

  const onTab = (e: React.MouseEvent, href: string) => {
    const action = dockTapAction(pathname, href);
    if (action === "scroll-top") {
      e.preventDefault();
      window.dispatchEvent(new window.CustomEvent(SCROLL_TOP_EVENT, { detail: { href } }));
    } else if (action === "navigate" && isNavActive(pathname, href)) {
      // A sub-page of the tab you are on: pop back to its root.
      e.preventDefault();
      router.push(href);
    }
  };

  const pillTransition = reduced ? REDUCED_FADE : SPRING_PILL;

  return (
    <LazyMotion features={MOTION_FEATURES} strict>
      <nav
        aria-label="Primary"
        data-dock-bar=""
        className={cn(
          "md:hidden relative z-30 flex-none border-t border-border",
          "bg-primary-bg/80 backdrop-blur-xl backdrop-saturate-150 pb-safe",
        )}
      >
        <div className="mx-auto flex h-14 max-w-md items-stretch px-2">
          {NAV_ITEMS.map((item) => {
            const active = isNavActive(pathname, item.href);
            const isNewChat = item.href === "/chat" && chatTabAction(pathname) === "new";
            const label = isNewChat ? "New chat" : item.label;

            const icon =
              item.href === "/today" ? (
                <Sun className="h-[22px] w-[22px]" strokeWidth={active ? 2.1 : 1.8} aria-hidden />
              ) : item.href === "/chat" ? (
                isNewChat ? (
                  <SquarePen className="h-[21px] w-[21px]" strokeWidth={2} aria-hidden />
                ) : (
                  <MessageSquare className="h-[22px] w-[22px]" strokeWidth={active ? 2.1 : 1.8} aria-hidden />
                )
              ) : (
                <span className="relative inline-flex" aria-hidden>
                  <span
                    className={cn(
                      "inline-flex transition-[filter,opacity] duration-fast",
                      !active && "opacity-75 grayscale-[.6]",
                    )}
                  >
                    <EdwardAvatar size={26} variant="bust" animated={false} />
                  </span>
                  {badge.show ? (
                    <span
                      data-pip=""
                      className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-terminal ring-2 ring-primary-bg"
                    />
                  ) : null}
                </span>
              );

            const body = (
              <>
                <span className="relative flex h-8 w-16 items-center justify-center">
                  {active ? (
                    <m.span
                      // Reduced motion: the pill fades in place, it never slides across.
                      layoutId={reduced ? undefined : "dock-pill"}
                      initial={reduced ? { opacity: 0 } : false}
                      animate={{ opacity: 1 }}
                      aria-hidden
                      className="absolute inset-0 rounded-full bg-text-primary/[.09] dark:bg-text-primary/[.11]"
                      transition={pillTransition}
                    />
                  ) : null}
                  <span className="relative">{icon}</span>
                </span>
                <span
                  className={cn(
                    "text-[11px] font-medium leading-[14px] tracking-[.01em]",
                    active ? "text-text-primary" : "text-text-muted",
                  )}
                >
                  {label}
                </span>
              </>
            );

            const cls = cn(
              "group flex min-h-touch flex-1 flex-col items-center justify-center gap-0.5 rounded-md",
              "transition-[color,transform] duration-press ease-out active:scale-[.94] select-none motion-reduce:active:scale-100",
              "[-webkit-tap-highlight-color:transparent]",
              active ? "text-text-primary" : "text-text-muted hover:text-text-secondary",
            );

            if (isNewChat) {
              return (
                <button
                  key={item.href}
                  type="button"
                  data-tab={item.href}
                  onClick={newChat}
                  aria-label="New chat"
                  aria-current="page"
                  className={cls}
                >
                  {body}
                </button>
              );
            }
            return (
              <Link
                key={item.href}
                href={item.href}
                data-tab={item.href}
                onClick={(e) => onTab(e, item.href)}
                aria-label={item.href === "/settings" ? badge.label : item.label}
                aria-current={active ? "page" : undefined}
                className={cls}
              >
                {body}
              </Link>
            );
          })}
        </div>
      </nav>
    </LazyMotion>
  );
}
