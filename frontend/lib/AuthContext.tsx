"use client";

import React, { createContext, useContext, useState, useEffect, useCallback } from "react";
import { getAuthStatus, login as apiLogin, setupPassword as apiSetup, logout as apiLogout } from "./api";

interface AuthContextType {
  isAuthenticated: boolean;
  isLoading: boolean;
  needsSetup: boolean;
  login: (password: string) => Promise<void>;
  setup: (password: string) => Promise<void>;
  logout: () => Promise<void>;
  checkAuth: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

// Check if auth hint exists (optimistic check to avoid spinner flash).
// Uses localStorage so the hint survives iOS PWA cold launches
// (sessionStorage is wiped each time the PWA is reopened).
// checkAuth() corrects the hint immediately if the cookie has expired.
function hasAuthHint(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return localStorage.getItem("edward_auth_hint") === "1";
  } catch {
    return false;
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  // Optimistic: if we have the hint, assume authenticated until proven otherwise
  const hint = hasAuthHint();
  const [isAuthenticated, setIsAuthenticated] = useState(hint);
  const [isLoading, setIsLoading] = useState(!hint); // Skip loading if hint exists
  const [needsSetup, setNeedsSetup] = useState(false);

  const checkAuth = useCallback(async () => {
    try {
      const status = await getAuthStatus();
      setNeedsSetup(!status.configured);
      setIsAuthenticated(status.authenticated);
      // Update hint
      try {
        if (status.authenticated) {
          localStorage.setItem("edward_auth_hint", "1");
        } else {
          localStorage.removeItem("edward_auth_hint");
        }
      } catch { /* localStorage unavailable */ }
    } catch {
      setIsAuthenticated(false);
      try { localStorage.removeItem("edward_auth_hint"); } catch {}
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  const login = async (password: string) => {
    await apiLogin(password);
    setIsAuthenticated(true);
    setNeedsSetup(false);
    try { localStorage.setItem("edward_auth_hint", "1"); } catch {}
  };

  const setup = async (password: string) => {
    await apiSetup(password);
    setIsAuthenticated(true);
    setNeedsSetup(false);
    try { localStorage.setItem("edward_auth_hint", "1"); } catch {}
  };

  const logout = async () => {
    await apiLogout();
    setIsAuthenticated(false);
    try { localStorage.removeItem("edward_auth_hint"); } catch {}
  };

  return (
    <AuthContext.Provider
      value={{
        isAuthenticated,
        isLoading,
        needsSetup,
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
