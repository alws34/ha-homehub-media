import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const src = readFileSync(new URL("../../custom_components/homehub_media/www/homehub-media-card.js", import.meta.url), "utf8");
let Card;
const ctx = { window: { __HOMEHUB_TEST__: true, customCards: [] }, customElements: { get: () => undefined, define: (_, c) => { Card = c; } },
              HTMLElement: class {}, CustomEvent: class {}, localStorage: { getItem() {}, setItem() {} }, location: { origin: "http://ha" } };
ctx.window.window = ctx.window;
vm.createContext(ctx); vm.runInContext(src, ctx);
const { esc, mixToVolumes, nextTarget, textRoute, canSeek } = ctx.window.__homehubTest;

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

const snap = (keyboard, keyboard_active = false, activity = "tv") =>
  ({ targets: { keyboard, pc: true, box: true }, tv: { keyboard_active }, activity: { current: activity } });

test("text routing: TV-only setup lands on the TV", () => {
  assert.equal(textRoute(snap(false), "tv"), "tv");
  assert.equal(textRoute(snap(true, true), "tv"), "tv");  // bridge present but the TV's IME is up
  assert.equal(textRoute(undefined, "tv"), "tv");
});
test("text routing: PC target and PC activity route to the pc", () => {
  assert.equal(textRoute(snap(false), "pc"), "pc");
  assert.equal(textRoute(snap(true, false, "pc"), "tv"), "pc");
});
test("text routing: bridge present and TV keyboard inactive routes to the keyboard", () => {
  assert.equal(textRoute(snap(true), "tv"), "keyboard");
  assert.equal(textRoute(snap(true), "box"), "keyboard");
});

// drives the card's real _kbd with a stubbed _call: which keyboard_key calls reach the hub
const keyCalls = (s, target) => {
  const calls = [];
  const card = Object.assign(Object.create(Card.prototype), { _snap: s, _target: target, _call: (...a) => calls.push(a) });
  card._kbd("keyboard_key", { key: "clear" });
  card._kbd("keyboard_key", { key: "backspace" });
  return calls.map(([, d]) => JSON.stringify(d));
};
test("clear/backspace are suppressed when text lands on the TV", () => {
  assert.deepEqual(keyCalls(snap(false), "tv"), []);
  assert.deepEqual(keyCalls(snap(true, true), "tv"), []);
});
test("clear/backspace still reach the pc and the keyboard bridge", () => {
  assert.deepEqual(keyCalls(snap(false), "pc"), ['{"key":"clear","target":"pc"}', '{"key":"backspace","target":"pc"}']);
  assert.deepEqual(keyCalls(snap(true), "tv"), ['{"key":"clear"}', '{"key":"backspace"}']);  // hub picks the bridge
});
test("seek buttons are hidden while the PC is the activity", () => {
  assert.equal(canSeek(snap(false, false, "pc")), false);
  assert.equal(canSeek(snap(false, false, "tv")), true);
  assert.equal(canSeek(undefined), true);
});
