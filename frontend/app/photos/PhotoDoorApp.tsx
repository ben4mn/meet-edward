"use client";

// The household photo door (2026-10-11): /photos.
//
// A small home-screen app with its own password for someone else in the
// household. They see the photos the hub's screensaver shows, add more,
// turn or remove them, and set the screensaver. It talks only to
// /api/photo-door/* (backend/routers/photo_door.py), whose session can open
// nothing else of the owner's. The door is off until the server has
// PHOTO_DOOR_PASSWORD_HASH; then this page says so instead of asking for a
// password. Leaf imports only — the components/ui barrel pulls framer-motion.

import React, { useCallback, useEffect, useRef, useState } from "react";
import { LogOut, Plus, RotateCcw, RotateCw, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Card, EmptyState, SectionLabel, Segment } from "@/components/ui/primitives";
import { Notice } from "@/components/ui/Feedback";

const API = "/api/photo-door";
// `image/*` alone hides HEIC on some Android pickers; iOS needs the
// explicit types to offer originals.
const ACCEPT = "image/*,.heic,.HEIC,.heif,.HEIF";
const CONFIRM_MS = 4000;

type Transition = "crossfade" | "kenburns" | "slide";

interface Settings {
  slide_seconds: number;
  transition: Transition;
  edward_presents: boolean;
}

interface Album {
  photos: string[];
  skipped_unsupported: number;
}

interface Rejected {
  name: string;
  reason: string;
}

const call = (path: string, init: RequestInit = {}) => fetch(`${API}${path}`, { credentials: "include", ...init });

const sendJson = (path: string, method: string, body: unknown) =>
  call(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

const enc = encodeURIComponent;

type DoorState = "checking" | "in" | "out" | "closed";

export function PhotoDoorApp() {
  const [state, setState] = useState<DoorState>("checking");

  useEffect(() => {
    call("/auth/status")
      .then((r) => (r.ok ? r.json() : { authenticated: false }))
      .then((b) => setState(b.enabled === false ? "closed" : b.authenticated ? "in" : "out"))
      .catch(() => setState("out"));
  }, []);

  return (
    <main className="mx-auto min-h-[100dvh] max-w-2xl px-4 pb-[calc(env(safe-area-inset-bottom)+24px)] pt-[calc(env(safe-area-inset-top)+20px)]">
      {state === "checking" ? null : state === "closed" ? (
        <Closed />
      ) : state === "in" ? (
        <Photos onSignedOut={() => setState("out")} />
      ) : (
        <Door onIn={() => setState("in")} onClosed={() => setState("closed")} />
      )}
    </main>
  );
}

/** The server has no password for this door, so there is nothing to open. */
function Closed() {
  return (
    <div className="mx-auto mt-[18vh] max-w-sm space-y-3">
      <h1 className="text-title2 text-text-primary">Hub photos</h1>
      <p className="text-body text-text-muted">
        This door isn&apos;t set up yet. Whoever runs Edward can turn it on by giving it a password.
      </p>
    </div>
  );
}

function Door({ onIn, onClosed }: { onIn: () => void; onClosed: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await sendJson("/auth/login", "POST", { password });
      if (r.ok) onIn();
      else if (r.status === 403) onClosed();
      else if (r.status === 429) setError("Too many tries. Wait a few minutes.");
      else setError("That's not it. Try again.");
    } catch {
      setError("I can't reach the house right now.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="mx-auto mt-[18vh] max-w-sm space-y-4">
      <h1 className="text-title2 text-text-primary">Hub photos</h1>
      <label className="block space-y-1.5">
        <span className="text-footnote text-text-muted">Password</span>
        <input
          type="password"
          autoComplete="current-password"
          autoFocus
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="h-11 w-full rounded-md border border-border bg-surface-3 px-3 text-body text-text-primary outline-none focus:border-text-muted"
        />
      </label>
      {error ? <p className="text-footnote text-bad">{error}</p> : null}
      <Button type="submit" className="w-full" loading={busy} disabled={!password}>
        Open
      </Button>
    </form>
  );
}

function Photos({ onSignedOut }: { onSignedOut: () => void }) {
  const [album, setAlbum] = useState<Album | null>(null);
  const [failed, setFailed] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [rejected, setRejected] = useState<Rejected[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const r = await call("/photos");
      if (r.status === 401) return onSignedOut();
      if (!r.ok) throw new Error();
      setAlbum(await r.json());
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [onSignedOut]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!confirming) return;
    const t = setTimeout(() => setConfirming(null), CONFIRM_MS);
    return () => clearTimeout(t);
  }, [confirming]);

  // One file per request, in order: twenty parallel POSTs over the tunnel
  // is how photos go missing.
  const upload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const list = Array.from(files);
    const failures: Rejected[] = [];
    setRejected([]);
    setProgress({ done: 0, total: list.length });
    for (let i = 0; i < list.length; i++) {
      const form = new FormData();
      form.append("files", list[i]);
      try {
        const r = await call("/photos", { method: "POST", body: form });
        if (!r.ok) throw new Error(`upload failed (${r.status})`);
        const body = await r.json();
        failures.push(...(body.rejected ?? []));
      } catch (e) {
        failures.push({ name: list[i].name, reason: e instanceof Error ? e.message : "upload failed" });
      }
      setProgress({ done: i + 1, total: list.length });
    }
    setRejected(failures);
    setProgress(null);
    if (inputRef.current) inputRef.current.value = "";
    await load();
  };

  const rotate = async (name: string, direction: "left" | "right") => {
    setBusy(name);
    try {
      await sendJson(`/photos/${enc(name)}/rotate`, "POST", { direction });
    } finally {
      setBusy(null);
      await load();
    }
  };

  const remove = async (name: string) => {
    if (confirming !== name) return setConfirming(name);
    setConfirming(null);
    setAlbum((a) => (a ? { ...a, photos: a.photos.filter((p) => p !== name) } : a));
    await call(`/photos/${enc(name)}`, { method: "DELETE" });
    await load();
  };

  const signOut = async () => {
    await call("/auth/logout", { method: "POST" }).catch(() => undefined);
    onSignedOut();
  };

  const uploading = progress !== null;

  return (
    <div className="space-y-6">
      <header className="flex items-center justify-between">
        <h1 className="text-title2 text-text-primary">Hub photos</h1>
        <Button variant="ghost" size="sm" icon={<LogOut aria-hidden />} onClick={signOut}>
          Sign out
        </Button>
      </header>

      <div className="space-y-2">
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          multiple
          className="hidden"
          onChange={(e) => upload(e.target.files)}
        />
        <Button className="w-full" icon={<Plus aria-hidden />} loading={uploading} onClick={() => inputRef.current?.click()}>
          {uploading ? `Uploading ${progress.done} of ${progress.total}…` : "Add photos"}
        </Button>
        <p className="text-footnote text-text-muted">
          These are the photos the screen at the pet station shows when it goes idle.
        </p>
      </div>

      {rejected.length > 0 ? (
        <Notice tone="warn" title={`${rejected.length} not added`}>
          <ul className="mt-1 space-y-0.5">
            {rejected.map((r, i) => (
              <li key={`${r.name}-${i}`} className="[overflow-wrap:anywhere]">
                {r.name} — {r.reason}
              </li>
            ))}
          </ul>
        </Notice>
      ) : null}

      <section aria-label="In the album" className="space-y-2">
        <SectionLabel action={album ? String(album.photos.length) : undefined}>In the album</SectionLabel>
        {failed && !album ? (
          <Notice tone="bad" action={<Button size="sm" variant="secondary" onClick={load}>Try again</Button>}>
            I can&apos;t open the album right now.
          </Notice>
        ) : !album ? null : album.photos.length === 0 ? (
          <Card>
            <EmptyState title="Nothing here yet.">Add a few and the screen starts showing them.</EmptyState>
          </Card>
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {album.photos.map((name) => (
              <figure key={name} className="overflow-hidden rounded-md border border-border bg-surface-3">
                <div className="aspect-square bg-surface-3">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={`${API}/photos/${enc(name)}/thumb`}
                    alt=""
                    loading="lazy"
                    className={`h-full w-full object-cover ${busy === name ? "opacity-40" : ""}`}
                  />
                </div>
                <figcaption className="flex items-center justify-between">
                  <IconButton label={`Rotate ${name} left`} onClick={() => rotate(name, "left")} disabled={busy !== null}>
                    <RotateCcw className="h-4 w-4" aria-hidden />
                  </IconButton>
                  <IconButton label={`Rotate ${name} right`} onClick={() => rotate(name, "right")} disabled={busy !== null}>
                    <RotateCw className="h-4 w-4" aria-hidden />
                  </IconButton>
                  <IconButton
                    label={confirming === name ? `Really delete ${name}` : `Delete ${name}`}
                    onClick={() => remove(name)}
                    danger={confirming === name}
                  >
                    {confirming === name ? <span className="text-footnote font-medium">Delete?</span> : <Trash2 className="h-4 w-4" aria-hidden />}
                  </IconButton>
                </figcaption>
              </figure>
            ))}
          </div>
        )}
        {album && album.skipped_unsupported > 0 ? (
          <p className="px-0.5 text-footnote text-text-muted">
            {album.skipped_unsupported} file{album.skipped_unsupported === 1 ? "" : "s"} in the album can&apos;t be shown.
          </p>
        ) : null}
      </section>

      <ScreensaverSettings onSignedOut={onSignedOut} />
    </div>
  );
}

function IconButton({
  label,
  onClick,
  disabled,
  danger,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  danger?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onClick}
      disabled={disabled}
      className={`flex h-11 flex-1 items-center justify-center px-2 transition-colors duration-fast disabled:opacity-40 ${
        danger ? "text-bad" : "text-text-muted hover:text-text-primary"
      }`}
    >
      {children}
    </button>
  );
}

function ScreensaverSettings({ onSignedOut }: { onSignedOut: () => void }) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [seconds, setSeconds] = useState<number | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    call("/settings")
      .then((r) => {
        if (r.status === 401) onSignedOut();
        return r.ok ? r.json() : null;
      })
      .then((s) => s && setSettings(s))
      .catch(() => undefined);
  }, [onSignedOut]);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const save = async (patch: Partial<Settings>) => {
    setSettings((s) => (s ? { ...s, ...patch } : s));
    const r = await sendJson("/settings", "PATCH", patch).catch(() => null);
    if (r?.ok) setSettings(await r.json());
  };

  // The slider saves once it is let go, not on every pixel.
  const slide = (value: number) => {
    setSeconds(value);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      setSeconds(null);
      void save({ slide_seconds: value });
    }, 600);
  };

  if (!settings) return null;
  const shown = seconds ?? settings.slide_seconds;

  return (
    <section aria-label="Screensaver" className="space-y-3">
      <SectionLabel>Screensaver</SectionLabel>
      <Card className="space-y-5">
        <label className="block space-y-2">
          <span className="flex items-center justify-between text-body text-text-primary">
            Seconds per photo
            <span className="tabular-nums text-text-muted">{shown}s</span>
          </span>
          <input
            type="range"
            min={5}
            max={180}
            step={5}
            value={shown}
            onChange={(e) => slide(Number(e.target.value))}
            className="h-11 w-full"
          />
        </label>
        <div className="space-y-2">
          <span className="block text-body text-text-primary">Transition</span>
          <Segment<Transition>
            label="Transition"
            value={settings.transition}
            onChange={(t) => save({ transition: t })}
            options={[
              { value: "crossfade", label: "Crossfade" },
              { value: "kenburns", label: "Ken Burns" },
              { value: "slide", label: "Slide" },
            ]}
          />
        </div>
        <div className="space-y-2">
          <span className="block text-body text-text-primary">Edward pushes the slides</span>
          <Segment<"on" | "off">
            label="Edward pushes the slides"
            value={settings.edward_presents ? "on" : "off"}
            onChange={(v) => save({ edward_presents: v === "on" })}
            options={[
              { value: "on", label: "On" },
              { value: "off", label: "Off" },
            ]}
          />
        </div>
      </Card>
    </section>
  );
}
