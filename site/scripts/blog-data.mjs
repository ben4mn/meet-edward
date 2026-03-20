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
