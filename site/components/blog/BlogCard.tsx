"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { Calendar, Clock, ArrowRight } from "lucide-react";
import type { BlogPostMeta } from "@/lib/blog";

function formatDate(dateStr: string): string {
  return new Date(dateStr + "T00:00:00").toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

export function BlogCard({ post }: { post: BlogPostMeta }) {
  return (
    <motion.div
      variants={{
        hidden: { opacity: 0, y: 30 },
        visible: {
          opacity: 1,
          y: 0,
          transition: { duration: 0.5, ease: [0.25, 0.1, 0.25, 1] },
        },
      }}
    >
      <Link
        href={`/blog/${post.slug}`}
        className="group block rounded-xl border border-[#334155]/50 bg-[#1e293b]/40 p-6 hover:border-[#52b788]/40 hover:bg-[#1e293b]/70 transition-all duration-300"
      >
        <div className="flex flex-wrap items-center gap-3 text-xs text-[#64748b] mb-3">
          <span className="flex items-center gap-1">
            <Calendar className="w-3.5 h-3.5" />
            {formatDate(post.publishDate)}
          </span>
          <span className="flex items-center gap-1">
            <Clock className="w-3.5 h-3.5" />
            {post.readingTime}
          </span>
        </div>
        <h2 className="font-mono font-bold text-lg text-[#f1f5f9] mb-2 group-hover:text-[#52b788] transition-colors">
          {post.title}
        </h2>
        <p className="text-sm text-[#94a3b8] leading-relaxed mb-4">
          {post.description}
        </p>
        <div className="flex items-center gap-2 text-sm text-[#52b788] font-medium">
          Read more
          <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
        </div>
      </Link>
    </motion.div>
  );
}
