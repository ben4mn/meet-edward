"use client";

import Link from "next/link";
import { Github, BookOpen } from "lucide-react";
import { EdwardAvatar } from "./EdwardAvatar";

const GITHUB_URL = "https://github.com/ben4mn/meet-edward";

export function LandingFooter() {
  return (
    <footer className="relative py-12 px-6 border-t border-[#334155]/40">
      <div className="max-w-6xl mx-auto flex flex-col items-center gap-4 text-center">
        <EdwardAvatar size={40} animated />
        <div className="flex items-center gap-4">
          <Link
            href="/docs"
            className="text-[#94a3b8] hover:text-[#f1f5f9] transition-colors"
          >
            <BookOpen className="w-5 h-5" />
          </Link>
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[#94a3b8] hover:text-[#f1f5f9] transition-colors"
          >
            <Github className="w-5 h-5" />
          </a>
        </div>
        <p className="text-sm text-[#94a3b8]">
          Built with care. Open source under Apache 2.0.
        </p>
        <p className="text-sm text-[#94a3b8]">
          Built by Ben Foreman —{" "}
          <a href="https://zyroi.com" target="_blank" rel="noopener noreferrer" className="text-[#52b788] hover:text-[#6fcf97] transition-colors">zyroi.com</a>
          {" · "}
          <a href="https://github.com/ben4mn" target="_blank" rel="noopener noreferrer" className="text-[#52b788] hover:text-[#6fcf97] transition-colors">GitHub</a>
          {" · "}
          <a href="https://linkedin.com/in/ben4mn" target="_blank" rel="noopener noreferrer" className="text-[#52b788] hover:text-[#6fcf97] transition-colors">LinkedIn</a>
        </p>
        <p className="text-xs text-[#475569]">
          &copy; {new Date().getFullYear()} Edward. All rights reserved.
        </p>
      </div>
    </footer>
  );
}
