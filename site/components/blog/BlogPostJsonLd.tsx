import type { BlogPost } from "@/lib/blog";

export function BlogPostJsonLd({ post }: { post: BlogPost }) {
  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "BlogPosting",
    headline: post.title,
    description: post.description,
    datePublished: post.publishDate,
    author: {
      "@type": "Person",
      name: "Ben Foreman",
      url: "https://zyroi.com",
    },
    publisher: {
      "@type": "Organization",
      name: "Edward",
      url: "https://meet-edward.com",
    },
    url: `https://meet-edward.com/blog/${post.slug}`,
    keywords: post.tags.join(", "),
    mainEntityOfPage: {
      "@type": "WebPage",
      "@id": `https://meet-edward.com/blog/${post.slug}`,
    },
  };

  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
    />
  );
}
