import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pyytlounge import NowPlayingEvent, PlaybackStateEvent
from pyytlounge.exceptions import NotConnectedException
from samsungtvws import async_connection
from samsungtvws import event as samsungtvws_event
from samsungtvws.exceptions import UnauthorizedError
from websockets.exceptions import ConnectionClosed
from websockets.protocol import State
from yarl import URL

from custom_components.homehub_media import tv as tvmod
from custom_components.homehub_media.const import APP_CATALOG
from custom_components.homehub_media.library import BODY_MAX

BASE = "http://192.0.2.1:8001/api/v2/applications/"
DIAL = "http://192.0.2.1:8080/ws/apps/YouTube"
VID = "B5unCXpegAw"


def dial_xml(state, extra=""):
    return (f'<?xml version="1.0" encoding="UTF-8"?><service xmlns="urn:dial-multiscreen-org:schemas:dial">'
            f"<name>YouTube</name><options allowStop=\"true\"/><state>{state}</state>"
            f"<additionalData>{extra}</additionalData></service>")


class FakeRemote:
    """tv.TVRemote stand-in; cb(event, response) is a frame from the TV."""
    def __init__(self, *a, **kw):
        self.sent, self.kw, self.token = [], kw, kw.get("token")
    async def __aenter__(self): return self
    async def __aexit__(self, *exc): ...
    async def open(self): ...
    async def start_listening(self):
        await self.open()  # samsungtvws' start_listening opens the socket itself
    def cb(self, event, _response):
        self.kw["on_event"](event)
    def is_alive(self): return True
    async def send_command(self, cmd, key_press_delay=None): await self.send_commands([cmd])
    async def send_commands(self, cmds, key_press_delay=None): self.sent.extend(cmds)  # the TV never acks
    async def close(self): ...


@pytest.fixture
def fake_remote(monkeypatch):
    holder = {}
    def factory(*a, **kw):
        holder["r"] = FakeRemote(*a, **kw); return holder["r"]
    monkeypatch.setattr(tvmod, "TVRemote", factory)
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
    monkeypatch.setattr(tvmod, "TVRemote", Revoked)
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
    monkeypatch.setattr(tvmod, "TVRemote", factory)
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
    monkeypatch.setattr(tvmod, "TVRemote", Rotate)
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
    monkeypatch.setattr(tvmod, "TVRemote", Boom)
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
    with pytest.raises(ServiceValidationError, match="3201606009684 is not installed on the TV"):
        await tv.launch("3201606009684")
    assert "r" not in fake_remote
    aioclient_mock.post(BASE + "3201907018807", exc=TimeoutError())
    aioclient_mock.post(BASE + "3201901017640", status=500)
    async with asyncio.timeout(1):  # fire-and-forget: no reply is awaited
        await tv.launch("3201907018807")  # REST unreachable -> WebSocket launch
        await tv.launch("3201901017640")  # REST erroring -> WebSocket launch
    assert [c.as_dict()["params"]["data"] for c in fake_remote["r"].sent] == [
        {"action_type": "DEEP_LINK", "appId": "3201907018807", "metaTag": ""},
        {"action_type": "DEEP_LINK", "appId": "3201901017640", "metaTag": ""}]
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


class FakeSocket:
    """websockets ClientConnection stand-in under the real samsungtvws: replays TV frames, records sends."""
    def __init__(self, *frames):
        self.state, self.sent, self.frames = State.OPEN, [], asyncio.Queue()
        for frame in frames:
            self.push(frame)
    def push(self, frame): self.frames.put_nowait(json.dumps(frame))
    async def recv(self):
        if (frame := await self.frames.get()) is None:
            raise ConnectionClosed(None, None)
        return frame
    async def send(self, data): self.sent.append(data)
    async def close(self):
        self.state = State.CLOSED
        self.frames.put_nowait(None)


async def test_touch_events_before_the_connect_reply_are_not_a_failure(hass, monkeypatch):
    # the browser is open: the TV says touchDisable before ms.channel.connect (samsungtvws alone fails here)
    sock = FakeSocket({"event": "ms.remote.touchDisable", "data": {}},
                      {"event": "ms.channel.connect", "data": {"clients": [], "id": "x", "token": "tok"}})
    monkeypatch.setattr(async_connection, "connect", AsyncMock(return_value=sock))
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    tv.pointer_active = True
    await tv.send_key("KEY_HOME")
    assert tv.available and not tv.pointer_active and "KEY_HOME" in sock.sent[0]
    sock.push({"event": "ms.remote.touchEnable", "data": {}})  # later events: the same handler, once
    for _ in range(3):
        await asyncio.sleep(0)
    assert tv.pointer_active
    await tv.close()
    monkeypatch.setattr(async_connection, "IGNORE_EVENTS_AT_STARTUP", samsungtvws_event.IGNORE_EVENTS_AT_STARTUP)
    async_connection.connect.return_value = FakeSocket({"event": "ms.remote.touchDisable", "data": {}})
    with pytest.raises(tvmod.CannotConnect):  # samsungtvws' own list: the failure this works around
        await tv.send_key("KEY_HOME")


class FakeLounge:
    """pyytlounge.YtLoungeApi stand-in. `on_play(lounge, video_id)` is what the TV does with a play_video."""
    made = None  # this test's lounges, reset by the fixture
    refresh_ok = True
    on_play = None

    def __init__(self, name, listener, logger=None):
        self.listener, self.loaded, self.paired, self.played, self.autoplay = listener, None, None, [], None
        self.logger = logger
        self.screen, self.token, self.live, self.disconnected, self.seeks = None, None, False, False, []
        self.auth = SimpleNamespace(serialize=lambda: {"version": 0, "screenId": self.screen,
                                                       "loungeIdToken": self.token, "refreshToken": None, "expiry": None})
        FakeLounge.made.append(self)
    def load_auth_state(self, data):
        self.loaded, self.screen = data, data["screenId"]  # KeyError on store_auth_state()'s keys, like the library
    async def refresh_auth(self):
        self.token = f"lt{len(FakeLounge.made)}"
        return self.refresh_ok
    async def pair_with_screen_id(self, screen_id):
        self.paired = self.screen = screen_id
        self.token = "fresh"
        return True
    async def connect(self):
        self.live = True
        return True
    def connected(self): return self.live
    async def subscribe(self): await asyncio.Event().wait()
    async def set_auto_play_mode(self, enabled): self.autoplay = enabled; return True
    async def play_video(self, video_id):
        if not self.live:
            raise NotConnectedException("Not connected")
        self.played.append(video_id)
        if FakeLounge.on_play:
            await FakeLounge.on_play(self, video_id)
        return True
    async def seek_to(self, time): self.seeks.append(time); return True
    async def disconnect(self): self.live, self.disconnected = False, True; return True


def now_playing(video_id):
    return NowPlayingEvent({"videoId": video_id, "state": "3", "currentTime": "0", "duration": "300"})


ENDED = PlaybackStateEvent({"currentTime": "300", "duration": "300", "state": "0"})  # YouTube's "ended"


async def tv_plays(lounge, video_id):
    await lounge.listener.now_playing_changed(now_playing(video_id))


@pytest.fixture
def lounge(monkeypatch, aioclient_mock):
    """YouTube open on the TV (REST visible, DIAL with a screen id) and a lounge that plays what it is asked."""
    FakeLounge.made, FakeLounge.refresh_ok, FakeLounge.on_play = [], True, tv_plays
    monkeypatch.setattr(tvmod, "YtLoungeApi", FakeLounge)
    aioclient_mock.get(BASE + "111299001912", json={"running": True, "visible": True})
    aioclient_mock.get(DIAL, text=dial_xml("running", "<screenId>SCREEN</screenId>"))
    aioclient_mock.delete(DIAL + "/run", status=200)
    return FakeLounge


async def test_play_youtube_pairs_through_the_screen_id_and_persists_the_auth(hass, aioclient_mock, lounge):
    changes = []
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None, on_change=lambda: changes.append(1))
    await tv.play_youtube(VID, "111299001912")
    api, = lounge.made
    assert api.paired == "SCREEN" and api.played == [VID] and api.autoplay is False
    assert api.logger.name == "pyytlounge"  # the logger __init__'s secret filter covers
    assert tv.lounge_auth == api.auth.serialize() == {"version": 0, "screenId": "SCREEN", "loungeIdToken": "fresh",
                                                      "refreshToken": None, "expiry": None}  # never store_auth_state()
    assert changes == [1]  # the Hub persists it
    assert aioclient_mock.mock_calls[1][3]["Origin"] == "package:com.google.android.youtube"
    await tv.play_youtube("fireFIREfir", "111299001912")  # warm: the same session, nothing launched
    assert lounge.made == [api] and api.played == [VID, "fireFIREfir"]
    assert [m.upper() for m, *_ in aioclient_mock.mock_calls] == ["GET", "GET", "GET"]  # visible, DIAL, visible
    await tv.stop_youtube("111299001912")


async def test_play_youtube_saved_auth_then_pairs_again_once(hass, aioclient_mock, lounge):
    saved = {"version": 0, "screenId": "OLD", "loungeIdToken": "x", "refreshToken": None, "expiry": None}
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None, lounge_auth=saved)
    await tv.play_youtube(VID, "111299001912")
    api, = lounge.made
    assert api.loaded == saved and api.paired is None and tv.lounge_auth["loungeIdToken"] == "lt1"  # refreshed
    await tv.stop_youtube("111299001912")
    lounge.refresh_ok = False  # YouTube forgot this remote
    await tv.play_youtube(VID, "111299001912")
    assert lounge.made[-1].paired == "SCREEN" and tv.lounge_auth["screenId"] == "SCREEN"
    await tv.stop_youtube("111299001912")


async def test_youtube_loops_on_ended_until_stopped(hass, aioclient_mock, lounge):
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    await tv.play_youtube(VID, "111299001912")
    api = lounge.made[0]
    await api.listener.playback_state_changed(PlaybackStateEvent({"currentTime": "120", "duration": "300", "state": "1"}))
    await api.listener.playback_state_changed(ENDED)
    assert api.played == [VID, VID]  # played again from 0:00, not seek_to(0)
    await api.listener.now_playing_changed(now_playing("otherVIDEO1"))  # the user picked another video on the TV
    await api.listener.playback_state_changed(ENDED)
    assert api.played == [VID, VID]
    await tv.stop_youtube("111299001912")
    assert api.disconnected and aioclient_mock.mock_calls[-1][:2] == ("DELETE", URL(DIAL + "/run"))
    await api.listener.now_playing_changed(now_playing(VID))
    await api.listener.playback_state_changed(ENDED)
    assert api.played == [VID, VID]  # no loop after the stop


async def test_play_youtube_cold_start_retries_after_the_session_drops(hass, aioclient_mock, lounge, monkeypatch):
    aioclient_mock.clear_requests()
    aioclient_mock.get(BASE + "111299001912", json={"running": False, "visible": False})
    aioclient_mock.post(DIAL, status=201)
    aioclient_mock.get(DIAL, text=dial_xml("running", "<screenId>SCREEN</screenId>"))
    aioclient_mock.delete(DIAL + "/run", status=200)
    async def app_starting(api, video_id):  # accepted, then the app start drops the session: no NowPlaying
        api.live = False
        lounge.on_play = tv_plays
    lounge.on_play = app_starting
    monkeypatch.setattr(tvmod, "YT_ACK_SECS", 0.01)
    sleeps, real = [], asyncio.sleep
    async def sleep(delay):
        sleeps.append(delay); await real(0)
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    with patch.object(asyncio, "sleep", sleep):
        await tv.play_youtube(VID, "111299001912")
    first, second = lounge.made
    assert first.played == second.played == [VID] and sleeps == [1]
    assert second.loaded == first.auth.serialize() and second.paired is None  # reconnected with the saved auth
    calls = [c[:2] for c in aioclient_mock.mock_calls]
    assert ("POST", URL(DIAL)) in calls and ("POST", URL(BASE + "111299001912")) not in calls  # DIAL, not REST
    await tv.stop_youtube("111299001912")


async def test_play_youtube_drops_a_session_that_ignores_plays(hass, aioclient_mock, lounge, monkeypatch):
    """Connected before the cold-started app was ready: YouTube accepts plays on it but never plays them."""
    async def deaf(api, video_id):  # still connected, never reports the video
        lounge.on_play = tv_plays
    lounge.on_play = deaf
    monkeypatch.setattr(tvmod, "YT_ACK_SECS", 0.01)
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    with patch.object(asyncio, "sleep", AsyncMock()):
        await tv.play_youtube(VID, "111299001912")
    first, second = lounge.made
    assert first.disconnected and second.played == [VID]  # a fresh session, not the deaf one again
    await tv.stop_youtube("111299001912")


async def test_play_youtube_from_0_and_same_video_again(hass, aioclient_mock, lounge):
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    await tv.play_youtube(VID, "111299001912")
    api = lounge.made[0]
    resumed = PlaybackStateEvent({"currentTime": "680", "duration": "3000", "state": "1"})
    await api.listener.playback_state_changed(resumed)  # YouTube resumed where it was last watched
    await api.listener.playback_state_changed(resumed)
    assert api.seeks == [0]  # once
    await tv.play_youtube(VID, "111299001912")  # the same mode again: no NowPlaying would come, so no wait
    assert api.played == [VID] and api.seeks == [0, 0]
    await tv.stop_youtube("111299001912")


async def test_play_youtube_gives_up_and_validates(hass, aioclient_mock, lounge, monkeypatch):
    lounge.on_play = None  # accepted, never reported
    monkeypatch.setattr(tvmod, "YT_ACK_SECS", 0.01)
    monkeypatch.setattr(tvmod, "YT_START_SECS", 0)
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    with pytest.raises(HomeAssistantError, match="did not start"):
        await tv.play_youtube(VID, "111299001912")
    aioclient_mock.mock_calls.clear()
    for bad in ("short", "B5unCXpegAw&x", "../../etc/pa", "B5unCXpegAw\n"):
        with pytest.raises(ServiceValidationError):
            await tv.play_youtube(bad, "111299001912")
    assert aioclient_mock.call_count == 0
    await tv.stop_youtube("111299001912")


async def test_stop_youtube_dial_then_rest(hass, aioclient_mock):
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    aioclient_mock.delete(DIAL + "/run", status=200)
    await tv.stop_youtube("111299001912")
    assert [(m.upper(), str(u)) for m, u, *_ in aioclient_mock.mock_calls] == [("DELETE", DIAL + "/run")]
    aioclient_mock.clear_requests()
    aioclient_mock.delete(DIAL + "/run", status=501)
    aioclient_mock.delete(BASE + "3201234567890", json=True)
    await tv.stop_youtube("3201234567890")  # the id the TV's app discovery found
    assert [str(u) for _, u, *_ in aioclient_mock.mock_calls] == [DIAL + "/run", BASE + "3201234567890"]


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


async def test_rest_and_dial_answers_are_size_capped(hass, aioclient_mock):
    huge = b'{"device": {"PowerState": "on"}}' + b" " * BODY_MAX
    aioclient_mock.get("http://192.0.2.1:8001/api/v2/", content=huge)
    aioclient_mock.get(DIAL, content=b"<screenId>SCREEN</screenId>" + b" " * BODY_MAX)
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    with pytest.raises(tvmod.CannotConnect):
        await tv.device_info()
    with pytest.raises(tvmod.CannotConnect):
        await tv._dial("GET")


async def test_close_ends_the_lounge_session(hass, aioclient_mock, lounge):
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    await tv.play_youtube(VID, "111299001912")
    sub, api = tv._yt_sub, lounge.made[0]
    await tv.close()  # unload: the subscription task is cancelled and awaited, the session disconnected
    assert sub.done() and api.disconnected and tv._yt_api is tv._yt_sub is tv._yt_video is None


async def test_a_hung_lounge_disconnect_does_not_hold_the_lock(hass, aioclient_mock, lounge, monkeypatch):
    monkeypatch.setattr(tvmod, "YT_STOP_SECS", 0.01)
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    await tv.play_youtube(VID, "111299001912")
    async def hang(): await asyncio.Event().wait()
    lounge.made[0].disconnect = hang
    async with asyncio.timeout(5):
        await tv.stop_youtube("111299001912")
    assert not tv._yt_lock.locked() and tv._yt_api is None


async def test_a_hung_websocket_send_does_not_hold_the_lock(hass, fake_remote, monkeypatch):
    monkeypatch.setattr(tvmod, "WS_SEND_SECS", 0.01)
    tv = tvmod.SamsungTV(hass, "192.0.2.1", "tok", None)
    await tv.send_key("KEY_HOME")
    async def hang(cmds, key_press_delay=None): await asyncio.Event().wait()
    fake_remote["r"].send_commands = hang
    async with asyncio.timeout(5):
        with pytest.raises(tvmod.CannotConnect):
            await tv.send_key("KEY_HOME")
    assert not tv._lock.locked() and not tv.available
