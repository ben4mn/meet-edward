"use client";

// Toasts with Undo (docs/design-system-2026-09-25.md §1.7: undo instead of
// confirm where possible). `ToastProvider` is mounted once in app/layout;
// anything below calls `useToast().toast({ title, undo })`. Outside a
// provider `useToast` is a harmless no-op, so a component can toast without
// knowing where it is rendered.
//
// Toasts sit above the dock and the home indicator on the phone
// (`.toast-viewport` in globals.css) and bottom-right on md+. They enter
// with a short rise and leave with a 0.7x fade; under reduced motion both
// are plain fades. At most three at once; the oldest makes way.

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { AlertCircle, CheckCircle2 } from "lucide-react";
import { cn } from "./cn";
import {
  MAX_TOASTS,
  TOAST_DURATION_MS,
  TOAST_EXIT_MS,
  TOAST_UNDO_DURATION_MS,
  ToastContext,
  type ToastApi,
  type ToastOptions,
  type ToastTone,
} from "./toastContext";

export {
  MAX_TOASTS,
  TOAST_DURATION_MS,
  TOAST_EXIT_MS,
  TOAST_UNDO_DURATION_MS,
  useToast,
  type ToastApi,
  type ToastOptions,
  type ToastTone,
} from "./toastContext";

interface ToastRecord extends ToastOptions {
  id: string;
  leaving: boolean;
}

let nextId = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastRecord[]>([]);
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>[]>());
  const live = useRef<string[]>([]);

  const clearTimers = useCallback((id: string) => {
    for (const t of timers.current.get(id) ?? []) clearTimeout(t);
    timers.current.delete(id);
  }, []);

  const remove = useCallback(
    (id: string) => {
      clearTimers(id);
      live.current = live.current.filter((x) => x !== id);
      setToasts((ts) => ts.filter((t) => t.id !== id));
    },
    [clearTimers],
  );

  // Both steps are scheduled up front (leave, then remove), so the exit
  // never waits on a render to schedule its own end.
  const schedule = useCallback(
    (id: string, leaveIn: number) => {
      clearTimers(id);
      timers.current.set(id, [
        setTimeout(() => setToasts((ts) => ts.map((t) => (t.id === id ? { ...t, leaving: true } : t))), leaveIn),
        setTimeout(() => remove(id), leaveIn + TOAST_EXIT_MS),
      ]);
    },
    [clearTimers, remove],
  );

  const dismiss = useCallback(
    (id?: string) => {
      const ids = id ? [id] : [...live.current];
      for (const x of ids) {
        live.current = live.current.filter((y) => y !== x);
        setToasts((ts) => ts.map((t) => (t.id === x ? { ...t, leaving: true } : t)));
        clearTimers(x);
        timers.current.set(x, [setTimeout(() => remove(x), TOAST_EXIT_MS)]);
      }
    },
    [clearTimers, remove],
  );

  const toast = useCallback(
    (options: ToastOptions) => {
      const id = `toast-${++nextId}`;
      const duration = options.duration ?? (options.undo ? TOAST_UNDO_DURATION_MS : TOAST_DURATION_MS);
      live.current = [...live.current, id];
      setToasts((ts) => [...ts, { ...options, id, leaving: false }]);
      schedule(id, duration);
      while (live.current.length > MAX_TOASTS) dismiss(live.current[0]);
      return id;
    },
    [dismiss, schedule],
  );

  useEffect(() => {
    const all = timers.current;
    return () => {
      all.forEach((list) => list.forEach((t) => clearTimeout(t)));
      all.clear();
    };
  }, []);

  const api = useMemo<ToastApi>(() => ({ toast, dismiss }), [toast, dismiss]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        data-toast-viewport=""
        role="region"
        aria-label="Notifications"
        aria-live="polite"
        className="toast-viewport pointer-events-none fixed inset-x-0 z-[70] flex flex-col items-center gap-2 px-4 md:inset-x-auto md:right-6 md:items-end md:px-0"
      >
        {toasts.map((t) => (
          <ToastItem key={t.id} toast={t} onDismiss={() => dismiss(t.id)} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function ToastItem({ toast, onDismiss }: { toast: ToastRecord; onDismiss: () => void }) {
  const { title, body, tone = "neutral", undo, undoLabel = "Undo", action, leaving } = toast;
  const act = undo ? { label: undoLabel, onClick: undo } : action;
  return (
    <div
      role={tone === "bad" ? "alert" : undefined}
      aria-hidden={leaving || undefined}
      data-leaving={leaving ? "" : undefined}
      className={cn(
        "pointer-events-auto flex w-full max-w-sm items-center gap-3 rounded-lg border border-border",
        "bg-surface-elevated/95 px-4 py-2.5 text-text-primary shadow-e2 backdrop-blur-md",
        leaving ? "toast-leave pointer-events-none" : "animate-fade-rise",
      )}
    >
      {tone === "ok" ? <CheckCircle2 className="h-4 w-4 flex-none text-ok" aria-hidden /> : null}
      {tone === "bad" ? <AlertCircle className="h-4 w-4 flex-none text-bad" aria-hidden /> : null}
      {tone === "edward" ? (
        <span aria-hidden className="h-2 w-2 flex-none rounded-full bg-terminal shadow-[0_0_8px_rgb(var(--terminal-rgb)/0.6)]" />
      ) : null}
      <div className="min-w-0 flex-1 py-1">
        <p className="text-callout leading-5">{title}</p>
        {body ? <p className="mt-0.5 text-footnote text-text-secondary">{body}</p> : null}
      </div>
      {act && !leaving ? (
        <button
          type="button"
          onClick={() => {
            act.onClick();
            onDismiss();
          }}
          className="-my-1.5 -mr-2 inline-flex min-h-[44px] flex-none items-center rounded-md px-3 text-callout text-text-primary transition-[background-color,transform] duration-press hover:bg-surface-3 active:scale-[.97]"
        >
          {act.label}
        </button>
      ) : null}
    </div>
  );
}
