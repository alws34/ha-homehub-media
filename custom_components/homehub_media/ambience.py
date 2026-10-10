"""Ambience modes: lighting snapshot/scene/flicker plus TV-page or media-PC playback."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import secrets
import time
import unicodedata
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path
from urllib.parse import quote

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.network import get_url
from homeassistant.util import dt as dt_util

from . import catalog as cat
from .activity import call_service
from .const import (
    APP_CATALOG,
    DEFAULT_DAYPARTS,
    DEFAULT_MODES,
    MEDIA_ROOT,
    OPT_AMBIENCE,
    OPT_DAYPARTS,
    OPT_MODES,
    OPT_TV,
    PAGE_FILE,
    PREFERRED_VISUAL,
    SOUNDTRACK_DIR,
    STATIC_URL,
    VIDEO_DIR,
)
from .library import read_capped
from .store import HomeHubStore

_LOGGER = logging.getLogger(__name__)

# (brightness_pct, transition_s), ported from homehub_media_controller.yaml:2847-2882
FLICKER = [(24, 3.2), (12, 3.8), (28, 2.9), (16, 4.1), (22, 3.5), (9, 4.2), (30, 2.8), (14, 3.9), (26, 3.1),
           (8, 4.3), (27, 3.0), (17, 3.7), (23, 3.4), (10, 4.0), (25, 3.2), (13, 3.8), (11, 2.6)]
SNAPSHOT_ID = "homehub_media_snapshot"
STATE_PATH = "/api/homehub_media/page/state"
STATE_URL_HOURS = 2  # the page's state URL signature; media URLs keep the default 24 h
HEALTH_SECS, PAGE_GRACE_SECS, PAGE_TIMEOUT_SECS, FADE_SECS, ENTER_DELAY_SECS = 30, 60, 45, 60, 4
OEMBED, OEMBED_SECS, NAME_MAX = "https://www.youtube.com/oembed", 5, 80
IDLE_APP_STATES = {"", "unknown", "home", "Home", "TV", "off"}
_DIRS = {"visual": VIDEO_DIR, "soundtrack": SOUNDTRACK_DIR}
_SERVER_ONLY = {"path", "preview_path", "url", "youtube_id"}
# Composition -> K8 PATCH layers: (layer, media key, enabled key, {composition key: layer field})
_LAYERS = (
    ("video", "visual", "visual_enabled",
     {"video_loop": "loop", "video_random_start": "random_start", "original_volume": "volume"}),
    ("soundtrack", "soundtrack", None,
     {"soundtrack_loop": "loop", "soundtrack_random_start": "random_start", "soundtrack_volume": "volume"}),
)


def _pc_patch(comp: dict, keys: set) -> dict:
    """K8 PATCH body for the changed composition keys; layers not named stay untouched on the PC."""
    body = {}
    for layer, media, enabled, fields in _LAYERS:
        on = comp[enabled] if enabled else comp[media] is not None
        if keys & {media, enabled}:  # (re)select the layer: the PC wants enabled=false alone
            body[layer] = ({"enabled": True, "media_id": comp[media], **{f: comp[k] for k, f in fields.items()}}
                           if on else {"enabled": False})
        elif on and (part := {f: comp[k] for k, f in fields.items() if k in keys}):
            body[layer] = part  # volume/loop only: never (re)starts a layer that has ended
    return body


async def _cancel(jobs: list) -> None:
    """Cancel unsub callbacks and tasks; wait for the tasks so no light call lands after a restore."""
    tasks = [j for j in jobs if isinstance(j, asyncio.Task)]
    for job in jobs:
        (job.cancel if isinstance(job, asyncio.Task) else job)()
    jobs.clear()
    if tasks:
        await asyncio.wait(tasks)


class AmbienceManager:
    def __init__(self, hass: HomeAssistant, hub, store: HomeHubStore) -> None:
        self.hass, self._hub, self._store = hass, hub, store
        self.mode: str = "off"
        self.error: str | None = None
        self.sleep_until: datetime | None = None
        self.auto = False
        self.uses_media = False
        self.page_version = int(time.time() * 1000)  # clock-seeded: a restart never repeats a version
        self.page_last_seen = 0.0
        self._sid: str | None = None  # TV page session: only its state URL is answered
        self._catalog: dict[str, list[dict]] = {"visuals": [], "soundtracks": []}
        self._comp: dict | None = None
        self._yt: str | None = None  # video id while the media is a YouTube visual on the TV
        self._snapshot = False  # scene.homehub_media_snapshot holds the pre-ambience lights
        self._fade_secs: int | None = None
        self._started = 0.0
        self._gen = 0  # bumped by start(); timers and checks capture it
        self._jobs: list = []   # flicker task, health interval, autoplay key timer of the running mode
        self._sleep: list = []  # sleep timer chain (timer, PC fade task)
        self._unsub_idle = None
        self._lock = asyncio.Lock()

    # ---- options / data ----------------------------------------------------------------------------

    @property
    def _backend(self) -> str:
        want = (self._hub.options.get(OPT_AMBIENCE) or {}).get("backend")
        return "pc" if want == "pc" and self._hub.pc_ambient else "tv"

    def modes(self) -> list[dict]:
        return self._hub.options.get(OPT_MODES, DEFAULT_MODES)

    def _mode(self, mode_id: str) -> dict:
        if mode := next((m for m in self.modes() if m["id"] == mode_id), None):
            return mode
        raise ServiceValidationError(f"Unknown ambience mode: {mode_id}")

    def composition(self, mode_id: str) -> dict:
        if comp := self._store.composition(mode_id):
            return comp
        ids = [v["id"] for v in self._catalog["visuals"]]
        pref = PREFERRED_VISUAL.get(mode_id)
        return cat.validate_composition({"visual": pref if pref in ids else next(iter(ids), None)})

    async def async_load(self) -> None:
        """Local data only: fast, and awaited in setup so a save right after it cannot race the load."""
        await self._store.async_load()

    async def async_load_catalog(self) -> None:
        try:
            await self.refresh_catalog()
        except HomeAssistantError as err:
            self.error = str(err)
            _LOGGER.warning("Ambience catalog unavailable: %s", err)
        self._unsub_idle = async_track_time_interval(self.hass, self._idle_check, timedelta(minutes=1))

    async def async_shutdown(self) -> None:
        if self._unsub_idle:
            self._unsub_idle()
            self._unsub_idle = None
        if self.mode != "off":
            await self.stop()

    def _scan_tv(self) -> dict:
        root = Path(self.hass.config.media_dirs.get("local", self.hass.config.path("media")), MEDIA_ROOT)
        found = {}
        for kind, sub in _DIRS.items():
            (root / sub).mkdir(parents=True, exist_ok=True)
            found[f"{kind}s"] = items = cat.scan(root / sub, kind)
            for item in items:
                pic = item["preview_path"]
                item["preview"] = f"/media/local/{MEDIA_ROOT}/{sub}/{quote(pic)}" if pic else None
        return found

    def _add_pc_previews(self, items: list[dict]) -> None:
        for item in items:  # ids are ID_RE-validated, so they are safe as file names
            item["preview"] = next((f"/local/Ambience/{item['id']}{ext}" for ext in (".webp", ".png")
                                    if os.path.isfile(self.hass.config.path("www", "Ambience", item["id"] + ext))),
                                   None)

    async def _scan(self, amb: dict) -> dict:
        """The backend's files (and radio stations)."""
        if self._backend == "pc":
            await self._hub.pc_ambient.refresh()  # rescan the PC's disk
            data = await self._hub.pc_ambient.catalog()
            found = {key: [{"id": i["id"], "kind": key[:-1], "title": str(i.get("title") or i["id"]),
                            "source_type": "stream" if i.get("source_type") == "stream" else "file",
                            "live": i.get("source_type") == "stream", "duration": i.get("duration")}
                           for i in data.get(key) or []
                           if isinstance(i, dict) and isinstance(i.get("id"), str) and cat.ID_RE.fullmatch(i["id"])]
                     for key in ("visuals", "soundtracks")}
            await self.hass.async_add_executor_job(self._add_pc_previews, found["visuals"] + found["soundtracks"])
            return found
        try:
            radio = cat.radio_items(amb.get("radio") or {})
            found = await self.hass.async_add_executor_job(self._scan_tv)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        except OSError as err:
            raise HomeAssistantError("Cannot read the ambience media folder") from err
        found["soundtracks"] += [{**r, "preview": None} for r in radio]
        return found

    async def refresh_catalog(self) -> None:
        """Rescan; YouTube videos need no scan, so they are offered even when the scan fails (which still raises)."""
        amb, error = self._hub.options.get(OPT_AMBIENCE) or {}, None
        try:
            found = await self._scan(amb)
        except HomeAssistantError as err:  # keep the files the last scan found
            found = {k: [i for i in items if i.get("source_type") != "youtube"] for k, items in self._catalog.items()}
            error = err
        try:  # YouTube plays in the TV's app whatever the backend
            found["visuals"] += cat.youtube_items(amb.get("youtube") or {}, {v["id"] for v in found["visuals"]})
        except ValueError as err:
            error = error or ServiceValidationError(str(err))
        for kind in _DIRS:
            favs = self._store.favorites(kind)
            for item in found[f"{kind}s"]:
                item["favorite"] = item["id"] in favs
        self._catalog = found
        self._hub.async_notify()
        if error:
            raise error

    def has_youtube(self, video_id: str) -> bool:
        return any(v.get("youtube_id") == video_id for v in self._catalog["visuals"])

    def catalog(self) -> dict:
        # ponytail: TV previews are unsigned /media/local/... paths; the ws handler signs them per caller (K5)
        return {key: [{k: v for k, v in item.items() if k not in _SERVER_ONLY} for item in items]
                for key, items in self._catalog.items()}

    # ---- start / stop / update ---------------------------------------------------------------------

    def _youtube_id(self, comp: dict) -> str | None:
        """Video id when the composition shows a YouTube visual."""
        item = comp["visual_enabled"] and next((i for i in self._catalog["visuals"] if i["id"] == comp["visual"]), None)
        return item.get("youtube_id") if item else None

    def _with_visual(self, comp: dict, shuffle: bool) -> dict:
        """This run's visual: a random one of the `visuals` still in the catalog when shuffling or `visual` is
        unset; else `visual` (no visual at all turns the layer off)."""
        if comp["visuals"] and (shuffle or comp["visual"] is None):
            known = {i["id"] for i in self._catalog["visuals"]}
            pool = [v for v in comp["visuals"] if v in known]
            visual = random.choice(pool) if pool else comp["visual"]
            comp = {**comp, "visual": visual, "visual_enabled": comp["visual_enabled"] and visual is not None}
        return comp

    @property
    def _youtube_app(self) -> str:
        return self._hub.apps.get("youtube") or APP_CATALOG["youtube"]["ids"][0]

    async def start(self, mode_id: str, composition: dict | None = None, *, shuffle: bool = True,
                    video: str | None = None) -> None:
        """`video`: play this catalog visual instead of the mode's own pick."""
        mode = self._mode(mode_id)
        try:
            comp = self.composition(mode_id) if composition is None else cat.validate_composition(composition)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        if video is not None:
            if all(i["id"] != video for i in self._catalog["visuals"]):
                raise ServiceValidationError(f"Unknown video: {video}")
            comp, shuffle = {**comp, "visual": video, "visual_enabled": True}, False
        comp = self._with_visual(comp, shuffle)
        yt = None if mode["lights_only"] else self._youtube_id(comp)
        player = "tv" if yt else self._backend  # YouTube plays in the TV's app whatever the backend
        lighting = bool(mode["scene"] or mode["flicker"])
        async with self._lock:
            self._gen += 1  # in-flight health checks / sleep timers of the previous run become no-ops
            need_activity = not mode["lights_only"] and (
                player == "pc" or self._hub.activity.current != "tv" or not self._hub.tv.available)
            # the same player stays up: swap media in place. The TV page changes URLs without a reload (while
            # it still polls); YouTube switches to the next video over its lounge session, without closing.
            page_up = self._backend == "tv" and time.monotonic() - self.page_last_seen < PAGE_TIMEOUT_SECS
            swap = (self.uses_media and not mode["lights_only"] and not need_activity
                    and (self._yt is not None if yt else self._yt is None and page_up))
            if self.mode != "off":  # switching: keep the pre-ambience snapshot if the new mode drives lights
                # ponytail: lights only the new mode touches are not in the kept snapshot, so stay as set
                await self._stop(restore_lights=not lighting, keep_page=swap)
            try:
                # before self.mode is set: activity.set() stops a running media ambience
                if need_activity and not await self._hub.activity.set(player):
                    await self._stop()  # superseded by a newer activity request: start nothing
                    return
                if lighting:
                    await self._lights(mode)
                self.mode, self.uses_media, self._comp, self.error = mode_id, not mode["lights_only"], comp, None
                if self.uses_media:
                    await self._play(comp, swap)
            except Exception as err:
                self.error = str(err) if isinstance(err, HomeAssistantError) else "Could not start ambience"
                await self._stop()
                if isinstance(err, HomeAssistantError):
                    raise
                raise HomeAssistantError(self.error) from err
        self._hub.async_notify()

    async def _lights(self, mode: dict) -> None:
        call = partial(call_service, self.hass)  # bounded: this runs under the lock
        if not self._snapshot:
            ents, snap = list(mode["flicker"]), set()
            if mode["scene"] and (scene := self.hass.states.get(mode["scene"])):
                ents += scene.attributes.get("entity_id", [])
            for ent in ents:  # light groups expand to their members
                state = self.hass.states.get(ent)
                snap.update(state and state.attributes.get("entity_id") or [ent])
            if snap:
                await call("scene", "create", {"scene_id": SNAPSHOT_ID, "snapshot_entities": sorted(snap)})
                self._snapshot = True
        if mode["scene"]:
            await call("scene", "turn_on", {"entity_id": mode["scene"]})
        if mode["flicker"]:
            self._jobs.append(self.hass.async_create_background_task(
                self._flicker(list(mode["flicker"])), "homehub_media_flicker"))

    async def _flicker(self, lights: list[str]) -> None:
        while True:
            for pct, secs in FLICKER:
                try:
                    await call_service(self.hass, "light", "turn_on",
                                       {"entity_id": lights, "brightness_pct": pct, "transition": secs})
                except HomeAssistantError as err:
                    _LOGGER.debug("Flicker step failed: %s", err)
                await asyncio.sleep(secs)

    async def _play(self, comp: dict, swap: bool = False) -> None:
        if yt := self._youtube_id(comp):
            if comp["soundtrack"]:
                _LOGGER.debug("YouTube visual plays its own sound; soundtrack %s is not used", comp["soundtrack"])
            self._yt = yt  # first: a failed start still stops what began
            await self._hub.tv.play_youtube(yt, self._youtube_app)
        elif self._backend == "pc":
            await self._hub.pc_ambient.play({k: v for k, v in comp.items() if k != "visuals"})
        else:
            self.page_version += 1
            if not swap:
                await self._hub.tv.open_url(self._page_url())
                self.page_last_seen = time.monotonic()  # counts as alive while it loads
                self._jobs.append(async_call_later(self.hass, ENTER_DELAY_SECS, self._press_enter))
            elif time.monotonic() - self._started < ENTER_DELAY_SECS:  # swapped before the autoplay key fired
                self._jobs.append(async_call_later(self.hass, ENTER_DELAY_SECS, self._press_enter))
        self._started = time.monotonic()
        self._jobs.append(async_track_time_interval(
            self.hass, partial(self._health, self._gen), timedelta(seconds=HEALTH_SECS)))

    def _page_url(self) -> str:
        self._sid = secrets.token_urlsafe(16)  # a fresh page: every earlier state URL stops working
        base = (self._hub.options.get(OPT_TV) or {}).get("base_url") or get_url(
            self.hass, allow_external=False, allow_cloud=False, prefer_external=False)
        return f"{base.rstrip('/')}{STATIC_URL}/{PAGE_FILE}?s={quote(self._state_url(), safe='')}"

    def _state_url(self) -> str:
        return self._hub.sign_path(f"{STATE_PATH}?sid={self._sid}", hours=STATE_URL_HOURS)

    async def _press_enter(self, _now: datetime) -> None:  # user gesture so the page may autoplay with sound
        try:
            await self._hub.tv.send_key("KEY_ENTER")
        except HomeAssistantError as err:
            _LOGGER.debug("Autoplay key failed: %s", err)

    async def stop(self, restore_lights: bool = True) -> None:
        async with self._lock:
            await self._stop(restore_lights)

    async def _stop_if(self, gen: int, _now: datetime | None = None) -> None:
        """Stop only if no start() happened since `gen` was captured (stale timers/checks are no-ops)."""
        async with self._lock:
            if gen == self._gen:
                await self._stop()

    async def _stop(self, restore_lights: bool = True, keep_page: bool = False) -> None:
        self._hub.last_command = time.monotonic()  # any stop counts as activity: auto ambience must not restart it
        await _cancel(self._jobs)
        await _cancel(self._sleep)
        self.sleep_until = self._fade_secs = None
        if self.uses_media and not keep_page:
            self.uses_media = False
            self.page_version += 1  # page sees stop: true
            yt, self._yt = self._yt, None
            try:
                if yt:
                    await self._hub.tv.stop_youtube(self._youtube_app)
                elif self._backend == "pc":
                    await self._hub.pc_ambient.stop()
                elif time.monotonic() - self.page_last_seen < PAGE_TIMEOUT_SECS:  # never HOME the user's app
                    await self._hub.tv.send_key("KEY_HOME")
            except HomeAssistantError as err:
                _LOGGER.warning("Could not stop ambience media: %s", err)
        if restore_lights and self._snapshot:
            self._snapshot = False
            try:
                await call_service(self.hass, "scene", "turn_on", {"entity_id": f"scene.{SNAPSHOT_ID}"})
            except HomeAssistantError as err:
                _LOGGER.warning("Could not restore lights: %s", err)
        self.mode = "off"
        self._hub.async_notify()

    async def update(self, changes: dict) -> None:
        async with self._lock:
            if not self.uses_media:
                raise ServiceValidationError("No ambience media is playing")
            try:
                part = cat.validate_composition(changes, partial=True)
                comp = self._with_visual(cat.validate_composition({**self._comp, **part}), shuffle=False)
            except ValueError as err:
                raise ServiceValidationError(str(err)) from err
            yt, restart = self._youtube_id(comp), None
            if (yt is None) != (self._yt is None):  # YouTube <-> file: another player, so start over below
                restart = self.mode
            else:
                if yt and yt != self._yt:
                    await self._hub.tv.play_youtube(yt, self._youtube_app)  # warm: YouTube is up already
                    self._yt = yt
                elif not yt and self._backend == "pc" and (body := _pc_patch(comp, set(part))):
                    await self._hub.pc_ambient.patch(body)
                self._comp = comp
                self.page_version += 1
        if restart:
            await self.start(restart, comp, shuffle=False)
        else:
            self._hub.async_notify()

    async def save_mode(self, mode_id: str, comp: dict) -> None:
        self._mode(mode_id)
        try:
            await self._store.async_save_composition(mode_id, comp)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        self._hub.async_notify()

    def _used(self, mode_id: str) -> set[str]:
        comp = self.composition(mode_id)
        return {comp["visual"], *comp["visuals"]}

    async def _video_name(self, video: dict, taken: dict[str, str]) -> str:
        """The given name, else the video's YouTube title (its id when that fails), fit for name=link."""
        if name := (video.get("name") or "").strip():
            return name
        vid = cat.youtube_id(video["url"])
        try:
            async with async_get_clientsession(self.hass).get(
                    OEMBED, params={"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"},
                    timeout=aiohttp.ClientTimeout(total=OEMBED_SECS)) as resp:
                resp.raise_for_status()
                title = json.loads(await read_capped(resp)).get("title")
        except (aiohttp.ClientError, TimeoutError, ValueError, AttributeError) as err:
            _LOGGER.debug("No YouTube title for %s: %s", vid, err)
            title = None
        clean = "".join(" " if c == "=" or unicodedata.category(c)[0] == "C" else c for c in str(title or ""))
        name = " ".join(clean.split())[:NAME_MAX].strip() or vid
        if name in taken and cat.youtube_id(taken[name]) != vid:  # never take over another video's name
            name = f"{name[:NAME_MAX - 14].strip()} ({vid})"
        return name

    async def set_mode_videos(self, mode_id: str, videos: list[dict]) -> None:
        """The mode's visuals become exactly these YouTube videos (added to or changed in the options' list by
        name; a video without a name gets its YouTube title); the videos only this mode used are dropped."""
        self._mode(mode_id)
        amb = self._hub.options.get(OPT_AMBIENCE) or {}
        taken = {**(amb.get("youtube") or {}), **{v["name"].strip(): v["url"] for v in videos if v.get("name")}}
        try:
            names = await asyncio.gather(*(self._video_name(v, taken) for v in videos))
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        new = {name: v["url"].strip() for name, v in zip(names, videos, strict=True)}
        if len(new) < len(videos):
            raise ServiceValidationError("Each video needs its own name")
        merged = {**(amb.get("youtube") or {}), **new}  # changed names keep their place, new ones go last
        files = {v["id"] for v in self._catalog["visuals"] if v["source_type"] != "youtube"}
        try:
            drop = self._used(mode_id) - {i for m in self.modes() if m["id"] != mode_id for i in self._used(m["id"])}
            keep = {name: url for (name, url), item in zip(merged.items(), cat.youtube_items(merged, files))
                    if name in new or item["id"] not in drop}
            # ponytail: a drop can shift a later same-slug video's id (rain_2 -> rain); persist ids if that bites
            ids = dict(zip(keep, (i["id"] for i in cat.youtube_items(keep, files))))
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        self._hub.options[OPT_AMBIENCE] = amb = {**amb, "youtube": keep}
        entry = self._hub.entry  # no update listener, so this does not reload the entry (see rescan_apps)
        self.hass.config_entries.async_update_entry(entry, options={**entry.options, OPT_AMBIENCE: amb})
        visuals = [ids[name] for name in new]
        await self.save_mode(mode_id, {**self.composition(mode_id), "visual": next(iter(visuals), None),
                                       "visuals": visuals, "visual_enabled": bool(visuals)})
        if mode_id == self.mode and self._comp:  # the running mode's next pick (and the card's list) follow
            self._comp = {**self._comp, "visuals": visuals}
        try:
            await self.refresh_catalog()
        except HomeAssistantError as err:  # the videos are saved; only the file scan failed
            _LOGGER.warning("Ambience catalog unavailable: %s", err)

    async def set_favorite(self, kind: str, media_id: str, favorite: bool) -> None:
        try:
            await self._store.async_set_favorite(kind, media_id, favorite)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        for item in self._catalog[f"{kind}s"]:
            if item["id"] == media_id:
                item["favorite"] = favorite
        self._hub.async_notify()

    # ---- health, sleep timer, dayparts ------------------------------------------------------------

    async def _health(self, gen: int, _now: datetime) -> None:
        if self._yt:  # YouTube left the screen (HOME, another app: it keeps running hidden) or the TV went off
            if time.monotonic() - self._started <= PAGE_GRACE_SECS:
                return
            try:  # an unavailable TV (the hub polls it every 30 s) counts as gone
                info = await self._hub.tv.app_info(self._youtube_app) if self._hub.tv.available else None
            except HomeAssistantError as err:  # one failed poll is no proof that the TV is off
                _LOGGER.debug("YouTube status failed: %s", err)
                return
            if not (gone := not (info or {}).get("visible")):
                await self._hub.tv.youtube_keepalive()  # a dropped lounge session would stop the loop
        elif self._backend == "pc":
            try:
                gone = (await self._hub.pc_ambient.status()).get("state") == "idle"
            except HomeAssistantError as err:  # ponytail: an unreachable PC keeps the mode on; stop() still works
                _LOGGER.debug("Ambient player status failed: %s", err)
                return
        else:  # the user left the browser (or it never loaded): no poll for PAGE_TIMEOUT_SECS
            now = time.monotonic()
            gone = now - self._started > PAGE_GRACE_SECS and now - self.page_last_seen > PAGE_TIMEOUT_SECS
        if gone:
            _LOGGER.info("Ambience playback ended, stopping %s", self.mode)
            await self._stop_if(gen)

    async def set_sleep_timer(self, minutes: int) -> None:
        await _cancel(self._sleep)
        self.sleep_until = None
        if minutes:
            if self.mode == "off":
                raise ServiceValidationError("No ambience mode is running")
            self.sleep_until = dt_util.utcnow() + timedelta(minutes=minutes)
            self._sleep.append(async_call_later(
                self.hass, minutes * 60 - FADE_SECS, partial(self._sleep_fade, self._gen)))
        self._hub.async_notify()

    async def _sleep_fade(self, gen: int, _now: datetime) -> None:  # no await: cannot interleave with start()
        # ponytail: a YouTube visual does not fade (its volume is the TV's); it just stops at the end
        if self.uses_media and self._backend == "pc" and not self._yt:
            self._sleep.append(self.hass.async_create_background_task(
                self._fade_pc(dict(self._comp)), "homehub_media_fade"))
        elif self.uses_media:
            self._fade_secs = FADE_SECS
            self.page_version += 1
        self._sleep.append(async_call_later(self.hass, FADE_SECS, partial(self._stop_if, gen)))

    async def _fade_pc(self, comp: dict) -> None:
        for step in range(5, -1, -1):  # 6 PATCH calls over FADE_SECS, the last one at volume 0
            faded = {**comp, "original_volume": comp["original_volume"] * step // 6,
                     "soundtrack_volume": comp["soundtrack_volume"] * step // 6}
            try:
                await self._hub.pc_ambient.patch(_pc_patch(faded, {"original_volume", "soundtrack_volume"}))
            except HomeAssistantError:
                return
            await asyncio.sleep(FADE_SECS / 6)

    def _dayparts(self) -> dict:
        return self._hub.options.get(OPT_DAYPARTS) or DEFAULT_DAYPARTS

    def daypart_mode(self, now: datetime | None = None) -> str | None:
        hhmm = (now or dt_util.now()).strftime("%H:%M")
        dps = self._dayparts()
        parts = sorted((dps[p]["start"][:5], dps[p]["mode"]) for p in ("morning", "afternoon", "evening", "night"))
        return next((mode for start, mode in reversed(parts) if start <= hhmm), parts[-1][1])

    async def auto_start(self) -> None:
        if not (mode_id := self.daypart_mode()):
            raise ServiceValidationError("No ambience mode is set for this time of day")
        await self.start(mode_id)

    async def _idle_check(self, _now: datetime) -> None:
        hub, tv = self._hub, self._hub.options.get(OPT_TV) or {}
        if (self._unsub_idle is None or not self.auto or self.mode != "off" or hub.activity.current != "tv"
                or not self.daypart_mode()):  # _unsub_idle None: unloading
            return
        app = tv.get("app_entity") and self.hass.states.get(tv["app_entity"])
        if (hub.activity.is_on()
                and not (hub.library and hub.library.now_playing)
                and time.monotonic() - hub.last_command >= self._dayparts().get("idle_minutes", 10) * 60
                and (not app or app.state in IDLE_APP_STATES)):
            try:
                await self.auto_start()
            except HomeAssistantError as err:
                _LOGGER.warning("Idle auto-start failed: %s", err)

    # ---- outputs -------------------------------------------------------------------------------------

    def _layer(self, kind: str, media_id: str | None, loop: bool, random_start: bool, volume: int) -> dict | None:
        item = next((i for i in self._catalog[f"{kind}s"] if i["id"] == media_id), None)
        if item and "url" in item:  # radio: cross-origin https, allowed by the page only with stream: true
            return {"url": item["url"], "stream": True, "loop": False, "random_start": False, "volume": volume / 100}
        if item and "path" in item:
            url = self._hub.sign_path(f"/media/local/{MEDIA_ROOT}/{_DIRS[kind]}/{quote(item['path'])}")
            return {"url": url, "loop": loop, "random_start": random_start, "volume": volume / 100}
        return None

    def page_state(self, sid: str | None) -> dict | None:
        """K7 payload for the current TV page session (None for any other sid); every answer is a heartbeat."""
        if not self._sid or sid != self._sid:
            return None
        if not self.uses_media:
            self._sid = None  # the page gets this stop once; the session then ends
        self.page_last_seen = time.monotonic()
        comp = self._comp if self.uses_media else None
        return {
            "version": self.page_version, "stop": comp is None, "fade_secs": self._fade_secs,
            "visual": comp and comp["visual_enabled"] and self._layer(
                "visual", comp["visual"], comp["video_loop"], comp["video_random_start"],
                comp["original_volume"]) or None,
            "soundtrack": comp and self._layer(
                "soundtrack", comp["soundtrack"], comp["soundtrack_loop"], comp["soundtrack_random_start"],
                comp["soundtrack_volume"]),
            "next": self._state_url() if self.uses_media else None,
        }

    def snapshot(self) -> dict:
        modes = self.modes()
        return {
            "backend": self._backend, "mode": self.mode, "error": self.error,
            "visual": self._comp["visual"] if self.uses_media and self._comp["visual_enabled"] else None,
            "sleep_until": self.sleep_until.isoformat() if self.sleep_until else None, "auto": self.auto,
            "daypart_mode": self.daypart_mode(),
            "modes": [{"id": m["id"], "name": m["name"], "icon": m["icon"], "lights_only": m["lights_only"],
                       "lighting": bool(m["scene"] or m["flicker"]), "flicker": bool(m["flicker"])} for m in modes],
            "compositions": {m["id"]: self._comp if m["id"] == self.mode and self._comp else self.composition(m["id"])
                             for m in modes},
        }
