"use client";

import React, { createContext, useCallback, useContext, useEffect, useRef, useState, ReactNode } from "react";
import { Sun, Moon } from "lucide-react";
import { applyTheme, readInitialTheme, storeTheme, syncThemeColor, type Theme } from "@/lib/themeColors";

export type { Theme };

interface ThemeContextType {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  toggleTheme: () => void;
  mounted: boolean;
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>("dark");
  const [mounted, setMounted] = useState(false);
  const current = useRef<Theme>("dark");

  // First paint: the boot script in <head> already set the class from the
  // same rule (System follows the device; else the stored choice, else
  // dark); adopt it and make sure the status-bar colour matches. No fade.
  useEffect(() => {
    const initial: Theme = readInitialTheme();
    current.current = initial;
    setThemeState(initial);
    applyTheme(document, initial);
    setMounted(true);
  }, []);

  // Next's app router remounts <head> on a segment change and puts its
  // per-scheme theme-color defaults back (app/layout.tsx viewport), so after
  // the first tab switch a Light choice on a dark phone got a dark status
  // bar. Re-apply the owner's theme whenever a theme-color meta is inserted.
  // childList only: syncThemeColor's setAttribute cannot loop back here.
  useEffect(() => {
    const head = document.head;
    if (!head || typeof MutationObserver === "undefined") return;
    const obs = new MutationObserver((records) => {
      const added = records.some((r) =>
        Array.from(r.addedNodes).some(
          (n) => n.nodeName === "META" && (n as HTMLMetaElement).name === "theme-color",
        ),
      );
      if (added) syncThemeColor(document, current.current);
    });
    obs.observe(head, { childList: true });
    return () => obs.disconnect();
  }, []);

  const setTheme = useCallback((next: Theme) => {
    current.current = next;
    setThemeState(next);
    applyTheme(document, next, { animate: true });
    storeTheme(next);
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme(current.current === "dark" ? "light" : "dark");
  }, [setTheme]);

  return (
    <ThemeContext.Provider value={{ theme, setTheme, toggleTheme, mounted }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (context === undefined) {
    throw new Error("useTheme must be used within a ThemeProvider");
  }
  return context;
}

export function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();

  return (
    <button
      type="button"
      onClick={toggleTheme}
      className="inline-flex h-11 w-11 items-center justify-center rounded-md text-text-secondary transition-colors duration-fast hover:bg-surface-3 hover:text-text-primary active:scale-[.97]"
      aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
    >
      {theme === "dark" ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
    </button>
  );
}
