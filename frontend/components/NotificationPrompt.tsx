"use client";

import { useState, useEffect, useCallback } from "react";
import { Bell, X } from "lucide-react";
import {
  shouldShowPrompt,
  subscribeToPush,
  markAsDismissed,
  getPushStatus,
  getNotificationPermission,
} from "@/lib/notifications";

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
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
        setError(result.error || "Failed to complete subscription");
      }
    } else if (permission === "denied") {
      // Permission denied
      setLoading(false);
      setError("Notifications were denied. You can enable them in your browser/device settings.");
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
        setError(retry.error || "Failed to complete subscription");
      } else {
        setError(result.error || "Failed to enable notifications");
      }
    }
  };

  const handleDismiss = () => {
    markAsDismissed();
    setVisible(false);
  };

  if (!visible) return null;

  return (
    <div className="fixed bottom-4 left-4 right-4 md:left-auto md:right-4 md:w-96 z-50">
      <div className="bg-surface border border-border rounded-lg shadow-lg p-4">
        <div className="flex items-start gap-3">
          <div className="flex-shrink-0 w-10 h-10 rounded-full bg-terminal/10 flex items-center justify-center">
            <Bell className="w-5 h-5 text-terminal" />
          </div>

          <div className="flex-1 min-w-0">
            <h3 className="text-sm font-medium text-text-primary">
              Enable notifications
            </h3>
            <p className="mt-1 text-xs text-text-secondary">
              Get notified about scheduled reminders, incoming messages, and when Edward needs your attention.
            </p>

            {error && (
              <p className="mt-2 text-xs text-red-400">{error}</p>
            )}

            <div className="mt-3 flex gap-2">
              <button
                onClick={handleEnable}
                disabled={loading}
                className="px-3 py-1.5 text-xs font-medium rounded bg-terminal text-white hover:bg-terminal/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {loading ? "Enabling..." : "Enable"}
              </button>
              <button
                onClick={handleDismiss}
                disabled={loading}
                className="px-3 py-1.5 text-xs font-medium rounded text-text-secondary hover:text-text-primary hover:bg-surface-hover transition-colors"
              >
                Not now
              </button>
            </div>
          </div>

          <button
            onClick={handleDismiss}
            className="flex-shrink-0 p-1 rounded hover:bg-surface-hover transition-colors"
            aria-label="Dismiss"
          >
            <X className="w-4 h-4 text-text-secondary" />
          </button>
        </div>
      </div>
    </div>
  );
}
