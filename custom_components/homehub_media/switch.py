"""Quiet mode and auto ambience switches."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import HomeHubEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    hub = entry.runtime_data
    async_add_entities([QuietSwitch(hub, "quiet_mode"), AutoAmbienceSwitch(hub, "auto_ambience")])


class QuietSwitch(HomeHubEntity, SwitchEntity):
    @property
    def is_on(self) -> bool:
        return self.hub.activity.quiet

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.hub.activity.set_quiet(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.hub.activity.set_quiet(False)


class AutoAmbienceSwitch(HomeHubEntity, SwitchEntity, RestoreEntity):
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if last := await self.async_get_last_state():
            self.hub.ambience.auto = last.state == STATE_ON

    @property
    def is_on(self) -> bool:
        return self.hub.ambience.auto

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._set(False)

    def _set(self, on: bool) -> None:
        self.hub.ambience.auto = on
        self.hub.async_notify()  # the card's snapshot and this entity's state
