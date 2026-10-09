// Home Hub Media Hub control pad v0.3.
// Mouse-pad mode prevents only the surrounding controller's swipe gesture.
// It does not change the Ubuntu host, Home Assistant entities, or other remotes.

const HOMEHUB_MEDIA_HUB_SWIPE_LOCK_EVENT = "homehub-media-hub-swipe-lock";

class HomeHubMediaHubControlPad extends HTMLElement {
  setConfig(config) {
    const required = [
      "up_service",
      "down_service",
      "left_service",
      "right_service",
      "select_service",
      "pointer_move_service",
      "pointer_left_click_service",
      "pointer_right_click_service",
      "pointer_scroll_service",
    ];
    const missing = required.filter((key) => !config[key]);
    if (missing.length) {
      throw new Error(`Missing required config: ${missing.join(", ")}`);
    }

    this._config = { width: "194px", ...config };
    this._mode = this._mode || "dpad";
    this._setSwipeLock(this._mode === "mouse");
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
  }

  connectedCallback() {
    this._setSwipeLock(this._mode === "mouse");
    this._render();
  }

  disconnectedCallback() {
    clearTimeout(this._moveTimer);
    clearTimeout(this._scrollTimer);
    this._setSwipeLock(false);
  }

  getCardSize() {
    return 3;
  }

  _render() {
    if (!this._config || this.shadowRoot) {
      return;
    }

    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        :host {
          display: block;
          width: ${this._escapeCss(this._config.width)};
          min-width: ${this._escapeCss(this._config.width)};
          max-width: ${this._escapeCss(this._config.width)};
          margin: 4px auto 8px;
          -webkit-tap-highlight-color: transparent;
        }
        ha-card { width: 100%; background: transparent; box-shadow: none; overflow: visible; }
        button { box-sizing: border-box; border: 0; color: rgba(247,252,255,0.98); cursor: pointer; font: inherit; touch-action: manipulation; }
        button:focus-visible, .touchpad:focus-visible { outline: 2px solid rgba(138,203,255,0.92); outline-offset: 2px; }
        .dpad {
          position: relative; box-sizing: border-box; width: 194px; height: 194px; padding: 10px;
          border: 1px solid rgba(132,121,255,0.22); border-radius: 50%;
          background: radial-gradient(circle at 50% 46%, rgba(57,77,170,0.22), rgba(16,46,94,0.42) 55%, rgba(7,25,55,0.22) 72%, rgba(255,255,255,0.025) 100%);
          box-shadow: inset 0 1px 0 rgba(255,255,255,0.08), 0 0 22px rgba(102,91,255,0.09);
          display: grid; grid-template-areas: ". up ." "left ok right" ". down .";
          grid-template-columns: 1fr 1fr 1fr; grid-template-rows: 1fr 1fr 1fr; align-items: center; justify-items: center;
        }
        .dpad-key { width: 52px; height: 46px; border-radius: 18px; background: rgba(255,255,255,0.035); box-shadow: inset 0 1px 0 rgba(255,255,255,0.07); font-size: 28px; line-height: 1; }
        .dpad-key:active, .click-key:active, .mode-toggle:active { background: rgba(65,116,185,0.58); transform: translateY(1px); }
        .up { grid-area: up; } .left { grid-area: left; } .right { grid-area: right; } .down { grid-area: down; }
        .ok {
          grid-area: ok; width: 68px; height: 68px; border: 1px solid rgba(170,159,255,0.66); border-radius: 50%;
          background: radial-gradient(circle at 38% 28%, rgba(122,105,255,0.36), rgba(42,74,147,0.78) 58%, rgba(15,31,67,0.92));
          box-shadow: 0 0 18px rgba(116,101,255,0.18), inset 0 1px 0 rgba(255,255,255,0.20); font-size: 15px; font-weight: 750;
        }
        .mouse { display: none; width: 194px; }
        .touchpad {
          position: relative; box-sizing: border-box; width: 194px; height: 154px; border: 1px solid rgba(167,139,255,0.55); border-radius: 26px;
          background: radial-gradient(circle at 42% 28%, rgba(122,105,255,0.26), rgba(30,58,119,0.62) 55%, rgba(7,25,55,0.92));
          box-shadow: inset 0 1px 0 rgba(255,255,255,0.12), 0 0 20px rgba(122,105,255,0.16); color: rgba(229,240,255,0.74);
          display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 7px; user-select: none; touch-action: none; cursor: default;
        }
        .touchpad ha-icon { width: 30px; height: 30px; color: rgba(209,198,255,0.94); }
        .touchpad-label { font-size: 11px; font-weight: 700; letter-spacing: 0.15px; }
        .touchpad-hint { max-width: 145px; color: rgba(221,237,255,0.60); font-size: 9px; line-height: 1.25; text-align: center; }
        .swipe-lock {
          position: absolute; top: 9px; right: 9px; width: 25px; height: 25px; border: 1px solid rgba(218,193,255,0.70); border-radius: 9px;
          background: rgba(87,55,149,0.66); box-shadow: inset 0 1px 0 rgba(255,255,255,0.16); display: inline-flex; align-items: center; justify-content: center;
        }
        .swipe-lock ha-icon { width: 15px; height: 15px; color: rgba(246,241,255,0.98); }
        .mouse-actions { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) 32px; gap: 7px; align-items: stretch; margin-top: 7px; }
        .click-key {
          min-width: 0; height: 42px; border: 1px solid rgba(154,210,255,0.20); border-radius: 15px;
          background: linear-gradient(155deg, rgba(255,255,255,0.105), rgba(255,255,255,0.035)); box-shadow: inset 0 1px 0 rgba(255,255,255,0.10), 0 4px 10px rgba(0,0,0,0.11);
          color: rgba(245,251,255,0.98); font-size: 10px; font-weight: 700; white-space: nowrap;
        }
        .mode-toggle { width: 32px; height: 32px; padding: 0; border: 1px solid rgba(185,150,255,0.66); border-radius: 11px; background: rgba(127,79,211,0.78); box-shadow: inset 0 1px 0 rgba(255,255,255,0.20); display: inline-flex; align-items: center; justify-content: center; }
        .mode-toggle ha-icon { width: 18px; height: 18px; }
        .dpad-toggle { position: absolute; right: 9px; bottom: 9px; z-index: 1; }
        .status { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
        .control[data-mode="mouse"] .dpad { display: none; }
        .control[data-mode="mouse"] .mouse { display: block; }
      </style>
      <ha-card>
        <div class="control" data-mode="${this._mode}">
          <section class="dpad" aria-label="Media Hub directional pad">
            <button class="dpad-key up" type="button" data-remote="up" aria-label="Up">▲</button>
            <button class="dpad-key left" type="button" data-remote="left" aria-label="Left">◀</button>
            <button class="ok" type="button" data-remote="select" aria-label="OK">OK</button>
            <button class="dpad-key right" type="button" data-remote="right" aria-label="Right">▶</button>
            <button class="dpad-key down" type="button" data-remote="down" aria-label="Down">▼</button>
            <button class="mode-toggle dpad-toggle" type="button" data-mode="mouse" aria-label="Switch to mouse pad" title="Switch to mouse pad"><ha-icon icon="mdi:mouse"></ha-icon></button>
          </section>
          <section class="mouse" aria-label="Media Hub mouse pad">
            <div class="touchpad" tabindex="0" role="application" aria-label="Mouse pad: drag one finger to move, use two fingers to scroll. Controller swipe switching is locked.">
              <span class="swipe-lock" role="img" aria-label="Controller swipe switching locked" title="Controller swipe switching locked"><ha-icon icon="mdi:lock"></ha-icon></span>
              <ha-icon icon="mdi:mouse"></ha-icon>
              <div class="touchpad-label">Mouse pad</div>
              <div class="touchpad-hint">Drag to move · two fingers or wheel to scroll</div>
            </div>
            <div class="mouse-actions">
              <button class="click-key" type="button" data-click="left" aria-label="Left click">Left click</button>
              <button class="click-key" type="button" data-click="right" aria-label="Right click">Right click</button>
              <button class="mode-toggle" type="button" data-mode="dpad" aria-label="Switch to directional pad" title="Switch to directional pad"><ha-icon icon="mdi:gamepad-variant-outline"></ha-icon></button>
            </div>
          </section>
        </div>
        <div class="status" aria-live="polite"></div>
      </ha-card>`;

    this._control = root.querySelector(".control");
    this._status = root.querySelector(".status");
    this._touchpad = root.querySelector(".touchpad");
    this._pointers = new Map();
    this._pendingMove = { dx: 0, dy: 0 };
    this._pendingScroll = 0;
    this._sendingMove = false;
    this._movePromise = null;
    this._sendingScroll = false;

    root.querySelectorAll("[data-remote]").forEach((button) => {
      button.addEventListener("click", () => this._callQuietly(this._config[`${button.dataset.remote}_service`]));
    });
    root.querySelectorAll("[data-mode]").forEach((button) => {
      button.addEventListener("click", () => this._setMode(button.dataset.mode));
    });
    root.querySelectorAll("[data-click]").forEach((button) => {
      button.addEventListener("click", () => this._click(button.dataset.click));
    });
    this._touchpad.addEventListener("pointerdown", (event) => this._pointerDown(event));
    this._touchpad.addEventListener("pointermove", (event) => this._pointerMove(event));
    this._touchpad.addEventListener("pointerup", (event) => this._pointerEnd(event));
    this._touchpad.addEventListener("pointercancel", (event) => this._pointerEnd(event));
    this._touchpad.addEventListener("wheel", (event) => {
      event.preventDefault();
      this._queueScroll(event.deltaY * 0.72);
    }, { passive: false });
  }

  _setMode(mode) {
    this._mode = mode === "mouse" ? "mouse" : "dpad";
    this._control.dataset.mode = this._mode;
    this._pointers.clear();
    this._lastTwoFingerY = null;
    this._setSwipeLock(this._mode === "mouse");
    this._setStatus(this._mode === "mouse" ? "Mouse pad enabled; controller swipe switching locked" : "Directional pad enabled; controller swipe switching unlocked");
  }

  _setSwipeLock(locked) {
    const isLocked = Boolean(locked);
    window.__homehubMediaHubSwipeLocked = isLocked;
    window.dispatchEvent(new CustomEvent(HOMEHUB_MEDIA_HUB_SWIPE_LOCK_EVENT, { detail: { locked: isLocked } }));
  }

  async _callService(serviceName, data = {}) {
    if (!this._hass) throw new Error("Home Assistant is not connected");
    const [domain, service, extra] = String(serviceName).split(".");
    if (!domain || !service || extra) throw new Error("Invalid service");
    return this._hass.callService(domain, service, data);
  }

  _callQuietly(serviceName, data) {
    this._callService(serviceName, data).catch(() => this._setStatus("Command was not sent"));
  }

  async _click(button) {
    const serviceName = button === "left" ? this._config.pointer_left_click_service : this._config.pointer_right_click_service;
    // A tap on the click button can occur immediately after a drag.  Wait for
    // the final pointer-motion request so Mutter sees the same virtual mouse
    // arrive at the target before it receives its button press.
    await this._flushPendingMove();
    this._callQuietly(serviceName, { button });
  }

  _pointerDown(event) {
    event.preventDefault();
    this._touchpad.setPointerCapture?.(event.pointerId);
    this._pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (this._pointers.size >= 2) this._lastTwoFingerY = this._averagePointerY();
  }

  _pointerMove(event) {
    const previous = this._pointers.get(event.pointerId);
    if (!previous) return;
    event.preventDefault();
    this._pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (this._pointers.size >= 2) {
      const currentY = this._averagePointerY();
      if (this._lastTwoFingerY !== null) this._queueScroll(-(currentY - this._lastTwoFingerY) * 1.2);
      this._lastTwoFingerY = currentY;
      return;
    }
    this._queueMove((event.clientX - previous.x) * 1.15, (event.clientY - previous.y) * 1.15);
  }

  _pointerEnd(event) {
    this._pointers.delete(event.pointerId);
    if (this._pointers.size < 2) this._lastTwoFingerY = null;
  }

  _averagePointerY() {
    let total = 0;
    this._pointers.forEach((point) => { total += point.y; });
    return total / this._pointers.size;
  }

  _queueMove(dx, dy) {
    this._pendingMove.dx += this._clamp(dx, -120, 120);
    this._pendingMove.dy += this._clamp(dy, -120, 120);
    if (!this._moveTimer) this._moveTimer = setTimeout(() => this._flushMove(), 36);
  }

  _queueScroll(dy) {
    this._pendingScroll += this._clamp(dy, -180, 180);
    if (!this._scrollTimer) this._scrollTimer = setTimeout(() => this._flushScroll(), 36);
  }

  _flushMove() {
    this._moveTimer = null;
    if (this._sendingMove) return;
    const { dx, dy } = this._pendingMove;
    this._pendingMove = { dx: 0, dy: 0 };
    if (Math.abs(dx) < 0.2 && Math.abs(dy) < 0.2) return;
    this._sendingMove = true;
    const request = this._callService(this._config.pointer_move_service, { dx: this._clamp(dx, -160, 160), dy: this._clamp(dy, -160, 160) })
      .catch(() => this._setStatus("Pointer move was not sent"))
      .finally(() => {
        this._sendingMove = false;
        if (this._movePromise === request) this._movePromise = null;
        if (Math.abs(this._pendingMove.dx) >= 0.2 || Math.abs(this._pendingMove.dy) >= 0.2) this._queueMove(0, 0);
      });
    this._movePromise = request;
  }

  async _flushPendingMove() {
    if (this._moveTimer) {
      clearTimeout(this._moveTimer);
      this._moveTimer = null;
      this._flushMove();
    }
    if (this._movePromise) await this._movePromise.catch(() => {});
    if (Math.abs(this._pendingMove.dx) >= 0.2 || Math.abs(this._pendingMove.dy) >= 0.2) {
      this._flushMove();
      if (this._movePromise) await this._movePromise.catch(() => {});
    }
  }

  _flushScroll() {
    this._scrollTimer = null;
    if (this._sendingScroll) return;
    const dy = this._pendingScroll;
    this._pendingScroll = 0;
    if (Math.abs(dy) < 0.2) return;
    this._sendingScroll = true;
    this._callService(this._config.pointer_scroll_service, { dy: this._clamp(dy, -240, 240) })
      .catch(() => this._setStatus("Scroll was not sent"))
      .finally(() => {
        this._sendingScroll = false;
        if (Math.abs(this._pendingScroll) >= 0.2) this._queueScroll(0);
      });
  }

  _clamp(value, minimum, maximum) {
    return Math.min(maximum, Math.max(minimum, value));
  }

  _setStatus(message) {
    if (this._status) this._status.textContent = message;
  }

  _escapeCss(value) {
    return String(value).replace(/[;{}]/g, "");
  }
}

const tagName = "homehub-media-hub-control-pad";
if (!customElements.get(tagName)) customElements.define(tagName, HomeHubMediaHubControlPad);

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === tagName)) {
  window.customCards.push({
    type: tagName,
    name: "HomeHub Media Hub Control Pad",
    description: "Media Hub-only D-pad and virtual mouse pad for the Ubuntu HDMI browser",
  });
}
