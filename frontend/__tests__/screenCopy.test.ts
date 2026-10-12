// Words on Edward's screen (2026-09-26).
//
// - An alert's time is "42m ago", never a raw ISO string (the fixture and any
//   future backend change leaked "2026-09-26T23:08:27.312645Z" onto the wall).
// - The header says what he watches in plain words and counts workers
//   properly ("1 WORKERS" was on screen).

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { alertTimeLabel, watchingLabel, workersLabel } from "../app/(display)/display/lib/screenCopy.ts";

const NOW = new Date("2026-09-26T23:50:00Z");

describe("alertTimeLabel", () => {
  it("turns an ISO time into a relative one", () => {
    assert.equal(alertTimeLabel("2026-09-26T23:08:27.312645Z", NOW), "42m ago");
  });

  it("keeps what the backend already phrased", () => {
    assert.equal(alertTimeLabel("now", NOW), "now");
    assert.equal(alertTimeLabel("12m ago", NOW), "12m ago");
  });

  it("empty stays empty", () => {
    assert.equal(alertTimeLabel("", NOW), "");
  });
});

describe("workersLabel", () => {
  it("counts properly", () => {
    assert.equal(workersLabel(0), "no workers");
    assert.equal(workersLabel(1), "1 worker");
    assert.equal(workersLabel(3), "3 workers");
    assert.equal(workersLabel(null), "—");
  });
});

describe("watchingLabel", () => {
  it("names what he watches", () => {
    assert.equal(
      watchingLabel({ imessage: { enabled: true }, calendar: { enabled: true }, email: { enabled: false } }),
      "watching messages · calendar",
    );
  });

  it("says so when he watches nothing", () => {
    assert.equal(watchingLabel({ imessage: { enabled: false } }), "not watching");
  });

  it("unknown before the first answer", () => {
    assert.equal(watchingLabel(null), "—");
  });

  it("an unfamiliar track keeps its own name", () => {
    assert.equal(watchingLabel({ slack: { enabled: true } }), "watching slack");
  });
});
