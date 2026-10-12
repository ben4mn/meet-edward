// The model catalog as the Model and Orchestrator panels read it
// (2026-09-26). GET /api/settings/models is the live Codex catalog; the
// panels offer exactly what it lists, never a dead provider button, and
// never a Claude label for a model Edward no longer runs. A stored value
// the catalog does not list is still shown — as itself, marked current —
// so a select never pretends the first option is what is saved.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { modelOptions, parseCatalog, whoThinksWhat } from "../lib/modelCatalog.ts";

const RAW = {
  providers: {
    openai: {
      available: true,
      models: [
        { id: "gpt-6-astra", name: "GPT-6-Astra (Recommended)", description: "Frontier intelligence.", default_effort: "medium", supports_images: true },
        { id: "gpt-5.6-terra", name: "GPT-5.6-Terra", description: "Balanced.", default_effort: "medium" },
        { id: "gpt-5.6-luna", name: "GPT-5.6-Luna", description: "Fast.", default_effort: "low" },
      ],
    },
    anthropic: { available: false, models: [] },
  },
  models: [],
  catalog: { live: true, recommended: "gpt-6-astra" },
  background_model: "gpt-5.6-terra",
  voice_model: "gpt-5.6-luna",
};

describe("parseCatalog", () => {
  it("keeps the available providers' models, with Recommended as a flag", () => {
    const c = parseCatalog(RAW);
    assert.deepEqual(c.models.map((m) => m.id), ["gpt-6-astra", "gpt-5.6-terra", "gpt-5.6-luna"]);
    assert.equal(c.models[0].name, "GPT-6-Astra");
    assert.equal(c.models[0].recommended, true);
    assert.equal(c.models[1].recommended, false);
    assert.equal(c.models[2].effort, "low");
    assert.equal(c.provider, "openai");
    assert.equal(c.available, true);
  });

  it("never offers an unavailable provider", () => {
    const c = parseCatalog({ ...RAW, providers: { ...RAW.providers, anthropic: { available: false, models: [{ id: "claude-x", name: "Claude" }] } } });
    assert.ok(!c.models.some((m) => m.id === "claude-x"));
  });

  it("says when nothing is available", () => {
    const c = parseCatalog({ providers: { openai: { available: false, models: RAW.providers.openai.models } } });
    assert.equal(c.available, false);
    assert.equal(c.models.length, 3, "the catalog is still listed so the saved model reads right");
  });

  it("copes with junk", () => {
    const c = parseCatalog(null);
    assert.deepEqual(c.models, []);
    assert.equal(c.available, false);
  });
});

describe("modelOptions", () => {
  const c = parseCatalog(RAW);
  it("lists the catalog by name", () => {
    assert.deepEqual(modelOptions(c, "gpt-5.6-terra").map((o) => [o.value, o.label]), [
      ["gpt-6-astra", "GPT-6-Astra (recommended)"],
      ["gpt-5.6-terra", "GPT-5.6-Terra"],
      ["gpt-5.6-luna", "GPT-5.6-Luna"],
    ]);
  });
  it("shows a stored value the catalog does not list, first, as current", () => {
    const opts = modelOptions(c, "claude-haiku-4-5-20251001");
    assert.deepEqual(opts[0], { value: "claude-haiku-4-5-20251001", label: "claude-haiku-4-5-20251001 (current)" });
    assert.equal(opts.length, 4);
  });
});

describe("whoThinksWhat", () => {
  it("names each role's model from what the server says, and nothing it does not", () => {
    const rows = whoThinksWhat(parseCatalog(RAW), "gpt-6-astra");
    assert.deepEqual(rows, [
      { role: "Chat", model: "GPT-6-Astra", effort: null },
      { role: "Voice", model: "GPT-5.6-Luna", effort: null },
      { role: "Background", model: "GPT-5.6-Terra", effort: null },
    ]);
  });
  it("names the model chat really runs on, and where a wake thinks harder or lighter", () => {
    const rows = whoThinksWhat(
      parseCatalog({
        ...RAW,
        chat_model: "gpt-5.6-terra",
        chat_effort: "low",
        mind_model: "gpt-6-astra",
        mind_effort: "high",
        mind_efforts: { morning: "high", mention: "medium", reflect: "xhigh", messages: "medium" },
      }),
      "gpt-5.4-retired",
    );
    assert.deepEqual(rows[0], { role: "Chat", model: "GPT-5.6-Terra", effort: "low" });
    assert.equal(rows[1].role, "Mind");
    assert.equal(rows[1].detail, "Answering a mention and reading your messages at medium · reflecting on the week at xhigh");
  });
  it("adds the mind and the pulse, with efforts, when the server reports them", () => {
    const rows = whoThinksWhat(
      parseCatalog({ ...RAW, mind_model: "gpt-6-astra", mind_effort: "high", pulse_model: "gpt-5.6-terra", pulse_effort: "low", chat_effort: "low" }),
      "gpt-6-astra",
    );
    assert.deepEqual(rows, [
      { role: "Chat", model: "GPT-6-Astra", effort: "low" },
      { role: "Mind", model: "GPT-6-Astra", effort: "high" },
      { role: "Surfaces", model: "GPT-5.6-Terra", effort: "low" },
      { role: "Voice", model: "GPT-5.6-Luna", effort: null },
      { role: "Background", model: "GPT-5.6-Terra", effort: null },
    ]);
  });
  it("shows an id the catalog does not know as itself", () => {
    const rows = whoThinksWhat(parseCatalog({ ...RAW, voice_model: "gpt-9" }), "gpt-6-astra");
    assert.equal(rows.find((r) => r.role === "Voice")?.model, "gpt-9");
  });
});
