"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { Copy, Check, Download } from "lucide-react";
import { useState, useCallback } from "react";

interface MarkdownContentProps {
  content: string;
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [text]);

  return (
    <button
      onClick={handleCopy}
      className="absolute top-2 right-2 p-1.5 rounded bg-surface/80 hover:bg-surface text-text-muted hover:text-text-primary transition-colors"
      aria-label="Copy code"
    >
      {copied ? (
        <Check className="w-4 h-4 text-terminal" />
      ) : (
        <Copy className="w-4 h-4" />
      )}
    </button>
  );
}

export function MarkdownContent({ content }: MarkdownContentProps) {
  return (
    <div className="markdown-content">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
        code({ className, children, ...props }) {
          const match = /language-(\w+)/.exec(className || "");
          const codeString = String(children).replace(/\n$/, "");

          // Check if this is a code block (has language) or inline code
          const isCodeBlock = match || (codeString.includes("\n"));

          if (isCodeBlock) {
            return (
              <div className="relative group my-3">
                <CopyButton text={codeString} />
                <SyntaxHighlighter
                  style={oneDark}
                  language={match?.[1] || "text"}
                  PreTag="div"
                  customStyle={{
                    margin: 0,
                    borderRadius: "0.5rem",
                    fontSize: "0.875rem",
                  }}
                >
                  {codeString}
                </SyntaxHighlighter>
              </div>
            );
          }

          return (
            <code
              className="bg-surface px-1.5 py-0.5 rounded text-sm font-mono text-terminal"
              {...props}
            >
              {children}
            </code>
          );
        },
        pre({ children }) {
          // Just pass through - the code component handles the styling
          return <>{children}</>;
        },
        a({ href, children }) {
          const fileDownloadMatch = href?.match(/(?:https?:\/\/[^/]+)?\/api\/files\/([^/]+)\/download$/);
          if (fileDownloadMatch) {
            // Normalize to relative path so it works regardless of domain
            const downloadHref = `/api/files/${fileDownloadMatch[1]}/download`;
            return (
              <a
                href={downloadHref}
                download
                className="inline-flex items-center gap-1.5 bg-terminal/10 border border-terminal/30 rounded-lg px-2.5 py-1 text-sm text-terminal hover:bg-terminal/20 hover:border-terminal/50 transition-colors no-underline cursor-pointer"
              >
                <Download className="w-3.5 h-3.5 flex-shrink-0" />
                <span className="truncate max-w-[200px]">{children}</span>
              </a>
            );
          }
          return (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-terminal hover:underline"
            >
              {children}
            </a>
          );
        },
        ul({ children }) {
          return <ul className="list-disc pl-6 my-2">{children}</ul>;
        },
        ol({ children }) {
          return <ol className="list-decimal pl-6 my-2">{children}</ol>;
        },
        li({ children }) {
          return <li className="my-0.5">{children}</li>;
        },
        blockquote({ children }) {
          return (
            <blockquote className="border-l-4 border-terminal pl-4 my-3 text-text-muted italic">
              {children}
            </blockquote>
          );
        },
        table({ children }) {
          return (
            <div className="overflow-x-auto my-3">
              <table className="min-w-full border border-border rounded">
                {children}
              </table>
            </div>
          );
        },
        th({ children }) {
          return (
            <th className="bg-surface px-3 py-2 text-left border-b border-border font-semibold">
              {children}
            </th>
          );
        },
        td({ children }) {
          return (
            <td className="px-3 py-2 border-b border-border">{children}</td>
          );
        },
        hr() {
          return <hr className="my-4 border-border" />;
        },
        strong({ children }) {
          return <strong className="font-semibold">{children}</strong>;
        },
        em({ children }) {
          return <em className="italic">{children}</em>;
        },
        h1({ children }) {
          return <h1 className="text-2xl font-bold my-3">{children}</h1>;
        },
        h2({ children }) {
          return <h2 className="text-xl font-bold my-3">{children}</h2>;
        },
        h3({ children }) {
          return <h3 className="text-lg font-semibold my-2">{children}</h3>;
        },
        h4({ children }) {
          return <h4 className="text-base font-semibold my-2">{children}</h4>;
        },
        p({ children }) {
          return <p className="my-2">{children}</p>;
        },
      }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
