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

async function loadFonts() {
  // Use Google Fonts CSS API with user-agent that triggers TTF format
  const cssRes = await fetch('https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap', {
    headers: { 'User-Agent': 'Mozilla/4.0' },
  });
  if (!cssRes.ok) throw new Error('Failed to fetch Google Fonts CSS');
  const css = await cssRes.text();
  const urls = [...css.matchAll(/url\((https:\/\/[^)]+\.ttf)\)/g)].map(m => m[1]);
  if (urls.length < 2) throw new Error('Failed to parse font URLs from Google Fonts CSS');
  const [regularRes, boldRes] = await Promise.all(urls.slice(0, 2).map(u => fetch(u)));
  if (!regularRes.ok || !boldRes.ok) {
    throw new Error('Failed to download Inter fonts from Google Fonts');
  }
  return {
    regular: Buffer.from(await regularRes.arrayBuffer()),
    bold: Buffer.from(await boldRes.arrayBuffer()),
  };
}

const COLORS = [
  { bg: '#0f172a', accent: '#3b82f6', text: '#f8fafc' },
  { bg: '#0f172a', accent: '#8b5cf6', text: '#f8fafc' },
  { bg: '#0f172a', accent: '#06b6d4', text: '#f8fafc' },
  { bg: '#0f172a', accent: '#10b981', text: '#f8fafc' },
  { bg: '#0f172a', accent: '#f59e0b', text: '#f8fafc' },
  { bg: '#0f172a', accent: '#ef4444', text: '#f8fafc' },
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
              style: { display: 'flex', alignItems: 'center', marginBottom: '24px' },
              children: [
                {
                  type: 'div',
                  props: {
                    style: { backgroundColor: color.accent, color: '#fff', padding: '6px 16px', borderRadius: '20px', fontSize: '18px', fontWeight: 600 },
                    children: tag,
                  },
                },
              ],
            },
          },
          {
            type: 'div',
            props: {
              style: { fontSize: post.title.length > 40 ? '48px' : '56px', fontWeight: 700, color: color.text, lineHeight: 1.2, marginBottom: '32px' },
              children: post.title,
            },
          },
          {
            type: 'div',
            props: {
              style: { display: 'flex', alignItems: 'center', marginTop: 'auto' },
              children: [
                { type: 'div', props: { style: { fontSize: '24px', fontWeight: 700, color: color.accent }, children: 'Edward' } },
                { type: 'div', props: { style: { fontSize: '20px', color: '#94a3b8', marginLeft: '16px' }, children: 'meet-edward.com' } },
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
        { name: 'Inter', data: fonts.regular, weight: 400, style: 'normal' },
        { name: 'Inter', data: fonts.bold, weight: 700, style: 'normal' },
      ],
    }
  );

  return await sharp(Buffer.from(svg)).png().toBuffer();
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
