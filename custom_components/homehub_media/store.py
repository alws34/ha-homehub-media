"""Persistent per-mode compositions and favourites."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .catalog import ID_RE, validate_composition
from .const import DOMAIN


class HomeHubStore:
    def __init__(self, hass: HomeAssistant) -> None:
        # ponytail: no migration yet; bump the Store version and add a migrate func when the shape changes
        self._store = Store(hass, 1, f"{DOMAIN}.data")
        self._data: dict = {"compositions": {}, "favorites": {"visual": [], "soundtrack": []}}

    async def async_load(self) -> None:
        saved = await self._store.async_load() or {}
        self._data = {
            "compositions": saved.get("compositions", {}),
            "favorites": {"visual": [], "soundtrack": [], **saved.get("favorites", {})},
        }

    def composition(self, mode_id: str) -> dict | None:
        comp = self._data["compositions"].get(mode_id)
        return dict(comp) if comp else None

    async def async_save_composition(self, mode_id: str, comp: dict) -> None:
        self._data["compositions"][mode_id] = validate_composition(comp)
        await self._store.async_save(self._data)

    def favorites(self, kind: str) -> set[str]:
        return set(self._data["favorites"][kind])

    async def async_set_favorite(self, kind: str, media_id: str, favorite: bool) -> None:
        if kind not in ("visual", "soundtrack") or not ID_RE.fullmatch(media_id):
            raise ValueError("invalid favourite")
        favs = self.favorites(kind)
        favs.add(media_id) if favorite else favs.discard(media_id)
        self._data["favorites"][kind] = sorted(favs)
        await self._store.async_save(self._data)
