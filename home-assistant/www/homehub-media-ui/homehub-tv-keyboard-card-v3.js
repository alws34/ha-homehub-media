class HomeHubTVKeyboardCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = null;
    this._built = false;
    this._busy = false;
    this._lastApp = null;
  }

  setConfig(config) {
    if (!config) throw new Error("HomeHub TV Keyboard: configuration required");
    this._config = {
      entity: config.entity || "sensor.homehub_media_observed_app",
      submit_service: config.submit_service || "script.homehub_media_keyboard_submit",
      backspace_service: config.backspace_service || "script.homehub_media_keyboard_backspace",
      clear_service: config.clear_service || "script.homehub_media_keyboard_clear",
      width: config.width || "88%",
    };
    this.style.setProperty("--homehub-tv-keyboard-width", this._config.width);
    this._build();
    this._update();
  }

  set hass(hass) {
    this._hass = hass;
    this._build();
    this._update();
  }

  getCardSize() { return 1; }

  _build() {
    if (this._built || !this._config) return;
    this._built = true;
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          width: var(--homehub-tv-keyboard-width, 88%);
          margin: -2px auto 9px auto;
          box-sizing: border-box;
        }
        .wrap {
          position: relative;
          width: 100%;
          height: 36px;
          box-sizing: border-box;
        }
        .icon {
          position: absolute;
          left: 11px;
          top: 50%;
          transform: translateY(-50%);
          width: 18px;
          height: 18px;
          color: rgba(224,244,255,0.82);
          pointer-events: none;
          z-index: 2;
        }
        input {
          width: 100%;
          height: 36px;
          box-sizing: border-box;
          border-radius: 18px;
          border: 1px solid rgba(95,220,224,0.28);
          outline: none;
          padding: 0 42px 0 36px;
          background: linear-gradient(145deg, rgba(43,196,188,0.13), rgba(39,114,188,0.08));
          box-shadow: inset 0 1px 0 rgba(255,255,255,0.08);
          color: rgba(255,255,255,0.98);
          caret-color: #8fefff;
          font: 600 12px/1.2 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
          letter-spacing: 0.1px;
          transition: border-color 150ms ease, box-shadow 150ms ease, opacity 150ms ease, background 150ms ease;
        }
        input::placeholder { color: rgba(220,238,250,0.58); }
        .clear {
          position: absolute;
          right: 6px;
          top: 50%;
          transform: translateY(-50%);
          width: 28px;
          height: 28px;
          border-radius: 50%;
          border: 1px solid rgba(135,225,255,0.20);
          background: rgba(255,255,255,0.055);
          color: rgba(229,244,255,0.86);
          display: grid;
          place-items: center;
          padding: 0;
          cursor: pointer;
          z-index: 3;
        }
        .clear ha-icon { --mdc-icon-size: 17px; }
        .clear:active { transform: translateY(-50%) scale(0.94); }
        .wrap.disabled .clear {
          opacity: 0.28;
          pointer-events: none;
        }
        input:focus {
          border-color: rgba(132,232,255,0.92);
          box-shadow: 0 0 0 2px rgba(65,186,255,0.18), 0 0 14px rgba(51,181,255,0.20), inset 0 1px 0 rgba(255,255,255,0.10);
          background: linear-gradient(145deg, rgba(39,176,191,0.19), rgba(38,109,190,0.12));
        }
        .wrap.limited input {
          border-color: rgba(255,194,96,0.48);
          background: linear-gradient(145deg, rgba(170,107,34,0.14), rgba(74,72,142,0.09));
        }
        .wrap.limited input:focus {
          border-color: rgba(255,207,123,0.92);
          box-shadow: 0 0 0 2px rgba(255,168,74,0.15), 0 0 14px rgba(255,157,62,0.16);
        }
        .wrap.disabled input {
          opacity: 0.43;
          cursor: default;
          border-color: rgba(255,255,255,0.10);
          background: rgba(255,255,255,0.025);
        }
        .wrap.disabled .icon { opacity: 0.40; }
        .wrap.busy input { opacity: 0.68; }
      </style>
      <div class="wrap disabled">
        <ha-icon class="icon" icon="mdi:keyboard-outline"></ha-icon>
        <input
          type="text"
          maxlength="100"
          inputmode="text"
          enterkeyhint="send"
          autocomplete="off"
          autocorrect="off"
          autocapitalize="off"
          spellcheck="false"
          placeholder="Keyboard unavailable"
          disabled
          aria-label="Type on TV"
        />
        <button class="clear" type="button" title="Clear TV search" aria-label="Clear TV search">
          <ha-icon icon="mdi:close"></ha-icon>
        </button>
      </div>
    `;
    this._wrap = this.shadowRoot.querySelector(".wrap");
    this._input = this.shadowRoot.querySelector("input");
    this._clear = this.shadowRoot.querySelector(".clear");

    // The card lives inside a button-card based remote. Stop keyboard events
    // from bubbling to the outer button-card. In particular, outer button
    // handlers can consume Space as a button activation key.
    this._input.addEventListener("keydown", (event) => {
      event.stopPropagation();

      if (event.key === "Enter") {
        event.preventDefault();
        this._submit();
        return;
      }

      // Guarantee one literal space in the text field instead of allowing the
      // parent Lovelace/button-card keyboard handler to swallow it.
      if (event.key === " " || event.key === "Spacebar" || event.code === "Space") {
        event.preventDefault();
        const start = this._input.selectionStart ?? this._input.value.length;
        const end = this._input.selectionEnd ?? start;
        this._input.setRangeText(" ", start, end, "end");
        this._input.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
        return;
      }

      // If the local field is already empty, Backspace is interpreted as a
      // real TV Backspace. This permits fine editing of a search already shown
      // on the TV without putting old TV text back into the browser field.
      if (event.key === "Backspace" && this._input.value.length === 0) {
        event.preventDefault();
        this._sendBackspace();
      }
    });

    this._input.addEventListener("keyup", (event) => event.stopPropagation());
    this._input.addEventListener("keypress", (event) => event.stopPropagation());

    this._clear.addEventListener("pointerdown", (event) => {
      // Do not let the button steal focus from the text input on mobile.
      event.preventDefault();
      event.stopPropagation();
    });
    this._clear.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      this._clearTv();
    });
  }

  _state() {
    if (!this._hass || !this._config) return null;
    return this._hass.states[this._config.entity] || null;
  }

  _update() {
    if (!this._built || !this._input || !this._wrap) return;
    const stateObj = this._state();
    const app = stateObj?.state || "Unknown";
    const support = stateObj?.attributes?.keyboard_support || "unsupported";
    const available = support === "supported" || support === "limited";

    if (this._lastApp !== app && !available && document.activeElement !== this._input) {
      // Keep typed text locally, but never leave a mobile keyboard open when
      // the foreground app changes to an unsupported target.
      this._input.blur();
    }
    this._lastApp = app;

    this._wrap.classList.toggle("limited", support === "limited");
    this._wrap.classList.toggle("disabled", !available);
    this._wrap.classList.toggle("busy", this._busy);
    this._input.disabled = !available || this._busy;
    if (this._clear) {
      this._clear.disabled = !available || this._busy;
      this._clear.title = app === "HBO Max"
        ? "Clear HBO Max search (limited app support)"
        : "Clear TV search";
    }

    if (this._busy) {
      this._input.placeholder = `Sending to ${app}…`;
    } else if (support === "supported") {
      this._input.placeholder = `Type on ${app}…`;
      this._input.title = `${app}: keyboard supported`;
    } else if (support === "limited") {
      this._input.placeholder = `${app} · limited keyboard`;
      this._input.title = "HBO Max may drop physical-keyboard characters";
    } else if (app === "Amazon Prime" || app === "Disney+") {
      this._input.placeholder = `${app} · keyboard unsupported`;
      this._input.title = `${app} does not accept physical keyboard text`;
    } else if (app === "Partner TV") {
      this._input.placeholder = "Partner TV · keyboard unavailable";
      this._input.title = "Samsung BLE keyboard field is disabled on Partner TV";
    } else if (app === "Off") {
      this._input.placeholder = "TV is off";
      this._input.title = "TV is off";
    } else {
      this._input.placeholder = "Keyboard unavailable in current TV screen";
      this._input.title = "Open Netflix, YouTube, Apple TV or HBO Max";
    }
  }

  async _callServiceRef(serviceRef, data = {}) {
    const splitAt = serviceRef.indexOf(".");
    if (splitAt <= 0) throw new Error("HomeHub TV Keyboard: service must be domain.service");
    const domain = serviceRef.slice(0, splitAt);
    const service = serviceRef.slice(splitAt + 1);
    return this._hass.callService(domain, service, data);
  }

  async _sendBackspace() {
    if (!this._hass || this._busy || this._input.disabled) return;
    try {
      await this._callServiceRef(this._config.backspace_service);
    } catch (error) {
      console.error("HomeHub TV Keyboard backspace failed", error);
    }
  }

  async _clearTv() {
    if (!this._hass || this._busy || this._input.disabled) return;

    this._input.value = "";
    try {
      await this._callServiceRef(this._config.clear_service);
    } catch (error) {
      console.error("HomeHub TV Keyboard clear failed", error);
      this._input.title = "Clear failed — check ESP32/Wi-Fi/Bluetooth connection";
    }
  }

  async _submit() {
    if (!this._hass || this._busy || this._input.disabled) return;
    const text = this._input.value;
    if (!text || text.length > 100) return;

    this._busy = true;
    this._update();
    try {
      await this._callServiceRef(this._config.submit_service, { text });
      this._input.value = "";
      this._input.blur();
    } catch (error) {
      console.error("HomeHub TV Keyboard send failed", error);
      this._input.title = "Send failed — check ESP32/Wi-Fi/Bluetooth connection";
    } finally {
      this._busy = false;
      this._update();
    }
  }
}

if (!customElements.get("homehub-tv-keyboard-card")) {
  customElements.define("homehub-tv-keyboard-card", HomeHubTVKeyboardCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "homehub-tv-keyboard-card")) {
  window.customCards.push({
    type: "homehub-tv-keyboard-card",
    name: "HomeHub TV Keyboard",
    description: "Compact app-aware Samsung TV keyboard field for the Home Hub remote drawer",
    preview: false,
  });
}
