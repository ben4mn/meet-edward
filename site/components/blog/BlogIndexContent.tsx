"use client";

import { motion } from "framer-motion";
import { BlogCard } from "./BlogCard";
import type { BlogPostMeta } from "@/lib/blog";

export function BlogIndexContent({ posts }: { posts: BlogPostMeta[] }) {
  if (posts.length === 0) {
    return (
      <p className="text-[#64748b] text-center py-16">
        No posts published yet. Check back soon.
      </p>
    );
  }

  return (
    <motion.div
      className="grid gap-6"
      initial="hidden"
      animate="visible"
      variants={{
        hidden: {},
        visible: { transition: { staggerChildren: 0.1 } },
      }}
    >
      {posts.map((post) => (
        <BlogCard key={post.slug} post={post} />
      ))}
    </motion.div>
  );
}
