// The presence context object on its own (2026-09-26), so a light module —
// EdwardAvatar, which /login, /screen and the hub also draw — can ask "is a
// presence provider feeding me?" without pulling in the provider (and
// ChatContext with it). lib/presence.tsx owns the provider and the hooks.

import { createContext, useContext } from "react";
import type { EdwardPresence } from "./presence";

export const PresenceContext = createContext<EdwardPresence | null>(null);

/** True inside an EdwardPresenceProvider: it owns the beats (perk included). */
export function usePresenceProvided(): boolean {
  return useContext(PresenceContext) !== null;
}

/** His hands in the owner's Chrome are down, per the provider (false outside it):
 *  every Edward's antenna tip goes amber (design system §4, trust light). */
export function usePresenceHandsDown(): boolean {
  return useContext(PresenceContext)?.signals.handsDown === true;
}
