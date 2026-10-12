"use client";

import React, { useState, useEffect, useCallback, useRef } from "react";
import { usePathname } from "next/navigation";
import { Bell, X } from "lucide-react";
import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";
import { promptPlacement } from "@/lib/chrome";
import { useComposing } from "@/lib/useComposing";
import {
  shouldShowPrompt,
  subscribeToPush,
  markAsDismissed,
  getPushStatus,
  getNotificationPermission,
} from "@/lib/notifications";

/** The CSS variable the toast viewport adds to its bottom offset (globals.css). */
const PROMPT_H = "--prompt-h";
/** The prompt's own gap above the dock (its bottom calc below). */
const PROMPT_GAP_PX = 10;

function setPromptHeight(value: string) {
  if (typeof document === "undefined") return;
  document.documentElement.style.setProperty(PROMPT_H, value);
}

/**
 * A prompt component that asks users to enable push notifications.
 *
 * Only shows when:
 * - Push notifications are supported
 * - Permission hasn't been granted/denied yet
 * - User hasn't dismissed the prompt
 * - Backend has push configured (VAPID keys set)
 */
export function NotificationPrompt() {
  const pathname = usePathname() ?? "/today";
  const composing = useComposing();
  // Never over the dock or the composer (lib/chrome.ts promptPlacement).
  const placement = promptPlacement(pathname, composing);
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  // Check and complete subscription if permission was granted (e.g., returning from iOS settings)
  const checkAndSubscribe = useCallback(async () => {
    const permission = getNotificationPermission();

    if (permission === "granted") {
      // Permission granted - complete subscription
      setLoading(true);
      const result = await subscribeToPush();
      setLoading(false);

      if (result.success) {
        setVisible(false);
      } else {
        setError(result.error || "I couldn't finish turning them on. Try once more?");
      }
    } else if (permission === "denied") {
      // Permission denied
      setLoading(false);
      setError("Notifications are blocked. You can turn them on in Settings → Notifications → Edward.");
    }
    // If still "default", keep waiting
  }, []);

  useEffect(() => {
    // Check if we should show the prompt
    const checkPrompt = async () => {
      if (!shouldShowPrompt()) {
        setVisible(false);
        return;
      }

      // Also check if backend has push configured
      const status = await getPushStatus();
      if (!status.configured) {
        setVisible(false);
        return;
      }

      setVisible(true);
    };

    checkPrompt();
  }, []);

  // Listen for visibility changes (user returning from iOS settings)
  useEffect(() => {
    if (!loading) return;

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        checkAndSubscribe();
      }
    };

    // Also check on focus (some browsers)
    const handleFocus = () => {
      checkAndSubscribe();
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    window.addEventListener("focus", handleFocus);

    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.removeEventListener("focus", handleFocus);
    };
  }, [loading, checkAndSubscribe]);

  const handleEnable = async () => {
    setLoading(true);
    setError(null);

    const result = await subscribeToPush();

    setLoading(false);

    if (result.success) {
      setVisible(false);
    } else {
      // Check if permission is actually granted (iOS workaround)
      const actualPermission = getNotificationPermission();
      if (actualPermission === "granted") {
        // Permission granted but subscription failed for another reason
        // Try one more time
        console.log("[NotificationPrompt] Retrying subscription after permission granted...");
        const retry = await subscribeToPush();
        if (retry.success) {
          setVisible(false);
          return;
        }
        setError(retry.error || "I couldn't finish turning them on. Try once more?");
      } else {
        setError(result.error || "I couldn't turn notifications on. Try once more?");
      }
    }
  };

  const handleDismiss = () => {
    markAsDismissed();
    setVisible(false);
  };

  // Above the dock the prompt and the toasts share the same spot: while it
  // shows there it tells the toast viewport its height (plus its gap), so a
  // "Logged · Undo" stacks above it instead of covering its buttons.
  const liftsToasts = visible && placement === "above-dock";
  useEffect(() => {
    const el = boxRef.current;
    if (!liftsToasts || !el) {
      setPromptHeight("0px");
      return;
    }
    const measure = () => setPromptHeight(`${Math.ceil(el.getBoundingClientRect().height) + PROMPT_GAP_PX}px`);
    measure();
    const RO = (globalThis as { ResizeObserver?: typeof ResizeObserver }).ResizeObserver;
    const ro = RO ? new RO(measure) : null;
    ro?.observe(el);
    return () => {
      ro?.disconnect();
      setPromptHeight("0px");
    };
  }, [liftsToasts, error]);

  if (!visible || placement === "hidden") return null;

  return (
    <div
      ref={boxRef}
      role="dialog"
      aria-labelledby="notif-prompt-title"
      className={cn(
        "fixed inset-x-3 z-40 md:inset-x-auto md:right-4 md:top-[68px] md:bottom-auto md:w-[360px]",
        "motion-safe:animate-fade-rise",
        placement === "top"
          ? "top-[calc(env(safe-area-inset-top)+56px)]"
          : "bottom-[calc(max(env(safe-area-inset-bottom),8px)+var(--dock-h,56px)+10px)]",
      )}
    >
      <div className="rounded-lg border border-border bg-surface-elevated p-4 shadow-e2">
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 flex-none items-center justify-center rounded-full bg-surface-3">
            <Bell className="h-5 w-5 text-text-secondary" aria-hidden />
          </div>

          <div className="min-w-0 flex-1">
            <h3 id="notif-prompt-title" className="text-callout text-text-primary">
              Let me reach you
            </h3>
            <p className="mt-0.5 text-footnote text-text-secondary">
              I&apos;ll only buzz you for what can&apos;t wait — a late dose, someone who needs an answer.
            </p>

            {error && (
              <p role="alert" className="mt-2 text-footnote text-bad">
                {error}
              </p>
            )}

            <div className="mt-3 flex gap-2">
              <Button size="sm" variant="primary" onClick={handleEnable} loading={loading} className="h-11 md:h-9">
                {loading ? "Turning on…" : "Allow notifications"}
              </Button>
              <Button size="sm" variant="ghost" onClick={handleDismiss} disabled={loading} className="h-11 md:h-9">
                Not now
              </Button>
            </div>
          </div>

          <button
            type="button"
            onClick={handleDismiss}
            className="-mr-2 -mt-2 inline-flex h-11 w-11 flex-none items-center justify-center rounded-full text-text-muted transition-colors hover:bg-surface-3 hover:text-text-primary"
            aria-label="Dismiss"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </div>
      </div>
    </div>
  );
}
