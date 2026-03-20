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
  return `  <url>\n    <loc>${loc}</loc>\n    <lastmod>${lastmod}</lastmod>\n    <changefreq>${changefreq}</changefreq>\n    <priority>${priority}</priority>\n  </url>`;
}

const publishedPosts = getPublishedPosts();

const urls = [
  urlEntry(`${SITE_URL}/`, TODAY, 'weekly', '1.0'),
  urlEntry(`${SITE_URL}/docs`, TODAY, 'weekly', '0.8'),
  ...DOC_PAGES.map(slug => urlEntry(`${SITE_URL}/docs/${slug}`, TODAY, 'monthly', '0.6')),
  urlEntry(`${SITE_URL}/blog`, TODAY, 'weekly', '0.8'),
  ...publishedPosts.map(post => urlEntry(`${SITE_URL}/blog/${post.slug}`, post.publishDate, 'monthly', '0.7')),
];

const sitemap = `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls.join('\n')}\n</urlset>\n`;

writeFileSync(join(PUBLIC_DIR, 'sitemap.xml'), sitemap);
console.log(`Sitemap generated: ${publishedPosts.length} blog posts, ${DOC_PAGES.length} doc pages`);
