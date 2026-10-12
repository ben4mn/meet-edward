// llms-full.txt: every docs page as one markdown file, for AI search
// (https://llmstxt.org). Generated from the exported HTML, so it can never
// drift from the pages themselves.
//
// Runs as `postbuild` (after `next build` has written out/): it writes
// out/llms-full.txt and public/llms-full.txt. Commit the public copy when the
// docs change, so `next dev` serves it too.
// Run by hand: node scripts/generate-llms-full.mjs

import { existsSync, readFileSync, writeFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { SITE_URL, DOC_PAGES } from './blog-data.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SITE_DIR = join(__dirname, '..');
const OUT_DIR = join(SITE_DIR, 'out');
const PUBLIC_DIR = join(SITE_DIR, 'public');

const VOID = new Set(['img', 'br', 'hr', 'meta', 'link', 'input', 'source']);
const ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ' };

function decode(text) {
  return text.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e) => {
    if (e[0] === '#') {
      const code = e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
      return String.fromCodePoint(code);
    }
    return ENTITIES[e.toLowerCase()] ?? m;
  });
}

// A small parser for React's server-rendered markup, which is well formed.
function parse(html) {
  const root = { tag: '#root', attrs: {}, children: [] };
  const stack = [root];
  const tokens = html.replace(/<!--[\s\S]*?-->/g, '').match(/<[^>]+>|[^<]+/g) || [];
  for (const token of tokens) {
    const top = stack[stack.length - 1];
    if (token.startsWith('</')) {
      const tag = token.slice(2, -1).trim().toLowerCase();
      for (let i = stack.length - 1; i > 0; i--) {
        if (stack[i].tag === tag) {
          stack.length = i;
          break;
        }
      }
    } else if (token.startsWith('<')) {
      const m = token.match(/^<([a-zA-Z0-9]+)([\s\S]*?)\/?>$/);
      if (!m) continue;
      const tag = m[1].toLowerCase();
      const attrs = {};
      for (const a of m[2].matchAll(/([a-zA-Z_:-]+)="([^"]*)"/g)) attrs[a[1]] = decode(a[2]);
      const node = { tag, attrs, children: [] };
      top.children.push(node);
      if (!VOID.has(tag) && !token.endsWith('/>')) stack.push(node);
    } else {
      top.children.push({ tag: '#text', text: decode(token) });
    }
  }
  return root;
}

function absolute(href) {
  if (!href) return '';
  if (href.startsWith('/')) return SITE_URL + href;
  return href;
}

function textOf(node) {
  if (node.tag === '#text') return node.text;
  return node.children.map(textOf).join('');
}

function inline(node) {
  if (node.tag === '#text') return node.text.replace(/\s+/g, ' ');
  const inner = () => node.children.map(inline).join('');
  switch (node.tag) {
    case 'code':
      return '`' + textOf(node) + '`';
    case 'strong':
    case 'b':
      return '**' + inner().trim() + '**';
    case 'em':
    case 'i':
      return '*' + inner().trim() + '*';
    case 'a':
      return `[${inner().trim()}](${absolute(node.attrs.href)})`;
    case 'br':
      return '\n';
    case 'img':
      return node.attrs.alt ? `![${node.attrs.alt}](${absolute(node.attrs.src)})` : '';
    default:
      return inner();
  }
}

function list(node, depth) {
  const ordered = node.tag === 'ol';
  let n = 0;
  const lines = [];
  for (const item of node.children) {
    if (item.tag !== 'li') continue;
    n += 1;
    const marker = ordered ? `${n}. ` : '- ';
    const pad = '  '.repeat(depth);
    const text = item.children
      .filter((c) => c.tag !== 'ul' && c.tag !== 'ol')
      .map(inline)
      .join('')
      .trim();
    lines.push(pad + marker + text);
    for (const c of item.children) {
      if (c.tag === 'ul' || c.tag === 'ol') lines.push(list(c, depth + 1));
    }
  }
  return lines.join('\n');
}

function table(node) {
  const rows = [];
  const walk = (n) => {
    for (const c of n.children) {
      if (c.tag === 'tr') {
        rows.push(c.children.filter((x) => x.tag === 'td' || x.tag === 'th').map((cell) => inline(cell).trim().replace(/\|/g, '\\|')));
      } else if (c.children) {
        walk(c);
      }
    }
  };
  walk(node);
  if (rows.length === 0) return '';
  const width = Math.max(...rows.map((r) => r.length));
  const line = (r) => '| ' + Array.from({ length: width }, (_, i) => r[i] ?? '').join(' | ') + ' |';
  return [line(rows[0]), '|' + ' --- |'.repeat(width), ...rows.slice(1).map(line)].join('\n');
}

// Page headings move down one level: the file's own title is the only "#".
function blocks(node) {
  const out = [];
  for (const c of node.children) {
    if (c.tag === '#text') {
      const t = c.text.trim();
      if (t) out.push(t);
      continue;
    }
    const cls = c.attrs.class || '';
    switch (c.tag) {
      case 'h1':
        out.push('## ' + inline(c).trim());
        break;
      case 'h2':
        out.push('### ' + inline(c).trim());
        break;
      case 'h3':
        out.push('#### ' + inline(c).trim());
        break;
      case 'p':
        out.push(cls.includes('subtitle') ? '*' + inline(c).trim() + '*' : inline(c).trim());
        break;
      case 'ul':
      case 'ol':
        out.push(list(c, 0));
        break;
      case 'pre':
        out.push('```\n' + textOf(c).replace(/^\n+|\s+$/g, '') + '\n```');
        break;
      case 'table':
        out.push(table(c));
        break;
      case 'hr':
        break;
      case 'figure': {
        const img = c.children.find((x) => x.tag === 'img');
        const caption = c.children.find((x) => x.tag === 'figcaption');
        if (img) out.push(inline(img) + (caption ? '\n*' + inline(caption).trim() + '*' : ''));
        break;
      }
      case 'div':
        if (cls.includes('note')) {
          out.push(blocks(c).split('\n').map((l) => (l ? '> ' + l : '>')).join('\n'));
        } else {
          out.push(blocks(c));
        }
        break;
      default:
        out.push(blocks(c));
    }
  }
  return out.filter((b) => b.trim()).join('\n\n');
}

function page(slug) {
  const html = readFileSync(join(OUT_DIR, 'docs', `${slug}.html`), 'utf8');
  const start = html.indexOf('<article');
  const end = html.indexOf('</article>');
  if (start < 0 || end < 0) throw new Error(`no <article> in docs/${slug}.html`);
  const article = parse(html.slice(start, end + '</article>'.length));
  return blocks(article.children[0]) + `\n\nSource: ${SITE_URL}/docs/${slug}`;
}

function main() {
  if (!existsSync(join(OUT_DIR, 'docs'))) {
    console.warn('llms-full.txt: no out/docs (run next build first); kept the committed copy');
    return;
  }
  const header = [
    '# Edward — Complete Documentation',
    '',
    '> An open-source AI assistant that remembers, notices what matters and works on your Mac, for one person. The Claude API for every model call; macOS on Apple silicon only.',
    '',
    `Website: ${SITE_URL}`,
    'GitHub: https://github.com/ben4mn/meet-edward',
    'Operating guide for AI assistants: https://github.com/ben4mn/meet-edward/blob/main/CLAUDE.md',
    'License: Apache 2.0',
  ].join('\n');
  const body = DOC_PAGES.map(page).join('\n\n---\n\n');
  const text = header + '\n\n---\n\n' + body + '\n';
  writeFileSync(join(OUT_DIR, 'llms-full.txt'), text);
  writeFileSync(join(PUBLIC_DIR, 'llms-full.txt'), text);
  console.log(`llms-full.txt generated: ${DOC_PAGES.length} docs pages, ${text.length} characters`);
}

try {
  main();
} catch (err) {
  // Never fail a deploy over this file: the committed copy still ships.
  console.warn(`llms-full.txt not regenerated: ${err.message}`);
}
