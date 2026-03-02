"use client";

import { Brain, Calendar, MessageSquare, Code2, Workflow, GitBranch } from "lucide-react";
import { ScrollReveal, StaggerContainer, StaggerItem } from "./ScrollReveal";

const features = [
  {
    icon: Brain,
    title: "Long-term Memory",
    description:
      "Edward remembers your preferences, past conversations, and important details. Every interaction makes him smarter.",
  },
  {
    icon: Calendar,
    title: "Smart Scheduling",
    description:
      "Set reminders, schedule messages, and automate recurring tasks. Edward handles timing so you don't have to.",
  },
  {
    icon: MessageSquare,
    title: "Messaging Integration",
    description:
      "Connects to iMessage, SMS, and WhatsApp. Edward can send messages on your behalf or monitor for important conversations.",
  },
  {
    icon: Code2,
    title: "Code Execution",
    description:
      "Run Python, JavaScript, SQL, and shell commands in a sandboxed environment. Edward computes, analyzes, and builds.",
  },
  {
    icon: Workflow,
    title: "Multi-Agent Orchestration",
    description:
      "Spawns parallel worker agents that run autonomously with full tool access. Edward delegates, coordinates, and synthesizes.",
  },
  {
    icon: GitBranch,
    title: "Self-Evolution",
    description:
      "Edward can propose, test, and deploy improvements to his own codebase — with full rollback safety and review.",
  },
];

export function FeaturesGrid() {
  return (
    <section className="relative py-24 sm:py-32 px-6">
      <div className="max-w-6xl mx-auto">
        <ScrollReveal className="text-center mb-16">
          <p className="font-mono text-sm text-[#52b788] tracking-widest uppercase mb-3">
            Capabilities
          </p>
          <h2 className="font-mono text-3xl sm:text-4xl font-bold text-[#f1f5f9] tracking-tight">
            More than a chatbot.
          </h2>
        </ScrollReveal>

        <StaggerContainer className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
          {features.map((feature) => (
            <StaggerItem key={feature.title}>
              <div className="group relative h-full rounded-xl bg-[#1e293b]/60 border border-[#334155]/60 p-6 hover:border-[#52b788]/30 transition-all duration-300 hover:bg-[#1e293b]/80">
                <div className="absolute top-0 left-6 right-6 h-px bg-gradient-to-r from-transparent via-[#52b788]/40 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
                <div className="flex items-start gap-4">
                  <div className="flex-shrink-0 w-10 h-10 rounded-lg bg-[#52b788]/10 flex items-center justify-center">
                    <feature.icon className="w-5 h-5 text-[#52b788]" />
                  </div>
                  <div>
                    <h3 className="font-mono text-base font-semibold text-[#f1f5f9] mb-2">
                      {feature.title}
                    </h3>
                    <p className="text-sm text-[#94a3b8] leading-relaxed">
                      {feature.description}
                    </p>
                  </div>
                </div>
              </div>
            </StaggerItem>
          ))}
        </StaggerContainer>
      </div>
    </section>
  );
}
