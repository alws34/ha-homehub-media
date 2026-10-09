"""Plex / Jellyfin continue watching, recently added and active sessions."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

_LOGGER = logging.getLogger(__name__)
TIMEOUT = aiohttp.ClientTimeout(total=10)
ID_RE = re.compile(r"[A-Za-z0-9-]{1,64}")
PLEX_PATH_RE = re.compile(r"/library/metadata/\d+/(?:thumb|art)/\d+", re.ASCII)  # the only paths art keys may reach
ART = "/api/homehub_media/art"
ART_MAX = 5 * 1024 * 1024  # bytes of one proxied image
TICKS = 10_000_000  # Jellyfin ticks per second
# request failures plus malformed server payloads; messages never contain credentials
FAILURES = (HomeAssistantError, LookupError, TypeError, AttributeError, ValueError)


class InvalidAuth(HomeAssistantError):
    """The server rejected the token / API key (HTTP 401/403)."""


def _check_id(value: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ServiceValidationError("Invalid item id")
    return value


def _guid(value) -> str:
    return str(value).replace("-", "").lower()


def _frac(pos, dur) -> float | None:
    return min(max(pos / dur, 0.0), 1.0) if pos and dur else None


class _Server:
    """Shared HTTP plumbing. Errors surface as HomeAssistantError without server text or credentials."""

    name: str

    def __init__(self, hass: HomeAssistant, url: str, headers: dict, player: str | None) -> None:
        self.hass = hass
        self._url = url.rstrip("/")
        self._headers = headers
        self._player = (player or "").lower()

    def _match(self, *names: str | None) -> bool:
        """`player` is a case-insensitive "name contains" filter; empty matches everything."""
        return not self._player or any(self._player in (n or "").lower() for n in names)

    async def _req(self, path: str, params: dict | None = None, method: str = "GET", accept: str = "application/json"):
        try:
            async with async_get_clientsession(self.hass).request(
                method, self._url + path, params=params, headers={**self._headers, "Accept": accept}, timeout=TIMEOUT
            ) as resp:
                resp.raise_for_status()
                if not accept.startswith("image/"):
                    return await resp.read(), resp.content_type
                # checked before reading the body: never buffer or relay non-images through HA's origin
                if not resp.content_type.startswith("image/"):
                    raise HomeAssistantError(f"{self.name} {path}: not an image")
                if int(resp.headers.get("Content-Length") or 0) > ART_MAX:
                    raise HomeAssistantError(f"{self.name} {path}: image too large")
                body = bytearray()
                async for chunk in resp.content.iter_chunked(65536):  # memory stays bounded without a length
                    body += chunk
                    if len(body) > ART_MAX:
                        raise HomeAssistantError(f"{self.name} {path}: image too large")
                return bytes(body), resp.content_type
        except (aiohttp.ClientError, TimeoutError) as err:
            status = getattr(err, "status", None)
            raise (InvalidAuth if status in (401, 403) else HomeAssistantError)(
                f"{self.name} {path} failed: {status or type(err).__name__}") from None

    async def _json(self, path: str, params: dict | None = None):
        body, _ = await self._req(path, params)
        try:
            return json.loads(body)
        except ValueError:
            raise HomeAssistantError(f"{self.name} {path}: invalid JSON") from None


class PlexServer(_Server):
    name = "plex"

    def __init__(self, hass: HomeAssistant, url: str, token: str, player: str | None) -> None:
        super().__init__(hass, url, {"X-Plex-Token": token, "X-Plex-Client-Identifier": "homehub-media"}, player)

    async def _metadata(self, path: str, params: dict | None = None) -> list[dict]:
        return ((await self._json(path, params)).get("MediaContainer") or {}).get("Metadata") or []

    def _norm(self, m: dict) -> dict:
        show = m.get("grandparentTitle") or m.get("parentTitle")  # episode -> show, season/album -> parent
        thumb = m.get("thumb") or ""
        return {
            "id": str(m["ratingKey"]), "server": self.name, "title": show or m.get("title", ""),
            "subtitle": m.get("title") if show else (str(m["year"]) if m.get("year") else None),
            "type": m.get("type", ""), "progress": _frac(m.get("viewOffset"), m.get("duration")),
            "image": f"{ART}/plex/{base64.urlsafe_b64encode(thumb.encode()).decode().rstrip('=')}" if PLEX_PATH_RE.fullmatch(thumb) else None,
        }

    async def continue_watching(self) -> list[dict]:
        return [self._norm(m) for m in await self._metadata("/hubs/continueWatching/items")]

    async def recently_added(self) -> list[dict]:
        params = {"X-Plex-Container-Start": 0, "X-Plex-Container-Size": 20}
        return [self._norm(m) for m in await self._metadata("/library/recentlyAdded", params)]

    async def sessions(self) -> list[dict]:
        out = []
        # ponytail: not filtered by user: the owner's User.id here (local "1" or the plex.tv id) is unverified, and a
        # wrong guess hides every session; `player` scopes it. Filter on m["User"]["id"] once checked on a live server.
        for m in await self._metadata("/status/sessions"):
            player = m.get("Player") or {}
            if self._match(player.get("title"), player.get("product")):
                item = self._norm(m)
                out.append({
                    "server": self.name, "player": player.get("title") or player.get("product") or "",
                    "title": item["title"], "subtitle": item["subtitle"],
                    "state": "paused" if player.get("state") == "paused" else "playing",
                    "position": (m.get("viewOffset") or 0) / 1000,
                    "duration": m["duration"] / 1000 if m.get("duration") else None, "image": item["image"],
                })
        return out

    async def fetch_art(self, key: str) -> tuple[bytes, str]:
        try:
            path = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4)).decode()
        except ValueError:  # bad base64 or non-UTF-8
            raise ServiceValidationError("Invalid art key") from None
        if not PLEX_PATH_RE.fullmatch(path):  # token only ever reaches the image transcoder, for a known thumb/art path
            raise ServiceValidationError("Invalid art key")
        return await self._req(
            "/photo/:/transcode", {"url": path, "width": 300, "height": 450, "minSize": 1, "upscale": 1}, accept="image/*")

    async def play(self, item_id: str) -> bool:
        _check_id(item_id)
        players = [
            (e.entity_id, st.name) for e in er.async_get(self.hass).entities.values()
            if e.platform == "plex" and e.entity_id.startswith("media_player.") and not e.disabled_by
            and (st := self.hass.states.get(e.entity_id)) and st.state != "unavailable"
            and self._match(e.entity_id, st.name)]
        if not self._player and len(players) > 1:  # never start on a random phone: only a TV-looking player
            players = [p for p in players if any(w in n.casefold() for n in p for w in ("samsung", "tv"))]
        if not players:
            _LOGGER.debug("plex play: no Plex player to target; set the player filter if there are several")
            return False
        entity = players[0][0]
        try:
            machine = (await self._json("/identity"))["MediaContainer"]["machineIdentifier"]
            await self.hass.services.async_call("media_player", "play_media", {
                "entity_id": entity, "media_content_type": "movie",
                "media_content_id": f"plex://{machine}/{item_id}?resume=1"}, blocking=True)
        except FAILURES as err:
            _LOGGER.debug("plex play failed: %s", err)  # caller retries while the TV app starts
            return False
        return True


class JellyfinServer(_Server):
    name = "jellyfin"

    def __init__(self, hass: HomeAssistant, url: str, api_key: str, user_id: str, player: str | None) -> None:
        super().__init__(hass, url, {"Authorization": f'MediaBrowser Token="{api_key}"'}, player)
        self._user = user_id

    def _norm(self, m: dict) -> dict:
        show = m.get("SeriesName")
        return {
            "id": m["Id"], "server": self.name, "title": show or m.get("Name", ""),
            "subtitle": m.get("Name") if show else (str(m["ProductionYear"]) if m.get("ProductionYear") else None),
            "type": m.get("Type", "").lower(),
            "progress": _frac((m.get("UserData") or {}).get("PlaybackPositionTicks"), m.get("RunTimeTicks")),
            "image": f"{ART}/jellyfin/{m['Id']}" if ID_RE.fullmatch(str(m["Id"])) else None,
        }

    async def users(self) -> list[dict]:
        """[{"id", "name"}] of the server's users (needs an admin API key)."""
        return [{"id": str(u["Id"]), "name": str(u.get("Name") or u["Id"])} for u in await self._json("/Users")]

    async def continue_watching(self) -> list[dict]:
        data = await self._json("/UserItems/Resume", {"userId": self._user, "limit": 20, "mediaTypes": "Video"})
        return [self._norm(m) for m in data.get("Items") or []]

    async def recently_added(self) -> list[dict]:
        return [self._norm(m) for m in await self._json("/Items/Latest", {"userId": self._user, "limit": 20})]

    async def _sessions(self) -> list[dict]:
        # configured user only; GUIDs compared without dashes/case (Jellyfin emits them dashless)
        return [s for s in await self._json("/Sessions", {"activeWithinSeconds": 120})
                if _guid(s.get("UserId", self._user)) == _guid(self._user)
                and self._match(s.get("DeviceName"), s.get("Client"))]

    async def sessions(self) -> list[dict]:
        out = []
        for s in await self._sessions():
            if item := s.get("NowPlayingItem"):
                norm, state = self._norm(item), s.get("PlayState") or {}
                out.append({
                    "server": self.name, "player": s.get("DeviceName") or s.get("Client") or "",
                    "title": norm["title"], "subtitle": norm["subtitle"],
                    "state": "paused" if state.get("IsPaused") else "playing",
                    "position": (state.get("PositionTicks") or 0) / TICKS,
                    "duration": item["RunTimeTicks"] / TICKS if item.get("RunTimeTicks") else None,
                    "image": norm["image"],
                })
        return out

    async def fetch_art(self, key: str) -> tuple[bytes, str]:
        return await self._req(f"/Items/{_check_id(key)}/Images/Primary", {"maxHeight": 450}, accept="image/*")

    async def play(self, item_id: str) -> bool:
        _check_id(item_id)
        try:
            controllable = [s for s in await self._sessions() if s.get("SupportsMediaControl")]
            if not controllable:
                return False
            session = max(controllable, key=lambda s: s.get("LastActivityDate") or "")  # ISO-8601 sorts as text
            items = (await self._json("/Items", {"ids": item_id, "userId": self._user})).get("Items") or [{}]
            ticks = (items[0].get("UserData") or {}).get("PlaybackPositionTicks") or 0
            await self._req(
                f"/Sessions/{_check_id(session['Id'])}/Playing",
                {"playCommand": "PlayNow", "itemIds": item_id, "startPositionTicks": ticks}, method="POST")
        except FAILURES as err:
            _LOGGER.debug("jellyfin play failed: %s", err)  # caller retries while the TV app starts
            return False
        return True


class Library:
    def __init__(self, hass: HomeAssistant, servers: list, on_change: Callable[[], None]) -> None:
        self.hass, self._on_change = hass, on_change
        self._servers = {s.name: s for s in servers}
        self.items: dict[str, dict] = {n: {"continue": [], "recent": []} for n in self._servers}
        self.now_playing: list[dict] = []

    def server(self, name: str):
        return self._servers.get(name)

    async def async_refresh(self, _now: datetime | None = None) -> None:
        async def one(s) -> None:
            try:
                self.items[s.name] = {"continue": await s.continue_watching(), "recent": await s.recently_added()}
            except FAILURES as err:
                _LOGGER.warning("%s library refresh failed: %s", s.name, err)

        before = dict(self.items)
        await asyncio.gather(*(one(s) for s in self._servers.values()))
        if self.items != before:
            self._on_change()

    async def async_refresh_sessions(self, _now: datetime | None = None) -> None:
        async def one(s) -> list[dict]:
            try:
                return await s.sessions()
            except FAILURES as err:
                _LOGGER.debug("%s session refresh failed: %s", s.name, err)  # polled every 10 s: no warning spam
                return []

        now = [n for r in await asyncio.gather(*(one(s) for s in self._servers.values())) for n in r]
        if now != self.now_playing:  # idle polls stay silent
            self.now_playing = now
            self._on_change()

    def async_start(self) -> Callable[[], None]:
        """Schedule polling (no immediate fetch: callers await async_refresh() first). Returns cancel."""
        cancels = [async_track_time_interval(self.hass, self.async_refresh, timedelta(minutes=5)),
                   async_track_time_interval(self.hass, self.async_refresh_sessions, timedelta(seconds=10))]

        def cancel() -> None:
            for c in cancels:
                c()

        return cancel
