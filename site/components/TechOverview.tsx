"use client";

import {
  Database,
  GitBranch,
  Shield,
  Radio,
  Layers,
  Server,
  FileCode2,
  Workflow,
} from "lucide-react";
import { ScrollReveal, StaggerContainer, StaggerItem } from "./ScrollReveal";

const capabilities = [
  {
    icon: Database,
    label: "Hybrid Memory Engine",
    detail: "Vector similarity + keyword search across every conversation. Memories strengthen with use and fade when stale.",
  },
  {
    icon: Layers,
    label: "Multi-Layer Retrieval",
    detail: "Parallel query expansion and post-turn reflection ensure Edward surfaces the right context at the right time.",
  },
  {
    icon: Workflow,
    label: "Multi-Agent Orchestrator",
    detail: "Spawns lightweight worker agents that run in parallel with full tool access, memory retrieval, and state persistence. Edward delegates complex tasks and synthesizes results.",
  },
  {
    icon: Radio,
    label: "Ambient Awareness",
    detail: "Background monitoring with intelligent triage — Edward knows when something needs attention before you ask.",
  },
  {
    icon: GitBranch,
    label: "Self-Evolution Engine",
    detail: "A full pipeline — branch, code, validate, test, review, merge — powered by Claude Code. Edward proposes and ships improvements to himself with rollback safety.",
  },
  {
    icon: Server,
    label: "Runtime Tool Discovery",
    detail: "Discovers and installs new capabilities on the fly. No restarts, no config files — just ask.",
  },
  {
    icon: FileCode2,
    label: "Persistent Compute",
    detail: "Sandboxed execution across Python, JavaScript, SQL, and shell — with databases and files that persist across sessions.",
  },
  {
    icon: Shield,
    label: "Private by Default",
    detail: "Self-hosted, single-tenant architecture. Your data never leaves your infrastructure.",
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
            Built different.
          </h2>
          <p className="text-[#94a3b8] max-w-xl mx-auto leading-relaxed">
            Edward isn&apos;t a wrapper around an API. It&apos;s a full operating system
            for an AI that learns, adapts, and acts autonomously.
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
