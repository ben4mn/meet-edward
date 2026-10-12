// The docs pages, in sidebar order. The sidebar and the breadcrumb JSON-LD read
// this list; scripts/blog-data.mjs keeps its own copy of the slugs (DOC_PAGES)
// for the sitemap, so change both together. Renamed pages keep their old URLs
// through public/_redirects.

export type DocsSection = "Get started" | "Using Edward" | "Reference";

export interface DocsPage {
  slug: string;
  label: string;
  section: DocsSection;
}

export const DOCS_PAGES: DocsPage[] = [
  { slug: "introduction", label: "Introduction", section: "Get started" },
  { slug: "beginner-guide", label: "Beginner guide", section: "Get started" },
  { slug: "setup-with-ai", label: "Set up with AI", section: "Get started" },
  { slug: "getting-started", label: "Quick start", section: "Get started" },
  { slug: "configuration", label: "Configuration", section: "Get started" },
  { slug: "platform-support", label: "Platform support", section: "Get started" },
  { slug: "upgrading", label: "Upgrading from March", section: "Get started" },
  { slug: "app", label: "The app", section: "Using Edward" },
  { slug: "memory", label: "Memory", section: "Using Edward" },
  { slug: "mind", label: "The mind", section: "Using Edward" },
  { slug: "listening", label: "Listening & pings", section: "Using Edward" },
  { slug: "computer", label: "His computer", section: "Using Edward" },
  { slug: "surfaces", label: "Widget, screen & hub", section: "Using Edward" },
  { slug: "guard-rails", label: "Guard rails", section: "Reference" },
  { slug: "skills", label: "Skills & integrations", section: "Reference" },
  { slug: "adapting", label: "Adapting to your Mac", section: "Reference" },
  { slug: "architecture", label: "Architecture", section: "Reference" },
];

export const DOCS_SECTIONS: DocsSection[] = ["Get started", "Using Edward", "Reference"];

export const GITHUB_URL = "https://github.com/ben4mn/meet-edward";
export const GITHUB_BLOB = `${GITHUB_URL}/blob/main`;
export const GITHUB_TREE = `${GITHUB_URL}/tree/main`;
