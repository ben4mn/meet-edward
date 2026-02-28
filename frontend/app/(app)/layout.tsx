"use client";

import { AuthProvider } from "@/lib/AuthContext";
import { AuthGuard } from "@/components/auth/AuthGuard";
import { ChatProvider } from "@/lib/ChatContext";
import { ClientLayout } from "@/components/layout/ClientLayout";

export default function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AuthProvider>
      <AuthGuard>
        <ChatProvider>
          <ClientLayout>{children}</ClientLayout>
        </ChatProvider>
      </AuthGuard>
    </AuthProvider>
  );
}
