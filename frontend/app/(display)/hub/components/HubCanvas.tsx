"use client";

/**
 * Edward's expression surface, drawn over whatever hub page is up.
 *
 * Polls /api/hub/canvas. When Edward has put something there — a movie, an
 * image, an alert, a note, an HTML scene — it fills the screen. A tap
 * dismisses it and tells Edward who did (well, that someone did).
 *
 * HTML scenes run in a sandboxed iframe with no network and no access to
 * the hub's cookie: `sandbox="allow-scripts"` only, content via srcDoc.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { contentKey, isExpired, rendererFor, type CanvasContent } from "../lib/canvas";
import { dismissCanvas, fetchCanvas } from "../lib/hubApi";

const POLL_MS = 4_000;

export function HubCanvas() {
  const [content, setContent] = useState<CanvasContent | null>(null);
  const keyRef = useRef("");

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const c = await fetchCanvas();
        if (!alive) return;
        const next = c && !isExpired(c, Date.now()) ? c : null;
        const k = contentKey(next);
        if (k !== keyRef.current) {
          keyRef.current = k;
          setContent(next);
        }
      } catch {
        /* keep whatever we have; the hub tolerates a flaky backend */
      }
    };
    tick();
    const id = window.setInterval(tick, POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  // Local expiry between polls so a 30s alert really leaves at 30s.
  useEffect(() => {
    if (!content?.expires_at) return;
    const ms = Date.parse(content.expires_at) - Date.now();
    if (ms <= 0) return;
    const id = window.setTimeout(() => {
      keyRef.current = "";
      setContent(null);
    }, ms);
    return () => window.clearTimeout(id);
  }, [content]);

  const renderer = useMemo(() => (content ? rendererFor(content) : null), [content]);
  if (!content || !renderer) return null;

  const dismiss = () => {
    keyRef.current = "";
    setContent(null);
    dismissCanvas().catch(() => {});
  };

  return (
    <div className="hub-canvas" data-kind={content.kind} role="dialog" aria-label="Edward's screen">
      {renderer === "img" && <img className="hub-canvas-media" src={content.source ?? ""} alt={content.title ?? "Edward"} onClick={dismiss} />}
      {renderer === "iframe-video" && (
        <iframe className="hub-canvas-media" src={content.embed_url ?? ""} allow="autoplay; encrypted-media; fullscreen" title={content.title ?? "video"} />
      )}
      {renderer === "video" && <video className="hub-canvas-media" src={content.source ?? ""} autoPlay playsInline controls onClick={dismiss} />}
      {renderer === "iframe-html" && <iframe className="hub-canvas-media" sandbox="allow-scripts" srcDoc={content.body ?? ""} title={content.title ?? "scene"} />}
      {(renderer === "alert" || renderer === "text") && (
        <button className={`hub-canvas-text ${renderer === "alert" ? "is-alert" : ""}`} onClick={dismiss}>
          {content.title && <span className="hub-canvas-title">{content.title}</span>}
          <span className="hub-canvas-body">{content.body}</span>
          <span className="hub-canvas-hint">tap to dismiss</span>
        </button>
      )}
      {(renderer === "img" || renderer === "iframe-video" || renderer === "video" || renderer === "iframe-html") && (
        <button className="hub-canvas-close" onClick={dismiss} aria-label="Dismiss">
          {content.title ? content.title : "Edward"} · tap to dismiss
        </button>
      )}
    </div>
  );
}
