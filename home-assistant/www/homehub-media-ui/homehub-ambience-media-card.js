/*
 * Home Hub Ambience Media Card
 * Version 2.0.0 — 2026-08-29
 *
 * Catalog-driven Lovelace card for Home Hub ambience Visuals and Soundtracks.
 * Reads media IDs dynamically from an input_select's attributes.options.
 * No media IDs are hardcoded in this frontend component.
 *
 * Install:
 *   /homeassistant/www/homehub-ambience-media-card.js
 * Lovelace Resource:
 *   /local/homehub-ambience-media-card.js?v=1
 * Resource type:
 *   JavaScript Module
 */

class HomeHubAmbienceMediaCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = null;
    this._hass = null;
    this._browseId = null;
    this._lastSelected = null;
    this._pointerStart = null;
    this._suppressClickUntil = 0;
    this._previewGeneration = 1;
    this._previewCacheToken = Date.now();
    this._lastRefreshState = undefined;
    this._lastOptionsSignature = undefined;
  }

  setConfig(config) {
    if (!config || !config.media_kind) {
      throw new Error("homehub-ambience-media-card: media_kind is required");
    }
    if (!["visual", "soundtrack"].includes(config.media_kind)) {
      throw new Error("homehub-ambience-media-card: media_kind must be visual or soundtrack");
    }

    const defaults = config.media_kind === "visual"
      ? {
          entity: "input_select.homehub_media_ambience_ui_visual",
          favorites_entity: "input_select.homehub_media_ambience_ui_visual_favorites",
          enabled_entity: "input_boolean.homehub_media_ambience_ui_visual_enabled",
          height: 144,
          exclude: ["No visual"],
          preview_base: "/local/Ambience",
          refresh_entity: "input_button.homehub_media_ambience_ui_refresh_placeholder",
        }
      : {
          entity: "input_select.homehub_media_ambience_ui_soundtrack",
          favorites_entity: "input_select.homehub_media_ambience_ui_soundtrack_favorites",
          enabled_entity: "input_boolean.homehub_media_ambience_ui_audio_enabled",
          height: 96,
          exclude: ["No soundtrack"],
          preview_base: "/local/Ambience",
          refresh_entity: "input_button.homehub_media_ambience_ui_refresh_placeholder",
        };

    this._config = { ...defaults, ...config };
    this._render();
  }

  set hass(hass) {
    const refreshEntity =
      this._config?.refresh_entity ||
      "input_button.homehub_media_ambience_ui_refresh_placeholder";

    const refreshState = hass?.states?.[refreshEntity]?.state ?? "";
    const options = hass?.states?.[this._config?.entity]?.attributes?.options;
    const optionsSignature = JSON.stringify(Array.isArray(options) ? options : []);

    const refreshChanged =
      this._lastRefreshState !== undefined &&
      refreshState !== this._lastRefreshState;

    const catalogChanged =
      this._lastOptionsSignature !== undefined &&
      optionsSignature !== this._lastOptionsSignature;

    if (refreshChanged || catalogChanged) {
        this._previewGeneration += 1;
      this._previewCacheToken = Date.now();
    }

    this._lastRefreshState = refreshState;
    this._lastOptionsSignature = optionsSignature;
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return this._config?.media_kind === "visual" ? 3 : 2;
  }

  getGridOptions() {
    return {
      columns: "full",
      rows: this._config?.media_kind === "visual" ? 3 : 2,
      min_rows: 1,
    };
  }

  _state(entityId) {
    return this._hass?.states?.[entityId];
  }

  _options(entityId) {
    const value = this._state(entityId)?.attributes?.options;
    return Array.isArray(value) ? value : [];
  }

  _mediaIds() {
    if (!this._config) return [];
    const excluded = new Set(this._config.exclude || []);
    return this._options(this._config.entity)
      .filter((id) => typeof id === "string" && id && !excluded.has(id));
  }

  _favoriteIds() {
    if (!this._config) return new Set();
    return new Set(
      this._options(this._config.favorites_entity)
        .filter((id) => id && id !== "No favorites")
    );
  }

  _prettyName(id) {
    const special = {
      mozart_rondo_k511: "Mozart · Rondo K.511",
      dog_tv: "DOG TV",
    };
    if (special[id]) return special[id];

    let text = String(id || "");
    if (text.toLowerCase().startsWith("ambient_")) {
      text = text.slice("ambient_".length);
    }

    return text
      .replace(/[_-]+/g, " ")
      .trim()
      .split(/\s+/)
      .map((word) => {
        const lower = word.toLowerCase();
        if (lower === "tv") return "TV";
        if (lower === "hd") return "HD";
        if (lower === "4k") return "4K";
        return word ? word.charAt(0).toUpperCase() + word.slice(1) : "";
      })
      .join(" ");
  }

  _previewCandidates(id) {
    const base = String(this._config?.preview_base || "/local/Ambience").replace(/\/$/, "");
    const safeId = encodeURIComponent(String(id || ""));
    const token = encodeURIComponent(String(this._previewCacheToken));
    return [
      `${base}/${safeId}.webp?v=${token}`,
      `${base}/${safeId}.png?v=${token}`,
    ];
  }

  _currentBrowseId(ids, selected) {
    if (selected !== this._lastSelected && ids.includes(selected)) {
      this._browseId = selected;
    }
    this._lastSelected = selected;

    if (!ids.includes(this._browseId)) {
      this._browseId = ids.includes(selected) ? selected : (ids[0] || null);
    }
    return this._browseId;
  }

  _move(delta) {
    const ids = this._mediaIds();
    if (ids.length < 2) return;

    const selected = this._state(this._config.entity)?.state;
    const current = this._currentBrowseId(ids, selected);
    let index = Math.max(0, ids.indexOf(current));
    index = (index + delta + ids.length) % ids.length;
    this._browseId = ids[index];
    this._render();
  }

  _callService(domain, service, data = {}, target = undefined) {
    if (!this._hass) return Promise.resolve();
    return this._hass.callService(domain, service, data, target);
  }

  _selectVisible() {
    if (Date.now() < this._suppressClickUntil) return;
    const id = this._browseId;
    if (!id || !this._config) return;

    const selected = this._state(this._config.entity)?.state;
    if (selected === id) return;

    this._callService(
      "input_select",
      "select_option",
      { option: id },
      { entity_id: this._config.entity }
    );
  }

  _toggleEnabled(event) {
    event.preventDefault();
    event.stopPropagation();

    if (this._config.media_kind === "visual") {
      this._callService(
        "input_boolean",
        "toggle",
        {},
        { entity_id: this._config.enabled_entity }
      );
    } else {
      this._callService(
        "script",
        "homehub_media_ambience_ui_toggle_audio"
      );
    }
  }

  _toggleFavorite(event) {
    event.preventDefault();
    event.stopPropagation();

    const id = this._browseId;
    if (!id) return;

    this._callService(
      "script",
      "homehub_media_ambience_ui_toggle_media_favorite",
      {
        media_kind: this._config.media_kind === "visual" ? "video" : "soundtrack",
        media_id: id,
      }
    );
  }

  _onPointerDown(event) {
    if (event.button !== undefined && event.button !== 0) return;
    this._pointerStart = {
      x: event.clientX,
      y: event.clientY,
      time: performance.now(),
    };
  }

  _onPointerUp(event) {
    if (!this._pointerStart) return;

    const dx = event.clientX - this._pointerStart.x;
    const dy = event.clientY - this._pointerStart.y;
    const dt = performance.now() - this._pointerStart.time;
    this._pointerStart = null;

    // Horizontal swipe only. Vertical movement remains available to drawer scroll.
    if (
      Math.abs(dx) >= 38 &&
      Math.abs(dx) > Math.abs(dy) * 1.15 &&
      dt < 900
    ) {
      this._suppressClickUntil = Date.now() + 300;
      this._move(dx < 0 ? 1 : -1);
    }
  }

  _onKeyDown(event) {
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      this._move(-1);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      this._move(1);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      this._selectVisible();
    }
  }

  _render() {
    if (!this.shadowRoot || !this._config) return;

    if (!this._hass) {
      this.shadowRoot.innerHTML = `
        <style>${this._styles()}</style>
        <ha-card class="media-card loading"></ha-card>
      `;
      return;
    }

    const ids = this._mediaIds();
    const selected = this._state(this._config.entity)?.state || "";
    const browseId = this._currentBrowseId(ids, selected);
    const favorites = this._favoriteIds();
    const enabled = this._state(this._config.enabled_entity)?.state === "on";
    const isSelected = Boolean(browseId && browseId === selected);
    const isFavorite = Boolean(browseId && favorites.has(browseId));
    const isVisual = this._config.media_kind === "visual";
    const height = Number(this._config.height) || (isVisual ? 144 : 96);

    if (!browseId) {
      this.shadowRoot.innerHTML = `
        <style>${this._styles()}</style>
        <ha-card class="media-card empty" style="height:${height}px">
          <div class="empty-text">No ${isVisual ? "visuals" : "soundtracks"} in catalog</div>
        </ha-card>
      `;
      return;
    }

    const title = this._prettyName(browseId);
    const enabledIcon = isVisual
      ? "mdi:power"
      : (enabled ? "mdi:music-note" : "mdi:music-note-off");

    const previewCandidates = this._previewCandidates(browseId);

    this.shadowRoot.innerHTML = `
      <style>${this._styles()}</style>
      <ha-card
        class="media-card ${isVisual ? "visual" : "soundtrack"} ${isSelected ? "selected" : ""}"
        style="height:${height}px"
        tabindex="0"
        role="button"
        aria-label="${this._escape(title)}${isSelected ? ", selected" : ""}"
      >
        <img
          class="preview"
          alt="${this._escape(title)} preview"
          data-media-id="${this._escape(browseId)}"
          data-stage="webp"
        >

        ${isVisual ? `
          <div class="visual-fallback">
            <ha-icon icon="mdi:image-outline"></ha-icon>
            <span>${this._escape(title)}</span>
          </div>
        ` : `
          <div class="soundtrack-fallback">
            <ha-icon icon="mdi:music-note-outline"></ha-icon>
            <div class="soundtrack-copy">
              <div class="soundtrack-title">${this._escape(title)}</div>
              <div class="soundtrack-label">Independent soundtrack</div>
            </div>
          </div>
          <div class="soundtrack-preview-overlay">
            <div class="soundtrack-title">${this._escape(title)}</div>
            <div class="soundtrack-label">Independent soundtrack</div>
          </div>
        `}

        <button class="round-action enabled-action ${enabled ? "active" : ""}" type="button"
          aria-label="${enabled ? "Disable" : "Enable"} ${isVisual ? "visual" : "audio"}">
          <ha-icon icon="${enabledIcon}"></ha-icon>
        </button>

        <button class="round-action favorite-action ${isFavorite ? "active" : ""}" type="button"
          aria-label="${isFavorite ? "Remove from favorites" : "Add to favorites"}">
          <ha-icon icon="mdi:star"></ha-icon>
        </button>

        <div class="selected-check ${isSelected ? "visible" : ""}" aria-hidden="true">✓</div>
      </ha-card>
    `;

    const card = this.shadowRoot.querySelector(".media-card");
    const previewImage = this.shadowRoot.querySelector("img.preview");
    const enabledButton = this.shadowRoot.querySelector(".enabled-action");
    const favoriteButton = this.shadowRoot.querySelector(".favorite-action");

    // Deterministic preview loading:
    //   exact media ID -> .webp -> .png -> fallback UI.
    // The <img> is inserted without src, handlers are attached first, and only
    // then is the request started. This avoids the previous detached Image()
    // preloader/state-machine race and makes Visual and Audio use the same path.
    if (previewImage) {
      const renderedId = browseId;
      let candidateIndex = 0;

      const loadCandidate = () => {
        if (renderedId !== this._browseId) return;

        if (candidateIndex >= previewCandidates.length) {
          // Both candidates failed. With no src, the normal fallback content
          // underneath the image remains visible.
          previewImage.removeAttribute("src");
          return;
        }

        previewImage.dataset.stage = candidateIndex === 0 ? "webp" : "png";
        previewImage.src = previewCandidates[candidateIndex];
      };

      previewImage.addEventListener("error", () => {
        if (renderedId !== this._browseId) return;
        candidateIndex += 1;
        loadCandidate();
      });

      loadCandidate();
    }

    card.addEventListener("click", () => this._selectVisible());
    card.addEventListener("pointerdown", (event) => this._onPointerDown(event));
    card.addEventListener("pointerup", (event) => this._onPointerUp(event));
    card.addEventListener("pointercancel", () => { this._pointerStart = null; });
    card.addEventListener("keydown", (event) => this._onKeyDown(event));

    enabledButton?.addEventListener("click", (event) => this._toggleEnabled(event));
    favoriteButton?.addEventListener("click", (event) => this._toggleFavorite(event));
  }

  _styles() {
    return `
      :host {
        display: block;
        width: 100%;
        min-width: 0;
      }

      .media-card {
        position: relative;
        width: 100%;
        box-sizing: border-box;
        border-radius: 16px;
        overflow: hidden;
        border: 1px solid rgba(121,199,255,0.14);
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.055);
        transition: border 160ms ease, box-shadow 160ms ease;
        cursor: pointer;
        touch-action: pan-y;
        user-select: none;
        -webkit-user-select: none;
        outline: none;
      }

      .media-card:focus-visible {
        outline: 2px solid rgba(179,232,255,0.90);
        outline-offset: 2px;
      }

      .media-card.selected {
        border: 2px solid rgba(179,232,255,0.98);
        box-shadow:
          0 0 0 1px rgba(64,178,255,0.22),
          0 0 16px rgba(40,166,255,0.34),
          inset 0 1px 0 rgba(255,255,255,0.14);
      }

      .media-card.visual {
        padding: 0;
        background:
          radial-gradient(circle at 50% 18%, rgba(83,139,183,0.16), transparent 48%),
          linear-gradient(155deg, rgba(255,255,255,0.075), rgba(255,255,255,0.028));
      }

      .preview {
        position: absolute;
        inset: 0;
        width: 100%;
        height: 100%;
        object-fit: cover;
        opacity: 1;
        pointer-events: none;
        z-index: 1;
      }

      .visual-fallback {
        position: absolute;
        inset: 0;
        z-index: 0;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 7px;
        padding: 16px;
        box-sizing: border-box;
        text-align: center;
        color: rgba(232,247,255,0.82);
        font-size: 11px;
        font-weight: 700;
      }

      .visual-fallback ha-icon {
        width: 28px;
        height: 28px;
        color: rgba(218,240,255,0.72);
      }

      .media-card.soundtrack {
        padding: 16px 10px 10px 10px;
        background:
          linear-gradient(155deg, rgba(255,255,255,0.075), rgba(255,255,255,0.028));
      }

      .media-card.soundtrack .preview {
        position: absolute;
        inset: 0;
        width: 100%;
        height: 100%;
        object-fit: cover;
        opacity: 1;
        pointer-events: none;
        z-index: 1;
      }

      .soundtrack-fallback {
        position: absolute;
        inset: 42px 8px 7px 8px;
        z-index: 0;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 7px;
        min-width: 0;
        text-align: center;
      }

      .soundtrack-fallback ha-icon {
        width: 28px;
        height: 28px;
        color: rgba(218,240,255,0.72);
      }

      .soundtrack-copy {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: flex-end;
        min-width: 0;
      }

      .soundtrack-preview-overlay {
        display: none;
      }

      .soundtrack-title {
        max-width: 100%;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        color: white;
        font-size: 12px;
        line-height: 15px;
        font-weight: 750;
        text-align: center;
      }

      .soundtrack-label {
        margin-top: 1px;
        color: rgba(255,255,255,0.72);
        font-size: 9px;
        line-height: 12px;
        text-align: center;
      }

      .round-action {
        position: absolute;
        top: 8px;
        width: 30px;
        height: 30px;
        margin: 0;
        padding: 0;
        display: grid;
        place-items: center;
        box-sizing: border-box;
        border-radius: 50%;
        background: rgba(255,255,255,0.84);
        border: 1px solid rgba(255,255,255,0.32);
        box-shadow: 0 2px 8px rgba(0,0,0,0.28);
        z-index: 5;
        cursor: pointer;
        color: rgba(75,75,75,0.74);
        line-height: 0;
        -webkit-tap-highlight-color: transparent;
      }

      .round-action:focus-visible {
        outline: 2px solid rgba(179,232,255,0.98);
        outline-offset: 1px;
      }

      .round-action ha-icon {
        display: block;
        width: 18px;
        height: 18px;
        margin: 0;
        padding: 0;
        color: currentColor;
        --mdc-icon-size: 18px;
      }

      .round-action.active {
        color: rgb(245,148,37);
      }

      .enabled-action {
        left: 8px;
      }

      .favorite-action {
        left: 44px;
        color: rgba(90,90,90,0.72);
      }

      .selected-check {
        position: absolute;
        top: 8px;
        right: 10px;
        width: 26px;
        height: 26px;
        display: flex;
        align-items: center;
        justify-content: center;
        border-radius: 50%;
        background: rgba(9,29,54,0.72);
        border: 1px solid rgba(205,241,255,0.72);
        box-shadow: 0 2px 8px rgba(0,0,0,0.28);
        font-size: 18px;
        font-weight: 800;
        line-height: 1;
        color: rgba(225,248,255,1);
        opacity: 0;
        pointer-events: none;
        z-index: 4;
      }

      .selected-check.visible {
        opacity: 1;
      }

      .empty,
      .loading {
        background: linear-gradient(155deg, rgba(255,255,255,0.055), rgba(255,255,255,0.020));
      }

      .empty-text {
        position: absolute;
        inset: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 12px;
        box-sizing: border-box;
        color: rgba(255,255,255,0.58);
        font-size: 10px;
        text-align: center;
      }
    `;
  }

  _escape(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }
}

if (!customElements.get("homehub-ambience-media-card")) {
  customElements.define("homehub-ambience-media-card", HomeHubAmbienceMediaCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "homehub-ambience-media-card")) {
  window.customCards.push({
    type: "homehub-ambience-media-card",
    name: "Home Hub Ambience Media Card",
    description: "Dynamic catalog-driven Visual/Soundtrack swipe card for Home Hub.",
    preview: true,
  });
}

console.info(
  "%c HOMEHUB-AMBIENCE-MEDIA-CARD %c v2.0.0 ",
  "background:#17324d;color:#dff4ff;font-weight:700;padding:2px 5px;border-radius:3px 0 0 3px",
  "background:#4d6d88;color:#fff;font-weight:700;padding:2px 5px;border-radius:0 3px 3px 0"
);
