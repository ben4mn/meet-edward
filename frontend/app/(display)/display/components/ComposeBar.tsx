"use client";

// Always-on compose bar for the display view. Wired to the same chat
// endpoint as /chat and /inbox so the conversation lives in the shared
// inbox — per the "one intelligent entity" principle. Stores the
// display's conversation id in localStorage so follow-up messages keep
// the thread together.

import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import Link from "next/link";
import { ArrowUpRight, Mic, Send, X } from "lucide-react";

import { streamChatEvents } from "@/lib/api";
import { useBuildReloadHold } from "@/lib/buildReload";
import {
  QUICK_ACTIONS,
  applyComposeEvent,
  classifyComposeKey,
  composeCardStatus,
  displayInboxHref,
  initialComposeState,
  thinkingLines,
  type ComposeState,
} from "../lib/composeBarController";
import { ComposeAnswer, ComposeThinking } from "./ComposeReply";

const STORAGE_KEY = "edward_display_conversation_id";
const REPLY_DISMISS_MS = 90_000;

interface Props {
  /** The bar is up — a reply card on screen or the owner typing: the stage keeps his lane clear of it. */
  onActiveChange?: (active: boolean) => void;
}

export function ComposeBar({ onActiveChange }: Props = {}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [card, setCard] = useState<ComposeState | null>(null);
  const cardRef = useRef<HTMLDivElement | null>(null);
  const pinnedRef = useRef(false); // the reader scrolled up or hovered: stop following, stop the dismiss timer
  const [conversationId, setConversationId] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const dismissRef = useRef<number | null>(null);
  const [focused, setFocused] = useState(false);

  // The card's 90 s dismiss never outlives the bar.
  useEffect(
    () => () => {
      if (dismissRef.current) window.clearTimeout(dismissRef.current);
    },
    [],
  );

  const active = card !== null || focused;
  const activeRef = useRef(onActiveChange);
  activeRef.current = onActiveChange;
  useEffect(() => {
    activeRef.current?.(active);
  }, [active]);

  // A new deploy's reload waits while Edward answers (lib/buildReload.ts):
  // one Enter is the last input, a turn with tools outlasts the display's
  // half-minute quiet window, and a reload closes the stream, which
  // cancels the turn part-way (2026-09-27 review). The answer itself is in
  // the shared thread, so the card on screen does not hold.
  useBuildReloadHold(busy);

  // Hydrate stored conversation id once on mount.
  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      if (stored) setConversationId(stored);
    } catch {
      /* localStorage blocked — ignore */
    }
  }, []);

  // Press `/` anywhere on the display (except in another input) to focus.
  useEffect(() => {
    function onGlobalKey(e: globalThis.KeyboardEvent) {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      const active = document.activeElement as HTMLElement | null;
      const tag = active?.tagName.toLowerCase();
      if (tag === "input" || tag === "textarea" || active?.isContentEditable) return;
      e.preventDefault();
      textareaRef.current?.focus();
    }
    document.addEventListener("keydown", onGlobalKey);
    return () => document.removeEventListener("keydown", onGlobalKey);
  }, []);

  const autoSize = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
  }, []);

  useEffect(autoSize, [text, autoSize]);

  const sendText = useCallback(
    async (message: string) => {
      if (!message.trim() || busy) return;
      setBusy(true);
      setText("");

      let currentId: string | undefined = conversationId ?? undefined;
      let buffer = "";

      setCard(initialComposeState());
      pinnedRef.current = false;
      if (dismissRef.current) {
        window.clearTimeout(dismissRef.current);
        dismissRef.current = null;
      }

      try {
        for await (const event of streamChatEvents(message, currentId)) {
          if (event.conversation_id && currentId !== event.conversation_id) {
            currentId = event.conversation_id;
            setConversationId(event.conversation_id);
            try {
              window.localStorage.setItem(STORAGE_KEY, event.conversation_id);
            } catch {
              /* localStorage blocked — fine */
            }
          }
          if (event.type === "content" && typeof event.content === "string") buffer += event.content;
          setCard((prev) => applyComposeEvent(prev ?? initialComposeState(), event));
        }
      } catch {
        setCard((prev) => ({ ...(prev ?? initialComposeState()), reply: (buffer || "") + "\n(connection failed — try again)" }));
      } finally {
        setBusy(false);
        armDismiss();
      }
    },
    [busy, conversationId],
  );

  const submit = useCallback(() => sendText(text), [sendText, text]);

  function armDismiss() {
    if (dismissRef.current) window.clearTimeout(dismissRef.current);
    dismissRef.current = window.setTimeout(() => {
      if (pinnedRef.current) return; // someone is reading it
      setCard(null);
    }, REPLY_DISMISS_MS);
  }

  function dismissReply() {
    setCard(null);
    if (dismissRef.current) {
      window.clearTimeout(dismissRef.current);
      dismissRef.current = null;
    }
  }

  // Follow the tail while Edward works, unless the reader scrolled up.
  useEffect(() => {
    const el = cardRef.current;
    if (!el || !busy || pinnedRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [card, busy]);

  function onKeyDown(e: ReactKeyboardEvent<HTMLTextAreaElement>) {
    const result = classifyComposeKey(e);
    if (result === "submit") {
      submit();
    } else if (result === "clear") {
      setText("");
      textareaRef.current?.blur();
    }
  }

  const inboxHref = displayInboxHref(conversationId);
  const hasThinking = !!card && thinkingLines(card.thinking).length > 0;

  return (
    <div style={{ position: "relative" }}>
      {card !== null && (
        <div
          data-testid="compose-reply-card"
          ref={cardRef}
          onMouseEnter={() => { pinnedRef.current = true; }}
          onMouseLeave={() => { pinnedRef.current = false; if (!busy) armDismiss(); }}
          onScroll={(e) => {
            const el = e.currentTarget;
            pinnedRef.current = el.scrollTop + el.clientHeight < el.scrollHeight - 24;
          }}
          style={{
            position: "absolute",
            bottom: "calc(100% + 12px)",
            left: "50%",
            transform: "translateX(-50%)",
            width: "min(960px, 80%)",
            maxHeight: "62vh",
            overflowY: "auto",
            padding: "12px 18px 12px",
            background: "var(--ed-surface)",
            border: "1px solid var(--ed-border-strong)",
            borderRadius: 10,
            color: "var(--ed-text)",
            fontSize: 13,
            lineHeight: 1.55,
            boxShadow: "0 8px 24px rgba(0,0,0,0.35)",
            animation: "compose-card-in 220ms ease-out",
            zIndex: 6,
          }}
        >
          <div
            style={{
              position: "sticky",
              top: -12,
              margin: "-12px -18px 8px",
              padding: "8px 18px",
              background: "var(--ed-surface)",
              borderBottom: "1px solid var(--ed-border)",
              fontSize: 10,
              letterSpacing: "0.16em",
              textTransform: "uppercase",
              color: busy ? "var(--ed-accent)" : "var(--ed-text-dim)",
              display: "flex",
              alignItems: "center",
              gap: 8,
            }}
          >
            {busy && <span className="mc-dot mc-dot-pulse" style={{ background: "var(--ed-accent)", color: "var(--ed-accent)" }} />}
            {composeCardStatus(card, busy)}
            <button
              type="button"
              onClick={dismissReply}
              aria-label="Dismiss"
              style={{ marginLeft: "auto", padding: 2, background: "transparent", border: "none", color: "var(--ed-text-faint)", cursor: "pointer", lineHeight: 0 }}
            >
              <X size={14} />
            </button>
          </div>

          {hasThinking && (
            <div
              style={{
                paddingBottom: card.reply ? 10 : 0,
                marginBottom: card.reply ? 10 : 0,
                borderBottom: card.reply ? "1px dashed var(--ed-border)" : "none",
              }}
            >
              <ComposeThinking thinking={card.thinking} />
            </div>
          )}

          {card.reply ? (
            <ComposeAnswer reply={card.reply} />
          ) : (
            !hasThinking && <span style={{ color: "var(--ed-text-faint)" }}>thinking…</span>
          )}

          {inboxHref && !busy && card.reply && (
            <div style={{ marginTop: 10, paddingTop: 8, borderTop: "1px solid var(--ed-border)", display: "flex", justifyContent: "flex-end" }}>
              <Link href={inboxHref} style={{ fontSize: 11, color: "var(--ed-accent)", textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 4 }}>
                open in chat <ArrowUpRight size={12} />
              </Link>
            </div>
          )}
        </div>
      )}

      <div
        style={{
          display: "flex",
          gap: 6,
          marginBottom: 6,
          flexWrap: "wrap",
        }}
      >
        {QUICK_ACTIONS.map((action) => (
          <button
            key={action.label}
            type="button"
            onClick={() => sendText(action.prompt)}
            disabled={busy}
            style={{
              fontSize: 10,
              letterSpacing: "0.03em",
              padding: "4px 10px",
              borderRadius: 999,
              border: "1px solid var(--ed-border)",
              background: "transparent",
              color: "var(--ed-text-dim)",
              cursor: busy ? "not-allowed" : "pointer",
              opacity: busy ? 0.5 : 1,
              transition: "color 0.15s ease, border-color 0.15s ease",
            }}
            onMouseEnter={(e) => {
              if (busy) return;
              e.currentTarget.style.color = "var(--ed-accent)";
              e.currentTarget.style.borderColor = "var(--ed-accent)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.color = "var(--ed-text-dim)";
              e.currentTarget.style.borderColor = "var(--ed-border)";
            }}
          >
            {action.label}
          </button>
        ))}
      </div>

      <div
        style={{
          display: "flex",
          alignItems: "flex-end",
          gap: 10,
          padding: "10px 14px",
          background: "var(--ed-surface)",
          border: "1px solid var(--ed-border)",
          borderRadius: 12,
          transition: "border-color 0.2s ease, box-shadow 0.2s ease",
        }}
        className="mc-compose"
      >
        <Mic
          size={18}
          style={{
            color: "var(--ed-text-dim)",
            marginBottom: 8,
            flexShrink: 0,
          }}
          aria-hidden
        />
        <textarea
          ref={textareaRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          placeholder="Talk to Edward — press / to focus, Enter to send"
          rows={1}
          disabled={busy}
          aria-label="Talk to Edward"
          style={{
            flex: 1,
            resize: "none",
            background: "transparent",
            border: "none",
            outline: "none",
            color: "var(--ed-text)",
            fontSize: 14,
            fontFamily: "inherit",
            padding: "6px 0",
            maxHeight: 120,
            lineHeight: 1.4,
          }}
        />
        <button
          type="button"
          onClick={submit}
          disabled={busy || !text.trim()}
          aria-label="Send"
          style={{
            padding: "6px 12px",
            borderRadius: 8,
            // The owner's own primary action is neutral: emerald means Edward.
            background: "var(--ed-text)",
            color: "var(--ed-bg, #0a0e14)",
            border: "none",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            cursor: text.trim() && !busy ? "pointer" : "not-allowed",
            opacity: text.trim() && !busy ? 1 : 0.45,
            transition: "opacity 0.15s ease",
            flexShrink: 0,
          }}
        >
          <Send size={14} />
        </button>
      </div>
    </div>
  );
}
