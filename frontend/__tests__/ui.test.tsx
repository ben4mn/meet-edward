// R/G red for the shared primitives every panel composes from.
//
// Twenty settings panels each hand-roll their own rows, chips and empty
// states. The primitives are deliberately tiny; what matters is that a
// status reads the same everywhere, so the tone -> class mapping is what
// this test pins.
//
// DOM nodes are compared with `assert.ok(a === b)`, never `assert.equal`:
// a failing equal makes the runner inspect the jsdom node (the whole window)
// and the process dies of memory instead of reporting the failure.

import { describe, it, afterEach, mock } from "node:test";
import assert from "node:assert/strict";
import React, { useState } from "react";
import { render, screen, fireEvent, act, cleanup } from "@testing-library/react";

import {
  Pill,
  Segment,
  Row,
  SectionLabel,
  EmptyState,
  Card,
  Button,
  buttonClasses,
  Sheet,
  ConfirmSheet,
  Switch,
  Field,
  Input,
  Select,
  Textarea,
  Notice,
  Meter,
  Skeleton,
  ToastProvider,
  useToast,
  PanelShell,
  Pulse,
  EdwardPulse,
  cn,
  shouldDismissDrag,
  MAX_TOASTS,
} from "../components/ui/index.ts";

afterEach(() => {
  cleanup();
  mock.timers.reset();
});

describe("Pill", () => {
  it("maps tone to the status token classes", () => {
    const { container } = render(
      <>
        <Pill tone="ok">given</Pill>
        <Pill tone="warn">overdue</Pill>
        <Pill tone="bad">failed</Pill>
        <Pill tone="info">noted</Pill>
        <Pill tone="edward">acted</Pill>
        <Pill>plain</Pill>
      </>,
    );
    const pills = Array.from(container.querySelectorAll("span"));
    assert.equal(pills.length, 6);
    assert.match(pills[0].className, /bg-ok-soft/);
    assert.match(pills[1].className, /bg-warn-soft/);
    assert.match(pills[2].className, /bg-bad-soft/);
    assert.match(pills[3].className, /bg-info-soft/);
    assert.match(pills[4].className, /bg-terminal-soft/);
    assert.doesNotMatch(pills[5].className, /-soft/);
  });
});

describe("Segment", () => {
  it("renders one button per option and reports the chosen value", () => {
    let chosen = "";
    render(
      <Segment
        value="a"
        onChange={(v) => (chosen = v)}
        options={[
          { value: "a", label: "Yours" },
          { value: "b", label: "Edward's" },
        ]}
      />,
    );
    const yours = screen.getByRole("radio", { name: "Yours" });
    const his = screen.getByRole("radio", { name: "Edward's" });
    assert.equal(yours.getAttribute("aria-checked"), "true");
    fireEvent.click(his);
    assert.equal(chosen, "b");
  });
});

describe("Segment (2026-09-26 review)", () => {
  function Seg({ initial = "a" }: { initial?: string }) {
    const [v, setV] = useState(initial);
    return (
      <Segment
        value={v}
        onChange={setV}
        label="Right call?"
        options={[
          { value: "a", label: <>Yes<span className="sr-only">, right call</span></> },
          { value: "b", label: <>No<span className="sr-only">, wrong call</span></> },
          { value: "c", label: "Skip" },
        ]}
      />
    );
  }

  it("each option contains its own sr-only text and is a 44px thumb target on touch", () => {
    render(<Seg />);
    for (const r of screen.getAllByRole("radio")) {
      // An absolutely placed sr-only label whose nearest positioned ancestor
      // was <body> stretched the document (/settings/thoughts scrolled 4,000px).
      assert.match(r.className, /(^|\s)relative(\s|$)/);
      assert.match(r.className, /(^|\s)min-h-\[44px\](\s|$)/);
      assert.match(r.className, /md:min-h-\[32px\]/);
    }
  });

  it("is one tab stop; arrows, Home and End move the choice like ARIA radios", () => {
    render(<Seg />);
    const [yes, no, skip] = screen.getAllByRole("radio");
    assert.deepEqual([yes, no, skip].map((r) => r.getAttribute("tabindex")), ["0", "-1", "-1"]);
    yes.focus();
    fireEvent.keyDown(yes, { key: "ArrowRight" });
    assert.equal(no.getAttribute("aria-checked"), "true");
    assert.equal(document.activeElement, no);
    fireEvent.keyDown(no, { key: "End" });
    assert.equal(skip.getAttribute("aria-checked"), "true");
    fireEvent.keyDown(skip, { key: "ArrowRight" });
    assert.equal(yes.getAttribute("aria-checked"), "true", "wraps around");
    fireEvent.keyDown(yes, { key: "ArrowLeft" });
    assert.equal(skip.getAttribute("aria-checked"), "true");
    fireEvent.keyDown(skip, { key: "Home" });
    assert.equal(yes.getAttribute("aria-checked"), "true");
    assert.deepEqual([yes, no, skip].map((r) => r.getAttribute("tabindex")), ["0", "-1", "-1"]);
  });

  it("with nothing chosen, the first option is the tab stop", () => {
    render(<Seg initial="none" />);
    assert.deepEqual(screen.getAllByRole("radio").map((r) => r.getAttribute("tabindex")), ["0", "-1", "-1"]);
  });
});

describe("Row", () => {
  it("is at least 44px tall when tappable and wraps its detail to two lines", () => {
    const { container } = render(<Row href="/settings/model" title="Model" detail="a long detail" />);
    const a = container.querySelector("a")!;
    assert.match(a.className, /min-h-\[44px\]/);
    const detail = screen.getByText("a long detail");
    assert.match(detail.className, /line-clamp-2/);
  });

  it("shows a chevron only for links", () => {
    const { container } = render(
      <>
        <Row href="/x" title="Link" />
        <Row onClick={() => {}} title="Action" />
      </>,
    );
    const [link, action] = [container.querySelector("a")!, container.querySelector("button")!];
    assert.ok(link.querySelector("svg.lucide-chevron-right"), "a link row has a chevron");
    assert.ok(action.querySelector("svg") === null, "an action row has none");
  });

  it("an expander has a chevron-down that turns, aria-expanded, and unclamps its detail", () => {
    function Expander() {
      const [open, setOpen] = useState(false);
      return <Row onClick={() => setOpen(!open)} expanded={open} title="More" detail="all the detail" />;
    }
    render(<Expander />);
    const btn = screen.getByRole("button", { name: /More/ });
    assert.equal(btn.getAttribute("aria-expanded"), "false");
    const chevron = btn.querySelector("svg.lucide-chevron-down")!;
    assert.ok(chevron, "expander chevron");
    assert.doesNotMatch(chevron.getAttribute("class") ?? "", /rotate-180/);
    fireEvent.click(btn);
    assert.equal(btn.getAttribute("aria-expanded"), "true");
    assert.match(btn.querySelector("svg.lucide-chevron-down")!.getAttribute("class") ?? "", /rotate-180/);
    assert.doesNotMatch(screen.getByText("all the detail").className, /line-clamp/);
  });

  it("is a link when href is given, a button when onClick is given, else static", () => {
    const { container } = render(
      <>
        <Row href="/settings/model" title="Model" detail="astra" />
        <Row onClick={() => {}} title="Tap" />
        <Row title="Static" />
      </>,
    );
    assert.ok(container.querySelector("a[href='/settings/model']"));
    assert.ok(screen.getByRole("button", { name: /Tap/ }));
    assert.ok(screen.getByText("Static"));
  });
});

describe("SectionLabel + EmptyState", () => {
  it("SectionLabel is an overline and its action gets a padded 44px hit area", () => {
    let hit = 0;
    render(
      <SectionLabel action="See all" onAction={() => hit++}>
        For you
      </SectionLabel>,
    );
    assert.match(screen.getByText("For you").parentElement!.className, /text-overline/);
    const action = screen.getByRole("button", { name: "See all" });
    assert.match(action.className, /min-h-\[44px\]/);
    fireEvent.click(action);
    assert.equal(hit, 1);
  });

  it("SectionLabel is a heading, so VoiceOver's rotor can jump between sections", () => {
    render(<SectionLabel>For you</SectionLabel>);
    const h = screen.getByRole("heading", { name: "For you" });
    assert.equal(h.tagName, "H2");
    render(<SectionLabel as="h3">Running on</SectionLabel>);
    assert.equal(screen.getByRole("heading", { name: "Running on" }).tagName, "H3");
  });

  it("render their text", () => {
    render(
      <>
        <SectionLabel>Needs you</SectionLabel>
        <EmptyState>Nothing waiting on you.</EmptyState>
      </>,
    );
    assert.ok(screen.getByText("Needs you"));
    assert.ok(screen.getByText("Nothing waiting on you."));
  });
});

describe("cn (tailwind-merge that knows the design tokens)", () => {
  it("keeps a type-scale size next to a text colour", () => {
    assert.equal(cn("text-body", "text-text-primary"), "text-body text-text-primary");
    assert.equal(cn("text-overline text-text-muted", "text-caption"), "text-text-muted text-caption");
  });
  it("merges the new shadows and radii", () => {
    assert.equal(cn("shadow-e1", "shadow-e2"), "shadow-e2");
    assert.equal(cn("rounded-lg", "rounded-xl"), "rounded-xl");
    assert.equal(cn("bg-surface-3", "bg-surface"), "bg-surface");
    assert.equal(cn("duration-fast", "duration-slow"), "duration-slow");
  });
});

describe("Card", () => {
  it("is a hairlined surface with the card radius and e1", () => {
    const { container } = render(<Card>hi</Card>);
    const el = container.firstElementChild!;
    for (const c of ["bg-surface", "border", "border-border", "rounded-lg", "shadow-e1"]) {
      assert.ok(el.classList.contains(c), `missing ${c}`);
    }
  });
  it("is one tappable link when given href", () => {
    const { container } = render(<Card href="/today/goals/g1">Goal</Card>);
    const a = container.querySelector("a[href='/today/goals/g1']")!;
    assert.ok(a);
    assert.match(a.className, /active:scale-/);
  });
});

describe("Button", () => {
  it("maps variants: Ellis's primary is neutral, Edward's is emerald", () => {
    render(
      <>
        <Button>Save</Button>
        <Button variant="edward">Try it</Button>
        <Button variant="secondary">Later</Button>
        <Button variant="ghost">Skip</Button>
        <Button variant="danger">Forget</Button>
      </>,
    );
    const cls = (name: string) => screen.getByRole("button", { name }).className;
    assert.match(cls("Save"), /bg-text-primary/);
    assert.match(cls("Save"), /text-primary-bg/);
    assert.doesNotMatch(cls("Save"), /bg-terminal/);
    assert.match(cls("Try it"), /bg-terminal/);
    assert.match(cls("Try it"), /text-terminal-ink/);
    assert.match(cls("Later"), /bg-surface-3/);
    assert.match(cls("Skip"), /bg-transparent/);
    assert.match(cls("Forget"), /bg-bad/);
  });

  it("is 44px on touch, 36px on md+, presses, and defaults to type=button", () => {
    render(<Button>Go</Button>);
    const b = screen.getByRole("button", { name: "Go" });
    assert.match(b.className, /\bh-11\b/);
    assert.match(b.className, /md:h-9/);
    assert.match(b.className, /active:scale-\[\.97\]/);
    assert.equal(b.getAttribute("type"), "button");
  });

  it("loading disables it and says so", () => {
    let clicks = 0;
    render(
      <Button loading onClick={() => clicks++}>
        Send
      </Button>,
    );
    const b = screen.getByRole("button", { name: /Send/ });
    assert.equal(b.getAttribute("aria-busy"), "true");
    assert.equal((b as HTMLButtonElement).disabled, true);
    fireEvent.click(b);
    assert.equal(clicks, 0);
  });

  it("renders a link with href, and buttonClasses styles anything", () => {
    const { container } = render(<Button href="/chat">Open</Button>);
    assert.ok(container.querySelector("a[href='/chat']"));
    assert.match(buttonClasses({ variant: "edward", size: "sm" }), /bg-terminal/);
  });
});

describe("Sheet", () => {
  function Harness({ onClose = () => {} }: { onClose?: () => void }) {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button type="button" onClick={() => setOpen(true)}>
          Open sheet
        </button>
        <Sheet
          open={open}
          onClose={() => {
            onClose();
            setOpen(false);
          }}
          title="Forget…"
        >
          <button type="button">First</button>
          <button type="button">Last</button>
        </Sheet>
      </>
    );
  }

  it("renders nothing while closed", () => {
    render(<Sheet open={false} onClose={() => {}} title="x">body</Sheet>);
    assert.ok(screen.queryByRole("dialog") === null, "no dialog while closed");
  });

  it("is a labelled modal dialog that locks the page scroll", () => {
    render(<Harness />);
    fireEvent.click(screen.getByText("Open sheet"));
    const dialog = screen.getByRole("dialog");
    assert.equal(dialog.getAttribute("aria-modal"), "true");
    const labelledBy = dialog.getAttribute("aria-labelledby")!;
    assert.equal(document.getElementById(labelledBy)?.textContent, "Forget…");
    assert.equal(document.body.style.overflow, "hidden");
  });

  it("moves focus in, traps Tab, and closes on Escape returning focus", () => {
    let closed = 0;
    render(<Harness onClose={() => closed++} />);
    const trigger = screen.getByText("Open sheet");
    trigger.focus();
    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog");
    assert.ok(dialog.contains(document.activeElement), "focus moved into the sheet");

    screen.getByText("Last").focus();
    fireEvent.keyDown(dialog, { key: "Tab" });
    assert.ok(dialog.contains(document.activeElement), "Tab stays inside");
    assert.ok(document.activeElement !== screen.getByText("Last"), "Tab from the last control wraps to the first");

    fireEvent.keyDown(document, { key: "Escape" });
    assert.equal(closed, 1);
    assert.ok(document.activeElement === trigger, "focus returns to what opened it");
    assert.notEqual(document.body.style.overflow, "hidden");
  });

  it("closes from the backdrop and the close button", () => {
    let closed = 0;
    render(<Sheet open onClose={() => closed++} title="T">x</Sheet>);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.click(document.querySelector("[data-sheet-backdrop]")!);
    assert.equal(closed, 2);
  });
});

describe("Sheet stacking and drag", () => {
  it("Escape closes only the top-most of two open sheets", () => {
    let outer = 0;
    let inner = 0;
    render(
      <>
        <Sheet open onClose={() => outer++} title="Outer">
          outer body
        </Sheet>
        <Sheet open onClose={() => inner++} title="Inner">
          inner body
        </Sheet>
      </>,
    );
    fireEvent.keyDown(document, { key: "Escape" });
    assert.deepEqual({ outer, inner }, { outer: 0, inner: 1 });
  });

  it("a drag dismisses once it is far or fast enough", () => {
    assert.equal(shouldDismissDrag(40, 100), false);
    assert.equal(shouldDismissDrag(120, 0), true);
    assert.equal(shouldDismissDrag(20, 900), true);
  });
});

describe("ConfirmSheet", () => {
  it("confirms or cancels, never both", () => {
    let yes = 0;
    let no = 0;
    render(
      <ConfirmSheet
        open
        onClose={() => no++}
        onConfirm={() => yes++}
        title="Sign out?"
        body="You'll need your password to come back."
        confirmLabel="Sign out"
      />,
    );
    assert.ok(screen.getByText("You'll need your password to come back."));
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    assert.equal(yes, 1);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    assert.equal(no, 1);
    assert.match(screen.getByRole("button", { name: "Sign out" }).className, /bg-bad/);
  });
});

describe("Switch", () => {
  it("is a switch that reports the flipped value", () => {
    let got: boolean | null = null;
    render(<Switch checked={false} onChange={(v) => (got = v)} label="Heartbeat" />);
    const sw = screen.getByRole("switch", { name: "Heartbeat" });
    assert.equal(sw.getAttribute("aria-checked"), "false");
    fireEvent.click(sw);
    assert.equal(got, true);
  });
  it("does nothing when disabled", () => {
    let got: boolean | null = null;
    render(<Switch checked onChange={(v) => (got = v)} label="Off" disabled />);
    fireEvent.click(screen.getByRole("switch", { name: "Off" }));
    assert.equal(got, null);
  });
});

describe("Field", () => {
  it("labels its control, links the hint, and uses 16px inputs", () => {
    render(
      <Field label="Timezone" hint="Where you are">
        <Input defaultValue="America/Chicago" />
      </Field>,
    );
    const input = screen.getByLabelText("Timezone");
    assert.equal(input.tagName, "INPUT");
    assert.match(input.className, /text-\[16px\]/);
    const describedBy = input.getAttribute("aria-describedby")!;
    assert.equal(document.getElementById(describedBy)?.textContent, "Where you are");
  });
  it("marks an error invalid and says why", () => {
    render(
      <Field label="Name" error="Needs a name">
        <Input />
      </Field>,
    );
    const input = screen.getByLabelText("Name");
    assert.equal(input.getAttribute("aria-invalid"), "true");
    assert.ok(screen.getByText("Needs a name"));
  });
  it("tokenises select and textarea too", () => {
    render(
      <>
        <Field label="Model">
          <Select defaultValue="a">
            <option value="a">astra</option>
          </Select>
        </Field>
        <Field label="Note">
          <Textarea />
        </Field>
      </>,
    );
    assert.equal(screen.getByLabelText("Model").tagName, "SELECT");
    assert.match(screen.getByLabelText("Note").className, /text-\[16px\]/);
  });
});

describe("Notice", () => {
  it("is an alert when bad, a status otherwise", () => {
    render(
      <>
        <Notice tone="bad">Couldn't save.</Notice>
        <Notice tone="ok">Saved.</Notice>
      </>,
    );
    assert.match(screen.getByRole("alert").className, /bg-bad-soft/);
    assert.match(screen.getByRole("status").className, /bg-ok-soft/);
  });
});

describe("Meter", () => {
  it("is a meter with a clamped fill and an optional pace marker", () => {
    const { container } = render(<Meter value={140} pace={40} label="Week's quota" />);
    const m = screen.getByRole("meter", { name: "Week's quota" });
    assert.equal(m.getAttribute("aria-valuenow"), "100");
    assert.equal((container.querySelector("[data-meter-fill]") as HTMLElement).style.width, "100%");
    assert.equal((container.querySelector("[data-meter-pace]") as HTMLElement).style.left, "40%");
  });
});

describe("Skeleton", () => {
  it("is hidden from assistive tech and draws N lines", () => {
    const { container } = render(<Skeleton lines={3} />);
    const root = container.firstElementChild!;
    assert.equal(root.getAttribute("aria-hidden"), "true");
    assert.equal(root.querySelectorAll("[data-skeleton-line]").length, 3);
  });
});

describe("Pulse", () => {
  it("is a breathing emerald status dot that respects reduced motion", () => {
    render(<Pulse label="Loading memories" />);
    const p = screen.getByRole("status", { name: "Loading memories" });
    assert.match(p.innerHTML, /motion-safe:animate-breathe/);
    assert.match(p.innerHTML, /bg-terminal/);
    assert.equal(EdwardPulse, Pulse);
  });
});

describe("Toast", () => {
  function Trigger({ undo }: { undo?: () => void }) {
    const { toast } = useToast();
    return (
      <button type="button" onClick={() => toast({ title: "Archived", undo })}>
        archive
      </button>
    );
  }

  it("shows above the dock, offers Undo, and Undo runs once and closes it", () => {
    let undone = 0;
    render(
      <ToastProvider>
        <Trigger undo={() => undone++} />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByText("archive"));
    assert.ok(screen.getByText("Archived"));
    const viewport = document.querySelector("[data-toast-viewport]")!;
    assert.match(viewport.className, /toast-viewport/);
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    assert.equal(undone, 1);
    assert.ok(screen.queryByRole("button", { name: "Undo" }) === null, "Undo is gone once used");
  });

  it("dismisses itself", () => {
    mock.timers.enable({ apis: ["setTimeout"] });
    render(
      <ToastProvider>
        <Trigger />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByText("archive"));
    assert.ok(screen.getByText("Archived"));
    act(() => mock.timers.tick(10_000));
    assert.ok(screen.queryByText("Archived") === null, "the toast is gone");
  });

  it("keeps at most three on screen, the oldest making way", () => {
    function Many() {
      const { toast } = useToast();
      return (
        <button type="button" onClick={() => [1, 2, 3, 4].forEach((n) => toast({ title: `Toast ${n}` }))}>
          many
        </button>
      );
    }
    render(
      <ToastProvider>
        <Many />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByText("many"));
    const shown = Array.from(document.querySelectorAll("[data-toast-viewport] > :not([data-leaving])"));
    assert.equal(MAX_TOASTS, 3);
    assert.deepEqual(
      shown.map((el) => el.textContent),
      ["Toast 2", "Toast 3", "Toast 4"],
    );
  });

  it("is a harmless no-op outside a provider", () => {
    render(<Trigger />);
    fireEvent.click(screen.getByText("archive"));
    assert.ok(screen.queryByText("Archived") === null, "the toast is gone");
  });
});

describe("PanelShell", () => {
  it("breadcrumbs back to the panel's room with a title1 heading", () => {
    render(
      <PanelShell slug="memories" title="Memories" description="What he remembers" actions={<Button>New</Button>}>
        <p>body</p>
      </PanelShell>,
    );
    const back = screen.getByRole("link", { name: /Memory/ });
    assert.equal(back.getAttribute("href"), "/settings/room/memory");
    const h1 = screen.getByRole("heading", { level: 1, name: "Memories" });
    assert.match(h1.className, /text-title1/);
    assert.ok(screen.getByText("What he remembers"));
    assert.ok(screen.getByRole("button", { name: "New" }));
    assert.ok(screen.getByText("body"));
  });

  it("a sub-view breadcrumbs to its parent view, and a null title draws no header", () => {
    render(
      <PanelShell slug="documents" title={null} back={{ href: "/settings/documents", label: "Documents" }}>
        <h1>Cabin weekend</h1>
      </PanelShell>,
    );
    const back = screen.getByRole("link", { name: /Documents/ });
    assert.equal(back.getAttribute("href"), "/settings/documents");
    assert.equal(screen.getAllByRole("heading", { level: 1 }).length, 1, "the document's own title is the one h1");
  });
});
