import Link from "next/link";
import { Calendar, Clock, ArrowLeft } from "lucide-react";
import type { BlogPost } from "@/lib/blog";

function formatDate(dateStr: string): string {
  return new Date(dateStr + "T00:00:00").toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

export function BlogArticle({ post }: { post: BlogPost }) {
  return (
    <article className="max-w-3xl mx-auto px-6 py-12 lg:py-16">
      <Link
        href="/blog"
        className="inline-flex items-center gap-1.5 text-sm text-[#52b788] hover:text-[#6fcf97] transition-colors mb-8"
      >
        <ArrowLeft className="w-4 h-4" />
        All posts
      </Link>
      <header className="mb-10">
        <h1 className="font-mono font-bold text-3xl sm:text-4xl text-[#f1f5f9] mb-4 leading-tight">
          {post.title}
        </h1>
        <div className="flex flex-wrap items-center gap-4 text-sm text-[#64748b]">
          <span className="flex items-center gap-1.5">
            <Calendar className="w-4 h-4" />
            {formatDate(post.publishDate)}
          </span>
          <span className="flex items-center gap-1.5">
            <Clock className="w-4 h-4" />
            {post.readingTime}
          </span>
        </div>
      </header>
      <div className="docs-prose">{post.content()}</div>
    </article>
  );
}
