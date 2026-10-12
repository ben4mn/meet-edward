# meet-edward.com

The website for [Edward](https://github.com/ben4mn/meet-edward): the landing page, the docs and the blog. Next.js 14 with a static export (`output: 'export'`), deployed to Cloudflare Pages.

## Working on it

```bash
cd site
npm ci
npm run dev          # http://localhost:3000
npm run lint
npm run build        # static site in out/
```

`npm run build` also runs, before the build, `scripts/generate-sitemap.mjs`, `scripts/generate-feeds.mjs` (RSS and JSON Feed) and `scripts/generate-og-images.mjs` (one Open Graph image per blog post; it fetches the Inter font from Google Fonts), and after it `scripts/generate-llms-full.mjs`, which turns every exported docs page into `out/llms-full.txt` and `public/llms-full.txt`. Commit the public copy when the docs change.

## Where things are

- `app/page.tsx` and `components/` — the landing page
- `app/docs/<slug>/page.tsx` — one docs page each; `lib/docsNav.ts` is the sidebar's list, and `scripts/blog-data.mjs` (`DOC_PAGES`) the sitemap's, so add a page to both
- `lib/blog.tsx` — the posts; `scripts/blog-data.mjs` mirrors their metadata for the build scripts. A post whose features have changed keeps its text and gets an `editorsNote`
- `public/og.png` — the default link-preview card, from `scripts/generate-og-default.mjs` (run by hand when the tagline changes; commit the PNG)
- `public/llms.txt`, `public/llms-full.txt` — machine-readable docs ([llmstxt.org](https://llmstxt.org))
- `public/screenshots/` — app screenshots from the repository's `docs/screenshots` (the UX harness, invented data only), resized for the web
- `public/_redirects` — Cloudflare Pages redirects, including docs pages that were renamed

The docs describe the release in the repository: when the README, `CLAUDE.md`, `docs/ADAPTING.md`, `docs/UPGRADING.md` or `docs/architecture/` change what Edward does, change the matching page here.

## Deploying

A push to `main` that touches `site/**` builds and deploys the site to Cloudflare Pages (`.github/workflows/deploy-site.yml`), and a weekly rebuild publishes blog posts whose date has come. Open a pull request instead, and build it locally first.
