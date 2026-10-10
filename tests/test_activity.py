import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.homehub_media import activity
from custom_components.homehub_media.activity import ActivityManager

VOL = {"normal": 19, "quiet": 10, "block_entity": None}


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(activity, "VERIFY_SECS", 0.01)
    monkeypatch.setattr(activity, "POWER_WAIT_SECS", 0.01)
    monkeypatch.setattr(activity, "SETTLE_SECS", 0.01)


def make_hub(options):
    hub = MagicMock(); hub.options = options; hub.tv = AsyncMock(); hub.tv.available = True
    hub.send_tv_key = hub.tv.send_key  # routing is the hub's (test_hub)
    hub.ambience = AsyncMock(); hub.ambience.uses_media = True
    return hub


async def test_blocked_by_entity(hass):
    hass.states.async_set("input_boolean.night", "on")
    hub = make_hub({"volume": {"normal": 19, "quiet": 10, "block_entity": "input_boolean.night"}, "tv": {}})
    am = ActivityManager(hass, hub)
    with pytest.raises(ServiceValidationError):
        await am.set("tv")
    assert am.phase == "blocked"
    await am.set("off")  # off always allowed
    assert am.current == "off"


async def test_quiet_volume_applied(hass):
    calls = async_mock_service(hass, "media_player", "volume_set")
    hass.states.async_set("media_player.tv", "on")
    hub = make_hub({"volume": {"normal": 19, "quiet": 10, "block_entity": None}, "tv": {"entity": "media_player.tv"}})
    am = ActivityManager(hass, hub)
    await am.set_quiet(True)
    assert calls[-1].data["volume_level"] == 0.10


async def test_box_source_retry(hass):
    sel = async_mock_service(hass, "media_player", "select_source")
    async_mock_service(hass, "media_player", "turn_on"); async_mock_service(hass, "remote", "turn_on")
    hass.states.async_set("media_player.tv", "on", {"source": "TV"})
    hub = make_hub({"volume": {"normal": 19, "quiet": 10, "block_entity": None}, "tv": {"entity": "media_player.tv"},
                    "box": {"remote": "remote.box", "media_player": None, "input": {"method": "source", "value": "HDMI1"}}})
    am = ActivityManager(hass, hub)
    await am.set("box")
    assert len(sel) == 2 and am.phase == "needs_reset"   # warm: 2 attempts, source never confirmed


def box_setup(hass, tv_state, source):
    sel = async_mock_service(hass, "media_player", "select_source")
    async_mock_service(hass, "remote", "turn_on")
    hass.states.async_set("media_player.tv", tv_state, {"source": source})
    hub = make_hub({"volume": VOL, "tv": {"entity": "media_player.tv"},
                    "box": {"remote": "remote.box", "media_player": None, "input": {"method": "source", "value": "HDMI1"}}})
    return sel, ActivityManager(hass, hub)


async def test_box_cold_entity_wins_over_stale_available(hass):
    sel, am = box_setup(hass, "off", None)  # hub.tv.available is a stale True; the entity says off -> cold
    await am.set("box")
    assert len(sel) == 3 and am.phase == "needs_reset"


async def test_box_force_repeats_even_when_confirmed(hass):
    sel, am = box_setup(hass, "on", "HDMI1")
    await am.set("box", force=True)
    assert len(sel) == 2 and am.phase == "ready"


async def test_tv_sends_home_and_normal_volume(hass):
    async_mock_service(hass, "media_player", "turn_on")
    vol = async_mock_service(hass, "media_player", "volume_set")
    hass.states.async_set("media_player.tv", "on")
    hub = make_hub({"volume": VOL, "tv": {"entity": "media_player.tv"}})
    am = ActivityManager(hass, hub)
    await am.set("tv")
    hub.tv.send_key.assert_awaited_with("KEY_HOME")
    assert vol[-1].data["volume_level"] == 0.19
    assert (am.current, am.phase) == ("tv", "ready")
    hub.async_notify.assert_called()


async def test_newer_request_cancels_running_one(hass):
    remote_on = async_mock_service(hass, "remote", "turn_on")
    stops = []

    async def stop():
        stops.append(1)
        if len(stops) == 1:
            await asyncio.Event().wait()  # first request hangs until cancelled

    hub = make_hub({"volume": VOL, "tv": {},
                    "box": {"remote": "remote.box", "media_player": None,
                            "input": {"method": "key", "value": "KEY_HDMI1"}}})
    hub.ambience.stop.side_effect = stop
    am = ActivityManager(hass, hub)
    first = asyncio.create_task(am.set("box"))
    while not stops:
        await asyncio.sleep(0)
    await asyncio.wait_for(am.set("tv"), 1)
    await first  # superseded request returns quietly
    assert (am.current, am.phase) == ("tv", "ready") and remote_on == []


async def test_off_stops_lights_only_ambience(hass):
    hub = make_hub({"volume": VOL, "tv": {}})
    hub.ambience.mode, hub.ambience.uses_media = "movie_time", False
    am = ActivityManager(hass, hub)
    await am.set("tv")
    hub.ambience.stop.assert_not_awaited()  # lights-only modes coexist with media
    await am.set("off")
    hub.ambience.stop.assert_awaited_once()
    hub.tv.send_key.assert_awaited_with("KEY_POWER")


async def test_pc_key_input(hass):
    hub = make_hub({"volume": VOL, "tv": {},
                    "pc": {"host": "192.0.2.5", "input": {"method": "key", "value": "KEY_HDMI3"}, "clients": []}})
    am = ActivityManager(hass, hub)
    await am.set("pc")
    hub.tv.send_key.assert_awaited_once_with("KEY_HDMI3")
    assert (am.current, am.phase) == ("pc", "ready")


async def test_unconfigured_target_rejected(hass):
    am = ActivityManager(hass, make_hub({"volume": VOL, "tv": {}, "box": None,
                                         "pc": {"input": {"method": "source", "value": "HDMI3"}}}))
    with pytest.raises(ServiceValidationError):
        await am.set("box")
    with pytest.raises(ServiceValidationError):  # source switching needs a TV entity
        await am.set("pc")


async def test_unexpected_error_leaves_needs_reset(hass):
    hub = make_hub({"volume": VOL, "tv": {}})
    hub.tv.send_key.side_effect = RuntimeError("boom")
    am = ActivityManager(hass, hub)
    with pytest.raises(RuntimeError):
        await am.set("tv")
    assert am.phase == "needs_reset"


async def test_shutdown_cancels_running_request(hass):
    hub = make_hub({"volume": VOL, "tv": {}})
    hub.ambience.stop.side_effect = asyncio.Event().wait
    am = ActivityManager(hass, hub)
    pending = asyncio.create_task(am.set("tv"))
    while not hub.ambience.stop.await_count:
        await asyncio.sleep(0)
    await am.async_shutdown()
    await asyncio.wait_for(pending, 1)  # superseded caller returns quietly
    assert am.current == "off" and not hub.tv.send_key.await_count


async def test_a_hung_service_call_is_a_failed_step(hass, monkeypatch, caplog):
    monkeypatch.setattr(activity, "SERVICE_SECS", 0.05)
    async def hang(call): await asyncio.Event().wait()
    hass.services.async_register("media_player", "volume_set", hang)
    hass.states.async_set("media_player.tv", "on")
    am = ActivityManager(hass, make_hub({"volume": VOL, "tv": {"entity": "media_player.tv"}}))
    async with asyncio.timeout(5):
        await am.set_quiet(True)
    assert "media_player.volume_set did not finish" in caplog.text
