"use client";

import { usePathname } from "next/navigation";
import { DOCS_PAGES } from "../../lib/docsNav";

const SLUG_LABELS: Record<string, string> = Object.fromEntries(
  DOCS_PAGES.map((page) => [page.slug, page.label])
);

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
