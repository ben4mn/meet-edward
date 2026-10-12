"use client";

// Moved to lib/useWakeLock.ts so /chat can hold the phone awake too.
export { useWakeLock, type WakeLockState } from "@/lib/useWakeLock";
