import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";

const geistSans = localFont({
  src: "./fonts/GeistVF.woff",
  variable: "--font-geist-sans",
  weight: "100 900",
});
const geistMono = localFont({
  src: "./fonts/GeistMonoVF.woff",
  variable: "--font-geist-mono",
  weight: "100 900",
});

const TITLE = "Edward — the open-source AI assistant that remembers, on your Mac";
const DESCRIPTION =
  "An open-source AI assistant for one person: you. Edward remembers where he heard things, follows through on what he promised, works in a sandboxed computer of his own and runs on your Mac with the Claude API.";

export const metadata: Metadata = {
  metadataBase: new URL("https://meet-edward.com"),
  title: TITLE,
  description: DESCRIPTION,
  keywords: [
    "AI assistant",
    "personal AI",
    "long-term memory",
    "open source",
    "self-hosted",
    "macOS",
    "Apple silicon",
    "Claude API",
    "iMessage",
    "LangGraph",
    "Next.js",
    "FastAPI",
    "PostgreSQL",
    "pgvector",
  ],
  manifest: "/manifest.json",
  robots: "index, follow",
  icons: {
    icon: "/favicon.svg",
    apple: "/favicon.svg",
  },
  openGraph: {
    title: TITLE,
    description: DESCRIPTION,
    type: "website",
    url: "https://meet-edward.com",
    siteName: "Edward",
    images: [
      {
        url: "/og.png",
        width: 1200,
        height: 630,
        alt: "Edward — an open-source AI assistant that remembers, on your Mac",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: TITLE,
    description: DESCRIPTION,
    creator: "@pennedbyben",
    images: ["/og.png"],
  },
  alternates: {
    canonical: "https://meet-edward.com",
    types: {
      "application/rss+xml": "https://meet-edward.com/feed.xml",
      "application/feed+json": "https://meet-edward.com/feed.json",
    },
  },
};

const jsonLd = {
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  name: "Edward",
  description: DESCRIPTION,
  url: "https://meet-edward.com",
  applicationCategory: "DeveloperApplication",
  operatingSystem: "macOS (Apple silicon)",
  license: "https://www.apache.org/licenses/LICENSE-2.0",
  sameAs: ["https://github.com/ben4mn/meet-edward"],
  offers: {
    "@type": "Offer",
    price: "0",
    priceCurrency: "USD",
  },
  author: {
    "@type": "Person",
    name: "Ben Foreman",
    url: "https://zyroi.com",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <head>
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
        />
      </head>
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
