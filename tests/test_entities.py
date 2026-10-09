from datetime import UTC, datetime
from unittest.mock import Mock, patch

from homeassistant.core import State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from pytest_homeassistant_custom_component.common import mock_restore_cache

from custom_components.homehub_media.const import DOMAIN, SIGNAL_UPDATE


def _eid(hass, entry, platform, key):
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{entry.entry_id}_{key}")


async def _call(hass, domain, service, entity_id, **data):
    await hass.services.async_call(domain, service, {"entity_id": entity_id, **data}, blocking=True)


async def test_selects_call_managers(hass, setup_entry):
    hub = setup_entry.runtime_data
    mode, activity = _eid(hass, setup_entry, "select", "ambience_mode"), _eid(hass, setup_entry, "select", "activity")
    state = hass.states.get(mode)
    assert state.state == "off" and state.attributes["options"][:3] == ["off", "fireplace", "dinner"]
    assert hass.states.get(activity).attributes["options"] == ["off", "tv"]  # no box/pc configured
    with patch.object(hub.ambience, "start") as start, patch.object(hub.ambience, "stop") as stop, \
            patch.object(hub.activity, "set") as set_activity:
        await _call(hass, "select", "select_option", mode, option="dinner")
        hub.ambience.auto, hub.last_command = True, 0.0  # idle for ages, auto ambience on
        await _call(hass, "select", "select_option", mode, option="off")
        assert hub.last_command > 0  # the user's "off" holds off the idle auto-start
        await _call(hass, "select", "select_option", activity, option="tv")
    start.assert_awaited_once_with("dinner")
    stop.assert_awaited_once_with()
    set_activity.assert_awaited_once_with("tv")


async def test_switches(hass, setup_entry):
    hub = setup_entry.runtime_data
    quiet, auto = _eid(hass, setup_entry, "switch", "quiet_mode"), _eid(hass, setup_entry, "switch", "auto_ambience")
    hub.last_command = 0.0
    with patch.object(hub.activity, "set_quiet") as set_quiet:
        await _call(hass, "switch", "turn_on", quiet)
    set_quiet.assert_awaited_once_with(True)
    assert hub.last_command > 0
    hub.activity.quiet = True
    async_dispatcher_send(hass, SIGNAL_UPDATE)
    await hass.async_block_till_done()
    assert hass.states.get(quiet).state == "on"

    assert hass.states.get(auto).state == "off"  # default
    await _call(hass, "switch", "turn_on", auto)
    assert hub.ambience.auto is True and hass.states.get(auto).state == "on"


async def test_auto_ambience_restored(hass, setup_entry):
    auto = _eid(hass, setup_entry, "switch", "auto_ambience")
    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    mock_restore_cache(hass, [State(auto, "on")])
    assert await hass.config_entries.async_setup(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert setup_entry.runtime_data.ambience.auto is True and hass.states.get(auto).state == "on"


async def test_sensors(hass, setup_entry):
    hub = setup_entry.runtime_data
    timer, playing = (_eid(hass, setup_entry, "sensor", k) for k in ("ambience_sleep_timer", "now_playing"))
    assert hass.states.get(timer).attributes["device_class"] == "timestamp"
    assert hass.states.get(playing).state == "unknown"

    hub.ambience.sleep_until = datetime(2026, 10, 9, 22, 30, tzinfo=UTC)
    hub.library = Mock(now_playing=[{"server": "plex", "player": "TV", "title": "x" * 300, "subtitle": "S1E2",
                                     "state": "playing", "position": 12.0, "duration": 1800.0,
                                     "image": "/api/homehub_media/art/secret"}])
    async_dispatcher_send(hass, SIGNAL_UPDATE)
    await hass.async_block_till_done()
    assert hass.states.get(timer).state == "2026-10-09T22:30:00+00:00"
    state = hass.states.get(playing)
    assert state.state == "x" * 255
    assert state.attributes["subtitle"] == "S1E2" and state.attributes["position"] == 12.0
    assert "image" not in state.attributes and "entity_picture" not in state.attributes


def test_now_playing_position_not_recorded():
    from custom_components.homehub_media.sensor import NowPlayingSensor

    assert {"position", "duration"} <= NowPlayingSensor._Entity__combined_unrecorded_attributes
