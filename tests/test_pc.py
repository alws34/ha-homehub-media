from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.homehub_media.keyboard import KeyboardBridge
from custom_components.homehub_media.library import BODY_MAX
from custom_components.homehub_media.pc import AmbientPlayerClient, CoordinatorClient, PCAuthError, PCError

AP = "http://192.0.2.5:8097"
CO = "http://192.0.2.5:8098"


async def test_bearer_and_json(hass, aioclient_mock):
    aioclient_mock.post("http://192.0.2.5:8097/v2/play", json={"ok": True})
    await AmbientPlayerClient(hass, "192.0.2.5", 8097, "secret").play({"visual": "rain"})
    _, _, body, headers = aioclient_mock.mock_calls[0]
    assert headers["Authorization"] == "Bearer secret" and body == {"visual": "rain"}


async def test_play_waits_longer_than_other_calls(hass):
    """The player answers /v2/play only after waiting for HDMI and starting mpv."""
    with patch.object(AmbientPlayerClient, "_request", AsyncMock(return_value={})) as req:
        await AmbientPlayerClient(hass, "192.0.2.5", 8097, "t").play({"visual": "rain"})
    assert req.await_args.args[-1].total == 30


async def test_401_maps_to_token_error(hass, aioclient_mock):
    aioclient_mock.post("http://192.0.2.5:8098/v1/open/jellyfin", status=401)
    with pytest.raises(PCError, match="token"):
        await CoordinatorClient(hass, "192.0.2.5", 8098, "bad").open("jellyfin")


async def test_auth_rejection_is_typed(hass, aioclient_mock):
    aioclient_mock.get(f"{AP}/v2/status", status=403)
    aioclient_mock.get(f"{CO}/v1/status", status=500)
    with pytest.raises(PCAuthError):
        await AmbientPlayerClient(hass, "192.0.2.5", 8097, "bad").status()
    with pytest.raises(PCError) as err:
        await CoordinatorClient(hass, "192.0.2.5", 8098, "t").status()
    assert not isinstance(err.value, PCAuthError)


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (401, {}, "Media PC rejected the token"),
        (403, {}, "Media PC rejected the token"),
        (421, {}, "Media PC host name mismatch"),
        (409, {"error": "unknown_client"}, "unknown_client"),
        (409, {"nope": 1}, "Media PC request failed"),
        (415, {}, "Media PC request failed"),
        (500, {"error": "boom"}, "Media PC request failed"),
    ],
)
async def test_error_mapping(hass, aioclient_mock, status, body, message):
    aioclient_mock.post(f"{CO}/v1/close", status=status, json=body)
    with pytest.raises(PCError) as err:
        await CoordinatorClient(hass, "192.0.2.5", 8098, "t").close()
    assert str(err.value) == message


async def test_409_without_json_body(hass, aioclient_mock):
    aioclient_mock.post(f"{CO}/v1/close", status=409, text="not json")
    with pytest.raises(PCError, match="Media PC request failed"):
        await CoordinatorClient(hass, "192.0.2.5", 8098, "t").close()


@pytest.mark.parametrize("exc", [aiohttp.ClientConnectionError, TimeoutError])
async def test_unreachable(hass, aioclient_mock, exc):
    aioclient_mock.get(f"{AP}/v2/status", exc=exc)
    with pytest.raises(PCError, match="Media PC is unreachable"):
        await AmbientPlayerClient(hass, "192.0.2.5", 8097, "t").status()


async def test_non_json_success_is_an_error(hass, aioclient_mock):
    aioclient_mock.get(f"{AP}/v2/status", text="<html>")
    with pytest.raises(PCError, match="Media PC request failed"):
        await AmbientPlayerClient(hass, "192.0.2.5", 8097, "t").status()


@pytest.mark.parametrize(
    ("call", "args", "method", "path", "body"),
    [
        ("status", (), "get", "/v2/status", None),
        ("catalog", (), "get", "/v2/catalog", None),
        ("refresh", (), "post", "/v2/catalog/refresh", None),
        ("play", ({"visual": "rain"},), "post", "/v2/play", {"visual": "rain"}),
        ("patch", ({"video": {"volume": 3}},), "patch", "/v2/state", {"video": {"volume": 3}}),
        ("stop", (), "post", "/v2/stop", None),
    ],
)
async def test_ambient_routes(hass, aioclient_mock, call, args, method, path, body):
    getattr(aioclient_mock, method)(f"{AP}{path}", json={"ok": True})
    assert await getattr(AmbientPlayerClient(hass, "192.0.2.5", 8097, "t"), call)(*args) == {"ok": True}
    got_method, url, got_body, _ = aioclient_mock.mock_calls[0]
    assert (got_method, url.raw_path, got_body) == (method.upper(), path, body)


@pytest.mark.parametrize(
    ("call", "args", "method", "path", "body"),
    [
        ("status", (), "get", "/v1/status", None),
        ("open", ("jellyfin",), "post", "/v1/open/jellyfin", None),
        ("open", ("a/b",), "post", "/v1/open/a%2Fb", None),
        ("close", (), "post", "/v1/close", None),
        ("remote", ("play_pause",), "post", "/v1/remote/play_pause", None),
        ("text", ("hi",), "post", "/v1/keyboard/text", {"text": "hi"}),
        ("pointer_move", (3, -4.5), "post", "/v1/pointer/move", {"dx": 3, "dy": -4.5}),
        ("click", (), "post", "/v1/pointer/click", {"button": "left"}),
        ("click", ("right",), "post", "/v1/pointer/click", {"button": "right"}),
        ("scroll", (-2,), "post", "/v1/pointer/scroll", {"dy": -2}),
    ],
)
async def test_coordinator_routes(hass, aioclient_mock, call, args, method, path, body):
    getattr(aioclient_mock, method)(f"{CO}{path}", json={"ok": True})
    await getattr(CoordinatorClient(hass, "192.0.2.5", 8098, "t"), call)(*args)
    got_method, url, got_body, headers = aioclient_mock.mock_calls[0]
    assert (got_method, url.raw_path, got_body) == (method.upper(), path, body)
    assert headers["Authorization"] == "Bearer t" and headers["Content-Type"] == "application/json"
    assert "Host" not in headers  # aiohttp derives Host (host:port) from the URL


async def test_status_returns_json(hass, aioclient_mock):
    aioclient_mock.get(f"{CO}/v1/status", json={"active": "jellyfin"})
    assert await CoordinatorClient(hass, "192.0.2.5", 8098, "t").status() == {"active": "jellyfin"}


async def test_keyboard_type_and_key(hass, aioclient_mock):
    aioclient_mock.post("http://kbd.local/type")
    aioclient_mock.post("http://kbd.local/key")
    bridge = KeyboardBridge(hass, "kbd.local", "shared")
    await bridge.type("héllo")
    await bridge.key("enter")
    for (method, _, body, headers), want in zip(aioclient_mock.mock_calls, ["héllo", "enter"], strict=True):
        assert method == "POST" and body == want
        assert headers["X-HomeHub-Key"] == "shared" and headers["Content-Type"].startswith("text/plain")
    assert [str(c[1]) for c in aioclient_mock.mock_calls] == ["http://kbd.local/type", "http://kbd.local/key"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"status": 401}, "Keyboard bridge rejected the shared key"),
        ({"status": 403}, "Keyboard bridge rejected the shared key"),
        ({"status": 500}, "Keyboard bridge request failed"),
        ({"exc": aiohttp.ClientConnectionError}, "Keyboard bridge is unreachable"),
        ({"exc": TimeoutError}, "Keyboard bridge is unreachable"),
    ],
)
async def test_keyboard_errors(hass, aioclient_mock, kwargs, message):
    aioclient_mock.post("http://kbd.local/key", **kwargs)
    with pytest.raises(HomeAssistantError) as err:
        await KeyboardBridge(hass, "kbd.local", "shared").key("clear")
    assert str(err.value) == message


async def test_response_size_is_capped(hass, aioclient_mock):
    aioclient_mock.get(f"{AP}/v2/status", content=b'{"state": "idle"}' + b" " * BODY_MAX)
    with pytest.raises(PCError):
        await AmbientPlayerClient(hass, "192.0.2.5", 8097, "t").status()


async def test_authenticated_requests_do_not_follow_redirects(hass, redirector):
    host, port = redirector.host, redirector.port
    with pytest.raises(PCError):
        await AmbientPlayerClient(hass, host, port, "t").status()
    with pytest.raises(HomeAssistantError):
        await KeyboardBridge(hass, f"{host}:{port}", "k").type("hi")
    assert redirector.hits == []  # the token and key never went to the new location
