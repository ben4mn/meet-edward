"use client";

import { cn } from "@/lib/utils";

interface EdwardAvatarProps {
  size?: "sm" | "md" | "lg" | "xl" | number;
  animated?: boolean;
  className?: string;
}

const sizeMap = {
  sm: 32,
  md: 64,
  lg: 120,
  xl: 96,
};

export function EdwardAvatar({ size = "md", animated = false, className }: EdwardAvatarProps) {
  const px = typeof size === "number" ? size : sizeMap[size];

  return (
    <div
      className={cn(
        "inline-flex items-center justify-center",
        className
      )}
      style={{ width: px, height: px }}
    >
      <svg
        width={px}
        height={px}
        viewBox="0 0 100 100"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        {/* Body circle — static */}
        <circle cx="50" cy="50" r="50" fill="#52b788" />

        {/* Face group — scaled down + floats inside the circle */}
        <g transform={animated ? "translate(50,50) scale(0.82) translate(-50,-50)" : undefined}>
          <g className={animated ? "edward-float" : undefined}>
            {/* Screen bezel (white) */}
            <rect x="25" y="32" width="50" height="40" rx="8" fill="#FFFFFF" />

            {/* Screen (dark) */}
            <rect x="28" y="35" width="44" height="34" rx="6" fill="#0d1117" className={animated ? "edward-screen-flicker" : undefined} />

            {/* Eyes */}
            <g className={animated ? "edward-eye-glow" : undefined}>
              <circle
                cx="40"
                cy="52"
                r="4"
                fill="#52b788"
                className={animated ? "edward-blink" : undefined}
              />
              <circle
                cx="60"
                cy="52"
                r="4"
                fill="#52b788"
                className={animated ? "edward-blink" : undefined}
              />
            </g>

            {/* Antenna group */}
            <g className={animated ? "edward-antenna" : undefined}>
              {/* Antenna stick */}
              <rect x="48" y="20" width="4" height="14" rx="1" fill="#FFFFFF" />
              {/* Antenna ball */}
              <circle cx="50" cy="18" r="4" fill="#52b788" stroke="#FFFFFF" strokeWidth="1.5" className={animated ? "edward-antenna-glow" : undefined} />
            </g>

            {/* Mouth */}
            <rect
              x="42"
              y="62"
              width="16"
              height="2"
              rx="1"
              fill="#52b788"
              fillOpacity="0.8"
              className={animated ? "edward-mouth" : undefined}
            />
          </g>
        </g>
      </svg>
    </div>
  );
}
