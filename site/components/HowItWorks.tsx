"use client";

import { MessageCircle, Cpu, CalendarCheck } from "lucide-react";
import { ScrollReveal, StaggerContainer, StaggerItem } from "./ScrollReveal";

const steps = [
  {
    number: "01",
    icon: MessageCircle,
    title: "You talk",
    description:
      "In the app, by voice, or with @Edward in iMessage. The same Edward, with the same memory, everywhere.",
  },
  {
    number: "02",
    icon: Cpu,
    title: "Edward works",
    description:
      "He recalls what he knows, searches the web, works in his own shell, and shows you every step as he goes.",
  },
  {
    number: "03",
    icon: CalendarCheck,
    title: "Edward follows through",
    description:
      "He answers, files what he learned with where it came from, and comes back when he said he would.",
  },
];

export function HowItWorks() {
  return (
    <section className="relative py-24 sm:py-32 px-6">
      <div className="absolute top-0 left-1/2 -translate-x-1/2 w-40 h-px bg-gradient-to-r from-transparent via-[#334155] to-transparent" />
      <div className="max-w-5xl mx-auto">
        <ScrollReveal className="text-center mb-16 sm:mb-20">
          <p className="font-mono text-sm text-[#52b788] tracking-widest uppercase mb-3">
            How it works
          </p>
          <h2 className="font-mono text-3xl sm:text-4xl font-bold text-[#f1f5f9] tracking-tight">
            Three steps. Nothing hidden.
          </h2>
        </ScrollReveal>

        <StaggerContainer className="grid grid-cols-1 md:grid-cols-3 gap-8 md:gap-6 relative" staggerDelay={0.15}>
          <div className="hidden md:block absolute top-[52px] left-[16.67%] right-[16.67%] h-px bg-gradient-to-r from-[#334155] via-[#52b788]/30 to-[#334155]" />
          {steps.map((step, i) => (
            <StaggerItem key={step.number} className="relative">
              <div className="flex flex-col items-center text-center">
                <div className="relative z-10 w-[72px] h-[72px] rounded-full bg-[#0f172a] border-2 border-[#334155] flex items-center justify-center mb-6 group">
                  <div className="absolute inset-0 rounded-full bg-[#52b788]/5 opacity-0 group-hover:opacity-100 transition-opacity" />
                  <step.icon className="w-6 h-6 text-[#52b788]" />
                  <span className="absolute -top-1 -right-1 w-6 h-6 rounded-full bg-[#52b788] text-[#0f172a] text-xs font-mono font-bold flex items-center justify-center">
                    {i + 1}
                  </span>
                </div>
                {i < steps.length - 1 && (
                  <div className="md:hidden absolute top-[72px] left-1/2 -translate-x-1/2 w-px h-8 bg-gradient-to-b from-[#334155] to-transparent" />
                )}
                <h3 className="font-mono text-lg font-semibold text-[#f1f5f9] mb-2">
                  {step.title}
                </h3>
                <p className="text-sm text-[#94a3b8] leading-relaxed max-w-xs">
                  {step.description}
                </p>
              </div>
            </StaggerItem>
          ))}
        </StaggerContainer>
      </div>
    </section>
  );
}
