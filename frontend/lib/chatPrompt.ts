// `/chat?prompt=` fills the composer without sending (CLAUDE.md's route
// table). Pure. The helpers lived with Edward's ideas until unit G of the
// proactivity rebuild (2026-10-11) took the ideas; the chat page reads the
// prompt once into the composer (the owner presses send) and drops it from the
// URL so a reload or back does not type it again. Pinned in
// __tests__/chatPrompt.test.ts.

export const MAX_PROMPT_CHARS = 1000;

export function chatPromptHref(prompt: string): string {
  return `/chat?prompt=${encodeURIComponent((prompt ?? "").trim())}`;
}

/** The prompt to type into the composer, or null. */
export function readPromptParam(params: { get(name: string): string | null } | null | undefined): string | null {
  const raw = params?.get("prompt");
  const text = (raw ?? "").trim();
  return text ? text.slice(0, MAX_PROMPT_CHARS) : null;
}

/** The query string with ``prompt`` removed ("?c=abc", or "" when empty). */
export function withoutPrompt(search: string): string {
  const params = new URLSearchParams((search ?? "").replace(/^\?/, ""));
  params.delete("prompt");
  const rest = params.toString();
  return rest ? `?${rest}` : "";
}
