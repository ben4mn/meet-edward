import { ReactNode } from "react";

export function DocsContent({ children }: { children: ReactNode }) {
  return (
    <article className="docs-prose max-w-3xl mx-auto px-6 py-12 lg:py-16">
      {children}
    </article>
  );
}
