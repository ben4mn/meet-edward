// R/G red for lib/presenceHero — the scroll-aware hero and the presence sheet.
//
// Design system §1/§4: Edward is the status bar, but a page with a hero
// Edward (Today's presence hero, chat's empty state, the Edward tab hero,
// the voice view) already has him front and centre. While that hero is on
// screen the bar hides its own avatar and status and the hero is the one
// who breathes; when the hero scrolls out, the bar shows him again. The
// presence sheet, when open, has its own live Edward, so neither the hero
// nor the bar is live meanwhile.
//
// The voice view uses the simpler registration: useRegisterPresenceHero(on).

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React, { useRef } from "react";
import { act, cleanup, render, screen, fireEvent } from "@testing-library/react";

import {
  PresenceHeroProvider,
  usePresenceHero,
  useHeroOnScreen,
  usePresenceSheet,
  heroVisibleFrom,
  nextHeroes,
  anyHeroOnScreen,
  heroStore,
  useRegisterPresenceHero,
  HERO_MIN_RATIO,
} from "../lib/presenceHero.tsx";

// ---- a controllable IntersectionObserver ----------------------------------

type Cb = (entries: Array<{ target: Element; isIntersecting: boolean; intersectionRatio: number }>) => void;
const observers: Array<{ cb: Cb; targets: Element[]; options: unknown; disconnected: boolean }> = [];
const originalIO = (globalThis as { IntersectionObserver?: unknown }).IntersectionObserver;

class FakeIO {
  private rec: { cb: Cb; targets: Element[]; options: unknown; disconnected: boolean };
  constructor(cb: Cb, options?: unknown) {
    this.rec = { cb, targets: [], options, disconnected: false };
    observers.push(this.rec);
  }
  observe(el: Element) {
    this.rec.targets.push(el);
  }
  unobserve() {}
  disconnect() {
    this.rec.disconnected = true;
  }
  takeRecords() {
    return [];
  }
}

function fire(isIntersecting: boolean, ratio = isIntersecting ? 1 : 0) {
  act(() => {
    for (const o of observers) {
      if (o.disconnected) continue;
      o.cb(o.targets.map((t) => ({ target: t, isIntersecting, intersectionRatio: ratio })));
    }
  });
}

function Hero({ id = "hero", enabled }: { id?: string; enabled?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const live = usePresenceHero(ref, { enabled });
  return <div ref={ref} data-testid={id} data-live={String(live)} />;
}

function Bar() {
  const heroOn = useHeroOnScreen();
  const sheet = usePresenceSheet();
  return (
    <>
      <span data-testid="bar" data-hero={String(heroOn)} data-sheet={String(sheet.isOpen)} />
      <button type="button" onClick={sheet.open}>open sheet</button>
      <button type="button" onClick={sheet.close}>close sheet</button>
    </>
  );
}

const attr = (id: string, name: string) => screen.getByTestId(id).getAttribute(name);

describe("presenceHero pure helpers", () => {
  it("counts a hero as on screen only when enough of him shows", () => {
    assert.equal(heroVisibleFrom({ isIntersecting: true, intersectionRatio: 1 }), true);
    assert.equal(heroVisibleFrom({ isIntersecting: true, intersectionRatio: HERO_MIN_RATIO }), true);
    assert.equal(heroVisibleFrom({ isIntersecting: true, intersectionRatio: HERO_MIN_RATIO / 2 }), false);
    assert.equal(heroVisibleFrom({ isIntersecting: false, intersectionRatio: 0 }), false);
  });

  it("tracks several heroes: on screen while any one is", () => {
    let m = nextHeroes(new Map(), "a", true);
    m = nextHeroes(m, "b", false);
    assert.equal(anyHeroOnScreen(m), true);
    m = nextHeroes(m, "a", false);
    assert.equal(anyHeroOnScreen(m), false);
    m = nextHeroes(m, "b", true);
    assert.equal(anyHeroOnScreen(m), true);
    m = nextHeroes(m, "b", null); // unmounted
    assert.equal(anyHeroOnScreen(m), false);
    assert.equal(m.has("b"), false);
  });

  it("returns the same map when nothing changed (no re-render)", () => {
    const m = nextHeroes(new Map(), "a", true);
    assert.equal(nextHeroes(m, "a", true), m);
    assert.equal(nextHeroes(m, "zz", null), m);
  });
});

describe("usePresenceHero(ref)", () => {
  beforeEach(() => {
    observers.length = 0;
    (globalThis as { IntersectionObserver?: unknown }).IntersectionObserver = FakeIO;
  });
  afterEach(() => {
    cleanup();
    act(() => heroStore.closeSheet());
    (globalThis as { IntersectionObserver?: unknown }).IntersectionObserver = originalIO;
  });

  it("a mounted hero is live and hides the bar's Edward until it scrolls out", () => {
    render(
      <>
        <Bar />
        <Hero />
      </>,
    );
    assert.equal(attr("hero", "data-live"), "true");
    assert.equal(attr("bar", "data-hero"), "true");

    fire(false);
    assert.equal(attr("hero", "data-live"), "false", "scrolled out: the hero stops breathing");
    assert.equal(attr("bar", "data-hero"), "false", "scrolled out: the bar shows him");

    fire(true, 0.9);
    assert.equal(attr("hero", "data-live"), "true");
    assert.equal(attr("bar", "data-hero"), "true");
  });

  it("observes below the bar so a hero hidden behind it counts as gone", () => {
    render(<Hero />);
    const opts = observers[0]?.options as { rootMargin?: string } | undefined;
    assert.match(String(opts?.rootMargin ?? ""), /^-\d+px/);
  });

  it("the bar shows him again when the hero unmounts or is disabled", () => {
    const { rerender } = render(
      <>
        <Bar />
        <Hero />
      </>,
    );
    assert.equal(attr("bar", "data-hero"), "true");
    rerender(
      <>
        <Bar />
        <Hero enabled={false} />
      </>,
    );
    assert.equal(attr("bar", "data-hero"), "false");
    assert.equal(attr("hero", "data-live"), "false");
    rerender(<Bar />);
    assert.equal(attr("bar", "data-hero"), "false");
  });

  it("while the presence sheet is open neither the hero nor the bar is live", () => {
    render(
      <>
        <Bar />
        <Hero />
      </>,
    );
    fireEvent.click(screen.getByText("open sheet"));
    assert.equal(attr("bar", "data-sheet"), "true");
    assert.equal(attr("hero", "data-live"), "false");
    fireEvent.click(screen.getByText("close sheet"));
    assert.equal(attr("hero", "data-live"), "true");
  });

  it("without IntersectionObserver a mounted hero is simply on screen", () => {
    (globalThis as { IntersectionObserver?: unknown }).IntersectionObserver = undefined;
    render(
      <>
        <Bar />
        <Hero />
      </>,
    );
    assert.equal(attr("hero", "data-live"), "true");
    assert.equal(attr("bar", "data-hero"), "true");
  });

  it("PresenceHeroProvider is optional and just renders its children", () => {
    render(
      <PresenceHeroProvider>
        <Hero />
      </PresenceHeroProvider>,
    );
    assert.equal(attr("hero", "data-live"), "true");
  });
});

describe("useRegisterPresenceHero (a hero that covers the screen: the voice view)", () => {
  afterEach(cleanup);

  function Cover({ on = true }: { on?: boolean }) {
    useRegisterPresenceHero(on);
    return null;
  }
  function Reader({ out }: { out: { v: boolean } }) {
    out.v = usePresenceHero();
    return null;
  }

  it("is off with no hero, on while one is up, off after it leaves", () => {
    const out = { v: true };
    render(<Reader out={out} />);
    assert.equal(out.v, false);
    const hero = render(<Cover />);
    assert.equal(out.v, true);
    act(() => hero.unmount());
    assert.equal(out.v, false);
  });

  it("counts heroes, and an inactive registration does not count", () => {
    const a = render(<Cover />);
    const b = render(<Cover on={false} />);
    assert.equal(heroStore.active(), true);
    act(() => a.unmount());
    assert.equal(heroStore.active(), false);
    b.rerender(<Cover on />);
    assert.equal(heroStore.active(), true);
    b.rerender(<Cover on={false} />);
    assert.equal(heroStore.active(), false);
  });
});
