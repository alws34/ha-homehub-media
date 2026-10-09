"""Tests for the Plex / Jellyfin library client."""
from __future__ import annotations

import base64
from datetime import timedelta

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed, async_mock_service

from custom_components.homehub_media.library import InvalidAuth, JellyfinServer, Library, PlexServer

PLEX = "http://plex.local:32400"
JF = "http://jf.local:8096"
JPEG = {"Content-Type": "image/jpeg"}


async def test_auth_rejection_is_typed(hass, aioclient_mock):
    aioclient_mock.get(f"{PLEX}/hubs/continueWatching/items", status=401)
    aioclient_mock.get(f"{JF}/UserItems/Resume", status=500)
    with pytest.raises(InvalidAuth):
        await PlexServer(hass, PLEX, "bad", None).continue_watching()
    with pytest.raises(HomeAssistantError) as err:
        await JellyfinServer(hass, JF, "key", "u1", None).continue_watching()
    assert not isinstance(err.value, InvalidAuth)


async def test_jellyfin_users(hass, aioclient_mock):
    aioclient_mock.get(f"{JF}/Users", json=[{"Id": "u1", "Name": "alon"}, {"Id": "u2"}])
    assert await JellyfinServer(hass, JF, "key", "", None).users() == [
        {"id": "u1", "name": "alon"}, {"id": "u2", "name": "u2"}]
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == 'MediaBrowser Token="key"'


async def test_plex_continue_normalised(hass, aioclient_mock):
    aioclient_mock.get(f"{PLEX}/hubs/continueWatching/items", json={"MediaContainer": {"Metadata": [
        {"ratingKey": "123", "type": "episode", "title": "Pilot", "grandparentTitle": "Severance",
         "viewOffset": 600000, "duration": 3000000, "thumb": "/library/metadata/123/thumb/1"}]}})
    items = await PlexServer(hass, PLEX, "tok", None).continue_watching()
    assert items[0]["id"] == "123" and items[0]["title"] == "Severance"
    assert items[0]["subtitle"] == "Pilot" and abs(items[0]["progress"] - 0.2) < 1e-6
    assert items[0]["image"].startswith("/api/homehub_media/art/plex/")
    assert aioclient_mock.mock_calls[0][3]["X-Plex-Token"] == "tok"


async def test_jellyfin_sessions_now_playing(hass, aioclient_mock):
    aioclient_mock.get(f"{JF}/Sessions", json=[{"Id": "s1", "DeviceName": "Samsung TV", "SupportsMediaControl": True,
        "NowPlayingItem": {"Id": "abc", "Name": "Dune", "RunTimeTicks": 100_000_000_000, "Type": "Movie"},
        "PlayState": {"PositionTicks": 50_000_000_000, "IsPaused": True}}])
    np = await JellyfinServer(hass, JF, "key", "u1", None).sessions()
    assert np == [{"server": "jellyfin", "player": "Samsung TV", "title": "Dune", "subtitle": None,
                   "state": "paused", "position": 5000.0, "duration": 10000.0,
                   "image": "/api/homehub_media/art/jellyfin/abc"}]


async def test_library_refresh_survives_server_error(hass, aioclient_mock):
    aioclient_mock.get(f"{JF}/UserItems/Resume", status=500)
    aioclient_mock.get(f"{JF}/Items/Latest", status=500)
    lib = Library(hass, [JellyfinServer(hass, JF, "key", "u1", None)], on_change=lambda: None)
    await lib.async_refresh()
    assert lib.items["jellyfin"] == {"continue": [], "recent": []}


async def test_plex_recently_added_movie(hass, aioclient_mock):
    aioclient_mock.get(f"{PLEX}/library/recentlyAdded", json={"MediaContainer": {"Metadata": [
        {"ratingKey": "9", "type": "movie", "title": "Dune", "year": 2021, "thumb": "/library/metadata/9/thumb/5"}]}})
    items = await PlexServer(hass, PLEX, "tok", None).recently_added()
    thumb = items[0]["image"].rsplit("/", 1)[1]
    assert base64.urlsafe_b64decode(thumb + "=" * (-len(thumb) % 4)) == b"/library/metadata/9/thumb/5"
    assert {k: v for k, v in items[0].items() if k != "image"} == {
        "id": "9", "server": "plex", "title": "Dune", "subtitle": "2021", "type": "movie", "progress": None}
    query = aioclient_mock.mock_calls[0][1].query
    assert query["X-Plex-Container-Start"] == "0" and query["X-Plex-Container-Size"] == "20"
    assert aioclient_mock.mock_calls[0][3]["Accept"] == "application/json"


async def test_jellyfin_resume_and_latest(hass, aioclient_mock):
    aioclient_mock.get(f"{JF}/UserItems/Resume", json={"Items": [
        {"Id": "e1", "Name": "Pilot", "SeriesName": "Severance", "Type": "Episode", "RunTimeTicks": 400,
         "UserData": {"PlaybackPositionTicks": 100}}]})
    aioclient_mock.get(f"{JF}/Items/Latest", json=[{"Id": "m1", "Name": "Dune", "Type": "Movie", "ProductionYear": 2021}])
    jf = JellyfinServer(hass, JF, "key", "u1", None)
    assert await jf.continue_watching() == [{"id": "e1", "server": "jellyfin", "title": "Severance", "subtitle": "Pilot",
        "type": "episode", "progress": 0.25, "image": "/api/homehub_media/art/jellyfin/e1"}]
    assert (await jf.recently_added())[0]["subtitle"] == "2021"
    first, second = (c[1].query for c in aioclient_mock.mock_calls)
    assert (first["userId"], first["limit"], first["mediaTypes"]) == ("u1", "20", "Video")
    assert (second["userId"], second["limit"]) == ("u1", "20")
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == 'MediaBrowser Token="key"'


async def test_plex_sessions_player_filter(hass, aioclient_mock):
    aioclient_mock.get(f"{PLEX}/status/sessions", json={"MediaContainer": {"Metadata": [
        {"ratingKey": "1", "type": "movie", "title": "Dune", "duration": 10000, "viewOffset": 2500,
         "thumb": "/library/metadata/1/thumb/1", "Player": {"title": "Living Room TV", "state": "paused"}},
        {"ratingKey": "2", "type": "movie", "title": "Heat", "Player": {"title": "Phone", "state": "playing"}}]}})
    np = await PlexServer(hass, PLEX, "tok", "living").sessions()
    assert [(n["player"], n["title"], n["state"], n["position"], n["duration"]) for n in np] == [
        ("Living Room TV", "Dune", "paused", 2.5, 10.0)]


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "x\n", "a" * 65])
async def test_item_id_validation(hass, aioclient_mock, bad):
    jf, plex = JellyfinServer(hass, JF, "key", "u1", None), PlexServer(hass, PLEX, "tok", None)
    for call in (jf.play(bad), jf.fetch_art(bad), plex.play(bad)):
        with pytest.raises(ServiceValidationError):
            await call
    assert aioclient_mock.call_count == 0


async def test_jellyfin_play_picks_matching_newest_session_and_resumes(hass, aioclient_mock):
    aioclient_mock.get(f"{JF}/Sessions", json=[
        {"Id": "old", "DeviceName": "Samsung TV", "SupportsMediaControl": True, "LastActivityDate": "2026-10-09T10:00:00Z"},
        {"Id": "new", "DeviceName": "Samsung TV", "SupportsMediaControl": True, "LastActivityDate": "2026-10-09T12:00:00Z"},
        {"Id": "phone", "DeviceName": "Pixel", "SupportsMediaControl": True, "LastActivityDate": "2026-10-09T13:00:00Z"},
        {"Id": "nomc", "DeviceName": "Samsung TV", "SupportsMediaControl": False, "LastActivityDate": "2026-10-09T14:00:00Z"}])
    aioclient_mock.get(f"{JF}/Items", json={"Items": [{"Id": "abc", "UserData": {"PlaybackPositionTicks": 123456}}]})
    aioclient_mock.post(f"{JF}/Sessions/new/Playing", status=204)
    assert await JellyfinServer(hass, JF, "key", "u1", "samsung").play("abc") is True
    method, url, _, headers = aioclient_mock.mock_calls[-1]
    assert method == "POST" and url.path == "/Sessions/new/Playing"
    assert dict(url.query) == {"playCommand": "PlayNow", "itemIds": "abc", "startPositionTicks": "123456"}
    assert headers["Authorization"] == 'MediaBrowser Token="key"'
    items_query = next(c[1].query for c in aioclient_mock.mock_calls if c[1].path == "/Items")
    assert (items_query["ids"], items_query["userId"]) == ("abc", "u1")


async def test_jellyfin_play_without_session_returns_false(hass, aioclient_mock):
    aioclient_mock.get(f"{JF}/Sessions", json=[{"Id": "p", "DeviceName": "Pixel", "SupportsMediaControl": True}])
    assert await JellyfinServer(hass, JF, "key", "u1", "samsung").play("abc") is False
    assert all(c[0] == "GET" for c in aioclient_mock.mock_calls)


async def test_plex_play_via_core_entity(hass, aioclient_mock):
    ent = er.async_get(hass).async_get_or_create("media_player", "plex", "uid1", suggested_object_id="lr_tv")
    other = er.async_get(hass).async_get_or_create("media_player", "cast", "uid2", suggested_object_id="lr_cast")
    hass.states.async_set(ent.entity_id, "idle", {"friendly_name": "Living Room TV"})
    hass.states.async_set(other.entity_id, "idle", {"friendly_name": "Living Room Cast"})
    aioclient_mock.get(f"{PLEX}/identity", json={"MediaContainer": {"machineIdentifier": "m4ch1ne"}})
    calls = async_mock_service(hass, "media_player", "play_media")
    assert await PlexServer(hass, PLEX, "tok", "living room").play("123") is True
    assert calls[0].data == {"entity_id": ent.entity_id, "media_content_type": "movie",
                             "media_content_id": "plex://m4ch1ne/123?resume=1"}
    assert await PlexServer(hass, PLEX, "tok", "bedroom").play("123") is False
    assert len(calls) == 1


async def test_plex_play_service_failure_returns_false(hass, aioclient_mock):
    ent = er.async_get(hass).async_get_or_create("media_player", "plex", "uid1")
    hass.states.async_set(ent.entity_id, "idle")
    aioclient_mock.get(f"{PLEX}/identity", status=500)
    assert await PlexServer(hass, PLEX, "tok", None).play("123") is False


async def test_jellyfin_art_proxy(hass, aioclient_mock):
    aioclient_mock.get(f"{JF}/Items/abc/Images/Primary", content=b"\xff\xd8img", headers=JPEG)
    assert await JellyfinServer(hass, JF, "key", "u1", None).fetch_art("abc") == (b"\xff\xd8img", "image/jpeg")
    assert aioclient_mock.mock_calls[0][1].query["maxHeight"] == "450"


def _key(path: bytes) -> str:
    return base64.urlsafe_b64encode(path).decode().rstrip("=")


async def test_plex_art_goes_through_transcoder(hass, aioclient_mock):
    aioclient_mock.get(f"{PLEX}/photo/:/transcode", content=b"img", headers=JPEG)
    assert await PlexServer(hass, PLEX, "tok", None).fetch_art(_key(b"/library/metadata/1/thumb/2")) == (b"img", "image/jpeg")
    method, url, _, headers = aioclient_mock.mock_calls[0]
    assert (method, url.path) == ("GET", "/photo/:/transcode")
    assert dict(url.query) == {"url": "/library/metadata/1/thumb/2", "width": "300", "height": "450",
                               "minSize": "1", "upscale": "1"}
    assert headers["X-Plex-Token"] == "tok" and headers["Accept"] == "image/*"


@pytest.mark.parametrize("bad", [
    b"/library/sections/all/refresh", b"/library/parts/1/2/file.mkv", b"/status/sessions",
    b"/library/../status/sessions", b"/library/metadata/1/thumb/2/../../x", b"/library/metadata/1/thumb/2\n",
    b"/library/metadata/x/thumb/2", b"//evil.example/x", b"http://evil.example/x"])
async def test_plex_art_rejects_non_thumb_paths(hass, aioclient_mock, bad):
    with pytest.raises(ServiceValidationError):
        await PlexServer(hass, PLEX, "tok", None).fetch_art(_key(bad))
    with pytest.raises(ServiceValidationError):
        await PlexServer(hass, PLEX, "tok", None).fetch_art("!!not-base64!!")
    assert aioclient_mock.call_count == 0


async def test_plex_art_rejects_non_image_response(hass, aioclient_mock):
    aioclient_mock.get(f"{PLEX}/photo/:/transcode", content=b"<html>", headers={"Content-Type": "text/html"})
    with pytest.raises(HomeAssistantError):
        await PlexServer(hass, PLEX, "tok", None).fetch_art(_key(b"/library/metadata/3/art/2"))


async def test_plex_item_with_unproxyable_thumb_has_no_image(hass, aioclient_mock):
    aioclient_mock.get(f"{PLEX}/library/recentlyAdded", json={"MediaContainer": {"Metadata": [
        {"ratingKey": "1", "type": "movie", "title": "A", "thumb": "/library/sections/all/refresh"},
        {"ratingKey": "2", "type": "movie", "title": "B"}]}})
    assert [i["image"] for i in await PlexServer(hass, PLEX, "tok", None).recently_added()] == [None, None]


async def test_jellyfin_sessions_only_configured_user(hass, aioclient_mock):
    now = {"Id": "a", "Name": "Dune"}
    aioclient_mock.get(f"{JF}/Sessions", json=[
        {"Id": "mine", "UserId": "A1B2-C3", "DeviceName": "TV", "SupportsMediaControl": True, "NowPlayingItem": now,
         "LastActivityDate": "2026-10-09T10:00:00Z"},
        {"Id": "other", "UserId": "ffff", "DeviceName": "TV", "SupportsMediaControl": True, "NowPlayingItem": now,
         "LastActivityDate": "2026-10-09T12:00:00Z"}])
    aioclient_mock.get(f"{JF}/Items", json={"Items": []})
    aioclient_mock.post(f"{JF}/Sessions/mine/Playing", status=204)
    jf = JellyfinServer(hass, JF, "key", "a1b2c3", None)  # dashes and case are irrelevant
    assert [n["player"] for n in await jf.sessions()] == ["TV"]
    assert await jf.play("abc") is True
    assert aioclient_mock.mock_calls[-1][1].path == "/Sessions/mine/Playing"  # newer session belongs to someone else
    assert await JellyfinServer(hass, JF, "key", "zzzz", None).sessions() == []


async def test_library_sessions_aggregate_both_servers(hass, aioclient_mock):
    aioclient_mock.get(f"{PLEX}/status/sessions", json={"MediaContainer": {"Metadata": [
        {"ratingKey": "1", "type": "movie", "title": "Heat", "Player": {"title": "TV", "state": "playing"}}]}})
    aioclient_mock.get(f"{JF}/Sessions", json=[{"Id": "s", "DeviceName": "TV2", "NowPlayingItem": {"Id": "a", "Name": "Dune"},
                                               "PlayState": {"PositionTicks": 0}}, {"Id": "idle", "DeviceName": "x"}])
    changes = []
    lib = Library(hass, [PlexServer(hass, PLEX, "tok", None), JellyfinServer(hass, JF, "key", "u1", None)],
                  on_change=lambda: changes.append(1))
    await lib.async_refresh_sessions()
    assert [(n["server"], n["title"], n["state"]) for n in lib.now_playing] == [
        ("plex", "Heat", "playing"), ("jellyfin", "Dune", "playing")]
    assert len(changes) == 1
    await lib.async_refresh_sessions()  # unchanged -> no notification
    assert len(changes) == 1
    assert lib.server("plex").name == "plex" and lib.server("nope") is None


async def test_library_sessions_one_server_down(hass, aioclient_mock):
    aioclient_mock.get(f"{PLEX}/status/sessions", status=401)
    aioclient_mock.get(f"{JF}/Sessions", json=[{"Id": "s", "DeviceName": "TV2", "NowPlayingItem": {"Id": "a", "Name": "Dune"}}])
    lib = Library(hass, [PlexServer(hass, PLEX, "tok", None), JellyfinServer(hass, JF, "key", "u1", None)], lambda: None)
    await lib.async_refresh_sessions()
    assert [n["server"] for n in lib.now_playing] == ["jellyfin"]


async def test_library_refresh_fills_items_and_notifies(hass, aioclient_mock):
    aioclient_mock.get(f"{JF}/UserItems/Resume", json={"Items": [{"Id": "e1", "Name": "Pilot", "Type": "Episode"}]})
    aioclient_mock.get(f"{JF}/Items/Latest", json=[{"Id": "m1", "Name": "Dune", "Type": "Movie"}])
    changes = []
    lib = Library(hass, [JellyfinServer(hass, JF, "key", "u1", None)], on_change=lambda: changes.append(1))
    await lib.async_refresh()
    assert [i["id"] for i in lib.items["jellyfin"]["continue"]] == ["e1"]
    assert [i["id"] for i in lib.items["jellyfin"]["recent"]] == ["m1"] and changes == [1]


async def test_library_refresh_keeps_last_items_when_server_fails(hass, aioclient_mock):
    aioclient_mock.get(f"{JF}/UserItems/Resume", json={"Items": [{"Id": "e1", "Name": "Pilot", "Type": "Episode"}]})
    aioclient_mock.get(f"{JF}/Items/Latest", json=[{"Id": "m1", "Name": "Dune", "Type": "Movie"}])
    changes = []
    lib = Library(hass, [JellyfinServer(hass, JF, "key", "u1", None)], on_change=lambda: changes.append(1))
    await lib.async_refresh()
    kept = lib.items["jellyfin"]
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{JF}/UserItems/Resume", status=500)
    aioclient_mock.get(f"{JF}/Items/Latest", status=500)
    await lib.async_refresh()
    assert lib.items["jellyfin"] == kept and kept["continue"][0]["id"] == "e1" and changes == [1]


async def test_library_refresh_silent_when_unchanged(hass, aioclient_mock):
    aioclient_mock.get(f"{JF}/UserItems/Resume", json={"Items": []})
    aioclient_mock.get(f"{JF}/Items/Latest", json=[{"Id": "m1", "Name": "Dune", "Type": "Movie"}])
    changes = []
    lib = Library(hass, [JellyfinServer(hass, JF, "key", "u1", None)], on_change=lambda: changes.append(1))
    await lib.async_refresh()
    await lib.async_refresh()
    assert changes == [1]


async def test_library_start_polls_sessions_until_cancelled(hass, aioclient_mock):
    aioclient_mock.get(f"{JF}/Sessions", json=[{"Id": "s", "DeviceName": "TV", "NowPlayingItem": {"Id": "a", "Name": "Dune"}}])
    lib = Library(hass, [JellyfinServer(hass, JF, "key", "u1", None)], on_change=lambda: None)
    cancel = lib.async_start()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(lib.now_playing) == 1
    cancel()
    calls = aioclient_mock.call_count
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert aioclient_mock.call_count == calls
