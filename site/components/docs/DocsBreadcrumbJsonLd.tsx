"use client";

import { usePathname } from "next/navigation";

const SLUG_LABELS: Record<string, string> = {
  introduction: "Introduction",
  "beginner-guide": "Beginner Guide",
  "setup-with-ai": "Setup with AI",
  "getting-started": "Getting Started",
  configuration: "Configuration",
  "platform-support": "Platform Support",
  architecture: "Architecture",
  skills: "Skills & Integrations",
  memory: "Memory System",
  heartbeat: "Heartbeat",
  widget: "Widget",
  "file-storage": "File Storage",
  orchestrator: "Orchestrator & Claude Code",
};

export function DocsBreadcrumbJsonLd() {
  const pathname = usePathname();
  const slug = pathname.replace(/^\/docs\/?/, "").replace(/\/$/, "");
  const pageLabel = SLUG_LABELS[slug];

  const items = [
    { name: "Home", url: "https://meet-edward.com" },
    { name: "Docs", url: "https://meet-edward.com/docs" },
  ];

  if (pageLabel) {
    items.push({
      name: pageLabel,
      url: `https://meet-edward.com/docs/${slug}`,
    });
  }

  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: items.map((item, i) => ({
      "@type": "ListItem",
      position: i + 1,
      name: item.name,
      item: item.url,
    })),
  };

  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
    />
  );
}
