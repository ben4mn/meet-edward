"use client";

import { Github } from "lucide-react";
import { ScrollReveal } from "./ScrollReveal";

const GITHUB_URL = "https://github.com/ben4mn/meet-edward";

export function GetStarted() {
  return (
    <section id="get-started" className="relative py-24 sm:py-32 px-6">
      <div className="absolute top-0 left-1/2 -translate-x-1/2 w-40 h-px bg-gradient-to-r from-transparent via-[#334155] to-transparent" />
      <div className="max-w-2xl mx-auto text-center">
        <ScrollReveal>
          <p className="font-mono text-sm text-[#52b788] tracking-widest uppercase mb-3">
            Open Source
          </p>
          <h2 className="font-mono text-3xl sm:text-4xl font-bold text-[#f1f5f9] tracking-tight mb-4">
            Run Edward on your Mac.
          </h2>
          <p className="text-[#94a3b8] mb-8 leading-relaxed">
            Clone the repo, run the setup script, add your Anthropic API key, and you&apos;re live.
            Edward runs entirely on your machine — your data stays yours.
          </p>

          <div className="bg-[#1e293b]/80 border border-[#334155]/60 rounded-xl p-6 mb-8 text-left">
            <pre className="font-mono text-sm text-[#94a3b8] overflow-x-auto">
              <code>
{`git clone https://github.com/ben4mn/meet-edward.git
cd meet-edward
./setup.sh
# Add your ANTHROPIC_API_KEY to .env
./restart.sh`}
              </code>
            </pre>
          </div>

          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2.5 text-base font-semibold text-white px-8 py-3.5 rounded-xl bg-[#52b788] hover:bg-[#52b788]/90 transition-all"
          >
            <Github className="w-5 h-5" />
            View on GitHub
          </a>
        </ScrollReveal>
      </div>
    </section>
  );
}
