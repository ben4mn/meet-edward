"use client";

// Chat's tool labels for the thought lane's receipts (lib/toolLabels.ts),
// loaded only once a turn is really running on the stage: the label table
// is Chat's, and /screen's first load has a 145 kB budget (2026-09-28).
// Until it lands a receipt reads as the tool's name in plain words.

import { useEffect, useState } from "react";

import type { DescribeTool } from "../lib/thinkingModel";

let loaded: DescribeTool | undefined;

export function useToolLabels(wanted: boolean): DescribeTool | undefined {
  const [describe, setDescribe] = useState<DescribeTool | undefined>(() => loaded);
  useEffect(() => {
    if (!wanted || describe) return;
    let live = true;
    import("@/lib/toolLabels")
      .then((m) => {
        loaded = m.describeTool;
        if (live) setDescribe(() => m.describeTool);
      })
      .catch(() => {
        /* the plain names stay */
      });
    return () => {
      live = false;
    };
  }, [wanted, describe]);
  return describe;
}
