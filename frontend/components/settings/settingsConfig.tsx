// The Edward tab's panels: the index (settingsIndex.ts — titles, one-line
// descriptions, icons, search keywords) plus how each panel renders. Only
// the panel page (/settings/<slug>) imports this file, and every panel is
// its own chunk (next/dynamic), so opening one panel loads that panel's code
// and nothing else — the rail and the landing never pull the panels in.
//
// Contract with the panels: the page frames each one in PanelShell (title,
// description, breadcrumb to its room), so every panel is rendered with
// `hideHeader` and must draw content only.

import React from "react";
import dynamic from "next/dynamic";
import type { ReactNode } from "react";
import { Pulse } from "@/components/ui";
import { CATEGORY_LABEL, SETTINGS_INDEX, type SettingsCategory, type SettingsIndexItem } from "./settingsIndex";

export { CATEGORY_LABEL };
export type { SettingsCategory };

export interface SettingsItem extends SettingsIndexItem {
  render: () => ReactNode;
}

const opening = () => <Pulse center label="Opening the panel" />;

type PanelProps = { isExpanded?: boolean; hideHeader?: boolean };

const MindPanel = dynamic<PanelProps>(() => import("./MindPanel").then((m) => m.MindPanel), { loading: opening });
const JournalPanel = dynamic<PanelProps>(() => import("./JournalPanel").then((m) => m.JournalPanel), { loading: opening });
const ModelPanel = dynamic<PanelProps>(() => import("./ModelPanel").then((m) => m.ModelPanel), { loading: opening });
const SkillsPanel = dynamic<PanelProps>(() => import("./SkillsPanel").then((m) => m.SkillsPanel), { loading: opening });
const CustomMCPPanel = dynamic<PanelProps>(() => import("./CustomMCPPanel").then((m) => m.CustomMCPPanel), { loading: opening });
const MemoryBrowser = dynamic<PanelProps>(() => import("./MemoryBrowser").then((m) => m.MemoryBrowser), { loading: opening });
const DocumentBrowser = dynamic<PanelProps>(() => import("./DocumentBrowser").then((m) => m.DocumentBrowser), { loading: opening });
const FileBrowser = dynamic<PanelProps>(() => import("./FileBrowser").then((m) => m.FileBrowser), { loading: opening });
const DatabaseBrowser = dynamic<PanelProps>(() => import("./DatabaseBrowser").then((m) => m.DatabaseBrowser), { loading: opening });
const DreamsPanel = dynamic<PanelProps>(() => import("./DreamsPanel").then((m) => m.DreamsPanel), { loading: opening });
const HeartbeatPanel = dynamic<PanelProps>(() => import("./HeartbeatPanel").then((m) => m.HeartbeatPanel), { loading: opening });
const NotificationsPanel = dynamic<PanelProps>(() => import("./NotificationsPanel").then((m) => m.NotificationsPanel), { loading: opening });
const ProactivePanel = dynamic<PanelProps>(() => import("./ProactivePanel").then((m) => m.ProactivePanel), { loading: opening });
const GmailPanel = dynamic<PanelProps>(() => import("./GmailPanel").then((m) => m.GmailPanel), { loading: opening });
const AlexaPanel = dynamic<PanelProps>(() => import("./AlexaPanel").then((m) => m.AlexaPanel), { loading: opening });
const EventsBrowser = dynamic<PanelProps>(() => import("./EventsBrowser").then((m) => m.EventsBrowser), { loading: opening });
const OrchestratorPanel = dynamic<PanelProps>(() => import("./OrchestratorPanel").then((m) => m.OrchestratorPanel), { loading: opening });
const WishlistPanel = dynamic<PanelProps>(() => import("./WishlistPanel").then((m) => m.WishlistPanel), { loading: opening });
const UsagePanel = dynamic<PanelProps>(() => import("./UsagePanel").then((m) => m.UsagePanel), { loading: opening });
const LocalePanel = dynamic<PanelProps>(() => import("./LocalePanel").then((m) => m.LocalePanel), { loading: opening });
const HubPhotosPanel = dynamic<PanelProps>(() => import("./HubPhotosPanel").then((m) => m.HubPhotosPanel), { loading: opening });

const RENDER: Record<string, () => ReactNode> = {
  thoughts: () => <MindPanel isExpanded hideHeader />,
  journal: () => <JournalPanel isExpanded hideHeader />,
  model: () => <ModelPanel isExpanded hideHeader />,
  skills: () => <SkillsPanel isExpanded hideHeader />,
  mcp: () => <CustomMCPPanel isExpanded hideHeader />,
  memories: () => <MemoryBrowser isExpanded hideHeader />,
  documents: () => <DocumentBrowser isExpanded hideHeader />,
  files: () => <FileBrowser isExpanded hideHeader />,
  databases: () => <DatabaseBrowser isExpanded hideHeader />,
  dreaming: () => <DreamsPanel isExpanded hideHeader />,
  heartbeat: () => <HeartbeatPanel isExpanded hideHeader />,
  notifications: () => <NotificationsPanel isExpanded hideHeader />,
  proactive: () => <ProactivePanel isExpanded hideHeader />,
  gmail: () => <GmailPanel isExpanded hideHeader />,
  alexa: () => <AlexaPanel isExpanded hideHeader />,
  events: () => <EventsBrowser isExpanded hideHeader />,
  orchestrator: () => <OrchestratorPanel isExpanded hideHeader />,
  wishlist: () => <WishlistPanel isExpanded hideHeader />,
  usage: () => <UsagePanel isExpanded hideHeader />,
  locale: () => <LocalePanel isExpanded hideHeader />,
  "hub-photos": () => <HubPhotosPanel isExpanded hideHeader />,
};

export const SETTINGS_ITEMS: SettingsItem[] = SETTINGS_INDEX.map((i) => ({
  ...i,
  render: RENDER[i.slug] ?? (() => null),
}));

export function getSettingsItem(slug: string): SettingsItem | undefined {
  return SETTINGS_ITEMS.find((item) => item.slug === slug);
}

export const SETTINGS_SLUGS = SETTINGS_ITEMS.map((i) => i.slug);
