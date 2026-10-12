"use client";

// The Edward tab's live status, shared by the landing and the desktop rail
// (one fetch — components/settings/edwardTabStore.ts). Every source is read
// on its own and degrades to null; lib/../edwardHomeModel turns the
// snapshot into honest lines. mind/now comes from the presence provider
// when one is mounted (it polls), else from this store's own read.

import { useEffect, useMemo, useSyncExternalStore } from "react";

import {
  getHeartbeatStatus,
  getMemoryStats,
  getMindIntentions,
  getOrchestratorStatus,
  getWishlist,
  type DocumentItem,
  type HeartbeatStatus,
  type MemoryStats,
} from "@/lib/api";
import type { MindIntention } from "@/lib/mindLog";
import { fetchMindNow, type MindNow } from "@/lib/mindNowApi";
import { useEdwardPresence } from "@/lib/presence";
import { fetchHealth } from "@/app/(display)/hub/lib/hubApi";
import type { HealthResponse } from "@/app/(display)/hub/lib/types";
import { createTabStore } from "./edwardTabStore";
import { roomLines, type RoomLine } from "./edwardHomeModel";
import { getStateDocument } from "./edwardTabApi";
import type { RoomId } from "./settingsRooms";

export interface EdwardTabSnapshot {
  memory: MemoryStats | null;
  heartbeat: HeartbeatStatus | null;
  workersRunning: number | null;
  wishesOpen: number | null;
  house: HealthResponse | null;
  intentions: MindIntention[] | null;
  /** null: he has no state page (or it could not be read — see stateDocFailed). */
  stateDoc: DocumentItem | null;
  stateDocFailed: boolean;
  mindNow: MindNow | null;
}

/** Snapshots younger than this are not refetched when a page mounts. */
export const TAB_STATUS_MAX_AGE_MS = 30_000;

async function orNull<T>(p: Promise<T>): Promise<T | null> {
  try {
    return await p;
  } catch {
    return null;
  }
}

async function loadSnapshot(): Promise<EdwardTabSnapshot> {
  let stateDocFailed = false;
  const [memory, heartbeat, orchestrator, wishes, house, intentions, stateDoc, mind] = await Promise.all([
    orNull(getMemoryStats()),
    orNull(getHeartbeatStatus()),
    orNull(getOrchestratorStatus()),
    orNull(getWishlist("open")),
    orNull(fetchHealth()),
    orNull(getMindIntentions()),
    getStateDocument().catch(() => {
      stateDocFailed = true;
      return null;
    }),
    fetchMindNow().catch(() => null),
  ]);
  return {
    memory,
    heartbeat,
    workersRunning: orchestrator ? orchestrator.active_count ?? 0 : null,
    wishesOpen: wishes ? wishes.length : null,
    house,
    intentions: intentions ? intentions.intentions ?? [] : null,
    stateDoc,
    stateDocFailed,
    mindNow: mind && mind.status === "ok" ? mind.data : null,
  };
}

export const edwardTabStore = createTabStore(loadSnapshot);

export interface EdwardTabStatus {
  data: EdwardTabSnapshot | null;
  loading: boolean;
  /** Presence's mind/now when it has one, else the store's. */
  mindNow: MindNow | null;
  lines: Record<RoomId, RoomLine> | null;
  refresh: () => void;
}

/** `enabled: false` reads what the store already has and fetches nothing
 *  (the rail on a phone, where it is hidden). */
export function useEdwardTabStatus({ enabled = true }: { enabled?: boolean } = {}): EdwardTabStatus {
  const state = useSyncExternalStore(edwardTabStore.subscribe, edwardTabStore.getState, edwardTabStore.getState);
  const presence = useEdwardPresence();

  useEffect(() => {
    if (enabled) void edwardTabStore.refresh({ maxAgeMs: TAB_STATUS_MAX_AGE_MS });
  }, [enabled]);

  const data = state.data;
  const mindNow = presence.available && presence.mindNow ? presence.mindNow : data?.mindNow ?? null;

  const lines = useMemo(() => {
    if (!data) return null;
    return roomLines(
      {
        mindNow,
        intentions: data.intentions ? data.intentions.length : null,
        memory: data.memory,
        heartbeat: data.heartbeat,
        workersRunning: data.workersRunning,
        wishesOpen: data.wishesOpen,
        house: data.house
          ? { overall: data.house.overall, problems: data.house.watchdog?.problems ?? [], repairs: data.house.watchdog?.repairs ?? [] }
          : null,
      },
      { now: new Date() },
    );
  }, [data, mindNow]);

  return {
    data,
    loading: state.loading,
    mindNow,
    lines,
    refresh: () => void edwardTabStore.refresh({ maxAgeMs: 0 }),
  };
}
