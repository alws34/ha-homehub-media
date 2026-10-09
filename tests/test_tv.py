import base64
from unittest.mock import MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from samsungtvws.exceptions import UnauthorizedError

from custom_components.homehub_media import tv as tvmod
from custom_components.homehub_media.const import APP_CATALOG

BASE = "http://192.0.2.1:8001/api/v2/applications/"
ACKS = {}  # appId -> ed.apps.launch reply data (default 200); tests use monkeypatch.setitem


class FakeRemote:
    def __init__(self, *a, **kw):
        self.sent, self.kw, self.token = [], kw, kw.get("token")
    async def __aenter__(self): return self
    async def __aexit__(self, *exc): ...
    async def open(self): ...
    async def start_listening(self, cb):
        await self.open()  # samsungtvws' start_listening opens the socket itself
        self.cb = cb
    def is_alive(self): return True
    async def send_command(self, cmd, key_press_delay=None): await self.send_commands([cmd])
    async def send_commands(self, cmds, key_press_delay=None):
        self.sent.extend(cmds)
        for c in cmds:
            if c.params.get("event") == "ed.apps.launch":
                self.cb("ed.apps.launch", {"data": ACKS.get(c.params["data"]["appId"], 200)})
    async def close(self): ...


@pytest.fixture
def fake_remote(monkeypatch):
    holder = {}
    def factory(*a, **kw):
        holder["r"] = FakeRemote(*a, **kw); return holder["r"]
    monkeypatch.setattr(tvmod, "SamsungTVWSAsyncRemote", factory)
    return holder


async def test_send_key_validates(hass, fake_remote):
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    with pytest.raises(ServiceValidationError):
        await tv.send_key("rm -rf")
    await tv.send_key("KEY_HOME")
    assert any("KEY_HOME" in str(getattr(c, "params", c)) for c in fake_remote["r"].sent)


async def test_text_rejects_control_chars(hass, fake_remote):
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    with pytest.raises(ServiceValidationError):
        await tv.send_text("hi\nthere")
    with pytest.raises(ServiceValidationError):
        await tv.send_text("")


async def test_ime_events_toggle_flags(hass, fake_remote):
    changes = []
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None, on_change=lambda: changes.append(1))
    await tv.send_key("KEY_HOME")
    fake_remote["r"].cb("ms.remote.imeStart", {})
    assert tv.keyboard_active and changes
    fake_remote["r"].cb("ms.remote.imeEnd", {})
    assert not tv.keyboard_active


async def test_discover_apps_picks_first_installed(hass, aioclient_mock):
    base = "http://192.0.2.1:8001/api/v2/applications/"
    aioclient_mock.get(base + "3201901017640", json={"id": "3201901017640", "name": "Disney+"})
    for app in APP_CATALOG.values():
        for i in app["ids"]:
            if i != "3201901017640":
                aioclient_mock.get(base + i, status=404)
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    assert await tv.discover_apps() == {"disney_plus": "3201901017640"}


async def test_unreachable_raises_friendly(hass, monkeypatch):
    class Boom(FakeRemote):
        async def open(self): raise OSError("no route")
    monkeypatch.setattr(tvmod, "SamsungTVWSAsyncRemote", Boom)
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    with pytest.raises(HomeAssistantError, match="off or unreachable"):
        await tv.send_key("KEY_HOME")
    assert tv.available is False


async def test_hold_key_and_hold_bounds(hass, fake_remote):
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    await tv.send_key("KEY_POWER", hold_secs=2)
    assert [c.params.get("Cmd") for c in fake_remote["r"].sent] == ["Press", None, "Release"]
    with pytest.raises(ServiceValidationError):
        await tv.send_key("KEY_POWER", hold_secs=60)


async def test_text_broadcast_once_per_ime_session(hass, fake_remote):
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    await tv.send_text("héllo")
    await tv.send_text("x")
    fake_remote["r"].cb("ms.remote.imeStart", {})
    await tv.send_text("y")
    sent = fake_remote["r"].sent
    assert [c.params.get("event") or c.params["TypeOfRemote"] for c in sent] == [
        "custom.remote.textReceived", "SendInputString", "SendInputEnd",
        "SendInputString", "SendInputEnd",
        "custom.remote.textReceived", "SendInputString", "SendInputEnd",
    ]
    assert base64.b64decode(sent[1].params["Cmd"]).decode() == "héllo"


async def test_pointer_clamps(hass, fake_remote):
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    await tv.pointer_move(40, -20)
    await tv.pointer_move(9000, -9000.4)
    await tv.pointer_click()
    sent = [c.as_dict() for c in fake_remote["r"].sent]
    assert sent[0] == {"method": "ms.remote.control", "params": {
        "Cmd": "Move", "Position": {"x": 40, "y": -20, "Time": "0"}, "TypeOfRemote": "ProcessMouseDevice"}}
    assert sent[1]["params"]["Position"] == {"x": 500, "y": -500, "Time": "0"}
    assert sent[2]["params"] == {"Cmd": "LeftClick", "TypeOfRemote": "ProcessMouseDevice"}


async def test_launch_ack_and_rest_fallback(hass, fake_remote, aioclient_mock, monkeypatch):
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    await tv.launch("111299001912")
    assert fake_remote["r"].sent[-1].as_dict() == {"method": "ms.channel.emit", "params": {
        "event": "ed.apps.launch", "to": "host",
        "data": {"action_type": "DEEP_LINK", "appId": "111299001912", "metaTag": ""}}}
    assert aioclient_mock.call_count == 0
    monkeypatch.setitem(ACKS, "3201907018807", 404)
    monkeypatch.setitem(ACKS, "3201606009684", 404)
    aioclient_mock.post(BASE + "3201907018807", json=True)
    aioclient_mock.post(BASE + "3201606009684", status=404)
    await tv.launch("3201907018807")  # negative ack -> REST POST launches it
    assert aioclient_mock.call_count == 1
    with pytest.raises(HomeAssistantError, match="did not launch"):
        await tv.launch("3201606009684")  # not installed
    with pytest.raises(ServiceValidationError):
        await tv.launch("../../etc")


async def test_open_url_falls_back_to_browser_id(hass, fake_remote, aioclient_mock, monkeypatch):
    monkeypatch.setitem(ACKS, "org.tizen.browser", 404)
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    with pytest.raises(ServiceValidationError):
        await tv.open_url("javascript:alert(1)")
    await tv.open_url("http://192.0.2.9:8123/x")
    assert [c.params["data"] for c in fake_remote["r"].sent] == [
        {"action_type": "NATIVE_LAUNCH", "appId": "org.tizen.browser", "metaTag": "http://192.0.2.9:8123/x"},
        {"action_type": "DEEP_LINK", "appId": "3202010022079", "metaTag": "http://192.0.2.9:8123/x"},
    ]
    assert aioclient_mock.call_count == 0  # metaTag launches never fall back to REST


async def test_restart_app_deletes_posts_and_waits_running(hass, aioclient_mock):
    aioclient_mock.delete(BASE + "111299001912", json=True)
    aioclient_mock.post(BASE + "111299001912", json=True)
    aioclient_mock.get(BASE + "111299001912", json={"id": "111299001912", "running": True})
    aioclient_mock.delete(BASE + "3201606009684", status=404)
    aioclient_mock.post(BASE + "3201606009684", status=404)
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    await tv.restart_app("111299001912")
    assert [c[0].upper() for c in aioclient_mock.mock_calls] == ["DELETE", "POST", "GET"]
    with pytest.raises(HomeAssistantError, match="not installed"):
        await tv.restart_app("3201606009684")


async def test_wake(hass, monkeypatch):
    sock = MagicMock()
    monkeypatch.setattr(tvmod, "socket", sock)
    await tvmod.SamsungTV(hass, "192.0.2.1", "tok", None).wake()
    sock.socket.assert_not_called()
    await tvmod.SamsungTV(hass, "192.0.2.1", "tok", "AA:BB:CC:DD:EE:FF").wake()
    packet, _ = sock.socket.return_value.__enter__.return_value.sendto.call_args.args
    assert packet == b"\xff" * 6 + bytes.fromhex("AABBCCDDEEFF") * 16
    with pytest.raises(HomeAssistantError):
        await tvmod.SamsungTV(hass, "192.0.2.1", "tok", "nope").wake()


async def test_pair(hass, monkeypatch):
    class Approve(FakeRemote):
        async def open(self): self.token = "new"
    monkeypatch.setattr(tvmod, "SamsungTVWSAsyncRemote", Approve)
    tv = tvmod.SamsungTV(hass, "192.0.2.1", None, None)
    assert await tv.pair() == "new" and tv.token == "new"

    class Deny(FakeRemote):
        async def open(self): raise UnauthorizedError("denied")
    monkeypatch.setattr(tvmod, "SamsungTVWSAsyncRemote", Deny)
    with pytest.raises(tvmod.PairingDenied):
        await tv.pair()
