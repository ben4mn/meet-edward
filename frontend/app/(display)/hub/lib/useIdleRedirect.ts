"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef } from "react";

/**
 * Drop to the ambient screensaver after a stretch of no interaction.
 *
 * Deliberately generous: this screen sits by the pet station, so someone
 * standing in front of it reading tonight's doses should not have it
 * vanish mid-sentence.
 */
export function useIdleRedirect(afterMs = 120_000, to = "/hub/idle") {
  const router = useRouter();
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    router.prefetch(to);

    const reset = () => {
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => router.replace(to), afterMs);
    };

    const opts = { passive: true } as const;
    window.addEventListener("pointerdown", reset, opts);
    window.addEventListener("keydown", reset);
    window.addEventListener("touchstart", reset, opts);
    reset();

    return () => {
      if (timer.current) clearTimeout(timer.current);
      window.removeEventListener("pointerdown", reset);
      window.removeEventListener("keydown", reset);
      window.removeEventListener("touchstart", reset);
    };
  }, [router, afterMs, to]);
}
