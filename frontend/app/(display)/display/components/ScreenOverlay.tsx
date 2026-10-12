"use client";

// What Edward put on top of his tiles. Image / video / alert / text / html
// fill the screen here, like the household canvas.

import { useEffect } from "react";

import { showRenderer, type ShowContent } from "../lib/screenModel";

export function ScreenOverlay({ show, onDismiss }: { show: ShowContent | null; onDismiss: () => void }) {
  useEffect(() => {
    if (!show) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onDismiss();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [show, onDismiss]);

  if (!show) return null;
  const renderer = showRenderer(show);


  const media = renderer === "img" || renderer === "iframe-video" || renderer === "video" || renderer === "iframe-html";
  return (
    <div className="mc-show" data-kind={show.kind} role="dialog" aria-label="Edward's screen">
      {renderer === "img" && (
        // eslint-disable-next-line @next/next/no-img-element
        <img className="mc-show-media" src={show.source ?? ""} alt={show.title ?? "Edward"} onClick={onDismiss} />
      )}
      {renderer === "iframe-video" && (
        <iframe className="mc-show-media" src={show.embed_url ?? ""} allow="autoplay; encrypted-media; fullscreen" title={show.title ?? "video"} />
      )}
      {renderer === "video" && <video className="mc-show-media" src={show.source ?? ""} autoPlay playsInline controls onClick={onDismiss} />}
      {renderer === "iframe-html" && <iframe className="mc-show-media" sandbox="allow-scripts" srcDoc={show.body ?? ""} title={show.title ?? "scene"} />}
      {(renderer === "alert" || renderer === "text") && (
        <button className={`mc-show-text ${renderer === "alert" ? "is-alert" : ""}`} onClick={onDismiss}>
          {show.title && <span className="mc-show-title">{show.title}</span>}
          <span className="mc-show-body">{show.body}</span>
          <span className="mc-show-hint">click or Esc to dismiss</span>
        </button>
      )}
      {media && (
        <button className="mc-show-close" onClick={onDismiss} aria-label="Dismiss">
          {show.title ? show.title : "Edward"} · click or Esc to dismiss
        </button>
      )}
    </div>
  );
}
