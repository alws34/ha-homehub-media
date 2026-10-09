"""Websocket commands (K5), services (K6) and HTTP views: TV page state (K7) and artwork proxy."""
from __future__ import annotations

import re
import unicodedata
from functools import partial
from typing import Any

import voluptuous as vol
from aiohttp import web
from homeassistant.components import websocket_api
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.service import async_register_admin_service

from .ambience import STATE_PATH
from .const import ACTIVITIES, COMMANDS, DOMAIN, SIGNAL_UPDATE, TARGETS, TEXT_MAX
from .hub import Hub, loaded_hub, sign
from .library import ART_MAX, YouTubeArt

ART_KEY_RE = re.compile(r"[A-Za-z0-9_=-]{1,512}")
ART_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}  # never SVG/HTML from HA's origin
SIGNED = ("/media/", "/api/homehub_media/art/")  # /local/... is public


def _hub(hass: HomeAssistant) -> Hub:
    if hub := loaded_hub(hass):
        return hub
    raise ServiceValidationError("HomeHub Media is not set up")


def _signed(hass: HomeAssistant, connection: websocket_api.ActiveConnection, path: str | None) -> str | None:
    if path and path.startswith(SIGNED):
        return sign(hass, path, connection.refresh_token_id)
    return path


# ---- websocket (K5) --------------------------------------------------------------------------------


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe"})
@callback
def ws_subscribe(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    _hub(hass)

    @callback
    def send() -> None:
        if hub := loaded_hub(hass):  # looked up each time: the subscription survives entry reloads
            snap = hub.snapshot()
            snap["now_playing"] = [{**n, "image": _signed(hass, connection, n.get("image"))} for n in snap["now_playing"]]
            connection.send_message(websocket_api.event_message(msg["id"], snap))

    connection.subscriptions[msg["id"]] = async_dispatcher_connect(hass, SIGNAL_UPDATE, send)
    connection.send_result(msg["id"])
    send()


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/catalog"})
@callback
def ws_catalog(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    connection.send_result(msg["id"], {
        kind: [{**item, "preview": _signed(hass, connection, item.get("preview"))} for item in items]
        for kind, items in _hub(hass).ambience.catalog().items()})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/library"})
@callback
def ws_library(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    lib = _hub(hass).library
    connection.send_result(msg["id"], {
        server: {kind: [{**item, "image": _signed(hass, connection, item.get("image"))} for item in items]
                 for kind, items in lists.items()}
        for server, lists in (lib.items if lib else {}).items()})


# ---- services (K6) ---------------------------------------------------------------------------------


def _text(value: Any) -> str:
    text = cv.string(value)
    if not 0 < len(text) <= TEXT_MAX or any(unicodedata.category(c)[0] == "C" for c in text):
        raise vol.Invalid(f"text must be 1-{TEXT_MAX} printable characters")
    return text


_APP = vol.Match(r"^[A-Za-z0-9._-]{1,64}\Z")
_TYPE_TARGET = vol.In(("tv", "pc", "keyboard"))
_DELTA = vol.All(vol.Coerce(float), vol.Range(-500, 500))
_PCT = vol.All(vol.Coerce(float), vol.Range(0, 100))  # catalog.validate_composition rounds to int
COMPOSITION = {  # types only; the ambience manager validates ids and the merged composition
    vol.Optional("visual"): vol.Any(None, cv.string), vol.Optional("visual_enabled"): cv.boolean,
    vol.Optional("video_loop"): cv.boolean, vol.Optional("video_random_start"): cv.boolean,
    vol.Optional("original_volume"): _PCT, vol.Optional("soundtrack"): vol.Any(None, cv.string),
    vol.Optional("soundtrack_loop"): cv.boolean, vol.Optional("soundtrack_random_start"): cv.boolean,
    vol.Optional("soundtrack_volume"): _PCT,
    vol.Optional("visuals"): vol.All(cv.ensure_list, [cv.string], vol.Length(max=50)),
}
ADMIN_SERVICES = ("rescan_apps",)  # writes the config entry; a media-folder rescan is harmless for any user
SERVICES: dict[str, tuple[dict, Any]] = {
    "remote": ({vol.Required("command"): vol.In(COMMANDS), vol.Optional("target"): vol.In(TARGETS)},
               lambda hub, d: hub.remote(**d)),
    "send_key": ({vol.Required("key"): vol.Match(r"^KEY_[A-Z0-9_]{1,32}\Z"),
                  vol.Optional("hold_secs"): vol.All(vol.Coerce(float), vol.Range(0, 5))},
                 lambda hub, d: hub.tv.send_key(**d)),
    "launch_app": ({vol.Required("app"): _APP}, lambda hub, d: hub.launch_app(**d)),
    "restart_app": ({vol.Required("app"): _APP}, lambda hub, d: hub.restart_app(**d)),
    "send_text": ({vol.Required("text"): _text, vol.Optional("target"): _TYPE_TARGET},
                  lambda hub, d: hub.send_text(**d)),
    "keyboard_key": ({vol.Required("key"): vol.In(("backspace", "enter", "clear")), vol.Optional("target"): _TYPE_TARGET},
                     lambda hub, d: hub.keyboard_key(**d)),
    "pointer": ({vol.Required("action"): vol.In(("move", "click", "right_click", "scroll")),
                 vol.Optional("dx"): _DELTA, vol.Optional("dy"): _DELTA, vol.Optional("target"): vol.In(("tv", "pc"))},
                lambda hub, d: hub.pointer(**d)),
    "set_volume": ({vol.Required("level"): vol.All(vol.Coerce(int), vol.Range(0, 100))},
                   lambda hub, d: hub.set_volume(**d)),
    "set_activity": ({vol.Required("activity"): vol.In(ACTIVITIES), vol.Optional("force"): cv.boolean},
                     lambda hub, d: hub.activity.set(**d)),
    "channel": ({vol.Required("number"): vol.All(cv.string, vol.Match(r"^\d{1,4}\Z"))},
                lambda hub, d: hub.channel(**d)),
    "ambience_start": ({vol.Required("mode"): cv.slug}, lambda hub, d: hub.ambience.start(d["mode"])),
    "ambience_stop": ({}, lambda hub, d: hub.ambience.stop()),
    "ambience_update": (COMPOSITION, lambda hub, d: hub.ambience.update(dict(d))),
    "save_mode": ({vol.Required("mode"): cv.slug, **COMPOSITION},
                  lambda hub, d: hub.ambience.save_mode(d["mode"], {k: v for k, v in d.items() if k != "mode"})),
    "set_favorite": ({vol.Required("kind"): vol.In(("visual", "soundtrack")),
                      vol.Required("media_id"): vol.Match(r"^[a-z0-9][a-z0-9_-]{0,63}\Z"),  # catalog.ID_RE
                      vol.Required("favorite"): cv.boolean}, lambda hub, d: hub.ambience.set_favorite(**d)),
    "ambience_auto": ({}, lambda hub, d: hub.ambience.auto_start()),
    "ambience_sleep_timer": ({vol.Required("minutes"): vol.All(vol.Coerce(int), vol.Range(0, 600))},
                             lambda hub, d: hub.ambience.set_sleep_timer(**d)),
    "refresh_catalog": ({}, lambda hub, d: hub.ambience.refresh_catalog()),
    "rescan_apps": ({}, lambda hub, d: hub.rescan_apps()),
    "play_library_item": ({vol.Required("server"): vol.In(("plex", "jellyfin")),
                           vol.Required("item_id"): vol.Match(r"^[A-Za-z0-9-]{1,64}\Z")},
                          lambda hub, d: hub.play_library_item(**d)),
    "pc_open": ({vol.Required("client"): cv.slug}, lambda hub, d: hub.pc_open(**d)),
    "pc_close": ({}, lambda hub, d: hub.pc_close()),
}


async def _call(run: Any, call: ServiceCall) -> None:
    hub = _hub(call.hass)
    await hub.command(run(hub, call.data))


# ---- views -----------------------------------------------------------------------------------------


class PageStateView(HomeAssistantView):
    """K7 state for the TV page; reached through a signed path."""

    url, name, requires_auth = STATE_PATH, "api:homehub_media:page_state", True

    async def get(self, request: web.Request) -> web.Response:
        hub = loaded_hub(request.app[KEY_HASS])
        if not (state := hub and hub.ambience.page_state(request.query.get("sid"))):
            raise web.HTTPNotFound  # not loaded, or not the current page session
        return self.json(state, headers={"Cache-Control": "no-store"})


class ArtView(HomeAssistantView):
    """Plex/Jellyfin artwork without exposing their tokens, and YouTube thumbnails; images only."""

    url, name, requires_auth = "/api/homehub_media/art/{server}/{key}", "api:homehub_media:art", True

    async def get(self, request: web.Request, server: str, key: str) -> web.Response:
        hass = request.app[KEY_HASS]
        hub = loaded_hub(hass)
        srv = hub and (YouTubeArt(hass) if server == "youtube" else hub.library and hub.library.server(server))
        if not srv or not ART_KEY_RE.fullmatch(key):
            raise web.HTTPNotFound
        try:
            body, content_type = await srv.fetch_art(key)
        except ServiceValidationError:
            raise web.HTTPNotFound from None
        except HomeAssistantError:
            raise web.HTTPBadGateway from None
        if content_type not in ART_TYPES or len(body) > ART_MAX:
            raise web.HTTPBadGateway
        return web.Response(body=body, content_type=content_type, headers={
            "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'",
            "Cache-Control": "private, max-age=3600"})


@callback
def async_register(hass: HomeAssistant) -> None:
    for command in (ws_subscribe, ws_catalog, ws_library):
        websocket_api.async_register_command(hass, command)
    for name, (schema, run) in SERVICES.items():
        if name in ADMIN_SERVICES:
            async_register_admin_service(hass, DOMAIN, name, partial(_call, run), vol.Schema(schema))
        else:
            hass.services.async_register(DOMAIN, name, partial(_call, run), vol.Schema(schema))
    hass.http.register_view(PageStateView())
    hass.http.register_view(ArtView())
