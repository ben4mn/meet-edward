// Settings → Memory → Files (2026-09-26). Pure.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { fileKind, fileKindLabel, fileMatches, fileMeta, formatFileSize, madeBy } from "../lib/storageView.ts";

const NOW = new Date("2026-09-26T12:00:00Z");

describe("formatFileSize", () => {
  it("reads like Finder", () => {
    assert.equal(formatFileSize(0), "0 B");
    assert.equal(formatFileSize(412), "412 B");
    assert.equal(formatFileSize(184220), "179.9 KB");
    assert.equal(formatFileSize(5 * 1024 * 1024), "5 MB");
  });
});

describe("fileKind", () => {
  it("comes from the mime type, then the name", () => {
    assert.equal(fileKind("image/jpeg", "a.jpg"), "image");
    assert.equal(fileKind("audio/mp4", "memo.m4a"), "audio");
    assert.equal(fileKind("application/pdf", "x.pdf"), "pdf");
    assert.equal(fileKind("text/csv", "b.csv"), "data");
    assert.equal(fileKind("text/markdown", "c.md"), "text");
    assert.equal(fileKind("application/octet-stream", "notes.txt"), "text");
    assert.equal(fileKind("application/zip", "a.zip"), "other");
  });

  it("labels it in a word", () => {
    assert.equal(fileKindLabel("text/csv", "b.csv"), "CSV");
    assert.equal(fileKindLabel("text/markdown", "c.md"), "Markdown");
    assert.equal(fileKindLabel("image/png", "p.png"), "PNG image");
    assert.equal(fileKindLabel("application/pdf", "x.pdf"), "PDF");
    assert.equal(fileKindLabel("audio/mp4", "m.m4a"), "Audio");
  });
});

describe("filters", () => {
  const f = (mime: string, filename = "x") => ({ mime_type: mime, filename });
  it("matches images, documents and audio", () => {
    assert.equal(fileMatches(f("image/png"), "images"), true);
    assert.equal(fileMatches(f("image/png"), "docs"), false);
    assert.equal(fileMatches(f("application/pdf"), "docs"), true);
    assert.equal(fileMatches(f("text/csv"), "docs"), true);
    assert.equal(fileMatches(f("audio/mp4"), "audio"), true);
    assert.equal(fileMatches(f("application/zip"), "all"), true);
  });

  it("says who made it", () => {
    assert.equal(madeBy("user"), "you");
    assert.equal(madeBy("upload"), "you");
    assert.equal(madeBy("sandbox"), "edward");
    assert.equal(madeBy("worker"), "edward");
  });
});

describe("fileMeta", () => {
  it("is size, kind, who and when", () => {
    assert.equal(
      fileMeta({ size_bytes: 412, mime_type: "text/csv", filename: "b.csv", source: "sandbox", created_at: "2026-09-26T11:34:00Z" }, { now: NOW }),
      "412 B · CSV · made by Edward · 26m ago",
    );
    assert.equal(
      fileMeta({ size_bytes: 48213, mime_type: "audio/mp4", filename: "m.m4a", source: "upload", created_at: null }, { now: NOW }),
      "47.1 KB · Audio · from you",
    );
  });
});
