import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const src = readFileSync(new URL("../../custom_components/homehub_media/www/homehub-media-card.js", import.meta.url), "utf8");
let Card;
const ctx = { window: { __HOMEHUB_TEST__: true, customCards: [] }, customElements: { get: () => undefined, define: (_, c) => { Card = c; } },
              HTMLElement: class {}, CustomEvent: class {}, localStorage: { getItem() {}, setItem() {} }, location: { origin: "http://ha" },
              setTimeout, clearTimeout, performance };
ctx.window.window = ctx.window;
vm.createContext(ctx); vm.runInContext(src, ctx);
const { esc, mixToVolumes, nextTarget, textRoute, canSeek, toggleIn, randomLabel, volumeSlider, modeIds, nextId, modeVideos, featuredMode } = ctx.window.__homehubTest;
const plain = (v) => JSON.parse(JSON.stringify(v));  // values from the card's realm, for deepStrictEqual

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

test("rotation toggle adds and removes an id, keeps order and the cap", () => {
  assert.deepEqual(plain(toggleIn(undefined, "rain")), ["rain"]);
  assert.deepEqual(plain(toggleIn(["rain", "fire"], "sea")), ["rain", "fire", "sea"]);
  assert.deepEqual(plain(toggleIn(["rain", "fire", "sea"], "fire")), ["rain", "sea"]);
  const full = Array.from({ length: 50 }, (_, i) => `v${i}`);
  assert.equal(toggleIn(full, "one_more").length, 50);  // the backend rejects more than 50
  assert.equal(toggleIn(full, "v3").length, 49);
});
test("mode tiles say when a mode picks from several videos", () => {
  assert.equal(randomLabel({ visuals: ["rain", "fire", "sea"] }), "Random from 3 videos");
  assert.equal(randomLabel({ visuals: ["rain"] }), "");
  assert.equal(randomLabel(undefined), "");
});
test("rotation chip toggles the browsed visual and goes live while the mode runs", () => {
  const calls = [];
  const card = Object.assign(Object.create(Card.prototype), {
    _snap: { ambience: { mode: "chill" } }, _noClickUntil: 0, _render() {}, _call: (...a) => calls.push(a),
    _catalog: { visuals: [{ id: "rain" }, { id: "fire" }], soundtracks: [] }, _browse: { visual: "fire" },
    _edit: { id: "chill", draft: { visual: "rain", visuals: ["rain"] }, pending: null },
  });
  const click = () => card._onClick({ target: { closest: () => ({ dataset: { a: "rot" }, disabled: false }) } });
  click();
  assert.deepEqual(plain(card._edit.draft.visuals), ["rain", "fire"]);
  card._flushLive(card._edit);
  assert.deepEqual(plain(calls), [["ambience_update", { visuals: ["rain", "fire"] }]]);
  click();
  assert.deepEqual(plain(card._edit.draft.visuals), ["rain"]);
  card._flushLive(card._edit);
});
test("volume slider only when the TV volume is known (the TV media player)", () => {
  assert.equal(volumeSlider(null), "");
  assert.equal(volumeSlider(undefined), "");
  assert.match(volumeSlider(0), /<input type="range"[^>]* data-volume/);
});

// drives the card's real touchpad handlers; every service call resolves at once
const padCard = (pointerActive) => {
  const calls = [];
  const card = Object.assign(Object.create(Card.prototype), {
    _snap: { tv: { pointer_active: pointerActive }, targets: {} }, _target: "tv", _pointers: new Map(),
    _mv: { dx: 0, dy: 0 }, _sc: 0, _arrows: [], _call: (...a) => { calls.push(a); return Promise.resolve(true); },
  });
  const ev = (type, x, y = 0) => ({ type, pointerId: 1, isPrimary: true, clientX: x, clientY: y, preventDefault() {} });
  const drag = (to, step = 10) => {
    card._tpDown(ev("pointerdown", 0), { setPointerCapture() {} });
    for (let x = step; x <= to; x += step) card._tpMove(ev("pointermove", x));
    card._tpUp(ev("pointerup", to));
  };
  return { card, calls, drag };
};
const settle = () => new Promise((r) => setTimeout(r, 80));

test("swipe mode: 100 px right presses right twice, one request at a time", async () => {
  const { calls, drag } = padCard(false);
  drag(100);
  assert.equal(calls.length, 1);  // the second arrow waits for the first request
  await settle();
  assert.deepEqual(plain(calls), [["remote", { command: "right", target: "tv" }], ["remote", { command: "right", target: "tv" }]]);
});
test("swipe mode: a fast 3-step swipe in one move still sends 3 arrows; vertical goes up/down", async () => {
  const { calls, drag } = padCard(false);
  drag(150, 150);
  await settle();
  assert.equal(calls.filter(([, d]) => d.command === "right").length, 3);
  const v = padCard(false);
  v.card._tpDown({ pointerId: 1, isPrimary: true, clientX: 0, clientY: 0, preventDefault() {} }, {});
  v.card._tpMove({ pointerId: 1, clientX: 5, clientY: -60, preventDefault() {} });
  await settle();
  assert.deepEqual(plain(v.calls), [["remote", { command: "up", target: "tv" }]]);
});
test("swipe mode: a tap is OK", async () => {
  const { calls, drag } = padCard(false);
  drag(0);
  await settle();
  assert.deepEqual(plain(calls), [["remote", { command: "select", target: "tv" }]]);
});
test("pointer mode (TV browser): drag moves and tap clicks, as before", async () => {
  const { calls, drag } = padCard(true);
  drag(30);
  await settle();
  drag(0);
  await settle();
  assert.deepEqual(plain(calls).map(([s, d]) => [s, d.action, d.target]), [["pointer", "move", "tv"], ["pointer", "click", "tv"]]);
});
test("volume slider sends one debounced set_volume", async () => {
  const calls = [];
  const card = Object.assign(Object.create(Card.prototype), { _call: (...a) => calls.push(a) });
  const input = (value) => ({ composedPath: () => [{ value, matches: (s) => s === "[data-volume]", style: { setProperty() {} } }] });
  card._onInput(input("20"));
  card._onInput(input("42.4"));
  assert.equal(calls.length, 0);
  await new Promise((r) => setTimeout(r, 300));
  assert.deepEqual(plain(calls), [["set_volume", { level: 42 }]]);
});
test("mode videos: the rotation in order (else the one visual), its YouTube visuals as rows", () => {
  const visuals = [{ id: "rain", source_type: "file", title: "Rain" },
    { id: "fire", source_type: "youtube", title: "Fire", link: "fireFIREfir", preview: "/p/fire" },
    { id: "sea", source_type: "youtube", title: "Sea", link: "https://youtu.be/seaSEAseaSE", preview: "/p/sea" }];
  // the playing (random) visual does not reorder the list
  assert.deepEqual(plain(modeIds({ visual: "sea", visuals: ["rain", "fire", "sea"] })), ["rain", "fire", "sea"]);
  assert.deepEqual(plain(modeIds({ visual: "sea", visuals: [] })), ["sea"]);
  assert.deepEqual(plain(modeIds(undefined)), []);
  assert.deepEqual(plain(modeVideos({ visual: "sea", visuals: ["rain", "fire", "sea"] }, visuals)), [
    { id: "fire", name: "Fire", url: "fireFIREfir", preview: "/p/fire" },
    { id: "sea", name: "Sea", url: "https://youtu.be/seaSEAseaSE", preview: "/p/sea" }]);
  assert.deepEqual(plain(modeVideos({ visual: "fire" }, undefined)), []);  // catalog not loaded
});
test("next video wraps, and starts from the first when the playing one is not in the list", () => {
  assert.equal(nextId(["a", "b", "c"], "b"), "c");
  assert.equal(nextId(["a", "b", "c"], "c"), "a");
  assert.equal(nextId(["a", "b"], null), "a");
});

test("controls take their depth from a neumorphic theme's soft-* tokens, with flat fallbacks", () => {
  for (const [token, fallback] of [["--soft-surface", "var(--hh-s1)"], ["--soft-shadow-small", "inset 0 0 0 1px var(--hh-line)"],
                                   ["--soft-shadow-raised", "inset 0 0 0 1px var(--hh-line)"], ["--soft-shadow-inset", "none"]])
    assert.ok(src.includes(`var(${token}, ${fallback})`), token);
  assert.match(src, /\.dpad \{[^}]*box-shadow: var\(--hh-up-lg\)/);
  assert.match(src, /\.ib \{[^}]*box-shadow: var\(--hh-up\)/);
});

test("top shelf: the playing mode, else the auto ambience pick, else the first mode", () => {
  const modes = [{ id: "fireplace", name: "Fireplace" }, { id: "dinner", name: "Dinner" }, { id: "chill", name: "Chill" }];
  assert.deepEqual(plain(featuredMode({ modes, mode: "dinner", daypart_mode: "chill" })), { m: modes[1], playing: true });
  assert.deepEqual(plain(featuredMode({ modes, mode: "off", daypart_mode: "chill" })), { m: modes[2], playing: false });
  assert.deepEqual(plain(featuredMode({ modes })), { m: modes[0], playing: false });
  assert.deepEqual(plain(featuredMode({ modes: [], mode: "cat_tv" })), { m: { id: "cat_tv", name: "Cat Tv" }, playing: true });
  assert.equal(featuredMode({}).m, null);
});

test("mode tiles start on tap and open the video editor on hold; a video tile plays that video", () => {
  assert.match(src, /data-a="mode" data-v="\$\{esc\(m\.id\)\}"\$\{m\.lights_only \? "" : ` data-hold="edit"`\}/);
  assert.match(src, /if \(el\.dataset\.hold === "edit"\) return this\._openEditor\(el\.dataset\.v\);/);
  assert.match(src, /case "vid": return this\._call\("ambience_start", \{ mode: b\.dataset\.m, video: v \}\);/);
});

test("mode tiles keep a pencil next to the tile (a sibling, so holding the tile still works)", () => {
  assert.match(src, /<\/button>\$\{m\.lights_only \? "" : `<button class="tvedit" data-a="edit" data-v="\$\{esc\(m\.id\)\}"/);
});
