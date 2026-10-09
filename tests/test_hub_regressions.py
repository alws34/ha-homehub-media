"""Regression tests from the hub review: startup store race, unload cleanup, signing cache."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry, get_scheduled_timer_handles

from custom_components.homehub_media import api
from custom_components.homehub_media import hub as hub_mod
from custom_components.homehub_media.catalog import validate_composition
from custom_components.homehub_media.const import DOMAIN

KEY = f"{DOMAIN}.data"
DATA = {"host": "192.0.2.1", "token": "t", "mac": None}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def _entry(hass, tmp_path):
    hass.config.media_dirs = {"local": str(tmp_path)}
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, options={"ambience": {"backend": "tv", "radio": {}}})
    entry.add_to_hass(hass)
    return entry


def _tv(tv_cls):
    tv = tv_cls.return_value
    tv.token, tv.available, tv.keyboard_active, tv.pointer_active = "t", True, False, False
    tv.discover_apps.return_value = {"youtube": "111299001912"}
    return tv


def _slow_store_load():
    real = Store.async_load

    async def slow(self):
        if self.key == KEY:
            await asyncio.sleep(0.05)  # stands in for the executor read of .storage/homehub_media.data
        return await real(self)
    return slow


def _our_timers(hass):  # live timers whose job is one of ours (_poll_tv, _idle_check, library, ...)
    return [h for h in get_scheduled_timer_handles(hass.loop)
            if not h.cancelled() and "custom_components.homehub_media" in repr(h._callback)]


# ---- the race ---------------------------------------------------------------------------------------

async def test_race_save_right_after_setup_keeps_persisted_data(hass, hass_storage, tmp_path):
    comp = validate_composition({"visual": "old"})
    hass_storage[KEY] = {"version": 1, "minor_version": 1, "key": KEY,
                         "data": {"compositions": {"dinner": comp}, "favorites": {"visual": ["old"], "soundtrack": []}}}
    entry = _entry(hass, tmp_path)
    with patch.object(Store, "async_load", _slow_store_load()), \
            patch.object(hub_mod, "SamsungTV", autospec=True) as tv_cls:
        _tv(tv_cls)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()  # setup is done; a background store load would still be waiting
        hub = entry.runtime_data
        shown = hub.ambience.snapshot()["compositions"]["dinner"]
        await hass.services.async_call(DOMAIN, "set_favorite",
                                       {"kind": "visual", "media_id": "new", "favorite": True}, blocking=True)
        await hass.async_block_till_done(wait_background_tasks=True)
        on_disk = hass_storage[KEY]["data"]
        in_mem = hub.ambience._store._data
        result = {"snapshot_during_load_is_saved": shown == comp,
                  "disk_favs": on_disk["favorites"]["visual"], "disk_comp": "dinner" in on_disk["compositions"],
                  "mem_favs": in_mem["favorites"]["visual"], "mem_comp": "dinner" in in_mem["compositions"]}
        assert await hass.config_entries.async_unload(entry.entry_id)
    assert result == {"snapshot_during_load_is_saved": True, "disk_favs": ["new", "old"], "disk_comp": True,
                      "mem_favs": ["new", "old"], "mem_comp": True}


# ---- unload regressions -------------------------------------------------------------------------------

async def test_setup_unload_twice_leaves_no_timers(hass, tmp_path):
    entry = _entry(hass, tmp_path)
    with patch.object(hub_mod, "SamsungTV", autospec=True) as tv_cls:
        _tv(tv_cls)
        for _ in range(2):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done(wait_background_tasks=True)
            hub = entry.runtime_data
            assert hub.ambience._unsub_idle is not None and _our_timers(hass)
            assert await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done(wait_background_tasks=True)
            assert entry.state is ConfigEntryState.NOT_LOADED
            assert hub.ambience._unsub_idle is None and not hub._unsubs and not _our_timers(hass)


async def test_unload_while_startup_load_is_running_twice(hass, tmp_path):
    entry = _entry(hass, tmp_path)
    gate = asyncio.Event()  # never set: the catalog load is always mid-flight at unload

    async def stuck(self):
        await gate.wait()
    with patch("custom_components.homehub_media.ambience.AmbienceManager.refresh_catalog", stuck), \
            patch.object(hub_mod, "SamsungTV", autospec=True) as tv_cls:
        _tv(tv_cls)
        for _ in range(2):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
            hub = entry.runtime_data
            assert await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done(wait_background_tasks=True)
            assert entry.state is ConfigEntryState.NOT_LOADED
            assert hub.ambience._unsub_idle is None and not _our_timers(hass)
            assert not entry._background_tasks


# ---- signing cache --------------------------------------------------------------------------------------

async def test_sign_cache_never_shares_a_url_between_tokens(hass, freezer):
    hass.data["http.auth"] = "content-user-token"
    freezer.move_to("2026-10-09 12:00:05")
    path = "/api/homehub_media/art/plex/abc"
    a1 = api._signed(hass, SimpleNamespace(refresh_token_id="token-a"), path)
    b1 = api._signed(hass, SimpleNamespace(refresh_token_id="token-b"), path)
    content = hub_mod.sign(hass, path)
    freezer.tick(60)
    a2 = api._signed(hass, SimpleNamespace(refresh_token_id="token-a"), path)
    assert a1 == a2 and len({a1, b1, content}) == 3
    import jwt
    iss = {jwt.decode(u.split("authSig=")[1], options={"verify_signature": False})["iss"] for u in (a1, b1, content)}
    assert {"token-a", "token-b"} <= iss and len(iss) == 3


# ---- \Z patterns ------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("service", "good", "bad"), [
    ("launch_app", [{"app": "youtube"}, {"app": "3201907018807"}, {"app": "org.tizen.browser"}, {"app": "a" * 64}],
     [{"app": "youtube\n"}, {"app": "a" * 65}, {"app": ""}]),
    ("send_key", [{"key": "KEY_VOLUP"}, {"key": "KEY_3", "hold_secs": 1}], [{"key": "KEY_VOLUP\n"}, {"key": "VOLUP"}]),
    ("channel", [{"number": "7"}, {"number": "1234"}, {"number": 12}], [{"number": "12\n"}, {"number": "12345"}]),
    ("set_favorite", [{"kind": "visual", "media_id": "rain-1", "favorite": True},
                      {"kind": "soundtrack", "media_id": "a" * 64, "favorite": False}],
     [{"kind": "visual", "media_id": "rain\n", "favorite": True},
      {"kind": "visual", "media_id": "Rain", "favorite": True}]),
    ("play_library_item", [{"server": "plex", "item_id": "12345"}, {"server": "jellyfin", "item_id": "abc-DEF-1"}],
     [{"server": "plex", "item_id": "123\n"}]),
])
def test_z_patterns(service, good, bad):
    schema = vol.Schema(api.SERVICES[service][0])
    for data in good:
        schema(data)
    for data in bad:
        with pytest.raises(vol.Invalid):
            schema(data)
