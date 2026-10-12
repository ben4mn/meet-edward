// A chat message as Ellis sees it (2026-09-26): his own words in a neutral,
// high-contrast bubble (emerald is Edward's); Edward's answer without a
// bubble, its work rail above it, and — once finished — Copy, Retry and Read
// aloud underneath. Links into the app stay in the app.

import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { MessageBubble } from "../components/chat/MessageBubble.tsx";
import type { Message } from "../lib/ChatContext.tsx";

const present = (el: Element | null) => el !== null;
afterEach(cleanup);

const ANSWER: Message = {
  id: "a1",
  role: "assistant",
  content: "See [the thread](/chat?c=t-reply-parker) and the [forecast](https://www.nws.gov/x).",
};

describe("MessageBubble", () => {
  it("Ellis's bubble is neutral, not Edward's emerald", () => {
    render(<MessageBubble message={{ id: "u1", role: "user", content: "hi there" }} />);
    const bubble = screen.getByText("hi there").closest("[data-testid=user-bubble]");
    assert.equal(present(bubble), true);
    const cls = bubble!.className;
    assert.equal(/\bbg-terminal\b/.test(cls), false, cls);
    assert.equal(/bg-surface-3/.test(cls), true, cls);
  });

  it("a finished answer offers Copy, Retry and Read aloud", async () => {
    let retried = "";
    const copied: string[] = [];
    Object.defineProperty(navigator, "clipboard", { value: { writeText: async (t: string) => void copied.push(t) }, configurable: true });
    render(<MessageBubble message={ANSWER} retryText="what's the weather" onRetry={(t) => (retried = t)} canReadAloud />);
    fireEvent.click(screen.getByRole("button", { name: "Copy answer" }));
    await waitFor(() => assert.equal(present(screen.queryByRole("button", { name: "Copied" })), true));
    assert.deepEqual(copied, [ANSWER.content]);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    assert.equal(retried, "what's the weather");
    assert.equal(present(screen.queryByRole("button", { name: "Read aloud" })), true);
  });

  it("offers no Retry without the question, and no Read aloud without voice", () => {
    render(<MessageBubble message={ANSWER} />);
    assert.equal(present(screen.queryByRole("button", { name: "Copy answer" })), true);
    assert.equal(present(screen.queryByRole("button", { name: "Retry" })), false);
    assert.equal(present(screen.queryByRole("button", { name: "Read aloud" })), false);
  });

  it("no actions while the answer is still streaming", () => {
    render(<MessageBubble message={{ ...ANSWER, content: "Partial" }} live turn={null} canReadAloud retryText="x" onRetry={() => {}} />);
    assert.equal(present(screen.queryByRole("button", { name: "Copy answer" })), false);
    assert.equal(screen.getByTestId("work-rail").getAttribute("data-live"), "true");
  });

  it("keeps app links in the app and marks the rest as leaving", () => {
    render(<MessageBubble message={ANSWER} />);
    const inside = screen.getByText("the thread").closest("a")!;
    assert.equal(inside.getAttribute("href"), "/chat?c=t-reply-parker");
    assert.equal(inside.getAttribute("target"), null);
    const outside = screen.getByText("forecast").closest("a")!;
    assert.equal(outside.getAttribute("target"), "_blank");
    assert.equal(present(outside.querySelector("[aria-label='(opens in a new tab)']")), true);
  });

  it("a fenced block without a language is still a block", () => {
    render(<MessageBubble message={{ id: "a9", role: "assistant", content: "Output:\n\n```\n735 441.0 294.0\n```\n\nand `inline` too." }} />);
    assert.equal(screen.getAllByRole("button", { name: "Copy code" }).length, 1, "one block, with its copy button");
    assert.equal(screen.getByText("inline").tagName, "CODE");
  });

  it("says where Ellis stopped him", () => {
    render(<MessageBubble message={{ ...ANSWER, wasInterrupted: true }} />);
    assert.equal(present(screen.queryByText("You stopped me here.")), true);
  });
});

// 2026-09-26 review: Read aloud cancelled while the voice was still being
// made — or the thread left — still played a moment later, with no Stop on
// screen. The request is aborted and a late clip is never played.
describe("Read aloud, cancelled while loading", () => {
  const originalFetch = globalThis.fetch;
  const g = globalThis as unknown as { Audio?: unknown };
  const originalAudio = g.Audio;
  const originalCreate = URL.createObjectURL;
  let release: () => void = () => {};
  let signals: Array<AbortSignal | undefined> = [];
  let plays = 0;

  function install() {
    signals = [];
    plays = 0;
    globalThis.fetch = (async (_url: RequestInfo | URL, init?: RequestInit) => {
      signals.push(init?.signal ?? undefined);
      await new Promise<void>((r) => (release = r));
      return new Response(new ArrayBuffer(8), { status: 200 });
    }) as typeof fetch;
    class FakeAudio {
      onended: (() => void) | null = null;
      constructor(public src: string) {}
      play() {
        plays++;
        return Promise.resolve();
      }
      pause() {}
    }
    g.Audio = FakeAudio;
    (window as unknown as { Audio: unknown }).Audio = FakeAudio;
    URL.createObjectURL = () => "blob:x";
  }
  function restore() {
    globalThis.fetch = originalFetch;
    g.Audio = originalAudio;
    URL.createObjectURL = originalCreate;
  }

  it("a second tap cancels it: nothing plays when the voice arrives", async () => {
    install();
    try {
      render(<MessageBubble message={ANSWER} canReadAloud isLast />);
      fireEvent.click(screen.getByRole("button", { name: "Read aloud" }));
      await waitFor(() => assert.equal(present(screen.queryByRole("button", { name: "Getting my voice ready" })), true));
      fireEvent.click(screen.getByRole("button", { name: "Getting my voice ready" }));
      assert.equal(present(screen.queryByRole("button", { name: "Read aloud" })), true);
      assert.equal(signals[0]?.aborted, true, "the request is aborted");
      release();
      await new Promise((r) => setTimeout(r, 20));
      assert.equal(plays, 0);
      assert.equal(present(screen.queryByRole("button", { name: "Stop reading" })), false);
      assert.equal(present(screen.queryByRole("button", { name: "I couldn't read that aloud" })), false, "a cancel is not an error");
    } finally {
      restore();
    }
  });

  it("leaving the thread cancels it too", async () => {
    install();
    try {
      const { unmount } = render(<MessageBubble message={ANSWER} canReadAloud isLast />);
      fireEvent.click(screen.getByRole("button", { name: "Read aloud" }));
      await waitFor(() => assert.equal(signals.length, 1));
      unmount();
      assert.equal(signals[0]?.aborted, true);
      release();
      await new Promise((r) => setTimeout(r, 20));
      assert.equal(plays, 0);
    } finally {
      restore();
    }
  });
});

// 2026-09-26 review: a turn stopped before any answer text had no Retry —
// only "You stopped me here." — so Ellis retyped the question on his phone.
describe("a turn stopped before it answered", () => {
  it("offers Try again beside 'You stopped me here.'", () => {
    let retried = "";
    render(
      <MessageBubble
        message={{ id: "s1", role: "assistant", content: "", wasInterrupted: true, progressSteps: [{ id: "m", step: "memory_search", status: "completed", message: "", timestamp: 1, count: 0 }] }}
        retryText="what's the weather"
        onRetry={(t) => (retried = t)}
        isLast
      />,
    );
    assert.equal(present(screen.queryByText("You stopped me here.")), true);
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    assert.equal(retried, "what's the weather");
  });
});

describe("a table still being written", () => {
  const partial = "Here's the plan.\n\n| Week | Miles | Long";
  it("is held back while he streams", () => {
    render(<MessageBubble message={{ id: "t1", role: "assistant", content: partial }} live turn={null} />);
    assert.equal(present(screen.queryByText(/\| Week/)), false);
    assert.equal(present(screen.queryByText("Here's the plan.")), true);
  });
  it("and dropped once Ellis stopped him", () => {
    render(<MessageBubble message={{ id: "t2", role: "assistant", content: partial, wasInterrupted: true }} />);
    assert.equal(present(screen.queryByText(/\| Week/)), false);
    assert.equal(present(screen.queryByText("You stopped me here.")), true);
  });
});

// 2026-09-26 review: a voice memo was the browser's native <audio controls>
// — a grey system pill squeezed into the bubble. It is a small player now:
// play / pause, progress, and the length in tabular numbers.
describe("a voice memo", () => {
  const memo: Message = {
    id: "u-memo",
    role: "user",
    content: "Remind me to call the vet",
    attachments: [{ id: "a1", file_id: "f1", filename: "memo.m4a", mime_type: "audio/mp4", size: 2048 }],
  };

  it("plays through its own control, not the browser's", () => {
    const proto = window.HTMLMediaElement.prototype as unknown as { play: () => Promise<void>; pause: () => void };
    const origPlay = proto.play;
    const origPause = proto.pause;
    let plays = 0;
    proto.play = function () {
      plays++;
      return Promise.resolve();
    };
    proto.pause = function () {};
    try {
      const { container } = render(<MessageBubble message={memo} />);
      assert.equal(present(container.querySelector("audio[controls]")), false, "no native player");
      fireEvent.click(screen.getByRole("button", { name: /play voice memo/i }));
      assert.equal(plays, 1);
      assert.equal(present(screen.queryByRole("progressbar")), true);
    } finally {
      proto.play = origPlay;
      proto.pause = origPause;
    }
  });
});

// Review 2026-09-26 (interrupted-answer-jargon): a turn cut off mid-answer
// is its own warn notice in Edward's voice, with Retry — not
// "(stream interrupted)" in the markdown, not a finished-looking answer.
describe("a cut-off answer", () => {
  it("keeps what he said, and says plainly that he was cut off, with Retry", () => {
    const retried: string[] = [];
    render(
      <MessageBubble
        message={{ id: "a9", role: "assistant", content: "Nothing on", wasCutOff: true }}
        retryText="what's on saturday"
        onRetry={(t) => retried.push(t)}
        isLast
      />,
    );
    assert.equal(present(screen.queryByText(/stream interrupted/)), false);
    const notice = screen.getByRole("status");
    assert.match(notice.textContent ?? "", /I got cut off before finishing/);
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    assert.deepEqual(retried, ["what's on saturday"]);
  });

  it("with nothing said, the notice is the whole message", () => {
    render(<MessageBubble message={{ id: "a8", role: "assistant", content: "", wasCutOff: true }} retryText="go" onRetry={() => {}} isLast />);
    assert.match(screen.getByRole("status").textContent ?? "", /I couldn't finish that/);
    assert.equal(present(screen.queryByText(/encountered an error/)), false);
  });
});
