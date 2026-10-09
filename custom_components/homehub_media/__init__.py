"""HomeHub Media integration."""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import llm
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from . import api, assistant
from .const import CARD_FILE, DOMAIN, OPT_ASSISTANT, STATIC_URL
from .hub import Hub

_LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS: list[Platform] = []  # Task 12 adds select, switch, sensor and conversation


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
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
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)  # then on_unload stops the hub
