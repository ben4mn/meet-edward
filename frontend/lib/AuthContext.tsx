"use client";

import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { getAuthStatus, login as apiLogin, setupPassword as apiSetup, logout as apiLogout } from "./api";
import {
  SIGNED_OUT_EVENT,
  authRetryDelayMs,
  authVerdict,
  clearSessionContent,
  readAuthHint,
  writeAuthHint,
} from "./authSession";
import { setOwnerName } from "./owner";

interface AuthContextType {
  isAuthenticated: boolean;
  isLoading: boolean;
  /** True once the server has answered — the optimistic hint is not this. */
  authChecked: boolean;
  needsSetup: boolean;
  /** The setup form asks for the one-time code in backend/.setup_code. */
  setupCodeRequired: boolean;
  /** The last status check could not reach the server (a restart, the
   *  tunnel): no verdict yet, retrying with backoff. Not "signed out". */
  unreachable: boolean;
  login: (password: string) => Promise<void>;
  setup: (password: string, setupCode?: string) => Promise<void>;
  /** Throws when the server did not confirm; the session then stands. */
  logout: () => Promise<void>;
  checkAuth: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

// The optimistic hint (avoids a spinner flash) lives in localStorage so it
// survives iOS PWA cold launches; read/write through lib/authSession.ts so a
// 401 anywhere can clear it. checkAuth() corrects it as soon as the server
// answers, and `authChecked` says when that has happened.
//
// 2026-09-26: only a real answer is a verdict. A status call that fails (a
// 500 while uvicorn restarts behind a live Next, the tunnel's 502, a
// network error) keeps the hint and the state as they are, leaves
// `authChecked` false (so the login page never leaves on a guess) and
// retries with backoff — also on `online` and when the tab comes back. With
// the hint the shell renders and presence says Offline; without it AuthGuard
// waits and says it can't reach Edward. /login gets the same retries, so its
// form leaves by itself once the server says the session is fine.
function hasAuthHint(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return readAuthHint(window.localStorage);
  } catch {
    return false;
  }
}

function storage() {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export interface AuthProviderProps {
  children: React.ReactNode;
  /** Backoff after an unreachable status check (tests); authRetryDelayMs by default. */
  retryDelayMs?: (attempt: number) => number;
}

export function AuthProvider({ children, retryDelayMs = authRetryDelayMs }: AuthProviderProps) {
  // Initialize with server-safe defaults to avoid hydration mismatch
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [authChecked, setAuthChecked] = useState(false);
  const [needsSetup, setNeedsSetup] = useState(false);
  const [setupCodeRequired, setSetupCodeRequired] = useState(false);
  const [unreachable, setUnreachable] = useState(false);

  const alive = useRef(true);
  const attempt = useRef(0);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const delayRef = useRef(retryDelayMs);
  delayRef.current = retryDelayMs;
  const checkRef = useRef<() => Promise<void>>(async () => {});

  const clearRetry = () => {
    if (retryTimer.current) clearTimeout(retryTimer.current);
    retryTimer.current = null;
  };

  const checkAuth = useCallback(async () => {
    clearRetry();
    let verdict: ReturnType<typeof authVerdict> = null;
    try {
      verdict = authVerdict(await getAuthStatus());
    } catch {
      verdict = null; // a 5xx, a proxy page, the network: unreachable
    }
    if (!alive.current) return;
    if (!verdict) {
      setUnreachable(true);
      clearRetry();
      const wait = delayRef.current(attempt.current);
      attempt.current += 1;
      retryTimer.current = setTimeout(() => {
        retryTimer.current = null;
        void checkRef.current();
      }, wait);
      return;
    }
    attempt.current = 0;
    setUnreachable(false);
    setOwnerName(verdict.owner_name);
    setNeedsSetup(!verdict.configured);
    setSetupCodeRequired(!verdict.configured && verdict.setup_code_required === true);
    setIsAuthenticated(verdict.authenticated);
    writeAuthHint(storage(), verdict.authenticated);
    setAuthChecked(true);
    setIsLoading(false);
  }, []);
  checkRef.current = checkAuth;

  useEffect(() => {
    alive.current = true;
    // Optimistic: set auth from hint before server check resolves
    if (hasAuthHint()) {
      setIsAuthenticated(true);
      setIsLoading(false);
    }
    void checkAuth();
    return () => {
      alive.current = false;
      clearRetry();
    };
  }, [checkAuth]);

  // While unreachable, try again the moment the network or the tab returns.
  useEffect(() => {
    if (!unreachable) return;
    const again = () => {
      if (document.visibilityState === "hidden") return;
      attempt.current = 0;
      void checkRef.current();
    };
    window.addEventListener("online", again);
    document.addEventListener("visibilitychange", again);
    return () => {
      window.removeEventListener("online", again);
      document.removeEventListener("visibilitychange", again);
    };
  }, [unreachable]);

  const login = async (password: string) => {
    await apiLogin(password);
    clearRetry();
    setUnreachable(false);
    setIsAuthenticated(true);
    setAuthChecked(true);
    setNeedsSetup(false);
    setIsLoading(false);
    writeAuthHint(storage(), true);
  };

  const setup = async (password: string, setupCode?: string) => {
    await apiSetup(password, setupCode);
    clearRetry();
    setUnreachable(false);
    setIsAuthenticated(true);
    setAuthChecked(true);
    setNeedsSetup(false);
    setSetupCodeRequired(false);
    setIsLoading(false);
    writeAuthHint(storage(), true);
  };

  // Only once the server confirms: a sign-out that never reached the server
  // leaves the cookie alive, so claiming "signed out" would be false. On
  // success nothing of the owner's stays in the tab — storage now, module caches
  // (the Today feed) through SIGNED_OUT_EVENT, before the caller navigates.
  const logout = async () => {
    await apiLogout();
    clearRetry();
    clearSessionContent();
    try {
      window.dispatchEvent(new window.CustomEvent(SIGNED_OUT_EVENT));
    } catch {
      /* no window */
    }
    setIsAuthenticated(false);
    writeAuthHint(storage(), false);
  };

  return (
    <AuthContext.Provider
      value={{
        isAuthenticated,
        isLoading,
        authChecked,
        needsSetup,
        setupCodeRequired,
        unreachable,
        login,
        setup,
        logout,
        checkAuth,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
