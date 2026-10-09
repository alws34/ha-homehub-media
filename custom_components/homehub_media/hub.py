"""Hub: composition root (K3), card snapshot (K4), listeners and command routing."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from functools import lru_cache
from urllib.parse import quote

from homeassistant.components.http.auth import async_sign_path
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from .activity import OFF_STATES, ActivityManager
from .ambience import AmbienceManager
from .const import (
    APP_CATALOG,
    BOX_KEYS,
    CONF_HOST,
    CONF_MAC,
    CONF_PORT,
    CONF_TOKEN,
    DEFAULT_DAYPARTS,
    DEFAULT_MODES,
    DEFAULT_VOLUME,
    DOMAIN,
    OPT_AMBIENCE,
    OPT_APPS,
    OPT_BOX,
    OPT_DAYPARTS,
    OPT_KEYBOARD,
    OPT_MODES,
    OPT_PC,
    OPT_SERVERS,
    OPT_TV,
    OPT_VOLUME,
    PC_COMMANDS,
    SIGNAL_UPDATE,
    TV_KEYS,
    VOLUME_COMMANDS,
)
from .keyboard import KeyboardBridge
from .library import JellyfinServer, Library, PlexServer
from .pc import AmbientPlayerClient, CoordinatorClient
from .store import HomeHubStore
from .tv import APP_ID_RE, PairingDenied, SamsungTV

_LOGGER = logging.getLogger(__name__)
PC_KEYS = {**PC_COMMANDS, "play": "play_pause", "pause": "play_pause"}  # the PC only has a toggle
ENTITY_COMMANDS = ("power", *VOLUME_COMMANDS)  # through the TV media player when one is configured
NAMES = {"tv": "TV", "box": "streaming box", "pc": "media PC", "keyboard": "keyboard bridge"}
PLAY_TIMEOUT, PLAY_RETRY = 15, 3  # s: a just-launched Plex/Jellyfin app needs a moment before remote play works
PC_MOVE, PC_SCROLL, PC_TEXT_MAX = 160, 240, 100  # the media-PC coordinator's limits


def loaded_hub(hass: HomeAssistant) -> Hub | None:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    return entries[0].runtime_data if entries else None


def sign(hass: HomeAssistant, path: str, refresh_token_id: str | None = None, hours: int = 24) -> str:
    """Signed URL (the content user's when no token is given), the same all hour so browsers can cache it."""
    return _sign(hass, path, refresh_token_id, hours, int(time.time() // 3600))


@lru_cache(maxsize=1024)  # ponytail: entries of past hours just age out of the LRU
def _sign(hass: HomeAssistant, path: str, refresh_token_id: str | None, hours: int, _hour: int) -> str:
    signed = async_sign_path(hass, path, timedelta(hours=hours), refresh_token_id=refresh_token_id,
                             use_content_user=refresh_token_id is None)
    path, _, query = signed.rpartition("?")  # HA returns the path decoded: '#', '?', ' ' would break the URL
    return f"{quote(path, safe='/')}?{query}"


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


def _apps(section: dict | None) -> dict[str, str]:
    """Installed app ids: discovered, overridden by the user's own ids."""
    return {**((section or {}).get("discovered") or {}), **((section or {}).get("ids") or {})}


class Hub:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass, self.entry = hass, entry
        self.options: dict = {OPT_AMBIENCE: {"backend": "tv", "radio": {}}, OPT_MODES: DEFAULT_MODES,
                              OPT_VOLUME: DEFAULT_VOLUME, OPT_DAYPARTS: DEFAULT_DAYPARTS, **entry.options}
        self.last_command = time.monotonic()
        self.current_app: str | None = None
        self.pc_active: str | None = None
        self._paused = False  # last play/pause key sent to the TV
        self._reauth = False  # a revoked TV token has started the reauth flow
        self._unsubs: list[Callable[[], None]] = []
        data, pc, kb = entry.data, self.options.get(OPT_PC), self.options.get(OPT_KEYBOARD)
        self.tv = SamsungTV(hass, data[CONF_HOST], data.get(CONF_TOKEN), data.get(CONF_MAC),
                            data.get(CONF_PORT, 8002), on_change=self._tv_changed)
        self.pc_ambient = AmbientPlayerClient(hass, pc["host"], pc["ambient_port"], pc["ambient_token"]) if pc else None
        self.pc_coord = CoordinatorClient(hass, pc["host"], pc["coord_port"], pc["coord_token"]) if pc else None
        self.keyboard = KeyboardBridge(hass, kb["host"], kb["key"]) if kb else None
        srv, servers = self.options.get(OPT_SERVERS) or {}, []
        if p := srv.get("plex"):
            servers.append(PlexServer(hass, p["url"], p["token"], p.get("player")))
        if j := srv.get("jellyfin"):
            servers.append(JellyfinServer(hass, j["url"], j["api_key"], j["user_id"], j.get("player")))
        self.library = Library(hass, servers, self.async_notify) if servers else None
        self.activity = ActivityManager(hass, self)
        self.ambience = AmbienceManager(hass, self, HomeHubStore(hass))
        self.apps = _apps(self.options.get(OPT_APPS))

    # ---- lifecycle ---------------------------------------------------------------------------------

    async def async_start(self) -> None:
        await self.ambience.async_load()
        if self.library:
            self._unsubs.append(self.library.async_start())
        self._unsubs.append(async_track_time_interval(self.hass, self._poll_tv, timedelta(seconds=30)))
        # first loads in the background: a TV, media PC or media server that is down must not fail or slow setup
        load = self.entry.async_create_background_task(self.hass, self._load(), "homehub_media_load")
        self._unsubs.append(load.cancel)

    async def _load(self) -> None:
        lib = self.library
        await asyncio.gather(self.ambience.async_load_catalog(), self._poll_tv(),
                             *((lib.async_refresh(), lib.async_refresh_sessions()) if lib else ()))

    async def async_stop(self) -> None:
        while self._unsubs:  # first: a cancelled startup load then registers nothing after the shutdowns
            self._unsubs.pop()()
        await self.ambience.async_shutdown()
        await self.activity.async_shutdown()
        await self.tv.close()

    async def _poll_tv(self, _now: datetime | None = None) -> None:
        """Refresh tv.available (REST PowerState); discover the apps once, when the TV is really on."""
        try:
            await self.tv.device_info()
            # standby answers REST too but may 404 every app, which would persist an empty discovery
            if self.tv.available and "discovered" not in (self.entry.options.get(OPT_APPS) or {}):
                await self.rescan_apps()
        except HomeAssistantError as err:
            _LOGGER.debug("TV poll failed: %s", err)

    async def rescan_apps(self) -> dict[str, str]:
        if not (found := await self.tv.discover_apps()):
            raise HomeAssistantError("The TV listed no apps; turn it on and rescan")
        section = {**(self.entry.options.get(OPT_APPS) or {}), "discovered": found}
        self.options[OPT_APPS], self.apps = section, _apps(section)
        # no update listener (OptionsFlowWithReload reloads on its own), so this does not reload
        self.hass.config_entries.async_update_entry(self.entry, options={**self.entry.options, OPT_APPS: section})
        self.async_notify()
        return self.apps

    @callback
    def _tv_changed(self) -> None:
        if self.tv.token != self.entry.data.get(CONF_TOKEN):  # the TV rotated the token: keep it, no reload
            self.hass.config_entries.async_update_entry(self.entry, data={**self.entry.data, CONF_TOKEN: self.tv.token})
        self.async_notify()

    @callback
    def async_notify(self) -> None:
        if self.activity.current != "tv":  # box, PC or off: no TV app in front any more
            self.current_app = None
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    def sign_path(self, path: str, hours: int = 24) -> str:
        return sign(self.hass, path, hours=hours)

    @property
    def activities(self) -> list[str]:
        return ["off", "tv", *(a for a in (OPT_BOX, OPT_PC) if self.options.get(a))]

    # ---- commands ----------------------------------------------------------------------------------

    async def command[T](self, aw: Awaitable[T]) -> T:
        """Run a user command: holds off idle auto ambience; a revoked TV token asks for re-pairing (once)."""
        self.last_command = time.monotonic()
        try:
            return await aw
        except PairingDenied:
            if not self._reauth:
                self._reauth = True
                self.entry.async_start_reauth(self.hass)
            raise

    def _target(self, target: str | None) -> str:
        return target or (self.activity.current if self.activity.current != "off" else "tv")

    def _unsupported(self, what: str, target: str) -> ServiceValidationError:
        return ServiceValidationError(f"{what.replace('_', ' ').capitalize()} is not available on the {NAMES[target]}")

    async def remote(self, command: str, target: str | None = None) -> None:
        target = self._target(target)
        if command in VOLUME_COMMANDS or (target == "pc" and command == "power"):
            target = "tv"  # only the TV has volume, and the PC's screen is the TV
        if target == "tv" and command == "play_pause":
            # ponytail: toggles on the last key sent, not the player's real state; worst case one extra press
            command = "play" if self._paused else "pause"
        box = self.options.get(OPT_BOX)
        entity = (self.options.get(OPT_TV) or {}).get("entity")
        if target == "tv" and entity and command in ENTITY_COMMANDS:  # the core Samsung TV integration's player
            await self._tv_entity(entity, command)
        elif target == "tv" and command == "power" and not self.tv.available and self.tv.mac:
            await self.tv.wake()  # a WebSocket key cannot reach a TV that is off
        elif target == "tv" and command in TV_KEYS:
            await self.tv.send_key(TV_KEYS[command])
            if command in ("play", "pause"):
                self._paused = command == "pause"
        elif target == "box" and box and command in BOX_KEYS:
            await self.hass.services.async_call(
                "remote", "send_command", {"entity_id": box["remote"], "command": BOX_KEYS[command]}, blocking=True)
        elif target == "pc" and self.pc_coord and command in PC_KEYS:
            await self.pc_coord.remote(PC_KEYS[command])
        else:
            raise self._unsupported(command, target)

    async def _tv_entity(self, entity: str, command: str) -> None:
        st = self.hass.states.get(entity)
        if command == "power" and (not st or st.state in OFF_STATES):
            await self.activity.power_on()  # turn_on when the entity supports it, else Wake-on-LAN
            return
        data = {"entity_id": entity}
        if command == "power":
            service = "turn_off"
        elif command == "mute":
            service, data["is_volume_muted"] = "volume_mute", not (st and st.attributes.get("is_volume_muted"))
        else:
            service = command  # volume_up / volume_down
        await self.hass.services.async_call("media_player", service, data, blocking=True)

    async def set_volume(self, level: int) -> None:
        if not (entity := (self.options.get(OPT_TV) or {}).get("entity")):
            raise ServiceValidationError("Setting the volume needs the TV media player (Configure › TV)")
        await self.hass.services.async_call(
            "media_player", "volume_set", {"entity_id": entity, "volume_level": level / 100}, blocking=True)

    def _server_url(self, app: str) -> str | None:
        server = APP_CATALOG.get(app, {}).get("server")
        return ((self.options.get(OPT_SERVERS) or {}).get(server) or {}).get("url") if server else None

    def _app_key(self, value: str | None) -> str | None:
        """APP_CATALOG key for an app key, name or Tizen id (e.g. an app sensor's state); else the value."""
        if not value:
            return value
        folded = value.casefold()
        return next((key for key, app in APP_CATALOG.items()
                     if folded in (key, app["name"].casefold()) or value in app["ids"] or self.apps.get(key) == value),
                    value)

    async def launch_app(self, app: str) -> None:
        app_id, url = self.apps.get(app), self._server_url(app)
        if not app_id and not url and (app in APP_CATALOG or not APP_ID_RE.fullmatch(app)):
            raise ServiceValidationError(f"{APP_CATALOG.get(app, {}).get('name', app)} is not installed on the TV")
        if self.ambience.uses_media and self.ambience.mode != "off":  # the page would sit on top of the app
            await self.ambience.stop()
        if self.activity.current != "tv" and not await self.activity.set("tv"):
            return  # superseded by a newer activity request
        if app_id or not url:
            await self.tv.launch(app_id or app)  # installed app, or a raw Tizen id
        else:  # e.g. no Jellyfin app on the TV: the server's web UI in the TV browser
            await self.tv.open_url(url)
        self.current_app = self._app_key(app)
        self.async_notify()

    async def restart_app(self, app: str) -> None:
        await self.tv.restart_app(self.apps.get(app, app))

    def _text_target(self, target: str | None) -> str:
        if target:
            return target
        if self.activity.current == "pc":
            return "pc"
        return "tv" if self.tv.keyboard_active or not self.keyboard else "keyboard"

    async def send_text(self, text: str, target: str | None = None) -> None:
        target = self._text_target(target)
        if target == "tv":
            await self.tv.send_text(text)
        elif target == "pc" and self.pc_coord:
            if len(text) > PC_TEXT_MAX or not (text.isascii() and text.isprintable()):
                raise ServiceValidationError(f"The media PC accepts up to {PC_TEXT_MAX} plain ASCII characters")
            await self.pc_coord.text(text)
        elif target == "keyboard" and self.keyboard:
            await self.keyboard.type(text)
        else:
            raise self._unsupported("typing", target)

    async def keyboard_key(self, key: str, target: str | None = None) -> None:
        target = self._text_target(target)
        if target == "tv" and key == "enter":
            await self.tv.send_key("KEY_ENTER")
        elif target == "tv":
            raise ServiceValidationError(f"The TV's on-screen keyboard has no {key} key; it needs the keyboard bridge")
        elif target == "pc" and self.pc_coord:
            await self.pc_coord.remote(key)
        elif target == "keyboard" and self.keyboard:
            await self.keyboard.key(key)
        else:
            raise self._unsupported(key, target)

    async def pointer(self, action: str, dx: float = 0, dy: float = 0, target: str | None = None) -> None:
        target = target or ("pc" if self.activity.current == "pc" else "tv")
        if target == "pc" and self.pc_coord:
            if action == "move":
                await self.pc_coord.pointer_move(_clamp(dx, PC_MOVE), _clamp(dy, PC_MOVE))
            elif action == "scroll":
                await self.pc_coord.scroll(_clamp(dy, PC_SCROLL))
            else:
                await self.pc_coord.click("right" if action == "right_click" else "left")
        elif target == "tv" and action == "move":
            await self.tv.pointer_move(dx, dy)
        elif target == "tv" and action == "click":
            await self.tv.pointer_click()
        else:
            raise self._unsupported(f"pointer {action}", target)

    async def channel(self, number: str) -> None:
        if not (player := (self.options.get(OPT_BOX) or {}).get("media_player")):
            raise ServiceValidationError("Channels need the streaming box's media player entity")
        await self.hass.services.async_call("media_player", "play_media", {
            "entity_id": player, "media_content_type": "channel", "media_content_id": number}, blocking=True)

    async def pc_open(self, client: str) -> None:
        if not self.pc_coord:
            raise ServiceValidationError("The media PC is not configured")
        if self.ambience.uses_media and self.ambience.mode != "off":
            await self.ambience.stop()
        # re-selecting would reset the volume for nothing; a superseded switch opens nothing
        if self.activity.current != "pc" and not await self.activity.set("pc"):
            return
        await self.pc_coord.open(client)
        self.pc_active = client
        self.async_notify()

    async def pc_close(self) -> None:
        if not self.pc_coord:
            raise ServiceValidationError("The media PC is not configured")
        await self.pc_coord.close()
        self.pc_active = None
        self.async_notify()

    async def play_library_item(self, server: str, item_id: str) -> None:
        name = APP_CATALOG.get(server, {}).get("name", server)
        if not (srv := self.library and self.library.server(server)):
            raise ServiceValidationError(f"{name} is not configured")
        await self.launch_app(server)
        end = time.monotonic() + PLAY_TIMEOUT
        while not await srv.play(item_id):
            if time.monotonic() >= end:
                raise HomeAssistantError(f"{name} did not start playing")
            await asyncio.sleep(PLAY_RETRY)

    # ---- K4 ----------------------------------------------------------------------------------------

    def snapshot(self) -> dict:
        tv_opt, pc = self.options.get(OPT_TV) or {}, self.options.get(OPT_PC) or {}
        st = self.hass.states.get(tv_opt.get("entity") or "")
        app = self.hass.states.get(tv_opt.get("app_entity") or "")
        level = st.attributes.get("volume_level") if st else None
        return {
            "tv": {"available": self.tv.available, "power": "on" if self.activity.is_on() else "off",
                   "volume": round(level * 100) if level is not None else None,
                   "muted": st.attributes.get("is_volume_muted") if st else None,
                   "keyboard_active": self.tv.keyboard_active, "pointer_active": self.tv.pointer_active,
                   "app": self._app_key(app.state) if app else self.current_app},
            "activity": {"current": self.activity.current, "phase": self.activity.phase, "quiet": self.activity.quiet,
                         "available": self.activities},
            "targets": {"box": bool(self.options.get(OPT_BOX)), "pc": bool(self.pc_coord), "keyboard": bool(self.keyboard)},
            "apps": [{"key": k, "name": APP_CATALOG[k]["name"], "installed": bool(k in self.apps or self._server_url(k))}
                     for k in (self.options.get(OPT_APPS) or {}).get("enabled") or APP_CATALOG if k in APP_CATALOG],
            "ambience": self.ambience.snapshot(),
            "now_playing": self.library.now_playing if self.library else [],
            "servers": list(self.library.items) if self.library else [],
            "pc": {"clients": pc.get("clients") or [], "active": self.pc_active},
        }
