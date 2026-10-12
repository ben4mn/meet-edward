"use client";

// /screen — Edward's screen on the host Mac: a tab in the owner's Chrome that
// services/screen_window_service.py finds or opens (EDWARD_CHROME_HANDS=on),
// or any browser pointed here by hand. /display and /fullscreen are aliases.

import "../display/display.css";
import { ScreenLayout } from "../display/components/ScreenLayout";

export default function ScreenPage() {
  return <ScreenLayout />;
}
