"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/AuthContext";

interface AuthGuardProps {
  children: React.ReactNode;
}

export function AuthGuard({ children }: AuthGuardProps) {
  const { isAuthenticated, isLoading, needsSetup } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (isLoading) return;

    // Redirect to login if not authenticated OR needs initial setup
    if (!isAuthenticated || needsSetup) {
      router.push("/login");
    }
  }, [isAuthenticated, isLoading, needsSetup, router]);

  // Show loading spinner while checking auth
  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[var(--primary-bg)]">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-[var(--terminal)]"></div>
      </div>
    );
  }

  // Only render children if authenticated (and not in setup mode)
  if (isAuthenticated && !needsSetup) {
    return <>{children}</>;
  }

  // Default: show loading while redirecting
  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--primary-bg)]">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-[var(--terminal)]"></div>
    </div>
  );
}
