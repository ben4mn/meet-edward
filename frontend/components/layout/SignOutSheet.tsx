"use client";

// One "Sign out of Edward?" for every way out (the avatar menu, the palette,
// the You card). Nothing destructive in one tap (design system §1.7). A
// sign-out the server never confirmed says so and keeps the sheet up.

import React from "react";
import { useRouter } from "next/navigation";
import { ConfirmSheet, useToast } from "@/components/ui";
import { useAuth } from "@/lib/AuthContext";
import { signOutStore, useSignOutOpen } from "./shellStores";

export function SignOutSheet() {
  const open = useSignOutOpen();
  const { logout } = useAuth();
  const router = useRouter();
  const { toast } = useToast();
  return (
    <ConfirmSheet
      open={open}
      onClose={signOutStore.close}
      title="Sign out of Edward?"
      body="You'll need your password to come back in. I'll keep working while you're away."
      confirmLabel="Sign out"
      tone="danger"
      onConfirm={async () => {
        // Only a sign-out the server confirmed leaves: otherwise the cookie
        // is still good and "signed out" would be false (2026-09-26).
        try {
          await logout();
        } catch {
          toast({
            title: "I couldn't sign you out",
            body: "The Mac didn't answer, so you're still signed in. Try again in a moment.",
            tone: "bad",
          });
          return;
        }
        signOutStore.close();
        router.push("/login");
      }}
    />
  );
}
