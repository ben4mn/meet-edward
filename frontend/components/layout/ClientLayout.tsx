"use client";

// The app shell (docs/design-system-2026-09-25.md §1, §5).
//
//   phone:   presence bar (live Edward + his line)  ·  page  ·  dock (Today · Chat · Edward)
//   desktop: header (Edward · Today|Chat|Edward · Search ⌘K · you)  ·  [chat sidebar] page
//
// Global overlays mount once here: the presence sheet (tap Edward anywhere),
// the ⌘K command palette, the sign-out confirm, the keyboard layer and the
// notification prompt. Only the layout owns the bottom inset: the dock takes
// the home indicator while it is shown, and the shell exposes
// `--shell-inset-bottom` (0 then; the inset while the dock is folded away,
// composing) for the composer to pad by — never both.

import React from "react";
import { usePathname } from "next/navigation";
import { ThemeProvider } from "@/lib/theme";
import { useSystemThemeSync } from "@/lib/themeMode";
import { cn } from "@/lib/utils";
import { lazyComponent } from "@/lib/lazyComponent";
import { useChatList } from "@/lib/ChatContext";
import { ThumbDock } from "@/components/chat/ThumbDock";
import { ServiceWorkerRegistration } from "@/components/ServiceWorkerRegistration";
import { usePresenceBeacon } from "@/lib/usePresenceBeacon";
import { useComposing } from "@/lib/useComposing";
import { shellBottomInset, shouldHideChrome } from "@/lib/chrome";
import { useIsDesktop } from "@/components/ui/motion";
import { PresenceBar } from "./PresenceBar";
import { DesktopHeader } from "./DesktopHeader";
import { KeyboardLayer } from "./KeyboardLayer";

// Off every page's first load (2026-09-26 perf pass; lib/lazyComponent):
// the overlays (./shellOverlays, one chunk) and the desktop chat sidebar,
// whose swipe rows carry framer-motion's animation engine. Each fetch starts
// as this module runs; each renders the moment its chunk is here.
const overlays = () => import("./shellOverlays");
const PresenceSheet = lazyComponent(() => overlays().then((mod) => mod.PresenceSheet));
const CommandPalette = lazyComponent(() => overlays().then((mod) => mod.CommandPalette));
const SignOutSheet = lazyComponent(() => overlays().then((mod) => mod.SignOutSheet));
const NotificationPrompt = lazyComponent(() => overlays().then((mod) => mod.NotificationPrompt));

/** The sidebar's box (same width and border, empty) until its chunk lands: nothing shifts. */
function SidebarPlaceholder() {
  const { sidebarCollapsed } = useChatList();
  return (
    <aside
      aria-hidden
      className={cn(
        "hidden flex-none overflow-hidden border-r bg-primary-bg md:flex",
        sidebarCollapsed ? "w-0 border-transparent" : "w-[18.5rem] border-border",
      )}
    />
  );
}
const ChatSidebar = lazyComponent(
  () => import("@/components/chat/ChatSidebar").then((mod) => mod.ChatSidebar),
  SidebarPlaceholder,
);

if (typeof window !== "undefined") {
  for (const part of [PresenceSheet, CommandPalette, SignOutSheet, NotificationPrompt, ChatSidebar]) {
    part.preload().catch(() => {
      /* retried when it mounts */
    });
  }
}

function ThemeModeSync() {
  useSystemThemeSync();
  return null;
}

interface ClientLayoutProps {
  children: React.ReactNode;
}

export function ClientLayout({ children }: ClientLayoutProps) {
  usePresenceBeacon();
  const pathname = usePathname() ?? "/today";
  const isChatPage = pathname === "/chat";
  const composing = useComposing();
  const isDesktop = useIsDesktop();
  const dockShown = !isDesktop && !shouldHideChrome(pathname, composing);

  return (
    <ThemeProvider>
      <ThemeModeSync />
      {/* Register service worker for PWA */}
      <ServiceWorkerRegistration />
      <KeyboardLayer />
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:rounded-md focus:bg-text-primary focus:px-3 focus:py-2 focus:text-primary-bg focus:outline-none"
      >
        Skip to main content
      </a>
      <div
        data-dock={dockShown ? "shown" : "hidden"}
        style={{ "--shell-inset-bottom": isDesktop ? "0px" : shellBottomInset(dockShown) } as React.CSSProperties}
        className={cn("group/shell flex h-dvh flex-col overflow-hidden bg-primary-bg")}
      >
        {/* Only the chrome for this size is mounted: hidden with CSS, the
            desktop header and the chat sidebar (30–50 rows on useChat) were
            reconciled on every streamed token on the phone. */}
        {isDesktop ? <DesktopHeader /> : <PresenceBar />}
        <div className="flex min-h-0 flex-1 overflow-hidden">
          {/* The conversations sidebar: chat on md+ (the phone has the drawer) */}
          {isChatPage && isDesktop && <ChatSidebar />}
          <main id="main" className="flex min-h-0 flex-1 flex-col overflow-hidden">
            {children}
          </main>
        </div>
        {/* Global mobile nav — one pattern on every authenticated page */}
        <ThumbDock />
        {/* Push notification prompt: never over the dock or the composer */}
        <NotificationPrompt />
      </div>
      <PresenceSheet />
      <CommandPalette />
      <SignOutSheet />
    </ThemeProvider>
  );
}
