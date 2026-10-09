"""Config flow (pair the TV) and a menu-based options flow: one menu item per option section (K1)."""
from __future__ import annotations

import ipaddress
import re
from collections.abc import Awaitable, Mapping
from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    IconSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
    TimeSelector,
)

from . import assistant, catalog, library, pc
from .const import (
    APP_CATALOG,
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
    OPT_ASSISTANT,
    OPT_BOX,
    OPT_DAYPARTS,
    OPT_KEYBOARD,
    OPT_MODES,
    OPT_PC,
    OPT_SERVERS,
    OPT_TV,
    OPT_VOLUME,
)
from .tv import APP_ID_RE, KEY_RE, PairingDenied, SamsungTV

LABEL_RE = re.compile(r"(?!-)[A-Za-z0-9-]{1,63}(?<!-)")
MAC_RE = re.compile(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}")
NEW_MODE = "add-new"  # slugify() never emits "-", so no mode id can collide
SECRETS = ("token", "api_key", "ambient_token", "coord_token", "key")
PARTS = ("morning", "afternoon", "evening", "night")
OLLAMA_URL = "http://homeassistant.local:11434"
MENU = ["tv", "apps", "servers", "box", "pc", "keyboard", "ambience", "modes", "volume", "dayparts", "assistant",
        "done"]

TEXT = TextSelector()
URL = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))
PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
PORT = NumberSelector(NumberSelectorConfig(min=1, max=65535, mode=NumberSelectorMode.BOX))
PERCENT = NumberSelector(NumberSelectorConfig(min=0, max=100, step=1, mode=NumberSelectorMode.SLIDER))


def _entities(*domains: str, multiple: bool = False) -> EntitySelector:
    return EntitySelector(EntitySelectorConfig(domain=list(domains), multiple=multiple))


def _select(options: list, **config: Any) -> SelectSelector:
    return SelectSelector(SelectSelectorConfig(options=options, **config))


HOST_SCHEMA = vol.Schema({vol.Required(CONF_HOST): TEXT, vol.Optional(CONF_MAC): TEXT})
INPUT = section(vol.Schema({
    vol.Required("method", default="key"): _select(["key", "source"], translation_key="input_method"),
    vol.Optional("value"): TEXT,
}))
TV_SCHEMA = vol.Schema({
    vol.Optional("entity"): _entities("media_player"),
    vol.Optional("app_entity"): _entities("sensor", "select", "media_player"),
    vol.Optional("base_url"): URL,
})
APPS_SCHEMA = vol.Schema({
    vol.Optional("enabled"): _select([{"value": k, "label": a["name"]} for k, a in APP_CATALOG.items()],
                                     multiple=True, mode=SelectSelectorMode.LIST),
    vol.Optional("overrides"): TEXT,
    vol.Optional("rescan", default=False): BooleanSelector(),
})
PLEX_SCHEMA = vol.Schema({vol.Optional("url"): URL, vol.Optional("token"): PASSWORD, vol.Optional("player"): TEXT})
JELLYFIN_SCHEMA = vol.Schema({vol.Optional("url"): URL, vol.Optional("api_key"): PASSWORD})
BOX_SCHEMA = vol.Schema({
    vol.Optional("remote"): _entities("remote"),
    vol.Optional("media_player"): _entities("media_player"),
    vol.Required("input"): INPUT,
})
PC_SCHEMA = vol.Schema({
    vol.Optional("host"): TEXT,
    vol.Required("ambient_port", default=8097): PORT,
    vol.Optional("ambient_token"): PASSWORD,
    vol.Required("coord_port", default=8098): PORT,
    vol.Optional("coord_token"): PASSWORD,
    vol.Required("input"): INPUT,
    vol.Optional("clients"): TextSelector(TextSelectorConfig(multiple=True)),
})
KEYBOARD_SCHEMA = vol.Schema({vol.Optional("host"): TEXT, vol.Optional("key"): PASSWORD})
MODE_SCHEMA = vol.Schema({
    vol.Required("name"): TEXT,
    vol.Required("icon"): IconSelector(),
    vol.Optional("scene"): _entities("scene"),
    vol.Optional("flicker"): _entities("light", multiple=True),
    vol.Optional("lights_only", default=False): BooleanSelector(),
})
VOLUME_SCHEMA = vol.Schema({
    vol.Required("normal"): PERCENT,
    vol.Required("quiet"): PERCENT,
    vol.Optional("block_entity"): _entities("input_boolean", "switch", "binary_sensor"),
})
PROVIDER_SCHEMA = vol.Schema({
    vol.Required("provider"): _select(["none", "ollama", "openai", "claude"], translation_key="provider"),
})


def _host_ok(host: str) -> bool:
    """Hostname, IPv4, or bracketed IPv6: the clients build http://{host}:{port} URLs from it."""
    if host.startswith("[") and host.endswith("]"):
        try:
            return ipaddress.ip_address(host[1:-1]).version == 6
        except ValueError:
            return False
    return len(host) <= 253 and all(LABEL_RE.fullmatch(label) for label in host.removesuffix(".").split("."))


def _url_ok(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return parts.scheme in ("http", "https") and bool(parts.hostname)
    except ValueError:
        return False


def _mac(value: str | None) -> str | None:
    """aa:bb:cc:dd:ee:ff, or None when missing or not a MAC."""
    mac = format_mac(value.strip()) if value else ""
    return mac if MAC_RE.fullmatch(mac) else None


def _pairs(text: str, sep: str) -> dict[str, str]:
    """'a=1<sep>b=2' -> {"a": "1", "b": "2"}; ValueError on a part without '='."""
    out = {}
    for part in filter(None, (p.strip() for p in text.split(sep))):
        key, eq, value = part.partition("=")
        if not eq:
            raise ValueError(part)
        out[key.strip()] = value.strip()
    return out


def _join(pairs: dict[str, str], sep: str) -> str:
    return sep.join(f"{k}={v}" for k, v in pairs.items())


def _input(raw: dict) -> dict:
    return {"method": raw["method"], "value": (raw.get("value") or "").strip()}


def _public(saved: dict | None) -> dict:
    """Saved values minus secrets: keys never go back to the browser (a blank field may keep them, see _secret)."""
    return {k: v for k, v in (saved or {}).items() if k not in SECRETS}


def _origin(url: str | None) -> tuple | None:
    """(scheme, host, port) of a URL."""
    try:
        parts = urlsplit(url or "")
        return parts.scheme, parts.hostname, parts.port or {"http": 80, "https": 443}.get(parts.scheme)
    except ValueError:
        return None


def _secret(typed: str | None, saved: dict, key: str, origin: Any, saved_origin: Any) -> str | None:
    """The typed secret, else the saved one only if it was for this same origin; None = ask for it again."""
    return typed or (saved.get(key) if origin == saved_origin else None) or None


async def _probe(call: Awaitable) -> tuple[Any, str | None]:
    """(result, None), or (None, form error key) when a server refuses or fails."""
    try:
        return await call, None
    except (library.InvalidAuth, pc.PCAuthError):
        return None, "invalid_auth"
    except library.FAILURES:  # any HomeAssistantError (PCError included) or a malformed reply
        return None, "cannot_connect"


class HomeHubConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
    _data: dict[str, Any]
    _title: str

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HomeHubOptionsFlow:
        return HomeHubOptionsFlow()

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        core = self.hass.config_entries.async_entries("samsungtv", include_ignore=False)
        return await self._host_step("user", user_input, core[0].data if core else {})

    async def async_step_reconfigure(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self._host_step("reconfigure", user_input, self._get_reconfigure_entry().data)

    async def _host_step(self, step_id: str, user_input: dict | None, defaults: Any) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            if not _host_ok(host):
                errors[CONF_HOST] = "invalid_host"
            elif user_input.get(CONF_MAC) and not _mac(user_input[CONF_MAC]):
                errors[CONF_MAC] = "invalid_mac"
            else:
                tv = SamsungTV(self.hass, host, token=None, mac=_mac(user_input.get(CONF_MAC)))
                try:
                    info = await tv.device_info()
                except HomeAssistantError:
                    errors["base"] = "cannot_connect"
                else:
                    self._data = {CONF_HOST: host, CONF_MAC: tv.mac or _mac(info.get("wifiMac"))}
                    self._title = info.get("modelName") or "Samsung TV"
                    return await self.async_step_pair()
                finally:
                    await tv.close()
        return self.async_show_form(step_id=step_id, errors=errors,
                                    data_schema=self.add_suggested_values_to_schema(HOST_SCHEMA, user_input or defaults))

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The TV rejected the token (the hub starts this): pair again, keep everything else."""
        self._data = {CONF_HOST: entry_data[CONF_HOST], CONF_MAC: entry_data.get(CONF_MAC)}
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self.async_step_pair(user_input)

    async def async_step_pair(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        reauth = self.source == SOURCE_REAUTH
        if user_input is not None:
            tv = SamsungTV(self.hass, self._data[CONF_HOST], token=None, mac=self._data[CONF_MAC])
            try:
                token = await tv.pair()
            except PairingDenied:
                errors["base"] = "pairing_denied"
            except HomeAssistantError:
                errors["base"] = "cannot_connect"
            else:
                if reauth:
                    return self.async_update_reload_and_abort(self._get_reauth_entry(),
                                                              data_updates={CONF_TOKEN: token})
                data = {**self._data, CONF_TOKEN: token, CONF_PORT: 8002}
                if self.source == SOURCE_RECONFIGURE:
                    return self.async_update_reload_and_abort(self._get_reconfigure_entry(), data=data)
                return self.async_create_entry(title=self._title, data=data, options=deepcopy({
                    OPT_AMBIENCE: {"backend": "tv", "radio": {}}, OPT_MODES: DEFAULT_MODES,
                    OPT_VOLUME: DEFAULT_VOLUME, OPT_DAYPARTS: DEFAULT_DAYPARTS}))
            finally:
                await tv.close()
        return self.async_show_form(step_id="reauth_confirm" if reauth else "pair", errors=errors,
                                    description_placeholders={"host": self._data[CONF_HOST]})


class HomeHubOptionsFlow(OptionsFlowWithReload):
    """Each step edits one section of a working copy; "done" saves it all (and reloads the entry)."""

    def __init__(self) -> None:
        self._options: dict[str, Any] = {}
        self._mode: str | None = None  # id of the mode being edited, None = new
        self._pending: dict[str, Any] = {}  # first-form answers of two-form steps (jellyfin, assistant)
        self._choices: list = []  # jellyfin users / assistant models for the second form

    async def async_step_init(self, user_input: dict | None = None) -> ConfigFlowResult:
        self._options = self._options or deepcopy(dict(self.config_entry.options))
        return self.async_show_menu(step_id="init", menu_options=MENU)

    async def async_step_done(self, user_input: dict | None = None) -> ConfigFlowResult:
        return self.async_create_entry(data=self._options)

    def _section(self, key: str) -> dict:
        return self._options.get(key) or {}

    def _set(self, key: str, value: Any) -> ConfigFlowResult:
        self._options[key] = value
        return self.async_show_menu(step_id="init", menu_options=MENU)

    def _form(self, step_id: str, schema: vol.Schema, values: Any, errors: dict | None = None,
              **placeholders: str) -> ConfigFlowResult:
        return self.async_show_form(step_id=step_id, errors=errors, description_placeholders=placeholders,
                                    data_schema=self.add_suggested_values_to_schema(schema, values or {}))

    def _input_errors(self, inp: dict) -> dict[str, str]:
        if not (KEY_RE.fullmatch(inp["value"]) if inp["method"] == "key" else inp["value"]):
            return {"base": "invalid_input"}
        if inp["method"] == "source" and not self._section(OPT_TV).get("entity"):
            return {"base": "tv_entity_required"}
        return {}

    async def async_step_tv(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            value = {k: user_input.get(k) or None for k in ("entity", "app_entity", "base_url")}
            if value["base_url"]:
                value["base_url"] = value["base_url"].rstrip("/")
            if value["base_url"] and not _url_ok(value["base_url"]):
                errors["base_url"] = "invalid_url"
            elif not value["entity"] and any(
                    self._section(s).get("input", {}).get("method") == "source" for s in (OPT_BOX, OPT_PC)):
                errors["entity"] = "tv_entity_required"
            else:
                return self._set(OPT_TV, value)
        return self._form("tv", TV_SCHEMA, user_input or self._options.get(OPT_TV), errors)

    async def async_step_apps(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        apps = self._section(OPT_APPS)
        values = {"enabled": apps.get("enabled", list(apps.get("ids") or APP_CATALOG)),
                  "overrides": _join(apps.get("ids") or {}, ", ")}
        if user_input is not None and user_input.get("rescan"):
            data = self.config_entry.data
            tv = SamsungTV(self.hass, data[CONF_HOST], token=data.get(CONF_TOKEN), mac=data.get(CONF_MAC))
            try:
                if not (found := await tv.discover_apps()):  # standby: an empty result would stop auto-discovery
                    raise HomeAssistantError
            except HomeAssistantError:
                errors["base"], values = "cannot_connect", user_input
            else:  # ids stay the user's overrides; discovered apps go to their own key (the hub merges both)
                self._options[OPT_APPS] = {**apps, "discovered": found}
                values = {"enabled": list(found), "overrides": user_input.get("overrides")}
            finally:
                await tv.close()
        elif user_input is not None:
            try:
                ids = _pairs(user_input.get("overrides", ""), ",")
                if not all(k in APP_CATALOG and APP_ID_RE.fullmatch(v) for k, v in ids.items()):
                    raise ValueError
            except ValueError:
                errors["overrides"], values = "invalid_id", user_input
            else:
                return self._set(OPT_APPS, {**apps, "enabled": user_input.get("enabled", []), "ids": ids})
        return self._form("apps", APPS_SCHEMA, values, errors)

    async def async_step_servers(self, user_input: dict | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="servers", menu_options=["plex", "jellyfin", "init"])

    def _set_server(self, name: str, value: dict | None) -> ConfigFlowResult:
        return self._set(OPT_SERVERS, {"plex": None, "jellyfin": None, **self._section(OPT_SERVERS), name: value})

    async def async_step_plex(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        saved = self._section(OPT_SERVERS).get("plex") or {}
        core = next((e.data.get("server_config") for e in
                     self.hass.config_entries.async_entries("plex", include_ignore=False)), None) or {}
        if user_input is not None:
            if user_input.get("prefill") and core:  # the core entry is read only on the user's explicit request
                url, token = core.get("url"), core.get("token")
            else:
                url = user_input.get("url")
                token = _secret(user_input.get("token"), saved, "token", _origin(url), _origin(saved.get("url")))
            if not url:
                return self._set_server("plex", None)
            if not _url_ok(url):
                errors["url"] = "invalid_url"
            elif not token:
                errors["token"] = "secret_required"
            else:
                _, err = await _probe(library.PlexServer(self.hass, url, token, None).continue_watching())
                if not err:
                    return self._set_server("plex", {"url": url, "token": token,
                                                     "player": user_input.get("player") or None})
                errors["base"] = err
        schema = PLEX_SCHEMA.extend({vol.Optional("prefill", default=False): BooleanSelector()}) if core else PLEX_SCHEMA
        return self._form("plex", schema, user_input or _public(saved), errors)

    async def async_step_jellyfin(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        saved = self._section(OPT_SERVERS).get("jellyfin") or {}
        if user_input is not None:
            if not (url := user_input.get("url")):
                return self._set_server("jellyfin", None)
            api_key = _secret(user_input.get("api_key"), saved, "api_key", _origin(url), _origin(saved.get("url")))
            if not _url_ok(url):
                errors["url"] = "invalid_url"
            elif not api_key:
                errors["api_key"] = "secret_required"
            else:
                self._pending = {"url": url, "api_key": api_key}
                users, err = await _probe(library.JellyfinServer(self.hass, url, api_key, "", None).users())
                if not err:
                    self._choices = [{"value": u["id"], "label": u["name"]} for u in users]
                    return await self.async_step_jellyfin_user()
                errors["base"] = err
        return self._form("jellyfin", JELLYFIN_SCHEMA, user_input or _public(saved), errors)

    async def async_step_jellyfin_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            value = {**self._pending, "user_id": user_input["user_id"], "player": user_input.get("player") or None}
            _, err = await _probe(library.JellyfinServer(self.hass, value["url"], value["api_key"], value["user_id"],
                                                         None).continue_watching())
            if not err:
                return self._set_server("jellyfin", value)
            errors["base"] = err
        schema = vol.Schema({vol.Required("user_id"): _select(self._choices), vol.Optional("player"): TEXT})
        return self._form("jellyfin_user", schema, user_input or _public(self._section(OPT_SERVERS).get("jellyfin")),
                          errors)

    async def async_step_box(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get("remote"):
                return self._set(OPT_BOX, None)
            value = {"remote": user_input["remote"], "media_player": user_input.get("media_player") or None,
                     "input": _input(user_input["input"])}
            if not (errors := self._input_errors(value["input"])):
                return self._set(OPT_BOX, value)
        return self._form("box", BOX_SCHEMA, user_input or self._options.get(OPT_BOX), errors)

    async def async_step_pc(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        saved = self._section(OPT_PC)
        if user_input is not None:
            if not (host := (user_input.get("host") or "").strip()):
                if self._section(OPT_AMBIENCE).get("backend") == "pc":  # nothing left to play on
                    self._options[OPT_AMBIENCE] = {**self._section(OPT_AMBIENCE), "backend": "tv"}
                return self._set(OPT_PC, None)
            try:  # K6 pc_open takes a slug
                clients = [cv.slug(c.strip()) for c in user_input.get("clients") or [] if c.strip()]
            except vol.Invalid:
                clients = None
            value = {"host": host, "ambient_port": int(user_input["ambient_port"]),
                     "coord_port": int(user_input["coord_port"]), "input": _input(user_input["input"]),
                     "clients": clients}
            for name, port in (("ambient_token", "ambient_port"), ("coord_token", "coord_port")):
                value[name] = _secret(user_input.get(name), saved, name, (host, value[port]),
                                      (saved.get("host"), saved.get(port)))
            if not _host_ok(host):
                errors["host"] = "invalid_host"
            elif clients is None:
                errors["clients"] = "invalid_client"
            elif missing := [k for k in ("ambient_token", "coord_token") if not value[k]]:
                errors = dict.fromkeys(missing, "secret_required")
            elif not (errors := self._input_errors(value["input"])):
                ambient = pc.AmbientPlayerClient(self.hass, host, value["ambient_port"], value["ambient_token"])
                coord = pc.CoordinatorClient(self.hass, host, value["coord_port"], value["coord_token"])
                if not (err := (await _probe(ambient.status()))[1] or (await _probe(coord.status()))[1]):
                    return self._set(OPT_PC, value)
                errors["base"] = err
        return self._form("pc", PC_SCHEMA, user_input or _public(saved), errors)

    async def async_step_keyboard(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        saved = self._section(OPT_KEYBOARD)
        if user_input is not None:
            if not (host := (user_input.get("host") or "").strip()):
                return self._set(OPT_KEYBOARD, None)
            key = _secret(user_input.get("key"), saved, "key", host, saved.get("host"))
            if not _host_ok(host):
                errors["host"] = "invalid_host"
            elif not key:
                errors["key"] = "secret_required"
            else:
                return self._set(OPT_KEYBOARD, {"host": host, "key": key})
        return self._form("keyboard", KEYBOARD_SCHEMA, user_input or _public(saved), errors)

    async def async_step_ambience(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            value = {"backend": user_input["backend"]}
            for key, check, error in (("radio", catalog.radio_items, "invalid_url"),
                                      ("youtube", catalog.youtube_items, "invalid_youtube")):
                try:
                    value[key] = _pairs(user_input.get(key, ""), "\n")
                    check(value[key])
                except ValueError:
                    errors[key] = error
            if not errors:
                return self._set(OPT_AMBIENCE, value)
        ambience = self._section(OPT_AMBIENCE)
        schema = vol.Schema({
            vol.Required("backend"): _select(["tv", "pc"] if self._options.get(OPT_PC) else ["tv"],
                                             translation_key="backend"),
            vol.Optional("radio"): TextSelector(TextSelectorConfig(multiline=True)),
            vol.Optional("youtube"): TextSelector(TextSelectorConfig(multiline=True)),
        })
        return self._form("ambience", schema, user_input or {
            "backend": ambience.get("backend", "tv"), "radio": _join(ambience.get("radio") or {}, "\n"),
            "youtube": _join(ambience.get("youtube") or {}, "\n")}, errors)

    def _modes(self) -> list[dict]:
        return self._options.get(OPT_MODES) or deepcopy(DEFAULT_MODES)

    async def async_step_modes(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._mode = None if user_input["mode"] == NEW_MODE else user_input["mode"]
            return await self.async_step_mode()
        options = [{"value": NEW_MODE, "label": "Add new mode"},  # label is the fallback for the translation
                   *({"value": m["id"], "label": m["name"]} for m in self._modes())]
        return self.async_show_form(step_id="modes", data_schema=vol.Schema(
            {vol.Required("mode", default=NEW_MODE): _select(options, translation_key="mode_pick")}))

    async def async_step_mode(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        modes = self._modes()
        if user_input is not None:
            if user_input.get("delete"):
                if dayparts := self._options.get(OPT_DAYPARTS):  # no daypart may keep pointing at it
                    self._options[OPT_DAYPARTS] = {
                        k: {**v, "mode": None} if k in PARTS and v.get("mode") == self._mode else v
                        for k, v in dayparts.items()}
                return self._set(OPT_MODES, [m for m in modes if m["id"] != self._mode])
            name = user_input["name"].strip()
            mode = {"id": self._mode or catalog.slugify(name), "name": name,
                    "icon": user_input["icon"], "scene": user_input.get("scene") or None,
                    "flicker": user_input.get("flicker") or [], "lights_only": user_input.get("lights_only", False)}
            if not name or mode["id"] == "off":  # "off" is the ambience select's stop option
                errors["name"] = "invalid_name"
            elif self._mode:
                return self._set(OPT_MODES, [mode if m["id"] == self._mode else m for m in modes])
            elif all(m["id"] != mode["id"] for m in modes):
                return self._set(OPT_MODES, [*modes, mode])
            else:
                errors["name"] = "mode_exists"
        current = next((m for m in modes if m["id"] == self._mode), None)
        schema = MODE_SCHEMA.extend({vol.Optional("delete", default=False): BooleanSelector()}) if current else MODE_SCHEMA
        return self._form("mode", schema, user_input or current or {"icon": "mdi:lightbulb"}, errors)

    async def async_step_volume(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self._set(OPT_VOLUME, {"normal": int(user_input["normal"]), "quiet": int(user_input["quiet"]),
                                          "block_entity": user_input.get("block_entity") or None})
        return self._form("volume", VOLUME_SCHEMA, self._options.get(OPT_VOLUME, DEFAULT_VOLUME))

    async def async_step_dayparts(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self._set(OPT_DAYPARTS, {
                **{p: {"start": cv.time(user_input[p]["start"]).strftime("%H:%M"),
                       "mode": user_input[p].get("mode") or None} for p in PARTS},
                "idle_minutes": int(user_input["idle_minutes"])})
        part = section(vol.Schema({
            vol.Required("start"): TimeSelector(),
            vol.Optional("mode"): _select([{"value": m["id"], "label": m["name"]} for m in self._modes()]),
        }))
        schema = vol.Schema({**{vol.Required(p): part for p in PARTS}, vol.Required("idle_minutes"): NumberSelector(
            NumberSelectorConfig(min=1, max=240, unit_of_measurement="min", mode=NumberSelectorMode.BOX))})
        return self._form("dayparts", schema, self._options.get(OPT_DAYPARTS, DEFAULT_DAYPARTS))

    async def async_step_assistant(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            if user_input["provider"] == "none":
                return self._set(OPT_ASSISTANT, None)
            self._pending = {"provider": user_input["provider"]}
            return await self.async_step_assistant_auth()
        return self._form("assistant", PROVIDER_SCHEMA, self._options.get(OPT_ASSISTANT) or {"provider": "none"})

    async def async_step_assistant_auth(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        provider = self._pending["provider"]
        current = self._section(OPT_ASSISTANT)
        same = current.get("provider") == provider  # a saved key is never reused across providers
        if user_input is not None:
            url = user_input.get("url")
            key = _secret(user_input.get("api_key"), current, "api_key", provider, current.get("provider"))
            if url is not None and not _url_ok(url):
                errors["url"] = "invalid_url"
            elif provider != "ollama" and not key:
                errors["api_key"] = "secret_required"
            else:
                try:
                    await assistant.ensure_sdk(self.hass, provider)
                    self._choices = await assistant.list_models(self.hass, provider, url, key)
                except HomeAssistantError as err:
                    errors["base"] = "invalid_auth" if str(err) == "invalid_auth" else "cannot_connect"
                else:
                    self._pending.update(url=url, api_key=key)
                    return await self.async_step_assistant_model()
        schema = vol.Schema({vol.Required("url"): URL} if provider == "ollama" else {vol.Optional("api_key"): PASSWORD})
        return self._form("assistant_auth", schema, user_input or (_public(current) if same else {"url": OLLAMA_URL}),
                          errors)

    async def async_step_assistant_model(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self._set(OPT_ASSISTANT, {**self._pending, "model": user_input["model"]})
        preferred = (self._section(OPT_ASSISTANT).get("model"), assistant.CLAUDE_MODEL)
        model = next((m for m in preferred if m in self._choices), next(iter(self._choices), None))
        schema = vol.Schema({vol.Required("model"): _select(self._choices, custom_value=True)})
        return self._form("assistant_model", schema, {"model": model})
