"""Client for the ESP32 BLE-HID keyboard bridge."""
from __future__ import annotations

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_TIMEOUT = aiohttp.ClientTimeout(total=15)  # BLE typing is slow; same budget as the original rest_command


class KeyboardBridge:
    def __init__(self, hass: HomeAssistant, host: str, key: str) -> None:
        self._session = async_get_clientsession(hass)
        self._base = f"http://{host}"
        self._headers = {"X-HomeHub-Key": key, "Content-Type": "text/plain"}

    async def type(self, text: str) -> None:
        await self._post("/type", text)

    async def key(self, name: str) -> None:
        await self._post("/key", name)  # backspace | enter | clear

    async def _post(self, path: str, body: str) -> None:
        try:
            async with self._session.post(
                f"{self._base}{path}", data=body, headers=self._headers, timeout=_TIMEOUT,
                allow_redirects=False,  # the key must never follow a redirect; 3xx is an error
            ) as resp:
                if resp.status >= 300:
                    raise HomeAssistantError(
                        "Keyboard bridge rejected the shared key" if resp.status in (401, 403)
                        else "Keyboard bridge request failed"
                    )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError("Keyboard bridge is unreachable") from err
