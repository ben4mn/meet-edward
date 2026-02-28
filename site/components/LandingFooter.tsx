"use client";

import { Github } from "lucide-react";
import { EdwardAvatar } from "./EdwardAvatar";

const GITHUB_URL = "https://github.com/ben4mn/meet-edward";

export function LandingFooter() {
  return (
    <footer className="relative py-12 px-6 border-t border-[#334155]/40">
      <div className="max-w-6xl mx-auto flex flex-col items-center gap-4 text-center">
        <EdwardAvatar size={40} animated />
        <div className="flex items-center gap-4">
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
          Built with care. Open source under MIT.
        </p>
        <p className="text-xs text-[#475569]">
          &copy; {new Date().getFullYear()} Edward. All rights reserved.
        </p>
      </div>
    </footer>
  );
}
