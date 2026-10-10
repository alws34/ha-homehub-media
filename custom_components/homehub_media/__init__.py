"""HomeHub Media integration."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import llm
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from . import api, assistant
from .const import CARD_FILE, DOMAIN, OPT_ASSISTANT, STATIC_URL
from .hub import Hub

_LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.SELECT, Platform.SWITCH, Platform.SENSOR]
_SECRET_RE = re.compile("token|lounge|screen|SendInputString|metaTag", re.IGNORECASE)
_FILTERED = ("samsungtvws", "pyytlounge")  # third-party loggers that see secrets


def _no_secrets(record: logging.LogRecord) -> bool:
    """Drops samsungtvws records with the TV token, typed text or the signed page URL, and pyytlounge records with
    the lounge token or screen id. pyytlounge tracebacks (request URLs with the token) are stripped."""
    if record.name.startswith("pyytlounge"):
        record.exc_info = record.exc_text = record.stack_info = None
    return not _SECRET_RE.search(record.getMessage())


def _platforms(hub: Hub) -> list[Platform]:
    return [*PLATFORMS, Platform.CONVERSATION] if hub.options.get(OPT_ASSISTANT) else PLATFORMS


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    # a logger's filters skip its children's records, so each module logger gets it (tv.py imported them all)
    for name in {*_FILTERED, *(n for n in list(logging.root.manager.loggerDict) if n.split(".")[0] in _FILTERED)}:
        logging.getLogger(name).addFilter(_no_secrets)  # a no-op when already added
    api.async_register(hass)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "www"), cache_headers=False)])
    version = (await async_get_integration(hass, DOMAIN)).version
    frontend.add_extra_js_url(hass, f"{STATIC_URL}/{CARD_FILE}?v={version}")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # No update listener: the options flow is OptionsFlowWithReload, and the hub's own option/token writes
    # (discovered apps, rotated TV token) must not reload mid-command.
    hub = entry.runtime_data = Hub(hass, entry)
    entry.async_on_unload(hub.async_stop)  # also runs when setup fails part-way
    await hub.async_start()
    entry.async_on_unload(llm.async_register_api(hass, assistant.HomeHubLLMAPI(hass)))
    if cfg := hub.options.get(OPT_ASSISTANT):
        try:
            await assistant.ensure_sdk(hass, cfg["provider"])
        except HomeAssistantError as err:
            _LOGGER.warning("Could not install the %s SDK: %s", cfg["provider"], err)
    elif agent := er.async_get(hass).async_get_entity_id("conversation", DOMAIN, f"{entry.entry_id}_assistant"):
        er.async_get(hass).async_remove(agent)  # assistant removed in the options: no orphaned agent
    await hass.config_entries.async_forward_entry_setups(entry, _platforms(hub))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # then on_unload stops the hub
    return await hass.config_entries.async_unload_platforms(entry, _platforms(entry.runtime_data))


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await Store(hass, 1, f"{DOMAIN}.data").async_remove()  # the ambience compositions and favourites
