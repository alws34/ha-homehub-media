import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const html = readFileSync(
  new URL("../../custom_components/homehub_media/www/ambience.html", import.meta.url),
  "utf8",
);
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const ORIGIN = "http://ha:8123";
const SIG = "/api/homehub_media/page/state?authSig=s0";
const drain = async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r)); };

function fakeEl(hidden = false) {
  return {
    hidden, loop: false, muted: false, volume: 1, currentTime: 0, duration: NaN,
    srcSets: 0, plays: 0, _src: "", listeners: {}, rejectPlay: null,
    get src() { return this._src; },
    set src(v) { this._src = v; this.srcSets++; },
    addEventListener(t, f) { (this.listeners[t] ||= []).push(f); },
    removeAttribute(n) { if (n === "src") this._src = ""; },
    load() {}, pause() { this.paused = true; },
    play() {
      this.plays++; this.paused = false;
      const e = this.rejectPlay; this.rejectPlay = null;
      return e ? Promise.reject(e) : Promise.resolve();
    },
    fire(t) { (this.listeners[t] || []).forEach((f) => f()); },
  };
}

function load({ search = "", fetchImpl } = {}) {
  const els = { v: fakeEl(), a: fakeEl(), hint: fakeEl(true) };
  const docL = {}, timers = [], raf = [], fetched = [];
  const clock = { now: 0 };
  const ctx = {
    URL, URLSearchParams,
    document: {
      getElementById: (id) => els[id],
      addEventListener: (t, f) => (docL[t] ||= []).push(f),
    },
    location: { origin: ORIGIN, search, href: `${ORIGIN}/homehub_media_static/ambience.html${search}` },
    fetch: (u, o) => { fetched.push(u); return (fetchImpl || (() => new Promise(() => {})))(u, o); },
    setTimeout: (f, ms) => timers.push({ f, ms }),
    requestAnimationFrame: (f) => raf.push(f),
    Date: { now: () => clock.now },
  };
  ctx.window = ctx; ctx.__HOMEHUB_TEST__ = true;
  vm.createContext(ctx);
  vm.runInContext("Math.random = () => 0.5", ctx);
  vm.runInContext(script, ctx);
  return { t: ctx.__ambienceTest, els, docL, timers, raf, fetched, clock };
}

const vis = (url, extra = {}) => ({ url, loop: true, random_start: true, volume: 0.2, ...extra });
const st = (version, o = {}) => ({ version, stop: false, fade_secs: null, visual: null, soundtrack: null, ...o });

test("sameOrigin accepts only same-origin http(s) URLs", () => {
  const { t } = load();
  assert.equal(t.sameOrigin("http://evil/x"), false);
  assert.equal(t.sameOrigin("//evil/x"), false);
  assert.equal(t.sameOrigin("javascript:alert(1)"), false);
  assert.equal(t.sameOrigin("https://ha:8123/x"), false);
  assert.equal(t.sameOrigin("/api/x"), true);
  assert.equal(t.sameOrigin("http://ha:8123/x?authSig=1"), true);
});

test("clampVolume clamps to 0..1 and survives junk", () => {
  const { t } = load();
  assert.equal(t.clampVolume(1.7), 1);
  assert.equal(t.clampVolume(-1), 0);
  assert.equal(t.clampVolume(0.4), 0.4);
  assert.equal(t.clampVolume("0.5"), 0.5);
  assert.equal(t.clampVolume(undefined), 0);
  assert.equal(t.clampVolume("x"), 0);
});

test("https stream is allowed for soundtrack only", () => {
  const { t } = load();
  const radio = { url: "https://radio.example/live", stream: true };
  assert.equal(t.layerOk(radio, "soundtrack"), true);
  assert.equal(t.layerOk(radio, "visual"), false);
  assert.equal(t.layerOk({ url: "https://radio.example/live" }, "soundtrack"), false);
  assert.equal(t.layerOk({ url: "http://radio.example/live", stream: true }, "soundtrack"), false);
  assert.equal(t.layerOk({ url: "/media/a.mp3" }, "soundtrack"), true);
  assert.equal(t.layerOk({ url: "http://evil/a.mp3", stream: true }, "soundtrack"), false);
  assert.equal(t.layerOk(null, "visual"), false);
});

test("apply loads layers, reloads only when the URL changes (authSig ignored)", () => {
  const { t, els } = load();
  t.apply(st(1, {
    visual: vis("/media/fire.mp4?authSig=a"),
    soundtrack: { url: "https://radio.example/live", stream: true, loop: false, volume: 1.4 },
  }));
  assert.equal(els.v.src, `${ORIGIN}/media/fire.mp4?authSig=a`);
  assert.equal(els.v.loop, true);
  assert.equal(els.v.volume, 0.2);
  assert.equal(els.a.src, "https://radio.example/live");
  assert.equal(els.a.volume, 1);
  assert.equal(els.a.loop, false);

  t.apply(st(2, {
    visual: vis("/media/fire.mp4?authSig=b", { volume: 0.5, loop: false }),
    soundtrack: { url: "https://radio.example/live", stream: true, volume: 0.3 },
  }));
  assert.equal(els.v.srcSets, 1);
  assert.equal(els.a.srcSets, 1);
  assert.equal(els.v.volume, 0.5);
  assert.equal(els.v.loop, false);
  assert.equal(els.a.volume, 0.3);

  t.apply(st(3, { visual: vis("/media/rain.mp4?authSig=c") }));
  assert.equal(els.v.srcSets, 2);
  assert.equal(els.v.src, `${ORIGIN}/media/rain.mp4?authSig=c`);
  assert.equal(els.a.src, "");
});

test("disallowed URLs are never loaded", () => {
  const { t, els } = load();
  t.apply(st(1, {
    visual: vis("https://radio.example/v.mp4", { stream: true }),
    soundtrack: { url: "http://evil/a.mp3", volume: 1 },
  }));
  assert.equal(els.v.srcSets, 0);
  assert.equal(els.a.srcSets, 0);
});

test("random_start seeks once per URL", () => {
  const { t, els } = load();
  t.apply(st(1, { visual: vis("/media/fire.mp4") }));
  els.v.duration = 100; els.v.fire("loadedmetadata");
  assert.equal(els.v.currentTime, 49.75);
  els.v.currentTime = 3; els.v.fire("loadedmetadata");
  assert.equal(els.v.currentTime, 3);
  t.apply(st(2, { visual: vis("/media/rain.mp4") }));
  els.v.currentTime = 0; els.v.fire("loadedmetadata");
  assert.equal(els.v.currentTime, 49.75);
  t.apply(st(3, { visual: vis("/media/x.mp4", { random_start: false }) }));
  els.v.currentTime = 0; els.v.fire("loadedmetadata");
  assert.equal(els.v.currentTime, 0);
});

test("stop pauses both and clears src", () => {
  const { t, els } = load();
  t.apply(st(1, { visual: vis("/media/fire.mp4"), soundtrack: vis("/media/a.mp3") }));
  t.apply(st(2, { stop: true, visual: vis("/media/fire.mp4") }));
  for (const e of [els.v, els.a]) { assert.equal(e.src, ""); assert.equal(e.paused, true); }
});

test("fade_secs ramps both volumes linearly to 0", () => {
  const { t, els, raf, clock } = load();
  t.apply(st(1, { visual: vis("/media/fire.mp4", { volume: 0.8 }), soundtrack: vis("/media/a.mp3", { volume: 0.4 }) }));
  clock.now = 1000;
  t.apply(st(2, { fade_secs: 10, visual: vis("/media/fire.mp4", { volume: 0.8 }), soundtrack: vis("/media/a.mp3", { volume: 0.4 }) }));
  clock.now = 6000; raf.shift()();
  assert.ok(Math.abs(els.v.volume - 0.4) < 1e-9);
  assert.ok(Math.abs(els.a.volume - 0.2) < 1e-9);
  t.apply(st(3, { fade_secs: 10, visual: vis("/media/fire.mp4", { volume: 0.8 }), soundtrack: vis("/media/a.mp3", { volume: 0.4 }) })); // mid-fade update must not undo the ramp
  assert.ok(Math.abs(els.v.volume - 0.4) < 1e-9);
  clock.now = 11000; raf.shift()();
  assert.equal(els.v.volume, 0);
  assert.equal(els.a.volume, 0);
  assert.equal(raf.length, 0);
});

test("rejected unmuted autoplay falls back to muted until first key", async () => {
  const { t, els, docL } = load();
  els.v.rejectPlay = { name: "NotAllowedError" };
  t.apply(st(1, { visual: vis("/media/fire.mp4"), soundtrack: vis("/media/a.mp3") }));
  await drain();
  assert.equal(els.v.muted, true);
  assert.equal(els.v.plays, 2);
  assert.equal(els.hint.hidden, false);
  docL.keydown[0]();
  assert.equal(els.v.muted, false);
  assert.equal(els.a.muted, false);
  assert.equal(els.hint.hidden, true);
});

test("an interrupted play (AbortError) does not mute", async () => {
  const { t, els } = load();
  els.v.rejectPlay = { name: "AbortError" };
  t.apply(st(1, { visual: vis("/media/fire.mp4") }));
  await drain();
  assert.equal(els.v.muted, false);
  assert.equal(els.hint.hidden, true);
});

test("polling: uses signed next, backs off to 10 s on errors, recovers to 1 s", async () => {
  let mode = "fail", n = 0;
  const { timers, fetched, els } = load({
    search: `?s=${encodeURIComponent(SIG)}`,
    fetchImpl: () => {
      if (mode === "fail") return Promise.reject(new Error("net"));
      n++;
      return Promise.resolve({ ok: true, json: async () => st(n, { visual: vis("/media/fire.mp4"), next: n === 1 ? "http://evil/x" : `/api/homehub_media/page/state?authSig=s${n}` }) });
    },
  });
  const step = async () => { await drain(); const x = timers.shift(); x.f(); return x.ms; };
  assert.equal(fetched[0], SIG);
  const delays = [];
  for (let i = 0; i < 6; i++) delays.push(await step());
  assert.deepEqual(delays, [2000, 4000, 8000, 10000, 10000, 10000]);
  mode = "ok";
  assert.equal(await step(), 10000);          // timer scheduled while still failing
  assert.equal(await step(), 1000);           // success resets the delay
  assert.equal(fetched.at(-1), SIG);          // evil `next` ignored
  await step();
  assert.equal(fetched.at(-1), "/api/homehub_media/page/state?authSig=s2");
  assert.equal(els.v.srcSets, 1);             // versions 1 and 2 carry the same URL: one load
});

test("no polling without a same-origin s parameter", () => {
  assert.equal(load().fetched.length, 0);
  assert.equal(load({ search: "?s=http%3A%2F%2Fevil%2Fx" }).fetched.length, 0);
});
