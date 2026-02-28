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

    const registerSW = async () => {
      try {
        // Check if already registered
        const registrations = await navigator.serviceWorker.getRegistrations();
        if (registrations.length > 0) {
          console.log("[SW] Already registered:", registrations.length, "registration(s)");
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
    return () => clearTimeout(timer);
  }, []);

  return null;
}
