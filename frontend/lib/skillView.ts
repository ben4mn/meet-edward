// Settings → House → Skills and Edward's servers (2026-09-26). Pure;
// pinned in __tests__/skillView.test.ts.
//
// Skills are grouped by what they let Edward do, named and described in
// plain words (the server's descriptions are for engineers). Status is a
// dot and one word; an error says what is wrong.

export type StatusTone = "ok" | "warn" | "bad" | "neutral";

export interface SkillLike {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  status: string;
  status_message: string | null;
}

type GroupKey = "messages" | "phone" | "web" | "home" | "code" | "work" | "other";

const GROUP_ORDER: GroupKey[] = ["messages", "phone", "web", "home", "code", "work", "other"];

const GROUP_LABEL: Record<GroupKey, string> = {
  messages: "Messages",
  phone: "Your phone",
  web: "Web",
  home: "Apple & home",
  code: "Code",
  work: "Helpers",
  other: "Other",
};

const SKILL_GROUP: Record<string, GroupKey> = {
  imessage_applescript: "messages",
  twilio_sms: "messages",
  twilio_whatsapp: "messages",
  whatsapp_mcp: "messages",
  push_notifications: "phone",
  ios_widget: "phone",
  voice: "phone",
  brave_search: "web",
  html_hosting: "web",
  apple_services: "home",
  contacts_lookup: "home",
  alexa_outbound: "home",
  alexa_inbound: "home",
  code_interpreter: "code",
  javascript_interpreter: "code",
  sql_interpreter: "code",
  shell_interpreter: "code",
  orchestrator: "work",
  claude_session: "work",
};

const SKILL_COPY: Record<string, { name: string; description: string }> = {
  imessage_applescript: { name: "iMessage", description: "Send iMessages from your number." },
  twilio_sms: { name: "Texts from my number", description: "Send and receive texts from my own phone number." },
  twilio_whatsapp: { name: "WhatsApp from my number", description: "Send and receive WhatsApp from my own number." },
  whatsapp_mcp: { name: "Your WhatsApp", description: "Read and send WhatsApp as you." },
  push_notifications: { name: "Notifications", description: "Tap you on the shoulder on your phone." },
  ios_widget: { name: "iPhone widget", description: "Show things on your home-screen widget." },
  voice: { name: "Voice", description: "Voice memos and live conversation." },
  brave_search: { name: "Web search", description: "Search the web and read pages." },
  html_hosting: { name: "Web pages", description: "Publish pages I make to your page host." },
  apple_services: { name: "Apple apps", description: "Calendar, Reminders, Notes, Mail and Maps on this Mac." },
  contacts_lookup: { name: "Contacts", description: "Look people up in your Contacts." },
  alexa_outbound: { name: "Echo announcements", description: "Speak on your Echo devices through Home Assistant." },
  alexa_inbound: { name: "Alexa skill", description: "Answer when you ask Alexa for me." },
  code_interpreter: { name: "Python", description: "Run Python in a sandbox." },
  javascript_interpreter: { name: "JavaScript", description: "Run JavaScript in a sandbox." },
  sql_interpreter: { name: "SQL", description: "Keep a scratch database for each conversation." },
  shell_interpreter: { name: "Shell", description: "Run shell commands in a sandbox." },
  orchestrator: { name: "Helpers", description: "Start helper agents for big jobs." },
  claude_session: { name: "Claude Code", description: "Start a coding session you can open in the Claude app." },
};

export function skillCopy(skill: SkillLike): { name: string; description: string } {
  return SKILL_COPY[skill.id] ?? { name: skill.name, description: skill.description };
}

export function groupSkills<S extends SkillLike>(skills: S[]): { key: string; label: string; skills: S[] }[] {
  const by = new Map<GroupKey, S[]>();
  for (const s of skills) {
    const g = SKILL_GROUP[s.id] ?? "other";
    by.set(g, [...(by.get(g) ?? []), s]);
  }
  return GROUP_ORDER.filter((g) => by.has(g)).map((g) => ({ key: g, label: GROUP_LABEL[g], skills: by.get(g)! }));
}

export function skillStatus(skill: SkillLike): { tone: StatusTone; text: string } {
  switch (skill.status) {
    case "connected":
      return { tone: "ok", text: "Connected" };
    case "connecting":
      return { tone: "warn", text: "Starting…" };
    case "error":
      return { tone: "bad", text: skill.status_message?.trim() || "Can't connect" };
    default:
      return { tone: "neutral", text: "Off" };
  }
}

/** "16 on · 1 needs a look". */
export function skillsSummary(skills: SkillLike[]): string {
  const on = skills.filter((s) => s.enabled).length;
  const broken = skills.filter((s) => s.enabled && s.status === "error").length;
  return broken ? `${on} on · ${broken} ${broken === 1 ? "needs" : "need"} a look` : `${on} on`;
}

export function serverStatus(status: string): { tone: StatusTone; text: string } {
  switch (status) {
    case "connected":
      return { tone: "ok", text: "Running" };
    case "starting":
      return { tone: "warn", text: "Starting…" };
    case "error":
      return { tone: "bad", text: "Stopped with an error" };
    case "stopped":
      return { tone: "neutral", text: "Stopped" };
    default:
      return { tone: "neutral", text: "Off" };
  }
}

/** Dot colour for a status tone. */
export const STATUS_DOT: Record<StatusTone, string> = {
  ok: "bg-ok",
  warn: "bg-warn",
  bad: "bg-bad",
  neutral: "bg-text-muted/50",
};
