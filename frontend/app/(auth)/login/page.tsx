"use client";

// The door (docs/design-system-2026-09-25.md §4 "login"). The form, Edward
// and all, is in LoginForm.tsx.
//
// The form is its own chunk (2026-09-26 perf pass): it reads the search
// params, so the server never drew it anyway — the prerendered page is this
// shell and the pulse below. Its fetch starts as this module runs, in
// parallel with hydration; until it lands the pulse the server drew stays
// (lib/lazyComponent), so nothing on screen changes.

import React, { Suspense } from "react";
import { Pulse } from "@/components/ui/Pulse";
import { lazyComponent } from "@/lib/lazyComponent";

function Opening() {
  return <Pulse center label="Opening the door" />;
}

const LoginForm = lazyComponent(() => import("./LoginForm").then((mod) => mod.LoginForm), Opening);
if (typeof window !== "undefined") {
  LoginForm.preload().catch(() => {
    /* retried when the page mounts it */
  });
}

export default function LoginPage() {
  return (
    <main className="relative flex min-h-dvh flex-col items-center justify-center overflow-hidden bg-primary-bg px-6 pb-[max(env(safe-area-inset-bottom),24px)] pt-[max(env(safe-area-inset-top),24px)]">
      <Suspense fallback={<Opening />}>
        <LoginForm />
      </Suspense>
    </main>
  );
}
