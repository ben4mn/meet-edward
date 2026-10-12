"use client";

import { useCallback, useEffect, useState } from "react";
import { WORK_PREF_KEY, parseWorkPref, type WorkPref } from "@/lib/workRail";

const EVENT = "edward:workDisplay-change";

function read(): WorkPref {
  try {
    return parseWorkPref(localStorage.getItem(WORK_PREF_KEY));
  } catch {
    return "collapse";
  }
}

/** How much of Edward's work the chat shows. Per device, in localStorage,
 *  kept in sync across every mounted rail via a window event. */
export function useWorkPref(): [WorkPref, (p: WorkPref) => void] {
  const [pref, setPrefState] = useState<WorkPref>("collapse");

  useEffect(() => {
    setPrefState(read());
    const onChange = () => setPrefState(read());
    window.addEventListener(EVENT, onChange);
    window.addEventListener("storage", onChange);
    return () => {
      window.removeEventListener(EVENT, onChange);
      window.removeEventListener("storage", onChange);
    };
  }, []);

  const setPref = useCallback((p: WorkPref) => {
    try {
      localStorage.setItem(WORK_PREF_KEY, p);
    } catch {
      /* private mode: in-memory only */
    }
    setPrefState(p);
    window.dispatchEvent(new Event(EVENT));
  }, []);

  return [pref, setPref];
}
