"""Ambience sleep timer and now-playing sensors."""
from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity

from .entity import HomeHubEntity

NOW_PLAYING_ATTRS = ("server", "player", "subtitle", "state", "position", "duration")  # no image: signed URL


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    hub = entry.runtime_data
    async_add_entities([SleepTimerSensor(hub, "ambience_sleep_timer"), NowPlayingSensor(hub, "now_playing")])


class SleepTimerSensor(HomeHubEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        return self.hub.ambience.sleep_until


class NowPlayingSensor(HomeHubEntity, SensorEntity):
    _attr_unrecorded_attributes = frozenset({"position", "duration"})

    @property
    def _playing(self) -> dict | None:
        return next(iter(self.hub.library.now_playing if self.hub.library else []), None)

    @property
    def native_value(self) -> str | None:
        return self._playing["title"][:255] if self._playing else None

    @property
    def extra_state_attributes(self) -> dict | None:
        return {k: self._playing.get(k) for k in NOW_PLAYING_ATTRS} if self._playing else None
