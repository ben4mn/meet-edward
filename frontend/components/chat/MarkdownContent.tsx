"use client";

// Edward's answers, rendered (2026-09-26). The components map is hoisted and
// the whole component memoised, so only the message that is streaming
// re-parses. One body size (text-body, 15/22) in Inter; headings on the type
// scale; tables and code may run wider than the reading column on desktop.
// Links: into the app → next/link, same tab; off the app → new tab with ↗;
// a stored file → a download chip (lib/answerText.ts classifyLink).

import React, { memo, useCallback, useState, type ReactNode } from "react";
import Link from "next/link";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { PrismAsync as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { ArrowUpRight, Check, Copy, Download } from "lucide-react";
import { classifyLink } from "@/lib/answerText";

// Prism with every language (refractor) is ~200 kB gzipped, most of /chat's
// first load, and no first paint needs it (2026-09-26 perf pass). PrismAsync
// is the same highlighter with the same languages, loaded as its own chunk:
// until it lands a block shows its code uncoloured in the same box, font and
// line height (no shift), then colours in place. The fetch starts once the
// page is idle, so by the time an answer with code arrives it is there.
// __tests__/firstLoadBundle.test.ts pins that the sync Prism stays out.
type AsyncHighlighter = {
  preload?: () => Promise<void>;
  astGenerator?: unknown;
  astGeneratorPromise?: Promise<void> | null;
};
if (typeof window !== "undefined") {
  const H = SyntaxHighlighter as unknown as AsyncHighlighter;
  const preload = () => {
    if (H.astGenerator || H.astGeneratorPromise || !H.preload) return; // a code block got there first
    H.preload().catch(() => {
      H.astGeneratorPromise = null; // the next code block to mount tries again
    });
  };
  if (typeof window.requestIdleCallback === "function") window.requestIdleCallback(preload, { timeout: 2000 });
  else window.setTimeout(preload, 1200);
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  }, [text]);
  return (
    <button
      type="button"
      onClick={handleCopy}
      className="flex h-8 w-8 items-center justify-center rounded-xs text-white/60 transition-colors duration-fast hover:bg-white/10 hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-terminal/60"
      aria-label={copied ? "Copied" : "Copy code"}
    >
      {copied ? <Check className="h-3.5 w-3.5 text-terminal" /> : <Copy className="h-3.5 w-3.5" />}
    </button>
  );
}

function currentOrigin(): string | null {
  return typeof window !== "undefined" ? window.location.origin : null;
}

const LINK_CLASS =
  "text-terminal-strong underline decoration-terminal/35 underline-offset-[3px] transition-colors duration-fast hover:decoration-terminal";

function AnswerLink({ href, children }: { href?: string; children?: ReactNode }) {
  const link = classifyLink(href, currentOrigin());
  if (link.kind === "download") {
    return (
      <a
        href={link.href}
        download
        className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-border bg-surface px-2.5 py-1 align-middle text-footnote text-text-primary no-underline shadow-e1 transition-colors duration-fast hover:bg-surface-3"
      >
        <Download className="h-3.5 w-3.5 flex-none text-text-secondary" aria-hidden />
        <span className="truncate">{children}</span>
      </a>
    );
  }
  if (link.kind === "internal") {
    return (
      <Link href={link.href} className={LINK_CLASS}>
        {children}
      </Link>
    );
  }
  if (link.kind === "external") {
    return (
      <a href={link.href} target="_blank" rel="noopener noreferrer" className={LINK_CLASS}>
        {children}
        <ArrowUpRight className="ml-px inline h-3.5 w-3.5 align-[-2px]" aria-label="(opens in a new tab)" />
      </a>
    );
  }
  return <span>{children}</span>;
}

// Wider than the reading column on large screens (the column is max-w-3xl).
const BREAKOUT = "xl:-mx-10";

const COMPONENTS: Components = {
  code({ className, children, ...props }) {
    const match = /language-(\w+)/.exec(className || "");
    const raw = String(children);
    const codeString = raw.replace(/\n$/, "");
    // A fenced block's text always ends in a newline (even without a
    // language); inline code never does.
    const isBlock = Boolean(match) || raw.endsWith("\n") || codeString.includes("\n");
    if (isBlock) {
      const language = match?.[1] || "text";
      return (
        <div className={`my-4 overflow-hidden rounded-md border border-white/[.06] bg-[#0f141b] shadow-e1 ${BREAKOUT}`}>
          <div className="flex h-9 items-center justify-between pl-3.5 pr-1">
            <span className="font-mono text-[11px] uppercase tracking-wide text-white/45">{language}</span>
            <CopyButton text={codeString} />
          </div>
          <SyntaxHighlighter
            style={oneDark}
            language={language}
            PreTag="div"
            customStyle={{ margin: 0, padding: "0 14px 14px", background: "transparent", fontSize: "12.5px", lineHeight: "20px" }}
            codeTagProps={{ style: { fontFamily: "var(--font-jetbrains), ui-monospace, monospace" } }}
          >
            {codeString}
          </SyntaxHighlighter>
        </div>
      );
    }
    return (
      <code className="rounded-xs bg-surface-3 px-1.5 py-0.5 font-mono text-[0.88em] text-text-primary [overflow-wrap:anywhere]" {...props}>
        {children}
      </code>
    );
  },
  pre({ children }) {
    return <>{children}</>;
  },
  a({ href, children }) {
    return <AnswerLink href={href}>{children}</AnswerLink>;
  },
  ul({ children }) {
    return <ul className="my-3 list-disc space-y-1 pl-5 marker:text-text-muted">{children}</ul>;
  },
  ol({ children }) {
    return <ol className="my-3 list-decimal space-y-1 pl-5 marker:text-text-muted">{children}</ol>;
  },
  li({ children }) {
    return <li className="pl-1">{children}</li>;
  },
  blockquote({ children }) {
    return <blockquote className="my-4 border-l-2 border-text-muted/40 pl-4 text-text-secondary">{children}</blockquote>;
  },
  table({ children }) {
    return (
      <div className={`my-4 overflow-x-auto rounded-md border border-border ${BREAKOUT}`}>
        <table className="w-full border-collapse text-footnote tabular-nums">{children}</table>
      </div>
    );
  },
  thead({ children }) {
    return <thead className="bg-surface-3/60">{children}</thead>;
  },
  th({ children, style }) {
    return (
      <th style={style} className="whitespace-nowrap px-3 py-2 text-left font-semibold text-text-secondary">
        {children}
      </th>
    );
  },
  td({ children, style }) {
    return (
      <td style={style} className="border-t border-border px-3 py-2 align-top">
        {children}
      </td>
    );
  },
  hr() {
    return <hr className="my-6 border-border" />;
  },
  strong({ children }) {
    return <strong className="font-semibold text-text-primary">{children}</strong>;
  },
  em({ children }) {
    return <em className="italic">{children}</em>;
  },
  h1({ children }) {
    return <h1 className="mb-2 mt-6 text-title2 first:mt-0">{children}</h1>;
  },
  h2({ children }) {
    return <h2 className="mb-2 mt-6 text-title3 first:mt-0">{children}</h2>;
  },
  h3({ children }) {
    return <h3 className="mb-1.5 mt-5 text-callout first:mt-0">{children}</h3>;
  },
  h4({ children }) {
    return <h4 className="mb-1 mt-4 text-callout first:mt-0">{children}</h4>;
  },
  p({ children }) {
    return <p className="my-3 first:mt-0 last:mb-0">{children}</p>;
  },
  img({ src, alt }) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={typeof src === "string" ? src : undefined} alt={alt ?? ""} className="my-3 max-h-80 rounded-md border border-border" />;
  },
};

const REMARK = [remarkGfm];

function MarkdownContentImpl({ content }: { content: string }) {
  return (
    <div className="markdown-content break-words [overflow-wrap:anywhere]">
      <ReactMarkdown remarkPlugins={REMARK} components={COMPONENTS}>
        {content}
      </ReactMarkdown>
    </div>
  );
}

export const MarkdownContent = memo(MarkdownContentImpl);
