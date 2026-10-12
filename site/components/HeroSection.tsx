"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { Github, BookOpen } from "lucide-react";
import { EdwardHero } from "./EdwardHero";

const GITHUB_URL = "https://github.com/ben4mn/meet-edward";

export function HeroSection() {
  return (
    <>
    <section className="relative min-h-screen flex flex-col items-center justify-center px-6 pt-24 pb-16 overflow-hidden">
      <div className="absolute inset-0 landing-gradient-bg" />
      <div
        className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] pointer-events-none"
        style={{
          background: "radial-gradient(circle, rgba(82, 183, 136, 0.08) 0%, transparent 60%)",
        }}
      />
      <div
        className="absolute inset-0 opacity-[0.03] pointer-events-none"
        style={{
          backgroundImage:
            "linear-gradient(rgba(82, 183, 136, 0.5) 1px, transparent 1px), linear-gradient(90deg, rgba(82, 183, 136, 0.5) 1px, transparent 1px)",
          backgroundSize: "60px 60px",
        }}
      />

      <div className="relative z-10 flex flex-col items-center text-center max-w-3xl mx-auto">
        <div className="mb-8 sm:mb-10">
          <EdwardHero size={220} />
        </div>

        <motion.h1
          className="font-mono text-4xl sm:text-5xl font-bold text-[#f1f5f9] tracking-tight leading-[1.1] mb-5 text-balance"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.3 }}
        >
          An AI assistant that{" "}
          <span className="text-[#52b788]">remembers, and follows through.</span>
        </motion.h1>

        <motion.p
          className="text-lg sm:text-xl text-[#94a3b8] max-w-xl mb-8 leading-relaxed"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.5 }}
        >
          Open source, and built for one person: you. Edward runs on your
          Mac, knows where he heard what he knows, keeps the promises he
          makes, and works in a sandboxed computer of his own.
        </motion.p>

        <motion.div
          className="w-full sm:w-auto flex flex-col sm:flex-row items-center gap-3"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.7 }}
        >
          <motion.a
            href={GITHUB_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="w-full sm:w-auto justify-center inline-flex items-center gap-2.5 text-base font-semibold text-white px-8 py-3.5 rounded-xl bg-[#52b788] hover:bg-[#52b788]/90 transition-all"
            whileHover={{ scale: 1.03 }}
            whileTap={{ scale: 0.98 }}
          >
            <Github className="w-5 h-5" />
            Get Started on GitHub
          </motion.a>
          <Link
            href="/docs/introduction"
            className="w-full sm:w-auto justify-center inline-flex items-center gap-2.5 text-base font-semibold text-[#f1f5f9] px-8 py-3.5 rounded-xl border border-[#334155] hover:border-[#52b788]/50 hover:bg-[#1e293b]/60 transition-all"
          >
            <BookOpen className="w-5 h-5" />
            Read the docs
          </Link>
        </motion.div>
      </div>

      <motion.div
        className="absolute bottom-8 left-1/2 -translate-x-1/2"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 1.2 }}
      >
        <motion.div
          className="w-5 h-8 rounded-full border-2 border-[#334155] flex justify-center pt-1.5"
          animate={{ opacity: [0.4, 0.8, 0.4] }}
          transition={{ duration: 2, repeat: Infinity }}
        >
          <motion.div
            className="w-1 h-2 rounded-full bg-[#52b788]"
            animate={{ y: [0, 8, 0] }}
            transition={{ duration: 1.5, repeat: Infinity }}
          />
        </motion.div>
      </motion.div>
    </section>

    {/* The film */}
    <section id="film" className="relative px-6 pb-20 pt-8 flex justify-center scroll-mt-24">
      <motion.figure
        className="w-full max-w-5xl"
        initial={{ opacity: 0, y: 30 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: "-50px" }}
        transition={{ duration: 0.6 }}
      >
        <div className="rounded-xl border border-[#334155] overflow-hidden shadow-2xl shadow-black/40 bg-[#0b1120] aspect-video">
          <video
            className="w-full h-full block"
            controls
            playsInline
            preload="none"
            poster="/video/meet-edward-poster.jpg"
            width={1920}
            height={1080}
            aria-label="Meet Edward, a five-minute film"
          >
            <source src="/video/meet-edward.mp4" type="video/mp4" />
          </video>
        </div>
        <figcaption className="text-sm text-[#64748b] mt-3 text-center">
          Meet Edward, in five minutes. The people and pets are invented; the
          screens are the real app on test data.{" "}
          <a
            href="/video/meet-edward.mp4"
            download
            className="text-[#52b788] hover:text-[#6fcf97] transition-colors"
          >
            Download the MP4
          </a>
        </figcaption>
      </motion.figure>
    </section>
    </>
  );
}
