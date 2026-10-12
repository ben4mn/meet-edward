"use client";

// The owner's menu, top right of the desktop header: appearance (Light /
// Dark / System), Edward's screen, sign out (behind a confirm). The button
// shows the owner's initial (EDWARD_OWNER_NAME via lib/owner.ts), or a
// person glyph when no name is configured. A menu button with
// a small popover: ↑↓ move, Home/End, Esc closes and returns focus, a click
// outside closes.

import React, { useEffect, useId, useRef, useState } from "react";
import { LazyMotion, AnimatePresence, m } from "framer-motion";
import { MOTION_FEATURES } from "@/components/ui/motionFeatures";
import { ExternalLink, LogOut, Monitor, MonitorSmartphone, Moon, Sun, User } from "lucide-react";
import { DURATION, EASE_OUT, EXIT_FACTOR, REDUCED_FADE, usePrefersReducedMotion } from "@/components/ui/motion";
import { ownerInitial, useOwnerName } from "@/lib/owner";
import { useThemeMode, type ThemeMode } from "@/lib/themeMode";
import { cn } from "@/lib/utils";
import { openEdwardScreen, signOutStore } from "./shellStores";

const MODES: { value: ThemeMode; label: string; icon: typeof Sun }[] = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "System", icon: MonitorSmartphone },
];

const ITEM =
  "flex h-9 w-full items-center gap-2.5 rounded-sm px-2.5 text-left text-footnote font-medium text-text-primary outline-none " +
  "transition-colors duration-fast hover:bg-surface-3 focus-visible:bg-surface-3 " +
  // A keyboard ring, not only a tint (review 2026-09-26: weak-focus-indicators).
  "focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-terminal/60";

export function AvatarMenu() {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const { mode, setMode } = useThemeMode();
  const reduced = usePrefersReducedMotion();
  const ownerName = useOwnerName();
  const who = ownerName || "You";
  const initial = ownerInitial(ownerName);

  const items = () => Array.from(menuRef.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]') ?? []);

  useEffect(() => {
    if (!open) return;
    const first = items().find((el) => el.getAttribute("aria-checked") === "true") ?? items()[0];
    first?.focus();
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node;
      if (menuRef.current?.contains(t) || buttonRef.current?.contains(t)) return;
      setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [open]);

  const close = (refocus = true) => {
    setOpen(false);
    if (refocus) buttonRef.current?.focus();
  };

  const onMenuKey = (e: React.KeyboardEvent) => {
    const list = items();
    const i = list.indexOf(document.activeElement as HTMLElement);
    if (e.key === "ArrowDown" || e.key === "ArrowRight") {
      e.preventDefault();
      list[(i + 1) % list.length]?.focus();
    } else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
      e.preventDefault();
      list[(i - 1 + list.length) % list.length]?.focus();
    } else if (e.key === "Home") {
      e.preventDefault();
      list[0]?.focus();
    } else if (e.key === "End") {
      e.preventDefault();
      list[list.length - 1]?.focus();
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      close();
    } else if (e.key === "Tab") {
      close(false);
    }
  };

  const motionProps = reduced
    ? { initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 }, transition: REDUCED_FADE }
    : {
        initial: { opacity: 0, scale: 0.96, y: -4 },
        animate: { opacity: 1, scale: 1, y: 0 },
        exit: { opacity: 0, scale: 0.98, y: -2, transition: { duration: DURATION.fast * EXIT_FACTOR } },
        transition: { duration: DURATION.fast, ease: EASE_OUT },
      };

  return (
    <div className="relative">
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={`${who} — appearance, screen, sign out`}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setOpen(true);
          }
        }}
        className={cn(
          "inline-flex h-9 w-9 items-center justify-center rounded-full border border-border bg-surface-3",
          "text-footnote font-semibold text-text-primary transition-[transform,background-color] duration-press",
          "hover:bg-surface-elevated active:scale-[.94]",
          open && "bg-surface-elevated",
        )}
      >
        {initial || <User className="h-4 w-4" aria-hidden />}
      </button>
      <LazyMotion features={MOTION_FEATURES} strict>
        <AnimatePresence>
          {open ? (
            <m.div
              key="menu"
              style={{ transformOrigin: "top right" }}
              className="absolute right-0 top-full z-50 mt-2 w-64 rounded-lg border border-border bg-surface-elevated p-1.5 shadow-e2"
              {...motionProps}
            >
              {/* The ref lives on this inner element: framer's AnimatePresence
                  reads its direct child's props.ref, which React 18 warns about. */}
              <div ref={menuRef} id={menuId} role="menu" aria-label={who} onKeyDown={onMenuKey}>
                <div className="px-2.5 pb-2 pt-1.5">
                  <p className="text-callout text-text-primary">{who}</p>
                  <p className="text-caption text-text-muted">Signed in on this device</p>
                </div>
                <div className="mx-1 mb-1.5 border-t border-border" />
                <p className="px-2.5 pb-1.5 text-overline text-text-muted" id={`${menuId}-appearance`}>
                  Appearance
                </p>
                <div role="group" aria-labelledby={`${menuId}-appearance`} className="mb-1.5 grid grid-cols-3 gap-1 px-1">
                  {MODES.map(({ value, label, icon: Icon }) => {
                    const on = mode === value;
                    return (
                      <button
                        key={value}
                        type="button"
                        role="menuitemradio"
                        aria-checked={on}
                        onClick={() => setMode(value)}
                        className={cn(
                          "flex h-14 flex-col items-center justify-center gap-1 rounded-sm border text-caption outline-none",
                          "transition-[background-color,border-color,color] duration-fast focus-visible:ring-2 focus-visible:ring-terminal/60",
                          on
                            ? "border-text-primary/25 bg-surface-3 text-text-primary"
                            : "border-transparent text-text-muted hover:bg-surface-3 hover:text-text-primary",
                        )}
                      >
                        <Icon className="h-4 w-4" aria-hidden />
                        {label}
                      </button>
                    );
                  })}
                </div>
                <div className="mx-1 mb-1 border-t border-border" />
                <button
                  type="button"
                  role="menuitem"
                  className={ITEM}
                  onClick={() => {
                    close(false);
                    openEdwardScreen();
                  }}
                >
                  <Monitor className="h-4 w-4 text-text-secondary" aria-hidden />
                  <span className="flex-1">Open Edward&apos;s screen</span>
                  <ExternalLink className="h-3.5 w-3.5 text-text-muted" aria-hidden />
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className={ITEM}
                  onClick={() => {
                    close(false);
                    signOutStore.open();
                  }}
                >
                  <LogOut className="h-4 w-4 text-text-secondary" aria-hidden />
                  <span className="flex-1">Sign out…</span>
                </button>
              </div>
            </m.div>
          ) : null}
        </AnimatePresence>
      </LazyMotion>
    </div>
  );
}
