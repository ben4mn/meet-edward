"use client";

import Link from "next/link";
import { Github, GraduationCap } from "lucide-react";
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
            You need a Mac with Apple silicon that stays on, Homebrew and an
            Anthropic API key. Edward keeps what he knows in your own
            Postgres; the one outside service he needs is the Claude API.
          </p>

          <div className="bg-[#1e293b]/80 border border-[#334155]/60 rounded-xl p-6 mb-8 text-left">
            <pre className="font-mono text-sm text-[#94a3b8] overflow-x-auto">
              <code>
{`git clone https://github.com/ben4mn/meet-edward.git
cd meet-edward
./setup.sh        # asks your name, API key and time zone
bash scripts/install-launchagents.sh

# then, on this Mac, open http://localhost:3000
cat backend/.setup_code   # the code for your first password`}
              </code>
            </pre>
          </div>

          <div className="flex flex-col sm:flex-row items-center justify-center gap-3">
            <a
              href={GITHUB_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2.5 text-base font-semibold text-white px-8 py-3.5 rounded-xl bg-[#52b788] hover:bg-[#52b788]/90 transition-all"
            >
              <Github className="w-5 h-5" />
              View on GitHub
            </a>
            <Link
              href="/docs/beginner-guide"
              className="inline-flex items-center gap-2.5 text-base font-semibold text-[#f1f5f9] px-8 py-3.5 rounded-xl border border-[#334155] hover:border-[#52b788]/50 hover:bg-[#1e293b]/60 transition-all"
            >
              <GraduationCap className="w-5 h-5" />
              Step-by-step guide
            </Link>
          </div>
        </ScrollReveal>
      </div>
    </section>
  );
}
