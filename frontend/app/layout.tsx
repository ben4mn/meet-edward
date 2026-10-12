import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import { cn } from "@/lib/utils";
import { themeBootScript, themeColorViewport } from "@/lib/themeColors";
import { ToastProvider } from "@/components/ui/Toast";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains",
});

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
  // viewport-fit=cover is required for notched iPhone PWAs so that
  // content extends behind the dynamic island and home indicator and
  // env(safe-area-inset-*) resolves to non-zero values.
  viewportFit: "cover",
  // The canvas of each theme (the iPhone status bar / PWA title bar), not an
  // accent stripe. The boot script and ThemeProvider replace it with the
  // theme the owner chose; see lib/themeColors.ts.
  themeColor: themeColorViewport(),
  colorScheme: "dark light",
};

// The app's public origin (link previews, metadataBase): NEXT_PUBLIC_BASE_URL
// at build time, e.g. https://edward.example.com; localhost otherwise.
const BASE_URL = (process.env.NEXT_PUBLIC_BASE_URL || "http://localhost:3000").replace(/\/+$/, "");

// Static OG image lives in /public. Use an absolute URL (rather than a
// relative path resolved via metadataBase) because Next's dev server
// silently overrides metadataBase with the request host — the prior
// dynamic app/opengraph-image.tsx leaked that same way. Absolute URL is
// the robust option for the two pieces of metadata that get scraped off
// a production URL (social previews).
const OG_IMAGE = {
  url: `${BASE_URL}/og-image.png`,
  width: 1200,
  height: 630,
  alt: "Edward — Your AI Assistant That Remembers Everything",
  type: "image/png",
};

export const metadata: Metadata = {
  metadataBase: new URL(BASE_URL),
  title: "Edward - AI Assistant",
  description: "Your personal AI assistant with long-term memory",
  manifest: "/manifest.json",
  icons: {
    icon: [
      { url: "/favicon.svg", type: "image/svg+xml" },
      { url: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
      { url: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
    ],
    apple: "/icons/apple-touch-icon.png",
  },
  appleWebApp: {
    capable: true,
    statusBarStyle: "default",
    title: "Edward",
  },
  formatDetection: {
    telephone: false,
  },
  openGraph: {
    title: "Edward - AI Assistant",
    description: "Your personal AI assistant with long-term memory",
    images: [OG_IMAGE],
  },
  twitter: {
    card: "summary_large_image",
    title: "Edward - AI Assistant",
    description: "Your personal AI assistant with long-term memory",
    images: [OG_IMAGE],
  },
};

// Sets the theme class and theme-color before first paint (no flash).
const themeScript = themeBootScript();

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <head>
        {/* viewport, apple-mobile-web-app-*, apple-touch-icon, and
            theme-color all come from the Metadata/Viewport exports above —
            do not duplicate them here. */}
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className={cn(inter.variable, jetbrainsMono.variable, "font-sans")}>
        <ToastProvider>{children}</ToastProvider>
      </body>
    </html>
  );
}
