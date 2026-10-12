// Controls for the panel itself, as opposed to controls for Edward.
//
// Everything on /hub/settings is about the piece of glass you are standing
// in front of: put it to sleep, black the screen, reload a wedged kiosk,
// or leave the hub for the full app. Edward's own settings (model,
// temperature, screensaver pacing) live on /hub/health, and anything
// destructive stays off the hub entirely until it has a PIN gate.
//
// Kept pure so the confirm behaviour is testable without a DOM.

export type DeviceActionKind = "sleep" | "screen-off" | "reload" | "leave";

export interface DeviceAction {
  id: string;
  kind: DeviceActionKind;
  label: string;
  hint: string;
  icon: string;
  /** Two taps, not one. Only for the ones that take the panel out of the hub. */
  confirm: boolean;
  /** What the button says once it is armed. */
  confirmLabel?: string;
}

export const DEVICE_ACTIONS: DeviceAction[] = [
  {
    id: "sleep",
    kind: "sleep",
    label: "Sleep now",
    hint: "Straight to the photo screensaver",
    icon: "🌙",
    confirm: false,
  },
  {
    id: "screen-off",
    kind: "screen-off",
    label: "Screen off",
    hint: "Black, and lets the panel actually sleep. Tap to wake",
    icon: "🌑",
    confirm: false,
  },
  {
    id: "reload",
    kind: "reload",
    label: "Reload hub",
    hint: "Reloads this screen — the fix for a stuck kiosk",
    icon: "↻",
    confirm: false,
  },
  {
    id: "leave",
    kind: "leave",
    label: "Exit hub",
    hint: "Closes the kiosk and drops to the desktop",
    icon: "↗",
    confirm: true,
    confirmLabel: "Tap again to leave",
  },
];

/**
 * How long an armed confirm survives.
 *
 * Short on purpose. This panel is used by whoever walks past it, so an
 * armed button is a trap for the next person: they tap once, expecting to
 * arm it, and instead complete an action they never started.
 */
export const CONFIRM_TIMEOUT_MS = 6_000;

export interface ConfirmState {
  armed: string | null;
  armedAt: number;
}

export const NOTHING_ARMED: ConfirmState = { armed: null, armedAt: 0 };

export function isArmed(state: ConfirmState, id: string, now: number): boolean {
  if (state.armed !== id) return false;
  return now - state.armedAt < CONFIRM_TIMEOUT_MS;
}

/**
 * Fold a tap into the confirm state.
 *
 * Returns the next state and whether the action should actually run now.
 * An action that needs no confirmation always runs — and clears anything
 * else that was armed, so changing your mind is a single tap.
 */
export function tap(
  state: ConfirmState,
  id: string,
  needsConfirm: boolean,
  now: number,
): { state: ConfirmState; fire: boolean } {
  if (!needsConfirm) return { state: NOTHING_ARMED, fire: true };
  if (isArmed(state, id, now)) return { state: NOTHING_ARMED, fire: true };
  return { state: { armed: id, armedAt: now }, fire: false };
}

/**
 * Drop an armed confirm once it has gone stale.
 *
 * `isArmed` answering false isn't enough on its own: the record stays in
 * state, so whatever timer is driving the countdown never sees a reason to
 * stop. Returns the *same object* when there is nothing to clear, so a
 * caller can hand it straight to a state setter without re-rendering.
 */
export function expire(state: ConfirmState, now: number): ConfirmState {
  if (state.armed === null) return state;
  if (isArmed(state, state.armed, now)) return state;
  return NOTHING_ARMED;
}
