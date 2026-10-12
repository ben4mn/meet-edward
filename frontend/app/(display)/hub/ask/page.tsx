"use client";

import "../hub.css";

import { useRouter } from "next/navigation";

import { useWakeLock } from "../../display/hooks/useWakeLock";
import { EdwardAvatar } from "@/components/EdwardAvatar";
import { Pulse } from "@/components/ui/Pulse";
import { lazyComponent } from "@/lib/lazyComponent";

// Voice reuses the existing continuous-voice stack wholesale: browser VAD
// -> /api/voice/ws -> Voxtral -> Claude (full tools) -> ElevenLabs. The hub
// contributes a full-screen mount, a way back, and who is speaking:
// surface="hub" — whoever is at the panel is someone in the owner's home,
// not necessarily the owner, so the backend runs these turns as
// `hub_voice`, never with the owner's rights (2026-10-11).
//
// No useIdleRedirect here on purpose — dropping to the screensaver
// mid-conversation would be maddening.
//
// The voice view is its own chunk (2026-09-26 perf pass: the Pi is slow to
// parse, and the page itself needs none of it to hydrate). Its fetch starts
// as this module runs, in parallel with hydration and the auth check, so it
// is almost always here when the page renders. Until it is, the page shows
// what the auth check showed a moment before — Edward, still, and his pulse
// (AuthGuard's wait) — so nothing flickers.

function VoiceWaiting() {
  return (
    <div className="flex min-h-dvh flex-col items-center justify-center gap-5 bg-primary-bg px-6 text-center">
      <EdwardAvatar size="lg" animated={false} aria-label="Edward" />
      <Pulse label="Getting ready to listen" />
    </div>
  );
}

const ContinuousVoiceView = lazyComponent(
  () => import("@/components/chat/ContinuousVoiceView").then((mod) => mod.ContinuousVoiceView),
  VoiceWaiting,
);
if (typeof window !== "undefined") {
  ContinuousVoiceView.preload().catch(() => {
    /* retried when the page mounts it */
  });
}

export default function HubAskPage() {
  useWakeLock(true);
  const router = useRouter();

  return (
    <div className="hub-root hub-ask">
      <ContinuousVoiceView surface="hub" onExitVoiceMode={() => router.replace("/hub")} />
    </div>
  );
}
