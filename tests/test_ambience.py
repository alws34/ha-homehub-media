import asyncio
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed, async_mock_service

from custom_components.homehub_media import catalog as cat
from custom_components.homehub_media.activity import ActivityManager
from custom_components.homehub_media.ambience import STATE_PATH, AmbienceManager
from custom_components.homehub_media.store import HomeHubStore


def hub_with(options):
    hub = MagicMock(); hub.options = options; hub.tv = AsyncMock(); hub.activity = AsyncMock()
    hub.pc_ambient = None; hub.library = None; hub.sign_path = lambda p, hours=24: p; return hub


async def test_lights_only_mode_snapshots_and_restores(hass):
    create = async_mock_service(hass, "scene", "create"); on = async_mock_service(hass, "scene", "turn_on")
    hass.states.async_set("scene.movie", "scening", {"entity_id": ["light.a", "light.grp"]})
    hass.states.async_set("light.grp", "on", {"entity_id": ["light.b", "light.c"]})
    opts = {"ambience": {"backend": "tv", "radio": {}}, "modes": [{"id": "movie_time", "name": "Movie",
            "icon": "mdi:movie", "scene": "scene.movie", "flicker": [], "lights_only": True}]}
    store = HomeHubStore(hass); await store.async_load()
    am = AmbienceManager(hass, hub_with(opts), store)
    await am.start("movie_time")
    assert set(create[0].data["snapshot_entities"]) == {"light.a", "light.b", "light.c"}
    assert on[0].data["entity_id"] == "scene.movie" and am.mode == "movie_time"
    am._hub.tv.open_url.assert_not_called()
    await am.stop()
    assert on[-1].data["entity_id"] == "scene.homehub_media_snapshot" and am.mode == "off"


async def test_no_scene_means_no_lighting(hass):
    create = async_mock_service(hass, "scene", "create")
    opts = {"ambience": {"backend": "tv", "radio": {}}, "tv": {"base_url": "http://192.0.2.9:8123"},
            "modes": [{"id": "chill", "name": "Chill", "icon": "mdi:sofa", "scene": None, "flicker": [],
                       "lights_only": False}]}
    store = HomeHubStore(hass); await store.async_load()
    am = AmbienceManager(hass, hub_with(opts), store)
    am._catalog = {"visuals": [{"id": "rain", "kind": "visual", "path": "rain.mp4", "title": "Rain",
                   "source_type": "file", "live": False, "duration": None, "favorite": False, "preview": None}],
                   "soundtracks": []}
    await am.start("chill")
    assert create == [] and am._hub.tv.open_url.await_args.args[0].startswith("http://192.0.2.9:8123/homehub_media_static/ambience.html?s=")
    await am.stop()  # cancel the autoplay-key and health timers


def test_daypart_selection(hass):
    opts = {"dayparts": {"morning": {"start": "06:00", "mode": "reading"}, "afternoon": {"start": "12:00", "mode": None},
                         "evening": {"start": "18:00", "mode": "fireplace"}, "night": {"start": "22:00", "mode": "chill"},
                         "idle_minutes": 10}}
    am = AmbienceManager(hass, hub_with(opts), MagicMock())
    assert am.daypart_mode(datetime(2026, 1, 1, 7, 0, tzinfo=UTC)) == "reading"
    assert am.daypart_mode(datetime(2026, 1, 1, 13, 0, tzinfo=UTC)) is None
    assert am.daypart_mode(datetime(2026, 1, 1, 2, 0, tzinfo=UTC)) == "chill"      # wraps to night
    assert am.daypart_mode(datetime(2026, 1, 1, 19, 30, tzinfo=UTC)) == "fireplace"


# --- helpers for the extra tests -------------------------------------------------------------------------

RAIN = {"id": "rain", "kind": "visual", "path": "rain.mp4", "title": "Rain", "source_type": "file", "live": False,
        "duration": None, "favorite": False, "preview": None}


def chill(backend="tv", scene=None, flicker=(), lights_only=False, radio=None):
    return {"ambience": {"backend": backend, "radio": radio or {}}, "tv": {"base_url": "http://192.0.2.9:8123"},
            "modes": [{"id": "chill", "name": "Chill", "icon": "mdi:sofa", "scene": scene, "flicker": list(flicker),
                       "lights_only": lights_only}]}


async def manager(hass, opts, soundtracks=()):
    store = HomeHubStore(hass); await store.async_load()
    am = AmbienceManager(hass, hub_with(opts), store)
    am._catalog = {"visuals": [dict(RAIN)], "soundtracks": list(soundtracks)}
    return am


async def test_pc_backend_sets_activity_before_mode_then_plays(hass):
    am = await manager(hass, chill("pc"))
    order = []
    am._hub.pc_ambient = AsyncMock()
    am._hub.activity.set.side_effect = lambda a: order.append(("set", a, am.mode, am.uses_media)) or True
    am._hub.pc_ambient.play.side_effect = lambda c: order.append(("play", c["visual"]))
    await am.start("chill")
    assert order == [("set", "pc", "off", False), ("play", "rain")] and am.mode == "chill" and am.uses_media
    await am.update({"original_volume": 55.4})
    assert am._hub.pc_ambient.patch.await_args.args[0] == {"video": {"volume": 55}}
    await am.update({"visual_enabled": False})
    assert am._hub.pc_ambient.patch.await_args.args[0] == {"video": {"enabled": False}}
    await am.update({"soundtrack": "rain"})
    assert am._hub.pc_ambient.patch.await_args.args[0] == {"soundtrack": {
        "enabled": True, "media_id": "rain", "loop": True, "random_start": True, "volume": 100}}
    await am.stop()
    am._hub.pc_ambient.stop.assert_awaited_once()
    assert am.mode == "off" and not am.uses_media


async def test_tv_update_bumps_version_and_page_state(hass):
    am = await manager(hass, chill())
    await am.start("chill")
    sid = am._sid
    v0, s0 = am.page_version, am.page_state(sid)
    assert s0["visual"]["volume"] == 0.2 and s0["stop"] is False
    await am.update({"original_volume": 50})
    s1 = am.page_state(sid)
    assert s1["version"] == v0 + 1 and s1["visual"]["volume"] == 0.5
    with pytest.raises(ServiceValidationError):
        await am.update({"original_volume": 101})
    with pytest.raises(ServiceValidationError):  # merged composition is re-validated in full
        await am.update({"visual": None})
    assert am.page_state(sid)["version"] == v0 + 1
    await am.stop()
    s2 = am.page_state(sid)  # the page gets its stop once, without a next URL; the session then ends
    assert s2["stop"] is True and s2["visual"] is None and s2["version"] > v0 + 1 and s2["next"] is None
    assert am.page_state(sid) is None
    am._hub.tv.send_key.assert_awaited_with("KEY_HOME")


async def test_page_state_signs_urls_and_includes_next(hass):
    radio = cat.radio_items({"radio_x": "https://example.com/live"})
    am = await manager(hass, chill(radio={"radio_x": "https://example.com/live"}), soundtracks=radio)
    am._hub.sign_path = lambda p, hours=24: f"{p}|{hours}h"
    await am.start("chill", {"visual": "rain", "soundtrack": "radio_x", "soundtrack_volume": 40})
    before = am.page_last_seen
    state = am.page_state(am._sid)
    assert am.page_last_seen >= before
    assert state["visual"]["url"] == "/media/local/homehub/ambience/videos/rain.mp4|24h"
    assert state["soundtrack"] == {"url": "https://example.com/live", "stream": True, "loop": False,
                                   "random_start": False, "volume": 0.4}
    assert state["next"] == f"{STATE_PATH}?sid={am._sid}|2h" and state["fade_secs"] is None
    await am.stop()


async def test_sleep_timer_fades_then_stops(hass):
    am = await manager(hass, chill())
    await am.start("chill")
    await am.set_sleep_timer(2)
    am._hub.last_command = 0
    assert am.sleep_until is not None and am.sleep_until > dt_util.utcnow() + timedelta(seconds=100)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert am.page_state(am._sid)["fade_secs"] == 60 and am.mode == "chill"
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=122))
    await hass.async_block_till_done()
    assert am.mode == "off" and am.sleep_until is None
    am._hub.tv.send_key.assert_awaited_with("KEY_HOME")
    assert am._hub.last_command > 0  # the stop counts as activity: idle auto ambience must not restart it


async def test_sleep_timer_needs_a_running_mode(hass):
    am = await manager(hass, chill())
    with pytest.raises(ServiceValidationError):
        await am.set_sleep_timer(15)
    await am.set_sleep_timer(0)
    assert am.sleep_until is None


async def test_tv_health_stops_when_page_is_gone(hass):
    async_mock_service(hass, "scene", "create"); on = async_mock_service(hass, "scene", "turn_on")
    hass.states.async_set("scene.chill", "scening", {"entity_id": ["light.a"]})
    am = await manager(hass, chill(scene="scene.chill"))
    await am.start("chill")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))  # inside the grace period
    await hass.async_block_till_done()
    assert am.mode == "chill"
    am._started = am.page_last_seen = time.monotonic() - 100   # no heartbeat for 100 s
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=62))
    await hass.async_block_till_done()
    assert am.mode == "off" and on[-1].data["entity_id"] == "scene.homehub_media_snapshot"
    assert call("KEY_HOME") not in am._hub.tv.send_key.await_args_list  # page gone: never HOME the user's app


async def test_flicker_runs_and_is_cancelled_on_stop(hass):
    async_mock_service(hass, "scene", "create")
    lights, called = [], asyncio.Event()

    async def light_on(call):
        lights.append(call.data); called.set()
    hass.services.async_register("light", "turn_on", light_on)
    am = await manager(hass, chill(flicker=["light.f"], lights_only=True))
    await am.start("chill")
    await asyncio.wait_for(called.wait(), 1)
    assert lights[0] == {"entity_id": ["light.f"], "brightness_pct": 24, "transition": 3.2}
    task = next(j for j in am._jobs if isinstance(j, asyncio.Task))
    await am.stop()
    assert task.cancelled() and am._jobs == []


def three_modes(backend="tv"):
    opts = chill(backend)
    opts["modes"] = [{"id": m, "name": m, "icon": "mdi:a", "scene": scene, "flicker": [], "lights_only": False}
                     for m, scene in (("a", "scene.a"), ("b", "scene.b"), ("c", None))]
    return opts


async def test_start_failure_restores_lights_and_sets_error(hass):
    async_mock_service(hass, "scene", "create"); on = async_mock_service(hass, "scene", "turn_on")
    hass.states.async_set("scene.a", "x", {"entity_id": ["light.a"]})
    am = await manager(hass, three_modes())
    am._hub.tv.open_url.side_effect = RuntimeError("boom")
    with pytest.raises(HomeAssistantError):
        await am.start("a")
    assert am.mode == "off" and am.error == "Could not start ambience" and not am.uses_media
    assert on[-1].data["entity_id"] == "scene.homehub_media_snapshot" and am._jobs == []


async def test_switch_keeps_snapshot_and_swaps_tv_page_in_place(hass):
    create = async_mock_service(hass, "scene", "create"); on = async_mock_service(hass, "scene", "turn_on")
    hass.states.async_set("scene.a", "x", {"entity_id": ["light.a"]})
    hass.states.async_set("scene.b", "x", {"entity_id": ["light.b"]})
    am = await manager(hass, three_modes())
    am._hub.activity.current = "tv"; am._hub.tv.available = True
    await am.start("a")
    v = am.page_version
    await am.start("b")
    assert len(create) == 1 and [c.data["entity_id"] for c in on] == ["scene.a", "scene.b"]
    assert am.page_version > v and am.page_state(am._sid)["stop"] is False and am.mode == "b"
    am._hub.tv.open_url.assert_awaited_once()            # no reload, no HOME between modes
    am._hub.tv.send_key.assert_not_awaited()
    am._hub.activity.set.assert_not_awaited()
    await am.start("c")  # no lighting: gives the room back
    assert on[-1].data["entity_id"] == "scene.homehub_media_snapshot" and len(on) == 3
    await am.stop()
    assert len(on) == 3  # snapshot restored exactly once


async def test_stale_health_check_cannot_stop_a_new_run(hass):
    am = await manager(hass, three_modes("pc"))
    am._hub.pc_ambient = pc = AsyncMock()
    gate = asyncio.Event()

    async def slow_status():
        await gate.wait(); return {"state": "idle"}
    pc.status.side_effect = slow_status
    await am.start("c")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await asyncio.sleep(0.05)                     # health check is now awaiting status()
    await am.start("c", {"visual": "rain", "original_volume": 50})
    gate.set(); await hass.async_block_till_done()
    assert am.mode == "c" and am.uses_media
    await am.stop()


async def test_pc_health_idle_stops(hass):
    am = await manager(hass, chill("pc"))
    am._hub.pc_ambient = pc = AsyncMock()
    pc.status.return_value = {"state": "idle"}
    await am.start("chill")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()
    assert am.mode == "off"
    pc.stop.assert_awaited_once()


async def test_pc_sleep_fades_in_six_patches_then_stops(hass):
    am = await manager(hass, chill("pc"))
    am._hub.pc_ambient = pc = AsyncMock()
    pc.status.return_value = {"state": "playing"}
    await am.start("chill")
    await am.set_sleep_timer(1)  # fade starts right away (1 min - 60 s)
    now = dt_util.utcnow()
    for i in range(8):  # i=0 fires the fade timer; each later step wakes one 10 s fade sleep
        async_fire_time_changed(hass, now + timedelta(seconds=11 * i))
        await hass.async_block_till_done()
        await asyncio.sleep(0)
    assert [c.args[0] for c in pc.patch.await_args_list] == [{"video": {"volume": v}} for v in (16, 13, 10, 6, 3, 0)]
    assert am.mode == "off"
    pc.stop.assert_awaited_once()


async def test_pc_refresh_rescans_and_maps_catalog(hass):
    am = await manager(hass, chill("pc"))
    am._hub.pc_ambient = pc = AsyncMock()
    pc.catalog.return_value = {"visuals": [{"id": "fire", "kind": "video", "title": "Fire", "source_type": "file"},
                                           {"id": "../evil", "title": "x"}], "soundtracks": []}
    await am.refresh_catalog()
    pc.refresh.assert_awaited_once()
    assert am.catalog()["visuals"] == [{"id": "fire", "kind": "visual", "title": "Fire", "source_type": "file",
                                        "live": False, "duration": None, "preview": None, "favorite": False}]


async def test_refresh_catalog_tv_scans_and_strips_server_keys(hass, tmp_path):
    hass.config.media_dirs = {"local": str(tmp_path)}
    vids = tmp_path / "homehub/ambience/videos"; vids.mkdir(parents=True)
    (vids / "Rain.mp4").write_bytes(b"x"); (vids / "Rain.webp").write_bytes(b"x")
    am = AmbienceManager(hass, hub_with(chill(radio={"radio_x": "https://example.com/live"})), HomeHubStore(hass))
    await am.async_load()
    await am.async_load_catalog()
    await am.set_favorite("visual", "rain", True)
    c = am.catalog()
    assert c["visuals"] == [{"id": "rain", "kind": "visual", "title": "Rain", "source_type": "file", "live": False,
                             "duration": None, "favorite": True,
                             "preview": "/media/local/homehub/ambience/videos/Rain.webp"}]
    assert [s["id"] for s in c["soundtracks"]] == ["radio_x"] and "url" not in c["soundtracks"][0]
    assert (tmp_path / "homehub/ambience/soundtracks").is_dir()
    assert am.composition("chill")["visual"] == "rain"
    await am.async_shutdown()


async def test_idle_auto_start(hass, tmp_path):
    hass.config.media_dirs = {"local": str(tmp_path)}
    opts = chill(lights_only=True)
    opts["dayparts"] = {p: {"start": s, "mode": "chill"} for p, s in
                        (("morning", "06:00"), ("afternoon", "12:00"), ("evening", "18:00"), ("night", "22:00"))}
    opts["dayparts"]["idle_minutes"] = 10
    am = AmbienceManager(hass, hub_with(opts), HomeHubStore(hass))
    am._hub.activity.current = "tv"; am._hub.activity.is_on = lambda: True
    am._hub.last_command = time.monotonic() - 3600
    await am.async_load()
    await am.async_load_catalog()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert am.mode == "off"  # auto is off
    am.auto = True
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=122))
    await hass.async_block_till_done()
    assert am.mode == "chill"
    await am.async_shutdown()
    assert am.mode == "off"


async def test_stale_page_session_is_rejected(hass):
    am = await manager(hass, chill())
    await am.start("chill")
    old = am._sid
    am.page_last_seen = 0.0
    assert am.page_state(None) is None and am.page_state("guess") is None
    assert am.page_last_seen == 0.0  # no heartbeat from a foreign sid
    am._sid = None
    await am._play(am._comp)  # a fresh page open starts a new session
    assert am._sid != old and am.page_state(old) is None and am.page_last_seen > 0.0
    await am.stop()


async def test_superseded_activity_opens_no_page(hass):
    am = await manager(hass, chill())
    hub = am._hub
    hub.ambience, hub.activity = am, ActivityManager(hass, hub)  # activity "off": start needs the TV first
    hub.tv.wake.side_effect = asyncio.Event().wait  # the TV takes forever to wake
    starting = asyncio.create_task(am.start("chill"))
    while not hub.tv.wake.await_count:
        await asyncio.sleep(0)
    assert await hub.activity.set("off") is True  # the user switches everything off meanwhile
    await asyncio.wait_for(starting, 1)
    hub.tv.open_url.assert_not_awaited()
    assert am.mode == "off" and hub.activity.current == "off"


# --- YouTube visuals and the random visual pool ----------------------------------------------------------

YT = {"id": "star_night", "kind": "visual", "title": "Star night", "source_type": "youtube", "live": False,
      "duration": None, "youtube_id": "B5unCXpegAw", "preview": "/api/homehub_media/art/youtube/B5unCXpegAw",
      "favorite": False}
FIRE = {**YT, "id": "fire", "title": "Fire", "youtube_id": "fireFIREfir"}


async def yt_manager(hass, backend="tv", scene=None):
    am = await manager(hass, chill(backend, scene=scene))
    am._catalog["visuals"] += [dict(YT), dict(FIRE)]
    return am


async def test_youtube_start_plays_in_the_tv_app_and_stop_restores_lights(hass):
    async_mock_service(hass, "scene", "create"); on = async_mock_service(hass, "scene", "turn_on")
    hass.states.async_set("scene.chill", "x", {"entity_id": ["light.a"]})
    am = await yt_manager(hass, scene="scene.chill")
    order = []
    am._hub.activity.set.side_effect = lambda a: order.append(("set", a, am.mode)) or True
    am._hub.tv.play_youtube.side_effect = lambda v: order.append(("play", v, am.mode))
    await am.start("chill", {"visual": "star_night", "soundtrack": "rain"})  # the soundtrack is not used
    assert order == [("set", "tv", "off"), ("play", "B5unCXpegAw", "chill")]  # the TV first, then the mode
    assert [c.data["entity_id"] for c in on] == ["scene.chill"] and am.page_state(am._sid) is None
    am._hub.tv.open_url.assert_not_awaited()
    await am.stop()
    am._hub.tv.stop_youtube.assert_awaited_once()
    am._hub.tv.send_key.assert_not_awaited()  # YouTube is closed over DIAL, no HOME key
    assert on[-1].data["entity_id"] == "scene.homehub_media_snapshot" and am.mode == "off"


async def test_youtube_on_the_pc_backend_plays_on_the_tv(hass):
    am = await yt_manager(hass, "pc")
    am._hub.pc_ambient = pc = AsyncMock()
    await am.start("chill", {"visual": "star_night"})
    am._hub.activity.set.assert_awaited_once_with("tv")
    pc.play.assert_not_awaited()
    await am.update({"original_volume": 50, "soundtrack": "rain"})  # nothing to change on YouTube
    await am.update({"visual": "fire"})  # another video: warm, YouTube stays up
    assert [c.args[0] for c in am._hub.tv.play_youtube.await_args_list] == ["B5unCXpegAw", "fireFIREfir"]
    am._hub.tv.stop_youtube.assert_not_awaited()
    pc.patch.assert_not_awaited()
    await am.stop()
    am._hub.tv.stop_youtube.assert_awaited_once()
    pc.stop.assert_not_awaited()


async def test_youtube_health_stops_when_the_app_was_closed(hass):
    am = await yt_manager(hass)
    tv = am._hub.tv
    tv.youtube_state.return_value = "stopped"
    await am.start("chill", {"visual": "star_night"})
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))  # grace period: not asked yet
    await hass.async_block_till_done()
    assert am.mode == "chill" and tv.youtube_state.await_count == 0
    am._started = time.monotonic() - 100
    tv.youtube_state.return_value = "unknown"  # TV unreachable: keep the mode
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=62))
    await hass.async_block_till_done()
    assert am.mode == "chill"
    tv.youtube_state.return_value = "stopped"
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=93))
    await hass.async_block_till_done()
    assert am.mode == "off"
    tv.stop_youtube.assert_awaited_once()


async def test_youtube_mode_switch_is_warm_and_file_switch_starts_over(hass):
    am = await yt_manager(hass)
    tv = am._hub.tv
    am._hub.activity.current = "tv"; tv.available = True
    await am.start("chill", {"visual": "star_night"})
    await am.start("chill", {"visual": "fire"})  # YouTube to YouTube: no stop, no profile picker again
    tv.stop_youtube.assert_not_awaited()
    await am.update({"visual": "rain"})  # a file plays in another player: the mode starts over
    tv.stop_youtube.assert_awaited_once()
    tv.open_url.assert_awaited_once()
    assert am.mode == "chill" and am._comp["visual"] == "rain" and am._yt is None
    await am.update({"visual": "star_night"})  # and back: the page goes HOME, YouTube plays
    assert call("KEY_HOME") in tv.send_key.await_args_list and am._yt == "B5unCXpegAw"
    assert [c.args[0] for c in tv.play_youtube.await_args_list] == ["B5unCXpegAw", "fireFIREfir", "B5unCXpegAw"]
    await am.stop()


async def test_visuals_pool_picks_a_random_visual_each_start(hass, monkeypatch):
    am = await yt_manager(hass, "pc")
    am._hub.pc_ambient = pc = AsyncMock()
    pools = []
    monkeypatch.setattr("custom_components.homehub_media.ambience.random.choice",
                        lambda pool: pools.append(list(pool)) or pool[-1])
    await am.save_mode("chill", {"visual": "rain", "visuals": ["rain", "ghost", "star_night"]})
    await am.start("chill")
    assert pools == [["rain", "star_night"]]  # ids no longer in the catalog are skipped
    am._hub.activity.set.assert_awaited_with("tv")  # star_night is a YouTube video
    am._hub.tv.play_youtube.assert_awaited_once_with("B5unCXpegAw")
    assert am.snapshot()["compositions"]["chill"]["visuals"] == ["rain", "ghost", "star_night"]
    monkeypatch.setattr("custom_components.homehub_media.ambience.random.choice", lambda pool: pool[0])
    await am.start("chill")  # this time a file on the PC
    body = pc.play.await_args.args[0]
    assert body["visual"] == "rain" and "visuals" not in body  # the PC gets one resolved visual
    await am.update({"visuals": ["rain"]})  # changes the pool for the next start, nothing restarts
    pc.patch.assert_not_awaited()  # an empty PATCH is an error on the PC
    assert am._comp["visual"] == "rain" and am._comp["visuals"] == ["rain"]
    await am.stop()


async def test_refresh_catalog_adds_youtube_for_both_backends(hass, tmp_path):
    hass.config.media_dirs = {"local": str(tmp_path)}
    vids = tmp_path / "homehub/ambience/videos"; vids.mkdir(parents=True)
    (vids / "Star Night.mp4").write_bytes(b"x")
    opts = chill()
    opts["ambience"]["youtube"] = {"Star night": "https://youtu.be/B5unCXpegAw", "Fire": "fireFIREfir"}
    am = AmbienceManager(hass, hub_with(opts), HomeHubStore(hass))
    await am.async_load()
    await am.refresh_catalog()
    visuals = am.catalog()["visuals"]
    assert [(v["id"], v["source_type"]) for v in visuals] == [
        ("star_night", "file"), ("star_night_2", "youtube"), ("fire", "youtube")]
    assert visuals[1]["preview"] == "/api/homehub_media/art/youtube/B5unCXpegAw"
    assert all("youtube_id" not in v for v in visuals)
    opts["ambience"]["backend"] = "pc"
    am._hub.pc_ambient = pc = AsyncMock()
    pc.catalog.return_value = {"visuals": [], "soundtracks": []}
    await am.refresh_catalog()
    assert [v["id"] for v in am.catalog()["visuals"]] == ["star_night", "fire"]
