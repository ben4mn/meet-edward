"use client";

import React, { useMemo } from "react";

interface Props {
  accent?: string;
  height?: number;
}

/**
 * Faux ECG-style pulse line. Mostly flat with a sharp dip + peak + bump
 * pattern every ~90px. Memoized so the points string is computed once.
 */
export function PulseLine({ accent = "var(--ed-accent)", height = 40 }: Props) {
  const points = useMemo(() => {
    const w = 600;
    const h = 50;
    const arr: string[] = [];
    for (let x = 0; x <= w; x += 4) {
      const base = h / 2;
      const spikePos = x % 90;
      let y = base + Math.sin(x / 18) * 1.5;
      if (spikePos === 32) y = base - 18;
      else if (spikePos === 36) y = base + 14;
      else if (spikePos === 40) y = base - 4;
      arr.push(`${x},${y.toFixed(1)}`);
    }
    return arr.join(" ");
  }, []);

  return (
    <svg
      viewBox="0 0 600 50"
      width="100%"
      height={height}
      preserveAspectRatio="none"
      style={{ display: "block" }}
      aria-hidden
    >
      <polyline
        points={points}
        fill="none"
        stroke={accent}
        strokeWidth="1.2"
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{ filter: `drop-shadow(0 0 3px ${accent})` }}
      />
    </svg>
  );
}
