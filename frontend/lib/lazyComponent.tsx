"use client";

// A component whose code arrives in its own chunk (2026-09-26 perf pass):
// what a first paint does not need leaves the route's first-load JS.
//
// Unlike next/dynamic (React.lazy, which always suspends at least once and
// commits its fallback for a frame even when the chunk is already here),
// this renders the real component *synchronously* once the chunk has
// loaded. Callers start the fetch at module scope with `preload()`, so by
// the time the component is first rendered (after the auth check and the
// feed) it is almost always there, and nothing on screen changes. Only when
// it is not does `fallback` (given the same props, so it can match the
// shape) stand in until it lands.
//
// Hydration-safe: while React hydrates it draws the fallback, exactly as the
// server did, and swaps right after. A failed load keeps the fallback and is
// retried by the next mount.

import React, { useEffect, useState, type ComponentType } from "react";
import { useIsClient } from "@/components/ui/motion";

export type LazyComponent<P extends object> = ComponentType<P> & {
  /** Start (or join) the fetch; resolves to the component. */
  preload: () => Promise<ComponentType<P>>;
};

export function lazyComponent<P extends object>(
  load: () => Promise<ComponentType<P>>,
  Fallback: ComponentType<P> | null = null,
): LazyComponent<P> {
  let loaded: ComponentType<P> | null = null;
  let pending: Promise<ComponentType<P>> | null = null;

  const preload = (): Promise<ComponentType<P>> => {
    if (!pending) {
      pending = load().then(
        (C) => (loaded = C),
        (err: unknown) => {
          pending = null;
          throw err;
        },
      );
    }
    return pending;
  };

  function Lazy(props: P) {
    const isClient = useIsClient();
    const [, setReady] = useState(false);
    const Component = isClient ? loaded : null;

    useEffect(() => {
      if (loaded) return;
      let live = true;
      preload().then(
        () => live && setReady(true),
        () => {
          /* keep the fallback; the next mount tries again */
        },
      );
      return () => {
        live = false;
      };
    }, []);

    if (Component) return <Component {...props} />;
    return Fallback ? <Fallback {...props} /> : null;
  }

  return Object.assign(Lazy, { preload });
}
