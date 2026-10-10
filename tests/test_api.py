from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import quote

import pytest
import voluptuous as vol
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError, Unauthorized

from custom_components.homehub_media.ambience import STATE_PATH
from custom_components.homehub_media.api import _signed
from custom_components.homehub_media.const import DOMAIN
from custom_components.homehub_media.library import BODY_MAX

from .test_hub import samsung_remote


async def test_subscribe_sends_snapshot(hass, hass_ws_client, setup_entry):
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "homehub_media/subscribe"})
    assert (await ws.receive_json())["success"]
    ev = await ws.receive_json()
    assert ev["event"]["activity"]["current"] in ("off", "tv", "box", "pc") and "ambience" in ev["event"]
    assert ev["event"]["apps"][0] == {"key": "netflix", "name": "Netflix", "installed": False}


async def test_signed_urls_stable_within_the_hour(hass, freezer):
    # what the subscribe/catalog/library handlers sign with; a jumped loop clock would close a real websocket
    conn, art = SimpleNamespace(refresh_token_id="token-id"), "/api/homehub_media/art/plex/abc"
    freezer.move_to("2026-10-09 12:00:05")
    first = _signed(hass, conn, art)
    freezer.tick(600)  # a new signature would carry a new iat
    second = _signed(hass, conn, art)
    freezer.tick(3600)
    third = _signed(hass, conn, art)
    assert first.startswith(f"{art}?authSig=") and first == second != third
    assert _signed(hass, conn, "/local/Ambience/a.webp") == "/local/Ambience/a.webp"


async def test_send_text_validation(hass, setup_entry):
    with pytest.raises((vol.Invalid, ServiceValidationError)):
        await hass.services.async_call(DOMAIN, "send_text", {"text": "a\x00b"}, blocking=True)


async def test_page_state_requires_auth(hass, hass_client_no_auth, setup_entry):
    client = await hass_client_no_auth()
    resp = await client.get("/api/homehub_media/page/state")
    assert resp.status == 401


async def test_page_state_signed_and_bound_to_the_page_session(hass, hass_client_no_auth, setup_entry):
    hub = setup_entry.runtime_data
    hub.ambience._sid, hub.ambience.page_last_seen = "s1", 0.0
    client = await hass_client_no_auth()
    for sid in ("old", ""):  # validly signed, but not the current page session
        assert (await client.get(hub.sign_path(f"{STATE_PATH}?sid={sid}"))).status == 404
    assert hub.ambience.page_last_seen == 0.0  # a stale page is no heartbeat
    resp = await client.get(hub.sign_path(f"{STATE_PATH}?sid=s1"))
    assert resp.status == 200 and resp.headers["Cache-Control"] == "no-store"
    body = await resp.json()
    assert body["stop"] is True and body["next"] is None and hub.ambience.page_last_seen > 0


async def test_art_rejects_unknown_server(hass, hass_client, setup_entry):
    client = await hass_client()
    resp = await client.get("/api/homehub_media/art/evil/abc")
    assert resp.status == 404


async def test_art_proxy_headers_and_types(hass, hass_client, setup_entry):
    srv = MagicMock(fetch_art=AsyncMock(return_value=(b"jpg", "image/jpeg")))
    setup_entry.runtime_data.library = MagicMock(server=lambda name: srv if name == "plex" else None)
    client = await hass_client()
    resp = await client.get("/api/homehub_media/art/plex/abc")
    assert resp.status == 200 and await resp.read() == b"jpg" and resp.content_type == "image/jpeg"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Content-Security-Policy"] == "default-src 'none'"
    assert resp.headers["Cache-Control"] == "private, max-age=3600"
    srv.fetch_art.return_value = (b"<svg onload=alert(1)/>", "image/svg+xml")
    assert (await client.get("/api/homehub_media/art/plex/abc")).status == 502
    srv.fetch_art.return_value = (b"\0" * (5 * 1024 * 1024 + 1), "image/jpeg")
    assert (await client.get("/api/homehub_media/art/plex/abc")).status == 502
    srv.fetch_art.side_effect = ServiceValidationError("Invalid art key")
    assert (await client.get("/api/homehub_media/art/plex/abc")).status == 404
    srv.fetch_art.side_effect = HomeAssistantError("plex down")
    assert (await client.get("/api/homehub_media/art/plex/abc")).status == 502


async def test_sign_path_keeps_encoding(hass, hass_client_no_auth, setup_entry):
    name = "a #?b é.mp4"
    Path(hass.config.media_dirs["local"], name).write_bytes(b"\0")
    url = setup_entry.runtime_data.sign_path(f"/media/local/{quote(name)}")
    assert url.startswith("/media/local/a%20%23%3Fb%20%C3%A9.mp4?authSig=")
    client = await hass_client_no_auth()
    assert (await client.get(url)).status == 200


async def test_catalog_signs_media_previews_only(hass, hass_ws_client, setup_entry):
    amb = setup_entry.runtime_data.ambience
    amb._catalog = {"visuals": [{"id": "rain", "preview": "/media/local/homehub/ambience/videos/rain.webp",
                                 "path": "rain.mp4", "preview_path": "rain.webp"}],
                    "soundtracks": [{"id": "pc", "preview": "/local/Ambience/pc.webp"}, {"id": "x", "preview": None}]}
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "homehub_media/catalog"})
    res = (await ws.receive_json())["result"]
    visual = res["visuals"][0]
    assert visual["preview"].startswith("/media/local/homehub/ambience/videos/rain.webp?authSig=")
    assert "path" not in visual and "preview_path" not in visual
    assert [s["preview"] for s in res["soundtracks"]] == ["/local/Ambience/pc.webp", None]


async def test_library_signs_art(hass, hass_ws_client, setup_entry):
    setup_entry.runtime_data.library = MagicMock(items={"plex": {"continue": [
        {"id": "1", "image": "/api/homehub_media/art/plex/abc"}], "recent": []}})
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "homehub_media/library"})
    res = (await ws.receive_json())["result"]
    assert res["plex"]["continue"][0]["image"].startswith("/api/homehub_media/art/plex/abc?authSig=")


async def test_art_proxy_youtube_thumbnail_falls_back_to_hq(hass, hass_client, setup_entry, aioclient_mock):
    hub = setup_entry.runtime_data
    client = await hass_client()
    assert (await client.get("/api/homehub_media/art/youtube/B5unCXpegAw")).status == 404  # not in the catalog
    assert aioclient_mock.call_count == 0  # HA fetched nothing
    hub.options["ambience"] = {**hub.options["ambience"], "youtube": {"Star night": "https://youtu.be/B5unCXpegAw"}}
    await hub.ambience.refresh_catalog()
    assert hub.art_source("youtube", "B5unCXpegAw") is hub.youtube_art  # one client, not one per request
    thumbs = "https://i.ytimg.com/vi/B5unCXpegAw/"
    aioclient_mock.get(thumbs + "maxresdefault.jpg", status=404)
    aioclient_mock.get(thumbs + "hqdefault.jpg", content=b"jpg", headers={"Content-Type": "image/jpeg"})
    resp = await client.get("/api/homehub_media/art/youtube/B5unCXpegAw")
    assert resp.status == 200 and await resp.read() == b"jpg" and resp.content_type == "image/jpeg"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert [str(u) for _, u, *_ in aioclient_mock.mock_calls] == [thumbs + "maxresdefault.jpg", thumbs + "hqdefault.jpg"]
    for bad in ("B5unCXpegA", "B5unCXpegAw1", "QjV1bkNYcGVnQXc="):
        assert (await client.get(f"/api/homehub_media/art/youtube/{bad}")).status == 404
    aioclient_mock.clear_requests()
    aioclient_mock.get(thumbs + "maxresdefault.jpg", content=b"<html>", headers={"Content-Type": "text/html"})
    aioclient_mock.get(thumbs + "hqdefault.jpg", content=b"<svg/>", headers={"Content-Type": "image/svg+xml"})
    assert (await client.get("/api/homehub_media/art/youtube/B5unCXpegAw")).status == 502  # images only


async def test_catalog_signs_youtube_previews_and_hides_video_ids(hass, hass_ws_client, setup_entry):
    hub = setup_entry.runtime_data
    hub.options["ambience"] = {**hub.options["ambience"], "youtube": {"Star night": "https://youtu.be/B5unCXpegAw"}}
    await hub.ambience.refresh_catalog()
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "homehub_media/catalog"})
    visual = (await ws.receive_json())["result"]["visuals"][-1]
    assert visual["id"] == "star_night" and visual["source_type"] == "youtube" and "youtube_id" not in visual
    assert visual["preview"].startswith("/api/homehub_media/art/youtube/B5unCXpegAw?authSig=")


@pytest.mark.parametrize(("service", "data"), [
    ("ambience_update", {"visual": "../etc"}),
    ("ambience_update", {"soundtrack": "x" * 65}),
    ("save_mode", {"mode": "chill", "visuals": ["Rain"]}),
    ("ambience_start", {"mode": "a" * 65}),
    ("pc_open", {"client": "a" * 65}),
])
async def test_service_ids_are_bounded_at_the_schema(hass, setup_entry, service, data):
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, service, data, blocking=True)


async def test_visuals_through_the_service_schemas(hass, setup_entry):
    amb = setup_entry.runtime_data.ambience
    await hass.services.async_call(DOMAIN, "save_mode", {"mode": "chill", "visuals": ["rain", "fire", "rain"]},
                                   blocking=True)
    assert amb.composition("chill")["visuals"] == ["rain", "fire"]
    await hass.services.async_call(DOMAIN, "save_mode", {"mode": "chill", "visuals": "rain"}, blocking=True)
    assert amb.composition("chill")["visuals"] == ["rain"]  # one id is a list of one
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, "save_mode", {"mode": "chill", "visuals": [f"v{i}" for i in range(51)]},
                                       blocking=True)
    with pytest.raises(vol.Invalid):  # ids are checked at the schema, then again by the manager
        await hass.services.async_call(DOMAIN, "save_mode", {"mode": "chill", "visuals": ["../x"]}, blocking=True)
    amb.update = AsyncMock()
    await hass.services.async_call(DOMAIN, "ambience_update", {"visuals": ["fire", "rain"]}, blocking=True)
    amb.update.assert_awaited_once_with({"visuals": ["fire", "rain"]})


async def test_set_volume_service(hass, setup_entry):
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, "set_volume", {"level": 101}, blocking=True)
    with pytest.raises(ServiceValidationError, match="TV media player"):
        await hass.services.async_call(DOMAIN, "set_volume", {"level": 30}, blocking=True)


def test_services_yaml_matches_schemas():
    from homeassistant.util.yaml import load_yaml_dict

    from custom_components.homehub_media.api import SERVICES
    described = load_yaml_dict(str(Path(__file__).parents[1] / "custom_components/homehub_media/services.yaml"))
    assert {name: set((described[name] or {}).get("fields", {})) for name in described} == {
        name: {str(key) for key in schema} for name, (schema, _) in SERVICES.items()}


def test_every_service_field_is_translated():
    import json

    from custom_components.homehub_media.api import SERVICES
    names = json.loads((Path(__file__).parents[1] / "custom_components/homehub_media/translations/en.json")
                       .read_text())["services"]
    assert {n: set(names[n].get("fields", {})) for n in names} == {
        n: {str(key) for key in schema} for n, (schema, _) in SERVICES.items()}


async def test_service_calls_hub_and_records_command(hass, setup_entry):
    hub = setup_entry.runtime_data
    before = hub.last_command
    await hass.services.async_call(DOMAIN, "remote", {"command": "up", "target": "tv"}, blocking=True)
    hub.tv.send_key.assert_awaited_with("KEY_UP", 0)
    assert hub.last_command > before
    await hass.services.async_call(DOMAIN, "pointer", {"action": "move", "dx": "3.5", "dy": -2}, blocking=True)
    hub.tv.pointer_move.assert_awaited_with(3.5, -2.0)
    with pytest.raises((vol.Invalid, ServiceValidationError)):
        await hass.services.async_call(DOMAIN, "pointer", {"action": "move", "dx": 501}, blocking=True)
    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    with pytest.raises(ServiceValidationError, match="not set up"):
        await hass.services.async_call(DOMAIN, "remote", {"command": "up"}, blocking=True)


async def test_send_key_service_uses_the_core_samsung_remote(hass, setup_entry):
    hub = setup_entry.runtime_data
    send = samsung_remote(hass)
    hub.options["tv"] = {"entity": "media_player.tv"}
    await hass.services.async_call(DOMAIN, "send_key", {"key": "KEY_UP"}, blocking=True)
    assert send[-1].data == {"entity_id": "remote.tv", "command": "KEY_UP"}
    hub.tv.send_key.assert_not_awaited()
    await hass.services.async_call(DOMAIN, "send_key", {"key": "KEY_POWER", "hold_secs": 2}, blocking=True)
    hub.tv.send_key.assert_awaited_once_with("KEY_POWER", 2.0)  # a hold needs HomeHub's own WebSocket


async def test_rescan_apps_is_admin_only(hass, setup_entry, hass_read_only_user):
    ctx = Context(user_id=hass_read_only_user.id)
    with pytest.raises(Unauthorized):
        await hass.services.async_call(DOMAIN, "rescan_apps", {}, blocking=True, context=ctx)
    await hass.services.async_call(DOMAIN, "refresh_catalog", {}, blocking=True, context=ctx)  # any user


async def test_set_mode_videos(hass, setup_entry, hass_read_only_user):
    hub = setup_entry.runtime_data
    amb, call = hub.ambience, hass.services.async_call
    hub.options["ambience"] = {**hub.options["ambience"], "youtube": {
        "Fire": "fireFIREfir", "Old": "https://youtu.be/B5unCXpegAw", "Spare": "spareSPAREs"}}
    hass.config_entries.async_update_entry(setup_entry, options={"ambience": hub.options["ambience"]})
    await amb.refresh_catalog()
    await amb.save_mode("dinner", {"visual": "fire"})
    await amb.save_mode("chill", {"visuals": ["old", "fire"], "soundtrack_volume": 40})
    videos = [{"name": " New ", "url": "https://www.youtube.com/watch?v=newNEWnewNE"},
              {"name": "Fire", "url": "https://youtu.be/fire2FIRE2f"}]
    with pytest.raises(Unauthorized):  # writes the config entry
        await call(DOMAIN, "set_mode_videos", {"mode": "chill", "videos": videos}, blocking=True,
                   context=Context(user_id=hass_read_only_user.id))
    for bad, error in (([{"name": "Bad", "url": "https://example.com/x"}], "not a YouTube video link"),
                       ([videos[1], {**videos[1], "name": "Fire "}], "its own name")):
        with pytest.raises(ServiceValidationError, match=error):
            await call(DOMAIN, "set_mode_videos", {"mode": "chill", "videos": bad}, blocking=True)
    for bad in ([{"name": "a=b", "url": "fireFIREfir"}], [{"name": "a"}]):
        with pytest.raises(vol.Invalid):
            await call(DOMAIN, "set_mode_videos", {"mode": "chill", "videos": bad}, blocking=True)
    with pytest.raises(ServiceValidationError, match="Unknown ambience mode"):
        await call(DOMAIN, "set_mode_videos", {"mode": "nope", "videos": []}, blocking=True)
    assert "Old" in setup_entry.options["ambience"]["youtube"]  # nothing written so far

    await call(DOMAIN, "set_mode_videos", {"mode": "chill", "videos": videos}, blocking=True)
    # Old was only chill's: dropped; Fire is dinner's too: changed in place; Spare was never used: kept
    assert setup_entry.options["ambience"]["youtube"] == hub.options["ambience"]["youtube"] == {
        "Fire": "https://youtu.be/fire2FIRE2f", "Spare": "spareSPAREs", "New": "https://www.youtube.com/watch?v=newNEWnewNE"}
    comp = amb.composition("chill")
    assert (comp["visual"], comp["visuals"], comp["visual_enabled"], comp["soundtrack_volume"]) == (
        "new", ["new", "fire"], True, 40)
    yt = [(v["id"], v["link"]) for v in amb.catalog()["visuals"] if v["source_type"] == "youtube"]
    assert yt == [("fire", "https://youtu.be/fire2FIRE2f"), ("spare", "spareSPAREs"),
                  ("new", "https://www.youtube.com/watch?v=newNEWnewNE")]
    assert setup_entry.runtime_data is hub  # the options write did not reload the entry

    await call(DOMAIN, "set_mode_videos", {"mode": "chill", "videos": []}, blocking=True)
    comp = amb.composition("chill")
    assert (comp["visual"], comp["visuals"], comp["visual_enabled"]) == (None, [], False)
    assert list(setup_entry.options["ambience"]["youtube"]) == ["Fire", "Spare"]


async def test_ambience_start_video_goes_to_the_manager(hass, setup_entry):
    amb = setup_entry.runtime_data.ambience
    amb.start = AsyncMock()
    await hass.services.async_call(DOMAIN, "ambience_start", {"mode": "chill", "video": "fire"}, blocking=True)
    amb.start.assert_awaited_once_with("chill", video="fire")
    await hass.services.async_call(DOMAIN, "ambience_start", {"mode": "chill"}, blocking=True)
    amb.start.assert_awaited_with("chill", video=None)
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, "ambience_start", {"mode": "chill", "video": "Bad id"}, blocking=True)


OEMBED = "https://www.youtube.com/oembed"


async def test_set_mode_videos_names_from_youtube(hass, setup_entry, aioclient_mock):
    hub = setup_entry.runtime_data
    hub.options["ambience"] = {**hub.options["ambience"], "youtube": {"Taken": "spareSPAREs"}}
    query = lambda vid: {"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"}
    aioclient_mock.get(OEMBED, params=query("newNEWnewNE"), json={"title": " Cozy = fire\n" + "x" * 100})
    aioclient_mock.get(OEMBED, params=query("fire2FIRE2f"), status=404)
    aioclient_mock.get(OEMBED, params=query("textTEXTtex"), text="not json")
    videos = [{"url": "https://youtu.be/newNEWnewNE"}, {"url": "fire2FIRE2f"},
              {"url": "takenTAKEN1"}, {"url": "textTEXTtex"}, {"url": "spareSPAREs"}]
    aioclient_mock.get(OEMBED, params=query("takenTAKEN1"), json={"title": "Taken"})
    aioclient_mock.get(OEMBED, params=query("spareSPAREs"), json={"title": "Taken"})
    await hass.services.async_call(DOMAIN, "set_mode_videos", {"mode": "chill", "videos": videos}, blocking=True)
    names = list(setup_entry.options["ambience"]["youtube"])
    assert names[1:] == [("Cozy fire " + "x" * 100)[:80], "fire2FIRE2f", "Taken (takenTAKEN1)", "textTEXTtex"]
    assert names[0] == "Taken"  # the same video under its existing name: kept, not renamed
    assert all(len(n) <= 80 and "=" not in n and "\n" not in n for n in names)


async def test_set_mode_videos_ignores_an_oversized_youtube_answer(hass, setup_entry, aioclient_mock):
    aioclient_mock.get(OEMBED, params={"url": "https://www.youtube.com/watch?v=hugeHUGEhug", "format": "json"},
                       content=b'{"title": "Huge"}' + b" " * BODY_MAX)
    await hass.services.async_call(DOMAIN, "set_mode_videos", {"mode": "chill", "videos": [{"url": "hugeHUGEhug"}]},
                                   blocking=True)
    assert list(setup_entry.options["ambience"]["youtube"]) == ["hugeHUGEhug"]  # its id, not the title
