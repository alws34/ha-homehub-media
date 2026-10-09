/* HomeHub Media card: remote, ambience and library for the homehub_media integration.
 * Vanilla web component, no build step, no third-party cards.
 * Backend contracts only: websocket homehub_media/subscribe (snapshot), homehub_media/catalog,
 * homehub_media/library, and hass.callService("homehub_media", ...).
 * Every dynamic string goes through esc(); image URLs must be same-origin (safeUrl). */

const TAG = "homehub-media-card";
const DOMAIN = "homehub_media";
const DRAWER_KEY = "homehub-media-card:drawer";
const TABS = {
  remote: ["Remote", "mdi:remote-tv"],
  ambience: ["Ambience", "mdi:weather-night"],
  library: ["Library", "mdi:filmstrip-box-multiple"],
};
// short label, accessible label, icon
const TARGETS = {
  tv: ["TV", "TV", "mdi:television"],
  box: ["Box", "Streaming box", "mdi:set-top-box"],
  pc: ["PC", "Media PC", "mdi:desktop-tower-monitor"],
};
// app tile [background, text]: brand colours, contrast-checked against the text colour
const BRANDS = {
  netflix: ["#000", "#f5141f"],  // a touch lighter than brand red for 5:1 contrast
  youtube: ["#e62117", "#fff"],
  prime_video: ["#0f171e", "#00a8e1"],
  disney_plus: ["linear-gradient(135deg,#0b1647,#1a3a9e)", "#fff"],
  apple_tv: ["#1c1c1e", "#fff"],
  max: ["#002be7", "#fff"],
  plex: ["#1f1f1f", "#e5a00d"],
  jellyfin: ["linear-gradient(135deg,#7b3fa0,#0b6f9e)", "#fff"],
  spotify: ["#1db954", "#0b0b0b"],
  twitch: ["#9146ff", "#fff"],
  paramount_plus: ["#0064ff", "#fff"],
};
const COMP_DEFAULTS = {
  visual: null, visual_enabled: false, video_loop: true, video_random_start: true, original_volume: 20,
  soundtrack: null, soundtrack_loop: true, soundtrack_random_start: true, soundtrack_volume: 100,
};
const ARROWS = { ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right" };

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const mixToVolumes = (v) => {
  const s = clamp(Math.round(Number(v) || 0), 0, 100);
  return { original_volume: 100 - s, soundtrack_volume: s };
};
const nextTarget = (current, targets, dir) => {
  const list = ["tv", ...["box", "pc"].filter((t) => targets?.[t])];
  const i = list.indexOf(current);
  return i < 0 ? "tv" : list[(i + dir + list.length) % list.length];
};
const safeUrl = (u) => {
  if (!u) return null;
  try {
    const x = new URL(u, location.origin);
    return x.origin === location.origin ? x.href : null;
  } catch {
    return null;
  }
};
const pretty = (s) => String(s ?? "").replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
const icon = (name) => `<ha-icon icon="${esc(name)}"></ha-icon>`;
const img = (u, cls = "") => {
  const src = safeUrl(u);
  return src ? `<img${cls ? ` class="${cls}"` : ""} src="${esc(src)}" alt="" loading="lazy">` : "";
};
const pad2 = (n) => String(n).padStart(2, "0");
const fmtTime = (sec) => {
  const s = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}:${pad2(m)}:${pad2(s % 60)}` : `${m}:${pad2(s % 60)}`;
};
const fmtMins = (m) => (m >= 60 ? `${Math.floor(m / 60)} h ${pad2(m % 60)} min` : `${m} min`);
const hint = (text, ic = "mdi:information-outline") => `<p class="hint">${icon(ic)}<span>${esc(text)}</span></p>`;
const banner = (text, ic = "mdi:alert-circle-outline", cls = "banner err") => `<div class="${cls}">${icon(ic)}<span>${esc(text)}</span></div>`;

class HomeHubMediaCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._slices = {};
    this._tab = "remote";
    this._browse = {};
    this._pointers = new Map();
    this._mv = { dx: 0, dy: 0 };
    this._sc = 0;
    this._channel = "";
    this._noClickUntil = 0;
    this._stopHoldB = () => this._stopHold();
    const r = this.shadowRoot;
    r.addEventListener("click", (e) => this._onClick(e));
    r.addEventListener("pointerdown", (e) => this._onDown(e));
    r.addEventListener("pointermove", (e) => this._onMove(e));
    r.addEventListener("pointerup", (e) => this._onUp(e));
    r.addEventListener("pointercancel", (e) => this._onUp(e));
    r.addEventListener("keydown", (e) => this._onKey(e));
    r.addEventListener("input", (e) => this._onInput(e));
    r.addEventListener("contextmenu", (e) => {
      if (e.target.closest?.("[data-hold],[data-touch]")) e.preventDefault();
    });
    r.addEventListener("wheel", (e) => {
      if (!e.target.closest?.("[data-touch]") || this._eff() !== "pc") return;
      e.preventDefault();
      this._queueScroll(e.deltaY * 0.72);
    }, { passive: false });
    // broken artwork falls back to the icon underneath
    r.addEventListener("error", (e) => { if (e.target.tagName === "IMG") e.target.remove(); }, true);
  }

  static getStubConfig() { return {}; }
  // drawer: the host is position:fixed, so an "auto" row collapses to nothing ("rows: 0" would emit invalid `span 0`)
  getGridOptions() { return this._config?.drawer ? { columns: 12, rows: "auto" } : { columns: 12, min_columns: 6, rows: "auto" }; }
  getCardSize() { return this._config?.drawer ? 0 : 8; }

  setConfig(config) {
    const tabs = config?.tabs ?? Object.keys(TABS);
    if (!Array.isArray(tabs) || !tabs.length || tabs.some((t) => !TABS[t])) {
      throw new Error("tabs: use any of remote, ambience, library");
    }
    if (config.target != null && !TARGETS[config.target]) throw new Error("target: use tv, box or pc");
    const def = config.default_tab ?? "remote";
    if (!TABS[def]) throw new Error("default_tab: use remote, ambience or library");
    this._config = { ...config, drawer: !!config.drawer, tabs };
    this._tab = tabs.includes(def) ? def : tabs[0];
    this._open = false;
    try { this._open = localStorage.getItem(DRAWER_KEY) === "open"; } catch { /* storage blocked */ }
    this.toggleAttribute("drawer", this._config.drawer);
    this._built = false;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._subscribe();
  }

  get hass() { return this._hass; }

  connectedCallback() {
    this._retryAt = 0;  // re-adding the card retries a failed subscribe right away
    this._subscribe();
    this._render();
    clearInterval(this._ticker);
    this._ticker = setInterval(() => this._tick(), 1000);
  }

  disconnectedCallback() {
    clearInterval(this._ticker);
    clearTimeout(this._chTimer);
    clearTimeout(this._mvTimer);
    clearTimeout(this._scTimer);
    this._mvTimer = this._scTimer = this._swipe = null;
    this._mv = { dx: 0, dy: 0 };
    this._sc = 0;
    this._pointers.clear();
    this._stopHold();
    if (this._edit) this._flushLive(this._edit);
    this._unsubscribe();
  }

  // ---------- data ----------

  async _subscribe() {
    // hass is set on every HA state change: after a failure wait 30 s instead of retrying each time
    if (this._sub || !this._hass?.connection || !this.isConnected || Date.now() < (this._retryAt || 0)) return;
    const sub = this._hass.connection.subscribeMessage((e) => this._onSnapshot(e), { type: `${DOMAIN}/subscribe` });
    this._sub = sub;  // the pending promise; _unsubscribe() resolves it, even mid-subscribe
    try {
      await sub;
    } catch (err) {
      if (this._sub !== sub) return;  // a stale attempt from before a disconnect/reconnect
      this._sub = null;
      this._retryAt = Date.now() + 30000;
      this._fatal = err?.code === "unknown_command"
        ? "HomeHub Media is not set up. Add the integration first."
        : `HomeHub Media is unavailable (${err?.message || "connection error"}).`;
      this._render();
    }
  }

  _unsubscribe() {
    const sub = this._sub;
    this._sub = null;
    sub?.then((unsub) => unsub()).catch(() => {});
  }

  _onSnapshot(snap) {
    const first = !this._snap;
    this._snap = snap;
    this._snapAt = Date.now();
    this._fatal = null;
    this._render();
    if (first) this._loadFor(this._tab);
  }

  _loadFor(tab) {
    if (tab === "ambience") this._load("catalog");
    if (tab === "library") this._load("library");
  }

  // kind "catalog" -> this._catalog / this._catalogErr, "library" -> this._library / this._libraryErr
  async _load(kind) {
    if (!this._hass) return;
    try {
      this[`_${kind}`] = await this._hass.callWS({ type: `${DOMAIN}/${kind}` });
      this[`_${kind}Err`] = null;
      this._catVer = (this._catVer || 0) + 1;  // cheap identity for the editor slice
    } catch (err) {
      this[`_${kind}Err`] = err?.message || `Could not load the ${kind}`;
    }
    this._render();
  }

  async _call(service, data = {}) {
    if (!this._hass) return false;
    try {
      await this._hass.callService(DOMAIN, service, data);
      return true;
    } catch (err) {
      this._toast(err?.message || `${pretty(service)} failed`);
      return false;
    }
  }

  _toast(message) {
    const now = Date.now();
    if (message === this._lastToast && now - this._lastToastAt < 3000) return;  // no spam from pointer batches
    this._lastToast = message;
    this._lastToastAt = now;
    this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message }, bubbles: true, composed: true }));
  }

  // ---------- state helpers ----------

  _tabs() {
    // before the first snapshot nothing is known yet, so keep default_tab
    return this._config.tabs.filter((t) => t !== "library" || !this._snap || this._snap.servers?.length);
  }

  _eff() {
    const s = this._snap;
    const act = s?.activity?.current;
    const t = this._target || this._config?.target || (TARGETS[act] ? act : "tv");
    return t === "tv" || s?.targets?.[t] ? t : "tv";
  }

  _setTab(tab) {
    if (!TABS[tab]) return;
    this._tab = tab;
    this._render();
    this._loadFor(tab);
  }

  _setTarget(t) {
    this._target = t;
    this._channel = "";
    this._render();
  }

  _remote(command, target = this._eff()) {
    return this._call("remote", { command, target });
  }

  _textTarget() {
    const t = this._eff();
    if (t === "pc") return "pc";
    if (t === "box") return "keyboard";
    return this._snap?.targets?.keyboard ? undefined : "tv";  // undefined: hub picks TV IME or keyboard bridge
  }

  _kbd(service, data) {
    const target = this._textTarget();
    return this._call(service, target ? { ...data, target } : data);
  }

  _toggleDrawer(open = !this._open) {
    this._open = open;
    try { localStorage.setItem(DRAWER_KEY, open ? "open" : "closed"); } catch { /* storage blocked */ }
    const d = this.shadowRoot.querySelector(".drawer");
    if (!d) return;
    d.dataset.open = open;
    const h = d.querySelector(".handle");
    h.setAttribute("aria-expanded", open);
    h.setAttribute("aria-label", `${open ? "Close" : "Open"} media controls`);
    d.querySelector(".panel").inert = !open;
  }

  // ---------- rendering ----------

  _sec(key, slice, html) {
    const el = this.shadowRoot.querySelector(`[data-s="${key}"]`);
    if (!el) return false;
    const s = JSON.stringify(slice);
    if (this._slices[key] === s) return false;
    this._slices[key] = s;
    el.innerHTML = html();
    return true;
  }

  _build() {
    const inner = `<div class="wrap">
        <div data-s="banner"></div>
        <div data-s="np"></div>
        <div class="tabs" role="tablist" aria-label="HomeHub Media" data-s="tabs"></div>
        <div class="body" id="panel" data-s="body"></div>
      </div>`;
    const open = !!this._open;
    this.shadowRoot.innerHTML = `<style>${CSS}</style>${this._config.drawer
      ? `<div class="drawer" data-open="${open}">
          <button class="handle" data-a="drawer" aria-expanded="${open}" aria-label="${open ? "Close" : "Open"} media controls">${icon("mdi:chevron-left")}</button>
          <ha-card class="panel"${open ? "" : " inert"}>${inner}</ha-card>
        </div>`
      : `<ha-card>${inner}</ha-card>`}`;
    this._slices = {};
    this._built = true;
  }

  _render() {
    if (!this._config || !this.isConnected) return;
    if (!this._built) this._build();
    const s = this._snap;
    const tabs = this._tabs();
    if (!tabs.includes(this._tab)) this._tab = tabs[0];
    if (this._edit && !s?.ambience?.modes?.some((m) => m.id === this._edit.id)) this._closeEditor(false);

    this._sec("banner", [this._fatal, s?.tv?.available], () => {
      if (this._fatal) return banner(this._fatal);
      if (s && s.tv?.available === false) return banner("TV is off or unreachable", "mdi:television-off", "banner");
      return "";
    });
    this._sec("np", (s?.now_playing ?? []).slice(0, 1).map(({ position, ...rest }) => rest), () => this._npHtml());
    this._tickNp();
    this._sec("tabs", [tabs.length > 1 && tabs, this._tab], () => (tabs.length < 2 ? "" : tabs.map((t) => {
      const on = t === this._tab;
      return `<button class="tab" role="tab" id="tab-${t}" aria-controls="panel" data-a="tab" data-v="${t}" aria-selected="${on}" tabindex="${on ? 0 : -1}">${icon(TABS[t][1])}<span>${TABS[t][0]}</span></button>`;
    }).join("")));
    const body = this.shadowRoot.querySelector(".body");
    if (tabs.length > 1) {
      body.setAttribute("role", "tabpanel");
      body.setAttribute("aria-labelledby", `tab-${this._tab}`);
    } else {
      body.removeAttribute("role");
      body.removeAttribute("aria-labelledby");
    }
    if (this._sec("body", [this._tab, !!s, !!this._fatal], () => this._bodyHtml())) {
      for (const k of Object.keys(this._slices)) if (!["banner", "np", "tabs", "body"].includes(k)) delete this._slices[k];
    }
    if (!s) return;
    if (this._tab === "remote") this._renderRemote();
    else if (this._tab === "ambience") this._renderAmbience();
    else this._renderLibrary();
  }

  _bodyHtml() {
    if (!this._snap) return this._fatal ? "" : `<div class="skel"></div><div class="skel short"></div>`;
    if (this._tab === "ambience") return `<div data-s="amberr"></div><div data-s="ambmain"></div><div data-s="sleep"></div>`;
    if (this._tab === "library") return `<div class="lib" data-s="lib"></div>`;
    return `<div class="remote">
      <div class="col">
        <div class="pills" role="radiogroup" aria-label="Control" data-s="targets"></div>
        <div class="row">
          <button class="ib power" data-a="remote" data-v="power" aria-label="Power">${icon("mdi:power")}</button>
          <span class="grow"></span>
          <button class="ib" data-a="remote" data-v="back" aria-label="Back">${icon("mdi:arrow-u-left-top")}</button>
          <button class="ib" data-a="remote" data-v="home" aria-label="Home">${icon("mdi:home-outline")}</button>
        </div>
        <div class="pad" data-s="pad"></div>
        <div data-s="padhint"></div>
        <div class="ctl" data-s="ctl"></div>
      </div>
      <div class="col">
        <div data-s="apps"></div>
        <div class="kbdwrap" data-kbd>
          <div class="kbd">${icon("mdi:keyboard-outline")}
            <input data-kbdin type="text" maxlength="200" enterkeyhint="send" autocomplete="off" autocorrect="off"
              autocapitalize="off" spellcheck="false" aria-label="Type text">
            <button class="ib" data-a="kclear" aria-label="Clear text">${icon("mdi:close")}</button>
          </div>
          <div data-s="kbdhint"></div>
        </div>
        <div data-s="extra"></div>
      </div>
    </div>`;
  }

  _npHtml() {
    const n = this._snap?.now_playing?.[0];
    if (!n) return "";
    const playing = n.state === "playing";
    const sub = [n.subtitle, n.player].filter(Boolean).join(" · ");
    // no target: the hub routes these by the current activity
    return `<div class="np">
      ${img(n.image, "npbg")}
      <div class="npart">${icon("mdi:movie-open-outline")}${img(n.image)}</div>
      <div class="npmeta">
        <b>${esc(n.title)}</b><small>${esc(sub || pretty(n.server))}</small>
        <div class="npbar"${n.duration ? "" : " hidden"}><span class="npfill"></span></div>
        <div class="nptime"><span data-npt></span><span>${n.duration ? fmtTime(n.duration) : ""}</span></div>
      </div>
      <div class="npbtns">
        <button class="ib flat" data-a="np" data-v="rewind" aria-label="Back 30 seconds">${icon("mdi:rewind-30")}</button>
        <button class="ib accent" data-a="np" data-v="${playing ? "pause" : "play"}" aria-label="${playing ? "Pause" : "Play"}">${icon(playing ? "mdi:pause" : "mdi:play")}</button>
        <button class="ib flat" data-a="np" data-v="fast_forward" aria-label="Forward 30 seconds">${icon("mdi:fast-forward-30")}</button>
      </div>
    </div>`;
  }

  _tickNp() {
    const n = this._snap?.now_playing?.[0];
    if (!n) return;
    const live = n.state === "playing" ? (Date.now() - this._snapAt) / 1000 : 0;
    const pos = Math.min(n.duration || Infinity, (n.position || 0) + live);
    const fill = this.shadowRoot.querySelector(".npfill");
    if (fill && n.duration) fill.style.width = `${clamp((pos / n.duration) * 100, 0, 100)}%`;
    const t = this.shadowRoot.querySelector("[data-npt]");
    if (t) t.textContent = fmtTime(pos);
  }

  _tick() {
    this._tickNp();
    if (this._snap?.ambience?.sleep_until && this._tab === "ambience") this._render();
  }

  // ---------- remote tab ----------

  _renderRemote() {
    const s = this._snap;
    const tg = s.targets || {};
    const t = this._eff();
    const avail = ["tv", ...["box", "pc"].filter((k) => tg[k])];
    this._sec("targets", [avail, t], () => (avail.length < 2 ? "" : avail.map((k) => {
      const [label, aria, ic] = TARGETS[k];
      return `<button class="pill" role="radio" aria-checked="${k === t}" tabindex="${k === t ? 0 : -1}" aria-label="${aria}" data-a="target" data-v="${k}">${icon(ic)}<span>${label}</span></button>`;
    }).join("")));

    const canTouch = t !== "box";
    const tp = this._touchpad && canTouch;
    this._sec("pad", [tp, t === "pc", avail.length > 1], () => (tp ? this._touchHtml(t === "pc") : this._dpadHtml(avail.length > 1)));
    this._sec("padhint", [tp && t === "tv" && !s.tv?.pointer_active], () => (
      tp && t === "tv" && !s.tv?.pointer_active ? hint("Pointer works in the browser") : ""));
    this._sec("ctl", [!!s.tv?.muted, tp, canTouch], () => this._ctlHtml(!!s.tv?.muted, tp, canTouch));
    const vol = this.shadowRoot.querySelector("[data-vol]");
    if (vol) vol.textContent = s.tv?.volume ?? "–";

    const apps = (s.apps || []).filter((a) => a.installed);
    this._sec("apps", [apps, s.tv?.app], () => (apps.length ? `<h3 class="sh">Apps</h3><div class="apps">${apps.map((a) => {
      const [bg, fg] = BRANDS[a.key] || [];
      const on = a.key === s.tv?.app;
      return `<button class="app${on ? " on" : ""}"${bg ? ` style="--b:${bg};--f:${fg}"` : ""} data-a="app" data-v="${esc(a.key)}" data-hold="restart"
        aria-label="${esc(a.name)}${on ? ", open now" : ""}. Long-press to restart">${esc(a.name)}</button>`;
    }).join("")}</div>` : ""));

    const kbd = this.shadowRoot.querySelector("[data-kbd]");
    if (kbd) {
      kbd.hidden = t === "box" && !tg.keyboard;
      kbd.querySelector("input").placeholder = `Type on the ${TARGETS[t][1]}`;
    }
    const imeHint = t === "tv" && !s.tv?.keyboard_active && !tg.keyboard;
    this._sec("kbdhint", [imeHint], () => (imeHint ? hint("Focus a search box on the TV first") : ""));
    this._sec("extra", [t, t === "pc" ? s.pc : null], () => (t === "box" ? this._boxHtml() : t === "pc" ? this._pcHtml() : ""));
  }

  _dpadHtml(swipe) {
    const k = (dir, ic, hold) => `<button class="dk ${dir}" tabindex="-1" data-a="remote" data-v="${dir}"${hold ? ` data-hold="repeat"` : ""} aria-label="${pretty(dir)}">${icon(ic)}</button>`;
    return `<div class="dpad" role="group" tabindex="0" ${swipe ? `data-swipe="target"` : ""}
        aria-label="Directional pad. Arrow keys move, Enter selects${swipe ? ". Swipe sideways to switch device" : ""}">
      ${k("up", "mdi:chevron-up", true)}${k("left", "mdi:chevron-left")}
      <button class="ok" tabindex="-1" data-a="remote" data-v="select" aria-label="OK">OK</button>
      ${k("right", "mdi:chevron-right")}${k("down", "mdi:chevron-down", true)}
    </div>`;
  }

  _touchHtml(pc) {
    return `<div class="touch" data-touch role="application" aria-label="Touchpad: drag to move the pointer, tap to click">
        ${icon("mdi:gesture-tap")}<span>Drag to move · tap to click${pc ? " · two fingers to scroll" : ""}</span>
      </div>
      <div class="clicks">
        <button class="btn" data-a="click" data-v="click">Click</button>
        ${pc ? `<button class="btn" data-a="click" data-v="right_click">Right click</button>` : ""}
      </div>`;
  }

  _ctlHtml(muted, tp, canTouch) {
    return `<div class="rocker" role="group" aria-label="Volume">
        <button data-a="remote" data-v="volume_down" data-hold="repeat" aria-label="Volume down">${icon("mdi:minus")}</button>
        <button class="mute${muted ? " on" : ""}" data-a="remote" data-v="mute" aria-pressed="${muted}" aria-label="Mute">${icon(muted ? "mdi:volume-off" : "mdi:volume-high")}<span data-vol></span></button>
        <button data-a="remote" data-v="volume_up" data-hold="repeat" aria-label="Volume up">${icon("mdi:plus")}</button>
      </div>
      <button class="ib" data-a="remote" data-v="play_pause" aria-label="Play or pause">${icon("mdi:play-pause")}</button>
      ${canTouch ? `<button class="ib${tp ? " on" : ""}" data-a="touchpad" aria-pressed="${tp}" aria-label="Touchpad">${icon("mdi:gesture-tap-hold")}</button>` : ""}`;
  }

  _boxHtml() {
    const key = (v, label, aria) => `<button class="key" data-a="digit" data-v="${v}"${aria ? ` aria-label="${aria}"` : ""}>${label}</button>`;
    return `<h3 class="sh">Channel <output class="chd" data-chd aria-live="polite">${esc(this._channel)}</output></h3>
      <div class="keypad">
        ${[1, 2, 3, 4, 5, 6, 7, 8, 9].map((d) => key(d, d)).join("")}
        ${key("del", icon("mdi:backspace-outline"), "Delete digit")}${key(0, 0)}${key("go", icon("mdi:check"), "Go to channel")}
      </div>
      <div class="chips center">
        <button class="chip" data-a="remote" data-v="channel_down" aria-label="Channel down">${icon("mdi:chevron-down")}CH</button>
        <button class="chip" data-a="remote" data-v="guide">${icon("mdi:television-guide")}Guide</button>
        <button class="chip" data-a="remote" data-v="channel_up" aria-label="Channel up">${icon("mdi:chevron-up")}CH</button>
      </div>`;
  }

  _pcHtml() {
    const pc = this._snap.pc || {};
    const clients = pc.clients || [];
    return `<h3 class="sh">On the media PC</h3>
      <div class="chips">
        ${clients.map((c) => `<button class="chip" data-a="pc_open" data-v="${esc(c)}" aria-pressed="${c === pc.active}">${esc(pretty(c))}</button>`).join("")}
        <button class="chip" data-a="pc_close"${pc.active ? "" : " disabled"}>${icon("mdi:exit-to-app")}Exit</button>
      </div>`;
  }

  _digit(v) {
    if (v === "go") return this._sendChannel();
    if (v === "del") this._channel = this._channel.slice(0, -1);
    else if (this._channel.length < 4) this._channel += v;
    clearTimeout(this._chTimer);
    if (this._channel) this._chTimer = setTimeout(() => this._sendChannel(), 1500);
    this._showChannel();
  }

  _sendChannel() {
    clearTimeout(this._chTimer);
    const number = this._channel;
    this._channel = "";
    this._showChannel();
    if (number) this._call("channel", { number });
  }

  _showChannel() {
    const d = this.shadowRoot.querySelector("[data-chd]");
    if (d) d.textContent = this._channel;
  }

  // ---------- touchpad (batching ported from homehub-media-hub-control-pad.js) ----------

  _pointer(action, extra = {}) {
    return this._call("pointer", { action, ...extra, target: this._eff() });
  }

  _tpDown(e, el) {
    e.preventDefault();
    if (e.isPrimary) this._pointers.clear();  // drop fingers whose pointerup we never saw
    el.setPointerCapture?.(e.pointerId);
    this._pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (this._pointers.size === 1) this._tap = { t: performance.now(), dist: 0 };
    else { this._tap = null; this._lastY = null; }
  }

  _tpMove(e) {
    const p = this._pointers.get(e.pointerId);
    e.preventDefault();
    const dx = e.clientX - p.x;
    const dy = e.clientY - p.y;
    p.x = e.clientX;
    p.y = e.clientY;
    if (this._tap) this._tap.dist += Math.abs(dx) + Math.abs(dy);
    if (this._pointers.size >= 2) {
      if (this._eff() !== "pc") return;
      let y = 0;
      this._pointers.forEach((q) => { y += q.y; });
      y /= this._pointers.size;
      if (this._lastY != null) this._queueScroll(-(y - this._lastY) * 1.2);
      this._lastY = y;
      return;
    }
    this._queueMove(dx * 1.15, dy * 1.15);
  }

  _tpUp(e) {
    this._pointers.delete(e.pointerId);
    if (this._pointers.size < 2) this._lastY = null;
    const tap = this._tap;
    if (!this._pointers.size && e.type === "pointerup" && tap && tap.dist < 8 && performance.now() - tap.t < 300) {
      this._tap = null;
      this._click("click");
    }
  }

  _queueMove(dx, dy) {
    this._mv.dx += clamp(dx, -120, 120);
    this._mv.dy += clamp(dy, -120, 120);
    if (!this._mvTimer) this._mvTimer = setTimeout(() => this._flushMove(), 36);
  }

  _flushMove() {
    this._mvTimer = null;
    if (this._mvBusy) return;  // one request in flight; the rest accumulates
    const { dx, dy } = this._mv;
    this._mv = { dx: 0, dy: 0 };
    if (Math.abs(dx) < 0.2 && Math.abs(dy) < 0.2) return;
    const r1 = (v) => Math.round(clamp(v, -160, 160) * 10) / 10;
    this._mvBusy = this._pointer("move", { dx: r1(dx), dy: r1(dy) }).finally(() => {
      this._mvBusy = null;
      if (Math.abs(this._mv.dx) >= 0.2 || Math.abs(this._mv.dy) >= 0.2) this._queueMove(0, 0);
    });
  }

  async _flushPendingMove() {
    if (this._mvTimer) {
      clearTimeout(this._mvTimer);
      this._flushMove();
    }
    await this._mvBusy;
    if (Math.abs(this._mv.dx) >= 0.2 || Math.abs(this._mv.dy) >= 0.2) {
      this._flushMove();
      await this._mvBusy;
    }
  }

  _queueScroll(dy) {
    this._sc += clamp(dy, -180, 180);
    if (!this._scTimer) this._scTimer = setTimeout(() => this._flushScroll(), 36);
  }

  _flushScroll() {
    this._scTimer = null;
    if (this._scBusy) return;
    const dy = this._sc;
    this._sc = 0;
    if (Math.abs(dy) < 0.2) return;
    this._scBusy = this._pointer("scroll", { dy: Math.round(clamp(dy, -240, 240) * 10) / 10 }).finally(() => {
      this._scBusy = null;
      if (Math.abs(this._sc) >= 0.2) this._queueScroll(0);
    });
  }

  async _click(action) {
    // the pointer must arrive before the click does
    await this._flushPendingMove();
    this._pointer(action);
  }

  // ---------- ambience tab ----------

  _ambSlice() {
    const a = this._snap.ambience || {};
    if (!this._edit) return ["grid", a.mode, a.modes, a.auto, a.daypart_mode];
    return ["edit", this._edit.id, this._edit.draft, this._browse, this._catVer, this._catalogErr,
      a.mode === this._edit.id, a.modes, this._refreshing];
  }

  _renderAmbience() {
    const a = this._snap.ambience || {};
    this._sec("amberr", [a.error], () => (a.error ? banner(a.error) : ""));
    this._sec("ambmain", this._ambSlice(), () => (this._edit ? this._editorHtml() : this._modesHtml()));
    const until = a.sleep_until ? new Date(typeof a.sleep_until === "number" ? a.sleep_until * 1000 : a.sleep_until).getTime() : null;
    const mins = until ? Math.max(0, Math.ceil((until - Date.now()) / 60000)) : null;
    this._sec("sleep", [mins], () => `<h3 class="sh">${icon("mdi:power-sleep")}Sleep timer${mins != null ? `<span class="note">Off in ${fmtMins(mins)}</span>` : ""}</h3>
      <div class="chips">
        ${[15, 30, 60, 90].map((m) => `<button class="chip" data-a="sleep" data-v="${m}" aria-label="Sleep in ${m} minutes">${m} min</button>`).join("")}
        ${mins != null ? `<button class="chip" data-a="sleep" data-v="0">${icon("mdi:close")}Cancel</button>` : ""}
      </div>`);
  }

  _modesHtml() {
    const a = this._snap.ambience || {};
    const modes = a.modes || [];
    const dp = modes.find((m) => m.id === a.daypart_mode);
    const badge = `<span class="badge">ON</span>`;
    const tile = (on, act, id, ic, name, sub, gear) => `<div class="mode${on ? " on" : ""}">
        <button class="mt" data-a="${act}"${id ? ` data-v="${esc(id)}"` : ""} aria-pressed="${on}">
          <span class="mi">${icon(ic)}</span>
          <span class="mx"><b>${esc(name)}</b><small>${on ? badge : ""}${esc(sub)}</small></span>
        </button>
        ${gear ? `<button class="gear" data-a="edit" data-v="${esc(id)}" aria-label="Edit ${esc(name)}">${icon("mdi:tune-variant")}</button>` : ""}
      </div>`;
    return `<h3 class="sh">Modes${a.mode && a.mode !== "off" ? `<button class="chip sm" data-a="amb_stop">${icon("mdi:stop")}Stop</button>` : ""}</h3>
      <div class="modes">
        ${tile(!!a.auto, "auto", null, "mdi:auto-fix", "Auto", dp ? dp.name : "Nothing scheduled")}
        ${modes.map((m) => tile(a.mode === m.id, "mode", m.id, m.icon || "mdi:lightbulb-group", m.name,
          m.lights_only ? "Lights only" : "", !m.lights_only)).join("")}
      </div>`;
  }

  _openEditor(id) {
    const saved = this._snap.ambience?.compositions?.[id] || {};
    const draft = { ...COMP_DEFAULTS, ...saved };
    if (!("visual_enabled" in saved)) draft.visual_enabled = draft.visual != null;
    this._edit = { id, draft, saved: JSON.stringify(draft), pending: null };
    this._browse = { visual: draft.visual, soundtrack: draft.soundtrack };
    if (!this._catalog) this._load("catalog");
    this._render();
    this.shadowRoot.querySelector("[data-a=edclose]")?.focus();
  }

  _closeEditor(render = true) {
    if (this._edit) this._flushLive(this._edit);
    this._edit = null;
    if (render) this._render();
  }

  _editSet(patch, render = true) {
    const ed = this._edit;
    Object.assign(ed.draft, patch);
    if (this._snap.ambience?.mode === ed.id) {  // live: the edited mode is playing
      ed.pending = { ...ed.pending, ...patch };
      clearTimeout(ed.timer);
      ed.timer = setTimeout(() => this._flushLive(ed), 200);
    }
    if (render) this._render();
  }

  _flushLive(ed) {
    clearTimeout(ed.timer);
    if (!ed.pending) return;
    this._call("ambience_update", ed.pending);
    ed.pending = null;
  }

  _list(kind) {
    const items = this._catalog?.[kind === "visual" ? "visuals" : "soundtracks"] || [];
    return [...items].sort((x, y) => (y.favorite ? 1 : 0) - (x.favorite ? 1 : 0));  // favourites first
  }

  _cur(kind) {
    const list = this._list(kind);
    const i = Math.max(0, list.findIndex((x) => x.id === this._browse[kind]));
    return { list, i, item: list[i] };
  }

  _browseBy(kind, d) {
    const { list, i } = this._cur(kind);
    if (list.length < 2) return;
    this._browse[kind] = list[(i + d + list.length) % list.length].id;
    this._render();
  }

  _pick(kind) {
    const { item } = this._cur(kind);
    if (!item) return;
    this._browse[kind] = item.id;
    this._editSet(kind === "visual" ? { visual: item.id, visual_enabled: true } : { soundtrack: item.id });
  }

  async _fav(kind) {
    const { item } = this._cur(kind);
    if (!item) return;
    this._browse[kind] = item.id;
    const flip = () => { item.favorite = !item.favorite; this._catVer++; this._render(); };
    flip();
    if (!(await this._call("set_favorite", { kind, media_id: item.id, favorite: item.favorite }))) flip();
  }

  async _refreshCatalog() {
    this._refreshing = true;
    this._render();
    if (await this._call("refresh_catalog")) await this._load("catalog");
    this._refreshing = false;
    this._render();
  }

  async _save() {
    const ed = this._edit;
    const name = this._snap.ambience?.modes?.find((m) => m.id === ed.id)?.name || pretty(ed.id);
    if (await this._call("save_mode", { mode: ed.id, ...ed.draft })) {
      ed.saved = JSON.stringify(ed.draft);
      this._toast(`Saved ${name}`);
    }
  }

  async _start() {
    const ed = this._edit;
    const draft = { ...ed.draft };
    // unsaved edits are applied live on top of the saved mode; Save keeps them
    if (await this._call("ambience_start", { mode: ed.id }) && JSON.stringify(draft) !== ed.saved) {
      this._call("ambience_update", draft);
    }
  }

  _carouselHtml(kind) {
    const label = kind === "visual" ? "Visual" : "Soundtrack";
    if (this._catalogErr && !this._catalog) return `<div class="car-empty">${icon("mdi:alert-circle-outline")}${esc(this._catalogErr)}</div>`;
    if (!this._catalog) return `<div class="skel ${kind}"></div>`;
    const { list, i, item } = this._cur(kind);
    if (!item) return `<div class="car-empty">${icon("mdi:folder-open-outline")}No ${label.toLowerCase()}s yet. Add files and refresh.</div>`;
    const d = this._edit.draft;
    const sel = (kind === "visual" ? d.visual : d.soundtrack) === item.id;
    const meta = [`${i + 1} / ${list.length}`, item.live ? "Live" : item.duration ? fmtTime(item.duration) : ""].filter(Boolean).join(" · ");
    const nav = (dir, ic, aria) => `<button class="nav ${dir}" data-a="browse" data-v="${kind}" data-d="${dir === "prev" ? -1 : 1}" aria-label="${aria} ${label.toLowerCase()}"${list.length < 2 ? " disabled" : ""}>${icon(ic)}</button>`;
    return `<div class="car ${kind}${sel ? " sel" : ""}" data-swipe="${kind}" tabindex="0" role="group" aria-roledescription="carousel"
        aria-label="${label}: ${esc(item.title)}, ${i + 1} of ${list.length}${sel ? ", selected" : ""}. Arrow keys browse, Enter selects">
      ${nav("prev", "mdi:chevron-left", "Previous")}
      <button class="slide" data-a="pick" data-v="${kind}" tabindex="-1" aria-pressed="${sel}" aria-label="Use ${esc(item.title)}">
        <span class="art"><span class="ph">${icon(kind === "visual" ? "mdi:image-filter-hdr" : "mdi:music-note")}</span>${img(item.preview)}</span>
        <span class="cap"><b>${esc(item.title)}</b><small>${esc(meta)}</small></span>
        ${sel ? `<span class="check">${icon("mdi:check")}</span>` : ""}
      </button>
      <button class="fav${item.favorite ? " on" : ""}" data-a="fav" data-v="${kind}" aria-pressed="${!!item.favorite}" aria-label="Favourite ${esc(item.title)}">${icon(item.favorite ? "mdi:star" : "mdi:star-outline")}</button>
      ${nav("next", "mdi:chevron-right", "Next")}
    </div>`;
  }

  _editorHtml() {
    const a = this._snap.ambience || {};
    const ed = this._edit;
    const d = ed.draft;
    const mode = a.modes.find((m) => m.id === ed.id);
    const live = a.mode === ed.id;
    const mix = clamp(100 - (Number(d.original_volume) || 0), 0, 100);
    const chip = (act, field, on, ic, label, aria) => `<button class="chip" data-a="${act}" data-v="${field}" aria-pressed="${!!on}"${aria ? ` aria-label="${aria}"` : ""}>${icon(ic)}${label}</button>`;
    return `<div class="edhead">
        <button class="ib flat" data-a="edclose" aria-label="Back to modes">${icon("mdi:arrow-left")}</button>
        <span class="mi on">${icon(mode.icon || "mdi:lightbulb-group")}</span>
        <div class="edtitle"><b>${esc(mode.name)}</b><small>${live ? "Live: changes apply right away" : "Pick a visual and a soundtrack"}</small></div>
        <button class="ib flat${this._refreshing ? " spin" : ""}" data-a="refresh" aria-label="Rescan media">${icon("mdi:refresh")}</button>
      </div>
      <section class="edsec">
        <h3 class="sh">${icon("mdi:image-outline")}Visual</h3>
        ${this._carouselHtml("visual")}
        <div class="chips">
          ${chip("vis", "visual_enabled", d.visual_enabled, d.visual_enabled ? "mdi:eye-outline" : "mdi:eye-off-outline", "On", "Show visual")}
          ${chip("tog", "video_loop", d.video_loop, "mdi:repeat", "Loop")}
          ${chip("tog", "video_random_start", d.video_random_start, "mdi:shuffle-variant", "Random start")}
        </div>
      </section>
      <section class="edsec">
        <h3 class="sh">${icon("mdi:music-note-outline")}Soundtrack</h3>
        ${this._carouselHtml("soundtrack")}
        <div class="chips">
          ${chip("audio", "soundtrack", d.soundtrack != null, d.soundtrack != null ? "mdi:volume-high" : "mdi:volume-off", "On", "Play soundtrack")}
          ${chip("tog", "soundtrack_loop", d.soundtrack_loop, "mdi:repeat", "Loop")}
          ${chip("tog", "soundtrack_random_start", d.soundtrack_random_start, "mdi:shuffle-variant", "Random start")}
        </div>
      </section>
      <section class="edsec mix">
        <h3 class="sh">${icon("mdi:tune-vertical-variant")}Mix</h3>
        <input type="range" min="0" max="100" step="1" value="${mix}" style="--p:${mix}%" data-mix
          aria-label="Mix between the visual's own sound and the soundtrack" aria-valuetext="${100 - mix}% ambience, ${mix}% soundtrack">
        <div class="mixlab"><span>Ambience <b data-mixa>${100 - mix}%</b></span><span>Soundtrack <b data-mixs>${mix}%</b></span></div>
      </section>
      <div class="actions">
        <button class="btn" data-a="save">${icon("mdi:content-save-outline")}Save</button>
        ${live
          ? `<button class="btn primary" data-a="amb_stop">${icon("mdi:stop")}Stop</button>`
          : `<button class="btn primary" data-a="start">${icon("mdi:play")}Start</button>`}
      </div>`;
  }

  // ---------- library tab ----------

  _renderLibrary() {
    this._sec("lib", [this._library, this._libraryErr, this._snap.servers], () => {
      if (this._libraryErr && !this._library) return banner(this._libraryErr);
      if (!this._library) return `<div class="skel shelf-skel"></div><div class="skel shelf-skel"></div>`;
      const out = (this._snap.servers || []).map((srv) => {
        const d = this._library[srv];
        if (!d) return "";
        const rows = this._shelf(srv, "Continue watching", d.continue, true) + this._shelf(srv, "Recently added", d.recent, false);
        return `<section class="srv"><h3 class="sh srvh">${icon(srv === "plex" ? "mdi:plex" : "mdi:jellyfish-outline")}${esc(pretty(srv))}</h3>
          ${rows || `<p class="hint">${icon("mdi:information-outline")}<span>Nothing here yet</span></p>`}</section>`;
      }).join("");
      return out || hint("No library servers responded");
    });
  }

  _shelf(srv, label, items, progress) {
    if (!items?.length) return "";
    return `<h4 class="rowlab">${label}</h4><div class="shelf" role="list">${items.map((it) => {
      const p = progress && it.progress != null ? Math.round(clamp(Number(it.progress) || 0, 0, 1) * 100) : null;
      return `<button class="poster" role="listitem" data-a="play" data-srv="${esc(srv)}" data-v="${esc(it.id)}"
          aria-label="Play ${esc(it.title)}${it.subtitle ? `, ${esc(it.subtitle)}` : ""}${p != null ? `, ${p}% watched` : ""}">
        <span class="art">${icon("mdi:movie-open-outline")}${img(it.image)}${p != null ? `<span class="prog"><span style="width:${p}%"></span></span>` : ""}</span>
        <b>${esc(it.title)}</b><small>${esc(it.subtitle || "")}</small>
      </button>`;
    }).join("")}</div>`;
  }

  // ---------- events ----------

  _onClick(e) {
    const b = e.target.closest?.("[data-a]");
    if (!b || b.disabled) return;
    if (performance.now() < this._noClickUntil) return;  // swallow the click that ends a swipe or long-press
    const v = b.dataset.v;
    const ed = this._edit;
    switch (b.dataset.a) {
      case "tab": return this._setTab(v);
      case "target": return this._setTarget(v);
      case "remote": return this._remote(v);
      case "np": return this._call("remote", { command: v });
      case "touchpad": this._touchpad = !this._touchpad; return this._render();
      case "click": return this._click(v);
      case "app": return this._call("launch_app", { app: v });
      case "kclear": {
        this.shadowRoot.querySelector("[data-kbdin]").value = "";
        return this._kbd("keyboard_key", { key: "clear" });
      }
      case "digit": return this._digit(v);
      case "pc_open": return this._call("pc_open", { client: v });
      case "pc_close": return this._call("pc_close");
      case "drawer": return this._toggleDrawer();
      case "mode": return this._snap.ambience?.mode === v ? this._call("ambience_stop") : this._call("ambience_start", { mode: v });
      case "auto": return this._call("ambience_auto");
      case "amb_stop": return this._call("ambience_stop");
      case "edit": return this._openEditor(v);
      case "edclose": return this._closeEditor();
      case "sleep": return this._call("ambience_sleep_timer", { minutes: Number(v) });
      case "refresh": return this._refreshCatalog();
      case "browse": return this._browseBy(v, Number(b.dataset.d));
      case "pick": return this._pick(v);
      case "fav": return this._fav(v);
      case "tog": return this._editSet({ [v]: !ed.draft[v] });
      case "vis": return this._editSet(ed.draft.visual_enabled ? { visual_enabled: false }
        : { visual_enabled: true, visual: ed.draft.visual ?? this._cur("visual").item?.id ?? null });
      case "audio": return this._editSet({ soundtrack: ed.draft.soundtrack != null ? null : this._cur("soundtrack").item?.id ?? null });
      case "save": return this._save();
      case "start": return this._start();
      case "play": {
        this._toast("Starting on the TV…");
        return this._call("play_library_item", { server: b.dataset.srv, item_id: v });
      }
      default: return undefined;
    }
  }

  _onDown(e) {
    if (e.button > 0) return;
    this._noClickUntil = 0;  // a new press is a new gesture: only the click ending a swipe/hold is swallowed
    const tp = e.target.closest?.("[data-touch]");
    if (tp) return this._tpDown(e, tp);
    if (e.target.closest?.("[data-a=kclear]")) e.preventDefault();  // keep the phone keyboard open
    const sw = e.target.closest?.("[data-swipe]");
    if (sw) this._swipe = { x: e.clientX, y: e.clientY, id: e.pointerId, kind: sw.dataset.swipe };
    const h = e.target.closest?.("[data-hold]");
    if (h) this._startHold(h, e);
    return undefined;
  }

  _onMove(e) {
    if (this._pointers.has(e.pointerId)) return this._tpMove(e);
    const h = this._hold;
    if (h && !h.fired && Math.hypot(e.clientX - h.x, e.clientY - h.y) > 10) this._stopHold();
    return undefined;
  }

  _onUp(e) {
    if (this._pointers.has(e.pointerId)) this._tpUp(e);
    const s = this._swipe;
    if (!s || s.id !== e.pointerId) return;
    this._swipe = null;
    const dx = e.clientX - s.x;
    const dy = e.clientY - s.y;
    if (e.type !== "pointerup" || Math.abs(dx) < (s.kind === "target" ? 55 : 38) || Math.abs(dx) <= Math.abs(dy) * 1.15) return;
    this._noClickUntil = performance.now() + 350;
    this._stopHold();
    const dir = dx < 0 ? 1 : -1;
    if (s.kind !== "target") return this._browseBy(s.kind, dir);
    const t = nextTarget(this._eff(), this._snap?.targets, dir);
    if (t === this._eff()) return;
    navigator.vibrate?.(10);
    this._setTarget(t);
  }

  _startHold(el, e) {
    this._stopHold();
    const repeat = el.dataset.hold === "repeat";
    const h = { x: e.clientX, y: e.clientY, fired: false };
    h.timer = setTimeout(() => {
      h.fired = true;
      navigator.vibrate?.(10);
      if (!repeat) {
        this._toast(`Restarting ${el.textContent.trim()}…`);
        this._call("restart_app", { app: el.dataset.v });
        return;
      }
      const fire = () => this._remote(el.dataset.v);
      fire();
      h.iv = setInterval(fire, 150);
    }, repeat ? 400 : 600);
    this._hold = h;
    window.addEventListener("pointerup", this._stopHoldB, { once: true });
    window.addEventListener("pointercancel", this._stopHoldB, { once: true });
  }

  _stopHold() {
    const h = this._hold;
    if (!h) return;
    this._hold = null;
    clearTimeout(h.timer);
    clearInterval(h.iv);
    if (h.fired) this._noClickUntil = performance.now() + 400;
    window.removeEventListener("pointerup", this._stopHoldB);
    window.removeEventListener("pointercancel", this._stopHoldB);
  }

  _onKey(e) {
    const t = e.composedPath()[0];
    if (t.matches?.("[data-kbdin]")) {
      e.stopPropagation();  // keep HA's global shortcuts out of the text field
      if (e.key === "Enter" && !e.isComposing) {
        e.preventDefault();
        const text = t.value.replace(/[\u0000-\u001f\u007f]/g, "");  // eslint-disable-line no-control-regex
        t.value = "";
        if (text) this._kbd("send_text", { text });
      } else if (e.key === "Backspace" && !t.value) {
        e.preventDefault();
        this._kbd("keyboard_key", { key: "backspace" });
      }
      return;
    }
    if (e.key === "Escape" && this._config.drawer && this._open) {
      this._toggleDrawer(false);
      this.shadowRoot.querySelector(".handle")?.focus();
      return;
    }
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    if (t.getAttribute?.("role") === "tab" && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
      const tabs = this._tabs();
      e.preventDefault();
      this._setTab(tabs[(tabs.indexOf(this._tab) + step + tabs.length) % tabs.length]);
      this.shadowRoot.querySelector("[role=tab][aria-selected=true]")?.focus();
      return;
    }
    if (step && t.getAttribute?.("role") === "radio") {  // target pills: roving radiogroup
      e.preventDefault();
      this._setTarget(nextTarget(this._eff(), this._snap?.targets, step));
      this.shadowRoot.querySelector("[role=radio][aria-checked=true]")?.focus();
      return;
    }
    if (t.classList?.contains("dpad")) {
      const dir = ARROWS[e.key];
      if (dir || e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        this._remote(dir || "select");
      }
      return;
    }
    const car = t.classList?.contains("car") ? t : null;
    if (car && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
      e.preventDefault();
      this._browseBy(car.dataset.swipe, e.key === "ArrowRight" ? 1 : -1);
      this.shadowRoot.querySelector(`.car.${car.dataset.swipe}`)?.focus();
    } else if (car && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      this._pick(car.dataset.swipe);
      this.shadowRoot.querySelector(`.car.${car.dataset.swipe}`)?.focus();
    }
  }

  _onInput(e) {
    const t = e.composedPath()[0];
    if (!t.matches?.("[data-mix]") || !this._edit) return;
    const v = clamp(Number(t.value) || 0, 0, 100);
    t.style.setProperty("--p", `${v}%`);
    t.setAttribute("aria-valuetext", `${100 - v}% ambience, ${v}% soundtrack`);
    this.shadowRoot.querySelector("[data-mixa]").textContent = `${100 - v}%`;
    this.shadowRoot.querySelector("[data-mixs]").textContent = `${v}%`;
    this._editSet(mixToVolumes(v), false);
    this._slices.ambmain = JSON.stringify(this._ambSlice());  // DOM already shows it; never re-render mid-drag
  }
}

const CSS = `
:host {
  --hh-fg: var(--primary-text-color, #1f2328);
  --hh-fg2: var(--secondary-text-color, #646a73);
  --hh-bg: var(--ha-card-background, var(--card-background-color, #fff));
  --hh-accent: var(--primary-color, #03a9f4);
  --hh-rgb: var(--rgb-primary-color, 3, 169, 244);
  --hh-on-accent: var(--text-primary-color, #fff);
  --hh-line: var(--divider-color, rgba(0, 0, 0, .12));
  --hh-r: var(--ha-card-border-radius, 12px);
  --hh-s1: color-mix(in srgb, var(--hh-fg) 5%, transparent);
  --hh-s2: color-mix(in srgb, var(--hh-fg) 9%, transparent);
  --hh-s3: color-mix(in srgb, var(--hh-fg) 16%, transparent);
  --hh-soft: rgba(var(--hh-rgb), .14);
  --hh-ease: cubic-bezier(.2, .8, .2, 1);
  display: block;
  color: var(--hh-fg);
  -webkit-tap-highlight-color: transparent;
}
*, *::before, *::after { box-sizing: border-box; }
[hidden], [data-s]:empty { display: none !important; }
ha-icon { --mdc-icon-size: 22px; display: inline-flex; flex: none; }
button {
  font: inherit; color: inherit; border: 0; margin: 0; padding: 0; background: none; cursor: pointer;
  touch-action: manipulation; user-select: none; -webkit-user-select: none; -webkit-touch-callout: none;
  transition: background-color .15s, color .15s, box-shadow .2s, transform .12s var(--hh-ease);
}
button:active:not(:disabled) { transform: scale(.96); }
button:disabled { opacity: .38; cursor: default; }
:focus-visible { outline: 2px solid var(--hh-accent); outline-offset: 2px; }
.wrap { container: hh / inline-size; display: grid; grid-template-columns: minmax(0, 1fr); gap: 14px; padding: 12px; }
.body, .remote, .col { display: grid; grid-template-columns: minmax(0, 1fr); gap: 18px; min-width: 0; }
.row, .ctl { display: flex; align-items: center; gap: 8px; }
.ctl { justify-content: center; gap: 10px; }
.grow { flex: 1; }
h3.sh { display: flex; align-items: center; gap: 8px; margin: 0 2px 10px; font-size: 14px; font-weight: 600; color: var(--hh-fg2); }
h3.sh ha-icon { --mdc-icon-size: 18px; }
.sh .chip, .sh .note { margin-left: auto; }
.note { font-weight: 600; color: var(--hh-fg); }
.hint { display: flex; align-items: center; gap: 6px; margin: 6px 4px 0; font-size: 12.5px; color: var(--hh-fg2); }
.hint ha-icon { --mdc-icon-size: 16px; }

/* buttons */
.ib {
  display: inline-grid; place-items: center; min-width: 44px; height: 44px; border-radius: 50%;
  background: var(--hh-s1); color: var(--hh-fg);
}
.ib.flat { background: none; }
.ib.on { background: var(--hh-soft); color: var(--hh-accent); }
.ib.accent { background: var(--hh-accent); color: var(--hh-on-accent); box-shadow: 0 4px 12px -4px rgba(var(--hh-rgb), .7); }
.ib.power ha-icon { color: var(--error-color, #db4437); }
.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 8px; min-height: 44px; padding: 0 20px;
  border-radius: 999px; background: var(--hh-s1); box-shadow: inset 0 0 0 1px var(--hh-line); font-weight: 600; font-size: 14px;
}
.btn ha-icon { --mdc-icon-size: 18px; }
.btn.primary { background: var(--hh-accent); color: var(--hh-on-accent); box-shadow: 0 4px 14px -6px rgba(var(--hh-rgb), .8); }
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chips.center { justify-content: center; }
.chip {
  display: inline-flex; align-items: center; gap: 6px; min-height: 44px; padding: 0 16px; border-radius: 999px;
  background: var(--hh-s1); box-shadow: inset 0 0 0 1px var(--hh-line); font-size: 14px; font-weight: 500;
}
.chip ha-icon { --mdc-icon-size: 18px; color: var(--hh-fg2); }
.chip[aria-pressed=true] { background: var(--hh-soft); box-shadow: inset 0 0 0 1.5px rgba(var(--hh-rgb), .6); }
.chip[aria-pressed=true] ha-icon { color: var(--hh-accent); }
.chip.sm { min-height: 36px; padding: 0 12px; font-size: 13px; }
@media (hover: hover) {
  .ib:hover:not(.accent):not(:disabled), .chip:hover:not(:disabled), .btn:not(.primary):hover { background: var(--hh-s2); }
  .ib.flat:hover { background: var(--hh-s1); }
}

/* banner, skeleton */
.banner {
  --c: var(--warning-color, #ffa600);
  display: flex; align-items: center; gap: 10px; padding: 10px 14px; border-radius: 12px; font-size: 13.5px;
  background: color-mix(in srgb, var(--c) 15%, transparent);
}
.banner.err { --c: var(--error-color, #db4437); }
.banner ha-icon { color: var(--c); }
.skel {
  height: 140px; border-radius: 16px; background: linear-gradient(90deg, var(--hh-s1), var(--hh-s2), var(--hh-s1));
  background-size: 200% 100%; animation: hh-shimmer 1.4s linear infinite;
}
.skel.short, .skel.soundtrack { height: 76px; }
.skel.visual { height: auto; aspect-ratio: 16 / 9; max-height: 260px; }
.skel.shelf-skel { height: 200px; }
@keyframes hh-shimmer { to { background-position: -200% 0; } }

/* tabs and target pills: segmented controls */
.tabs, .pills { display: flex; gap: 4px; padding: 4px; border-radius: 999px; background: var(--hh-s1); }
.pills { width: max-content; max-width: 100%; margin: 0 auto; }
.tab, .pill {
  flex: 1; display: inline-flex; align-items: center; justify-content: center; gap: 8px; min-width: 0; min-height: 44px; padding: 0 14px;
  border-radius: 999px; color: var(--hh-fg2); font-weight: 600; font-size: 14px; white-space: nowrap;
}
.tab span, .pill span { overflow: hidden; text-overflow: ellipsis; }
.pill { flex: 0 1 auto; }
.tab ha-icon, .pill ha-icon { --mdc-icon-size: 20px; }
.tab[aria-selected=true], .pill[aria-checked=true] {
  background: var(--hh-bg); color: var(--hh-fg); box-shadow: 0 1px 3px rgba(0, 0, 0, .18), inset 0 0 0 1px var(--hh-line);
}
.tab[aria-selected=true] ha-icon, .pill[aria-checked=true] ha-icon { color: var(--hh-accent); }
@container hh (max-width: 380px) { .tab ha-icon { display: none; } }

/* now playing */
.np {
  position: relative; display: flex; align-items: center; gap: 12px; padding: 10px; border-radius: 16px;
  overflow: hidden; isolation: isolate; background: var(--hh-s1); box-shadow: inset 0 0 0 1px var(--hh-line);
}
.npbg { position: absolute; inset: -24px; width: calc(100% + 48px); height: calc(100% + 48px); object-fit: cover; filter: blur(28px) saturate(1.5); opacity: .28; z-index: -1; }
.npart { position: relative; flex: none; display: grid; place-items: center; width: 52px; height: 72px; border-radius: 10px; overflow: hidden; background: var(--hh-s2); color: var(--hh-fg2); }
.npart img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
.npmeta { flex: 1; min-width: 0; }
.npmeta b, .npmeta small { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.npmeta b { font-size: 15px; font-weight: 600; }
.npmeta small { font-size: 12.5px; color: var(--hh-fg2); margin-top: 2px; }
.npbar { height: 4px; margin-top: 10px; border-radius: 2px; background: var(--hh-s3); overflow: hidden; }
.npfill { display: block; height: 100%; width: 0; border-radius: inherit; background: var(--hh-accent); transition: width 1s linear; }
.nptime { display: flex; justify-content: space-between; margin-top: 4px; font-size: 11px; color: var(--hh-fg2); font-variant-numeric: tabular-nums; }
.npbtns { display: flex; align-items: center; gap: 2px; flex: none; }
@container hh (max-width: 380px) {
  .np { flex-wrap: wrap; }
  .npmeta { flex-basis: calc(100% - 64px); }
  .npbtns { width: 100%; justify-content: center; gap: 16px; }
}

/* remote */
.remote { gap: 20px; }
.col { gap: 14px; align-content: start; }
@container hh (min-width: 720px) {
  .remote { grid-template-columns: minmax(300px, 400px) minmax(0, 1fr); gap: 28px; }
}
.pad { display: grid; justify-items: center; gap: 10px; }
.dpad {
  display: grid; grid-template: ". u ." 1fr "l o r" 1fr ". d ." 1fr / 1fr 1fr 1fr;
  width: 240px; width: min(240px, 72cqi); aspect-ratio: 1; border-radius: 50%; padding: 6px;
  background: radial-gradient(circle, transparent 38%, var(--hh-s1) 38.5%), var(--hh-s1);
  box-shadow: inset 0 0 0 1px var(--hh-line); touch-action: pan-y;
}
.dk { display: grid; place-items: center; width: 100%; height: 100%; border-radius: 50%; color: var(--hh-fg2); }
.dk ha-icon { --mdc-icon-size: 34px; }
.dk:active { background: var(--hh-s2); color: var(--hh-fg); }
.dk.up { grid-area: u; } .dk.down { grid-area: d; } .dk.left { grid-area: l; } .dk.right { grid-area: r; }
.ok {
  grid-area: o; place-self: center; width: 96%; aspect-ratio: 1; border-radius: 50%;
  background: var(--hh-accent); color: var(--hh-on-accent); font-size: 17px; font-weight: 700; letter-spacing: .06em;
  box-shadow: 0 8px 20px -8px rgba(var(--hh-rgb), .9), inset 0 1px 0 rgba(255, 255, 255, .25);
}
.dpad:focus-visible { outline-offset: 4px; }
.touch {
  display: grid; place-content: center; justify-items: center; gap: 8px; width: 100%; max-width: 380px; height: 200px;
  border-radius: 20px; background: var(--hh-s1); box-shadow: inset 0 0 0 1px var(--hh-line);
  color: var(--hh-fg2); font-size: 13px; text-align: center; padding: 16px; touch-action: none; user-select: none; -webkit-user-select: none;
}
.touch ha-icon { --mdc-icon-size: 32px; opacity: .7; }
.clicks { display: flex; gap: 8px; width: 100%; max-width: 380px; }
.clicks .btn { flex: 1; }
.rocker { display: inline-flex; align-items: center; height: 48px; border-radius: 999px; background: var(--hh-s1); }
.rocker button { display: inline-flex; align-items: center; justify-content: center; gap: 4px; min-width: 48px; height: 48px; border-radius: 999px; }
.rocker .mute { min-width: 72px; font-weight: 600; font-variant-numeric: tabular-nums; }
.rocker .mute ha-icon { --mdc-icon-size: 20px; color: var(--hh-fg2); }
.rocker .mute.on ha-icon { color: var(--error-color, #db4437); }
.rocker button:active { background: var(--hh-s2); }
.apps { display: grid; grid-template-columns: repeat(auto-fill, minmax(100px, 1fr)); gap: 10px; }
.app {
  position: relative; display: grid; place-items: center; height: 60px; padding: 0 10px; border-radius: 14px; overflow: hidden;
  background: var(--b, var(--hh-s2)); color: var(--f, var(--hh-accent)); font-size: 15px; font-weight: 700; letter-spacing: -.01em;
  line-height: 1.15; text-align: center; box-shadow: inset 0 0 0 1px rgba(255, 255, 255, .06), 0 1px 3px rgba(0, 0, 0, .18);
}
.app::before { content: ""; position: absolute; inset: 0; background: linear-gradient(180deg, rgba(255, 255, 255, .14), transparent 60%); pointer-events: none; }
.app.on { box-shadow: 0 0 0 2px var(--hh-bg), 0 0 0 4px var(--hh-accent), 0 10px 24px -8px rgba(var(--hh-rgb), .9); }
.kbdwrap { display: grid; }
.kbd {
  display: flex; align-items: center; gap: 4px; height: 48px; padding: 0 2px 0 14px; border-radius: 999px;
  background: var(--hh-s1); box-shadow: inset 0 0 0 1px var(--hh-line); transition: box-shadow .15s, background-color .15s;
}
.kbd:focus-within { background: var(--hh-bg); box-shadow: inset 0 0 0 2px var(--hh-accent); }
.kbd > ha-icon { color: var(--hh-fg2); }
.kbd input { flex: 1; min-width: 0; height: 100%; border: 0; outline: 0; background: none; color: inherit; font: inherit; font-size: 16px; }
.kbd input::placeholder { color: var(--hh-fg2); opacity: 1; }
.kbd .ib { background: none; }
.chd { margin-left: auto; min-width: 4ch; font-size: 22px; font-weight: 600; color: var(--hh-fg); font-variant-numeric: tabular-nums; text-align: right; }
.keypad { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; max-width: 320px; margin: 0 auto 12px; }
.key { min-height: 52px; border-radius: 14px; background: var(--hh-s1); font-size: 20px; font-weight: 500; display: grid; place-items: center; }
.key:active { background: var(--hh-s2); }
.key.go { background: var(--hh-soft); color: var(--hh-accent); }

/* ambience */
.modes { display: grid; grid-template-columns: repeat(auto-fill, minmax(112px, 1fr)); gap: 10px; }
.mode { position: relative; min-width: 0; }
.mt {
  display: grid; align-content: space-between; justify-items: start; gap: 14px; width: 100%; min-height: 104px; padding: 12px;
  border-radius: 16px; background: var(--hh-s1); text-align: left;
}
.mi { display: grid; place-items: center; width: 40px; height: 40px; border-radius: 50%; background: var(--hh-s2); color: var(--hh-fg2); transition: background-color .2s, color .2s, box-shadow .2s; }
.mx { display: grid; gap: 2px; width: 100%; min-width: 0; }
.mx b { font-size: 14px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mx small { display: flex; align-items: center; gap: 6px; min-height: 18px; font-size: 12px; color: var(--hh-fg2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.badge { padding: 1px 7px; border-radius: 999px; background: var(--hh-accent); color: var(--hh-on-accent); font-size: 10.5px; font-weight: 700; letter-spacing: .06em; }
.mode.on .mt { background: var(--hh-soft); box-shadow: inset 0 0 0 1.5px rgba(var(--hh-rgb), .55); }
.mode.on .mi, .mi.on { background: var(--hh-accent); color: var(--hh-on-accent); box-shadow: 0 0 18px rgba(var(--hh-rgb), .55); }
.gear { position: absolute; top: 2px; right: 2px; display: grid; place-items: center; width: 44px; height: 44px; border-radius: 50%; color: var(--hh-fg2); }
.gear ha-icon { --mdc-icon-size: 20px; }
@media (hover: hover) { .mt:hover { background: var(--hh-s2); } .mode.on .mt:hover { background: rgba(var(--hh-rgb), .2); } .gear:hover { background: var(--hh-s2); } }
.edhead { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
.edtitle { flex: 1; min-width: 0; }
.edtitle b { display: block; font-size: 17px; font-weight: 600; }
.edtitle small { display: block; font-size: 12.5px; color: var(--hh-fg2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.spin ha-icon { animation: hh-spin 1s linear infinite; }
@keyframes hh-spin { to { transform: rotate(360deg); } }
.edsec { display: grid; gap: 10px; margin-top: 16px; }
.edsec h3.sh { margin-bottom: 0; }
.car { position: relative; border-radius: 16px; overflow: hidden; background: var(--hh-s1); touch-action: pan-y; }
.car::after { content: ""; position: absolute; inset: 0; border-radius: inherit; pointer-events: none; transition: box-shadow .2s; }
.car.sel::after { box-shadow: inset 0 0 0 3px var(--hh-accent); }
.slide { display: block; width: 100%; height: 100%; text-align: left; }
.slide:active { transform: none; }
.art { position: relative; display: block; overflow: hidden; }
.art img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
.ph { position: absolute; inset: 0; display: grid; place-items: center; color: var(--hh-fg2); background: radial-gradient(circle at 30% 20%, rgba(var(--hh-rgb), .28), transparent 65%), var(--hh-s2); }
.ph ha-icon { --mdc-icon-size: 40px; opacity: .7; }
.cap b, .cap small { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cap b { font-size: 15px; font-weight: 600; }
.cap small { font-size: 12px; margin-top: 2px; font-variant-numeric: tabular-nums; }
.check { display: grid; place-items: center; width: 28px; height: 28px; border-radius: 50%; background: var(--hh-accent); color: var(--hh-on-accent); }
.check ha-icon { --mdc-icon-size: 18px; }
.nav, .fav { display: grid; place-items: center; width: 44px; height: 44px; border-radius: 50%; }
.fav.on ha-icon { color: var(--warning-color, #ffc107); }
/* visual: big 16:9 slide with glass controls on top */
.car.visual { aspect-ratio: 16 / 9; max-height: 260px; }
.car.visual .slide { position: absolute; inset: 0; }
.car.visual .art { position: absolute; inset: 0; }
.car.visual .cap { position: absolute; left: 0; right: 0; bottom: 0; padding: 32px 56px 12px 16px; color: #fff; background: linear-gradient(transparent, rgba(0, 0, 0, .7)); }
.car.visual .cap small { opacity: .85; }
.car.visual .check { position: absolute; top: 10px; left: 10px; }
.car.visual .nav, .car.visual .fav { position: absolute; z-index: 1; color: #fff; }
.car.visual .nav::before, .car.visual .fav::before { content: ""; position: absolute; inset: 6px; border-radius: 50%; background: rgba(0, 0, 0, .38); backdrop-filter: blur(6px); -webkit-backdrop-filter: blur(6px); }
.car.visual .nav ha-icon, .car.visual .fav ha-icon { position: relative; }
.car.visual .nav { top: 50%; margin-top: -22px; }
.car.visual .prev { left: 4px; } .car.visual .next { right: 4px; }
.car.visual .fav { top: 4px; right: 4px; }
/* soundtrack: compact row */
.car.soundtrack { display: flex; align-items: center; gap: 2px; padding: 8px 4px; }
.car.soundtrack .slide { flex: 1; min-width: 0; display: flex; align-items: center; gap: 12px; padding: 0 4px; }
.car.soundtrack .art { width: 56px; height: 56px; flex: none; border-radius: 12px; }
.car.soundtrack .ph ha-icon { --mdc-icon-size: 28px; }
.car.soundtrack .cap { flex: 1; min-width: 0; }
.car.soundtrack .cap small { color: var(--hh-fg2); }
.car.soundtrack .nav, .car.soundtrack .fav { flex: none; color: var(--hh-fg2); }
.car-empty { display: flex; align-items: center; gap: 10px; padding: 18px 16px; border-radius: 16px; background: var(--hh-s1); color: var(--hh-fg2); font-size: 13.5px; }
input[type=range] {
  --p: 50%; -webkit-appearance: none; appearance: none; width: 100%; height: 44px; margin: 0; background: transparent; cursor: pointer;
}
input[type=range]::-webkit-slider-runnable-track { height: 6px; border-radius: 3px; background: linear-gradient(90deg, var(--hh-accent) var(--p), var(--hh-s3) var(--p)); }
input[type=range]::-moz-range-track { height: 6px; border-radius: 3px; background: linear-gradient(90deg, var(--hh-accent) var(--p), var(--hh-s3) var(--p)); }
input[type=range]::-webkit-slider-thumb {
  -webkit-appearance: none; width: 26px; height: 26px; margin-top: -10px; border-radius: 50%; border: 0;
  background: var(--hh-bg); box-shadow: 0 0 0 2px var(--hh-accent), 0 2px 8px rgba(0, 0, 0, .3);
}
input[type=range]::-moz-range-thumb { width: 26px; height: 26px; border-radius: 50%; border: 0; background: var(--hh-bg); box-shadow: 0 0 0 2px var(--hh-accent), 0 2px 8px rgba(0, 0, 0, .3); }
input[type=range]:focus-visible { outline: none; }
input[type=range]:focus-visible::-webkit-slider-thumb { box-shadow: 0 0 0 2px var(--hh-accent), 0 0 0 6px rgba(var(--hh-rgb), .3); }
.mixlab { display: flex; justify-content: space-between; margin-top: -4px; font-size: 12.5px; color: var(--hh-fg2); }
.mixlab b { color: var(--hh-fg); font-weight: 600; font-variant-numeric: tabular-nums; }
.actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
.actions .btn { min-width: 112px; }

/* library */
.lib { display: grid; gap: 18px; }
.srv { min-width: 0; }
.srvh { color: var(--hh-fg) !important; font-size: 15px !important; }
.rowlab { margin: 4px 2px 8px; font-size: 13px; font-weight: 600; color: var(--hh-fg2); }
.shelf {
  display: grid; grid-auto-flow: column; grid-auto-columns: 116px; gap: 12px; overflow-x: auto; padding: 2px 2px 10px;
  margin-bottom: 6px; scroll-snap-type: x mandatory; overscroll-behavior-x: contain; scrollbar-width: thin;
}
.poster { display: grid; align-content: start; gap: 6px; min-width: 0; text-align: left; scroll-snap-align: start; }
.poster .art { display: grid; place-items: center; aspect-ratio: 2 / 3; border-radius: 12px; background: var(--hh-s2); color: var(--hh-fg2); box-shadow: 0 2px 8px rgba(0, 0, 0, .16); }
.poster b { font-size: 13px; font-weight: 600; line-height: 1.25; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.poster small { font-size: 12px; color: var(--hh-fg2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: -4px; }
.prog { position: absolute; left: 8px; right: 8px; bottom: 8px; height: 4px; border-radius: 2px; background: rgba(255, 255, 255, .38); overflow: hidden; }
.prog > span { display: block; height: 100%; background: var(--hh-accent); }
@media (hover: hover) { .poster:hover .art { box-shadow: 0 0 0 2px var(--hh-accent), 0 6px 16px rgba(0, 0, 0, .2); } }

/* drawer: fixed to the right edge, only the handle shows when closed */
:host([drawer]) { position: fixed; top: 50%; right: env(safe-area-inset-right, 0px); z-index: 5; transform: translateY(-50%); pointer-events: none; }
.drawer {
  --pw: min(380px, calc(100vw - 46px));
  display: flex; align-items: center; transform: translateX(var(--pw)); transition: transform .2s var(--hh-ease);
}
.drawer[data-open=true] { transform: none; }
.drawer > * { pointer-events: auto; }
.handle {
  position: relative; display: grid; place-items: center; flex: none; width: 28px; height: 72px; border-radius: 14px 0 0 14px;
  background: var(--hh-bg); color: var(--hh-fg2); box-shadow: -4px 0 16px rgba(0, 0, 0, .18), inset 1px 0 0 var(--hh-line);
}
.handle::before { content: ""; position: absolute; inset: -12px 0 -12px -14px; }
.handle:active { transform: none; }
.handle ha-icon { transition: transform .2s var(--hh-ease); }
.drawer[data-open=true] .handle ha-icon { transform: rotate(180deg); }
.panel {
  width: var(--pw); max-height: min(88vh, 820px); overflow-y: auto; overscroll-behavior: contain;
  border-radius: var(--hh-r) 0 0 var(--hh-r); box-shadow: -10px 0 32px rgba(0, 0, 0, .22);
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition: none !important; animation: none !important; }
}
`;

if (!customElements.get(TAG)) customElements.define(TAG, HomeHubMediaCard);
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === TAG)) {
  window.customCards.push({ type: TAG, name: "HomeHub Media", description: "Remote, ambience and library", preview: true });
}
if (window.__HOMEHUB_TEST__) window.__homehubTest = { esc, mixToVolumes, nextTarget };
