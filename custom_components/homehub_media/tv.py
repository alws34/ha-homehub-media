"""Samsung Tizen TV client: WebSocket remote (8002), REST (8001), Wake-on-LAN."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import socket
import time
import unicodedata
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pyytlounge import EventListener, NowPlayingEvent, PlaybackStateEvent, State, YtLoungeApi
from pyytlounge.exceptions import (
    NotConnectedException,
    NotLinkedException,
    NotPairedException,
    NotSupportedException,
)
from samsungtvws import async_connection
from samsungtvws.async_remote import SamsungTVWSAsyncRemote
from samsungtvws.exceptions import ConnectionFailure, UnauthorizedError
from samsungtvws.remote import (
    ChannelEmitCommand,
    RemoteControlCommand,
    SendInputEnd,
    SendInputString,
    SendRemoteKey,
)
from websockets.exceptions import WebSocketException

from .catalog import YT_ID_RE
from .const import APP_CATALOG, CLIENT_NAME, TEXT_MAX
from .library import read_capped, why

KEY_RE = re.compile(r"KEY_[A-Z0-9_]{1,32}")
APP_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
REST_TIMEOUT = aiohttp.ClientTimeout(total=5)  # REST (8001) and DIAL (8080)
WS_SEND_SECS = 15  # one WebSocket send; a held key waits up to 10 s inside it
WS_ERRORS = (ConnectionFailure, WebSocketException, OSError)
URL_VISIBLE_SECS = 5  # wait for the browser to come to the front after a URL launch
YT_ORIGIN = "package:com.google.android.youtube"  # without it the TV's DIAL server answers 403
# ponytail: tuning knobs. A warm switch reports the new video in ~2 s; a cold start may drop the lounge session.
# whole start, one play_video's NowPlaying, DIAL screenId, lounge disconnect
YT_START_SECS, YT_ACK_SECS, YT_PAIR_SECS, YT_STOP_SECS = 20, 5, 10, 5
# what pyytlounge raises when the session or YouTube's lounge server fails (it re-raises parse errors as they are)
LOUNGE_ERRORS = (aiohttp.ClientError, TimeoutError, LookupError, ValueError, NotConnectedException,
                 NotLinkedException, NotPairedException, NotSupportedException)
_LOGGER = logging.getLogger(__name__)
FLAGS = {  # TV event -> (attribute, value)
    "ms.remote.imeStart": ("keyboard_active", True),
    "ms.remote.imeEnd": ("keyboard_active", False),
    "ms.remote.imeDone": ("keyboard_active", False),
    "ms.remote.touchEnable": ("pointer_active", True),
    "ms.remote.touchDisable": ("pointer_active", False),
}
# ponytail: while its browser is open the TV sends touch events before ms.channel.connect; samsungtvws then fails
# the connect (and leaks the socket). Drop this once samsungtvws ignores them itself.
async_connection.IGNORE_EVENTS_AT_STARTUP = (
    *async_connection.IGNORE_EVENTS_AT_STARTUP, "ms.remote.touchEnable", "ms.remote.touchDisable")


class TVRemote(SamsungTVWSAsyncRemote):
    """Hands every TV event to `on_event`, including those that arrive before the connect reply."""

    def __init__(self, *args: Any, on_event: Callable[[str], None], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._on_tv_event = on_event

    def _websocket_event(self, event: str, response: dict[str, Any]) -> None:  # startup and listen loop alike
        super()._websocket_event(event, response)
        self._on_tv_event(event)


class LoungeListener(EventListener):
    """Hands the YouTube lounge's playback events to the TV."""

    def __init__(self, on_event: Callable[[NowPlayingEvent | PlaybackStateEvent], Any]) -> None:
        super().__init__()
        self.now_playing_changed = self.playback_state_changed = on_event


class PairingDenied(HomeAssistantError):
    """The TV did not allow this client (no or revoked token)."""


class CannotConnect(HomeAssistantError):
    """The TV is off or unreachable."""


def _app_path(app_id: str) -> str:
    if not APP_ID_RE.fullmatch(app_id):
        raise ServiceValidationError("Invalid app id")
    return f"applications/{app_id}"


def _wol(mac: str) -> None:
    """Broadcast a Wake-on-LAN magic packet (blocking)."""
    raw = bytes.fromhex(re.sub(r"[:.-]", "", mac))
    if len(raw) != 6:
        raise ValueError("MAC address must be 6 bytes")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(b"\xff" * 6 + raw * 16, ("255.255.255.255", 9))


class SamsungTV:
    """Single client for one TV; the WebSocket is opened lazily and reopened when dead."""

    def __init__(self, hass: HomeAssistant, host: str, token: str | None, mac: str | None,
                 port: int = 8002, on_change: Callable[[], None] | None = None, lounge_auth: dict | None = None) -> None:
        self.hass, self.host, self.token, self.mac, self.port = hass, host, token, mac, port
        self.lounge_auth = lounge_auth  # YouTube lounge pairing (AuthState.serialize()); Hub persists it
        self._on_change = on_change
        self.available = self.keyboard_active = self.pointer_active = False
        self.browser_id = APP_CATALOG["browser"]["ids"][0]  # replaced by discover_apps()
        self._remote: SamsungTVWSAsyncRemote | None = None
        self._lock = asyncio.Lock()
        self._text_sent = False
        self._yt_video: str | None = None  # the video to loop, set from play_youtube() to stop_youtube()
        self._yt_now: str | None = None  # the video YouTube last reported
        self._yt_from0 = False  # YouTube resumes where it was last watched: seek to 0:00 once it plays
        self._yt_api: YtLoungeApi | None = None
        self._yt_sub: asyncio.Task | None = None  # the lounge session's event stream
        self._yt_lock = asyncio.Lock()
        self._yt_started = asyncio.Event()

    def _notify(self) -> None:
        if self._on_change:
            try:
                self._on_change()
            except Exception:  # a listener bug must not kill the WebSocket receive task
                _LOGGER.exception("TV change listener failed")

    def _set_flag(self, attr: str, value: bool) -> None:
        if getattr(self, attr) != value:
            setattr(self, attr, value)
            self._notify()

    def _on_event(self, event: str) -> None:
        if flag := FLAGS.get(event):
            if flag[0] == "keyboard_active":
                self._text_sent = False  # new IME session: broadcast textReceived again
            self._set_flag(*flag)

    async def _send(self, *commands: Any) -> None:
        async with self._lock:
            try:
                if self._remote is None or not self._remote.is_alive():
                    if self._remote is not None:  # socket died since the last command
                        self._set_flag("available", False)
                    self._remote = TVRemote(self.host, token=self.token, port=self.port, timeout=5, key_press_delay=0,
                                            name=CLIENT_NAME, on_event=self._on_event)
                    async with asyncio.timeout(10):
                        await self._remote.start_listening()  # opens the socket itself
                    if self._remote.token != self.token:  # TV rotated the token; Hub persists it
                        self.token = self._remote.token
                        self._notify()
                async with asyncio.timeout(WS_SEND_SECS):  # a hung TV must not hold the lock
                    await self._remote.send_commands(list(commands))
            except WS_ERRORS as err:
                self._remote = None
                self._set_flag("available", False)
                if isinstance(err, UnauthorizedError):
                    raise PairingDenied("The TV refused the token, pair again") from err
                raise CannotConnect("TV is off or unreachable") from err
        self._set_flag("available", True)

    async def _rest(self, method: str, path: str = "") -> Any:
        """None on 404; parsed JSON for GET, True for other methods."""
        try:
            async with async_get_clientsession(self.hass).request(
                method, f"http://{self.host}:8001/api/v2/{path}", timeout=REST_TIMEOUT
            ) as resp:
                if resp.status == 404:
                    return None
                if resp.status >= 400:
                    raise HomeAssistantError(f"TV returned HTTP {resp.status}")
                return json.loads(await read_capped(resp)) if method == "GET" else True
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise CannotConnect("TV is off or unreachable") from err

    async def pair(self) -> str:
        try:
            async with (
                asyncio.timeout(31),
                SamsungTVWSAsyncRemote(self.host, port=self.port, timeout=31, name=CLIENT_NAME) as remote,
            ):
                await remote.open()
                token = remote.token
        except (ConnectionFailure, WebSocketException) as err:
            raise PairingDenied("Pairing was not allowed on the TV") from err
        except OSError as err:  # includes TimeoutError
            raise CannotConnect("TV is off or unreachable") from err
        if not token:
            raise PairingDenied("The TV did not issue a token")
        self.token = token
        return token

    async def device_info(self) -> dict:
        """REST device info; also refreshes `available` (REST answers in standby, so use PowerState)."""
        try:
            device = (await self._rest("GET"))["device"]
        except (CannotConnect, TypeError, KeyError) as err:
            self._set_flag("available", False)
            raise CannotConnect("TV is off or unreachable") from err
        self._set_flag("available", device.get("PowerState", "on") == "on")
        return device

    async def send_key(self, key: str, hold_secs: float = 0) -> None:
        if not KEY_RE.fullmatch(key) or not 0 <= hold_secs <= 10:
            raise ServiceValidationError("Invalid TV key or hold time")
        await self._send(*(SendRemoteKey.hold(key, hold_secs) if hold_secs else [SendRemoteKey.click(key)]))

    async def launch(self, app_id: str, meta_tag: str = "", action_type: str = "DEEP_LINK") -> None:
        """REST first (fast); the WebSocket for a metaTag or when REST itself fails. The TV never replies to a
        WebSocket launch (verified on Tizen 6.5), so that one is fire-and-forget."""
        path = _app_path(app_id)
        if not meta_tag:
            try:
                launched = await self._rest("POST", path)
            except HomeAssistantError:  # REST down or erroring: the WebSocket may still work
                pass
            else:
                if launched is None:
                    raise ServiceValidationError(f"{app_id} is not installed on the TV")
                return
        await self._send(ChannelEmitCommand.launch_app(app_id, action_type, meta_tag))

    async def app_info(self, app_id: str) -> dict | None:
        """REST app status ({"running": bool, "visible": bool, ...}); None when not installed."""
        return await self._rest("GET", _app_path(app_id))

    async def open_url(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc or len(url) > 2048:
            raise ServiceValidationError("Only http(s) URLs up to 2048 characters can be opened")
        await self.launch("org.tizen.browser", url, "NATIVE_LAUNCH")
        # 2021+ Samsung TVs start the browser hidden for a remote URL launch, so nothing shows
        for _ in range(URL_VISIBLE_SECS * 2):
            await asyncio.sleep(0.5)
            if (await self.app_info(self.browser_id) or {}).get("visible"):
                return
        raise HomeAssistantError("This TV does not allow opening web pages remotely")

    # ---- YouTube over DIAL (port 8080) -----------------------------------------------------------------

    async def _dial(self, method: str, data: str | None = None, run: bool = False) -> tuple[int, str]:
        url = f"http://{self.host}:8080/ws/apps/YouTube{'/run' if run else ''}"
        headers = {"Origin": YT_ORIGIN, "Content-Type": "text/plain; charset=utf-8"}
        try:
            async with async_get_clientsession(self.hass).request(
                method, url, data=data, headers=headers, timeout=REST_TIMEOUT
            ) as resp:
                return resp.status, (await read_capped(resp)).decode(errors="replace")
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise CannotConnect("TV is off or unreachable") from err

    async def _screen_id(self) -> str:
        """YouTube's lounge screen id; DIAL lists it only while YouTube runs."""
        for _ in range(YT_PAIR_SECS * 2):
            with contextlib.suppress(CannotConnect):
                if found := re.search(r"<screenId>\s*([^<\s]+)\s*</screenId>", (await self._dial("GET"))[1]):
                    return found[1]
            await asyncio.sleep(0.5)
        raise HomeAssistantError("YouTube on the TV did not offer a screen id to pair with")

    async def _lounge(self) -> YtLoungeApi | None:
        """The connected lounge session while a video is set: kept, reconnected, or paired again (saved auth
        first; a fresh pairing needs YouTube running)."""
        async with self._yt_lock:
            if not self._yt_video or (self._yt_api and self._yt_api.connected()):
                return self._yt_api
            if self._yt_sub:
                self._yt_sub.cancel()
            # pyytlounge logs the lounge token: its logger goes through __init__'s secret filter
            api = YtLoungeApi(CLIENT_NAME, LoungeListener(self._yt_event), logging.getLogger("pyytlounge"))
            api.session = async_get_clientsession(self.hass)  # HA's shared session: nothing to close
            ok = False
            if self.lounge_auth:
                with contextlib.suppress(*LOUNGE_ERRORS):
                    api.load_auth_state(self.lounge_auth)
                    ok = await api.refresh_auth() and await api.connect()
            if not ok and not (await api.pair_with_screen_id(await self._screen_id()) and await api.connect()):
                raise CannotConnect("YouTube on the TV refused the lounge connection")
            self._yt_api = api
            self._yt_sub = self.hass.async_create_background_task(self._subscribe(api), "homehub_media_youtube")
            if (auth := api.auth.serialize()) != self.lounge_auth:  # new pairing or refreshed token
                self.lounge_auth = auth
                self._notify()
            return api

    @staticmethod
    async def _subscribe(api: YtLoungeApi) -> None:
        with contextlib.suppress(*LOUNGE_ERRORS):  # a dropped session reconnects on the next _lounge()
            await api.subscribe()

    async def _yt_event(self, event: NowPlayingEvent | PlaybackStateEvent) -> None:
        if isinstance(event, NowPlayingEvent):
            self._yt_now = event.video_id
            if event.video_id and event.video_id == self._yt_video:
                self._yt_started.set()
        if (self._yt_from0 and self._yt_video and self._yt_now == self._yt_video and event.state is State.Playing
                and (event.current_time or 0) > 3):
            self._yt_from0 = False
            with contextlib.suppress(*LOUNGE_ERRORS):
                await self._yt_api.seek_to(0)
        # YouTube's "ended" (code 0, State.Buffering) at the end: play it again (seek_to(0) does nothing then)
        if (self._yt_video and self._yt_now == self._yt_video and event.state is State.Buffering
                and event.duration and (event.current_time or 0) >= event.duration):
            self._yt_from0 = True
            with contextlib.suppress(*LOUNGE_ERRORS):
                await self._yt_api.play_video(self._yt_video)

    async def play_youtube(self, video_id: str, app_id: str) -> None:
        """Play a video fullscreen from 0:00 and loop it until stop_youtube(). YouTube takes lounge commands
        only while it is open, and a cold start may drop the session: retry until it reports the video."""
        if not YT_ID_RE.fullmatch(video_id):
            raise ServiceValidationError("Invalid YouTube video id")
        self._yt_video, self._yt_from0 = video_id, True
        if self._yt_now == video_id and self._yt_api and self._yt_api.connected():  # already on: no NowPlaying
            with contextlib.suppress(*LOUNGE_ERRORS):
                if await self._yt_api.seek_to(0):
                    return
        # DIAL, not REST: a REST-launched YouTube without a video quits after ~5 s (verified on Tizen 6.5)
        if (not (await self.app_info(app_id) or {}).get("visible")
                and (await self._dial("POST", ""))[0] not in (200, 201)):
            await self.launch(app_id)
        deadline = time.monotonic() + YT_START_SECS
        while True:
            try:
                async with asyncio.timeout(YT_START_SECS):  # YouTube's servers hanging must not hold the locks
                    api = await self._lounge()
                    await api.set_auto_play_mode(False)
                    self._yt_started.clear()
                    if await api.play_video(video_id):  # False: the session ended, the next _lounge() reconnects
                        async with asyncio.timeout(YT_ACK_SECS):
                            await self._yt_started.wait()
                        return
                err: Exception = CannotConnect("YouTube lounge session ended")
            except (*LOUNGE_ERRORS, HomeAssistantError) as exc:
                err = exc
            _LOGGER.debug("YouTube start attempt failed: %s", why(err))  # never the URL: it holds the token
            # a session opened before a cold-started app was ready stays "connected" but ignores plays: open a new one
            async with self._yt_lock:
                await self._drop_lounge()
            if time.monotonic() > deadline:
                raise HomeAssistantError("YouTube on the TV did not start the video") from None
            await asyncio.sleep(1)

    async def youtube_keepalive(self) -> None:
        """Reconnect a dropped lounge session so the video keeps looping."""
        try:
            async with asyncio.timeout(YT_START_SECS):
                await self._lounge()
        except (*LOUNGE_ERRORS, HomeAssistantError) as err:
            _LOGGER.debug("YouTube lounge reconnect failed: %s", why(err))

    async def _drop_lounge(self) -> None:
        """End the lounge session (caller holds _yt_lock); the next _lounge() opens a new one."""
        api, sub, self._yt_api, self._yt_sub, self._yt_now = self._yt_api, self._yt_sub, None, None, None
        if sub:
            sub.cancel()
            await asyncio.wait([sub])
        if api and api.connected():
            with contextlib.suppress(*LOUNGE_ERRORS):  # incl. TimeoutError: never hold the lock
                async with asyncio.timeout(YT_STOP_SECS):
                    await api.disconnect()

    async def stop_youtube(self, app_id: str) -> None:
        """End the lounge session, then close YouTube over DIAL; REST (YouTube's Tizen `app_id`) when DIAL fails."""
        async with self._yt_lock:
            self._yt_video = None
            await self._drop_lounge()
        with contextlib.suppress(CannotConnect):
            if (await self._dial("DELETE", run=True))[0] in (200, 204, 404):  # 404: not running
                return
        await self._rest("DELETE", _app_path(app_id))

    async def restart_app(self, app_id: str) -> None:
        path = _app_path(app_id)
        try:  # DELETE may 404/fail when the app isn't running; the POST below decides
            await self._rest("DELETE", path)
        except CannotConnect:
            raise
        except HomeAssistantError:
            pass
        await asyncio.sleep(0.7)
        if await self._rest("POST", path) is None:
            raise HomeAssistantError(f"App {app_id} is not installed")
        for _ in range(10):
            await asyncio.sleep(0.5)
            if (await self._rest("GET", path) or {}).get("running"):
                return
        raise HomeAssistantError(f"App {app_id} did not restart")

    async def discover_apps(self) -> dict[str, str]:
        async def first_installed(ids: list[str]) -> str | None:
            for app_id in ids:
                if await self._rest("GET", _app_path(app_id)) is not None:
                    return app_id
            return None

        found = await asyncio.gather(*(first_installed(app["ids"]) for app in APP_CATALOG.values()))
        apps = {key: app_id for key, app_id in zip(APP_CATALOG, found) if app_id}
        self.browser_id = apps.get("browser", self.browser_id)
        return apps

    async def send_text(self, text: str) -> None:
        if not 0 < len(text) <= TEXT_MAX or any(unicodedata.category(c)[0] == "C" for c in text):
            raise ServiceValidationError(f"Text must be 1-{TEXT_MAX} printable characters")
        first = [] if self._text_sent else [ChannelEmitCommand.text_received()]
        await self._send(*first, SendInputString.send(text), SendInputEnd())
        self._text_sent = True

    async def pointer_move(self, dx: float, dy: float) -> None:
        x, y = (round(max(-500, min(500, d))) for d in (dx, dy))
        await self._send(RemoteControlCommand(
            {"Cmd": "Move", "Position": {"x": x, "y": y, "Time": "0"}, "TypeOfRemote": "ProcessMouseDevice"}))

    async def pointer_click(self) -> None:
        await self._send(RemoteControlCommand({"Cmd": "LeftClick", "TypeOfRemote": "ProcessMouseDevice"}))

    async def wake(self) -> None:
        if not self.mac:
            return
        try:
            await self.hass.async_add_executor_job(_wol, self.mac)
        except (ValueError, OSError) as err:
            raise HomeAssistantError("Wake-on-LAN failed") from err

    async def close(self) -> None:
        async with self._yt_lock:  # unload: no lounge subscription task outlives the entry
            self._yt_video = None
            await self._drop_lounge()
        if remote := self._remote:
            self._remote = None
            await remote.close()
