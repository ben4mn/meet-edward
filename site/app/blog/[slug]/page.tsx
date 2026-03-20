import { notFound } from "next/navigation";
import type { Metadata } from "next";
import { LandingNav } from "@/components/LandingNav";
import { LandingFooter } from "@/components/LandingFooter";
import { BlogArticle } from "@/components/blog/BlogArticle";
import { BlogPostJsonLd } from "@/components/blog/BlogPostJsonLd";
import { getAllSlugs, getPublishedPost, posts } from "@/lib/blog";

export function generateStaticParams() {
  return getAllSlugs().map((slug) => ({ slug }));
}

export function generateMetadata({
  params,
}: {
  params: { slug: string };
}): Metadata {
  const post = posts.find((p) => p.slug === params.slug);
  if (!post) return {};

  return {
    title: `${post.title} — Edward Blog`,
    description: post.description,
    alternates: { canonical: `/blog/${post.slug}` },
    keywords: post.tags,
    openGraph: {
      title: `${post.title} — Edward Blog`,
      description: post.description,
      url: `/blog/${post.slug}`,
      type: "article",
      publishedTime: post.publishDate,
      authors: ["Ben Foreman"],
      tags: post.tags,
      images: [`/og/${post.slug}.png`],
    },
    twitter: {
      card: "summary_large_image",
      title: post.title,
      description: post.description,
      images: [`/og/${post.slug}.png`],
    },
  };
}

export default function BlogPostPage({
  params,
}: {
  params: { slug: string };
}) {
  const post = getPublishedPost(params.slug);
  if (!post) notFound();

  return (
    <div className="min-h-screen bg-[#0f172a] text-[#f1f5f9]">
      <BlogPostJsonLd post={post} />
      <LandingNav />
      <main className="pt-24 pb-16">
        <BlogArticle post={post} />
      </main>
      <LandingFooter />
    </div>
  );
}
