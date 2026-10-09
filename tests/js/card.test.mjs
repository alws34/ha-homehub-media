import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const src = readFileSync(new URL("../../custom_components/homehub_media/www/homehub-media-card.js", import.meta.url), "utf8");
const ctx = { window: { __HOMEHUB_TEST__: true, customCards: [] }, customElements: { get: () => undefined, define() {} },
              HTMLElement: class {}, CustomEvent: class {}, localStorage: { getItem() {}, setItem() {} }, location: { origin: "http://ha" } };
ctx.window.window = ctx.window;
vm.createContext(ctx); vm.runInContext(src, ctx);
const { esc, mixToVolumes, nextTarget } = ctx.window.__homehubTest;

test("esc neutralises markup", () => {
  assert.equal(esc(`<img src=x onerror="a">'`), "&lt;img src=x onerror=&quot;a&quot;&gt;&#39;");
});
test("mix slider maps to volumes", () => {
  assert.deepEqual({ ...mixToVolumes(30) }, { original_volume: 70, soundtrack_volume: 30 });
});
test("target cycling wraps and skips unavailable", () => {
  assert.equal(nextTarget("tv", { box: false, pc: true }, 1), "pc");
  assert.equal(nextTarget("pc", { box: false, pc: true }, 1), "tv");
});
