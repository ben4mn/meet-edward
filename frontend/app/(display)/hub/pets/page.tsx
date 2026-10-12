"use client";

import "../hub.css";
import { HubRoot } from "../components/HubRoot";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { formatClock } from "@/lib/time";

import { usePoll } from "../../display/hooks/usePoll";
import { useWakeLock } from "../../display/hooks/useWakeLock";
import { useIdleRedirect } from "../lib/useIdleRedirect";
import { fetchBoard, logCare, undoCare } from "../lib/hubApi";
import { useWho } from "../lib/useWho";
import { WhoToggle } from "../components/WhoToggle";
import type { CareWindow, HubBoard, HubPet, MedEntry } from "../lib/types";

// Poll rather than push for v1. Both humans tap the same physical screen
// today; a live channel only earns its keep once voice logging lands and
// the board has to update while nobody is touching it.
const POLL_MS = 5000;

const SPECIES_ICON: Record<string, string> = { dog: "🐕", cat: "🐈" };

const STATE_LABEL: Record<string, string> = {
  due: "DUE",
  given: "GIVEN",
  overdue: "LATE",
  missed: "MISSED",
  refused: "REFUSED",
  skipped: "SKIPPED",
};

function useClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 10_000);
    return () => clearInterval(id);
  }, []);
  return now;
}

export default function HubPage() {
  useWakeLock(true);
  useIdleRedirect();

  const clock = useClock();
  const { data: board, error, refetch } = usePoll<HubBoard>(fetchBoard, POLL_MS);

  const { who, setWho } = useWho();
  // Item ids from the last action, so Undo can reverse a bulk give too.
  const [lastLogged, setLastLogged] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const undoTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Undo stays available for a good while — long enough to walk back from
  // the food bin and realise you tapped the wrong bowl.
  const armUndo = useCallback((ids: string[]) => {
    setLastLogged(ids);
    if (undoTimer.current) clearTimeout(undoTimer.current);
    undoTimer.current = setTimeout(() => setLastLogged([]), 90_000);
  }, []);

  const runLog = useCallback(
    async (ids: string[]) => {
      if (!ids.length || busy) return;
      setBusy(true);
      try {
        await Promise.all(ids.map((id) => logCare(id, who.toLowerCase())));
        armUndo(ids);
        refetch();
      } finally {
        setBusy(false);
      }
    },
    [busy, who, armUndo, refetch],
  );

  const runUndo = useCallback(async () => {
    if (!lastLogged.length || busy) return;
    setBusy(true);
    try {
      await Promise.all(lastLogged.map((id) => undoCare(id)));
      setLastLogged([]);
      refetch();
    } finally {
      setBusy(false);
    }
  }, [lastLogged, busy, refetch]);

  const currentWindow: CareWindow = board?.window ?? "am";

  // Everything still outstanding in this window, across all four pets.
  const outstanding = useMemo(() => {
    if (!board) return [] as string[];
    const ids: string[] = [];
    for (const pet of board.pets) {
      const bowl = pet.feedings[currentWindow];
      if (bowl.item_id && !bowl.done) ids.push(bowl.item_id);
      for (const med of pet.meds) {
        if (med.window === currentWindow && (med.status === "due" || med.status === "overdue")) {
          ids.push(med.id);
        }
      }
    }
    return ids;
  }, [board, currentWindow]);

  const windowLabel = currentWindow === "am" ? "MORNING" : "EVENING";

  return (
    <HubRoot>
      <header className="hub-header">
        <Link href="/hub" className="hub-back" aria-label="Back to hub home">
          ‹
        </Link>
        <div>
          <div className="hub-clock">
            {formatClock(clock)}
          </div>
          <div className="hub-date">
            {clock.toLocaleDateString([], {
              weekday: "short",
              month: "short",
              day: "numeric",
            })}
          </div>
        </div>

        <div className="hub-alerts">
          {board?.alerts.map((a) => (
            <div key={`${a.pet}-${a.item}`} className="hub-alert">
              {a.pet} · {a.item} · {a.minutes_late}m late
            </div>
          ))}
        </div>

        <WhoToggle who={who} onPick={setWho} />
      </header>

      <main className="hub-grid">
        {board?.pets.map((pet) => (
          <PetColumn
            key={pet.id}
            pet={pet}
            window={currentWindow}
            onLog={(id) => runLog([id])}
          />
        ))}
      </main>

      <footer className="hub-footer">
        <button
          className="hub-giveall"
          disabled={!outstanding.length || busy}
          onClick={() => runLog(outstanding)}
        >
          {outstanding.length
            ? `GIVE ALL ${windowLabel} · ${outstanding.length}${who ? ` · as ${who}` : ""}`
            : `${windowLabel} COMPLETE`}
        </button>
        <button className="hub-undo" disabled={!lastLogged.length || busy} onClick={runUndo}>
          Undo{lastLogged.length > 1 ? ` (${lastLogged.length})` : ""}
        </button>
        <div className="hub-status">
          {error ? "offline — showing last known" : board ? `care day ${board.care_day}` : "loading…"}
        </div>
      </footer>
    </HubRoot>
  );
}

function PetColumn({
  pet,
  window: currentWindow,
  onLog,
}: {
  pet: HubPet;
  window: CareWindow;
  onLog: (itemId: string) => void;
}) {
  // Show only this window's doses. The full day is a tap away later; at
  // 6am you care about the round in front of you, not tonight's.
  const meds = pet.meds.filter((m) => m.window === currentWindow);

  return (
    <section className="hub-pet">
      <div className="hub-pet-name">
        <span className="hub-pet-species">
          {SPECIES_ICON[pet.species ?? ""] ?? "🐾"}
        </span>
        {pet.name}
      </div>

      <div className="hub-bowls">
        {(["am", "pm"] as CareWindow[]).map((w) => {
          const bowl = pet.feedings[w];
          return (
            <button
              key={w}
              className="hub-bowl"
              data-done={bowl.done}
              disabled={!bowl.item_id}
              onClick={() => bowl.item_id && onLog(bowl.item_id)}
            >
              {bowl.marker && <span className="hub-bowl-marker">{bowl.marker}</span>}
              <span className="hub-bowl-window">{w.toUpperCase()}</span>
              <span className="hub-bowl-icon">{bowl.done ? "🍽" : "○"}</span>
              {bowl.done && (
                <span className="hub-bowl-by">
                  {(bowl.by ?? "?")[0]?.toUpperCase()} {bowl.at_local}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {meds.length ? (
        <div className="hub-meds">
          {meds.map((med) => (
            <MedButton key={med.id} med={med} onLog={onLog} />
          ))}
        </div>
      ) : (
        <div className="hub-empty">no meds</div>
      )}
    </section>
  );
}

function MedButton({ med, onLog }: { med: MedEntry; onLog: (id: string) => void }) {
  const done = med.status === "given";
  return (
    <button
      className="hub-med"
      data-status={med.status}
      onClick={() => !done && onLog(med.id)}
    >
      <span>
        <span className="hub-med-name">
          {med.name}
          {med.needs_review && <span className="hub-review">?</span>}
        </span>
        <span className="hub-med-dose">
          {[med.dose, med.at_time].filter(Boolean).join(" · ")}
        </span>
      </span>
      <span className="hub-med-state">
        {done && med.by
          ? `${med.by[0].toUpperCase()} ${med.at_local}`
          : STATE_LABEL[med.status] ?? med.status.toUpperCase()}
      </span>
    </button>
  );
}
