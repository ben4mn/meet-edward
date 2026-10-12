"use client";

// /fullscreen is an alias for /screen — same ScreenLayout, same auth gate
// (the (display) route group's layout wraps all three). Lives at this path
// because it's the URL most people remember when they want a kiosk view.

import "../display/display.css";
import { ScreenLayout } from "../display/components/ScreenLayout";

export default function FullscreenPage() {
  return <ScreenLayout />;
}
