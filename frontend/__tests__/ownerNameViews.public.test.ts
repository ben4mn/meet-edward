// The views name the owner from lib/owner.ts (GET /api/auth/status's
// owner_name), never a literal: a reminder Edward wrote for the owner reads
// as one, the owner's own name is never mistaken for someone a need named,
// and the screen's private "speak" line matches the backend's exactly.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";

import { setOwnerName } from "../lib/owner.ts";
import { didTitle, needTitle, type TodayDid, type TodayNeed } from "../components/today/todayModel.ts";
import { greetingWithout } from "../components/today/heroModel.ts";
import { SPEAK_LINE } from "../app/(display)/display/lib/thinkingModel.ts";

const ev = (title: string): TodayNeed => ({
  id: "event:7",
  kind: "event",
  tone: "info",
  priority: 0.5,
  title,
  detail: "",
  href: "/settings/events",
  actions: [],
});

const did = (title: string): TodayDid => ({
  id: "x",
  kind: "event_fired",
  title,
  detail: "",
  at: "2026-09-26T22:00:00Z",
  href: "/x",
});

beforeEach(() => setOwnerName(""));
afterEach(() => setOwnerName(""));

describe("a reminder for the owner", () => {
  it("reads as a reminder when it names the owner", () => {
    setOwnerName("Sam");
    assert.equal(needTitle(ev("Remind Sam to call the vet")), "Reminder: call the vet");
    assert.equal(needTitle(ev("remind sam about the vet")), "Reminder: the vet");
    assert.equal(didTitle(did("Remind Sam to water the plants")), "Reminded you to water the plants");
    assert.equal(didTitle(did("Remind Sam about the vet")), "Reminded you about the vet");
  });

  it('"Remind me …" is the owner too, with or without a name set', () => {
    assert.equal(needTitle(ev("Remind me to call the vet")), "Reminder: call the vet");
    assert.equal(didTitle(did("Remind me about the vet")), "Reminded you about the vet");
    setOwnerName("Sam");
    assert.equal(needTitle(ev("Remind me to call the vet")), "Reminder: call the vet");
  });

  it("someone else's reminder keeps its own title", () => {
    setOwnerName("Sam");
    assert.equal(needTitle(ev("Remind Jordan to call the vet")), "Remind Jordan to call the vet");
    // A task he ran still reads as done: "Remind" has no past tense of its own here.
    assert.equal(didTitle(did("Remind Jordan to call the vet")), "Remind Jordan to call the vet");
  });

  it("a name with regex characters in it is matched literally", () => {
    setOwnerName("A.J.");
    assert.equal(needTitle(ev("Remind A.J. to call the vet")), "Reminder: call the vet");
    assert.equal(needTitle(ev("Remind AxJx to call the vet")), "Remind AxJx to call the vet");
  });
});

describe("greetingWithout and the owner's own name", () => {
  const remind = { kind: "event", title: "Remind Sam to call Jordan", detail: "" };
  const line = "Evening. Jordan wants an answer, and Sam's day is clear.";

  it("never treats the owner's name as something a resolved need named", () => {
    setOwnerName("Sam");
    // Only Jordan is gone; the clause about Sam stays.
    assert.equal(greetingWithout(line, [remind], []), "Evening. Sam's day is clear.");
  });

  it("with no name set, the name is just another word", () => {
    assert.equal(greetingWithout(line, [remind], []), "Evening.");
  });

  it("a short salutation naming the owner is kept whole", () => {
    setOwnerName("Sam");
    const hello = "Good evening there, Sam. Jordan wants an answer.";
    assert.equal(greetingWithout(hello, [{ kind: "approval", title: "Send this to Jordan?", detail: "" }], []), "Good evening there, Sam.");
  });
});

describe("the screen's speak line", () => {
  it("is the backend's wake_state.SPEAK_LINE, word for word", () => {
    assert.equal(SPEAK_LINE, "Something came up — I left a note.");
  });
});
