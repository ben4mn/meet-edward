import type { Metadata } from "next";
import { LandingNav } from "@/components/LandingNav";
import { LandingFooter } from "@/components/LandingFooter";
import { BlogIndexContent } from "@/components/blog/BlogIndexContent";
import { getPublishedPosts } from "@/lib/blog";

export const metadata: Metadata = {
  title: "Blog — Edward",
  description:
    "Articles about building a self-hosted AI assistant with long-term memory, proactive monitoring, and self-evolution.",
  alternates: { canonical: "/blog" },
  openGraph: {
    title: "Blog — Edward",
    description:
      "Articles about building a self-hosted AI assistant with long-term memory, proactive monitoring, and self-evolution.",
    url: "/blog",
  },
};

export default function BlogPage() {
  const posts = getPublishedPosts().map(
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    ({ content, ...meta }) => meta
  );

  return (
    <div className="min-h-screen bg-[#0f172a] text-[#f1f5f9]">
      <LandingNav />
      <main className="pt-24 pb-16 px-6">
        <div className="max-w-4xl mx-auto">
          <h1 className="font-mono font-bold text-3xl sm:text-4xl text-[#f1f5f9] mb-3">
            Blog
          </h1>
          <p className="text-[#64748b] text-lg mb-12">
            Notes on building an AI assistant that remembers, evolves, and works
            in the background.
          </p>
          <BlogIndexContent posts={posts} />
        </div>
      </main>
      <LandingFooter />
    </div>
  );
}
