"use client";

// The toast API on its own (2026-09-26 perf pass): the context, useToast,
// the types and the timings. ToastProvider (./Toast.tsx, mounted once in
// app/layout) draws the toasts and re-exports all of this. A page that only
// calls useToast imports it from here, so its own chunk does not carry a
// second copy of the provider and its viewport.

import { createContext, useContext } from "react";
import type { ReactNode } from "react";

export type ToastTone = "neutral" | "ok" | "bad" | "edward";

export interface ToastOptions {
  title: ReactNode;
  body?: ReactNode;
  tone?: ToastTone;
  /** Adds an "Undo" button; runs once, then the toast closes. */
  undo?: () => void;
  undoLabel?: string;
  /** Any other single action ("Open"). Ignored when `undo` is given. */
  action?: { label: string; onClick: () => void };
  /** ms before it leaves on its own. */
  duration?: number;
}

export interface ToastApi {
  /** Shows a toast and returns its id. */
  toast: (options: ToastOptions) => string;
  /** Closes one toast, or all of them. */
  dismiss: (id?: string) => void;
}

export const TOAST_DURATION_MS = 4500;
export const TOAST_UNDO_DURATION_MS = 7000;
/** The leave animation (160ms x 0.7, rounded up). */
export const TOAST_EXIT_MS = 120;
export const MAX_TOASTS = 3;

const NOOP: ToastApi = { toast: () => "", dismiss: () => {} };
export const ToastContext = createContext<ToastApi>(NOOP);

export function useToast(): ToastApi {
  return useContext(ToastContext);
}
