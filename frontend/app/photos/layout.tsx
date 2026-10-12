import type { Metadata } from "next";

// The household photo door (2026-10-11): its own home-screen app, its own
// manifest and title, no Edward shell and no owner auth — the page signs in
// itself against /api/photo-door (backend/services/photo_door_auth.py).
export const metadata: Metadata = {
  title: "Hub Photos",
  description: "The photos on the hub's screensaver",
  manifest: "/photo-door-manifest.json",
  appleWebApp: {
    capable: true,
    statusBarStyle: "default",
    title: "Photos",
  },
};

export default function PhotoDoorLayout({ children }: { children: React.ReactNode }) {
  return children;
}
