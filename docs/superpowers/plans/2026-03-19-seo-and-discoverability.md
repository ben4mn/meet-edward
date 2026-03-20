# SEO & Discoverability Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Maximize Google indexing, search visibility, and GitHub discoverability for the meet-edward project site.

**Architecture:** All changes are additive to the existing static Next.js export site. Build-time scripts generate RSS feeds, OG images, and sitemap from blog post data. Cloudflare Pages handles redirects. GitHub API used for releases and issues.

**Tech Stack:** Node.js build scripts, `satori` + `sharp` for OG image generation, Cloudflare Pages `_redirects`, GitHub CLI (`gh`)

---

## File Structure

### New Files
- `site/scripts/generate-feeds.mjs` — Build-time RSS + JSON Feed generator
- `site/scripts/generate-og-images.mjs` — Build-time OG image generator
- `site/scripts/generate-sitemap.mjs` — Build-time sitemap generator (replaces static XML)
- `site/public/_redirects` — Cloudflare Pages www-to-non-www redirect
- `site/public/manifest.json` — PWA web app manifest
- `site/scripts/blog-data.mjs` — Shared blog post data for all build scripts
- `site/components/FAQSection.tsx` — FAQ component with JSON-LD (flat with other landing components)
- `CONTRIBUTING.md` — Contributor guide

### Modified Files
- `site/package.json` — Add `sharp`, `satori`, build scripts
- `site/app/layout.tsx:16-69` — Add RSS link, manifest link
- `site/app/blog/[slug]/page.tsx:13-41` — Per-post OG image in generateMetadata
- `site/lib/blog.tsx:4-12` — Add `relatedSlugs` field to BlogPost interface
- `site/components/LandingPage.tsx:14-20` — Add FAQSection
- `site/components/blog/BlogArticle.tsx:38-41` — Add related posts section
- `site/public/robots.txt` — Add feed URLs
- `README.md` — Overhaul with badges, better structure
- `site/public/sitemap.xml` — Deleted (replaced by build-time generation)

### Generated at Build Time (gitignored)
- `site/public/og/[slug].png` — Per-post OG images
- `site/public/feed.xml` — RSS 2.0 feed
- `site/public/feed.json` — JSON Feed
- `site/public/sitemap.xml` — Dynamic sitemap

---

### Task 1: www-to-non-www Redirect

**Files:**
- Create: `site/public/_redirects`

This is the highest-impact SEO fix — consolidates domain authority.

- [ ] **Step 1: Create `_redirects` file**

```
# Redirect www to non-www (301 permanent)
https://www.meet-edward.com/* https://meet-edward.com/:splat 301
```

Write this to `site/public/_redirects`.

- [ ] **Step 2: Verify the file is valid Cloudflare Pages format**

Run: `cat site/public/_redirects`
Expected: Two lines — comment and redirect rule.

- [ ] **Step 3: Commit**

```bash
git add site/public/_redirects
git commit -m "seo: add www-to-non-www 301 redirect"
```

---

### Task 2: Shared Blog Data + Build-Time Sitemap Generator

**Files:**
- Create: `site/scripts/blog-data.mjs` — single source of truth for all build scripts
- Create: `site/scripts/generate-sitemap.mjs`
- Modify: `site/package.json:5-9`
- Delete: `site/public/sitemap.xml` (replaced by generated version)

Replaces the static sitemap with a build-time generated one that only includes published posts. Also creates a shared data module so blog metadata lives in one place across all build scripts.

- [ ] **Step 0: Create shared blog data module**

Create `site/scripts/blog-data.mjs` — **all build scripts import from this file**. When a new post is added to `lib/blog.tsx`, update this file too.

```javascript
// Shared blog post metadata for build scripts
// Keep in sync with site/lib/blog.tsx
// All build scripts (sitemap, feeds, OG images) import from here.

export const SITE_URL = 'https://meet-edward.com';

export const BLOG_POSTS = [
  {
    slug: 'why-your-ai-forgets-you',
    title: 'Why Your AI Forgets You',
    description: "Every conversation with ChatGPT starts from zero. Here's why — and how Edward remembers everything.",
    publishDate: '2026-03-04',
    tags: ['AI memory', 'personal AI assistant', 'long-term memory AI'],
  },
  {
    slug: 'the-heartbeat-system',
    title: 'The Heartbeat: An AI That Pays Attention',
    description: 'Most AI assistants wait for you to talk. Edward watches your messages, calendar, and email — and acts on its own.',
    publishDate: '2026-03-11',
    tags: ['proactive AI', 'AI monitoring', 'heartbeat system', 'AI automation'],
  },
  {
    slug: 'self-evolving-ai',
    title: 'Self-Evolving AI: Teaching Edward to Rewrite Himself',
    description: "What happens when you give an AI the ability to modify its own source code? Edward's evolution engine finds out.",
    publishDate: '2026-03-18',
    tags: ['self-evolving AI', 'AI self-improvement', 'automated code generation', 'Claude Code'],
  },
  {
    slug: 'how-ai-memory-works',
    title: 'How AI Memory Actually Works',
    description: 'Vector search, BM25 ranking, memory consolidation — the architecture behind an AI that truly remembers.',
    publishDate: '2026-03-25',
    tags: ['AI memory architecture', 'vector search', 'memory consolidation', 'memory types'],
  },
  {
    slug: 'agents-that-work-while-you-sleep',
    title: 'Agents That Work While You Sleep',
    description: "Edward's orchestrator spawns worker agents that run tasks autonomously — scheduling, research, monitoring.",
    publishDate: '2026-04-01',
    tags: ['AI orchestrator', 'multi-agent AI', 'scheduled AI tasks', 'autonomous AI'],
  },
  {
    slug: 'why-i-built-edward',
    title: 'Why I Built Edward',
    description: 'The story behind building an open-source AI assistant that remembers, evolves, and runs on your machine.',
    publishDate: '2026-04-08',
    tags: ['building AI assistants', 'open source AI', 'personal AI project', 'self-hosted AI'],
  },
];

export const DOC_PAGES = [
  'introduction', 'beginner-guide', 'setup-with-ai', 'getting-started',
  'configuration', 'platform-support', 'architecture', 'skills',
  'memory', 'heartbeat', 'widget', 'file-storage', 'orchestrator',
];

export function getPublishedPosts() {
  const today = new Date().toISOString().split('T')[0];
  return BLOG_POSTS.filter(p => p.publishDate <= today);
}
```

- [ ] **Step 1: Create the sitemap generator script**

Create `site/scripts/generate-sitemap.mjs`:

```javascript
// Build-time sitemap generator
// Run: node scripts/generate-sitemap.mjs

import { writeFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { SITE_URL, DOC_PAGES, getPublishedPosts } from './blog-data.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PUBLIC_DIR = join(__dirname, '..', 'public');
const TODAY = new Date().toISOString().split('T')[0];

function urlEntry(loc, lastmod, changefreq, priority) {
  return `  <url>
    <loc>${loc}</loc>
    <lastmod>${lastmod}</lastmod>
    <changefreq>${changefreq}</changefreq>
    <priority>${priority}</priority>
  </url>`;
}

const publishedPosts = getPublishedPosts();

const urls = [
  urlEntry(`${SITE_URL}/`, TODAY, 'weekly', '1.0'),
  urlEntry(`${SITE_URL}/docs`, TODAY, 'weekly', '0.8'),
  ...DOC_PAGES.map(slug =>
    urlEntry(`${SITE_URL}/docs/${slug}`, TODAY, 'monthly', '0.6')
  ),
  urlEntry(`${SITE_URL}/blog`, TODAY, 'weekly', '0.8'),
  ...publishedPosts.map(post =>
    urlEntry(`${SITE_URL}/blog/${post.slug}`, post.publishDate, 'monthly', '0.7')
  ),
];

const sitemap = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls.join('\n')}
</urlset>
`;

writeFileSync(join(PUBLIC_DIR, 'sitemap.xml'), sitemap);
console.log(`Sitemap generated: ${publishedPosts.length} blog posts, ${DOC_PAGES.length} doc pages`);
```

- [ ] **Step 2: Delete the static sitemap**

```bash
rm site/public/sitemap.xml
```

- [ ] **Step 3: Add sitemap.xml to .gitignore**

Append to `site/.gitignore` (create if needed):
```
# Generated at build time
/public/sitemap.xml
```

- [ ] **Step 4: Update package.json build script**

In `site/package.json`, change the `build` script:

```json
"prebuild": "node scripts/generate-sitemap.mjs",
"build": "next build",
```

- [ ] **Step 5: Test the script**

Run: `cd site && node scripts/generate-sitemap.mjs`
Expected: "Sitemap generated: 3 blog posts, 13 doc pages" and `site/public/sitemap.xml` exists with only published posts.

- [ ] **Step 6: Commit**

```bash
git add site/scripts/generate-sitemap.mjs site/package.json site/.gitignore
git commit -m "seo: replace static sitemap with build-time generator"
```

---

### Task 3: RSS + JSON Feed

**Files:**
- Create: `site/scripts/generate-feeds.mjs`
- Modify: `site/package.json:5-9` — add to prebuild
- Modify: `site/app/layout.tsx:66-68` — add RSS alternate link
- Modify: `site/public/robots.txt` — add feed reference

- [ ] **Step 1: Create the feed generator script**

Create `site/scripts/generate-feeds.mjs`:

```javascript
// Build-time RSS + JSON Feed generator
// Run: node scripts/generate-feeds.mjs

import { writeFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { SITE_URL, getPublishedPosts } from './blog-data.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PUBLIC_DIR = join(__dirname, '..', 'public');

function escapeXml(str) {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

const published = getPublishedPosts();

// RSS 2.0
const rssItems = published.map(post => `    <item>
      <title>${escapeXml(post.title)}</title>
      <link>${SITE_URL}/blog/${post.slug}</link>
      <guid isPermaLink="true">${SITE_URL}/blog/${post.slug}</guid>
      <description>${escapeXml(post.description)}</description>
      <pubDate>${new Date(post.publishDate).toUTCString()}</pubDate>
      ${post.tags.map(t => `<category>${escapeXml(t)}</category>`).join('\n      ')}
    </item>`).join('\n');

const rss = `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Edward Blog</title>
    <link>${SITE_URL}/blog</link>
    <description>Articles about building a self-hosted AI assistant with long-term memory, self-evolution, and proactive monitoring.</description>
    <language>en-us</language>
    <lastBuildDate>${new Date().toUTCString()}</lastBuildDate>
    <atom:link href="${SITE_URL}/feed.xml" rel="self" type="application/rss+xml"/>
${rssItems}
  </channel>
</rss>
`;

// JSON Feed 1.1
const jsonFeed = {
  version: 'https://jsonfeed.org/version/1.1',
  title: 'Edward Blog',
  home_page_url: `${SITE_URL}/blog`,
  feed_url: `${SITE_URL}/feed.json`,
  description: 'Articles about building a self-hosted AI assistant with long-term memory, self-evolution, and proactive monitoring.',
  authors: [{ name: 'Ben Foreman', url: SITE_URL }],
  items: published.map(post => ({
    id: `${SITE_URL}/blog/${post.slug}`,
    url: `${SITE_URL}/blog/${post.slug}`,
    title: post.title,
    summary: post.description,
    date_published: `${post.publishDate}T00:00:00Z`,
    tags: post.tags,
  })),
};

writeFileSync(join(PUBLIC_DIR, 'feed.xml'), rss);
writeFileSync(join(PUBLIC_DIR, 'feed.json'), JSON.stringify(jsonFeed, null, 2));
console.log(`Feeds generated: ${published.length} published posts`);
```

- [ ] **Step 2: Add feed files to .gitignore**

Append to `site/.gitignore`:
```
/public/feed.xml
/public/feed.json
```

- [ ] **Step 3: Update prebuild script**

In `site/package.json`, update `prebuild`:
```json
"prebuild": "node scripts/generate-sitemap.mjs && node scripts/generate-feeds.mjs",
```

- [ ] **Step 4: Add RSS link to layout.tsx**

In `site/app/layout.tsx`, in the `alternates` section (around line 66-68), add the RSS feed link:

```typescript
alternates: {
  canonical: 'https://meet-edward.com',
  types: {
    'application/rss+xml': 'https://meet-edward.com/feed.xml',
    'application/feed+json': 'https://meet-edward.com/feed.json',
  },
},
```

- [ ] **Step 5: Add feed references to robots.txt**

Append before the Sitemap line in `site/public/robots.txt`:
```
# Feeds
# /feed.xml  — RSS 2.0
# /feed.json — JSON Feed 1.1
```

- [ ] **Step 6: Test the script**

Run: `cd site && node scripts/generate-feeds.mjs`
Expected: "Feeds generated: 3 published posts" and both `feed.xml` and `feed.json` exist in `site/public/`.

- [ ] **Step 7: Commit**

```bash
git add site/scripts/generate-feeds.mjs site/package.json site/app/layout.tsx site/public/robots.txt site/.gitignore
git commit -m "seo: add RSS and JSON Feed generation at build time"
```

---

### Task 4: OG Image Generation

**Files:**
- Create: `site/scripts/generate-og-images.mjs`
- Modify: `site/package.json` — add `satori`, `sharp` deps + prebuild step
- Modify: `site/app/blog/[slug]/page.tsx:13-41` — per-post OG image

- [ ] **Step 1: Install dependencies**

```bash
cd site && npm install --save-dev satori sharp
```

- [ ] **Step 2: Create the OG image generator script**

Create `site/scripts/generate-og-images.mjs`:

```javascript
// Build-time OG image generator
// Uses satori (SVG) + sharp (PNG) to render per-post OG images
// Requires Node.js >= 18 (uses global fetch)
// Run: node scripts/generate-og-images.mjs

import satori from 'satori';
import sharp from 'sharp';
import { writeFileSync, mkdirSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { getPublishedPosts } from './blog-data.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PUBLIC_DIR = join(__dirname, '..', 'public');
const OG_DIR = join(PUBLIC_DIR, 'og');

// Download Inter font (regular + bold) from Google Fonts
// Requires Node.js >= 18 for global fetch
async function loadFonts() {
  const [regularRes, boldRes] = await Promise.all([
    fetch('https://fonts.gstatic.com/s/inter/v18/UcCO3FwrK3iLTeHuS_nVMrMxCp50SjIw2boKoduKmMEVuLyfAZ9hiA.woff2'),
    fetch('https://fonts.gstatic.com/s/inter/v18/UcCO3FwrK3iLTeHuS_nVMrMxCp50SjIw2boKoduKmMEVuGKYAZ9hiA.woff2'),
  ]);
  if (!regularRes.ok || !boldRes.ok) {
    throw new Error('Failed to download Inter fonts from Google Fonts');
  }
  return {
    regular: Buffer.from(await regularRes.arrayBuffer()),
    bold: Buffer.from(await boldRes.arrayBuffer()),
  };
}

// Color palette for variety
const COLORS = [
  { bg: '#0f172a', accent: '#3b82f6', text: '#f8fafc' }, // blue
  { bg: '#0f172a', accent: '#8b5cf6', text: '#f8fafc' }, // purple
  { bg: '#0f172a', accent: '#06b6d4', text: '#f8fafc' }, // cyan
  { bg: '#0f172a', accent: '#10b981', text: '#f8fafc' }, // green
  { bg: '#0f172a', accent: '#f59e0b', text: '#f8fafc' }, // amber
  { bg: '#0f172a', accent: '#ef4444', text: '#f8fafc' }, // red
];

async function generateImage(post, index, fonts) {
  const color = COLORS[index % COLORS.length];
  const tag = post.tags[0] || '';

  const svg = await satori(
    {
      type: 'div',
      props: {
        style: {
          width: '1200px',
          height: '630px',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          padding: '60px 80px',
          backgroundColor: color.bg,
          fontFamily: 'Inter',
        },
        children: [
          {
            type: 'div',
            props: {
              style: {
                display: 'flex',
                alignItems: 'center',
                marginBottom: '24px',
              },
              children: [
                {
                  type: 'div',
                  props: {
                    style: {
                      backgroundColor: color.accent,
                      color: '#fff',
                      padding: '6px 16px',
                      borderRadius: '20px',
                      fontSize: '18px',
                      fontWeight: 600,
                    },
                    children: tag,
                  },
                },
              ],
            },
          },
          {
            type: 'div',
            props: {
              style: {
                fontSize: post.title.length > 40 ? '48px' : '56px',
                fontWeight: 700,
                color: color.text,
                lineHeight: 1.2,
                marginBottom: '32px',
              },
              children: post.title,
            },
          },
          {
            type: 'div',
            props: {
              style: {
                display: 'flex',
                alignItems: 'center',
                marginTop: 'auto',
              },
              children: [
                {
                  type: 'div',
                  props: {
                    style: {
                      fontSize: '24px',
                      fontWeight: 700,
                      color: color.accent,
                    },
                    children: 'Edward',
                  },
                },
                {
                  type: 'div',
                  props: {
                    style: {
                      fontSize: '20px',
                      color: '#94a3b8',
                      marginLeft: '16px',
                    },
                    children: 'meet-edward.com',
                  },
                },
              ],
            },
          },
        ],
      },
    },
    {
      width: 1200,
      height: 630,
      fonts: [
        {
          name: 'Inter',
          data: fonts.regular,
          weight: 400,
          style: 'normal',
        },
        {
          name: 'Inter',
          data: fonts.bold,
          weight: 700,
          style: 'normal',
        },
      ],
    }
  );

  const png = await sharp(Buffer.from(svg)).png().toBuffer();
  return png;
}

async function main() {
  if (!existsSync(OG_DIR)) mkdirSync(OG_DIR, { recursive: true });

  const fonts = await loadFonts();
  const published = getPublishedPosts();

  for (let i = 0; i < published.length; i++) {
    const post = published[i];
    const outputPath = join(OG_DIR, `${post.slug}.png`);
    const png = await generateImage(post, i, fonts);
    writeFileSync(outputPath, png);
    console.log(`  Generated: og/${post.slug}.png`);
  }

  console.log(`OG images generated: ${published.length} posts`);
}

main().catch(console.error);
```

- [ ] **Step 3: Add OG directory to .gitignore**

Append to `site/.gitignore`:
```
/public/og/
```

- [ ] **Step 4: Update prebuild script**

In `site/package.json`:
```json
"prebuild": "node scripts/generate-sitemap.mjs && node scripts/generate-feeds.mjs && node scripts/generate-og-images.mjs",
```

- [ ] **Step 5: Update blog post generateMetadata for per-post OG images**

In `site/app/blog/[slug]/page.tsx`, inside `generateMetadata()`, update the openGraph section to use per-post images:

Find the openGraph section and update the `images` field:
```typescript
openGraph: {
  title: post.title,
  description: post.description,
  url: `/blog/${post.slug}`,
  type: 'article',
  publishedTime: post.publishDate,
  authors: ['Ben Foreman'],
  tags: post.tags,
  images: [`/og/${post.slug}.png`],
},
```

Also update twitter images:
```typescript
twitter: {
  card: 'summary_large_image',
  title: post.title,
  description: post.description,
  images: [`/og/${post.slug}.png`],
},
```

- [ ] **Step 6: Test the script**

Run: `cd site && node scripts/generate-og-images.mjs`
Expected: PNG files generated in `site/public/og/` for each published post.

- [ ] **Step 7: Commit**

```bash
git add site/scripts/generate-og-images.mjs site/package.json site/app/blog/\\[slug\\]/page.tsx site/.gitignore
git commit -m "seo: add build-time OG image generation per blog post"
```

---

### Task 5: FAQ Section + JSON-LD

**Files:**
- Create: `site/components/FAQSection.tsx`
- Modify: `site/components/LandingPage.tsx:1-9,14-20`

- [ ] **Step 1: Create the FAQ component**

Create `site/components/FAQSection.tsx`:

```tsx
'use client';

import { useState } from 'react';

const FAQ_ITEMS = [
  {
    question: 'What is Edward?',
    answer: 'Edward is an open-source AI assistant with persistent long-term memory. Unlike ChatGPT or Claude, Edward remembers every conversation, learns your preferences over time, and runs entirely on your own machine.',
  },
  {
    question: 'Is my data private?',
    answer: 'Yes. Edward runs locally on your hardware. Your conversations, memories, and documents never leave your machine. You own your data completely.',
  },
  {
    question: 'What AI models does Edward use?',
    answer: 'Edward uses Anthropic\'s Claude models via API. You bring your own API key, so you have full control over costs and model selection.',
  },
  {
    question: 'How is Edward different from ChatGPT?',
    answer: 'Three key differences: Edward remembers everything across conversations using persistent vector memory. Edward can proactively monitor your messages, calendar, and email. And Edward runs on your machine — your data stays private.',
  },
  {
    question: 'Can Edward send messages on my behalf?',
    answer: 'Yes. Edward integrates with iMessage, SMS, and WhatsApp. It can send messages, respond to incoming messages, and even schedule future messages and reminders.',
  },
  {
    question: 'Is Edward free to use?',
    answer: 'Edward is free and open-source under the Apache 2.0 license. The only cost is your Anthropic API usage, which you pay directly to Anthropic.',
  },
];

export default function FAQSection() {
  const [openIndex, setOpenIndex] = useState<number | null>(null);

  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity: FAQ_ITEMS.map(item => ({
      '@type': 'Question',
      name: item.question,
      acceptedAnswer: {
        '@type': 'Answer',
        text: item.answer,
      },
    })),
  };

  return (
    <section className="py-24 px-6">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />
      <div className="max-w-3xl mx-auto">
        <h2 className="text-3xl font-bold text-center text-white mb-12">
          Frequently Asked Questions
        </h2>
        <div className="space-y-4">
          {FAQ_ITEMS.map((item, index) => (
            <div
              key={index}
              className="border border-white/10 rounded-lg overflow-hidden"
            >
              <button
                onClick={() => setOpenIndex(openIndex === index ? null : index)}
                className="w-full flex items-center justify-between p-5 text-left text-white hover:bg-white/5 transition-colors"
              >
                <span className="font-medium pr-4">{item.question}</span>
                <span className="text-white/50 shrink-0">
                  {openIndex === index ? '−' : '+'}
                </span>
              </button>
              {openIndex === index && (
                <div className="px-5 pb-5 text-white/70 leading-relaxed">
                  {item.answer}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
```

- [ ] **Step 2: Add FAQSection to LandingPage**

In `site/components/LandingPage.tsx`, add the import:
```typescript
import FAQSection from './FAQSection';
```

Add `<FAQSection />` between `<TechOverview />` and `<LandingFooter />` in the render.

- [ ] **Step 3: Verify it renders**

Run: `cd site && npm run dev`
Open `http://localhost:3000` and scroll to the FAQ section. Verify accordion opens/closes. View page source and confirm JSON-LD is present.

- [ ] **Step 4: Commit**

```bash
git add site/components/FAQSection.tsx site/components/LandingPage.tsx
git commit -m "seo: add FAQ section with FAQPage JSON-LD structured data"
```

---

### Task 6: PWA Manifest

**Files:**
- Create: `site/public/manifest.json`
- Modify: `site/app/layout.tsx:38-41`

- [ ] **Step 1: Create manifest.json**

Create `site/public/manifest.json`:

```json
{
  "name": "Edward — AI Assistant with Long-Term Memory",
  "short_name": "Edward",
  "description": "Open-source AI assistant that remembers every conversation, evolves its own code, and runs on your machine.",
  "start_url": "/",
  "display": "browser",
  "background_color": "#0f172a",
  "theme_color": "#0f172a",
  "icons": [
    {
      "src": "/favicon.svg",
      "sizes": "any",
      "type": "image/svg+xml"
    }
  ]
}
```

- [ ] **Step 2: Add manifest link to layout.tsx**

In `site/app/layout.tsx`, in the `icons` section of metadata (around line 38-41), add manifest as part of the metadata or as a `<link>` in the head. Since Next.js metadata API supports manifest:

Add to the metadata export:
```typescript
manifest: '/manifest.json',
```

- [ ] **Step 3: Commit**

```bash
git add site/public/manifest.json site/app/layout.tsx
git commit -m "seo: add PWA web app manifest"
```

---

### Task 7: Internal Linking — Related Posts

**Files:**
- Modify: `site/lib/blog.tsx:4-12` — add relatedSlugs to interface
- Modify: `site/lib/blog.tsx` — add relatedSlugs data to each post
- Modify: `site/components/blog/BlogArticle.tsx:38-41` — render related posts

- [ ] **Step 1: Add relatedSlugs to BlogPost interface**

In `site/lib/blog.tsx`, add `relatedSlugs` to the BlogPost interface (around line 4-12):

```typescript
relatedSlugs?: string[];
```

- [ ] **Step 2: Add relatedSlugs to each blog post**

Add `relatedSlugs` to each blog post object in the `blogPosts` array:

```
why-your-ai-forgets-you:    relatedSlugs: ['how-ai-memory-works', 'the-heartbeat-system']
the-heartbeat-system:       relatedSlugs: ['why-your-ai-forgets-you', 'agents-that-work-while-you-sleep']
self-evolving-ai:           relatedSlugs: ['agents-that-work-while-you-sleep', 'why-i-built-edward']
how-ai-memory-works:        relatedSlugs: ['why-your-ai-forgets-you', 'the-heartbeat-system']
agents-that-work-while-you-sleep: relatedSlugs: ['self-evolving-ai', 'the-heartbeat-system']
why-i-built-edward:         relatedSlugs: ['why-your-ai-forgets-you', 'self-evolving-ai']
```

- [ ] **Step 3: Add related posts section to BlogArticle**

The existing `getPublishedPost(slug)` function in `site/lib/blog.tsx` (line 623) already returns a post by slug with publish-date filtering — use that instead of creating a new helper. This ensures unpublished related posts won't appear.

In `site/components/blog/BlogArticle.tsx`, add the related posts section inside the `<article>` tag, after the `<div className="docs-prose">{post.content()}</div>` line (line 38), before the closing `</article>`:

```tsx
{post.relatedSlugs && post.relatedSlugs.length > 0 && (
  <div className="mt-16 pt-8 border-t border-white/10">
    <h3 className="text-lg font-semibold text-white mb-4">Related Posts</h3>
    <div className="grid gap-4 sm:grid-cols-2">
      {post.relatedSlugs.map(slug => {
        const related = getPublishedPost(slug);
        if (!related) return null;
        return (
          <a
            key={slug}
            href={`/blog/${slug}`}
            className="block p-4 rounded-lg border border-white/10 hover:border-white/20 hover:bg-white/5 transition-colors"
          >
            <div className="font-medium text-white mb-1">{related.title}</div>
            <div className="text-sm text-white/50">{related.readingTime} read</div>
          </a>
        );
      })}
    </div>
  </div>
)}
```

Import `getPublishedPost` from `@/lib/blog` at the top of BlogArticle.tsx.

- [ ] **Step 4: Verify rendering**

Run: `cd site && npm run dev`
Navigate to any blog post and verify the "Related Posts" section appears at the bottom.

- [ ] **Step 5: Commit**

```bash
git add site/lib/blog.tsx site/components/blog/BlogArticle.tsx
git commit -m "seo: add related posts for internal linking between blog articles"
```

---

### Task 8: GitHub README Overhaul

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Rewrite README.md**

Overhaul the README with:
- Better badge row (license, stars, PRs welcome, last commit, site link)
- One-line pitch at the top
- Feature highlights with clear visual hierarchy
- Architecture diagram (ASCII or mermaid)
- Screenshots section (keep existing)
- Quick start (streamlined)
- "Why Edward?" comparison section
- Links to blog and docs

Keep existing content but restructure. The README is 183 lines currently — target ~200 lines, better organized.

Key badges to add:
```markdown
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Stars](https://img.shields.io/github/stars/ben4mn/meet-edward)](https://github.com/ben4mn/meet-edward/stargazers)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)
[![Last Commit](https://img.shields.io/github/last-commit/ben4mn/meet-edward)](https://github.com/ben4mn/meet-edward/commits/main)
[![Website](https://img.shields.io/badge/Website-meet--edward.com-blue)](https://meet-edward.com)
```

- [ ] **Step 2: Review and verify links**

Check that all links in the README resolve correctly (docs, blog, license, etc.).

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: overhaul README with badges, better structure, and feature highlights"
```

---

### Task 9: CONTRIBUTING.md

**Files:**
- Create: `CONTRIBUTING.md`

- [ ] **Step 1: Create CONTRIBUTING.md**

Write a contributor guide covering:
- Development setup (prerequisites, clone, setup.sh)
- Project structure overview (frontend/backend/site)
- How to run the dev environment
- Code style (Python: ruff/black, TypeScript: ESLint)
- PR process (fork, branch, test, PR)
- Issue labels explained
- Where to ask questions

Keep it concise — ~80-100 lines.

- [ ] **Step 2: Commit**

```bash
git add CONTRIBUTING.md
git commit -m "docs: add CONTRIBUTING.md for new contributors"
```

---

### Task 10: GitHub Release

**Files:** None (GitHub API only)

- [ ] **Step 1: Create v1.0.0 release**

```bash
gh release create v1.0.0 \
  --title "v1.0.0 — Edward" \
  --notes "$(cat <<'NOTES'
## Edward v1.0.0

The first official release of Edward — a self-hosted AI assistant with persistent memory.

### Highlights
- **Long-term memory** — hybrid vector + BM25 search with memory consolidation
- **Self-evolution** — Edward can modify his own source code via Claude Code
- **Heartbeat system** — proactive monitoring of iMessage, Calendar, and Email
- **Orchestrator** — spawns worker agents for autonomous task execution
- **Multi-channel messaging** — iMessage, SMS, WhatsApp integration
- **Scheduled events** — self-triggering reminders and tasks
- **Document store** — persistent document storage with semantic search
- **Code execution** — Python, JavaScript, SQL, and Shell sandboxes
- **Skills system** — modular integrations (Brave Search, Apple Services, HTML Hosting)
- **Custom MCP servers** — discover and install MCP servers at runtime
- **Widget** — iOS home screen widget via Scriptable
- **Push notifications** — Web Push via VAPID

### Tech Stack
Next.js + FastAPI + PostgreSQL + LangGraph + Claude

### Links
- Website: https://meet-edward.com
- Docs: https://meet-edward.com/docs
- Blog: https://meet-edward.com/blog
NOTES
)"
```

- [ ] **Step 2: Verify release**

Run: `gh release view v1.0.0`
Expected: Release shows with correct title, notes, and tag.

---

### Task 11: Good First Issues

**Files:** None (GitHub API only)

- [ ] **Step 1: Create labeled issues**

Create 4-5 issues with the `good first issue` label:

```bash
gh label create "good first issue" --color 7057ff --description "Good for newcomers" 2>/dev/null || true

gh issue create --title "Add unit tests for memory search ranking" \
  --body "The hybrid search (vector + BM25) in \`backend/services/memory_service.py\` lacks unit tests. Add tests that verify:\n- Vector similarity scoring\n- BM25 keyword matching\n- Combined ranking with 70/30 weights\n\nGood entry point to understand the memory system." \
  --label "good first issue"

gh issue create --title "Add loading skeleton to blog index page" \
  --body "The blog index at \`site/app/blog/page.tsx\` could benefit from a loading skeleton while posts render.\n\n**Files:** \`site/components/blog/BlogIndexContent.tsx\`\n\nUse the existing Tailwind classes for a pulse animation skeleton." \
  --label "good first issue"

gh issue create --title "Add dark/light theme toggle to docs" \
  --body "The docs pages currently only support dark mode. Add a theme toggle that respects \`prefers-color-scheme\` and persists choice to localStorage.\n\n**Files:** \`site/app/docs/layout.tsx\`, new component \`site/components/docs/ThemeToggle.tsx\`" \
  --label "good first issue"

gh issue create --title "Add search to document browser in settings" \
  --body "The DocumentBrowser component in settings shows all documents but has no search/filter. Add a text input that filters by title and tags.\n\n**Files:** \`frontend/components/settings/DocumentBrowser.tsx\`" \
  --label "good first issue"

gh issue create --title "Improve error messages for skill connection failures" \
  --body "When a skill fails to connect (e.g., Twilio credentials wrong), the error messages in the skills panel are generic. Surface the actual error from the backend.\n\n**Files:** \`frontend/components/settings/SkillsPanel.tsx\`, \`backend/services/skills_service.py\`" \
  --label "good first issue"
```

- [ ] **Step 2: Verify issues**

Run: `gh issue list --label "good first issue"`
Expected: 5 issues listed.

---

### Task 12: Canonical URL Audit + Final Verification

**Files:** Potentially modify layout.tsx or blog metadata if issues found

- [ ] **Step 1: Audit canonical consistency**

Check every page's metadata for canonical URL consistency:
- All should use `https://meet-edward.com` (not `www`, not `http`)
- Blog posts should have canonical `/blog/{slug}`
- Doc pages should have canonical `/docs/{slug}`

Run: `cd site && grep -r "canonical" app/ --include="*.tsx" -n`
Expected: All canonical URLs use `meet-edward.com` (no www).

- [ ] **Step 2: Verify metadataBase cascading**

In `site/app/layout.tsx`, confirm `metadataBase` is set to `https://meet-edward.com`. This means all relative canonical URLs (`/blog/slug`) resolve correctly.

- [ ] **Step 3: Build and verify everything**

Run the full build to ensure nothing is broken:

```bash
cd site && npm run build
```

Expected: Build succeeds. Check `site/out/` for:
- `sitemap.xml` — contains only published posts
- `feed.xml` — valid RSS with published posts
- `feed.json` — valid JSON Feed
- `og/*.png` — OG images for published posts
- `manifest.json` — present

- [ ] **Step 4: Deploy**

```bash
gh workflow run weekly-rebuild.yml --ref main
```

- [ ] **Step 5: Resubmit sitemap in Google Search Console (MANUAL)**

This step requires browser interaction. Navigate to Google Search Console > Sitemaps and resubmit `https://meet-edward.com/sitemap.xml` to pick up the newly generated version. (Already done once earlier in this session — repeat after deploy.)

---

## Execution Order

Tasks are ordered by dependency and impact:

1. **Task 1** (www redirect) — no deps, highest SEO impact
2. **Task 2** (sitemap generator) — no deps, fixes crawl issues
3. **Task 3** (RSS feeds) — depends on Task 2 (shared prebuild)
4. **Task 4** (OG images) — depends on Task 3 (shared prebuild)
5. **Task 5** (FAQ + JSON-LD) — no deps, standalone
6. **Task 6** (manifest) — no deps, standalone
7. **Task 7** (internal linking) — no deps, standalone
8. **Task 8** (README) — no deps, standalone
9. **Task 9** (CONTRIBUTING.md) — no deps, standalone
10. **Task 10** (GitHub release) — after Task 8-9 (README + CONTRIBUTING should exist)
11. **Task 11** (good first issues) — after Task 10 (release should exist)
12. **Task 12** (audit + deploy) — final step, after all others

Tasks 5-9 are independent and can be parallelized.
