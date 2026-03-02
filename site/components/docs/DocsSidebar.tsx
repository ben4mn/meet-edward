"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { EdwardAvatar } from "../EdwardAvatar";
import {
  BookOpen,
  Rocket,
  Settings,
  Puzzle,
  Layers,
  Brain,
  Activity,
  Smartphone,
  HardDrive,
  GitBranch,
  GraduationCap,
  WandSparkles,
  Monitor,
  ArrowLeft,
  Menu,
  X,
} from "lucide-react";

type NavItem =
  | { type: "section"; label: string }
  | { type: "link"; href: string; label: string; icon: React.ComponentType<{ className?: string }> };

const NAV_ITEMS: NavItem[] = [
  { type: "section", label: "Get Started" },
  { type: "link", href: "/docs/introduction", label: "Introduction", icon: BookOpen },
  { type: "link", href: "/docs/beginner-guide", label: "Beginner Guide", icon: GraduationCap },
  { type: "link", href: "/docs/setup-with-ai", label: "Setup with AI", icon: WandSparkles },
  { type: "link", href: "/docs/getting-started", label: "Getting Started", icon: Rocket },
  { type: "link", href: "/docs/configuration", label: "Configuration", icon: Settings },
  { type: "link", href: "/docs/platform-support", label: "Platform Support", icon: Monitor },
  { type: "section", label: "Features" },
  { type: "link", href: "/docs/memory", label: "Memory System", icon: Brain },
  { type: "link", href: "/docs/heartbeat", label: "Heartbeat", icon: Activity },
  { type: "link", href: "/docs/widget", label: "Widget", icon: Smartphone },
  { type: "link", href: "/docs/file-storage", label: "File Storage", icon: HardDrive },
  { type: "link", href: "/docs/orchestrator", label: "Orchestrator", icon: GitBranch },
  { type: "section", label: "Reference" },
  { type: "link", href: "/docs/skills", label: "Skills & Integrations", icon: Puzzle },
  { type: "link", href: "/docs/architecture", label: "Architecture", icon: Layers },
];

export function DocsSidebar() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  const nav = (
    <nav className="flex flex-col gap-1">
      {NAV_ITEMS.map((item, i) => {
        if (item.type === "section") {
          return (
            <div
              key={item.label}
              className={`px-3 pt-4 pb-1 text-[10px] font-semibold uppercase tracking-widest text-[#64748b] ${i === 0 ? "pt-0" : ""}`}
            >
              {item.label}
            </div>
          );
        }
        const { href, label, icon: Icon } = item;
        const active = pathname === href;
        return (
          <Link
            key={href}
            href={href}
            onClick={() => setOpen(false)}
            className={`flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
              active
                ? "bg-[#52b788]/15 text-[#52b788]"
                : "text-[#94a3b8] hover:text-[#f1f5f9] hover:bg-[#1e293b]"
            }`}
          >
            <Icon className="w-4 h-4 shrink-0" />
            {label}
          </Link>
        );
      })}
    </nav>
  );

  return (
    <>
      {/* Mobile toggle */}
      <button
        onClick={() => setOpen(!open)}
        className="lg:hidden fixed top-4 left-4 z-50 p-2 rounded-lg bg-[#1e293b] border border-[#334155] text-[#f1f5f9]"
        aria-label="Toggle sidebar"
      >
        {open ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
      </button>

      {/* Mobile overlay */}
      {open && (
        <div
          className="lg:hidden fixed inset-0 z-40 bg-black/50"
          onClick={() => setOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={`fixed top-0 left-0 z-40 h-screen w-64 bg-[#0f172a] border-r border-[#334155]/50 flex flex-col p-5 transition-transform duration-200 lg:translate-x-0 lg:sticky lg:z-auto overflow-y-auto ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center gap-2.5 mb-6">
          <EdwardAvatar size={28} animated />
          <span className="font-mono font-bold text-[#f1f5f9] text-sm tracking-tight">
            Edward Docs
          </span>
        </div>

        <Link
          href="/"
          className="flex items-center gap-2 text-xs text-[#94a3b8] hover:text-[#f1f5f9] transition-colors mb-6"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          Back to home
        </Link>

        {nav}
      </aside>
    </>
  );
}
