// A screenshot of the app with a caption. The images in public/screenshots
// come from the repository's docs/screenshots (the UX harness: invented data
// only), resized for the web.

interface ScreenshotProps {
  src: string;
  alt: string;
  width: number;
  height: number;
  caption?: string;
  narrow?: boolean;
}

export function Screenshot({ src, alt, width, height, caption, narrow }: ScreenshotProps) {
  return (
    <figure className={`my-8 ${narrow ? "max-w-xs mx-auto" : ""}`}>
      {/* eslint-disable-next-line @next/next/no-img-element -- static export, no image optimizer */}
      <img
        src={src}
        alt={alt}
        width={width}
        height={height}
        loading="lazy"
        className="w-full h-auto rounded-lg border border-[#334155]"
      />
      {caption && (
        <figcaption className="text-sm text-[#94a3b8] mt-2 text-center">{caption}</figcaption>
      )}
    </figure>
  );
}
