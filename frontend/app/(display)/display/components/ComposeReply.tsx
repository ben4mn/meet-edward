"use client";

// What the /screen compose bar's reply card draws (round 2, 2026-09-28): his
// thinking as clean lines (a reasoning summary's heading its own line, the
// stream's "Thinking..." placeholders gone) and the answer's light markdown —
// bold, bullets, paragraphs — as React nodes. The parsing is pure
// (lib/composeBarController.ts thinkingLines / replyBlocks); no HTML strings
// and no markdown library on /screen's first load.

import React from "react";

import { replyBlocks, thinkingLines, type ReplySpan } from "../lib/composeBarController";

function Spans({ spans }: { spans: ReplySpan[] }) {
  return (
    <>
      {spans.map((s, i) => (s.bold ? <strong key={i} style={{ fontWeight: 600, color: "var(--ed-text)" }}>{s.text}</strong> : <React.Fragment key={i}>{s.text}</React.Fragment>))}
    </>
  );
}

export function ComposeThinking({ thinking }: { thinking: string }) {
  const lines = thinkingLines(thinking);
  if (!lines.length) return null;
  return (
    <div data-thinking style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12, lineHeight: 1.5 }}>
      {lines.map((l, i) => (
        <div
          key={i}
          data-think-line
          data-heading={l.heading ? "true" : undefined}
          style={{ color: l.heading ? "var(--ed-text-dim)" : "var(--ed-text-faint)", fontWeight: l.heading ? 500 : 400 }}
        >
          {l.text}
        </div>
      ))}
    </div>
  );
}

export function ComposeAnswer({ reply }: { reply: string }) {
  const blocks = replyBlocks(reply);
  return (
    <div data-reply>
      {blocks.map((b, i) =>
        b.kind === "ul" ? (
          <ul key={i} style={{ margin: "0 0 8px", paddingLeft: 18, listStyle: "disc" }}>
            {b.items.map((item, j) => (
              <li key={j} style={{ margin: "2px 0" }}>
                <Spans spans={item} />
              </li>
            ))}
          </ul>
        ) : (
          <p key={i} style={{ margin: "0 0 8px" }}>
            {b.lines.map((line, j) => (
              <React.Fragment key={j}>
                {j > 0 && <br />}
                <Spans spans={line} />
              </React.Fragment>
            ))}
          </p>
        ),
      )}
    </div>
  );
}
