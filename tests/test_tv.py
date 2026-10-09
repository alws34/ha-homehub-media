import asyncio
import base64
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from samsungtvws.exceptions import ConnectionFailure, UnauthorizedError

from custom_components.homehub_media import tv as tvmod
from custom_components.homehub_media.const import APP_CATALOG

BASE = "http://192.0.2.1:8001/api/v2/applications/"
DIAL = "http://192.0.2.1:8080/ws/apps/YouTube"
VID = "B5unCXpegAw"


def dial_xml(state):
    return (f'<?xml version="1.0" encoding="UTF-8"?><service xmlns="urn:dial-multiscreen-org:schemas:dial">'
            f"<name>YouTube</name><options allowStop=\"true\"/><state>{state}</state></service>")


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
    async def send_commands(self, cmds, key_press_delay=None): self.sent.extend(cmds)  # the TV never acks
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
    assert len(changes) == 1  # available False -> True
    fake_remote["r"].cb("ms.remote.imeStart", {})
    assert tv.keyboard_active and len(changes) == 2
    fake_remote["r"].cb("ms.remote.imeEnd", {})
    fake_remote["r"].cb("ms.remote.imeDone", {})  # no change, no notification
    assert not tv.keyboard_active and len(changes) == 3
    fake_remote["r"].cb("ms.remote.touchEnable", {})
    assert tv.pointer_active and len(changes) == 4


async def test_listener_error_does_not_break_events(hass, fake_remote):
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None, on_change=lambda: 1 / 0)
    await tv.send_key("KEY_HOME")
    fake_remote["r"].cb("ms.remote.imeStart", {})
    assert tv.keyboard_active


async def test_unauthorized_token_raises_pairing_denied(hass, monkeypatch):
    class Revoked(FakeRemote):
        async def open(self): raise UnauthorizedError("revoked")
    monkeypatch.setattr(tvmod, "SamsungTVWSAsyncRemote", Revoked)
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    with pytest.raises(tvmod.PairingDenied):
        await tv.send_key("KEY_HOME")
    assert tv.available is False


async def test_reconnects_after_failure_and_dead_socket(hass, monkeypatch):
    remotes, fails, changes = [], [OSError("down")], []
    class Flaky(FakeRemote):
        async def open(self):
            if fails:
                raise fails.pop()
    def factory(*a, **kw):
        remotes.append(Flaky(*a, **kw)); return remotes[-1]
    monkeypatch.setattr(tvmod, "SamsungTVWSAsyncRemote", factory)
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None, on_change=lambda: changes.append(tv.available))
    with pytest.raises(tvmod.CannotConnect):
        await tv.send_key("KEY_HOME")
    await tv.send_key("KEY_HOME")
    assert tv.available and len(remotes) == 2 and remotes[1].sent
    remotes[1].is_alive = lambda: False  # TV dropped the socket
    await tv.send_key("KEY_HOME")
    assert len(remotes) == 3 and changes == [True, False, True]


async def test_token_rotation_notifies(hass, monkeypatch):
    class Rotate(FakeRemote):
        async def open(self): self.token = "rotated"
    monkeypatch.setattr(tvmod, "SamsungTVWSAsyncRemote", Rotate)
    tokens = []
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None, on_change=lambda: tokens.append(tv.token))
    await tv.send_key("KEY_HOME")
    assert tv.token == "rotated" and tokens == ["rotated", "rotated"]  # token, then available


async def test_device_info_refreshes_availability(hass, aioclient_mock):
    url = "http://192.0.2.1:8001/api/v2/"
    aioclient_mock.get(url, json={"device": {"modelName": "QE55Q60BAUXSQ", "PowerState": "on"}})
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    assert (await tv.device_info())["modelName"] == "QE55Q60BAUXSQ" and tv.available
    aioclient_mock.clear_requests()
    aioclient_mock.get(url, json={"device": {"PowerState": "standby"}})
    await tv.device_info()
    assert not tv.available  # REST answers in standby
    aioclient_mock.clear_requests()
    aioclient_mock.get(url, exc=TimeoutError())
    with pytest.raises(tvmod.CannotConnect):
        await tv.device_info()


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


@pytest.fixture
def fast_sleep(monkeypatch):
    """asyncio.sleep without the wait; the delays asked for are recorded."""
    delays, real_sleep = [], asyncio.sleep
    async def sleep(delay, *a, **kw):
        delays.append(delay)
        await real_sleep(0)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    return delays


async def test_launch_uses_rest_first_and_never_waits(hass, fake_remote, aioclient_mock):
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    aioclient_mock.post(BASE + "111299001912", json=True)
    await tv.launch("111299001912")
    assert aioclient_mock.call_count == 1 and "r" not in fake_remote  # no WebSocket at all
    aioclient_mock.post(BASE + "3201606009684", status=404)
    aioclient_mock.post(BASE + "3201907018807", exc=TimeoutError())
    async with asyncio.timeout(1):  # fire-and-forget: no reply is awaited
        await tv.launch("3201606009684")  # REST 404 -> WebSocket launch
        await tv.launch("3201907018807")  # REST unreachable -> WebSocket launch
    assert [c.as_dict()["params"]["data"] for c in fake_remote["r"].sent] == [
        {"action_type": "DEEP_LINK", "appId": "3201606009684", "metaTag": ""},
        {"action_type": "DEEP_LINK", "appId": "3201907018807", "metaTag": ""}]
    calls = aioclient_mock.call_count
    await tv.launch("3202010022079", "http://x/", "NATIVE_LAUNCH")
    assert aioclient_mock.call_count == calls  # REST cannot carry a metaTag
    for bad in ("../../etc", "..", ".hidden"):
        with pytest.raises(ServiceValidationError):
            await tv.launch(bad)


async def test_open_url_raises_when_the_browser_stays_hidden(hass, fake_remote, aioclient_mock, fast_sleep):
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    for bad in ("javascript:alert(1)", "http:nohost", "http://x/" + "a" * 2048):
        with pytest.raises(ServiceValidationError):
            await tv.open_url(bad)
    aioclient_mock.get(BASE + "3202010022079", json={"id": "3202010022079", "running": True, "visible": False})
    with pytest.raises(HomeAssistantError, match="does not allow opening web pages remotely"):
        await tv.open_url("http://192.0.2.9:8123/x")
    assert [c.params["data"] for c in fake_remote["r"].sent] == [
        {"action_type": "NATIVE_LAUNCH", "appId": "org.tizen.browser", "metaTag": "http://192.0.2.9:8123/x"}]
    assert sum(fast_sleep) == 5 and aioclient_mock.call_count == 10
    aioclient_mock.clear_requests()
    tv.browser_id = "org.tizen.browser"  # an older TV, as discover_apps() finds it
    aioclient_mock.get(BASE + "org.tizen.browser", json={"id": "org.tizen.browser", "visible": True})
    await tv.open_url("http://192.0.2.9:8123/x")
    assert aioclient_mock.call_count == 1


def touch_flaky(fails, remotes):
    class Flaky(FakeRemote):
        async def open(self):
            if fails:
                raise fails.pop(0)
    def factory(*a, **kw):
        remotes.append(Flaky(*a, **kw)); return remotes[-1]
    return factory


async def test_connect_retries_touch_events(hass, monkeypatch, fast_sleep):
    touch = ConnectionFailure({"event": "ms.remote.touchDisable", "data": {}})
    remotes, fails = [], [touch, ConnectionFailure({"event": "ms.remote.touchEnable"})]
    monkeypatch.setattr(tvmod, "SamsungTVWSAsyncRemote", touch_flaky(fails, remotes))
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    await tv.send_key("KEY_HOME")
    assert len(remotes) == 3 and remotes[2].sent and tv.available and fast_sleep == [0.7, 0.7]
    remotes.clear(); tv._remote = None
    fails[:] = [touch] * 5  # still blocked after five tries
    with pytest.raises(tvmod.CannotConnect):
        await tv.send_key("KEY_HOME")
    assert len(remotes) == 5 and not tv.available
    remotes.clear()
    fails[:] = [ConnectionFailure({"event": "ms.channel.timeOut"})]  # anything else: no retry
    with pytest.raises(tvmod.CannotConnect):
        await tv.send_key("KEY_HOME")
    assert len(remotes) == 1


async def test_youtube_state(hass, aioclient_mock):
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    for reply, state in (({"text": dial_xml("running")}, "running"), ({"text": dial_xml("stopped")}, "stopped"),
                         ({"text": dial_xml("hidden")}, "unknown"), ({"status": 404}, "unknown"),
                         ({"exc": TimeoutError()}, "unknown")):
        aioclient_mock.clear_requests()
        aioclient_mock.get(DIAL, **reply)
        assert await tv.youtube_state() == state
    assert aioclient_mock.mock_calls[0][3]["Origin"] == "package:com.google.android.youtube"


async def youtube_run(hass, aioclient_mock, state, post_status=201):
    """(http calls, ordered events) of play_youtube; events record the HTTP call count at each wait/key."""
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    events = []
    async def send_key(key):
        events.append(("key", key, aioclient_mock.call_count))
    tv.send_key = send_key
    aioclient_mock.get(DIAL, text=dial_xml(state))
    aioclient_mock.post(DIAL, status=post_status)
    real = asyncio.sleep
    async def sleep(delay):
        events.append(("wait", delay, aioclient_mock.call_count)); await real(0)
    with patch.object(asyncio, "sleep", sleep):
        await tv.play_youtube(VID)
    return aioclient_mock.mock_calls, events


async def test_play_youtube_cold_passes_the_profile_picker(hass, aioclient_mock):
    calls, events = await youtube_run(hass, aioclient_mock, "stopped")
    assert [(m.upper(), str(u)) for m, u, *_ in calls] == [("GET", DIAL), ("POST", DIAL), ("POST", DIAL)]
    assert events == [("wait", 8, 2), ("key", "KEY_ENTER", 2), ("wait", 3, 3), ("key", "KEY_ENTER", 3)]
    _, _, body, headers = calls[1]
    assert body == f"v={VID}&t=0"
    assert headers["Origin"] == "package:com.google.android.youtube"
    assert headers["Content-Type"] == "text/plain; charset=utf-8"


async def test_play_youtube_warm_and_errors(hass, aioclient_mock):
    calls, events = await youtube_run(hass, aioclient_mock, "running", post_status=200)
    assert [m.upper() for m, *_ in calls] == ["GET", "POST"]
    assert events == [("wait", 3, 2), ("key", "KEY_ENTER", 2)]
    aioclient_mock.clear_requests()
    with pytest.raises(HomeAssistantError, match="HTTP 403"):  # e.g. a missing Origin header
        await youtube_run(hass, aioclient_mock, "running", post_status=403)
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    aioclient_mock.clear_requests()
    for bad in ("short", "B5unCXpegAw&x", "../../etc/pa", "B5unCXpegAw\n"):
        with pytest.raises(ServiceValidationError):
            await tv.play_youtube(bad)
    assert aioclient_mock.call_count == 0


async def test_stop_youtube_dial_then_rest(hass, aioclient_mock):
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    aioclient_mock.delete(DIAL + "/run", status=200)
    await tv.stop_youtube()
    assert [(m.upper(), str(u)) for m, u, *_ in aioclient_mock.mock_calls] == [("DELETE", DIAL + "/run")]
    aioclient_mock.clear_requests()
    aioclient_mock.delete(DIAL + "/run", status=501)
    aioclient_mock.delete(BASE + "111299001912", json=True)
    await tv.stop_youtube()
    assert [str(u) for _, u, *_ in aioclient_mock.mock_calls] == [DIAL + "/run", BASE + "111299001912"]


async def test_restart_app_deletes_posts_and_waits_running(hass, aioclient_mock, monkeypatch):
    aioclient_mock.delete(BASE + "111299001912", status=500)  # not running: DELETE error must not abort
    aioclient_mock.post(BASE + "111299001912", json=True)
    aioclient_mock.get(BASE + "111299001912", json={"id": "111299001912", "running": True})
    aioclient_mock.delete(BASE + "3201606009684", status=404)
    aioclient_mock.post(BASE + "3201606009684", status=404)
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    delays, real_sleep = [], asyncio.sleep
    async def fast_sleep(delay, *a, **kw):
        delays.append(delay)
        await real_sleep(0)
    with monkeypatch.context() as m:
        m.setattr(asyncio, "sleep", fast_sleep)
        await tv.restart_app("111299001912")
        assert [c[0].upper() for c in aioclient_mock.mock_calls] == ["DELETE", "POST", "GET"]
        assert delays == [0.7, 0.5]
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

    class Unreachable(FakeRemote):
        async def open(self): raise TimeoutError
    monkeypatch.setattr(tvmod, "SamsungTVWSAsyncRemote", Unreachable)
    with pytest.raises(tvmod.CannotConnect):
        await tv.pair()
