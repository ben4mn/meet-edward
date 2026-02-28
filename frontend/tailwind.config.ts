import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./lib/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-inter)", "system-ui", "sans-serif"],
        mono: ["var(--font-jetbrains)", "monospace"],
      },
      colors: {
        // Semantic color tokens using CSS variables
        "primary-bg": "var(--primary-bg)",
        surface: "var(--surface)",
        "text-primary": "var(--text-primary)",
        "text-muted": "var(--text-muted)",
        border: "var(--border)",
        terminal: "var(--terminal)",
        "input-bg": "var(--input-bg)",
        "input-border": "var(--input-border)",

        // Legacy tokens for compatibility
        background: "var(--primary-bg)",
        foreground: "var(--text-primary)",
        primary: {
          DEFAULT: "var(--terminal)",
          foreground: "#ffffff",
        },
        secondary: {
          DEFAULT: "var(--surface)",
          foreground: "var(--text-primary)",
        },
        muted: {
          DEFAULT: "var(--surface)",
          foreground: "var(--text-muted)",
        },
        accent: {
          DEFAULT: "var(--surface)",
          foreground: "var(--text-primary)",
        },
        card: {
          DEFAULT: "var(--surface)",
          foreground: "var(--text-primary)",
        },
        input: "var(--input-border)",
        ring: "var(--terminal)",
      },
      borderRadius: {
        lg: "0.5rem",
        md: "calc(0.5rem - 2px)",
        sm: "calc(0.5rem - 4px)",
      },
    },
  },
  plugins: [],
};

export default config;
