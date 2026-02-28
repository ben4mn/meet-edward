"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { MessageSquare, Settings, PanelLeft, LogOut } from "lucide-react";
import { ThemeProvider, ThemeToggle } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { ChatSidebar, MobileChatDrawer } from "@/components/chat/ChatSidebar";
import { NotificationPrompt } from "@/components/NotificationPrompt";
import { ServiceWorkerRegistration } from "@/components/ServiceWorkerRegistration";
import { useChat } from "@/lib/ChatContext";
import { useAuth } from "@/lib/AuthContext";
import { EdwardAvatar } from "@/components/EdwardAvatar";

function Header() {
  const pathname = usePathname();
  const router = useRouter();
  const [mobileDrawerOpen, setMobileDrawerOpen] = useState(false);
  const { sidebarCollapsed, toggleSidebar } = useChat();
  const { logout, isAuthenticated } = useAuth();
  const isChatPage = pathname === "/chat";

  const handleLogout = async () => {
    await logout();
    router.push("/login");
  };

  return (
    <>
      <header className="border-b border-border bg-primary-bg">
        <div className="container mx-auto px-4 h-14 flex items-center justify-between">
          {/* Left side: Mobile drawer toggle + Logo */}
          <div className="flex items-center gap-2">
            {/* Mobile drawer toggle (only on chat page) */}
            {isChatPage && (
              <button
                onClick={() => setMobileDrawerOpen(true)}
                className="md:hidden p-2 rounded-md hover:bg-surface transition-colors"
                aria-label="Open chat history"
              >
                <PanelLeft className="w-5 h-5 text-text-primary" />
              </button>
            )}

            {/* Logo */}
            <Link href="/chat" className="flex items-center gap-2">
              <EdwardAvatar size={36} animated />
              <span className="font-mono font-bold text-lg text-text-primary hidden sm:inline">
                Edward
              </span>
            </Link>
          </div>

          {/* Right side: Navigation icons */}
          <nav className="flex items-center gap-1">
            {/* Chat button (hidden on chat page) */}
            {!isChatPage && (
              <Link
                href="/chat"
                className="p-2 rounded-md hover:bg-surface transition-colors text-text-primary"
                title="Chat"
              >
                <MessageSquare className="w-5 h-5" />
              </Link>
            )}
            {/* Settings button */}
            <Link
              href="/settings"
              className={cn(
                "p-2 rounded-md transition-colors text-text-primary",
                pathname === "/settings" ? "bg-surface" : "hover:bg-surface"
              )}
              title="Settings"
            >
              <Settings className="w-5 h-5" />
            </Link>
            {/* Theme toggle */}
            <ThemeToggle />
            {/* Logout button */}
            {isAuthenticated && (
              <button
                onClick={handleLogout}
                className="p-2 rounded-md hover:bg-surface transition-colors text-text-primary"
                title="Sign out"
              >
                <LogOut className="w-5 h-5" />
              </button>
            )}
          </nav>
        </div>
      </header>

      {/* Mobile drawer (only on chat page) */}
      {isChatPage && (
        <MobileChatDrawer
          isOpen={mobileDrawerOpen}
          onClose={() => setMobileDrawerOpen(false)}
        />
      )}
    </>
  );
}

interface ClientLayoutProps {
  children: React.ReactNode;
}

export function ClientLayout({ children }: ClientLayoutProps) {
  const pathname = usePathname();
  const isChatPage = pathname === "/chat";

  return (
    <ThemeProvider>
      {/* Register service worker for PWA */}
      <ServiceWorkerRegistration />
      <div className="h-screen flex flex-col overflow-hidden bg-primary-bg">
        <Header />
        <div className="flex-1 flex overflow-hidden min-h-0">
          {/* Sidebar only on chat page */}
          {isChatPage && <ChatSidebar />}
          <main className="flex-1 flex flex-col overflow-hidden min-h-0">{children}</main>
        </div>
        {/* Push notification prompt */}
        <NotificationPrompt />
      </div>
    </ThemeProvider>
  );
}
