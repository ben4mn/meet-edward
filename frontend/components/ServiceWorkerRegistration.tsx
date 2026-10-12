"use client";

import { useEffect } from "react";

/**
 * Registers the service worker for PWA functionality.
 * next-pwa should do this automatically, but we add manual registration
 * as a fallback for development mode and iOS Safari.
 */
export function ServiceWorkerRegistration() {
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!("serviceWorker" in navigator)) {
      console.log("[SW] Service workers not supported");
      return;
    }

    // Ask the browser to look for a new build whenever the app comes back
    // to the foreground. Installed PWAs otherwise check only on a cold
    // start (at most once a day), and would keep serving the old bundle.
    const checkForUpdate = () => {
      if (document.visibilityState !== "visible") return;
      navigator.serviceWorker.getRegistration().then((r) => r?.update().catch(() => {}));
    };
    document.addEventListener("visibilitychange", checkForUpdate);

    const registerSW = async () => {
      try {
        // Check if already registered
        const registrations = await navigator.serviceWorker.getRegistrations();
        if (registrations.length > 0) {
          console.log("[SW] Already registered:", registrations.length, "registration(s)");
          registrations[0].update().catch(() => {});
          return;
        }

        console.log("[SW] Registering service worker...");
        const registration = await navigator.serviceWorker.register("/sw.js", {
          scope: "/",
        });

        console.log("[SW] Registered successfully, scope:", registration.scope);

        // Wait for the service worker to be ready
        registration.addEventListener("updatefound", () => {
          console.log("[SW] Update found, new worker installing...");
        });

        if (registration.installing) {
          console.log("[SW] Installing...");
        } else if (registration.waiting) {
          console.log("[SW] Waiting...");
        } else if (registration.active) {
          console.log("[SW] Active");
        }
      } catch (error) {
        console.error("[SW] Registration failed:", error);
      }
    };

    // Register after a short delay to not block initial render
    const timer = setTimeout(registerSW, 1000);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", checkForUpdate);
    };
  }, []);

  return null;
}
