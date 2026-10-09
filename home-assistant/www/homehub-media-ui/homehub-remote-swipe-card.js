// Home Hub Remote Swipe v0.3.
// Adds a browser-local lock used only while the Media Hub mouse pad is active.

const HOMEHUB_MEDIA_HUB_SWIPE_LOCK_EVENT = "homehub-media-hub-swipe-lock";

class HomeHubRemoteSwipeCard extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._config = null;
    this._surface = null;
    this._pointerId = null;
    this._startX = 0;
    this._startY = 0;
    this._lastX = 0;
    this._lastY = 0;
    this._horizontal = false;
    this._suppressClick = false;
    this._attachTimer = null;
    this._swipeLocked = window.__homehubMediaHubSwipeLocked === true;

    this._onPointerDown = this._onPointerDown.bind(this);
    this._onPointerMove = this._onPointerMove.bind(this);
    this._onPointerUp = this._onPointerUp.bind(this);
    this._onPointerCancel = this._onPointerCancel.bind(this);
    this._onClickCapture = this._onClickCapture.bind(this);
    this._onSwipeLock = this._onSwipeLock.bind(this);
  }

  setConfig(config) {
    if (!config) throw new Error("HomeHub Remote Swipe: configuration required");
    const options = Array.isArray(config.options) ? config.options : [];
    if (options.length < 2) throw new Error("HomeHub Remote Swipe: at least two options are required");

    this._config = {
      entity: config.entity || "input_select.homehub_media_remote_target",
      options,
      threshold: Number(config.threshold) || 55,
      wrap: config.wrap !== false,
      allow_mouse: config.allow_mouse === true,
    };
    this._swipeLocked = window.__homehubMediaHubSwipeLocked === true;
    this._setInvisibleLayout();
    this._attachSoon();
  }

  set hass(hass) {
    this._hass = hass;
  }

  connectedCallback() {
    this._swipeLocked = window.__homehubMediaHubSwipeLocked === true;
    window.addEventListener(HOMEHUB_MEDIA_HUB_SWIPE_LOCK_EVENT, this._onSwipeLock);
    this._setInvisibleLayout();
    this._attachSoon();
  }

  disconnectedCallback() {
    window.removeEventListener(HOMEHUB_MEDIA_HUB_SWIPE_LOCK_EVENT, this._onSwipeLock);
    if (this._attachTimer) window.clearTimeout(this._attachTimer);
    this._attachTimer = null;
    this._detach();
  }

  getCardSize() {
    return 0;
  }

  _setInvisibleLayout() {
    this.style.display = "block";
    this.style.width = "0";
    this.style.height = "0";
    this.style.minHeight = "0";
    this.style.margin = "0";
    this.style.padding = "0";
    this.style.overflow = "hidden";
    this.style.pointerEvents = "none";
  }

  _isVerticalStack(node) {
    return (node?.localName || "").includes("vertical-stack");
  }

  _findGestureSurface() {
    let node = this;
    for (let depth = 0; depth < 24 && node; depth += 1) {
      if (this._isVerticalStack(node)) return node;
      if (node.parentElement) {
        node = node.parentElement;
        continue;
      }
      const root = node.getRootNode?.();
      if (root instanceof ShadowRoot && root.host && root.host !== node) {
        node = root.host;
        continue;
      }
      break;
    }
    return null;
  }

  _attachSoon(attempt = 0) {
    if (this._attachTimer) window.clearTimeout(this._attachTimer);
    this._attachTimer = window.setTimeout(() => {
      this._attachTimer = null;
      if (!this.isConnected || !this._config) return;
      const surface = this._findGestureSurface();
      if (!surface) {
        if (attempt < 20) this._attachSoon(attempt + 1);
        else console.error("HomeHub Remote Swipe: vertical-stack surface not found");
        return;
      }
      if (this._surface === surface) return;
      this._detach();
      this._surface = surface;
      this._oldTouchAction = this._surface.style.touchAction;
      this._surface.style.touchAction = "pan-y";
      this._surface.addEventListener("pointerdown", this._onPointerDown, true);
      this._surface.addEventListener("pointermove", this._onPointerMove, true);
      this._surface.addEventListener("pointerup", this._onPointerUp, true);
      this._surface.addEventListener("pointercancel", this._onPointerCancel, true);
      this._surface.addEventListener("click", this._onClickCapture, true);
      this.dataset.swipeAttached = "true";
    }, attempt === 0 ? 0 : 100);
  }

  _detach() {
    if (!this._surface) return;
    this._surface.removeEventListener("pointerdown", this._onPointerDown, true);
    this._surface.removeEventListener("pointermove", this._onPointerMove, true);
    this._surface.removeEventListener("pointerup", this._onPointerUp, true);
    this._surface.removeEventListener("pointercancel", this._onPointerCancel, true);
    this._surface.removeEventListener("click", this._onClickCapture, true);
    this._surface.style.touchAction = this._oldTouchAction || "";
    this._surface = null;
    delete this.dataset.swipeAttached;
    this._resetGesture();
  }

  _onSwipeLock(event) {
    this._swipeLocked = Boolean(event?.detail?.locked);
    if (this._swipeLocked) {
      this._suppressClick = false;
      this._resetGesture();
    }
  }

  _startsInTextControl(event) {
    return event.composedPath().some((node) => ["INPUT", "TEXTAREA", "SELECT"].includes(node?.tagName));
  }

  _onPointerDown(event) {
    if (this._swipeLocked || !this._config || !this._hass || this._pointerId !== null) return;
    if (event.pointerType === "mouse" && !this._config.allow_mouse) return;
    if (this._startsInTextControl(event)) return;
    this._pointerId = event.pointerId;
    this._startX = this._lastX = event.clientX;
    this._startY = this._lastY = event.clientY;
    this._horizontal = false;
  }

  _onPointerMove(event) {
    if (event.pointerId !== this._pointerId) return;
    if (this._swipeLocked) {
      this._resetGesture();
      return;
    }
    this._lastX = event.clientX;
    this._lastY = event.clientY;
    const dx = this._lastX - this._startX;
    const dy = this._lastY - this._startY;
    if (!this._horizontal && Math.abs(dx) >= 12 && Math.abs(dx) > Math.abs(dy) * 1.15) {
      this._horizontal = true;
      try { this._surface?.setPointerCapture(event.pointerId); } catch (_) { /* optional */ }
    }
    if (this._horizontal) event.preventDefault();
  }

  _onPointerUp(event) {
    if (event.pointerId !== this._pointerId) return;
    if (this._swipeLocked) {
      this._resetGesture();
      return;
    }
    const dx = event.clientX - this._startX;
    const dy = event.clientY - this._startY;
    const isSwipe = this._horizontal && Math.abs(dx) >= this._config.threshold && Math.abs(dx) > Math.abs(dy) * 1.15;
    if (isSwipe) {
      event.preventDefault();
      event.stopPropagation();
      this._suppressClick = true;
      window.setTimeout(() => { this._suppressClick = false; }, 350);
      this._selectRelative(dx < 0 ? 1 : -1);
    }
    this._resetGesture();
  }

  _onPointerCancel(event) {
    if (event.pointerId === this._pointerId) this._resetGesture();
  }

  _onClickCapture(event) {
    if (this._swipeLocked || !this._suppressClick) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    this._suppressClick = false;
  }

  _resetGesture() {
    if (this._surface && this._pointerId !== null) {
      try { this._surface.releasePointerCapture(this._pointerId); } catch (_) { /* optional */ }
    }
    this._pointerId = null;
    this._horizontal = false;
  }

  async _selectRelative(step) {
    const state = this._hass?.states?.[this._config.entity]?.state;
    const options = this._config.options;
    const currentIndex = options.indexOf(state);
    if (currentIndex < 0) return;
    let nextIndex = currentIndex + step;
    if (this._config.wrap) nextIndex = (nextIndex + options.length) % options.length;
    else if (nextIndex < 0 || nextIndex >= options.length) return;
    if (navigator.vibrate) navigator.vibrate(10);
    try {
      await this._hass.callService("input_select", "select_option", { entity_id: this._config.entity, option: options[nextIndex] });
    } catch (error) {
      console.error("HomeHub remote swipe failed", error);
    }
  }
}

const tagName = "homehub-remote-swipe-card";
if (!customElements.get(tagName)) customElements.define(tagName, HomeHubRemoteSwipeCard);

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === tagName)) {
  window.customCards.push({
    type: tagName,
    name: "HomeHub Remote Swipe",
    description: "Invisible horizontal-swipe controller for the Home Hub media drawer",
    preview: false,
  });
}
