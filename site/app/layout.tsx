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

export const metadata: Metadata = {
  metadataBase: new URL("https://meet-edward.com"),
  title: "Edward — Secure, Self-Evolving AI That Never Forgets",
  description:
    "Self-hosted AI that remembers every conversation, evolves its own code, and proactively monitors your messages and calendar. Your data never leaves your machine.",
  keywords: [
    "AI assistant",
    "long-term memory",
    "open source",
    "self-hosted",
    "LangGraph",
    "Next.js",
    "FastAPI",
    "PostgreSQL",
    "pgvector",
    "macOS",
    "Claude",
    "conversational AI",
    "personal AI",
    "multi-agent",
  ],
  robots: "index, follow",
  icons: {
    icon: "/favicon.svg",
    apple: "/favicon.svg",
  },
  openGraph: {
    title: "Edward — Secure, Self-Evolving AI That Never Forgets",
    description:
      "Self-hosted AI that remembers every conversation, evolves its own code, and proactively monitors your messages and calendar. Your data never leaves your machine.",
    type: "website",
    url: "https://meet-edward.com",
    siteName: "Edward",
    images: [
      {
        url: "/og.png",
        width: 1200,
        height: 630,
        alt: "Edward — Secure, Self-Evolving AI That Never Forgets",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "Edward — Secure, Self-Evolving AI That Never Forgets",
    description:
      "Self-hosted AI that remembers every conversation, evolves its own code, and proactively monitors your messages and calendar. Your data never leaves your machine.",
    creator: "@pennedbyben",
    images: ["/og.png"],
  },
  alternates: {
    canonical: "https://meet-edward.com",
  },
};

const jsonLd = {
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  name: "Edward",
  description:
    "Self-hosted AI that remembers every conversation, evolves its own code, and proactively monitors your messages and calendar. Your data never leaves your machine.",
  url: "https://meet-edward.com",
  applicationCategory: "DeveloperApplication",
  operatingSystem: "macOS",
  license: "https://opensource.org/licenses/MIT",
  offers: {
    "@type": "Offer",
    price: "0",
    priceCurrency: "USD",
  },
  author: {
    "@type": "Person",
    name: "Ben Williams",
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
