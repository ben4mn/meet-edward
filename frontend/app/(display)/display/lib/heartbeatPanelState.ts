// Pure state + filter helpers for the display HeartbeatPanel.
// React component just dispatches actions and renders the result.

import type { HeartbeatEvent } from "./displayApi";

export type HeartbeatFilter = "all" | "urgent" | "pending" | "done";

export interface HeartbeatPanelState {
  filter: HeartbeatFilter;
  expandedId: string | null;
}

export type HeartbeatPanelAction =
  | { type: "SET_FILTER"; filter: HeartbeatFilter }
  | { type: "TOGGLE_EXPAND"; id: string };

export const initialHeartbeatPanelState: HeartbeatPanelState = {
  filter: "all",
  expandedId: null,
};

export function heartbeatPanelReducer(
  state: HeartbeatPanelState,
  action: HeartbeatPanelAction,
): HeartbeatPanelState {
  switch (action.type) {
    case "SET_FILTER":
      // Switching filters can hide the currently expanded row, so clear it.
      return { filter: action.filter, expandedId: null };
    case "TOGGLE_EXPAND":
      return {
        ...state,
        expandedId: state.expandedId === action.id ? null : action.id,
      };
    default:
      return state;
  }
}

const URGENT_STATUSES = new Set(["mention", "escalated", "calendar_urgent"]);
const DONE_STATUSES = new Set(["noted", "acted", "dismissed"]);

export function filterHeartbeatEvents<T extends Pick<HeartbeatEvent, "triage_status">>(
  events: T[],
  filter: HeartbeatFilter,
): T[] {
  if (filter === "all") return events;
  return events.filter((e) => {
    const status = e.triage_status ?? "pending";
    if (filter === "urgent") return URGENT_STATUSES.has(status);
    if (filter === "pending") return status === "pending" || e.triage_status == null;
    if (filter === "done") return DONE_STATUSES.has(status);
    return true;
  });
}
