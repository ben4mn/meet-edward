"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { Github, BookOpen, Newspaper } from "lucide-react";
import { EdwardAvatar } from "./EdwardAvatar";

const GITHUB_URL = "https://github.com/ben4mn/meet-edward";

export function LandingNav() {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const handleScroll = () => setScrolled(window.scrollY > 20);
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  return (
    <motion.nav
      className={`fixed top-0 left-0 right-0 z-50 transition-all duration-300 ${
        scrolled
          ? "bg-[#0f172a]/90 backdrop-blur-xl border-b border-[#334155]/50"
          : "bg-transparent"
      }`}
      initial={{ y: -80 }}
      animate={{ y: 0 }}
      transition={{ duration: 0.5, ease: [0.25, 0.1, 0.25, 1] }}
    >
      <div className="max-w-6xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between">
        <Link href="/" className="flex items-center gap-2.5 group">
          <EdwardAvatar size={32} animated />
          <span className="hidden sm:block font-mono font-bold text-lg text-[#f1f5f9] tracking-tight">
            Edward
          </span>
        </Link>

        <div className="flex items-center gap-1.5 sm:gap-3">
          <Link
            href="/docs"
            className="flex-shrink-0 flex items-center gap-2 text-sm font-medium text-white px-3 sm:px-5 py-2 rounded-lg bg-[#52b788]/20 border border-[#52b788]/30 hover:bg-[#52b788]/30 transition-all"
          >
            <BookOpen className="w-4 h-4" />
            <span className="hidden sm:inline">Docs</span>
          </Link>
          <Link
            href="/blog"
            className="flex-shrink-0 flex items-center gap-2 text-sm font-medium text-white px-3 sm:px-5 py-2 rounded-lg bg-[#52b788]/20 border border-[#52b788]/30 hover:bg-[#52b788]/30 transition-all"
          >
            <Newspaper className="w-4 h-4" />
            <span className="hidden sm:inline">Blog</span>
          </Link>
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="flex-shrink-0 flex items-center gap-2 text-sm font-medium text-white px-3 sm:px-5 py-2 rounded-lg bg-[#52b788]/20 border border-[#52b788]/30 hover:bg-[#52b788]/30 transition-all"
          >
            <Github className="w-4 h-4" />
            <span className="hidden sm:inline">GitHub</span>
          </a>
        </div>
      </div>
    </motion.nav>
  );
}
