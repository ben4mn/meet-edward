/**
 * Who Edward works for, on the client.
 *
 * The backend reads the owner's name from EDWARD_OWNER_NAME and serves it
 * as `owner_name` on GET /api/auth/status (before sign-in: the login page
 * greets the owner by it). AuthContext calls `setOwnerName` when that
 * verdict arrives; components read it with `useOwnerName()`.
 *
 * Copy speaks to the owner as "you" wherever it can. The name is for the
 * few places that need one (a greeting, the avatar initial) and for
 * matching the name in text the model wrote ("Remind Sam to …").
 */

import { useSyncExternalStore } from "react";

let current = "";
const listeners = new Set<() => void>();

/** Set by AuthContext from /api/auth/status (tests call it directly). */
export function setOwnerName(name: string | null | undefined): void {
  const next = (name ?? "").trim();
  if (next === current) return;
  current = next;
  listeners.forEach((fn) => fn());
}

/** The owner's name, or "" when the server has none configured. */
export function getOwnerName(): string {
  return current;
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** React hook: the owner's name ("" until known or when unset). */
export function useOwnerName(): string {
  return useSyncExternalStore(subscribe, getOwnerName, () => "");
}

/** The owner's initial for an avatar ("E"), or "" when no name is set. */
export function ownerInitial(name: string = current): string {
  return name.trim().charAt(0).toUpperCase();
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Regex source matching how text names the owner: their name (when set)
 * or "me". For model-written titles like "Remind Sam to …" / "Remind me to …".
 */
export function ownerNamePattern(name: string = current): string {
  const names = [name.trim(), "me"].filter(Boolean).map(escapeRegExp);
  return `(?:${names.join("|")})`;
}
