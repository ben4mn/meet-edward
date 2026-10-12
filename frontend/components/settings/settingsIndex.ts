// The Edward tab's index: every panel's slug, title, one-line description,
// icon and search keywords — and nothing that renders a panel. The rail
// (on every settings route), the landing, the rooms and the A–Z page read
// this; only the panel page imports settingsConfig, which adds the render.
// Pinned in __tests__/settingsIndex.test.ts.
//
// Titles and descriptions speak to the owner (docs/design-system-2026-09-25.md
// §6: no "heartbeat", "orchestrator", "MCP" on owner-facing surfaces). The old and
// technical names stay findable through `keywords`, and the pre-redesign
// category through `category` — both searchable, never shown.

import {
  BarChart3,
  Bell,
  Brain,
  Calendar,
  Cpu,
  Database,
  FileText,
  FolderOpen,
  GitBranch,
  Globe,
  Image as ImageIcon,
  Lightbulb,
  Mail,
  Moon,
  Sparkles,
  Volume2,
  Zap,
  Ear,
  NotebookPen,
  Library,
  Server,
  type LucideIcon,
} from "lucide-react";
import { ROOM_SLUGS, type RoomId } from "./settingsRooms";

export type SettingsCategory = "core" | "integrations" | "automation" | "system";

export const CATEGORY_LABEL: Record<SettingsCategory, string> = {
  core: "Core",
  integrations: "Integrations",
  automation: "Automation",
  system: "System",
};

export interface SettingsIndexItem {
  slug: string;
  title: string;
  description: string;
  icon: LucideIcon;
  /** Pre-redesign category — searchable, never shown. */
  category: SettingsCategory;
  /** Old or technical names — searchable, never shown. */
  keywords?: string;
}

export const SETTINGS_INDEX: readonly SettingsIndexItem[] = [
  // Mind
  { slug: "thoughts", title: "Thoughts", description: "What he intends, what each wake did, your verdicts", icon: Brain, category: "core", keywords: "mind wakes intentions" },
  { slug: "journal", title: "Journal", description: "His week: what he noticed and decided", icon: NotebookPen, category: "automation", keywords: "week observations" },
  { slug: "model", title: "Model", description: "Which model he thinks with", icon: Cpu, category: "core", keywords: "provider gpt codex reasoning" },
  { slug: "skills", title: "Skills", description: "What he can use: messages, search, code…", icon: Zap, category: "core", keywords: "integrations tools" },
  { slug: "mcp", title: "Servers", description: "Extra tools he installed for himself", icon: Server, category: "integrations", keywords: "mcp custom servers edward's servers" },
  // Memory
  { slug: "memories", title: "Memories", description: "What he remembers, and where it came from", icon: Library, category: "core", keywords: "long-term memory forget" },
  { slug: "documents", title: "Documents", description: "Pages he keeps: notes, recipes, his state", icon: FileText, category: "core", keywords: "docs" },
  { slug: "files", title: "Files", description: "Files he saved and files you sent", icon: FolderOpen, category: "system", keywords: "storage uploads" },
  { slug: "databases", title: "Databases", description: "Data tables he keeps", icon: Database, category: "system", keywords: "sql tables" },
  { slug: "dreaming", title: "Dreaming", description: "How he tidies his memory overnight", icon: Moon, category: "automation", keywords: "consolidation connections flags contradictions" },
  // Attention
  { slug: "heartbeat", title: "Listening", description: "What he heard in messages and mail, and what he did", icon: Ear, category: "automation", keywords: "heartbeat triage imessage calendar email" },
  { slug: "notifications", title: "Notifications", description: "How he reaches your phone", icon: Bell, category: "system", keywords: "push approval approve held sends waiting" },
  { slug: "proactive", title: "Proactive", description: "When he wakes on his own, and what he keeps on people", icon: Sparkles, category: "integrations", keywords: "wakes dossiers" },
  { slug: "gmail", title: "Gmail", description: "Let him read your Gmail", icon: Mail, category: "integrations", keywords: "email google" },
  { slug: "alexa", title: "Alexa", description: "Echo announcements and voice", icon: Volume2, category: "integrations", keywords: "echo speaker" },
  // Work
  { slug: "events", title: "Events", description: "Reminders and tasks he has scheduled", icon: Calendar, category: "automation", keywords: "scheduled reminders cron" },
  { slug: "orchestrator", title: "Workers", description: "Helpers he sends off on longer jobs", icon: GitBranch, category: "automation", keywords: "orchestrator agents codex" },
  { slug: "wishlist", title: "Wishlist", description: "Things he wishes he could do", icon: Lightbulb, category: "automation", keywords: "features wishes" },
  { slug: "usage", title: "Usage", description: "Tokens he has spent, by model and day", icon: BarChart3, category: "system", keywords: "tokens cost fuel quota" },
  // House
  { slug: "locale", title: "Locale", description: "The timezone he and the app keep", icon: Globe, category: "core", keywords: "timezone time zone" },
  { slug: "hub-photos", title: "Hub Photos", description: "The hub's screensaver album", icon: ImageIcon, category: "system", keywords: "screensaver pi" },
];

const BY_SLUG = new Map(SETTINGS_INDEX.map((i) => [i.slug, i]));

export function indexItem(slug: string): SettingsIndexItem | undefined {
  return BY_SLUG.get(slug);
}

export function itemsInRoom(room: RoomId): SettingsIndexItem[] {
  return ROOM_SLUGS[room].map((s) => BY_SLUG.get(s)).filter((i): i is SettingsIndexItem => Boolean(i));
}

/** Rows for searchSettings: the old category joins the keywords. */
export function searchableIndex(): Array<SettingsIndexItem & { was: string }> {
  return SETTINGS_INDEX.map((i) => ({ ...i, was: CATEGORY_LABEL[i.category] }));
}
