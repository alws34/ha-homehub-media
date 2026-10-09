#!/usr/bin/env python3
"""Home Hub Media Hub coordinator with keyboardless Firefox control.

Every request must come from the IP allowlist, name a configured Host and carry
the bearer token; request bodies must be JSON. Opening a Media Hub app starts
its dedicated browser user service (Home Assistant stops Ambience first).
Remote commands are accepted only while one approved service is active and are
restricted to a fixed key allowlist. No arbitrary URL can be supplied by a
caller.
"""

from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import logging
import math
import re
import subprocess
import threading
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LOGGER = logging.getLogger("homehub-media-hub")
MAX_REQUEST_BYTES = 4096
MAX_CONNECTIONS = 16  # for allowlisted peers
MAX_UNLISTED_CONNECTIONS = 2  # everyone else: enough for their 403, too few to starve Home Assistant
TOKEN_RE = re.compile(r"[\x21-\x7e]{32,}")
CLIENT_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")

# X11 keysyms accepted by Mutter's RemoteDesktop Session interface.
KEYSYM = {
    "space": 0x0020,
    "tab": 0xFF09,
    "return": 0xFF0D,
    "escape": 0xFF1B,
    "home": 0xFF50,
    "left": 0xFF51,
    "up": 0xFF52,
    "right": 0xFF53,
    "down": 0xFF54,
    "control_l": 0xFFE3,
    "alt_l": 0xFFE9,
    "shift_l": 0xFFE1,
    "backspace": 0xFF08,
}

MAX_TEXT_LENGTH = 100
SAFE_TEXT = re.compile(r"^[\x20-\x7e]{1,100}$")

REMOTE_COMMANDS = frozenset(
    {
        "up",
        "down",
        "left",
        "right",
        "tab",
        "select",
        "back",
        "play_pause",
        "stop",
        "home",
        "enter",
        "backspace",
        "clear",
    }
)

# ``shift_tab`` is intentionally internal.  The public API remains the fixed
# allowlist above; Coordinator.remote() resolves browser navigation to this
# command only for clients configured with "tab_navigation": true.
INPUT_COMMANDS = REMOTE_COMMANDS | frozenset({"shift_tab"})

# Arr-style web applications use standard browser focus navigation rather than
# the TV-style arrow navigation implemented by Jellyfin.  Keep left/right as
# browser previous/next focus, but preserve native up/down so an open select
# menu can move through its own options.  The optional pointer pad provides
# precise spatial navigation where browser focus order is insufficient.
BROWSER_FOCUS_COMMANDS = {
    "left": "shift_tab",
    "right": "tab",
}

MAX_POINTER_MOTION = 160.0
MAX_POINTER_SCROLL = 240.0
POINTER_BUTTONS = {"left": 0x110, "right": 0x111}
POINTER_AXIS_FINISH = 0x01


class CoordinatorError(RuntimeError):
    """An expected failure safe to return without secrets."""


@dataclass(frozen=True)
class ClientConfig:
    unit: str
    home_url: str
    tab_navigation: bool = False


@dataclass(frozen=True)
class Config:
    listen_host: str
    listen_port: int
    allowed_clients: frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address]
    clients: dict[str, ClientConfig]
    auth_token: str = field(repr=False)
    allowed_hosts: frozenset[str] = frozenset()  # empty: default_hosts() of the bound address

    @classmethod
    def from_dict(cls, raw: dict) -> Config:
        """Validate a configuration mapping. Port 0 (ephemeral) is accepted here for tests only."""
        try:
            listen_host = str(ipaddress.ip_address(raw["listen_host"]))
            listen_port = int(raw["listen_port"])
            allowed_clients = frozenset(
                ipaddress.ip_address(value) for value in raw["allowed_clients"]
            )
            raw_clients = raw["clients"]
            auth_token = raw["auth_token"]
            allowed_hosts = raw.get("allowed_hosts", [])
        except (KeyError, TypeError, ValueError) as exc:
            raise CoordinatorError(f"Invalid configuration: {exc}") from exc

        if not 0 <= listen_port <= 65535:
            raise CoordinatorError("listen_port must be between 1 and 65535")
        if not allowed_clients:
            raise CoordinatorError("allowed_clients must not be empty")
        if not isinstance(auth_token, str) or not TOKEN_RE.fullmatch(auth_token):
            raise CoordinatorError("auth_token must be at least 32 printable ASCII characters without spaces")
        if not isinstance(allowed_hosts, list) or not all(isinstance(h, str) and h for h in allowed_hosts):
            raise CoordinatorError("allowed_hosts must be a list of host[:port] names")
        if not isinstance(raw_clients, dict) or not raw_clients:
            raise CoordinatorError("clients must be a non-empty object")
        clients: dict[str, ClientConfig] = {}
        for mode, raw_client in raw_clients.items():
            if not CLIENT_RE.fullmatch(mode):
                raise CoordinatorError("client names must be lowercase slugs")
            if not isinstance(raw_client, dict):
                raise CoordinatorError(f"client configuration for {mode} is invalid")
            unit = str(raw_client.get("unit", ""))
            home_url = str(raw_client.get("home_url", ""))
            if not unit.endswith(".service") or "/" in unit:
                raise CoordinatorError(f"client unit for {mode} is invalid")
            parsed_home = urllib.parse.urlsplit(home_url)
            if (
                parsed_home.scheme != "http"
                or parsed_home.hostname not in {"127.0.0.1", "localhost"}
                or not parsed_home.path
            ):
                raise CoordinatorError(f"client home URL for {mode} must be a loopback HTTP URL")
            tab_navigation = raw_client.get("tab_navigation", False)
            if not isinstance(tab_navigation, bool):
                raise CoordinatorError(f"client tab_navigation for {mode} must be true or false")
            clients[mode] = ClientConfig(unit=unit, home_url=home_url, tab_navigation=tab_navigation)
        return cls(
            listen_host=listen_host,
            listen_port=listen_port,
            allowed_clients=allowed_clients,
            clients=clients,
            auth_token=auth_token,
            allowed_hosts=frozenset(h.lower() for h in allowed_hosts),
        )


def load_config(path: Path) -> Config:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CoordinatorError(f"Could not load configuration: {exc}") from exc
    config = Config.from_dict(raw)
    if config.listen_port == 0:
        raise CoordinatorError("listen_port must be between 1 and 65535")
    return config


def default_hosts(host: str, port: int) -> frozenset[str]:
    """Host header values accepted when allowed_hosts is not configured."""
    names = {host, "localhost", "127.0.0.1"} if ipaddress.ip_address(host).is_loopback else {host}
    return frozenset(value for name in names for value in (name, f"{name}:{port}"))


class MutterRemoteInput:
    """Inject constrained keyboard and pointer input through GNOME Mutter."""

    BUS_NAME = "org.gnome.Mutter.RemoteDesktop"
    ROOT_PATH = "/org/gnome/Mutter/RemoteDesktop"
    ROOT_INTERFACE = "org.gnome.Mutter.RemoteDesktop"
    SESSION_INTERFACE = "org.gnome.Mutter.RemoteDesktop.Session"

    def __init__(self, home_url: str, sleep: Callable[[float], None] = time.sleep):
        self.home_url = home_url
        self._sleep = sleep
        # Keep one GNOME RemoteDesktop session while the active Media Hub
        # browser client is being controlled. Mutter exposes the session as a
        # virtual keyboard/pointer device. Recreating it for every relative
        # movement and then clicking from another device can leave web apps
        # with a visible cursor but no pointer-enter/hover state.
        self._session_lock = threading.RLock()
        self._session: object | None = None
        self._gio: object | None = None
        self._glib: object | None = None

    @staticmethod
    def _key(keysym: int) -> list[tuple[str, int]]:
        return [("press", keysym), ("release", keysym)]

    @staticmethod
    def _chord(modifier: int, key: int) -> list[tuple[str, int]]:
        return [
            ("press", modifier),
            ("press", key),
            ("release", key),
            ("release", modifier),
        ]

    @staticmethod
    def _shifted(key: int) -> list[tuple[str, int]]:
        return [
            ("press", KEYSYM["shift_l"]),
            ("press", key),
            ("release", key),
            ("release", KEYSYM["shift_l"]),
        ]

    @classmethod
    def text_events(cls, text: str) -> list[tuple[str, int]]:
        if not isinstance(text, str) or not SAFE_TEXT.fullmatch(text):
            raise CoordinatorError(
                "Text must be 1-100 printable ASCII characters without newlines"
            )

        shifted_symbols = {
            "!": "1", "@": "2", "#": "3", "$": "4", "%": "5",
            "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
            "_": "-", "+": "=", ":": ";", '"': "'", "<": ",",
            ">": ".", "?": "/", "|": "\\", "{": "[", "}": "]",
        }
        events: list[tuple[str, int]] = []
        for character in text:
            if character == " ":
                events.extend(cls._key(KEYSYM["space"]))
            elif "A" <= character <= "Z":
                events.extend(cls._shifted(ord(character.lower())))
            elif character in shifted_symbols:
                events.extend(cls._shifted(ord(shifted_symbols[character])))
            else:
                events.extend(cls._key(ord(character)))
        return events

    def events_for(self, command: str) -> list[tuple[str, int]]:
        if command not in INPUT_COMMANDS:
            raise CoordinatorError("Refusing unsupported remote command")
        simple = {
            "up": KEYSYM["up"],
            "down": KEYSYM["down"],
            "left": KEYSYM["left"],
            "right": KEYSYM["right"],
            "tab": KEYSYM["tab"],
            "select": KEYSYM["return"],
            "play_pause": KEYSYM["space"],
            "stop": KEYSYM["escape"],
        }
        if command in simple:
            return self._key(simple[command])
        if command == "shift_tab":
            return self._shifted(KEYSYM["tab"])
        if command == "back":
            return self._chord(KEYSYM["alt_l"], KEYSYM["left"])
        if command == "enter":
            return self._key(KEYSYM["return"])
        if command == "backspace":
            return self._key(KEYSYM["backspace"])
        if command == "clear":
            return self._chord(KEYSYM["control_l"], ord("a")) + self._key(KEYSYM["backspace"])
        if command == "home":
            events = self._chord(KEYSYM["control_l"], ord("l"))
            for character in self.home_url:
                events.extend(self._key(ord(character)))
            events.extend(self._key(KEYSYM["return"]))
            return events
        raise CoordinatorError("Refusing unsupported remote command")

    @staticmethod
    def _bounded_delta(value: object, label: str, maximum: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CoordinatorError(f"Pointer {label} must be a number")
        result = float(value)
        if not math.isfinite(result) or abs(result) > maximum:
            raise CoordinatorError(f"Pointer {label} is outside the safe range")
        return result

    def _create_session(self) -> tuple[object, object, object]:
        """Create and start one constrained Mutter virtual-input session."""
        try:
            from gi.repository import Gio, GLib
        except (ImportError, ValueError) as exc:
            raise CoordinatorError("GNOME remote-input support is unavailable") from exc

        session = None
        started = False
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            root = Gio.DBusProxy.new_sync(
                bus,
                Gio.DBusProxyFlags.NONE,
                None,
                self.BUS_NAME,
                self.ROOT_PATH,
                self.ROOT_INTERFACE,
                None,
            )
            result = root.call_sync(
                "CreateSession", None, Gio.DBusCallFlags.NONE, 5000, None
            )
            session_path = result.unpack()[0]
            session = Gio.DBusProxy.new_sync(
                bus,
                Gio.DBusProxyFlags.NONE,
                None,
                self.BUS_NAME,
                session_path,
                self.SESSION_INTERFACE,
                None,
            )
            session.call_sync("Start", None, Gio.DBusCallFlags.NONE, 5000, None)
            started = True
            return session, Gio, GLib
        finally:
            if session is not None and not started:
                try:
                    session.call_sync(
                        "Stop", None, Gio.DBusCallFlags.NONE, 5000, None
                    )
                except Exception:  # noqa: BLE001 - best-effort D-Bus cleanup
                    LOGGER.warning("Could not close incomplete GNOME remote-input session")

    def _ensure_session(self) -> tuple[object, object, object]:
        if self._session is None:
            try:
                session, gio, glib = self._create_session()
            except CoordinatorError:
                raise
            except Exception as exc:
                raise CoordinatorError("GNOME remote-input session could not start") from exc
            self._session = session
            self._gio = gio
            self._glib = glib
        if self._gio is None or self._glib is None:
            raise CoordinatorError("GNOME remote-input session is incomplete")
        return self._session, self._gio, self._glib

    def close(self) -> None:
        """Release the persistent virtual device without affecting the browser."""
        with self._session_lock:
            session, gio = self._session, self._gio
            self._session = None
            self._gio = None
            self._glib = None
            if session is None or gio is None:
                return
            try:
                session.call_sync(
                    "Stop", None, gio.DBusCallFlags.NONE, 5000, None
                )
            except Exception:  # noqa: BLE001 - best-effort D-Bus cleanup
                LOGGER.warning("Could not close GNOME remote-input session")

    def _with_session(
        self,
        callback: Callable[[object, object, object], None],
        failure_message: str,
    ) -> None:
        with self._session_lock:
            try:
                callback(*self._ensure_session())
            except CoordinatorError:
                raise
            except Exception as exc:
                # A failed D-Bus call can mean Mutter discarded the session.
                # Drop the cached device so the next deliberate user action
                # obtains a fresh one. Do not automatically retry an input
                # action, because that could duplicate a click or key press.
                self.close()
                raise CoordinatorError(failure_message) from exc

    def _send_events(self, events: list[tuple[str, int]]) -> None:
        def send(session: object, gio: object, glib: object) -> None:
            for action, keysym in events:
                pressed = action == "press"
                session.call_sync(
                    "NotifyKeyboardKeysym",
                    glib.Variant("(ub)", (keysym, pressed)),
                    gio.DBusCallFlags.NONE,
                    5000,
                    None,
                )
                self._sleep(0.012)

        self._with_session(send, "GNOME remote-input command failed")

    def send(self, command: str) -> None:
        self._send_events(self.events_for(command))

    def send_text(self, text: str) -> None:
        self._send_events(self.text_events(text))

    def send_pointer_move(self, dx: object, dy: object) -> None:
        dx_value = self._bounded_delta(dx, "dx", MAX_POINTER_MOTION)
        dy_value = self._bounded_delta(dy, "dy", MAX_POINTER_MOTION)
        if dx_value == 0 and dy_value == 0:
            return

        def move(session: object, gio: object, glib: object) -> None:
            session.call_sync(
                "NotifyPointerMotionRelative",
                glib.Variant("(dd)", (dx_value, dy_value)),
                gio.DBusCallFlags.NONE,
                5000,
                None,
            )

        self._with_session(move, "GNOME virtual-pointer move failed")

    def send_pointer_click(self, button: object) -> None:
        if not isinstance(button, str) or button not in POINTER_BUTTONS:
            raise CoordinatorError("Pointer button must be left or right")
        button_code = POINTER_BUTTONS[button]

        def click(session: object, gio: object, glib: object) -> None:
            for pressed in (True, False):
                session.call_sync(
                    "NotifyPointerButton",
                    glib.Variant("(ib)", (button_code, pressed)),
                    gio.DBusCallFlags.NONE,
                    5000,
                    None,
                )
                self._sleep(0.012)

        self._with_session(click, "GNOME virtual-pointer click failed")

    def send_pointer_scroll(self, dy: object) -> None:
        dy_value = self._bounded_delta(dy, "scroll", MAX_POINTER_SCROLL)
        if dy_value == 0:
            return

        def scroll(session: object, gio: object, glib: object) -> None:
            session.call_sync(
                "NotifyPointerAxis",
                glib.Variant("(ddu)", (0.0, dy_value, POINTER_AXIS_FINISH)),
                gio.DBusCallFlags.NONE,
                5000,
                None,
            )

        self._with_session(scroll, "GNOME virtual-pointer scroll failed")


class Coordinator:
    def __init__(
        self,
        config: Config,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        remote_input: MutterRemoteInput | None = None,
    ) -> None:
        self.config = config
        self._runner = runner
        # Replaced by _input_for_active_client() whenever the active client differs.
        first_home_url = next(iter(config.clients.values())).home_url
        self._remote_input = remote_input or MutterRemoteInput(first_home_url)
        self._lock = threading.Lock()

    def _systemctl(self, action: str, unit: str) -> subprocess.CompletedProcess[str]:
        if action not in {"start", "stop", "is-active"}:
            raise CoordinatorError("Refusing unsupported systemctl action")
        try:
            return self._runner(
                ["systemctl", "--user", action, unit],
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError) as exc:  # missing binary, timeout
            LOGGER.warning("systemctl --user %s %s failed: %s", action, unit, exc)
            raise CoordinatorError("systemd user manager is unavailable") from exc

    def status(self) -> dict[str, object]:
        active_modes = []
        states: dict[str, str] = {}
        for mode, client in self.config.clients.items():
            result = self._systemctl("is-active", client.unit)
            state = result.stdout.strip() or "unknown"
            states[mode] = state
            if state == "active":
                active_modes.append(mode)
        active = len(active_modes) == 1
        mode = active_modes[0] if active else ("ambiguous" if active_modes else "idle")
        return {
            "available": True,
            "mode": mode,
            "client_active": active,
            "client_state": states.get(mode, "unknown") if active else mode,
            "active_modes": active_modes,
            "remote_available": active,
        }

    def open(self, mode: str) -> dict[str, object]:
        with self._lock:
            if mode not in self.config.clients:
                raise CoordinatorError("Refusing unsupported Media Hub app")
            current = self.status()
            if current["client_active"] and current["mode"] == mode:
                return current
            self._release_remote_input()
            for active_mode in current["active_modes"]:
                self._systemctl("stop", self.config.clients[active_mode].unit)
            result = self._systemctl("start", self.config.clients[mode].unit)
            if result.returncode != 0:
                raise CoordinatorError("Media Hub client service did not start")
            current = self.status()
            if not current["client_active"] or current["mode"] != mode:
                raise CoordinatorError("Media Hub client did not become active")
            return current

    def close(self) -> dict[str, object]:
        with self._lock:
            current = self.status()
            self._release_remote_input()
            for active_mode in current["active_modes"]:
                result = self._systemctl("stop", self.config.clients[active_mode].unit)
                if result.returncode != 0:
                    raise CoordinatorError("Media Hub client service did not stop")
            current = self.status()
            if current["client_active"]:
                raise CoordinatorError("Media Hub client remained active")
            return current

    def _input_for_active_client(self, current: dict[str, object]) -> object:
        if not current["client_active"]:
            raise CoordinatorError("Media Hub client is not active")
        home_url = self.config.clients[str(current["mode"])].home_url
        remote_input = self._remote_input
        if isinstance(remote_input, MutterRemoteInput) and remote_input.home_url != home_url:
            remote_input.close()
            remote_input = MutterRemoteInput(home_url)
            self._remote_input = remote_input
        return remote_input

    def _release_remote_input(self) -> None:
        if isinstance(self._remote_input, MutterRemoteInput):
            self._remote_input.close()

    def remote(self, command: str) -> dict[str, object]:
        with self._lock:
            if command not in REMOTE_COMMANDS:
                raise CoordinatorError("Refusing unsupported remote command")
            current = self.status()
            remote_input = self._input_for_active_client(current)
            input_command = (
                BROWSER_FOCUS_COMMANDS.get(command, command)
                if self.config.clients[str(current["mode"])].tab_navigation
                else command
            )
            remote_input.send(input_command)
            current["command"] = command
            if input_command != command:
                current["input_command"] = input_command
            return current

    def keyboard_text(self, text: str, submit: bool = False) -> dict[str, object]:
        with self._lock:
            current = self.status()
            remote_input = self._input_for_active_client(current)
            remote_input.send_text(text)
            if submit:
                remote_input.send("enter")
            current["text_length"] = len(text)
            current["submitted"] = submit
            return current

    def pointer_move(self, dx: object, dy: object) -> dict[str, object]:
        with self._lock:
            current = self.status()
            remote_input = self._input_for_active_client(current)
            remote_input.send_pointer_move(dx, dy)
            current["pointer_action"] = "move"
            return current

    def pointer_click(self, button: object) -> dict[str, object]:
        with self._lock:
            current = self.status()
            remote_input = self._input_for_active_client(current)
            remote_input.send_pointer_click(button)
            current["pointer_action"] = "click"
            current["pointer_button"] = button
            return current

    def pointer_scroll(self, dy: object) -> dict[str, object]:
        with self._lock:
            current = self.status()
            remote_input = self._input_for_active_client(current)
            remote_input.send_pointer_scroll(dy)
            current["pointer_action"] = "scroll"
            return current


class CoordinatorServer(ThreadingHTTPServer):
    """Threaded server with per-peer-class connection pools; connections beyond a pool are closed unanswered.

    The pool is chosen by source IP before any byte is read, so non-allowlisted hosts dripping slow
    requests can only exhaust their own MAX_UNLISTED_CONNECTIONS, never Home Assistant's slots.
    """

    daemon_threads = True
    coordinator: Coordinator
    allowed_hosts: frozenset[str]
    slots: threading.BoundedSemaphore
    unlisted_slots: threading.BoundedSemaphore
    held: dict  # request socket -> the pool it took a slot from

    def client_allowed(self, host: str) -> bool:
        try:
            return ipaddress.ip_address(host) in self.coordinator.config.allowed_clients
        except ValueError:
            return False

    def process_request(self, request, client_address) -> None:
        pool = self.slots if self.client_allowed(client_address[0]) else self.unlisted_slots
        if not pool.acquire(blocking=False):
            self.close_request(request)
            return
        self.held[request] = pool
        super().process_request(request, client_address)

    def shutdown_request(self, request) -> None:
        super().shutdown_request(request)
        pool = self.held.pop(request, None)
        if pool is not None:
            pool.release()


def make_server(config: Config) -> CoordinatorServer:
    """Bind (not yet serve) the HTTP server for config."""
    server = CoordinatorServer((config.listen_host, config.listen_port), Handler)
    server.coordinator = Coordinator(config)
    server.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
    server.unlisted_slots = threading.BoundedSemaphore(MAX_UNLISTED_CONNECTIONS)
    server.held = {}
    server.allowed_hosts = config.allowed_hosts or default_hosts(config.listen_host, server.server_port)
    return server


class Handler(BaseHTTPRequestHandler):
    server: CoordinatorServer
    timeout = 10  # socket read timeout: a stalled client cannot pin a handler thread

    def log_message(self, format_string: str, *args: object) -> None:
        LOGGER.info("%s %s", self.client_address[0], format_string % args)

    def _rejection(self) -> tuple[HTTPStatus, str] | None:
        """Guard order is part of the API: IP 403, Host 421, token 401, framing 411/400, JSON body 415."""
        chunked = "Transfer-Encoding" in self.headers
        lengths = self.headers.get_all("Content-Length") or ["0"]
        length = lengths[0].strip()
        if chunked or len(lengths) > 1 or (self.command == "GET" and length != "0"):
            self.close_connection = True  # an unread body must never be parsed as the next request
        if not self.server.client_allowed(self.client_address[0]):
            return HTTPStatus.FORBIDDEN, "client_not_allowed"
        if (self.headers.get("Host") or "").lower() not in self.server.allowed_hosts:
            return HTTPStatus.MISDIRECTED_REQUEST, "host_not_allowed"
        presented = (self.headers.get("Authorization") or "").encode()
        if not hmac.compare_digest(presented, f"Bearer {self.server.coordinator.config.auth_token}".encode()):
            return HTTPStatus.UNAUTHORIZED, "unauthorized"
        if len(lengths) > 1:
            return HTTPStatus.BAD_REQUEST, "invalid_content_length"
        if self.command in ("POST", "PUT", "PATCH"):
            if chunked:
                return HTTPStatus.LENGTH_REQUIRED, "length_required"
            if not length.isdecimal():
                return HTTPStatus.BAD_REQUEST, "invalid_content_length"
            media_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if int(length) and media_type != "application/json":
                return HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "unsupported_media_type"
        return None

    def _send(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _guard(self) -> bytes | None:
        rejection = self._rejection()
        if rejection is not None:
            self.close_connection = True
            self._send(rejection[0], {"error": rejection[1]})
            return None
        # Only POST bodies are read (framing validated by _rejection); a GET body just closes the connection.
        length = int(self.headers.get("Content-Length") or 0) if self.command == "POST" else 0
        if length > MAX_REQUEST_BYTES:
            self.close_connection = True
            self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request_too_large"})
            return None
        return self.rfile.read(length) if length else b""

    @staticmethod
    def _json_object(body: bytes, name: str) -> dict[str, object]:
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise CoordinatorError(f"{name} request is invalid")
        if not isinstance(payload, dict):
            raise CoordinatorError(f"{name} request is invalid")
        return payload

    def do_GET(self) -> None:
        if self._guard() is None:
            return
        try:
            if self.path == "/health":
                self._send(HTTPStatus.OK, {"status": "ok"})
            elif self.path == "/v1/status":
                self._send(HTTPStatus.OK, self.server.coordinator.status())
            else:
                self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        except CoordinatorError as exc:
            self._send(HTTPStatus.CONFLICT, {"error": str(exc)})

    def do_POST(self) -> None:
        body = self._guard()
        if body is None:
            return
        try:
            if self.path == "/v1/open":  # legacy: the first configured client
                first = next(iter(self.server.coordinator.config.clients))
                self._send(HTTPStatus.OK, self.server.coordinator.open(first))
            elif self.path.startswith("/v1/open/"):
                mode = self.path.removeprefix("/v1/open/")
                self._send(HTTPStatus.OK, self.server.coordinator.open(mode))
            elif self.path == "/v1/close":
                self._send(HTTPStatus.OK, self.server.coordinator.close())
            elif self.path.startswith("/v1/remote/"):
                command = self.path.removeprefix("/v1/remote/")
                self._send(HTTPStatus.OK, self.server.coordinator.remote(command))
            elif self.path == "/v1/keyboard/text":
                payload = self._json_object(body, "Keyboard text")
                text = payload.get("text")
                submit = bool(payload.get("submit", False))
                if not isinstance(text, str):
                    raise CoordinatorError("Keyboard text is missing")
                self._send(HTTPStatus.OK, self.server.coordinator.keyboard_text(text, submit))
            elif self.path == "/v1/pointer/move":
                payload = self._json_object(body, "Pointer move")
                self._send(
                    HTTPStatus.OK,
                    self.server.coordinator.pointer_move(
                        payload.get("dx"), payload.get("dy")
                    ),
                )
            elif self.path == "/v1/pointer/click":
                payload = self._json_object(body, "Pointer click")
                self._send(
                    HTTPStatus.OK,
                    self.server.coordinator.pointer_click(payload.get("button")),
                )
            elif self.path == "/v1/pointer/scroll":
                payload = self._json_object(body, "Pointer scroll")
                self._send(
                    HTTPStatus.OK,
                    self.server.coordinator.pointer_scroll(payload.get("dy")),
                )
            else:
                self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        except CoordinatorError as exc:
            self._send(HTTPStatus.CONFLICT, {"error": str(exc)})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config = load_config(args.config)
    except CoordinatorError as exc:
        print(f"ERROR: {exc}")
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = make_server(config)
    LOGGER.info("Media Hub coordinator listening on %s:%d", config.listen_host, config.listen_port)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
