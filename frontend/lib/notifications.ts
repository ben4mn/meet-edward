/**
 * Push notification utilities for Edward PWA.
 *
 * Handles subscription management, permission requests, and
 * communication with the backend push service.
 */

import { detectKind, stableDeviceId } from "./presenceBeacon";

// API URL (empty for relative URLs in browser)
const API_URL = typeof window !== "undefined" ? "" : (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000");

// Storage key for tracking notification state
const NOTIFICATION_PROMPTED_KEY = "edward-notification-prompted";
const NOTIFICATION_DISMISSED_KEY = "edward-notification-dismissed";

/**
 * Check if push notifications are supported in this browser.
 */
import { describeSubscribeError } from "./pushPanelModel";

export function isPushSupported(): boolean {
  if (typeof window === "undefined") return false;

  return (
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

/**
 * Check if we're running as a PWA (installed to home screen).
 */
export function isPWA(): boolean {
  if (typeof window === "undefined") return false;

  // Check if running in standalone mode (PWA)
  const isStandalone = window.matchMedia("(display-mode: standalone)").matches;
  // iOS Safari also uses navigator.standalone
  const isIOSStandalone = (navigator as Navigator & { standalone?: boolean }).standalone === true;

  return isStandalone || isIOSStandalone;
}

/**
 * Get the current notification permission state.
 */
export function getNotificationPermission(): NotificationPermission | "unsupported" {
  if (!isPushSupported()) return "unsupported";
  return Notification.permission;
}

/**
 * Check if the user has already been prompted for notifications.
 */
export function hasBeenPrompted(): boolean {
  if (typeof window === "undefined") return false;
  return localStorage.getItem(NOTIFICATION_PROMPTED_KEY) === "true";
}

/**
 * Mark that the user has been prompted.
 */
export function markAsPrompted(): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(NOTIFICATION_PROMPTED_KEY, "true");
}

/**
 * Check if the user dismissed the notification prompt.
 */
export function hasBeenDismissed(): boolean {
  if (typeof window === "undefined") return false;
  return localStorage.getItem(NOTIFICATION_DISMISSED_KEY) === "true";
}

/**
 * Mark that the user dismissed the prompt.
 */
export function markAsDismissed(): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(NOTIFICATION_DISMISSED_KEY, "true");
}

/**
 * Clear the dismissed state (e.g., for settings page).
 */
export function clearDismissed(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(NOTIFICATION_DISMISSED_KEY);
}

/**
 * Get push notification configuration status from backend.
 */
export async function getPushStatus(): Promise<{
  configured: boolean;
  subscriptionCount: number;
  vapidPublicKey: string | null;
  reachable: boolean;
}> {
  // `reachable: false` means the request failed — never "not configured".
  try {
    const response = await fetch(`${API_URL}/api/push/status`, {
      credentials: "include",
    });
    if (!response.ok) {
      return { configured: false, subscriptionCount: 0, vapidPublicKey: null, reachable: false };
    }
    const body = await response.json();
    return { ...body, reachable: true };
  } catch {
    return { configured: false, subscriptionCount: 0, vapidPublicKey: null, reachable: false };
  }
}

/** Resolve with `fallback` if `promise` has not settled within `ms`. */
function withTimeout<T>(promise: Promise<T>, ms: number, fallback: T): Promise<T> {
  return new Promise((resolve) => {
    const t = setTimeout(() => resolve(fallback), ms);
    promise.then((v) => { clearTimeout(t); resolve(v); }, () => { clearTimeout(t); resolve(fallback); });
  });
}

/**
 * The service worker registration, registering `/sw.js` ourselves when
 * nothing is registered yet (a freshly added iOS Home Screen app taps
 * Subscribe before the layout's delayed registration has run). Returns
 * null when no worker becomes active within `waitMs`.
 */
async function ensureServiceWorker(waitMs = 10000): Promise<ServiceWorkerRegistration | null> {
  if (!("serviceWorker" in navigator)) return null;
  let registration = await navigator.serviceWorker.getRegistration();
  if (!registration) {
    console.log("[Push] No registration yet — registering /sw.js");
    try {
      registration = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
    } catch (e) {
      console.log("[Push] Registration failed:", e);
      return null;
    }
  }
  if (registration.active) return registration;
  return withTimeout(navigator.serviceWorker.ready, waitMs, null as ServiceWorkerRegistration | null);
}

/**
 * Get the VAPID public key from the backend.
 */
async function getVapidPublicKey(): Promise<string | null> {
  try {
    const response = await fetch(`${API_URL}/api/push/vapid-key`, {
      credentials: "include",
    });
    if (!response.ok) return null;
    const data = await response.json();
    return data.vapidPublicKey;
  } catch {
    return null;
  }
}

/**
 * Convert a base64 VAPID key to Uint8Array for subscription.
 */
function urlBase64ToUint8Array(base64String: string): BufferSource {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding)
    .replace(/-/g, "+")
    .replace(/_/g, "/");

  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);

  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  // Return the underlying ArrayBuffer to satisfy BufferSource type
  return outputArray.buffer;
}

/**
 * Subscribe to push notifications.
 *
 * This requests permission if needed, subscribes via the PushManager,
 * and sends the subscription to our backend.
 */
export async function subscribeToPush(): Promise<{
  success: boolean;
  error?: string;
}> {
  console.log("[Push] Starting subscription flow");

  if (!isPushSupported()) {
    console.log("[Push] Not supported");
    return { success: false, error: "Push notifications not supported" };
  }

  try {
    // Check current permission state first
    let permission = Notification.permission;
    console.log("[Push] Current permission:", permission);

    // Only request if not already decided
    if (permission === "default") {
      console.log("[Push] Requesting permission...");
      // On iOS, this might open settings and the promise may not resolve correctly
      // We'll handle that case by checking permission state after
      try {
        permission = await Notification.requestPermission();
        console.log("[Push] Permission request result:", permission);
      } catch (e) {
        console.log("[Push] Permission request error:", e);
        // Check permission state directly as fallback
        permission = Notification.permission;
        console.log("[Push] Fallback permission check:", permission);
      }
    }

    markAsPrompted();

    // Re-check permission state (iOS fix - the promise might lie)
    const actualPermission = Notification.permission;
    console.log("[Push] Actual permission state:", actualPermission);

    if (actualPermission !== "granted") {
      return { success: false, error: "Permission denied" };
    }

    // Get VAPID key from backend
    console.log("[Push] Getting VAPID key...");
    const vapidPublicKey = await getVapidPublicKey();
    if (!vapidPublicKey) {
      console.log("[Push] No VAPID key from server");
      return { success: false, error: "Push not configured on server" };
    }
    console.log("[Push] Got VAPID key:", vapidPublicKey.substring(0, 20) + "...");

    // Get (or create) the service worker registration, bounded.
    console.log("[Push] Waiting for service worker...");
    const registration = await ensureServiceWorker();
    if (!registration) {
      return { success: false, error: "Service worker did not become active (10 s). Close and reopen the app, then retry." };
    }
    console.log("[Push] Service worker ready, scope:", registration.scope);

    // Subscribe to push. A subscription made with a different server key
    // makes the browser refuse; drop it and try once more.
    console.log("[Push] Subscribing to push manager...");
    const options = { userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(vapidPublicKey) };
    let subscription: PushSubscription;
    try {
      subscription = await registration.pushManager.subscribe(options);
    } catch (e) {
      const existing = await registration.pushManager.getSubscription().catch(() => null);
      if (!existing) throw e;
      console.log("[Push] Subscribe refused with an existing subscription; replacing it:", e);
      await existing.unsubscribe().catch(() => undefined);
      subscription = await registration.pushManager.subscribe(options);
    }
    console.log("[Push] Got subscription, endpoint:", subscription.endpoint.substring(0, 50) + "...");

    // Helper to convert ArrayBuffer to base64url
    const arrayBufferToBase64Url = (buffer: ArrayBuffer | null): string => {
      if (!buffer) return "";
      const bytes = new Uint8Array(buffer);
      let binary = "";
      for (let i = 0; i < bytes.length; i++) {
        binary += String.fromCharCode(bytes[i]);
      }
      return btoa(binary)
        .replace(/\+/g, "-")
        .replace(/\//g, "_")
        .replace(/=/g, "");
    };

    const p256dhKey = arrayBufferToBase64Url(subscription.getKey("p256dh"));
    const authKey = arrayBufferToBase64Url(subscription.getKey("auth"));
    console.log("[Push] Keys extracted, p256dh length:", p256dhKey.length, "auth length:", authKey.length);

    // Send subscription to backend
    console.log("[Push] Sending subscription to backend...");
    const response = await fetch(`${API_URL}/api/push/subscribe`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      credentials: "include",
      body: JSON.stringify({
        endpoint: subscription.endpoint,
        keys: {
          p256dh: p256dhKey,
          auth: authKey,
        },
        // Device identity so Edward can target this device (Session E).
        device_id: stableDeviceId(detectKind(navigator.userAgent, (navigator as { standalone?: boolean }).standalone), localStorage),
        device_name: `${detectKind(navigator.userAgent, (navigator as { standalone?: boolean }).standalone)} · ${navigator.platform || "browser"}`,
        kind: detectKind(navigator.userAgent, (navigator as { standalone?: boolean }).standalone),
      }),
    });

    console.log("[Push] Backend response status:", response.status);

    if (!response.ok) {
      const errorText = await response.text();
      console.log("[Push] Backend error:", errorText);
      throw new Error(`Failed to save subscription: ${errorText}`);
    }

    const result = await response.json();
    console.log("[Push] Subscription saved:", result);

    return { success: true };
  } catch (error) {
    console.error("[Push] Subscription error:", error);
    return {
      success: false,
      error: describeSubscribeError(error),
    };
  }
}

/**
 * Unsubscribe from push notifications.
 */
export async function unsubscribeFromPush(): Promise<{
  success: boolean;
  error?: string;
}> {
  if (!isPushSupported()) {
    return { success: false, error: "Push notifications not supported" };
  }

  try {
    const registration = await navigator.serviceWorker.ready;
    const subscription = await registration.pushManager.getSubscription();

    if (!subscription) {
      return { success: true }; // Already unsubscribed
    }

    // Unsubscribe locally
    await subscription.unsubscribe();

    // Notify backend
    await fetch(`${API_URL}/api/push/unsubscribe`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      credentials: "include",
      body: JSON.stringify({
        endpoint: subscription.endpoint,
      }),
    });

    return { success: true };
  } catch (error) {
    console.error("Push unsubscribe error:", error);
    return {
      success: false,
      error: error instanceof Error ? error.message : "Unknown error",
    };
  }
}

/**
 * Check if currently subscribed to push notifications.
 */
export async function isSubscribed(): Promise<boolean> {
  if (!isPushSupported()) return false;

  try {
    // Never await `ready` unbounded: with no active worker it never settles
    // and the panel used to hang here before asking the server anything.
    const registration = await navigator.serviceWorker.getRegistration();
    if (!registration) return false;
    const active = registration.active
      ? registration
      : await withTimeout(navigator.serviceWorker.ready, 3000, null as ServiceWorkerRegistration | null);
    if (!active) return false;
    const subscription = await active.pushManager.getSubscription();
    return subscription !== null;
  } catch {
    return false;
  }
}

/**
 * Determine if we should show the notification prompt.
 *
 * Shows prompt if:
 * - Push is supported
 * - Permission not yet granted or denied
 * - User hasn't dismissed the prompt
 * - Running as PWA OR on iOS Safari 16.4+
 */
export function shouldShowPrompt(): boolean {
  if (!isPushSupported()) return false;

  const permission = getNotificationPermission();

  // Already granted or denied
  if (permission === "granted" || permission === "denied") return false;

  // User dismissed the prompt
  if (hasBeenDismissed()) return false;

  // On iOS, push only works if installed as PWA
  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
  if (isIOS && !isPWA()) return false;

  return true;
}
