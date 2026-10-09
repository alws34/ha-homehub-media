"""Ambience mode and activity selects."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import HomeHubEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    hub = entry.runtime_data
    async_add_entities([AmbienceModeSelect(hub, "ambience_mode"), ActivitySelect(hub, "activity")])


class AmbienceModeSelect(HomeHubEntity, SelectEntity):
    @property
    def options(self) -> list[str]:
        return ["off", *(m["id"] for m in self.hub.ambience.modes())]

    @property
    def current_option(self) -> str:
        return self.hub.ambience.mode

    async def async_select_option(self, option: str) -> None:
        await self.hub.command(self.hub.ambience.stop() if option == "off" else self.hub.ambience.start(option))


class ActivitySelect(HomeHubEntity, SelectEntity, RestoreEntity):
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # the state only: re-running the activity after a restart would switch inputs and volume
        if (last := await self.async_get_last_state()) and last.state in self.options:
            self.hub.activity.current = last.state

    @property
    def options(self) -> list[str]:
        return self.hub.activities

    @property
    def current_option(self) -> str:
        return self.hub.activity.current

    async def async_select_option(self, option: str) -> None:
        await self.hub.command(self.hub.activity.set(option))
