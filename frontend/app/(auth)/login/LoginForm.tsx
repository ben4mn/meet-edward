"use client";

// The door's form (docs/design-system-2026-09-25.md §4 "login"): Edward's
// arrival is the moment. He is the one live Edward on the page; he politely
// looks away while a hidden password is typed, peeks when it is shown,
// winces at a wrong one, and nods on the right one before the app fades in.
// Signed in, the front door is Today. The pure half lives in
// lib/loginFlow.ts; the page (page.tsx) loads this module as its own chunk.
// Before any password exists the form also asks for the one-time setup
// code from backend/.setup_code on the Mac (2026-10-12 review: request
// headers could not prove the first password came from the Mac).

import React, { useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Eye, EyeOff } from "lucide-react";
import { useAuth } from "@/lib/AuthContext";
import { shouldLeaveLogin } from "@/lib/authSession";
import { EdwardAvatar } from "@/components/EdwardAvatar";
// Leaf imports: the components/ui barrel would pull framer-motion onto /login.
import { Button } from "@/components/ui/Button";
import { CONTROL } from "@/components/ui/Field";
import type { Beat } from "@/lib/edwardMood";
import {
  LOGIN_FADE_MS,
  LOGIN_NOD_MS,
  SETUP_CODE_HINT,
  asksForSetupCode,
  isSetupCodeRefusal,
  loginErrorCopy,
  loginGaze,
  loginRedirectTarget,
} from "@/lib/loginFlow";
import { useOwnerName } from "@/lib/owner";
import { cn } from "@/lib/utils";

export function LoginForm() {
  const [password, setPassword] = useState("");
  const [setupCode, setSetupCode] = useState("");
  const [error, setError] = useState("");
  const [reveal, setReveal] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  // idle → nod (the right password: he nods) → leaving (the door fades) → Today
  const [stage, setStage] = useState<"idle" | "nod" | "leaving">("idle");
  const leaving = stage !== "idle";
  const [beat, setBeat] = useState<{ beat: Beat; key: number } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const codeRef = useRef<HTMLInputElement>(null);
  const { login, setup, needsSetup, setupCodeRequired, checkAuth, isLoading, isAuthenticated, authChecked, unreachable } =
    useAuth();
  const askCode = asksForSetupCode({ needsSetup, setupCodeRequired });
  // From /api/auth/status (EDWARD_OWNER_NAME); "" when unset.
  const ownerName = useOwnerName();
  const router = useRouter();
  const searchParams = useSearchParams();

  // Safe same-site target; the front door (Today) by default.
  const redirectTarget = loginRedirectTarget(searchParams?.get("redirect"));

  // No dock here: toasts sit at the bottom edge.
  useEffect(() => {
    document.documentElement.style.setProperty("--dock-h", "0px");
  }, []);

  // Already signed in — on the server's verdict, never on the optimistic
  // hint (with a dead cookie that was a /login ↔ /chat loop), and never
  // while the password still needs setting (that looped /login ↔ /today).
  // While the server is unreachable AuthProvider keeps asking, so a good
  // session leaves on its own once it answers.
  useEffect(() => {
    if (leaving) return;
    if (shouldLeaveLogin({ isLoading, isAuthenticated, authChecked, needsSetup })) {
      router.push(redirectTarget);
    }
  }, [isLoading, isAuthenticated, authChecked, needsSetup, router, redirectTarget, leaving]);

  const play = (b: Beat) => setBeat((prev) => ({ beat: b, key: (prev?.key ?? 0) + 1 }));

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (isSubmitting || leaving) return;
    setError("");
    if (askCode && !setupCode.trim()) {
      setError("The setup code first, please.");
      play("wince");
      codeRef.current?.focus();
      return;
    }
    if (needsSetup && password.length < 8) {
      setError("At least 8 characters, please.");
      play("wince");
      return;
    }
    setIsSubmitting(true);
    const settingUp = needsSetup;
    try {
      if (settingUp) await setup(password, askCode ? setupCode.trim() : undefined);
      else await login(password);
      setStage("nod");
      play("nod");
      setTimeout(() => setStage("leaving"), LOGIN_NOD_MS);
      setTimeout(() => router.push(redirectTarget), LOGIN_NOD_MS + LOGIN_FADE_MS);
    } catch (err) {
      const message = err instanceof Error ? err.message : null;
      // Setting up, the server's refusal says what to do; it is shown as is.
      setError(loginErrorCopy(message, { setup: settingUp }));
      play("wince");
      const target = askCode && isSetupCodeRefusal(message) ? codeRef : inputRef;
      requestAnimationFrame(() => target.current?.select());
      // A password set meanwhile (on the Mac) turns this into the sign-in form.
      if (settingUp) void checkAuth();
    } finally {
      setIsSubmitting(false);
    }
  };

  const gaze = leaving ? null : loginGaze({ reveal, hasText: password.length > 0 });

  return (
    <div
      data-login-stage={stage}
      className={cn(
        "relative flex w-full max-w-[360px] flex-col items-center",
        "transition-[opacity,transform,filter] duration-slow ease-in-out",
        "motion-reduce:scale-100 motion-reduce:blur-0 motion-reduce:transition-opacity motion-reduce:duration-[120ms]",
        stage === "leaving" && "scale-[.98] opacity-0 blur-[2px]",
      )}
    >
      <div className="relative flex h-[168px] w-[168px] items-center justify-center motion-safe:animate-fade-rise">
        <div
          aria-hidden
          className="absolute inset-0 rounded-full bg-[radial-gradient(closest-side,rgb(var(--terminal-rgb)/0.16),transparent)]"
        />
        <EdwardAvatar
          size="xl"
          live
          mood={isSubmitting ? "think" : "idle"}
          gaze={gaze}
          beat={beat?.beat ?? null}
          beatKey={beat?.key}
          aria-label="Edward"
        />
      </div>

      <h1 className="mt-5 text-display text-text-primary">Edward</h1>
      <p className="mt-1.5 text-body text-text-secondary">
        {needsSetup ? "Choose a password to keep me yours." : ownerName ? `Welcome back, ${ownerName}.` : "Welcome back."}
      </p>
      {unreachable && !leaving ? (
        <p role="status" className="mt-2 text-footnote text-text-muted">
          I can&apos;t reach the Mac right now — I&apos;ll keep trying.
        </p>
      ) : null}

      <form onSubmit={handleSubmit} className="mt-8 w-full space-y-3" noValidate>
        {/* For password managers: the account this password belongs to. */}
        <input
          type="text"
          name="username"
          autoComplete="username"
          value="edward"
          readOnly
          tabIndex={-1}
          aria-hidden="true"
          className="sr-only"
        />
        {askCode ? (
          <div>
            <label htmlFor="setup-code" className="sr-only">
              Setup code
            </label>
            <input
              ref={codeRef}
              id="setup-code"
              name="setup-code"
              type="text"
              autoComplete="one-time-code"
              autoCapitalize="characters"
              autoCorrect="off"
              spellCheck={false}
              enterKeyHint="next"
              value={setupCode}
              onChange={(e) => {
                setSetupCode(e.target.value);
                if (error) setError("");
              }}
              placeholder="Setup code"
              aria-describedby={error ? "setup-code-hint login-error" : "setup-code-hint"}
              className={cn(CONTROL, "h-12 rounded-lg bg-surface px-4 font-mono tracking-wider shadow-e1")}
              autoFocus
              disabled={isSubmitting || leaving}
            />
            <p id="setup-code-hint" className="mt-1.5 text-footnote text-text-muted">
              {SETUP_CODE_HINT}
            </p>
          </div>
        ) : null}
        <div className="relative">
          <label htmlFor="password" className="sr-only">
            Password
          </label>
          <input
            ref={inputRef}
            id="password"
            name="password"
            type={reveal ? "text" : "password"}
            autoComplete={needsSetup ? "new-password" : "current-password"}
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            enterKeyHint="go"
            value={password}
            onChange={(e) => {
              setPassword(e.target.value);
              if (error) setError("");
            }}
            placeholder={needsSetup ? "New password" : "Password"}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? "login-error" : needsSetup ? "login-hint" : undefined}
            className={cn(CONTROL, "h-12 rounded-lg bg-surface pl-4 pr-12 shadow-e1")}
            autoFocus={!askCode}
            disabled={isSubmitting || leaving}
          />
          <button
            type="button"
            onClick={() => {
              setReveal((r) => !r);
              inputRef.current?.focus({ preventScroll: true });
            }}
            aria-label={reveal ? "Hide password" : "Show password"}
            aria-pressed={reveal}
            className={cn(
              "absolute right-1 top-1/2 inline-flex h-10 w-10 -translate-y-1/2 items-center justify-center rounded-md",
              "text-text-muted transition-colors duration-fast hover:bg-surface-3 hover:text-text-primary",
            )}
          >
            {reveal ? <EyeOff className="h-5 w-5" aria-hidden /> : <Eye className="h-5 w-5" aria-hidden />}
          </button>
        </div>

        <div className="min-h-[20px] text-center" aria-live="polite">
          {error ? (
            <p id="login-error" role="alert" className="text-footnote text-bad">
              {error}
            </p>
          ) : needsSetup ? (
            <p id="login-hint" className="text-footnote text-text-muted">
              At least 8 characters.
            </p>
          ) : null}
        </div>

        <Button
          type="submit"
          variant="primary"
          size="lg"
          className="w-full rounded-lg"
          loading={isSubmitting}
          disabled={!password || (askCode && !setupCode.trim()) || leaving}
        >
          {needsSetup ? "Set password" : "Sign in"}
        </Button>
      </form>
    </div>
  );
}
