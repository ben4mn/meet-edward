// The framer-motion features the app loads (2026-09-26). LazyMotion
// registers features in one global registry; `domAnimation` has no drag and
// no layout, so while the shell loaded it, sheets could not be swiped down
// and the dock/header pills snapped — until /chat's sidebar happened to load
// `domMax`. Every LazyMotion in the app loads this instead. It lives apart
// from motion.ts so a leaf import of the motion tokens never pulls
// framer-motion in.
//
// Loaded lazily (2026-09-26 perf pass): `domMax` is ~35 kB gzipped that no
// first paint needs, so it is an async chunk — `MOTION_FEATURES` is
// LazyMotion's loader form. The fetch starts as soon as this module runs in
// the browser (in parallel with hydration, not after it), is shared by every
// LazyMotion, and is retried on the next mount if it ever fails. Until it
// lands, `m.*` elements render in their initial state, exactly as the server
// drew them; drag, layout (the dock pill slide) and enter animations start
// the moment it does. __tests__/firstLoadBundle.test.ts pins that nothing
// imports `domMax` statically.

import type { FeatureBundle } from "framer-motion";

let pending: Promise<FeatureBundle> | null = null;

export const MOTION_FEATURES = (): Promise<FeatureBundle> => {
  if (!pending) {
    pending = import("./motionFeaturesMax").then(
      (mod) => mod.default,
      (err: unknown) => {
        pending = null; // the next LazyMotion to mount tries again
        throw err;
      },
    );
  }
  return pending;
};

if (typeof window !== "undefined") {
  MOTION_FEATURES().catch(() => {
    /* retried by the next LazyMotion mount */
  });
}
