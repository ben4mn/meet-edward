// R/G red for the hub's device-controls screen.
//
// The interesting logic here isn't the actions — it's the two-tap confirm
// in front of the ones that take the panel out of the hub. A wall screen
// is used by whoever walks past it, which makes an armed confirm a booby
// trap: arm "Exit hub", get distracted, and three hours later someone
// else's single innocent tap drops the household's pet screen into the
// chat UI. So arming has to go stale on its own.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  CONFIRM_TIMEOUT_MS,
  DEVICE_ACTIONS,
  NOTHING_ARMED,
  expire,
  isArmed,
  tap,
} from "../app/(display)/hub/lib/deviceActions.ts";

const T0 = 1_000_000;

describe("tap on an action that needs no confirmation", () => {
  it("fires on the first tap", () => {
    const { fire } = tap(NOTHING_ARMED, "reload", false, T0);
    assert.equal(fire, true);
  });

  it("leaves nothing armed behind it", () => {
    const { state } = tap(NOTHING_ARMED, "reload", false, T0);
    assert.deepEqual(state, NOTHING_ARMED);
  });

  it("disarms whatever else was armed", () => {
    // Half-pressed "Exit hub", then thought better of it and tapped Sleep.
    // The exit must not stay armed underneath.
    const armed = tap(NOTHING_ARMED, "leave", true, T0).state;
    const after = tap(armed, "sleep", false, T0 + 500).state;
    assert.equal(after.armed, null);
  });
});

describe("tap on an action that needs confirmation", () => {
  it("does not fire on the first tap", () => {
    const { fire } = tap(NOTHING_ARMED, "leave", true, T0);
    assert.equal(fire, false);
  });

  it("arms itself instead", () => {
    const { state } = tap(NOTHING_ARMED, "leave", true, T0);
    assert.ok(isArmed(state, "leave", T0));
  });

  it("fires on the second tap", () => {
    const armed = tap(NOTHING_ARMED, "leave", true, T0).state;
    const { fire } = tap(armed, "leave", true, T0 + 900);
    assert.equal(fire, true);
  });

  it("disarms once it has fired, so a third tap starts over", () => {
    const armed = tap(NOTHING_ARMED, "leave", true, T0).state;
    const fired = tap(armed, "leave", true, T0 + 900);
    assert.deepEqual(fired.state, NOTHING_ARMED);
    assert.equal(tap(fired.state, "leave", true, T0 + 1000).fire, false);
  });

  it("arming a different action drops the first one", () => {
    const armed = tap(NOTHING_ARMED, "leave", true, T0).state;
    const other = tap(armed, "wipe", true, T0 + 100).state;
    assert.equal(isArmed(other, "leave", T0 + 100), false);
    assert.ok(isArmed(other, "wipe", T0 + 100));
  });
});

describe("an armed confirm goes stale", () => {
  it("stops counting as armed after the timeout", () => {
    const { state } = tap(NOTHING_ARMED, "leave", true, T0);
    assert.ok(isArmed(state, "leave", T0 + CONFIRM_TIMEOUT_MS - 1));
    assert.equal(isArmed(state, "leave", T0 + CONFIRM_TIMEOUT_MS), false);
  });

  it("re-arms rather than firing when the next person taps hours later", () => {
    // The whole point: a stale confirm must not be completable by someone
    // who never saw the first tap.
    const armed = tap(NOTHING_ARMED, "leave", true, T0).state;
    const later = tap(armed, "leave", true, T0 + 3 * 60 * 60 * 1000);
    assert.equal(later.fire, false);
    assert.ok(isArmed(later.state, "leave", T0 + 3 * 60 * 60 * 1000));
  });

  it("never reports an untouched state as armed", () => {
    assert.equal(isArmed(NOTHING_ARMED, "leave", T0), false);
  });
});

describe("the action list itself", () => {
  it("has a stable id for every action", () => {
    const ids = DEVICE_ACTIONS.map((a) => a.id);
    assert.equal(new Set(ids).size, ids.length);
  });

  it("only asks for confirmation on the ones that leave the hub", () => {
    // Sleeping and reloading are one tap away from being undone. Making
    // them two-tap on a wall panel is friction with nothing behind it.
    for (const action of DEVICE_ACTIONS) {
      if (action.confirm) {
        assert.equal(action.kind, "leave", `${action.id} should not need a confirm`);
      }
    }
  });

  it("gives every confirming action the wording for its second tap", () => {
    for (const action of DEVICE_ACTIONS) {
      if (action.confirm) assert.ok(action.confirmLabel, `${action.id} has no confirm label`);
    }
  });

  it("labels and hints every action, since the panel is used by whoever walks past", () => {
    for (const action of DEVICE_ACTIONS) {
      assert.ok(action.label, `${action.id} has no label`);
      assert.ok(action.hint, `${action.id} has no hint`);
    }
  });
});

describe("expire", () => {
  // Found while watching it on the panel: nothing was clearing the armed
  // state once it went stale — `isArmed` just started answering false. The
  // record stayed set, so the timer driving the countdown never stopped and
  // the kiosk re-rendered twice a second for the rest of its life.

  it("leaves a live confirm alone", () => {
    const armed = tap(NOTHING_ARMED, "leave", true, T0).state;
    assert.equal(expire(armed, T0 + 1000), armed);
  });

  it("clears one that has gone stale", () => {
    const armed = tap(NOTHING_ARMED, "leave", true, T0).state;
    assert.deepEqual(expire(armed, T0 + CONFIRM_TIMEOUT_MS), NOTHING_ARMED);
  });

  it("returns the same object when there is nothing to do", () => {
    // Identity, not equality: a fresh object every tick would keep React
    // re-rendering, which is the thing this exists to stop.
    const armed = tap(NOTHING_ARMED, "leave", true, T0).state;
    assert.equal(expire(armed, T0 + 10), armed);
    assert.equal(expire(NOTHING_ARMED, T0), NOTHING_ARMED);
  });
});
