from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import quote

import pytest
import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.homehub_media.ambience import STATE_PATH
from custom_components.homehub_media.api import _signed
from custom_components.homehub_media.const import DOMAIN


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


async def test_page_state_signed(hass, hass_client_no_auth, setup_entry):
    client = await hass_client_no_auth()
    resp = await client.get(setup_entry.runtime_data.sign_path(STATE_PATH))
    assert resp.status == 200 and resp.headers["Cache-Control"] == "no-store"
    body = await resp.json()
    assert body["stop"] is True and body["next"].startswith(f"{STATE_PATH}?authSig=")


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
    amb.refresh_catalog = AsyncMock()
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "homehub_media/catalog", "refresh": True})
    res = (await ws.receive_json())["result"]
    amb.refresh_catalog.assert_awaited_once()
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


def test_services_yaml_matches_schemas():
    from homeassistant.util.yaml import load_yaml_dict

    from custom_components.homehub_media.api import SERVICES
    described = load_yaml_dict(str(Path(__file__).parents[1] / "custom_components/homehub_media/services.yaml"))
    assert {name: set((described[name] or {}).get("fields", {})) for name in described} == {
        name: {str(key) for key in schema} for name, (schema, _) in SERVICES.items()}


async def test_service_calls_hub_and_records_command(hass, setup_entry):
    hub = setup_entry.runtime_data
    before = hub.last_command
    await hass.services.async_call(DOMAIN, "remote", {"command": "up", "target": "tv"}, blocking=True)
    hub.tv.send_key.assert_awaited_with("KEY_UP")
    assert hub.last_command > before
    await hass.services.async_call(DOMAIN, "pointer", {"action": "move", "dx": "3.5", "dy": -2}, blocking=True)
    hub.tv.pointer_move.assert_awaited_with(3.5, -2.0)
    with pytest.raises((vol.Invalid, ServiceValidationError)):
        await hass.services.async_call(DOMAIN, "pointer", {"action": "move", "dx": 501}, blocking=True)
    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    with pytest.raises(ServiceValidationError, match="not set up"):
        await hass.services.async_call(DOMAIN, "remote", {"command": "up"}, blocking=True)
