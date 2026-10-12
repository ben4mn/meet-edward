"use client";

// Tiny strip at the very bottom of the display showing the compose-bar
// keyboard shortcuts. Visible by default, fades after 10s of no key
// activity, returns the moment any key is pressed.

import { useEffect, useState } from "react";

const HIDE_AFTER_MS = 10_000;

const HINTS: { key: string; label: string }[] = [
  { key: "/", label: "focus" },
  { key: "⏎", label: "send" },
  { key: "⎋", label: "clear" },
  { key: "⇧⏎", label: "newline" },
];

export function KeyboardHints() {
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    let timer: number | null = null;

    const reset = () => {
      setVisible(true);
      if (timer !== null) window.clearTimeout(timer);
      timer = window.setTimeout(() => setVisible(false), HIDE_AFTER_MS);
    };

    reset();
    document.addEventListener("keydown", reset);
    return () => {
      document.removeEventListener("keydown", reset);
      if (timer !== null) window.clearTimeout(timer);
    };
  }, []);

  return (
    <div
      style={{
        position: "fixed",
        bottom: 6,
        left: 0,
        right: 0,
        display: "flex",
        justifyContent: "center",
        gap: 14,
        fontSize: 10,
        fontFamily: "var(--ed-mono)",
        color: "var(--ed-text-faint)",
        opacity: visible ? 0.8 : 0,
        transition: "opacity 600ms ease",
        pointerEvents: "none",
        zIndex: 4,
        letterSpacing: "0.04em",
      }}
      aria-hidden
    >
      {HINTS.map((h) => (
        <span key={h.label} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
          <kbd
            style={{
              fontFamily: "var(--ed-mono)",
              fontSize: 10,
              padding: "1px 5px",
              border: "1px solid var(--ed-border)",
              borderRadius: 3,
              background: "rgba(0,0,0,0.4)",
              color: "var(--ed-text-dim)",
            }}
          >
            {h.key}
          </kbd>
          <span>{h.label}</span>
        </span>
      ))}
    </div>
  );
}
