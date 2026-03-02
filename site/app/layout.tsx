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
  title: "Edward — AI That Remembers, Evolves, and Orchestrates",
  description:
    "An open-source AI assistant with persistent memory, self-evolving code, and multi-agent orchestration. Built with LangGraph and Claude. Self-hosted on macOS.",
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
    title: "Edward — AI That Remembers, Evolves, and Orchestrates",
    description:
      "Open-source AI with persistent memory, self-evolving code, and multi-agent orchestration. Built with LangGraph and Claude.",
    type: "website",
    url: "https://meet-edward.com",
    siteName: "Edward",
    images: [
      {
        url: "/og.png",
        width: 1200,
        height: 630,
        alt: "Edward — AI That Remembers, Evolves, and Orchestrates",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "Edward — AI That Remembers, Evolves, and Orchestrates",
    description:
      "Open-source AI with persistent memory, self-evolving code, and multi-agent orchestration. Built with LangGraph and Claude.",
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
    "An open-source AI assistant with persistent memory, self-evolving code, and multi-agent orchestration. Built with LangGraph and Claude. Self-hosted on macOS.",
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
