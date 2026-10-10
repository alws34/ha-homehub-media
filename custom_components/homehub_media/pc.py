"""Clients for the hardened media-PC daemons (ambient player and coordinator)."""
from __future__ import annotations

import json
from urllib.parse import quote

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .library import read_capped

_TIMEOUT = aiohttp.ClientTimeout(total=10)
_PLAY_TIMEOUT = aiohttp.ClientTimeout(total=30)  # the player waits for HDMI and starts mpv before answering
_REJECTED = {401: "Media PC rejected the token", 403: "Media PC rejected the token",
             421: "Media PC host name mismatch"}


class PCError(HomeAssistantError):
    """The media PC refused or could not serve a request."""


class PCAuthError(PCError):
    """The media PC rejected the bearer token (HTTP 401/403)."""


class _PCClient:
    def __init__(self, hass: HomeAssistant, host: str, port: int, token: str) -> None:
        self._session = async_get_clientsession(hass)
        self._base = f"http://{host}:{port}"  # aiohttp sends Host: host:port from this URL
        self._headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async def _request(self, method: str, path: str, body: dict | None = None,
                       timeout: aiohttp.ClientTimeout = _TIMEOUT) -> dict:
        try:
            async with self._session.request(
                method, f"{self._base}{path}", json=body, headers=self._headers, timeout=timeout,
                allow_redirects=False,  # the token must never follow a redirect; 3xx is an error
            ) as resp:
                if resp.status >= 300:
                    msg = _REJECTED.get(resp.status, "Media PC request failed")
                    if resp.status == 409:
                        try:
                            msg = str(json.loads(await read_capped(resp))["error"])
                        except (ValueError, KeyError, TypeError):
                            pass
                    raise (PCAuthError if resp.status in (401, 403) else PCError)(msg)
                return json.loads(await read_capped(resp) or b"{}") or {}
        except (aiohttp.ClientError, TimeoutError) as err:
            raise PCError("Media PC is unreachable") from err
        except ValueError as err:  # 2xx with a non-JSON or oversized body: not one of our daemons
            raise PCError("Media PC request failed") from err


class AmbientPlayerClient(_PCClient):
    async def status(self) -> dict:
        return await self._request("GET", "/v2/status")

    async def catalog(self) -> dict:
        return await self._request("GET", "/v2/catalog")

    async def refresh(self) -> dict:
        return await self._request("POST", "/v2/catalog/refresh")

    async def play(self, composition: dict) -> dict:
        return await self._request("POST", "/v2/play", composition, _PLAY_TIMEOUT)

    async def patch(self, changes: dict) -> dict:
        return await self._request("PATCH", "/v2/state", changes)

    async def stop(self) -> dict:
        return await self._request("POST", "/v2/stop")


class CoordinatorClient(_PCClient):
    async def status(self) -> dict:
        return await self._request("GET", "/v1/status")

    async def open(self, client: str) -> None:
        await self._request("POST", f"/v1/open/{quote(client, safe='')}")

    async def close(self) -> None:
        await self._request("POST", "/v1/close")

    async def remote(self, command: str) -> None:
        await self._request("POST", f"/v1/remote/{quote(command, safe='')}")

    async def text(self, text: str) -> None:
        await self._request("POST", "/v1/keyboard/text", {"text": text})

    async def pointer_move(self, dx: float, dy: float) -> None:
        await self._request("POST", "/v1/pointer/move", {"dx": dx, "dy": dy})

    async def click(self, button: str = "left") -> None:
        await self._request("POST", "/v1/pointer/click", {"button": button})

    async def scroll(self, dy: float) -> None:
        await self._request("POST", "/v1/pointer/scroll", {"dy": dy})
