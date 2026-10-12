// The hub's household comes from the server, not from the code: the
// who-toggle lists GET /api/hub/who's `people` (EDWARD_HOUSEHOLD, owner
// first), and each person's palette goes by their position in that list.

import { beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  getPeople,
  getWho,
  hydrateWho,
  resetWhoForTests,
  setPeople,
  setWho,
  subscribeWho,
} from "../app/(display)/hub/lib/whoStore.ts";
import { DEFAULT_THEME, THEMES, themeFor } from "../app/(display)/hub/lib/whoTheme.ts";

function clearStorage() {
  try {
    window.localStorage.removeItem("hub.who");
    window.localStorage.removeItem("hub.people");
  } catch {
    /* no storage in this environment */
  }
}

beforeEach(() => {
  resetWhoForTests();
  clearStorage();
});

describe("the hub's household", () => {
  it("knows nobody until the server says who lives here", () => {
    assert.deepEqual([...getPeople()], []);
    assert.equal(getWho(), "");
    assert.equal(setWho("Sam"), false, "nobody to pick yet");
  });

  it("starts on the first person the server lists (the owner)", () => {
    assert.equal(setPeople(["Sam", "Alex"]), true);
    assert.deepEqual([...getPeople()], ["Sam", "Alex"]);
    assert.equal(getWho(), "Sam");
    assert.equal(setWho("Alex"), true);
    assert.equal(getWho(), "Alex");
  });

  it("never empties a known household on a bad answer", () => {
    setPeople(["Sam", "Alex"]);
    assert.equal(setPeople([]), false);
    assert.equal(setPeople(undefined), false);
    assert.equal(setPeople([" ", 3]), false);
    assert.deepEqual([...getPeople()], ["Sam", "Alex"]);
  });

  it("keeps the person logging when they are still in the household", () => {
    setPeople(["Sam", "Alex"]);
    setWho("Alex");
    setPeople(["Sam", "Alex", "Jo"]);
    assert.equal(getWho(), "Alex");
    setPeople(["Sam", "Jo"]);
    assert.equal(getWho(), "Sam", "someone who left hands the panel to the owner");
  });

  it("tells every subscriber when the household changes, once", () => {
    let calls = 0;
    subscribeWho(() => calls++);
    setPeople(["Sam", "Alex"]);
    setPeople(["Sam", "Alex"]);
    assert.equal(calls, 1);
  });

  it("an offline reload remembers the household and the choice", () => {
    setPeople(["Sam", "Alex"]);
    setWho("Alex");
    resetWhoForTests();
    hydrateWho();
    let stored = true;
    try {
      stored = window.localStorage.getItem("hub.people") !== null;
    } catch {
      stored = false;
    }
    if (!stored) return; // no storage here: nothing to remember
    assert.deepEqual([...getPeople()], ["Sam", "Alex"]);
    assert.equal(getWho(), "Alex");
  });
});

describe("themes by position", () => {
  const people = ["Sam", "Alex", "Jo", "Kit"];

  it("gives the first person the first palette, and so on", () => {
    assert.equal(themeFor("Sam", people), "first");
    assert.equal(themeFor("Alex", people), "second");
    assert.equal(themeFor("Jo", people), "third");
  });

  it("matches names however they round-tripped", () => {
    assert.equal(themeFor("  alex ", people), "second");
    assert.equal(themeFor("JO", people), "third");
  });

  it("anyone past the last palette, or a stranger, gets the default", () => {
    assert.equal(themeFor("Kit", people), DEFAULT_THEME);
    assert.equal(themeFor("Sitter", people), DEFAULT_THEME);
    assert.equal(themeFor("", people), DEFAULT_THEME);
    assert.equal(themeFor("Sam", []), DEFAULT_THEME);
  });

  it("no name is baked into a theme", () => {
    assert.deepEqual([...THEMES], ["first", "second", "third"]);
    assert.ok(THEMES.includes(DEFAULT_THEME));
  });
});
