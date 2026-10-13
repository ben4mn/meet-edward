"use client";

import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";

// An image that opens full size in a lightbox when clicked. A native <dialog>
// gives focus trapping and Escape for free; a click anywhere closes it.

interface ZoomableImageProps {
  src: string;
  alt: string;
  width: number;
  height: number;
  className?: string;
}

export function ZoomableImage({ src, alt, width, height, className }: ZoomableImageProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
    document.documentElement.style.overflow = open ? "hidden" : "";
    return () => {
      document.documentElement.style.overflow = "";
    };
  }, [open]);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={`Enlarge: ${alt}`}
        className="block w-full cursor-zoom-in focus:outline-none focus-visible:ring-2 focus-visible:ring-[#52b788] focus-visible:ring-offset-2 focus-visible:ring-offset-[#0f172a] rounded-[inherit]"
      >
        {/* eslint-disable-next-line @next/next/no-img-element -- static export, no image optimizer */}
        <img src={src} alt={alt} width={width} height={height} loading="lazy" className={className} />
      </button>
      <dialog
        ref={dialogRef}
        onClose={() => setOpen(false)}
        onClick={() => setOpen(false)}
        className="m-0 p-0 w-screen h-screen max-w-none max-h-none bg-transparent backdrop:bg-black/85 backdrop:backdrop-blur-sm cursor-zoom-out"
      >
        {open && (
          <div className="w-full h-full flex items-center justify-center p-4 sm:p-10">
            {/* eslint-disable-next-line @next/next/no-img-element -- static export, no image optimizer */}
            <img
              src={src}
              alt={alt}
              width={width}
              height={height}
              className="max-w-full max-h-full w-auto h-auto object-contain rounded-lg shadow-2xl shadow-black/60"
            />
            <button
              type="button"
              aria-label="Close"
              autoFocus
              onClick={() => setOpen(false)}
              className="absolute top-4 right-4 flex h-11 w-11 items-center justify-center rounded-full bg-[#0f172a]/80 text-[#f1f5f9] border border-[#334155] hover:bg-[#1e293b] focus:outline-none focus-visible:ring-2 focus-visible:ring-[#52b788]"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
        )}
      </dialog>
    </>
  );
}
