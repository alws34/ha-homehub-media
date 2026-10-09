"""Activity state machine: off / tv / box / pc, input switching, quiet volume (port of PC:1353-1688)."""
from __future__ import annotations

import asyncio
import logging
import time

from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import ACTIVITIES, OPT_BOX, OPT_PC, OPT_TV, OPT_VOLUME

_LOGGER = logging.getLogger(__name__)
VERIFY_SECS = 2.0       # wait for the TV entity's `source` after each select_source
POWER_WAIT_SECS = 10.0  # wait for the TV to report on
SETTLE_SECS = 3.0       # cold TV: let Tizen settle before keys / input switching (PC:1585-1590)
OFF_STATES = ("off", "unavailable", "unknown", "none")


class ActivityManager:
    def __init__(self, hass, hub) -> None:
        self.hass, self._hub = hass, hub
        self.current, self.phase, self.quiet = "off", "ready", False
        self._task: asyncio.Task | None = None

    def _opt(self, section: str) -> dict:
        return self._hub.options.get(section) or {}

    async def set(self, activity: str, force: bool = False) -> bool:
        """Run the switch; False when a newer request (or unload) superseded it."""
        switching = activity in (OPT_BOX, OPT_PC)
        if activity not in ACTIVITIES or (switching and not self._opt(activity)):
            raise ServiceValidationError(f"Activity {activity} is not available")
        if switching and self._opt(activity)["input"]["method"] == "source" and not self._opt(OPT_TV).get("entity"):
            raise ServiceValidationError(f"Switching to {activity} by source needs a TV media_player entity")
        # mode: restart — the newest request cancels the running one; a superseded caller returns quietly
        task = self._task = self.hass.async_create_task(self._run(activity, force, self._task))
        await asyncio.wait([task])
        if task.cancelled():
            return False
        task.result()
        return True

    async def async_shutdown(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.wait([self._task])

    async def _run(self, activity: str, force: bool, old: asyncio.Task | None) -> None:
        if old:
            old.cancel()
            try:
                await asyncio.wait([old])
            except asyncio.CancelledError:  # superseded as well: still let `old` finish before we end
                await asyncio.wait([old])
                raise
        block = self._opt(OPT_VOLUME).get("block_entity")
        if activity != "off" and block and self.hass.states.is_state(block, "on"):
            self._set_phase("blocked")
            raise ServiceValidationError(f"Blocked by {block}")
        amb = self._hub.ambience
        if amb.mode != "off" and (activity == "off" or amb.uses_media):
            await self._try(amb.stop())
        self.current = activity
        self._set_phase("transitioning")
        try:
            ok = await self._activate(activity, force)
        except Exception:
            self._set_phase("needs_reset")
            raise
        self._set_phase("ready" if ok else "needs_reset")

    async def _activate(self, activity: str, force: bool) -> bool:
        tv, entity = self._hub.tv, self._opt(OPT_TV).get("entity")
        if activity == "off":
            if box := self._opt(OPT_BOX):
                await self._svc("remote", "turn_off", box["remote"])
            if entity:
                await self._svc("media_player", "turn_off", entity)
            elif tv.available:
                await self._try(tv.send_key("KEY_POWER"))
            return True
        cold = not self.is_on()
        box_on = [self._svc("remote", "turn_on", self._opt(OPT_BOX)["remote"])] if activity == "box" else []
        await asyncio.gather(self._power_on(), *box_on)
        await self._until(self.is_on, POWER_WAIT_SECS)
        if cold:
            await asyncio.sleep(SETTLE_SECS)
        if activity == "tv":
            await self._try(tv.send_key("KEY_HOME"))
            ok = self.is_on()
        else:
            ok = await self._switch(self._opt(activity)["input"], 4 if force else 3 if cold else 2, force)
        await self.apply_volume()
        return ok

    async def _switch(self, inp: dict, attempts: int, force: bool) -> bool:
        if inp["method"] == "key":
            await self._try(self._hub.tv.send_key(inp["value"]))
            return True  # discrete key: unverifiable, one attempt (PC:747-792)
        entity = self._opt(OPT_TV).get("entity")

        def switched() -> bool:
            return (st := self.hass.states.get(entity or "")) is not None and st.attributes.get("source") == inp["value"]

        for attempt in range(1, attempts + 1):
            await self._svc("media_player", "select_source", entity, source=inp["value"])
            if await self._until(switched, VERIFY_SECS) and (not force or attempt >= 2):
                return True
        return switched()

    async def set_quiet(self, on: bool) -> None:
        self.quiet = on
        self._hub.async_notify()
        await self.apply_volume()

    async def apply_volume(self) -> None:
        entity = self._opt(OPT_TV).get("entity")
        st = self.hass.states.get(entity) if entity else None
        if st and st.state not in OFF_STATES:
            level = self._opt(OPT_VOLUME)["quiet" if self.quiet else "normal"] / 100
            await self._svc("media_player", "volume_set", entity, volume_level=level)

    def _set_phase(self, phase: str) -> None:
        self.phase = phase
        self._hub.async_notify()

    def is_on(self) -> bool:
        st = self.hass.states.get(self._opt(OPT_TV).get("entity") or "")  # a configured entity is the truth
        return st.state not in OFF_STATES if st else self._hub.tv.available

    async def _power_on(self) -> None:
        entity = self._opt(OPT_TV).get("entity")
        st = self.hass.states.get(entity) if entity else None
        if st and st.attributes.get("supported_features", 0) & MediaPlayerEntityFeature.TURN_ON:
            await self._svc("media_player", "turn_on", entity)
        else:
            await self._try(self._hub.tv.wake())

    @staticmethod
    async def _until(check, secs: float) -> bool:
        end = time.monotonic() + secs
        while not (ok := check()) and time.monotonic() < end:
            await asyncio.sleep(min(0.2, secs))
        return ok

    async def _svc(self, domain: str, service: str, entity_id: str | None, **data) -> None:
        await self._try(self.hass.services.async_call(domain, service, {"entity_id": entity_id, **data}, blocking=True))

    @staticmethod
    async def _try(aw) -> None:
        try:  # continue_on_error, as every step of the original script
            await aw
        except HomeAssistantError as err:
            _LOGGER.warning("Activity step failed: %s", err)
