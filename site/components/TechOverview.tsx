"use client";

import {
  Database,
  Layers,
  Shield,
  Sparkles,
  Cpu,
  Radio,
  Server,
  FileCode2,
} from "lucide-react";
import { ScrollReveal, StaggerContainer, StaggerItem } from "./ScrollReveal";

const capabilities = [
  {
    icon: Database,
    label: "Hybrid memory",
    detail: "pgvector and keyword search, blended 70/30, over a small embedding model that runs on your Mac. Provenance on every row, and upkeep that never deletes.",
  },
  {
    icon: Layers,
    label: "One tool loop, fourteen tools",
    detail: "The same tools in chat, voice, pings and wakes. What a turn may do is its policy, and a refusal comes back as an honest error, never a hidden tool.",
  },
  {
    icon: Shield,
    label: "Seatbelt rails",
    detail: "His shell can’t read his keys, write his own code, reach his API or databases, or script your apps. It is not a containment boundary, and the docs say so.",
  },
  {
    icon: Sparkles,
    label: "A mind that wakes for a reason",
    detail: "Follow-through on promises, a silent nightly, one small budgeted run of free time a day, and a journal of what he chose to do, instead of a timetable.",
  },
  {
    icon: Cpu,
    label: "Claude, by purpose",
    detail: "Sonnet for chat and wakes, Haiku for memory work, Opus for evaluations. Every call priced and counted against a daily cap.",
  },
  {
    icon: Radio,
    label: "Notice, then speak",
    detail: "A cheap noticer reads what arrives, a gate in plain code decides, a verified speaker says it once. Quiet hours are enforced in code.",
  },
  {
    icon: Server,
    label: "Runs like a service",
    detail: "LaunchAgents, a bottom-up watchdog, verified nightly backups, and staged frontend deploys with a one-command rollback.",
  },
  {
    icon: FileCode2,
    label: "Docs written for AI",
    detail: "CLAUDE.md and a page per subsystem, kept honest by a doc lint, so Claude Code can help you set him up, run him and change him.",
  },
];

export function TechOverview() {
  return (
    <section className="relative py-24 sm:py-32 px-6">
      <div className="absolute top-0 left-1/2 -translate-x-1/2 w-40 h-px bg-gradient-to-r from-transparent via-[#334155] to-transparent" />
      <div className="max-w-5xl mx-auto">
        <ScrollReveal className="text-center mb-14">
          <p className="font-mono text-sm text-[#52b788] tracking-widest uppercase mb-3">
            Under the Hood
          </p>
          <h2 className="font-mono text-3xl sm:text-4xl font-bold text-[#f1f5f9] tracking-tight mb-4">
            Built to be trusted.
          </h2>
          <p className="text-[#94a3b8] max-w-xl mx-auto leading-relaxed">
            Edward is a long-running service on your Mac that can read your
            messages and act as you, so he comes with rails, receipts and
            limits.
          </p>
        </ScrollReveal>

        <StaggerContainer className="grid grid-cols-1 sm:grid-cols-2 gap-4" staggerDelay={0.08}>
          {capabilities.map((cap) => (
            <StaggerItem key={cap.label}>
              <div className="flex items-start gap-4 p-5 rounded-lg border border-[#334155]/40 bg-[#0f172a]/60 hover:border-[#334155]/80 transition-colors">
                <div className="flex-shrink-0 w-8 h-8 rounded-md bg-[#52b788]/8 border border-[#52b788]/20 flex items-center justify-center mt-0.5">
                  <cap.icon className="w-4 h-4 text-[#52b788]" />
                </div>
                <div>
                  <h3 className="font-mono text-sm font-semibold text-[#f1f5f9] mb-1">
                    {cap.label}
                  </h3>
                  <p className="text-sm text-[#94a3b8] leading-relaxed">
                    {cap.detail}
                  </p>
                </div>
              </div>
            </StaggerItem>
          ))}
        </StaggerContainer>
      </div>
    </section>
  );
}
