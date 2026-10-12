// The site's default Open Graph card, public/og.png (1200×630).
// Not part of the build: run it by hand when the tagline or the pills change,
// and commit the PNG. Needs network for the Inter font, like the post cards.
// Run: node scripts/generate-og-default.mjs

import satori from 'satori';
import sharp from 'sharp';
import { readFileSync, writeFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PUBLIC_DIR = join(__dirname, '..', 'public');

const TAGLINE = 'An AI assistant that remembers, and follows through.';
const PILLS = ['Long-term memory', 'His own computer', 'Runs on your Mac'];

async function loadFonts() {
  const cssRes = await fetch('https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap', {
    headers: { 'User-Agent': 'Mozilla/4.0' },
  });
  if (!cssRes.ok) throw new Error('Failed to fetch Google Fonts CSS');
  const css = await cssRes.text();
  const urls = [...css.matchAll(/url\((https:\/\/[^)]+\.ttf)\)/g)].map((m) => m[1]);
  if (urls.length < 2) throw new Error('Failed to parse font URLs from Google Fonts CSS');
  const [regular, bold] = await Promise.all(urls.slice(0, 2).map((u) => fetch(u).then((r) => r.arrayBuffer())));
  return { regular: Buffer.from(regular), bold: Buffer.from(bold) };
}

const div = (style, children) => ({ type: 'div', props: { style: { display: 'flex', ...style }, children } });

async function main() {
  const fonts = await loadFonts();
  const avatar = `data:image/svg+xml;base64,${readFileSync(join(PUBLIC_DIR, 'favicon.svg')).toString('base64')}`;

  const svg = await satori(
    div(
      {
        width: '1200px',
        height: '630px',
        backgroundColor: '#0f172a',
        backgroundImage: 'radial-gradient(circle at 18% 50%, rgba(82, 183, 136, 0.18) 0%, transparent 38%)',
        fontFamily: 'Inter',
        position: 'relative',
        alignItems: 'center',
        padding: '0 80px',
      },
      [
        div({ position: 'absolute', top: '40px', right: '80px', alignItems: 'center', color: '#94a3b8', fontSize: '18px', fontWeight: 600 }, [
          div({ width: '10px', height: '10px', borderRadius: '5px', backgroundColor: '#52b788', marginRight: '10px' }, []),
          'Open source · Apache 2.0',
        ]),
        { type: 'img', props: { src: avatar, width: 200, height: 200, style: { marginRight: '60px' } } },
        div({ flexDirection: 'column', flex: 1 }, [
          div({ fontSize: '84px', fontWeight: 700, color: '#f8fafc', lineHeight: 1, marginBottom: '20px' }, 'Edward'),
          div({ fontSize: '29px', color: '#cbd5e1', lineHeight: 1.3, marginBottom: '36px' }, TAGLINE),
          div(
            { flexWrap: 'wrap' },
            PILLS.map((pill) =>
              div(
                {
                  color: '#6fcf97',
                  fontSize: '18px',
                  fontWeight: 600,
                  padding: '7px 14px',
                  borderRadius: '24px',
                  border: '1.5px solid rgba(82, 183, 136, 0.45)',
                  backgroundColor: 'rgba(82, 183, 136, 0.10)',
                  marginRight: '10px',
                  marginBottom: '10px',
                },
                pill
              )
            )
          ),
        ]),
        div({ position: 'absolute', bottom: '40px', right: '80px', color: '#64748b', fontSize: '20px' }, 'meet-edward.com'),
      ]
    ),
    {
      width: 1200,
      height: 630,
      fonts: [
        { name: 'Inter', data: fonts.regular, weight: 400, style: 'normal' },
        { name: 'Inter', data: fonts.bold, weight: 700, style: 'normal' },
      ],
    }
  );

  writeFileSync(join(PUBLIC_DIR, 'og.png'), await sharp(Buffer.from(svg)).png().toBuffer());
  console.log('public/og.png generated');
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
