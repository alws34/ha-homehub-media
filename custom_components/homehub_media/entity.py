"""Shared base for HomeHub Media entities: one device per entry, refreshed on every hub update."""
from __future__ import annotations

import time

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import CONF_MAC, DOMAIN, SIGNAL_UPDATE


class HomeHubEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub, key: str) -> None:
        entry, self.hub = hub.entry, hub
        mac = entry.data.get(CONF_MAC)
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name="HomeHub Media", manufacturer="HomeHub",
            connections={(CONNECTION_NETWORK_MAC, mac)} if mac else set())

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self.async_write_ha_state))

    def _touch(self) -> None:
        """Entity actions are user commands: they hold off idle auto ambience like the card's do."""
        self.hub.last_command = time.monotonic()
