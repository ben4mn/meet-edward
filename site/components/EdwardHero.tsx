"use client";

import { motion } from "framer-motion";
import { EdwardAvatar } from "./EdwardAvatar";

interface EdwardHeroProps {
  size?: number;
}

export function EdwardHero({ size = 280 }: EdwardHeroProps) {
  return (
    <motion.div
      className="relative inline-flex items-center justify-center"
      initial={{ opacity: 0, scale: 0.8 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.8, ease: [0.25, 0.1, 0.25, 1] }}
    >
      <div
        className="absolute rounded-full"
        style={{
          width: size * 1.8,
          height: size * 1.8,
          background: "radial-gradient(circle, rgba(82, 183, 136, 0.15) 0%, rgba(82, 183, 136, 0.05) 40%, transparent 70%)",
          animation: "edward-hero-pulse 4s ease-in-out infinite",
        }}
      />
      <div
        className="absolute rounded-full"
        style={{
          width: size * 1.4,
          height: size * 1.4,
          background: "radial-gradient(circle, rgba(82, 183, 136, 0.12) 0%, transparent 60%)",
          animation: "edward-hero-pulse 4s ease-in-out infinite 0.5s",
        }}
      />
      <div
        className="absolute rounded-full overflow-hidden pointer-events-none opacity-[0.03]"
        style={{
          width: size,
          height: size,
          backgroundImage: "repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(82, 183, 136, 1) 2px, rgba(82, 183, 136, 1) 4px)",
        }}
      />
      <EdwardAvatar size={size} animated />
    </motion.div>
  );
}
