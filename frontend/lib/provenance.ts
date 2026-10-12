// "Where this came from" for one memory (plan Phase 5, 2026-09-24). Pure.
//
// GET /api/memories/{id}/provenance returns only stored records: the
// memory's fields, its source conversation (and the iMessage chat or SMS
// contact it maps to), a folded-row marker, and source_ref. This turns them
// into labelled rows and never invents a source that is not there — Muse
// lost a user's trust by narrating where its knowledge came from.
// Pinned in __tests__/provenance.test.ts.

export interface ProvenanceMemory {
  id: string;
  content: string;
  origin: string;
  about: string;
  source_type: string;
  created_at: string | null;
  source_conversation_id: string | null;
  confidence?: number | null;
  tier?: string;
  reinforcement_count?: number;
}

export interface MemoryProvenance {
  memory: ProvenanceMemory;
  conversation: { id: string; title: string | null; source: string | null; channel: string | null } | null;
  chat: { chat_identifier: string; chat_name: string | null; is_group: boolean } | null;
  contact: { phone_number: string; contact_name: string | null; platform: string | null } | null;
  fold: [string, string] | null;
  source_ref: Record<string, unknown>;
  summary: string;
}

export interface ProvenanceRow {
  label: string;
  value: string;
  href?: string;
}

const ORIGIN: Record<string, string> = {
  heard: "You said it",
  read: "Edward read it (a message, email, page or screen)",
  thought: "Edward concluded or decided it",
  did: "something Edward did, and how it went",
};

const ABOUT: Record<string, string> = {
  owner: "about you",
  contact: "about someone else",
  self: "about Edward",
  world: "about the world",
};

// Where a conversation came from, in plain words (none for the owner's own chats).
const SOURCE_WORD: Record<string, string> = {
  heartbeat: "messages",
  edward_thought: "my thoughts",
  scheduled_event: "a reminder",
  orchestrator_worker: "a helper",
  twilio: "texts",
  voice: "voice",
};

// The kind of turn that kept it ("I noted it after …").
const TURN_WORD: Record<string, string> = {
  chat: "a chat",
  voice: "a voice chat",
  hub_voice: "a voice chat at the hub",
  heartbeat: "reading messages",
  edward_thought: "a wake",
  mind: "a wake",
  pulse: "checking my screens",
  scheduled_event: "a reminder fired",
  worker: "a helper's work",
  twilio: "a text",
  widget: "a widget message",
  alexa: "an Alexa request",
  briefing: "a briefing",
  background: "some background work",
};

function turnWord(v: unknown): string {
  const k = typeof v === "string" && v ? v : "chat";
  return TURN_WORD[k] ?? k.replace(/_/g, " ");
}

function list(v: unknown): string | null {
  if (Array.isArray(v) && v.length) return v.map(String).join(", ");
  return null;
}

export function provenanceRows(p: MemoryProvenance): ProvenanceRow[] {
  const m = p.memory;
  const rows: ProvenanceRow[] = [
    { label: "How", value: `${ORIGIN[m.origin] ?? m.origin}, ${ABOUT[m.about] ?? m.about}` },
  ];
  if (p.fold) {
    rows.push({ label: "Conversation", value: `folded from the old ${p.fold[0]} table (row ${p.fold[1]})` });
  } else if (p.conversation) {
    const where = SOURCE_WORD[p.conversation.source || "user"];
    rows.push({
      label: "Conversation",
      value: `${p.conversation.title || "Untitled"}${where ? ` (${where})` : ""}`,
      href: `/chat?c=${encodeURIComponent(p.conversation.id)}`,
    });
  } else if (m.source_conversation_id) {
    rows.push({ label: "Conversation", value: `${m.source_conversation_id} (no longer exists)` });
  } else {
    rows.push({ label: "Conversation", value: "not recorded" });
  }
  if (p.chat) {
    rows.push({
      label: "iMessage",
      value: [p.chat.chat_name, p.chat.chat_identifier].filter(Boolean).join(" · ") + (p.chat.is_group ? " (group)" : ""),
    });
  }
  if (p.contact) {
    rows.push({
      label: (p.contact.platform || "sms").toUpperCase(),
      value: [p.contact.contact_name, p.contact.phone_number].filter(Boolean).join(" · "),
    });
  }
  const ref = p.source_ref || {};
  const kind = typeof ref.kind === "string" ? ref.kind : null;
  const during = turnWord(ref.turn_source ?? ref.profile);
  if (kind === "remember") {
    rows.push({ label: "Kept", value: `I wrote it down myself${ref.turn_source ? `, during ${during}` : ""}` });
  } else if (kind === "extraction") {
    rows.push({ label: "Kept", value: `I noted it after ${during}` });
  } else if (kind) {
    rows.push({ label: "Kept", value: kind.replace(/_/g, " ") });
  }
  const events = list(ref.event_ids);
  if (events) rows.push({ label: "Record ids", value: events });
  const rowids = list(ref.rowids);
  if (rowids) rows.push({ label: "Message rows", value: rowids });
  if (typeof ref.url === "string") rows.push({ label: "Page", value: ref.url, href: ref.url });
  if (Array.isArray(ref.also) && ref.also.length) {
    rows.push({ label: "Reinforced", value: `${ref.also.length} later time${ref.also.length === 1 ? "" : "s"}` });
  }
  if (m.created_at) rows.push({ label: "Stored", value: m.created_at.replace("T", " ").replace(/\.\d+/, "").replace("Z", " UTC") });
  return rows;
}
