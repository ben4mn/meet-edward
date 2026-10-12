import type { Config } from "tailwindcss";
import plugin from "tailwindcss/plugin";

// Design contract: docs/design-system-2026-09-25.md ("calm presence").
//
// Every colour is a CSS variable with an `-rgb` channel twin (globals.css), and
// Tailwind reads the twin so alpha modifiers compile: `bg-terminal/20`,
// `border-border/70`, `bg-primary-bg/95`. Before this the colours were plain
// `var(--x)` strings and every one of those classes compiled to nothing.
// Raw `var(--terminal)` etc. still exist for CSS that reads them directly.

/** `rgb(var(--name-rgb) / <alpha-value>)` — a token that takes /NN. */
const rgb = (name: string) => `rgb(var(--${name}-rgb) / <alpha-value>)`;

type ColorFn = (opts: { opacityValue?: string }) => string;

/** A token whose resting colour is already translucent (the hairline, the
 *  soft chip fills, the glow). With no modifier it renders at its own alpha;
 *  `/NN` scales that alpha, so `border-border/50` is half a hairline, not a
 *  50%-white line. */
const scaled =
  (channel: string, alphaVar: string): ColorFn =>
  ({ opacityValue }) => {
    const a = opacityValue ?? "1";
    return `rgb(var(--${channel}-rgb) / calc(var(--${alphaVar}) * ${a}))`;
  };

const config: Config = {
  darkMode: "class",
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./lib/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  future: {
    // iPhone: a tap must not leave a sticky hover state behind.
    hoverOnlyWhenSupported: true,
  },
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-inter)", "system-ui", "sans-serif"],
        mono: ["var(--font-jetbrains)", "ui-monospace", "monospace"],
      },
      colors: {
        // Canvas and surfaces
        "primary-bg": rgb("primary-bg"),
        surface: rgb("surface"),
        "surface-elevated": rgb("surface-elevated"),
        "surface-3": rgb("surface-3"),
        // Text
        "text-primary": rgb("text-primary"),
        "text-secondary": rgb("text-secondary"),
        "text-muted": rgb("text-muted"),
        // Hairline (see `scaled`)
        border: scaled("border", "border-alpha") as unknown as string,
        // Edward's accent — his presence and his acts only.
        terminal: rgb("terminal"),
        "terminal-ink": rgb("terminal-ink"),
        "terminal-strong": rgb("terminal-strong"),
        "terminal-soft": scaled("terminal", "terminal-soft-alpha") as unknown as string,
        glow: scaled("terminal", "glow-alpha") as unknown as string,
        // Inputs
        "input-bg": rgb("input-bg"),
        "input-border": scaled("input-border", "input-border-alpha") as unknown as string,

        // Status tokens. Never emerald: "overdue" and "problem" must not
        // borrow the colour that means "Edward acted". Soft = chip fills.
        ok: rgb("ok"),
        warn: rgb("warn"),
        bad: rgb("bad"),
        info: rgb("info"),
        "ok-soft": scaled("ok", "soft-alpha") as unknown as string,
        "warn-soft": scaled("warn", "soft-alpha") as unknown as string,
        "bad-soft": scaled("bad", "soft-alpha") as unknown as string,
        "info-soft": scaled("info", "soft-alpha") as unknown as string,

        // Legacy tokens for compatibility
        background: rgb("primary-bg"),
        foreground: rgb("text-primary"),
        primary: {
          DEFAULT: rgb("terminal"),
          foreground: rgb("terminal-ink"),
        },
        secondary: {
          DEFAULT: rgb("surface"),
          foreground: rgb("text-primary"),
        },
        muted: {
          DEFAULT: rgb("surface"),
          foreground: rgb("text-muted"),
        },
        accent: {
          DEFAULT: rgb("surface"),
          foreground: rgb("text-primary"),
        },
        card: {
          DEFAULT: rgb("surface"),
          foreground: rgb("text-primary"),
        },
        input: scaled("input-border", "input-border-alpha") as unknown as string,
        ring: rgb("terminal"),
      },
      // Bare `border` / `divide` draw the hairline, not Tailwind's gray-200.
      borderColor: {
        DEFAULT: "rgb(var(--border-rgb) / var(--border-alpha))",
      },
      divideColor: {
        DEFAULT: "rgb(var(--border-rgb) / var(--border-alpha))",
      },
      fontSize: {
        overline: ["11px", { lineHeight: "14px", letterSpacing: "0.08em", fontWeight: "600" }],
        caption: ["12px", { lineHeight: "16px", fontWeight: "500" }],
        footnote: ["13px", { lineHeight: "18px", fontWeight: "400" }],
        body: ["15px", { lineHeight: "22px", fontWeight: "400" }],
        callout: ["15px", { lineHeight: "22px", fontWeight: "600" }],
        title3: ["17px", { lineHeight: "22px", fontWeight: "600" }],
        title2: ["20px", { lineHeight: "26px", fontWeight: "600", letterSpacing: "-0.01em" }],
        title1: ["28px", { lineHeight: "32px", fontWeight: "650", letterSpacing: "-0.02em" }],
        display: ["34px", { lineHeight: "38px", fontWeight: "650", letterSpacing: "-0.025em" }],
      },
      borderRadius: {
        xs: "6px",
        sm: "8px",
        md: "12px",
        lg: "16px",
        xl: "22px",
      },
      boxShadow: {
        e1: "var(--shadow-e1)",
        e2: "var(--shadow-e2)",
        e3: "var(--shadow-e3)",
        glow: "var(--shadow-glow)",
      },
      transitionDuration: {
        DEFAULT: "160ms",
        press: "90ms",
        fast: "160ms",
        base: "240ms",
        slow: "360ms",
      },
      transitionTimingFunction: {
        DEFAULT: "cubic-bezier(.22,1,.36,1)",
        out: "cubic-bezier(.22,1,.36,1)",
        "in-out": "cubic-bezier(.65,0,.35,1)",
        in: "cubic-bezier(.4,0,1,1)",
      },
      keyframes: {
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "fade-rise": {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "scale-in": {
          from: { opacity: "0", transform: "scale(.96)" },
          to: { opacity: "1", transform: "scale(1)" },
        },
        // Edward's breath: rise faster than fall.
        breathe: {
          "0%, 100%": { opacity: ".55", transform: "scale(.86)" },
          "38%": { opacity: "1", transform: "scale(1)" },
        },
        shimmer: {
          from: { backgroundPosition: "200% 0" },
          to: { backgroundPosition: "-200% 0" },
        },
      },
      animation: {
        "fade-in": "fade-in 160ms cubic-bezier(.22,1,.36,1) both",
        "fade-rise": "fade-rise 240ms cubic-bezier(.22,1,.36,1) both",
        "scale-in": "scale-in 160ms cubic-bezier(.22,1,.36,1) both",
        breathe: "breathe 5.2s cubic-bezier(.65,0,.35,1) infinite",
        shimmer: "shimmer 1.6s linear infinite",
      },
      minHeight: {
        touch: "44px",
      },
      minWidth: {
        touch: "44px",
      },
    },
  },
  plugins: [
    // `text-overline` is the section-label style: uppercase as well as sized.
    plugin(({ addUtilities }) => {
      addUtilities({ ".text-overline": { "text-transform": "uppercase" } });
    }),
  ],
};

export default config;
