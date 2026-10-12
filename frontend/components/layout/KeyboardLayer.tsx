"use client";

// The keyboard layer (docs/design-system-2026-09-25.md §5). The keymap is
// pure (lib/hotkeys.ts); this listens on window and runs it.
//
//   ⌘K / Ctrl+K   the command palette            (even while typing)
//   /             focus the composer: window "edward:focus-composer"
//   Esc           close the top sheet (the Sheet primitive does that and
//                 stops the event), else window "edward:stop-turn"
//   ⌘⇧O           new chat · ⌘\ the chat sidebar · g t / g c / g e
//
// While a sheet or the palette is open only ⌘K and Esc count (hotkeys.ts).
//
// Both window events are cancelable: a page that handles one calls
// preventDefault(), and the shell falls back only when nobody did (focus the
// page's textarea / stop the chat turn through ChatContext).

import { useEffect, useRef } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useChat } from "@/lib/ChatContext";
import {
  FOCUS_COMPOSER_EVENT,
  SEQUENCE_TIMEOUT_MS,
  STOP_TURN_EVENT,
  isMacPlatform,
  isTypingTarget,
  resolveHotkey,
  type HotkeyAction,
} from "@/lib/hotkeys";
import { paletteStore } from "./shellStores";

function platform(): string | undefined {
  if (typeof navigator === "undefined") return undefined;
  const uaData = (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData;
  return uaData?.platform || navigator.platform;
}

function anyModalOpen(): boolean {
  return Boolean(document.querySelector('[role="dialog"][aria-modal="true"]'));
}

/** Ask the page first; true when nobody claimed the event. */
function ask(type: string): boolean {
  return window.dispatchEvent(new window.CustomEvent(type, { cancelable: true }));
}

function focusComposerFallback(): boolean {
  const el = document.querySelector<HTMLTextAreaElement>(
    "textarea[data-composer], main textarea, textarea[aria-label^='Message']",
  );
  if (!el) return false;
  el.focus();
  return true;
}

export function KeyboardLayer() {
  const router = useRouter();
  const pathname = usePathname() ?? "/";
  const chat = useChat();
  const chatRef = useRef(chat);
  chatRef.current = chat;
  const pathRef = useRef(pathname);
  pathRef.current = pathname;

  useEffect(() => {
    const mac = isMacPlatform(platform());
    let pending: "g" | null = null;
    let pendingTimer: ReturnType<typeof setTimeout> | null = null;
    const clearPending = () => {
      pending = null;
      if (pendingTimer) clearTimeout(pendingTimer);
      pendingTimer = null;
    };

    const run = (action: HotkeyAction) => {
      const c = chatRef.current;
      const onChat = pathRef.current.split("?")[0] === "/chat";
      switch (action) {
        case "palette":
          paletteStore.toggle();
          return;
        case "escape":
          if (paletteStore.get() || anyModalOpen()) return;
          if (ask(STOP_TURN_EVENT) && c.canStop) c.stopGeneration();
          return;
        case "focus-composer":
          if (!ask(FOCUS_COMPOSER_EVENT)) return;
          if (focusComposerFallback()) return;
          if (!onChat) {
            router.push("/chat");
            setTimeout(() => {
              if (ask(FOCUS_COMPOSER_EVENT)) focusComposerFallback();
            }, 400);
          }
          return;
        case "new-chat":
          c.startNewChat();
          router.push("/chat");
          return;
        case "toggle-sidebar":
          if (onChat) c.toggleSidebar();
          return;
        case "go-today":
          router.push("/today");
          return;
        case "go-chat":
          router.push("/chat");
          return;
        case "go-edward":
          router.push("/settings");
          return;
      }
    };

    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented && e.key !== "Escape") return;
      const target = e.target as (Element & { type?: string; isContentEditable?: boolean }) | null;
      const typing = isTypingTarget(
        target && "tagName" in target
          ? { tagName: target.tagName, type: target.type, isContentEditable: target.isContentEditable }
          : null,
      );
      const r = resolveHotkey(
        {
          key: e.key,
          metaKey: e.metaKey,
          ctrlKey: e.ctrlKey,
          shiftKey: e.shiftKey,
          altKey: e.altKey,
          isComposing: e.isComposing,
        },
        { typing, pending, mac, modal: paletteStore.get() || anyModalOpen() },
      );
      clearPending();
      if (r.pending) {
        pending = r.pending;
        pendingTimer = setTimeout(clearPending, SEQUENCE_TIMEOUT_MS);
        return;
      }
      if (!r.action) return;
      if (r.action === "escape" && e.defaultPrevented) return;
      if (r.preventDefault) e.preventDefault();
      run(r.action);
    };

    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      clearPending();
    };
  }, [router]);

  return null;
}
