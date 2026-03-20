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
