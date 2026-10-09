"""Ambience mode and activity selects."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity

from .const import OPT_BOX, OPT_PC
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
        self._touch()
        await (self.hub.ambience.stop() if option == "off" else self.hub.ambience.start(option))


class ActivitySelect(HomeHubEntity, SelectEntity):
    @property
    def options(self) -> list[str]:
        return ["off", "tv", *(a for a in (OPT_BOX, OPT_PC) if self.hub.options.get(a))]  # as Hub.snapshot()

    @property
    def current_option(self) -> str:
        return self.hub.activity.current

    async def async_select_option(self, option: str) -> None:
        self._touch()
        await self.hub.activity.set(option)
