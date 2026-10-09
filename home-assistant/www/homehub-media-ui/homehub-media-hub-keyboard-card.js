class HomeHubMediaHubKeyboardCard extends HTMLElement {
  setConfig(config) {
    if (!config.type_service || !config.clear_service) {
      throw new Error("type_service and clear_service are required");
    }

    this._config = {
      placeholder: "Type to search…",
      title: "Media Hub keyboard",
      width: "194px",
      ...config,
    };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
  }

  connectedCallback() {
    this._render();
  }

  getCardSize() {
    return 1;
  }

  _render() {
    if (!this._config || this.shadowRoot) {
      return;
    }

    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        :host { display: block; width: ${this._escapeCss(this._config.width)}; }
        ha-card {
          box-sizing: border-box;
          width: 100%;
          padding: 6px;
          border-radius: 19px;
          border: 1px solid rgba(154, 210, 255, 0.18);
          background: linear-gradient(155deg, rgba(255,255,255,0.105), rgba(255,255,255,0.035));
          box-shadow: inset 0 1px 0 rgba(255,255,255,0.10), 0 4px 12px rgba(0,0,0,0.12);
        }
        .field { position: relative; display: flex; align-items: center; }
        .icon { position: absolute; left: 10px; color: rgba(210,235,255,0.82); font-size: 16px; line-height: 1; pointer-events: none; }
        input {
          box-sizing: border-box;
          width: 100%;
          height: 32px;
          border: 0;
          outline: 0;
          border-radius: 13px;
          padding: 0 35px 0 31px;
          background: rgba(3, 20, 42, 0.48);
          color: white;
          font: inherit;
          font-size: 12px;
          caret-color: #9ed7ff;
        }
        input::placeholder { color: rgba(220,239,255,0.58); }
        input:focus { box-shadow: 0 0 0 1px rgba(111,199,255,0.65), 0 0 12px rgba(47,154,243,0.28); }
        button {
          position: absolute;
          right: 4px;
          width: 25px;
          height: 25px;
          padding: 0;
          border: 1px solid rgba(154, 210, 255, 0.18);
          border-radius: 50%;
          background: rgba(4, 28, 60, 0.72);
          color: rgba(225,242,255,0.92);
          font-size: 20px;
          line-height: 21px;
          cursor: pointer;
        }
        button:active { background: rgba(57, 118, 180, 0.68); }
        .status { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
      </style>
      <ha-card>
        <div class="field">
          <span class="icon" aria-hidden="true">⌨</span>
          <input type="text" inputmode="text" enterkeyhint="send" autocomplete="off" autocapitalize="none" spellcheck="false" placeholder="${this._escapeHtml(this._config.placeholder)}" aria-label="${this._escapeHtml(this._config.title)}">
          <button type="button" aria-label="Clear the active Sonarr or Radarr search field">×</button>
        </div>
        <div class="status" aria-live="polite"></div>
      </ha-card>`;

    this._input = root.querySelector("input");
    this._clearButton = root.querySelector("button");
    this._status = root.querySelector(".status");
    this._input.addEventListener("beforeinput", (event) => {
      // iOS WebViews can expose Space only as a beforeinput event for a
      // custom-element input. Insert it explicitly so the value that reaches
      // Home Assistant retains the literal space.
      if (event.inputType === "insertText" && event.data === " ") {
        event.preventDefault();
        this._insertText(" ");
      }
    });
    this._input.addEventListener("keydown", (event) => {
      if (event.key === " " && !event.isComposing) {
        event.preventDefault();
        this._insertText(" ");
        return;
      }
      if (event.key === "Enter" && !event.isComposing) {
        event.preventDefault();
        this._type();
      }
    });
    this._clearButton.addEventListener("click", () => this._clear());
  }

  async _callService(serviceName, data = {}) {
    if (!this._hass) {
      throw new Error("Home Assistant is not connected");
    }
    const [domain, service] = serviceName.split(".");
    if (!domain || !service || serviceName.split(".").length !== 2) {
      throw new Error("Invalid service");
    }
    await this._hass.callService(domain, service, data);
  }

  async _type() {
    // Do not trim the transmitted text: in particular, an internal space in
    // a title such as "Dark Knight" must arrive at the Ubuntu browser intact.
    const text = this._input.value;
    if (!text.trim()) {
      this._setStatus("Type a title first");
      return;
    }

    this._input.disabled = true;
    try {
      await this._callService(this._config.type_service, { text });
      this._input.value = "";
      this._setStatus("Text sent — use D-pad OK to search");
    } catch (error) {
      this._setStatus("Could not send text");
    } finally {
      this._input.disabled = false;
      this._input.focus();
    }
  }

  async _clear() {
    this._input.value = "";
    this._clearButton.disabled = true;
    try {
      await this._callService(this._config.clear_service);
      this._setStatus("Search field cleared");
    } catch (error) {
      this._setStatus("Could not clear the search field");
    } finally {
      this._clearButton.disabled = false;
      this._input.focus();
    }
  }

  _insertText(text) {
    const start = this._input.selectionStart ?? this._input.value.length;
    const end = this._input.selectionEnd ?? start;
    const current = this._input.value;
    this._input.value = `${current.slice(0, start)}${text}${current.slice(end)}`;
    const caret = start + text.length;
    this._input.setSelectionRange(caret, caret);
    this._input.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
  }

  _setStatus(message) {
    this._status.textContent = message;
  }

  _escapeHtml(value) {
    return String(value).replace(/[&<>'"]/g, (character) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
    }[character]));
  }

  _escapeCss(value) {
    return String(value).replace(/[;{}]/g, "");
  }
}

const tagName = "homehub-media-hub-keyboard-card";
if (!customElements.get(tagName)) {
  customElements.define(tagName, HomeHubMediaHubKeyboardCard);
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: tagName,
  name: "HomeHub Media Hub Keyboard",
  description: "Native text input and remote search clear for the Ubuntu Media Hub browser",
});
