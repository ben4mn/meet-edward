"use client";

// Sheet: a bottom sheet on the phone, a centred dialog on md+
// (docs/design-system-2026-09-25.md §3). Controlled by `open`.
//
// - framer-motion (LazyMotion + MOTION_FEATURES: drag needs domMax), sheet spring 420/36; exits at
//   0.7x; reduced motion is a 120ms fade.
// - Drag the grabber/header down to dismiss (touch and pen only, so a mouse
//   selecting text never throws the sheet away).
// - Esc closes the top-most sheet only; Tab is trapped inside; focus moves
//   in on open and returns to whatever opened it; the page behind does not
//   scroll. All of that is keyed on `open`, not on the exit animation, so it
//   is undone the moment the sheet is asked to close.

import React, { useEffect, useId, useRef, useState } from "react";
import type { ReactNode, RefObject } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, LazyMotion, m, useDragControls } from "framer-motion";
import { MOTION_FEATURES } from "./motionFeatures";
import type { PanInfo } from "framer-motion";
import { X } from "lucide-react";
import { cn } from "./cn";
import { Button, type ButtonVariant } from "./Button";
import {
  DURATION,
  EASE_IN,
  EXIT_FACTOR,
  REDUCED_FADE,
  SPRING_SHEET,
  useIsClient,
  useIsDesktop,
  usePrefersReducedMotion,
} from "./motion";

// ------------------------------------------------------------ shared state
// Open sheets, oldest first: only the top one answers Esc and Tab.
const openStack: symbol[] = [];
let scrollLocks = 0;
let savedOverflow = "";

function lockScroll() {
  if (scrollLocks++ === 0) {
    savedOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
  }
}

function unlockScroll() {
  if (scrollLocks > 0 && --scrollLocks === 0) document.body.style.overflow = savedOverflow;
}

const FOCUSABLE =
  'a[href], area[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), ' +
  'textarea:not([disabled]), iframe, [tabindex]:not([tabindex="-1"]), [contenteditable="true"]';

function focusables(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => el.getAttribute("aria-hidden") !== "true" && !el.closest("[inert]"),
  );
}

/** Keep Tab inside `panel`: wrap last -> first and first -> last. */
export function trapTab(e: KeyboardEvent, panel: HTMLElement): void {
  const els = focusables(panel);
  if (els.length === 0) {
    e.preventDefault();
    panel.focus();
    return;
  }
  const first = els[0];
  const last = els[els.length - 1];
  const active = document.activeElement;
  if (!active || !panel.contains(active)) {
    e.preventDefault();
    (e.shiftKey ? last : first).focus();
  } else if (e.shiftKey && (active === first || active === panel)) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && active === last) {
    e.preventDefault();
    first.focus();
  }
}

/** A drag that ends this far down, or this fast, dismisses. */
export function shouldDismissDrag(offsetY: number, velocityY: number): boolean {
  return offsetY > 96 || velocityY > 500;
}

// ------------------------------------------------------------ Sheet
export interface SheetProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  /** One line under the title, linked as the dialog's description. */
  description?: ReactNode;
  /** Accessible name when there is no visible title. */
  ariaLabel?: string;
  children: ReactNode;
  /** Pinned under the scrolling body (actions); takes the home-indicator inset. */
  footer?: ReactNode;
  className?: string;
  /** Classes for the scrolling body (merged; e.g. `pt-0` for content with its
   *  own sticky header, so nothing scrolls past it through the padding). */
  bodyClassName?: string;
  /** Where focus goes on open (default: the sheet itself). */
  initialFocus?: RefObject<HTMLElement>;
  hideClose?: boolean;
}

export function Sheet({ open, onClose, initialFocus, ...frame }: SheetProps) {
  const isClient = useIsClient();
  const panelRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  useEffect(() => {
    if (!open || !isClient) return;
    const token = Symbol("sheet");
    openStack.push(token);
    const returnTo = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    lockScroll();
    (initialFocus?.current ?? panelRef.current)?.focus({ preventScroll: true });

    const onKey = (e: KeyboardEvent) => {
      if (openStack[openStack.length - 1] !== token || e.isComposing) return;
      if (e.key === "Escape") {
        e.stopPropagation();
        onCloseRef.current();
      } else if (e.key === "Tab" && panelRef.current) {
        trapTab(e, panelRef.current);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      const i = openStack.indexOf(token);
      if (i >= 0) openStack.splice(i, 1);
      unlockScroll();
      if (returnTo && returnTo.isConnected) returnTo.focus({ preventScroll: true });
    };
    // initialFocus is read once per opening on purpose.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, isClient]);

  if (!isClient) return null;
  return createPortal(
    <LazyMotion features={MOTION_FEATURES} strict>
      <AnimatePresence>
        {open ? <SheetFrame key="sheet" panelRef={panelRef} onClose={() => onCloseRef.current()} {...frame} /> : null}
      </AnimatePresence>
    </LazyMotion>,
    document.body,
  );
}

function SheetFrame({
  title,
  description,
  ariaLabel,
  children,
  footer,
  className,
  bodyClassName,
  hideClose,
  onClose,
  panelRef,
}: Omit<SheetProps, "open" | "onClose" | "initialFocus"> & {
  onClose: () => void;
  panelRef: RefObject<HTMLDivElement>;
}) {
  const titleId = useId();
  const descId = useId();
  const reduced = usePrefersReducedMotion();
  const desktop = useIsDesktop();
  const drag = useDragControls();
  const canDrag = !desktop && !reduced;

  const exitTween = { duration: DURATION.base * EXIT_FACTOR, ease: EASE_IN };
  const motionProps = reduced
    ? { initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0, transition: REDUCED_FADE }, transition: REDUCED_FADE }
    : desktop
      ? {
          initial: { opacity: 0, scale: 0.97, y: 8 },
          animate: { opacity: 1, scale: 1, y: 0 },
          exit: { opacity: 0, scale: 0.98, y: 4, transition: { duration: DURATION.fast * EXIT_FACTOR, ease: EASE_IN } },
          transition: SPRING_SHEET,
        }
      : {
          initial: { y: "100%" },
          animate: { y: 0 },
          exit: { y: "100%", transition: exitTween },
          transition: SPRING_SHEET,
        };

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center md:items-center md:p-6">
      <m.div
        data-sheet-backdrop=""
        aria-hidden="true"
        className="absolute inset-0 bg-black/55 backdrop-blur-[2px]"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0, transition: { duration: DURATION.base * EXIT_FACTOR } }}
        transition={reduced ? REDUCED_FADE : { duration: DURATION.base }}
        onClick={onClose}
      />
      <m.div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        aria-label={title ? undefined : ariaLabel}
        aria-describedby={description ? descId : undefined}
        tabIndex={-1}
        className={cn(
          "relative flex max-h-[88dvh] w-full flex-col overflow-hidden rounded-t-xl border border-b-0 border-border bg-surface-elevated shadow-e3 outline-none",
          "md:max-h-[80dvh] md:max-w-lg md:rounded-xl md:border-b md:shadow-e2",
          className,
        )}
        {...motionProps}
        drag={canDrag ? "y" : false}
        dragControls={drag}
        dragListener={false}
        dragConstraints={{ top: 0, bottom: 0 }}
        dragElastic={{ top: 0, bottom: 0.7 }}
        onDragEnd={(_: unknown, info: PanInfo) => {
          if (shouldDismissDrag(info.offset.y, info.velocity.y)) onClose();
        }}
      >
        <div
          className={cn("flex-none px-4 pt-2 md:pt-4", canDrag && "touch-none")}
          onPointerDown={(e) => {
            if (canDrag && e.pointerType !== "mouse") drag.start(e);
          }}
        >
          <div className="mx-auto mb-2 h-1 w-9 rounded-full bg-text-muted/40 md:hidden" aria-hidden />
          {title || !hideClose ? (
            <div className="flex min-h-[44px] items-center justify-between gap-3">
              {title ? (
                <h2 id={titleId} className="min-w-0 text-title3 text-text-primary">
                  {title}
                </h2>
              ) : (
                <span />
              )}
              {hideClose ? null : (
                <button
                  type="button"
                  aria-label="Close"
                  onClick={onClose}
                  className="-mr-2 inline-flex h-11 w-11 flex-none items-center justify-center rounded-full text-text-muted transition-[background-color,color,transform] duration-press hover:bg-surface-3 hover:text-text-primary active:scale-[.94]"
                >
                  <X className="h-5 w-5" aria-hidden />
                </button>
              )}
            </div>
          ) : null}
          {description ? (
            <p id={descId} className="-mt-1 text-footnote text-text-secondary">
              {description}
            </p>
          ) : null}
        </div>
        <div
          className={cn(
            "min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 pt-2",
            footer ? "pb-4" : "pb-safe md:pb-4",
            bodyClassName,
          )}
        >
          {children}
        </div>
        {footer ? <div className="flex-none border-t border-border px-4 pt-3 pb-safe md:pb-4">{footer}</div> : null}
      </m.div>
    </div>
  );
}

// ------------------------------------------------------------ ConfirmSheet
export interface ConfirmSheetProps {
  open: boolean;
  /** Cancel, Esc, the backdrop and the X all come here. Confirm does not:
   *  close the sheet yourself once `onConfirm` has done its work. */
  onClose: () => void;
  /** May return a promise; the confirm button shows busy until it settles. */
  onConfirm: () => void | Promise<unknown>;
  title: ReactNode;
  body?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** `danger` (default) for anything that loses something. */
  tone?: Extract<ButtonVariant, "danger" | "primary" | "edward">;
  busy?: boolean;
}

export function ConfirmSheet({
  open,
  onClose,
  onConfirm,
  title,
  body,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  tone = "danger",
  busy = false,
}: ConfirmSheetProps) {
  const [pending, setPending] = useState(false);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const confirm = async () => {
    const result = onConfirm();
    if (result && typeof (result as Promise<unknown>).then === "function") {
      setPending(true);
      try {
        await result;
      } finally {
        if (alive.current) setPending(false);
      }
    }
  };

  const working = busy || pending;
  return (
    <Sheet
      open={open}
      onClose={working ? () => {} : onClose}
      title={title}
      className="md:max-w-sm"
      footer={
        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button variant="secondary" onClick={onClose} disabled={working} className="w-full sm:w-auto">
            {cancelLabel}
          </Button>
          <Button variant={tone} onClick={confirm} loading={working} className="w-full sm:w-auto">
            {confirmLabel}
          </Button>
        </div>
      }
    >
      {body ? <div className="text-body text-text-secondary">{body}</div> : null}
    </Sheet>
  );
}
