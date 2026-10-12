"use client";

import { Brain, Terminal, CalendarCheck, Ear, MessageSquare, Shield } from "lucide-react";
import { ScrollReveal, StaggerContainer, StaggerItem } from "./ScrollReveal";

const features = [
  {
    icon: Brain,
    title: "Memory with provenance",
    description:
      "Every memory records who said it, what it is about and how sure he is. Ask how he knows something and he shows you. Only your own words become his standing instructions.",
  },
  {
    icon: Terminal,
    title: "A computer of his own",
    description:
      "A persistent shell, a file editor and a headless browser, sandboxed on your Mac: he can’t touch his own running code, read his keys or reach your database.",
  },
  {
    icon: CalendarCheck,
    title: "Follow-through, with a journal",
    description:
      "When he says he’ll check back, he schedules it and comes back. Whatever he does on his own gets a journal entry you can mark right or wrong.",
  },
  {
    icon: Ear,
    title: "Speaks up once, or not at all",
    description:
      "Opt-in noticing reads your messages for what matters (a deadline closing, someone waiting on you) and says it once, through one door, outside your quiet hours.",
  },
  {
    icon: MessageSquare,
    title: "@Edward in iMessage",
    description:
      "Write @Edward in any chat and he answers there, from your own account. Friends can ask too, inside limits; unknown numbers wait for you.",
  },
  {
    icon: Shield,
    title: "Limits you set",
    description:
      "A daily spend cap in dollars, a cap per turn, a secret scan on everything that leaves, and approvals when you want them.",
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
