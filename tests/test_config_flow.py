"""Config flow (TV pairing) and options menu tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homehub_media import config_flow
from custom_components.homehub_media.const import DEFAULT_MODES, DOMAIN
from custom_components.homehub_media.tv import CannotConnect, PairingDenied, SamsungTV

DATA = {"host": "192.0.2.1", "mac": None, "token": "t", "port": 8002}
INFO = {"modelName": "QE55Q60B", "wifiMac": "AA:BB:CC:DD:EE:FF"}
PLEX = "http://plex.local:32400"
JF = "http://jf.local:8096"
INPUT_KEY = {"method": "key", "value": "KEY_HDMI2"}


@pytest.fixture(autouse=True)
def no_setup():
    with (patch("custom_components.homehub_media.async_setup_entry", return_value=True),
          patch("custom_components.homehub_media.async_unload_entry", return_value=True)):
        yield


@pytest.fixture
def tv():
    with (patch.object(SamsungTV, "device_info", return_value=INFO) as info,
          patch.object(SamsungTV, "pair", return_value="tok") as pair,
          patch.object(SamsungTV, "close") as close):
        yield info, pair, close


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA,
                            options={"ambience": {"backend": "tv", "radio": {}}, "modes": DEFAULT_MODES})
    entry.add_to_hass(hass)
    return entry


def _schema(result, key):
    """(suggested value, selector) of a form field."""
    for marker, validator in result["data_schema"].schema.items():
        if marker == key:
            return (marker.description or {}).get("suggested_value"), validator
    raise KeyError(key)


async def _user(hass):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})


async def _open(hass, entry, *steps):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    for step in steps:
        assert result["type"] is FlowResultType.MENU
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": step})
    return result


async def _submit(hass, result, data):
    return await hass.config_entries.options.async_configure(result["flow_id"], data)


async def _save(hass, result):
    assert result["type"] is FlowResultType.MENU, result.get("errors")
    result = await _submit(hass, result, {"next_step_id": "done"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


# --- config flow ------------------------------------------------------------------------------------------

async def test_user_flow_pairs_and_creates_entry(hass, tv):
    result = await _user(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": "192.0.2.1"})
    assert result["step_id"] == "pair"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["title"] == "QE55Q60B"
    assert result["data"] == {"host": "192.0.2.1", "mac": "aa:bb:cc:dd:ee:ff", "token": "tok", "port": 8002}
    assert result["options"]["modes"] == DEFAULT_MODES
    assert result["options"]["ambience"] == {"backend": "tv", "radio": {}}
    assert set(result["options"]) == {"ambience", "modes", "volume", "dayparts"}
    assert tv[2].await_count == 2  # closed after the info probe and after pairing


async def test_pairing_denied_then_retry(hass, tv):
    tv[1].side_effect = [PairingDenied("no"), "tok"]
    result = await _user(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": "192.0.2.1"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "pair" and result["errors"] == {"base": "pairing_denied"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_cannot_connect_and_bad_input(hass, tv):
    tv[0].side_effect = CannotConnect("off")
    result = await _user(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": "192.0.2.1"})
    assert result["step_id"] == "user" and result["errors"] == {"base": "cannot_connect"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": "fe80::1"})
    assert result["errors"] == {"host": "invalid_host"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": "tv.lan", "mac": "nope"})
    assert result["errors"] == {"mac": "invalid_mac"}


async def test_prefill_from_samsungtv_entry(hass, tv):
    MockConfigEntry(domain="samsungtv", data={"host": "192.0.2.9", "mac": "11:22:33:44:55:66"}).add_to_hass(hass)
    result = await _user(hass)
    assert _schema(result, "host")[0] == "192.0.2.9" and _schema(result, "mac")[0] == "11:22:33:44:55:66"


async def test_reconfigure_repairs(hass, tv, entry):
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure" and _schema(result, "host")[0] == "192.0.2.1"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "192.0.2.2", "mac": "AA-BB-CC-DD-EE-01"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    assert entry.data == {"host": "192.0.2.2", "mac": "aa:bb:cc:dd:ee:01", "token": "tok", "port": 8002}


def test_host_validation():
    assert config_flow._host_ok("192.0.2.1") and config_flow._host_ok("media-pc.lan")
    assert config_flow._host_ok("[fe80::1]")
    assert not config_flow._host_ok("fe80::1") and not config_flow._host_ok("a b")
    assert not config_flow._host_ok("-bad.lan") and not config_flow._host_ok("[192.0.2.1]")


# --- options flow -----------------------------------------------------------------------------------------

async def test_volume_saves_only_its_section(hass, entry):
    result = await _open(hass, entry, "volume")
    result = await _submit(hass, result, {"normal": 25, "quiet": 8})
    await _save(hass, result)
    assert entry.options["volume"] == {"normal": 25, "quiet": 8, "block_entity": None}
    assert entry.options["modes"] == DEFAULT_MODES


async def test_box_source_input_needs_tv_entity(hass, entry):
    result = await _open(hass, entry, "box")
    box = {"remote": "remote.box", "input": {"method": "source", "value": "HDMI1"}}
    result = await _submit(hass, result, box)
    assert result["errors"] == {"base": "tv_entity_required"}
    result = await _submit(hass, result, {**box, "input": {"method": "key", "value": "HDMI1"}})
    assert result["errors"] == {"base": "invalid_input"}
    result = await _submit(hass, result, {**box, "input": INPUT_KEY})
    result = await _submit(hass, result, {"next_step_id": "tv"})
    result = await _submit(hass, result, {"entity": "media_player.tv", "base_url": "http://ha.lan:8123/"})
    result = await _submit(hass, result, {"next_step_id": "box"})
    result = await _submit(hass, result, box)  # source switching is fine once the TV entity is set
    result = await _submit(hass, result, {"next_step_id": "tv"})
    result = await _submit(hass, result, {})
    assert result["errors"] == {"entity": "tv_entity_required"}
    result = await _submit(hass, result, {"entity": "media_player.tv", "base_url": "http://ha.lan:8123/"})
    await _save(hass, result)
    assert entry.options["box"] == {"remote": "remote.box", "media_player": None, "input": box["input"]}
    assert entry.options["tv"] == {"entity": "media_player.tv", "app_entity": None, "base_url": "http://ha.lan:8123"}


async def test_pc_step(hass, entry, aioclient_mock):
    pc = {"host": "fe80::1", "ambient_port": 8097, "ambient_token": "a", "coord_port": 8098,
          "coord_token": "c", "input": INPUT_KEY, "clients": ["jellyfin"]}
    result = await _open(hass, entry, "pc")
    result = await _submit(hass, result, pc)
    assert result["errors"] == {"host": "invalid_host"}
    aioclient_mock.get("http://media-pc.lan:8097/v2/status", status=401)
    result = await _submit(hass, result, {**pc, "host": "media-pc.lan"})
    assert result["errors"] == {"base": "invalid_auth"}
    aioclient_mock.clear_requests()
    aioclient_mock.get("http://media-pc.lan:8097/v2/status", json={})
    aioclient_mock.get("http://media-pc.lan:8098/v1/status", json={})
    result = await _submit(hass, result, {**pc, "host": "media-pc.lan"})
    await _save(hass, result)
    assert entry.options["pc"] == {**pc, "host": "media-pc.lan"}


async def test_plex_invalid_auth(hass, entry, aioclient_mock):
    aioclient_mock.get(f"{PLEX}/hubs/continueWatching/items", status=401)
    result = await _open(hass, entry, "servers", "plex")
    result = await _submit(hass, result, {"url": PLEX, "token": "bad"})
    assert result["errors"] == {"base": "invalid_auth"}


async def test_plex_prefill_from_core_entry(hass, entry, aioclient_mock):
    MockConfigEntry(domain="plex", data={"server_config": {"url": PLEX, "token": "core"}}).add_to_hass(hass)
    aioclient_mock.get(f"{PLEX}/hubs/continueWatching/items", json={"MediaContainer": {}})
    result = await _open(hass, entry, "servers", "plex")
    result = await _submit(hass, result, {"prefill": True})
    await _save(hass, result)
    assert entry.options["servers"] == {"plex": {"url": PLEX, "token": "core", "player": None}, "jellyfin": None}
    assert aioclient_mock.mock_calls[0][3]["X-Plex-Token"] == "core"


async def test_jellyfin_user_dropdown(hass, entry, aioclient_mock):
    aioclient_mock.get(f"{JF}/Users", json=[{"Name": "alon", "Id": "u1"}, {"Name": "kid", "Id": "u2"}])
    aioclient_mock.get(f"{JF}/UserItems/Resume", json={"Items": []})
    result = await _open(hass, entry, "servers", "jellyfin")
    result = await _submit(hass, result, {"url": JF, "api_key": "key"})
    assert result["step_id"] == "jellyfin_user"
    assert _schema(result, "user_id")[1].config["options"] == [
        {"value": "u1", "label": "alon"}, {"value": "u2", "label": "kid"}]
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == 'MediaBrowser Token="key"'
    result = await _submit(hass, result, {"user_id": "u2", "player": "Samsung"})
    await _save(hass, result)
    assert entry.options["servers"]["jellyfin"] == {"url": JF, "api_key": "key", "user_id": "u2",
                                                     "player": "Samsung"}


async def test_assistant_lists_models(hass, entry):
    with (patch.object(config_flow.assistant, "ensure_sdk", AsyncMock()) as sdk,
          patch.object(config_flow.assistant, "list_models", AsyncMock(
              side_effect=[HomeAssistantError("invalid_auth"), ["claude-haiku-4", "claude-opus-5-5"]]))):
        result = await _open(hass, entry, "assistant")
        result = await _submit(hass, result, {"provider": "claude"})
        assert result["step_id"] == "assistant_auth"
        result = await _submit(hass, result, {"api_key": "bad"})
        assert result["errors"] == {"base": "invalid_auth"}
        result = await _submit(hass, result, {"api_key": "sk"})
        assert result["step_id"] == "assistant_model"
        default, selector = _schema(result, "model")
        assert default == "claude-opus-5-5" and selector.config["options"] == ["claude-haiku-4", "claude-opus-5-5"]
        sdk.assert_awaited_with(hass, "claude")
        result = await _submit(hass, result, {"model": "claude-opus-5-5"})
        await _save(hass, result)
    assert entry.options["assistant"] == {"provider": "claude", "url": None, "api_key": "sk",
                                          "model": "claude-opus-5-5"}


async def test_modes_add_edit_delete(hass, entry):
    result = await _open(hass, entry, "modes")
    result = await _submit(hass, result, {"mode": config_flow.NEW_MODE})
    result = await _submit(hass, result, {"name": "Reading", "icon": "mdi:book"})
    assert result["errors"] == {"name": "mode_exists"}
    result = await _submit(hass, result, {"name": "Movie Night", "icon": "mdi:movie", "flicker": ["light.a"]})
    result = await _submit(hass, result, {"next_step_id": "modes"})
    result = await _submit(hass, result, {"mode": "dinner"})
    assert _schema(result, "name")[0] == "Dinner"
    result = await _submit(hass, result, {"name": "Supper", "icon": "mdi:food", "scene": "scene.dinner",
                                          "lights_only": True})
    result = await _submit(hass, result, {"next_step_id": "modes"})
    result = await _submit(hass, result, {"mode": "chill"})
    result = await _submit(hass, result, {"name": "Chill", "icon": "mdi:sofa", "delete": True})
    await _save(hass, result)
    modes = {m["id"]: m for m in entry.options["modes"]}
    assert "chill" not in modes
    assert modes["movie_night"] == {"id": "movie_night", "name": "Movie Night", "icon": "mdi:movie", "scene": None,
                                    "flicker": ["light.a"], "lights_only": False}
    assert modes["dinner"] == {"id": "dinner", "name": "Supper", "icon": "mdi:food", "scene": "scene.dinner",
                               "flicker": [], "lights_only": True}


async def test_dayparts_ambience_apps(hass, entry):
    part = {"start": "07:30:00", "mode": "chill"}
    result = await _open(hass, entry, "dayparts")
    result = await _submit(hass, result, {"morning": part, "afternoon": {"start": "12:00:00"},
                                          "evening": {"start": "18:00"}, "night": {"start": "22:00"},
                                          "idle_minutes": 15})
    result = await _submit(hass, result, {"next_step_id": "ambience"})
    result = await _submit(hass, result, {"backend": "tv", "radio": "jazz=http://x.example/a"})
    assert result["errors"] == {"radio": "invalid_url"}
    result = await _submit(hass, result, {"backend": "tv", "radio": "jazz=https://x.example/a?b=1\n"})
    result = await _submit(hass, result, {"next_step_id": "apps"})
    result = await _submit(hass, result, {"enabled": ["youtube"], "overrides": "youtube=bad id"})
    assert result["errors"] == {"overrides": "invalid_id"}
    with patch.object(SamsungTV, "discover_apps", return_value={"netflix": "3201907018807"}):
        result = await _submit(hass, result, {"enabled": [], "rescan": True})
    assert _schema(result, "enabled")[0] == ["netflix"] and _schema(result, "overrides")[0] == "netflix=3201907018807"
    result = await _submit(hass, result, {"enabled": ["netflix", "youtube"], "overrides": "netflix=3201907018807"})
    await _save(hass, result)
    assert entry.options["dayparts"]["morning"] == {"start": "07:30", "mode": "chill"}
    assert entry.options["dayparts"]["afternoon"] == {"start": "12:00", "mode": None}
    assert entry.options["dayparts"]["idle_minutes"] == 15
    assert entry.options["ambience"] == {"backend": "tv", "radio": {"jazz": "https://x.example/a?b=1"}}
    assert entry.options["apps"] == {"enabled": ["netflix", "youtube"], "ids": {"netflix": "3201907018807"}}
