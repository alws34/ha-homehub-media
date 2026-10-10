"""Config flow (TV pairing) and options menu tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homehub_media import config_flow
from custom_components.homehub_media.const import DEFAULT_DAYPARTS, DEFAULT_MODES, DOMAIN
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
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, options={
        "ambience": {"backend": "tv", "radio": {}}, "modes": DEFAULT_MODES,
        "dayparts": {**DEFAULT_DAYPARTS, "morning": {"start": "06:00", "mode": "chill"}}})
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


async def test_reauth_repairs_and_keeps_the_rest(hass, tv, entry):
    tv[1].side_effect = [PairingDenied("no"), "tok2"]
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm" and result["description_placeholders"]["host"] == "192.0.2.1"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "reauth_confirm" and result["errors"] == {"base": "pairing_denied"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reauth_successful"
    assert entry.data == {**DATA, "token": "tok2"} and entry.options["modes"] == DEFAULT_MODES


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


async def test_pc_keeps_secrets_and_removal_resets_backend(hass, aioclient_mock):
    pc = {"host": "media-pc.lan", "ambient_port": 8097, "ambient_token": "a", "coord_port": 8098,
          "coord_token": "c", "input": INPUT_KEY, "clients": []}
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, options={"pc": pc, "ambience": {"backend": "pc", "radio": {}}})
    entry.add_to_hass(hass)
    aioclient_mock.get("http://media-pc.lan:8097/v2/status", json={})
    aioclient_mock.get("http://media-pc.lan:8098/v1/status", json={})
    result = await _open(hass, entry, "pc")
    assert _schema(result, "host")[0] == "media-pc.lan"
    assert _schema(result, "ambient_token")[0] is None and _schema(result, "coord_token")[0] is None
    public = {k: v for k, v in pc.items() if "token" not in k}
    result = await _submit(hass, result, {**public, "clients": ["My App"]})
    assert result["errors"] == {"clients": "invalid_client"}
    result = await _submit(hass, result, {**public, "coord_port": 8099})  # new port: that token must be re-entered
    assert result["errors"] == {"coord_token": "secret_required"} and aioclient_mock.call_count == 0
    result = await _submit(hass, result, public)  # blank tokens keep the saved ones
    assert [c[3]["Authorization"] for c in aioclient_mock.mock_calls] == ["Bearer a", "Bearer c"]
    result = await _submit(hass, result, {"next_step_id": "pc"})
    result = await _submit(hass, result, {"ambient_port": 8097, "coord_port": 8098, "input": INPUT_KEY})
    await _save(hass, result)
    assert entry.options["pc"] is None and entry.options["ambience"]["backend"] == "tv"


async def test_keyboard_step_keeps_saved_key(hass, entry):
    result = await _open(hass, entry, "keyboard")
    result = await _submit(hass, result, {"host": "kb lan", "key": "k1"})
    assert result["errors"] == {"host": "invalid_host"}
    result = await _submit(hass, result, {"host": "kb.lan", "key": "k1"})
    result = await _submit(hass, result, {"next_step_id": "keyboard"})
    assert _schema(result, "host")[0] == "kb.lan" and _schema(result, "key")[0] is None
    result = await _submit(hass, result, {"host": "kb2.lan"})
    assert result["errors"] == {"key": "secret_required"}
    result = await _submit(hass, result, {"host": "kb.lan"})
    await _save(hass, result)
    assert entry.options["keyboard"] == {"host": "kb.lan", "key": "k1"}


@pytest.fixture
def servers_entry(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, options={"servers": {
        "plex": {"url": PLEX, "token": "old", "player": None},
        "jellyfin": {"url": JF, "api_key": "oldkey", "user_id": "u1", "player": None}}})
    entry.add_to_hass(hass)
    return entry


async def test_plex_blank_token_kept_only_for_same_origin(hass, servers_entry, aioclient_mock):
    aioclient_mock.get(f"{PLEX}/hubs/continueWatching/items", json={"MediaContainer": {}})
    result = await _open(hass, servers_entry, "servers", "plex")
    assert _schema(result, "url")[0] == PLEX and _schema(result, "token")[0] is None
    for moved in ("http://other.local:32400", "https://plex.local:32400", "http://plex.local:32401"):
        result = await _submit(hass, result, {"url": moved})
        assert result["errors"] == {"token": "secret_required"}
    assert aioclient_mock.call_count == 0  # the saved token never went to another origin
    result = await _submit(hass, result, {"url": f"{PLEX}/"})  # same origin, different path
    await _save(hass, result)
    assert aioclient_mock.mock_calls[0][3]["X-Plex-Token"] == "old"
    assert servers_entry.options["servers"]["plex"]["token"] == "old"


async def test_jellyfin_blank_key_kept_only_for_same_origin(hass, servers_entry, aioclient_mock):
    aioclient_mock.get(f"{JF}/Users", json=[{"Name": "alon", "Id": "u1"}])
    aioclient_mock.get(f"{JF}/UserItems/Resume", json={"Items": []})
    result = await _open(hass, servers_entry, "servers", "jellyfin")
    assert _schema(result, "api_key")[0] is None
    result = await _submit(hass, result, {"url": "http://jf2.local:8096"})
    assert result["errors"] == {"api_key": "secret_required"} and aioclient_mock.call_count == 0
    result = await _submit(hass, result, {"url": JF})
    assert result["step_id"] == "jellyfin_user"
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == 'MediaBrowser Token="oldkey"'
    result = await _submit(hass, result, {"user_id": "u1"})
    await _save(hass, result)
    assert servers_entry.options["servers"]["jellyfin"]["api_key"] == "oldkey"
    assert servers_entry.options["servers"]["plex"]["token"] == "old"  # sibling untouched


async def test_servers_invalid_auth(hass, entry, aioclient_mock):
    aioclient_mock.get(f"{PLEX}/hubs/continueWatching/items", status=401)
    aioclient_mock.get(f"{JF}/Users", status=403)
    result = await _open(hass, entry, "servers", "plex")
    result = await _submit(hass, result, {"url": PLEX, "token": "bad"})
    assert result["errors"] == {"base": "invalid_auth"}
    result = await _open(hass, entry, "servers", "jellyfin")
    result = await _submit(hass, result, {"url": JF, "api_key": "bad"})
    assert result["errors"] == {"base": "invalid_auth"}


async def test_servers_redirected(hass, entry, aioclient_mock):
    aioclient_mock.get(f"{PLEX}/hubs/continueWatching/items", status=301, headers={"Location": "https://plex.local/"})
    aioclient_mock.get(f"{JF}/Users", status=302)
    result = await _open(hass, entry, "servers", "plex")
    result = await _submit(hass, result, {"url": PLEX, "token": "tok"})
    assert result["errors"] == {"base": "redirected"}
    result = await _open(hass, entry, "servers", "jellyfin")
    result = await _submit(hass, result, {"url": JF, "api_key": "key"})
    assert result["errors"] == {"base": "redirected"}


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
    models = ["claude-haiku-4", "claude-opus-5-5"]
    with (patch.object(config_flow.assistant, "ensure_sdk", AsyncMock()) as sdk,
          patch.object(config_flow.assistant, "list_models", AsyncMock(
              side_effect=[HomeAssistantError("invalid_auth"), models, models])) as list_models):
        result = await _open(hass, entry, "assistant")
        result = await _submit(hass, result, {"provider": "claude"})
        assert result["step_id"] == "assistant_auth"
        result = await _submit(hass, result, {})
        assert result["errors"] == {"api_key": "secret_required"}
        result = await _submit(hass, result, {"api_key": "bad"})
        assert result["errors"] == {"base": "invalid_auth"}
        result = await _submit(hass, result, {"api_key": "sk"})
        assert result["step_id"] == "assistant_model"
        default, selector = _schema(result, "model")
        assert default == "claude-opus-5-5" and selector.config["options"] == ["claude-haiku-4", "claude-opus-5-5"]
        sdk.assert_awaited_with(hass, "claude")
        result = await _submit(hass, result, {"model": "claude-opus-5-5"})
        result = await _submit(hass, result, {"next_step_id": "assistant"})
        result = await _submit(hass, result, {"provider": "claude"})
        assert _schema(result, "api_key")[0] is None  # the saved key is never sent back
        result = await _submit(hass, result, {})  # blank keeps it
        assert list_models.await_args.args == (hass, "claude", None, "sk")
        result = await _submit(hass, result, {"model": "claude-opus-5-5"})
        await _save(hass, result)
    assert entry.options["assistant"] == {"provider": "claude", "url": None, "api_key": "sk",
                                          "model": "claude-opus-5-5"}


async def test_modes_add_edit_delete(hass, entry):
    result = await _open(hass, entry, "modes")
    result = await _submit(hass, result, {"mode": config_flow.NEW_MODE})
    result = await _submit(hass, result, {"name": "Reading", "icon": "mdi:book"})
    assert result["errors"] == {"name": "mode_exists"}
    for bad in ("  ", "Off"):
        result = await _submit(hass, result, {"name": bad, "icon": "mdi:book"})
        assert result["errors"] == {"name": "invalid_name"}
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
    assert entry.options["dayparts"]["morning"] == {"start": "06:00", "mode": None}  # no dangling reference
    assert entry.options["dayparts"]["idle_minutes"] == DEFAULT_DAYPARTS["idle_minutes"]
    assert modes["movie_night"] == {"id": "movie_night", "name": "Movie Night", "icon": "mdi:movie", "scene": None,
                                    "flicker": ["light.a"], "lights_only": False}
    assert modes["dinner"] == {"id": "dinner", "name": "Supper", "icon": "mdi:food", "scene": "scene.dinner",
                               "flicker": [], "lights_only": True}


async def test_default_modes_are_copied(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, options={})
    entry.add_to_hass(hass)
    result = await _open(hass, entry, "modes")
    result = await _submit(hass, result, {"mode": "dinner"})
    result = await _submit(hass, result, {"name": "Supper", "icon": "mdi:food"})
    await _save(hass, result)
    assert DEFAULT_MODES[1]["name"] == "Dinner"
    assert not any(a is b for a, b in zip(entry.options["modes"], DEFAULT_MODES))


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
    result = await _submit(hass, result, {"enabled": ["netflix", "youtube"], "overrides": "netflix=3201907018807"})
    await _save(hass, result)
    assert entry.options["dayparts"]["morning"] == {"start": "07:30", "mode": "chill"}
    assert entry.options["dayparts"]["afternoon"] == {"start": "12:00", "mode": None}
    assert entry.options["dayparts"]["idle_minutes"] == 15
    assert entry.options["ambience"] == {"backend": "tv", "radio": {"jazz": "https://x.example/a?b=1"}, "youtube": {}}
    assert entry.options["apps"] == {"enabled": ["netflix", "youtube"], "ids": {"netflix": "3201907018807"}}


async def test_ambience_youtube_videos(hass, entry):
    result = await _open(hass, entry, "ambience")
    result = await _submit(hass, result, {"backend": "tv", "youtube": "Rain=https://example.com/watch?v=B5unCXpegAw"})
    assert result["errors"] == {"youtube": "invalid_youtube"}
    result = await _submit(hass, result, {"backend": "tv", "youtube": "no equals sign", "radio": "jazz"})
    assert result["errors"] == {"youtube": "invalid_youtube", "radio": "invalid_url"}
    lines = [f"Video {i}=https://youtu.be/B5unCXpeg{i:02d}" for i in range(30, 0, -1)]
    lines.insert(3, "Star night = https://www.youtube.com/watch?v=B5unCXpegAw&pp=ygUKc3RhciBuaWdodA%3D%3D")
    result = await _submit(hass, result, {"backend": "tv", "youtube": "\n".join(lines) + "\n\n"})
    await _save(hass, result)
    youtube = entry.options["ambience"]["youtube"]
    assert list(youtube) == [line.partition("=")[0].strip() for line in lines]  # every line, in order
    assert youtube["Star night"] == "https://www.youtube.com/watch?v=B5unCXpegAw&pp=ygUKc3RhciBuaWdodA%3D%3D"
    result = await _open(hass, entry, "ambience")
    assert _schema(result, "youtube")[0].splitlines()[3] == (
        "Star night=https://www.youtube.com/watch?v=B5unCXpegAw&pp=ygUKc3RhciBuaWdodA%3D%3D")


async def test_apps_keep_discovered_and_rescan_fills_it(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, options={"apps": {"enabled": ["youtube"], "ids": {},
                                                                        "discovered": {"youtube": "111299001912"}}})
    entry.add_to_hass(hass)
    result = await _open(hass, entry, "apps")
    result = await _submit(hass, result, {"enabled": ["youtube"], "overrides": "youtube=abc"})
    await _save(hass, result)
    assert entry.options["apps"] == {"enabled": ["youtube"], "ids": {"youtube": "abc"},
                                     "discovered": {"youtube": "111299001912"}}
    result = await _open(hass, entry, "apps")
    with patch.object(SamsungTV, "discover_apps", return_value={}):  # TV in standby
        result = await _submit(hass, result, {"enabled": [], "rescan": True})
    assert result["errors"] == {"base": "cannot_connect"}
    with patch.object(SamsungTV, "discover_apps", return_value={"netflix": "3201907018807"}):
        result = await _submit(hass, result, {"enabled": [], "overrides": "youtube=abc", "rescan": True})
    assert _schema(result, "enabled")[0] == ["netflix"] and _schema(result, "overrides")[0] == "youtube=abc"
    result = await _submit(hass, result, {"enabled": ["netflix", "youtube"], "overrides": "youtube=abc"})
    await _save(hass, result)
    assert entry.options["apps"] == {"enabled": ["netflix", "youtube"], "ids": {"youtube": "abc"},
                                     "discovered": {"netflix": "3201907018807"}}
