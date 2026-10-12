// R/G red: attachment previews make one object URL per file, once
// (lib/objectUrls.ts). The composer used to call URL.createObjectURL in
// render — a new blob URL on every keystroke, revoked only if the <img>
// happened to load.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { syncObjectUrls } from "../lib/objectUrls.ts";

type F = { name: string };

function fakes() {
  let n = 0;
  const created: string[] = [];
  const revoked: string[] = [];
  return {
    created,
    revoked,
    create: (f: F) => {
      const url = `blob:${f.name}#${++n}`;
      created.push(url);
      return url;
    },
    revoke: (url: string) => void revoked.push(url),
  };
}

describe("syncObjectUrls", () => {
  it("creates one URL per new file", () => {
    const fx = fakes();
    const a = { name: "a.png" };
    const b = { name: "b.png" };
    const m = syncObjectUrls(new Map(), [a, b], fx.create, fx.revoke);
    assert.equal(m.get(a), "blob:a.png#1");
    assert.equal(m.get(b), "blob:b.png#2");
    assert.equal(fx.created.length, 2);
  });

  it("re-syncing the same files creates nothing new (a keystroke re-render)", () => {
    const fx = fakes();
    const a = { name: "a.png" };
    const m1 = syncObjectUrls(new Map(), [a], fx.create, fx.revoke);
    const m2 = syncObjectUrls(m1, [a], fx.create, fx.revoke);
    assert.equal(fx.created.length, 1);
    assert.equal(m2.get(a), m1.get(a));
    assert.deepEqual(fx.revoked, []);
  });

  it("revokes the URL of a file that was removed", () => {
    const fx = fakes();
    const a = { name: "a.png" };
    const b = { name: "b.png" };
    const m1 = syncObjectUrls(new Map(), [a, b], fx.create, fx.revoke);
    const m2 = syncObjectUrls(m1, [b], fx.create, fx.revoke);
    assert.deepEqual(fx.revoked, ["blob:a.png#1"]);
    assert.equal(m2.has(a), false);
    assert.equal(m2.get(b), "blob:b.png#2");
  });

  it("two files with the same name are two previews", () => {
    const fx = fakes();
    const a1 = { name: "IMG.jpg" };
    const a2 = { name: "IMG.jpg" };
    const m = syncObjectUrls(new Map(), [a1, a2], fx.create, fx.revoke);
    assert.notEqual(m.get(a1), m.get(a2));
  });

  it("an empty list (sent, or unmounted) revokes everything", () => {
    const fx = fakes();
    const a = { name: "a.png" };
    const m1 = syncObjectUrls(new Map(), [a], fx.create, fx.revoke);
    const m2 = syncObjectUrls(m1, [], fx.create, fx.revoke);
    assert.equal(m2.size, 0);
    assert.deepEqual(fx.revoked, ["blob:a.png#1"]);
  });

  it("only files that want a preview get one", () => {
    const fx = fakes();
    const img = { name: "a.png", type: "image/png" };
    const pdf = { name: "b.pdf", type: "application/pdf" };
    const m = syncObjectUrls(new Map(), [img, pdf], fx.create, fx.revoke, (f) => f.type.startsWith("image/"));
    assert.equal(m.has(img), true);
    assert.equal(m.has(pdf), false);
  });
});
