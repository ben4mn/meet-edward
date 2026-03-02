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
  title: "Edward — AI Assistant with Long-Term Memory",
  description:
    "An open-source AI assistant that remembers everything. Built with Next.js, FastAPI, and PostgreSQL. Self-hosted on macOS.",
  icons: {
    icon: "/favicon.svg",
  },
  openGraph: {
    title: "Edward — AI Assistant with Long-Term Memory",
    description:
      "An open-source AI assistant that remembers everything. Self-hosted on macOS.",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
