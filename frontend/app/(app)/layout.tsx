"use client";

import { AuthProvider } from "@/lib/AuthContext";
import { AuthGuard } from "@/components/auth/AuthGuard";
import { ChatProvider } from "@/lib/ChatContext";
import { EdwardPresenceProvider } from "@/lib/presence";
import { ClientLayout } from "@/components/layout/ClientLayout";
import { TimezoneProvider } from "@/lib/useTimezone";
import { AppBuildReload } from "@/components/layout/AppBuildReload";

// ToastProvider is mounted once in app/layout.tsx (root); do not mount it
// again here. Edward's presence reads the chat turn, so it sits inside
// ChatProvider and wraps the whole shell (bar, dock, sheet, pages).
// AppBuildReload reloads onto a new deploy only while the owner is idle, so it
// too needs the ChatProvider (the turn, live voice, the queue).
export default function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AuthProvider>
      <AuthGuard>
        <TimezoneProvider>
          <ChatProvider>
            <AppBuildReload />
            <EdwardPresenceProvider>
              <ClientLayout>{children}</ClientLayout>
            </EdwardPresenceProvider>
          </ChatProvider>
        </TimezoneProvider>
      </AuthGuard>
    </AuthProvider>
  );
}
