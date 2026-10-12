"use client";

// One piece of the stage (2026-09-28), placed where the scene put it
// (lib/stageModel.ts::rectStyle). Authored kinds are drawn from what Edward
// wrote (AuthoredTiles.tsx); live kinds fetch their own data — the calendar
// and Listening through stage faces (StageLivePieces.tsx), the rest through
// the panels the tile grid used; `pets` reads the hub's care board. Every
// live piece draws the title Edward gave it. A piece enters once (fade, rise or type) and then sits
// still; one the lane crosses fades back (data-dimmed).

import React, { type CSSProperties } from "react";

import type { HubBoard } from "../../hub/lib/types";
import { useSharedPoll } from "../hooks/useSharedPoll";
import { careSummary, rectStyle, type StageGrid, type StagePiece as Piece } from "../lib/stageModel";
import { AlertStrip } from "./AlertStrip";
import {
  CountdownPiece,
  HtmlPiece,
  ImagePiece,
  ListPiece,
  NotePiece,
  StatPiece,
  TimerPiece,
  VideoPiece,
  WordsPiece,
} from "./AuthoredTiles";
import { LiveClock } from "./LiveClock";
import { MemoryTicker } from "./MemoryTicker";
import { RecentsPanel } from "./RecentsPanel";
import { CalendarStage, ListeningStage } from "./StageLivePieces";
import { VitalsPanel } from "./VitalsPanel";
import { WorkersPanel } from "./WorkersPanel";

export const BOARD_URL = "/api/hub/board";
const BOARD_POLL_MS = 30_000;

/** The hub's care board (hub/lib/hubApi.ts has the same call; importing it pulls the hub's whole API into /screen). */
async function fetchBoard(): Promise<HubBoard> {
  const res = await fetch(BOARD_URL, { credentials: "include" });
  if (!res.ok) throw new Error(`${BOARD_URL} failed: ${res.status}`);
  return (await res.json()) as HubBoard;
}

/** Today's care, one row a pet: only a late timed dose is amber (hub rules 1–3). */
export function PetsPiece({ title }: { title?: string | null }) {
  const { data } = useSharedPoll<HubBoard>(BOARD_URL, fetchBoard, BOARD_POLL_MS);
  const rows = careSummary(data);
  return (
    <div className="st-card st-pets">
      <div className="st-card-title">{title || "The pets"}</div>
      <div className="st-list-rows">
        {rows.length === 0 && <div className="st-meta">{data ? "Nothing on the board today." : "Reading the board…"}</div>}
        {rows.map((r) => (
          <div key={r.pet} className="st-care-row">
            <div className="st-care-text">
              <span className="st-list-label">
                {r.pet}
                {r.review && <span className="st-review" title="From a sitter's note — check it">?</span>}
              </span>
              <span className="st-list-detail">{r.detail}</span>
            </div>
            <span className="st-chip" data-tone={r.tone}>{r.chip}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Body({ piece, nowMs, tz, signed }: { piece: Piece; nowMs: number; tz?: string; signed: string | null }) {
  switch (piece.kind) {
    case "words":
      return <WordsPiece piece={piece} />;
    case "note":
      return <NotePiece piece={piece} signed={signed} />;
    case "list":
      return <ListPiece piece={piece} />;
    case "stat":
      return <StatPiece piece={piece} />;
    case "countdown":
      return <CountdownPiece piece={piece} nowMs={nowMs} />;
    case "timer":
      return <TimerPiece piece={piece} nowMs={nowMs} tz={tz} />;
    case "image":
      return <ImagePiece piece={piece} />;
    case "video":
      return <VideoPiece piece={piece} />;
    case "html":
      return <HtmlPiece piece={piece} />;
    // Live kinds draw the title Edward gave them: his own view of the
    // screen names it (screen_service.piece_phrase).
    case "clock":
      return <LiveClock face="stage" title={piece.title} />;
    case "calendar":
      return <CalendarStage title={piece.title} />;
    case "pets":
      return <PetsPiece title={piece.title} />;
    case "alerts":
      return (
        <>
          {piece.title && <div className="st-overline st-piece-title">{piece.title}</div>}
          <AlertStrip />
        </>
      );
    case "heartbeat":
      return <ListeningStage title={piece.title} />;
    case "recents":
      return <RecentsPanel title={piece.title} />;
    case "workers":
      return <WorkersPanel title={piece.title} />;
    case "vitals":
      return <VitalsPanel title={piece.title} />;
    case "memory_ticker":
      return <MemoryTicker title={piece.title} />;
    default:
      return null;
  }
}

interface Props {
  piece: Piece;
  stage: StageGrid;
  /** The lane crosses it: fade back. */
  dimmed: boolean;
  nowMs: number;
  tz?: string;
  /** A note's signature: "Written by me · 4:10 pm". */
  signed: string | null;
}

export function StagePiece({ piece, stage, dimmed, nowMs, tz, signed }: Props) {
  const style: CSSProperties = rectStyle(piece.rect, stage);
  return (
    <div
      className="st-piece"
      data-piece={piece.id}
      data-kind={piece.kind}
      data-size={piece.size}
      data-enter={piece.enter}
      data-dimmed={dimmed ? "true" : undefined}
      style={style}
    >
      <div className="st-piece-inner">
        <Body piece={piece} nowMs={nowMs} tz={tz} signed={signed} />
      </div>
    </div>
  );
}
