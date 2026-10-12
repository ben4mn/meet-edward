// Theme plumbing shared by the root layout (server) and ThemeProvider
// (client). Pure DOM helpers, no React, so node:test can pin them.
//
// docs/design-system-2026-09-25.md §5: theme-color = the canvas of the
// theme on screen (the iPhone status bar and the PWA title bar), updated
// when the owner toggles. A theme switch cross-fades through a brief
// `html.theme-transition` class (globals.css) instead of a global
// transition rule on every element.

export type Theme = "light" | "dark";

export const THEME_STORAGE_KEY = "edward-theme";
/** Light / Dark / System (lib/themeMode.ts); kept here so the boot script
 *  and ThemeProvider can resolve System on the very first paint. */
export const THEME_MODE_KEY = "edward-theme-mode";

/** The canvas (`--primary-bg`) of each theme. Keep in step with globals.css. */
export const THEME_CANVAS: Readonly<Record<Theme, string>> = {
  dark: "#0a0e14",
  light: "#f5f6f8",
};

/** How long `html.theme-transition` stays on (the CSS fade is 240ms). */
export const THEME_TRANSITION_MS = 320;

/** Next's `viewport.themeColor`: the canvas per system scheme, used until
 *  the boot script and ThemeProvider replace it with the stored choice. */
export function themeColorViewport(): { media: string; color: string }[] {
  return [
    { media: "(prefers-color-scheme: dark)", color: THEME_CANVAS.dark },
    { media: "(prefers-color-scheme: light)", color: THEME_CANVAS.light },
  ];
}

/** Every `<meta name="theme-color">` takes the canvas of `theme`; one is
 *  created when the page has none. */
export function syncThemeColor(doc: Document, theme: Theme): void {
  let metas = Array.from(doc.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]'));
  if (metas.length === 0) {
    const meta = doc.createElement("meta");
    meta.name = "theme-color";
    doc.head.appendChild(meta);
    metas = [meta];
  }
  for (const meta of metas) meta.setAttribute("content", THEME_CANVAS[theme]);
}

let transitionTimer: ReturnType<typeof setTimeout> | undefined;

/** Put `theme` on the document: the `.dark` class and the status-bar colour.
 *  With `animate`, colours cross-fade for THEME_TRANSITION_MS. */
export function applyTheme(doc: Document, theme: Theme, opts: { animate?: boolean } = {}): void {
  const root = doc.documentElement;
  if (opts.animate) {
    root.classList.add("theme-transition");
    if (transitionTimer) clearTimeout(transitionTimer);
    transitionTimer = setTimeout(() => {
      root.classList.remove("theme-transition");
      transitionTimer = undefined;
    }, THEME_TRANSITION_MS);
  }
  root.classList.toggle("dark", theme === "dark");
  syncThemeColor(doc, theme);
}

export function readStoredTheme(): Theme | null {
  try {
    const v = localStorage.getItem(THEME_STORAGE_KEY);
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null;
  }
}

/** The theme for the first paint: a System choice follows the device now
 *  (2026-09-26: it painted the last resolved theme, then cross-faded ~0.7 s
 *  later whenever the device had flipped since); otherwise the stored
 *  choice, else dark. The boot script applies the same rule. */
export function readInitialTheme(): Theme {
  let mode: string | null = null;
  try {
    mode = localStorage.getItem(THEME_MODE_KEY);
  } catch {
    mode = null;
  }
  if (mode === "system" && typeof window !== "undefined" && typeof window.matchMedia === "function") {
    try {
      return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    } catch {
      /* fall through to the stored theme */
    }
  }
  if (mode === "light" || mode === "dark") return mode;
  return readStoredTheme() ?? "dark";
}

export function storeTheme(theme: Theme): void {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    /* private mode / blocked storage: the choice lasts this page only */
  }
}

/** The inline <head> script that sets the theme before first paint (no
 *  flash; a System choice resolved from the device, as readInitialTheme).
 *  Runs again at DOMContentLoaded in case Next's theme-color metas are
 *  parsed after it. */
export function themeBootScript(): string {
  const canvas = JSON.stringify(THEME_CANVAS);
  const key = JSON.stringify(THEME_STORAGE_KEY);
  const modeKey = JSON.stringify(THEME_MODE_KEY);
  return `(function(){try{
var t,mo;try{t=localStorage.getItem(${key});mo=localStorage.getItem(${modeKey});}catch(e){}
if(mo==="system"&&window.matchMedia){t=window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light";}
else if(mo==="light"||mo==="dark")t=mo;
if(t!=="light"&&t!=="dark")t="dark";
var c=${canvas}[t],d=document,r=d.documentElement;
if(t==="dark")r.classList.add("dark");else r.classList.remove("dark");
var s=function(){var m=d.querySelectorAll('meta[name="theme-color"]');
if(!m.length&&d.head){var n=d.createElement("meta");n.name="theme-color";d.head.appendChild(n);m=[n];}
for(var i=0;i<m.length;i++)m[i].setAttribute("content",c);};
s();if(d.readyState==="loading")d.addEventListener("DOMContentLoaded",s);
}catch(e){}})();`;
}
