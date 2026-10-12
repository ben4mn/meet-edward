// Settings → Memory → Files and Databases; House → Locale and Hub photos
// (2026-09-26, Jarvis polish): tidied onto the design system.
//
// - Files: a kind filter, whole rows, ?id= opens the file.
// - Databases: expanders for tables and columns; deleting asks once.
// - Locale: a 16px field, save only when changed.
// - Hub photos: delete is undoable (the photo leaves the grid at once).

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { mockFetch, restoreFetch, type Call } from "./panelTestKit.ts";
import { FileBrowser } from "../components/settings/FileBrowser.tsx";
import { DatabaseBrowser } from "../components/settings/DatabaseBrowser.tsx";
import { LocalePanel } from "../components/settings/LocalePanel.tsx";
import { HubPhotosPanel } from "../components/settings/HubPhotosPanel.tsx";
import { pendingDeletes } from "../lib/pendingDeletes.ts";

const iso = (msAgo: number) => new Date(Date.now() - msAgo).toISOString();
const file = (id: string, filename: string, mime: string, size: number, over: Record<string, unknown> = {}) => ({
  id, filename, mime_type: mime, size_bytes: size, category: "document", description: null, tags: null, source: "sandbox",
  source_conversation_id: null, created_at: iso(3600_000), updated_at: iso(3600_000), last_accessed: null, access_count: 0,
  download_url: `/api/files/${id}/download`, ...over,
});
const FILES = [
  file("f-csv", "cabin-budget.csv", "text/csv", 412, { description: "Cabin weekend budget split", source_conversation_id: "c-cabin" }),
  file("f-jpg", "biscuit-labs.jpg", "image/jpeg", 184220, { source: "upload" }),
  file("f-m4a", "voice-memo.m4a", "audio/mp4", 48213, { source: "upload" }),
];

let calls: Call[] = [];

beforeEach(() => {
  calls = mockFetch((url, method) => {
    const fm = url.match(/\/api\/files\/([^/?]+)$/);
    if (fm) return FILES.find((f) => f.id === fm[1]);
    if (url.includes("/api/files")) return { files: FILES, pagination: { limit: 50, offset: 0, total: FILES.length } };
    if (url.match(/\/api\/databases\/runs\/tables\/runs\/columns$/)) return [
      { name: "id", data_type: "integer", is_nullable: "NO", column_default: null, ordinal_position: 1 },
      { name: "miles", data_type: "numeric", is_nullable: "NO", column_default: null, ordinal_position: 2 },
    ];
    if (url.match(/\/api\/databases\/runs\/tables$/)) return [{ name: "runs", column_count: 2 }];
    if (url.match(/\/api\/databases\/runs$/) && method === "DELETE") return { status: "deleted", name: "runs" };
    if (url.endsWith("/api/databases")) return [
      { id: "db-runs", name: "runs", schema_name: "edward_db_runs", description: "Ellis's training runs", created_at: iso(86400_000 * 20), updated_at: null, last_accessed: null },
    ];
    if (url.includes("/api/settings") && method === "POST") return { timezone: "America/New_York" };
    if (url.includes("/api/settings")) return { timezone: "America/Chicago" };
    if (url.includes("/api/hub/photos") && method === "DELETE") return { ok: true };
    if (url.includes("/api/hub/photos")) return { photos: ["cabin.jpg", "dogs.jpg"], skipped_unsupported: 1, dir: "~/edward-photos", dir_present: true };
    return undefined;
  });
});

afterEach(() => {
  cleanup();
  restoreFetch();
  pendingDeletes.flush();
});

describe("FileBrowser", () => {
  it("filters by kind and says who made each file", async () => {
    render(<FileBrowser />);
    const row = (await screen.findByText("cabin-budget.csv")).closest("button") as HTMLElement;
    assert.match(row.textContent ?? "", /412 B · CSV · made by Edward/);
    fireEvent.click(screen.getByRole("radio", { name: "Images" }));
    await waitFor(() => assert.equal(screen.queryByText("cabin-budget.csv") === null, true));
    assert.ok(screen.getByText("biscuit-labs.jpg"));
  });

  it("opens ?id= with a download and the conversation it came from", async () => {
    render(<FileBrowser initialId="f-csv" />);
    const dialog = await screen.findByRole("dialog", { name: "cabin-budget.csv" });
    assert.equal(within(dialog).getByRole("link", { name: /Download/ }).getAttribute("href"), "/api/files/f-csv/download");
    assert.ok(within(dialog).getByRole("link", { name: /Open the conversation/ }).getAttribute("href")?.includes("c-cabin"));
  });
});

describe("DatabaseBrowser", () => {
  it("expands a database to its tables and a table to its columns", async () => {
    render(<DatabaseBrowser />);
    fireEvent.click(await screen.findByRole("button", { name: /runs.*Ellis's training runs/s }));
    const table = await screen.findByRole("button", { name: /runs.*2 columns/s });
    fireEvent.click(table);
    assert.ok(await screen.findByText("miles"));
    assert.ok(screen.getByText("numeric"));
  });

  it("asks once before deleting a database", async () => {
    render(<DatabaseBrowser />);
    fireEvent.click(await screen.findByRole("button", { name: "Delete runs" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete “runs”?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => assert.ok(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/api/databases/runs"))));
  });
});

describe("LocalePanel", () => {
  it("edits the zone in a 16px field and saves only a change", async () => {
    render(<LocalePanel />);
    const input = await screen.findByLabelText("Time zone");
    assert.match(input.className, /text-\[16px\]/);
    const save = screen.getByRole("button", { name: "Save" });
    assert.equal((save as HTMLButtonElement).disabled, true);
    fireEvent.change(input, { target: { value: "America/New_York" } });
    assert.equal((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled, false);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => assert.ok(calls.some((c) => c.method === "POST" && c.url.includes("/api/settings"))));
  });
});

describe("HubPhotosPanel", () => {
  it("takes a deleted photo out at once and waits out the undo window", async () => {
    render(<HubPhotosPanel />);
    await screen.findByRole("button", { name: "Delete dogs.jpg" });
    fireEvent.click(screen.getByRole("button", { name: "Delete dogs.jpg" }));
    await waitFor(() => assert.equal(screen.queryByRole("button", { name: "Delete dogs.jpg" }) === null, true));
    assert.equal(calls.some((c) => c.method === "DELETE"), false, "nothing is deleted before the undo window");
    pendingDeletes.flush();
    await waitFor(() => assert.ok(calls.some((c) => c.method === "DELETE" && c.url.includes("dogs.jpg"))));
  });
});
