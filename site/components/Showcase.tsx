"use client";

import { ScrollReveal, StaggerContainer, StaggerItem } from "./ScrollReveal";
import { ZoomableImage } from "./ZoomableImage";

// Screenshots from the repository's UX harness (invented data only).
const shots: { src: string; width: number; height: number; title: string; caption: string; alt: string; wide?: boolean }[] = [
  {
    src: "/screenshots/today-desktop.png",
    width: 1600,
    height: 1000,
    title: "Today",
    caption: "The front door: what needs you, with the action on the card, and what he did on his own.",
    alt: "Edward's Today page: late pet doses to log, a call starting soon, a reply held for approval, and what he did today",
    wide: true,
  },
  {
    src: "/screenshots/chat-desktop.png",
    width: 1600,
    height: 1000,
    title: "Chat, with the work rail",
    caption: "Every memory search, page and tool call, live, then folded into a receipt.",
    alt: "A chat with Edward: his work rail lists the searches and the page he couldn't read, above his answer",
  },
  {
    src: "/screenshots/edward-tab-desktop.png",
    width: 1600,
    height: 1000,
    title: "The Edward tab",
    caption: "What he knows, watches and does, in five rooms.",
    alt: "The Edward tab: his status, what he knows, watches and does, and the five rooms",
  },
  {
    src: "/screenshots/screen-desktop.png",
    width: 1600,
    height: 1000,
    title: "His screen",
    caption: "A stage on the Mac’s display that he arranges himself.",
    alt: "Edward's screen: a headline, a countdown, the weather, a thought he wrote and tonight's pet care",
  },
  {
    src: "/screenshots/hub-kiosk.png",
    width: 1600,
    height: 960,
    title: "The household hub",
    caption: "Optional: pets’ feeding and meds, the calendar, lists and voice on a kitchen touchscreen.",
    alt: "The household hub: the time, two late doses, Edward's greeting and tiles for pets, calendar, lists and voice",
  },
];

export function Showcase() {
  return (
    <section className="relative py-24 sm:py-32 px-6">
      <div className="absolute top-0 left-1/2 -translate-x-1/2 w-40 h-px bg-gradient-to-r from-transparent via-[#334155] to-transparent" />
      <div className="max-w-6xl mx-auto">
        <ScrollReveal className="text-center mb-14">
          <p className="font-mono text-sm text-[#52b788] tracking-widest uppercase mb-3">
            Where he shows up
          </p>
          <h2 className="font-mono text-3xl sm:text-4xl font-bold text-[#f1f5f9] tracking-tight mb-4">
            Your phone, your desk, your kitchen.
          </h2>
          <p className="text-[#94a3b8] max-w-xl mx-auto leading-relaxed">
            An app you can install on your phone, live voice, an iPhone
            widget he fills himself, and push notifications that wait out
            your quiet hours.
          </p>
        </ScrollReveal>

        <StaggerContainer className="grid grid-cols-1 md:grid-cols-2 gap-8" staggerDelay={0.1}>
          {shots.map((shot) => (
            <StaggerItem key={shot.src} className={shot.wide ? "md:col-span-2" : undefined}>
              <figure>
                <div className="rounded-xl border border-[#334155]/80 overflow-hidden bg-[#0b1120] shadow-xl shadow-black/30">
                  <ZoomableImage
                    src={shot.src}
                    alt={shot.alt}
                    width={shot.width}
                    height={shot.height}
                    className="w-full h-auto block transition-opacity hover:opacity-90"
                  />
                </div>
                <figcaption className="mt-3">
                  <span className="font-mono text-sm font-semibold text-[#f1f5f9]">
                    {shot.title}
                  </span>
                  <span className="text-sm text-[#94a3b8]"> — {shot.caption}</span>
                </figcaption>
              </figure>
            </StaggerItem>
          ))}
        </StaggerContainer>
      </div>
    </section>
  );
}
