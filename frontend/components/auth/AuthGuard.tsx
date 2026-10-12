"use client";

// Waits for the server's verdict on the session, then renders the app or
// sends the owner to /login. The wait is a still Edward and his pulse — never a
// spinner (design system §3 "Loading is Edward").
//
// An unreachable server is not a verdict (lib/AuthContext.tsx): with the
// optimistic hint the app renders (presence says Offline); without it the
// wait says so, in his voice, while AuthProvider keeps retrying. Only a real
// "signed out" (or a password to set) goes to /login.
//
// Leaf imports, not the components/ui barrel: the barrel re-exports Sheet
// and Switch, which pull framer-motion into /hub, /screen and /login.

import React, { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/AuthContext";
import { EdwardAvatar } from "@/components/EdwardAvatar";
import { Pulse } from "@/components/ui/Pulse";

interface AuthGuardProps {
  children: React.ReactNode;
}

function Waiting({ label, showLabel = false }: { label: string; showLabel?: boolean }) {
  return (
    <div className="flex min-h-dvh flex-col items-center justify-center gap-5 bg-primary-bg px-6 text-center">
      <EdwardAvatar size="lg" animated={false} aria-label="Edward" />
      <Pulse label={label} showLabel={showLabel} />
    </div>
  );
}

export function AuthGuard({ children }: AuthGuardProps) {
  const { isAuthenticated, isLoading, needsSetup, unreachable } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (isLoading) return;

    // Redirect to login if not authenticated OR needs initial setup
    if (!isAuthenticated || needsSetup) {
      const dest = window.location.pathname + window.location.search;
      const loginUrl = dest && dest !== "/" && dest !== "/login"
        ? `/login?redirect=${encodeURIComponent(dest)}`
        : "/login";
      router.push(loginUrl);
    }
  }, [isAuthenticated, isLoading, needsSetup, router]);

  if (isLoading && unreachable) {
    return <Waiting label="I can't reach the Mac right now — trying again" showLabel />;
  }
  if (isLoading) return <Waiting label="Checking it's you" />;

  // Only render children if authenticated (and not in setup mode)
  if (isAuthenticated && !needsSetup) {
    return <>{children}</>;
  }

  // Redirecting to /login.
  return <Waiting label="Taking you to sign in" />;
}
