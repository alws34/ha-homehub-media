from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.homehub_media import hub as hub_mod
from custom_components.homehub_media.const import DOMAIN
from custom_components.homehub_media.hub import Hub
from custom_components.homehub_media.tv import CannotConnect

DATA = {"host": "192.0.2.1", "token": "t", "mac": None}
OPTS = {
    "box": {"remote": "remote.box", "media_player": "media_player.box", "input": {"method": "key", "value": "KEY_HDMI2"}},
    "pc": {"host": "192.0.2.5", "ambient_port": 8097, "ambient_token": "a", "coord_port": 8098, "coord_token": "c",
           "input": {"method": "key", "value": "KEY_HDMI3"}, "clients": ["jellyfin"]},
    "keyboard": {"host": "192.0.2.6", "key": "k"},
    "servers": {"jellyfin": {"url": "http://192.0.2.7:8096", "api_key": "j", "user_id": "u", "player": None}},
}


def make_hub(hass, options=OPTS):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, options=options)
    entry.add_to_hass(hass)
    with patch.object(hub_mod, "SamsungTV", autospec=True):
        hub = Hub(hass, entry)
    hub.tv.available = hub.tv.keyboard_active = hub.tv.pointer_active = False
    hub.pc_coord, hub.keyboard = AsyncMock(), AsyncMock()
    hub.activity = MagicMock(current="tv", set=AsyncMock())
    return hub


async def test_remote_routing(hass):
    send = async_mock_service(hass, "remote", "send_command")
    hub = make_hub(hass)
    hub.activity.current = "box"
    await hub.remote("up")  # no target: current activity
    assert send[-1].data == {"entity_id": "remote.box", "command": "DPAD_UP"}
    await hub.remote("rewind")
    assert send[-1].data["command"] == "MEDIA_REWIND"
    await hub.remote("volume_up")  # volume always goes to the TV
    hub.tv.send_key.assert_awaited_with("KEY_VOLUP")
    await hub.remote("select", "pc")
    hub.pc_coord.remote.assert_awaited_with("select")
    await hub.remote("pause", "pc")  # the PC only has a toggle
    hub.pc_coord.remote.assert_awaited_with("play_pause")
    await hub.remote("power", "pc")  # the PC has no power key: the TV's
    hub.tv.send_key.assert_awaited_with("KEY_POWER")
    for command in ("guide", "fast_forward"):
        with pytest.raises(ServiceValidationError):
            await hub.remote(command, "pc")
    hub.activity.current = "off"  # off: the TV
    await hub.remote("play_pause")
    await hub.remote("play_pause")
    await hub.remote("play")
    await hub.remote("play_pause")
    assert [c.args[0] for c in hub.tv.send_key.await_args_list[-4:]] == ["KEY_PAUSE", "KEY_PLAY", "KEY_PLAY", "KEY_PAUSE"]
    hub.options["box"] = None
    with pytest.raises(ServiceValidationError):
        await hub.remote("up", "box")


async def test_send_text_routing(hass):
    hub = make_hub(hass)
    hub.activity.current = "pc"
    await hub.send_text("hi")
    hub.pc_coord.text.assert_awaited_with("hi")
    await hub.keyboard_key("backspace")
    hub.pc_coord.remote.assert_awaited_with("backspace")
    hub.activity.current = "tv"
    await hub.send_text("hi")  # IME closed and a bridge exists: the bridge
    hub.keyboard.type.assert_awaited_with("hi")
    hub.tv.keyboard_active = True
    await hub.send_text("ho")
    hub.tv.send_text.assert_awaited_with("ho")
    await hub.keyboard_key("enter")
    hub.tv.send_key.assert_awaited_with("KEY_ENTER")
    with pytest.raises(ServiceValidationError, match="keyboard bridge"):
        await hub.keyboard_key("clear")
    await hub.send_text("x", "keyboard")
    hub.keyboard.type.assert_awaited_with("x")
    await hub.keyboard_key("clear", "keyboard")
    hub.keyboard.key.assert_awaited_with("clear")
    hub.keyboard, hub.tv.keyboard_active = None, False
    await hub.send_text("yo")  # no bridge: the TV IME
    hub.tv.send_text.assert_awaited_with("yo")
    with pytest.raises(ServiceValidationError):
        await hub.send_text("yo", "keyboard")


async def test_launch_app_routes(hass):
    hub = make_hub(hass)
    hub.activity.current = "off"
    hub.activity.set.side_effect = lambda activity: setattr(hub.activity, "current", activity)
    await hub.launch_app("jellyfin")  # not installed, server configured: its web UI in the TV browser
    hub.activity.set.assert_awaited_with("tv")
    hub.tv.open_url.assert_awaited_with("http://192.0.2.7:8096")
    assert hub.snapshot()["tv"]["app"] == "jellyfin"
    hub.apps = {"youtube": "111299001912"}
    await hub.launch_app("youtube")
    hub.tv.launch.assert_awaited_with("111299001912")
    await hub.launch_app("3201907018807")  # raw Tizen id of a catalog app: reported by its key
    hub.tv.launch.assert_awaited_with("3201907018807")
    assert hub.snapshot()["tv"]["app"] == "netflix"
    await hub.launch_app("org.example.app")  # unknown raw id: reported as is
    assert hub.snapshot()["tv"]["app"] == "org.example.app"
    with pytest.raises(ServiceValidationError):
        await hub.launch_app("max")  # catalog app, not installed, no server
    apps = {a["key"]: a["installed"] for a in hub.snapshot()["apps"]}
    assert apps["youtube"] and apps["jellyfin"] and not apps["max"]
    hub.activity.current = "pc"
    hub.async_notify()  # left the TV: no TV app any more
    assert hub.current_app is None


async def test_launch_app_stops_media_ambience_on_tv(hass):
    hub = make_hub(hass)
    hub.apps = {"youtube": "111299001912"}
    hub.ambience = MagicMock(uses_media=True, mode="chill", stop=AsyncMock())
    await hub.launch_app("youtube")  # already on tv: activity.set is skipped, so the hub stops the page itself
    hub.ambience.stop.assert_awaited_once()
    hub.activity.set.assert_not_awaited()


async def test_app_entity_state_maps_to_catalog_key(hass):
    hub = make_hub(hass, {"tv": {"app_entity": "sensor.tv_app"}})
    hub.ambience = MagicMock(snapshot=dict)
    for state, key in (("Netflix", "netflix"), ("111299001912", "youtube"), ("Some Game", "Some Game")):
        hass.states.async_set("sensor.tv_app", state)
        assert hub.snapshot()["tv"]["app"] == key


async def test_play_library_item_retries_until_the_app_is_ready(hass):
    hub = make_hub(hass)
    srv = MagicMock(play=AsyncMock(side_effect=[False, False, True]))
    hub.library = MagicMock(server=lambda name: srv if name == "jellyfin" else None)
    with patch.object(hub_mod.asyncio, "sleep", AsyncMock()) as sleep:
        await hub.play_library_item("jellyfin", "abc")
    hub.tv.open_url.assert_awaited_with("http://192.0.2.7:8096")
    assert srv.play.await_count == 3 and sleep.await_count == 2
    with pytest.raises(ServiceValidationError, match="Plex is not configured"):
        await hub.play_library_item("plex", "abc")
    srv.play = AsyncMock(return_value=False)
    with (patch.object(hub_mod.asyncio, "sleep", AsyncMock()), patch.object(hub_mod, "PLAY_TIMEOUT", 0),
          pytest.raises(HomeAssistantError, match="Jellyfin did not start playing")):
        await hub.play_library_item("jellyfin", "abc")


async def test_pc_open_stops_media_ambience_first(hass):
    hub = make_hub(hass)
    hub.ambience = MagicMock(uses_media=True, stop=AsyncMock())
    await hub.pc_open("jellyfin")
    hub.ambience.stop.assert_awaited_once()
    hub.activity.set.assert_awaited_with("pc")
    hub.pc_coord.open.assert_awaited_with("jellyfin")
    assert hub.pc_active == "jellyfin"
    await hub.pc_close()
    assert hub.pc_active is None


async def test_options_ids_override_discovered(hass):
    hub = make_hub(hass, {"apps": {"discovered": {"netflix": "a", "youtube": "b"}, "ids": {"netflix": "c"}}})
    assert hub.apps == {"netflix": "c", "youtube": "b"}


async def test_start_discovers_and_persists_apps(hass, setup_entry):
    assert setup_entry.options["apps"]["discovered"] == {"youtube": "111299001912"}
    assert setup_entry.runtime_data.apps == {"youtube": "111299001912"}
    assert setup_entry.options["ambience"] == {"backend": "tv", "radio": {}}


async def test_empty_discovery_is_not_persisted(hass):
    hub = make_hub(hass, {})
    hub.tv.discover_apps.return_value = {}
    with pytest.raises(HomeAssistantError):
        await hub.rescan_apps()
    assert "apps" not in hub.entry.options


@pytest.mark.parametrize("unreachable", [True, False])  # False: standby, REST answers but the TV is off
async def test_tv_off_does_not_fail_setup_or_discover(hass, unreachable):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA)
    entry.add_to_hass(hass)
    with patch.object(hub_mod, "SamsungTV", autospec=True) as tv_cls:
        tv = tv_cls.return_value
        tv.token, tv.available, tv.keyboard_active, tv.pointer_active = "t", False, False, False
        if unreachable:
            tv.device_info.side_effect = CannotConnect("off")
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        assert entry.state is ConfigEntryState.LOADED and "apps" not in entry.options
        tv.discover_apps.assert_not_awaited()
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_token_rotation_persists_without_reload(hass, setup_entry):
    hub = setup_entry.runtime_data
    hub.tv.token = "rotated"
    hub_mod.SamsungTV.call_args.kwargs["on_change"]()
    await hass.async_block_till_done()
    assert setup_entry.data["token"] == "rotated"
    assert setup_entry.state is ConfigEntryState.LOADED and setup_entry.runtime_data is hub


async def test_unload_stops_everything(hass, setup_entry):
    hub = setup_entry.runtime_data
    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    hub.tv.close.assert_awaited_once()
    assert hub.ambience._unsub_idle is None and hub._unsubs == []
