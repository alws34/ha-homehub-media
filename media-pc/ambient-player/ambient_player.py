#!/usr/bin/env python3
"""Home Hub dual-layer ambient player.

The service owns two independent MPV processes:
  * video: fullscreen visual plus its original audio
  * soundtrack: audio-only, independently looped/seeked/levelled

The v2 API is a catalog plus a player: composition playback, per-layer
lifecycle, partial live updates that never restart an unmentioned layer, and
configured live radio streams in the dynamic soundtrack catalog. Profiles and
favourites live in Home Assistant. Every request must come from the IP
allowlist, name a configured Host and carry the bearer token; bodies are JSON.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import random
import re
import signal
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

LOGGER = logging.getLogger("ambient-player")
MUTTER_BUS = "org.gnome.Mutter.DisplayConfig"
MUTTER_PATH = "/org/gnome/Mutter/DisplayConfig"
MUTTER_INTERFACE = "org.gnome.Mutter.DisplayConfig"
ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
AUDIO_ONLY_ID = "audio_only"
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov"}
SOUNDTRACK_EXTENSIONS = {".mp3", ".ogg", ".opus", ".flac", ".m4a", ".wav"}
DEFAULT_PORT = 8097
MAX_REQUEST_BYTES = 65536
MAX_CONNECTIONS = 16
MIN_TOKEN_LENGTH = 32


class PlayerError(RuntimeError):
    """An expected controller error. Clients only ever see `code`; the message is logged."""

    def __init__(self, message: str, code: str = "invalid_request"):
        # code is one of: invalid_request, unknown_media, unavailable, busy
        super().__init__(message)
        self.code = code


def run_checked(args: list[str], timeout: float = 20.0) -> str:
    completed = subprocess.run(
        args,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise PlayerError(
            f"command failed ({completed.returncode}): {args[0]}: "
            f"{completed.stdout.strip()}",
            "unavailable",
        )
    return completed.stdout


def atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    slug = slug[:64].strip("_") or "media"
    if not slug[0].isalnum():
        slug = f"media_{slug}"[:64]
    return slug


def bounded_number(value: Any, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise PlayerError(f"{name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PlayerError(f"{name} must be numeric") from exc
    if number < minimum or number > maximum:
        raise PlayerError(f"{name} must be between {minimum:g} and {maximum:g}")
    return number


def strict_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise PlayerError(f"{name} must be true or false")
    return value


@dataclass(frozen=True)
class MediaItem:
    media_id: str
    kind: str
    title: str
    path: Path | str
    relative_path: str | None
    size: int
    duration: float | None
    has_audio: bool
    fingerprint: str
    source_type: str = "file"

    def public(self) -> dict[str, Any]:
        return {
            "id": self.media_id,
            "kind": self.kind,
            "title": self.title,
            "source_type": self.source_type,
            "live": self.source_type == "stream",
            "relative_path": self.relative_path,
            "size": self.size,
            "duration": round(self.duration, 3) if self.duration is not None else None,
            "has_audio": self.has_audio,
        }


# Fullscreen black frame shown on the video layer during soundtrack-only playback.
AUDIO_ONLY_ITEM = MediaItem(
    media_id=AUDIO_ONLY_ID,
    kind="video",
    title="Audio Only",
    path="av://lavfi:color=c=black:s=1920x1080:r=1",
    relative_path=None,
    size=0,
    duration=None,
    has_audio=False,
    fingerprint="built-in-audio-only-guard",
)


@dataclass
class Layer:
    name: str
    process: subprocess.Popen[bytes] | None = None
    item_id: str | None = None
    ipc_path: Path | None = None
    started_at: float | None = None
    start_offset: float = 0.0
    loop: bool = True
    random_start: bool = False
    volume: float = 100.0
    paused: bool = False
    last_error: str | None = None


class PersistentState:
    """Only the catalog id cache (stable ids across rescans). v2 profiles/favourites are dropped."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {"version": 3, "catalog": {"video": {}, "soundtrack": {}}}
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict) or loaded.get("version") not in (2, 3):
                raise PlayerError("unsupported or invalid persistent state")
            for kind in ("video", "soundtrack"):
                self.data["catalog"][kind].update(loaded.get("catalog", {}).get(kind, {}))
        self.save()

    def save(self) -> None:
        atomic_json_write(self.path, self.data)


class Catalog:
    def __init__(self, config: dict[str, Any], state: PersistentState):
        self.state = state
        self.roots = {
            "video": Path(config["video_root"]).resolve(strict=True),
            "soundtrack": Path(config["soundtrack_root"]).resolve(strict=True),
        }
        self.aliases = {
            "video": dict(config.get("video_aliases", {})),
            "soundtrack": dict(config.get("soundtrack_aliases", {})),
        }
        self.soundtrack_streams = dict(config.get("soundtrack_streams", {}))
        self.items: dict[str, dict[str, MediaItem]] = {"video": {}, "soundtrack": {}}
        self.skipped = 0
        self.refresh()

    @staticmethod
    def _fingerprint(path: Path) -> str:
        stat = path.stat()
        digest = hashlib.sha256()
        digest.update(str(stat.st_size).encode("ascii"))
        with path.open("rb") as handle:
            digest.update(handle.read(65536))
            if stat.st_size > 65536:
                handle.seek(max(0, stat.st_size - 65536))
                digest.update(handle.read(65536))
        return digest.hexdigest()

    @staticmethod
    def _probe(path: Path) -> tuple[float, bool]:
        output = run_checked(
            [
                "/usr/bin/mpv",
                "--no-config",
                "--frames=1",
                "--length=0.1",
                "--vo=null",
                "--ao=null",
                "--term-playing-msg=DURATION=${duration}\nAUDIO=${audio-codec-name}",
                str(path),
            ],
            timeout=45,
        )
        duration_match = re.search(r"^DURATION=([^\r\n]+)$", output, re.MULTILINE)
        audio_match = re.search(r"^AUDIO=([^\r\n]+)$", output, re.MULTILINE)
        if not duration_match:
            raise PlayerError(f"media duration unavailable: {path.name}")
        duration_parts = duration_match.group(1).strip().split(":")
        try:
            if len(duration_parts) == 3:
                duration = (
                    float(duration_parts[0]) * 3600
                    + float(duration_parts[1]) * 60
                    + float(duration_parts[2])
                )
            else:
                duration = float(duration_parts[0])
        except (TypeError, ValueError) as exc:
            raise PlayerError(f"media duration is invalid: {path.name}") from exc
        if duration <= 0:
            raise PlayerError(f"media duration is invalid: {path.name}")
        has_audio = bool(
            audio_match
            and audio_match.group(1).strip().lower() not in ("", "n/a", "no")
        )
        return duration, has_audio

    def _assign_id(
        self,
        kind: str,
        relative_path: str,
        fingerprint: str,
        used: set[str],
    ) -> str:
        for alias, alias_path in self.aliases[kind].items():
            if alias_path == relative_path:
                if not ID_RE.fullmatch(alias):
                    raise PlayerError(f"invalid configured {kind} alias: {alias}")
                return alias
        old = self.state.data["catalog"][kind]
        for media_id, record in old.items():
            if media_id not in used and record.get("relative_path") == relative_path:
                return media_id
        fingerprint_matches = [
            media_id
            for media_id, record in old.items()
            if media_id not in used and record.get("fingerprint") == fingerprint
        ]
        if len(fingerprint_matches) == 1:
            return fingerprint_matches[0]
        base = slugify(Path(relative_path).stem)
        candidate = base
        counter = 2
        while candidate in used:
            suffix = f"_{counter}"
            candidate = f"{base[:64-len(suffix)]}{suffix}"
            counter += 1
        return candidate

    def refresh(self) -> dict[str, Any]:
        refreshed: dict[str, dict[str, MediaItem]] = {"video": {}, "soundtrack": {}}
        records: dict[str, dict[str, Any]] = {"video": {}, "soundtrack": {}}
        skipped = 0
        for kind, root in self.roots.items():
            extensions = VIDEO_EXTENSIONS if kind == "video" else SOUNDTRACK_EXTENSIONS
            used: set[str] = set()
            for path in sorted(root.rglob("*"), key=lambda item: str(item).lower()):
                if not path.is_file() or path.suffix.lower() not in extensions:
                    continue
                try:
                    resolved = path.resolve(strict=True)
                    if root not in resolved.parents:
                        raise PlayerError(f"catalog item escapes {kind} root")
                    if not os.access(resolved, os.R_OK):
                        raise PlayerError(f"catalog item is unreadable: {path.name}")
                    relative = resolved.relative_to(root).as_posix()
                    fingerprint = self._fingerprint(resolved)
                    media_id = self._assign_id(kind, relative, fingerprint, used)
                    if media_id in used:
                        raise PlayerError(f"duplicate {kind} id: {media_id}")
                    duration, has_audio = self._probe(resolved)
                except (PlayerError, OSError, subprocess.SubprocessError) as exc:
                    # One bad file must not take the whole catalog (or the service) down.
                    LOGGER.warning("Skipping %s: %s", path, exc)
                    skipped += 1
                    continue
                item = MediaItem(
                    media_id=media_id,
                    kind=kind,
                    title=resolved.stem.replace("_", " ").strip(),
                    path=resolved,
                    relative_path=relative,
                    size=resolved.stat().st_size,
                    duration=duration,
                    has_audio=has_audio,
                    fingerprint=fingerprint,
                )
                refreshed[kind][media_id] = item
                records[kind][media_id] = {
                    "relative_path": relative,
                    "fingerprint": fingerprint,
                }
                used.add(media_id)
            if kind == "soundtrack":
                for media_id, stream_url in sorted(self.soundtrack_streams.items()):
                    if media_id in used:
                        raise PlayerError(f"duplicate soundtrack id: {media_id}")
                    label = media_id.replace("_", " ").strip()
                    if label.startswith("radio "):
                        label = f"{label[6:]} live"
                    fingerprint = hashlib.sha256(stream_url.encode("utf-8")).hexdigest()
                    item = MediaItem(
                        media_id=media_id,
                        kind="soundtrack",
                        title=label.title(),
                        path=stream_url,
                        relative_path=None,
                        size=0,
                        duration=None,
                        has_audio=True,
                        fingerprint=fingerprint,
                        source_type="stream",
                    )
                    refreshed[kind][media_id] = item
                    records[kind][media_id] = {
                        "relative_path": None,
                        "fingerprint": fingerprint,
                        "source_type": "stream",
                    }
                    used.add(media_id)
        self.items = refreshed
        self.skipped = skipped
        self.state.data["catalog"] = records
        self.state.save()
        return self.public()

    def get(self, kind: str, media_id: str) -> MediaItem:
        if kind not in self.items or media_id not in self.items[kind]:
            raise PlayerError(f"unknown {kind}: {media_id}", "unknown_media")
        return self.items[kind][media_id]

    def public(self) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": True, "skipped": self.skipped}
        for kind in ("video", "soundtrack"):
            key = "visuals" if kind == "video" else "soundtracks"
            result[key] = [self.items[kind][media_id].public() for media_id in sorted(self.items[kind])]
        return result


class DualAmbientPlayer:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.lock = threading.RLock()
        runtime_default = (
            f"/run/user/{os.getuid()}"
            if hasattr(os, "getuid")
            else tempfile.gettempdir()
        )
        self.runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR", runtime_default))
        self.state = PersistentState(Path(config["state_file"]))
        self.catalog = Catalog(config, self.state)
        self.layers = {
            "video": Layer("video", ipc_path=self.runtime_dir / "ambient-player-video.sock"),
            "soundtrack": Layer(
                "soundtrack", ipc_path=self.runtime_dir / "ambient-player-soundtrack.sock"
            ),
        }
        self.last_error: str | None = None

    def _refresh_layer(self, layer: Layer) -> None:
        if layer.process is not None and layer.process.poll() is not None:
            return_code = layer.process.returncode
            layer.process = None
            layer.item_id = None
            layer.started_at = None
            layer.paused = False
            if layer.ipc_path:
                layer.ipc_path.unlink(missing_ok=True)
            if return_code not in (0, -signal.SIGTERM):
                layer.last_error = f"mpv exited with status {return_code}"

    def _ipc(self, layer: Layer, command: list[Any]) -> Any:
        self._refresh_layer(layer)
        if layer.process is None or layer.ipc_path is None:
            raise PlayerError(f"{layer.name} is not playing", "unavailable")
        request_id = random.SystemRandom().randrange(1, 2**31)
        payload = json.dumps({"command": command, "request_id": request_id}).encode() + b"\n"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(str(layer.ipc_path))
            client.sendall(payload)
            buffer = b""
            while True:
                chunk = client.recv(4096)
                if not chunk:
                    raise PlayerError(f"{layer.name} IPC closed unexpectedly", "unavailable")
                buffer += chunk
                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    response = json.loads(raw)
                    if response.get("request_id") != request_id:
                        continue
                    if response.get("error") != "success":
                        raise PlayerError(
                            f"{layer.name} IPC error: {response.get('error', 'unknown')}",
                            "unavailable",
                        )
                    return response.get("data")

    def _layer_status(self, layer: Layer) -> dict[str, Any]:
        self._refresh_layer(layer)
        position = None
        if layer.process is not None:
            try:
                position = self._ipc(layer, ["get_property", "time-pos"])
            except Exception:  # noqa: BLE001 - position is best-effort
                position = None
        return {
            "state": "playing" if layer.process else "idle",
            "id": layer.item_id,
            "pid": layer.process.pid if layer.process else None,
            "started_at": layer.started_at,
            "start_offset": round(layer.start_offset, 3),
            "position": round(float(position), 3) if position is not None else None,
            "loop": layer.loop,
            "random_start": layer.random_start,
            "volume": round(layer.volume, 2),
            "paused": layer.paused if layer.process else False,
            "last_error": layer.last_error,
        }

    def status(self) -> dict[str, Any]:
        with self.lock:
            video = self._layer_status(self.layers["video"])
            soundtrack = self._layer_status(self.layers["soundtrack"])
            return {
                "ok": True,
                "state": "playing"
                if video["state"] == "playing" or soundtrack["state"] == "playing"
                else "idle",
                "video": video,
                "soundtrack": soundtrack,
                "last_error": self.last_error,
                "catalog": {
                    "visuals": len(self.catalog.items["video"]),
                    "soundtracks": len(self.catalog.items["soundtrack"]),
                    "skipped": self.catalog.skipped,
                },
            }

    def catalog_public(self) -> dict[str, Any]:
        with self.lock:
            return self.catalog.public()

    def refresh_catalog(self) -> dict[str, Any]:
        with self.lock:
            return self.catalog.refresh()

    def _wait_for_hdmi(self) -> str:
        deadline = time.monotonic() + float(self.config.get("hdmi_wait_seconds", 20))
        while time.monotonic() < deadline:
            connected = []
            for status_path in Path("/sys/class/drm").glob("card*-HDMI-A-*/status"):
                try:
                    if status_path.read_text(encoding="ascii").strip() == "connected":
                        connected.append(status_path.parent.name)
                except OSError:
                    continue
            if len(connected) == 1:
                return connected[0]
            if len(connected) > 1:
                raise PlayerError(f"multiple HDMI displays are connected: {connected}", "unavailable")
            time.sleep(0.5)
        raise PlayerError("Samsung HDMI display did not connect before the timeout", "unavailable")

    def _wake_display(self) -> None:
        import gi  # lazy: only the live display path needs GObject introspection

        gi.require_version("Gio", "2.0")
        gi.require_version("GLib", "2.0")
        from gi.repository import Gio, GLib

        def dbus_call(proxy, method: str, parameters=None):
            return proxy.call_sync(method, parameters, Gio.DBusCallFlags.NONE, 15_000, None).unpack()

        proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.NONE,
            None,
            MUTTER_BUS,
            MUTTER_PATH,
            MUTTER_INTERFACE,
            None,
        )
        _serial, _monitors, logical_monitors, _properties = dbus_call(
            proxy, "GetCurrentState"
        )
        active_connectors = [
            monitor[0] for logical in logical_monitors for monitor in logical[5]
        ]
        if len(active_connectors) != 1 or not active_connectors[0].startswith("HDMI"):
            raise PlayerError(
                "refusing to replace the active monitor layout: "
                + (", ".join(active_connectors) if active_connectors else "none"),
                "unavailable",
            )
        properties_proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.NONE,
            None,
            MUTTER_BUS,
            MUTTER_PATH,
            "org.freedesktop.DBus.Properties",
            None,
        )
        dbus_call(
            properties_proxy,
            "Set",
            GLib.Variant(
                "(ssv)",
                (MUTTER_INTERFACE, "PowerSaveMode", GLib.Variant("i", 0)),
            ),
        )

    @staticmethod
    def _parse_section_id(status: str, section: str, fragment: str) -> int:
        in_section = False
        for raw_line in status.splitlines():
            stripped = raw_line.strip(" │├└─*\t")
            if stripped == f"{section}:":
                in_section = True
                continue
            if in_section and stripped.endswith(":"):
                break
            if in_section and fragment in stripped:
                match = re.search(r"(?:^|\s)(\d+)\.\s", stripped)
                if match:
                    return int(match.group(1))
        raise PlayerError(f"could not locate {fragment!r} in PipeWire {section}", "unavailable")

    def _configure_hdmi_audio(self) -> None:
        status = run_checked(["/usr/bin/wpctl", "status", "-n"])
        device_id = self._parse_section_id(
            status, "Devices", "alsa_card.pci-0000_00_1f.3"
        )
        run_checked(["/usr/bin/wpctl", "set-profile", str(device_id), "3"])
        time.sleep(0.4)
        status = run_checked(["/usr/bin/wpctl", "status", "-n"])
        sink_id = self._parse_section_id(
            status, "Sinks", "alsa_output.pci-0000_00_1f.3.hdmi-stereo"
        )
        run_checked(["/usr/bin/wpctl", "set-default", str(sink_id)])
        run_checked(["/usr/bin/wpctl", "set-mute", str(sink_id), "0"])
        run_checked(["/usr/bin/wpctl", "set-volume", str(sink_id), "1.0"])

    def _stop_layer_locked(self, layer: Layer) -> bool:
        self._refresh_layer(layer)
        if layer.process is None:
            if layer.ipc_path:
                layer.ipc_path.unlink(missing_ok=True)
            return False
        process = layer.process
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
        finally:
            layer.process = None
            layer.item_id = None
            layer.started_at = None
            layer.paused = False
            if layer.ipc_path:
                layer.ipc_path.unlink(missing_ok=True)
        return True

    def _start_audio_only_guard_locked(self) -> bool:
        """Show a fullscreen still before soundtrack-only playback.

        This process is intentionally represented by the video layer so a
        real visual can replace it without touching the soundtrack layer.
        """
        layer = self.layers["video"]
        self._refresh_layer(layer)
        if layer.process is not None and layer.item_id == AUDIO_ONLY_ID:
            return False
        self._wait_for_hdmi()
        self._wake_display()
        self._start_layer("video", AUDIO_ONLY_ITEM, True, False, 0)
        self._release_layer_locked(layer)
        return True

    def stop(self, kind: str | None = None) -> dict[str, Any]:
        with self.lock:
            if kind is not None and kind not in self.layers:
                raise PlayerError("unknown playback layer")
            targets = [self.layers[kind]] if kind else list(self.layers.values())
            stopped = {layer.name: self._stop_layer_locked(layer) for layer in targets}
            self.last_error = None
            result = self.status()
            result["stopped"] = stopped
            return result

    def validate_composition(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise PlayerError("composition must be a JSON object")
        visual = value.get("visual")
        soundtrack = value.get("soundtrack")
        if visual is not None:
            if not isinstance(visual, str) or not ID_RE.fullmatch(visual):
                raise PlayerError("visual must be a valid catalog id or null")
            self.catalog.get("video", visual)
        if soundtrack is not None:
            if not isinstance(soundtrack, str) or not ID_RE.fullmatch(soundtrack):
                raise PlayerError("soundtrack must be a valid catalog id or null")
            self.catalog.get("soundtrack", soundtrack)
        visual_enabled = value.get("visual_enabled", visual is not None)
        if not isinstance(visual_enabled, bool):
            raise PlayerError("visual_enabled must be true or false")
        if visual_enabled and visual is None:
            raise PlayerError("visual_enabled requires a visual")
        return {
            "visual": visual,
            "visual_enabled": visual_enabled,
            "video_loop": strict_bool(value.get("video_loop", True), "video_loop"),
            "video_random_start": strict_bool(
                value.get("video_random_start", False), "video_random_start"
            ),
            "original_volume": bounded_number(
                value.get("original_volume", 100), "original_volume", 0, 100
            ),
            "soundtrack": soundtrack,
            "soundtrack_loop": strict_bool(
                value.get("soundtrack_loop", True), "soundtrack_loop"
            ),
            "soundtrack_random_start": strict_bool(
                value.get("soundtrack_random_start", False),
                "soundtrack_random_start",
            ),
            "soundtrack_volume": bounded_number(
                value.get("soundtrack_volume", 100), "soundtrack_volume", 0, 100
            ),
        }

    @staticmethod
    def _offset(item: MediaItem, random_start: bool) -> float:
        if item.source_type == "stream" or not random_start:
            return 0.0
        if item.duration is None or item.duration <= 1.0:
            return 0.0
        return random.SystemRandom().uniform(0.0, max(0.0, item.duration - 0.5))

    @staticmethod
    def _mpv_command(
        kind: str,
        item: MediaItem,
        loop: bool,
        offset: float,
        volume: float,
        ipc_path: Path,
    ) -> list[str]:
        if kind not in ("video", "soundtrack"):
            raise PlayerError("unknown playback layer")
        command = [
            "/usr/bin/mpv",
            "--no-config",
            "--idle=no",
            f"--loop-file={'inf' if loop and item.source_type == 'file' else 'no'}",
            f"--volume={volume:g}",
            f"--input-ipc-server={ipc_path}",
            "--pause=yes",
            "--msg-level=all=warn",
        ]
        if item.source_type == "file":
            command.append(f"--start={offset:.3f}")
        else:
            command += [
                "--cache=yes",
                "--cache-secs=20",
                "--demuxer-lavf-o=reconnect=1,reconnect_streamed=1,reconnect_at_eof=1,reconnect_delay_max=5",
            ]
        if kind == "video":
            command += [
                "--fullscreen=yes",
                "--no-osc",
                "--osd-level=0",
                "--cursor-autohide=always",
                "--force-window=immediate",
            ]
            if item.media_id == AUDIO_ONLY_ID:
                command += [
                    "--image-display-duration=inf",
                    "--audio=no",
                    "--hwdec=no",
                ]
            else:
                command += ["--hwdec=auto-safe", "--audio-channels=stereo"]
        else:
            command += ["--video=no", "--force-window=no", "--audio-channels=stereo"]
        command.append(str(item.path))
        return command

    def _start_layer(
        self,
        kind: str,
        item: MediaItem,
        loop: bool,
        random_start: bool,
        volume: float,
    ) -> None:
        layer = self.layers[kind]
        self._stop_layer_locked(layer)
        assert layer.ipc_path is not None
        layer.ipc_path.unlink(missing_ok=True)
        if item.source_type == "stream":
            loop = False
            random_start = False
        offset = self._offset(item, random_start)
        command = self._mpv_command(
            kind, item, loop, offset, volume, layer.ipc_path
        )
        layer.process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=None,
            start_new_session=True,
        )
        layer.item_id = item.media_id
        layer.started_at = None
        layer.start_offset = offset
        layer.loop = loop
        layer.random_start = random_start
        layer.volume = volume
        layer.paused = True
        layer.last_error = None
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            self._refresh_layer(layer)
            if layer.process is None:
                raise PlayerError(layer.last_error or f"{kind} MPV exited during startup", "unavailable")
            if layer.ipc_path.exists():
                return
            time.sleep(0.05)
        raise PlayerError(f"{kind} MPV IPC socket did not become ready", "unavailable")

    def _release_layer_locked(self, layer: Layer) -> None:
        """Release one newly started layer without touching its peer."""
        self._ipc(layer, ["set_property", "pause", False])
        layer.paused = False
        layer.started_at = time.time()

    def _set_layer_volume_locked(self, layer: Layer, volume: float) -> None:
        """Update an active MPV volume in place; retain the value while idle."""
        self._refresh_layer(layer)
        if layer.process is not None:
            self._ipc(layer, ["set_property", "volume", volume])
        layer.volume = volume

    def _set_layer_loop_locked(self, layer: Layer, loop: bool) -> None:
        """Update loop-file through IPC without seeking or reloading media."""
        self._refresh_layer(layer)
        if layer.process is not None:
            self._ipc(layer, ["set_property", "loop-file", "inf" if loop else "no"])
        layer.loop = loop

    def _normalize_layer_patch(self, kind: str, value: Any) -> dict[str, Any]:
        if kind not in self.layers:
            raise PlayerError("unknown playback layer")
        if not isinstance(value, dict) or not value:
            raise PlayerError(f"{kind} patch must be a non-empty JSON object")
        allowed = {"enabled", "media_id", "loop", "random_start", "volume"}
        unknown = set(value) - allowed
        if unknown:
            raise PlayerError(
                f"unknown {kind} patch field(s): {', '.join(sorted(unknown))}"
            )
        result: dict[str, Any] = {}
        if "enabled" in value:
            result["enabled"] = strict_bool(value["enabled"], f"{kind}.enabled")
        if "media_id" in value:
            media_id = value["media_id"]
            if not isinstance(media_id, str) or not ID_RE.fullmatch(media_id):
                raise PlayerError(f"{kind}.media_id must be a valid catalog id")
            self.catalog.get(kind, media_id)
            result["media_id"] = media_id
        if "loop" in value:
            result["loop"] = strict_bool(value["loop"], f"{kind}.loop")
        if "random_start" in value:
            result["random_start"] = strict_bool(
                value["random_start"], f"{kind}.random_start"
            )
        if "volume" in value:
            result["volume"] = bounded_number(
                value["volume"], f"{kind}.volume", 0, 100
            )
        if result.get("enabled") is False and set(result) != {"enabled"}:
            raise PlayerError(
                f"{kind}.enabled=false cannot include other layer fields"
            )
        return result

    def _apply_layer_patch_locked(
        self, kind: str, update: dict[str, Any]
    ) -> dict[str, Any]:
        """Apply one normalized layer patch while leaving its peer untouched."""
        layer = self.layers[kind]
        self._refresh_layer(layer)

        if update.get("enabled") is False:
            if kind == "video":
                soundtrack = self.layers["soundtrack"]
                self._refresh_layer(soundtrack)
                if soundtrack.process is not None:
                    try:
                        started = self._start_audio_only_guard_locked()
                    except Exception:
                        # Fail closed: if the fullscreen safety guard cannot
                        # replace the visual, stop host audio rather than risk
                        # revealing the Ubuntu desktop on HDMI3.
                        self._stop_layer_locked(soundtrack)
                        raise
                    return {
                        "action": "audio_only_guard_started"
                        if started
                        else "audio_only_guard_unchanged"
                    }
            stopped = self._stop_layer_locked(layer)
            guard_stopped = False
            if kind == "soundtrack":
                video = self.layers["video"]
                self._refresh_layer(video)
                if video.process is not None and video.item_id == AUDIO_ONLY_ID:
                    # The guard exists only to make host-audio playback safe.
                    # Remove it when the last soundtrack is disabled, while
                    # preserving any real user-selected visual.
                    guard_stopped = self._stop_layer_locked(video)
            if "loop" in update:
                layer.loop = update["loop"]
            if "random_start" in update:
                layer.random_start = update["random_start"]
            if "volume" in update:
                layer.volume = update["volume"]
            return {
                "action": "stopped" if stopped else "already_idle",
                "audio_only_guard_stopped": guard_stopped,
            }

        requested_id = update.get("media_id")
        start_required = requested_id is not None and (
            layer.process is None or layer.item_id != requested_id
        )

        if start_required:
            item = self.catalog.get(kind, requested_id)
            loop = update.get("loop", layer.loop)
            random_start = update.get("random_start", False)
            if item.source_type == "stream":
                loop = False
                random_start = False
            volume = update.get("volume", layer.volume)
            if kind == "video":
                self._wait_for_hdmi()
                self._wake_display()
            elif self.layers["video"].process is None:
                # Soundtrack-only playback must never reveal the Ubuntu
                # desktop. Establish the fullscreen guard before audio.
                self._start_audio_only_guard_locked()
            self._configure_hdmi_audio()
            self._start_layer(kind, item, loop, random_start, volume)
            self._release_layer_locked(layer)
            return {"action": "started", "media_id": requested_id}

        if update.get("enabled") is True and layer.process is None:
            raise PlayerError(f"{kind}.enabled=true requires media_id while idle")

        changed: list[str] = []
        active_item = (
            self.catalog.get(kind, layer.item_id)
            if layer.item_id is not None
            else None
        )
        is_stream = active_item is not None and active_item.source_type == "stream"
        if "loop" in update:
            if is_stream:
                layer.loop = False
                changed.append("loop_ignored_live")
            else:
                self._set_layer_loop_locked(layer, update["loop"])
                changed.append("loop")
        if "volume" in update:
            self._set_layer_volume_locked(layer, update["volume"])
            changed.append("volume")
        if "random_start" in update:
            # This is a next-load preference. It must never seek/restart the
            # currently playing item merely because the helper changed.
            layer.random_start = False if is_stream else update["random_start"]
            changed.append(
                "random_start_ignored_live" if is_stream else "random_start_next_load"
            )
        if update.get("enabled") is True and layer.process is not None and layer.paused:
            self._release_layer_locked(layer)
            changed.append("resumed")
        return {"action": "updated" if changed else "unchanged", "changed": changed}

    def patch_state(self, value: Any) -> dict[str, Any]:
        """Apply only the named layer/property updates.

        Absence is the contract: a layer not included in the request is never
        stopped, loaded, sought, paused, resumed, or re-randomized.
        """
        if not isinstance(value, dict) or not value:
            raise PlayerError("state patch must be a non-empty JSON object")
        unknown = set(value) - set(self.layers)
        if unknown:
            raise PlayerError(
                f"unknown state patch section(s): {', '.join(sorted(unknown))}"
            )
        with self.lock:
            normalized = {
                kind: self._normalize_layer_patch(kind, update)
                for kind, update in value.items()
            }
            applied = {
                kind: self._apply_layer_patch_locked(kind, update)
                for kind, update in normalized.items()
            }
            self.last_error = None
            result = self.status()
            result["patched"] = applied
            return result

    def play(self, composition: Any) -> dict[str, Any]:
        with self.lock:
            selected = self.validate_composition(composition)
            video_item = (
                self.catalog.get("video", selected["visual"])
                if selected["visual_enabled"]
                else None
            )
            soundtrack_item = (
                self.catalog.get("soundtrack", selected["soundtrack"])
                if selected["soundtrack"] is not None
                else None
            )
            if soundtrack_item is not None and soundtrack_item.source_type == "stream":
                selected["soundtrack_loop"] = False
                selected["soundtrack_random_start"] = False
            display_item = video_item
            if display_item is None and soundtrack_item is not None:
                display_item = AUDIO_ONLY_ITEM
            self.stop()
            try:
                if display_item is not None:
                    self._wait_for_hdmi()
                    self._wake_display()
                if display_item is not None or soundtrack_item is not None:
                    self._configure_hdmi_audio()
                if display_item is not None:
                    self._start_layer(
                        "video",
                        display_item,
                        selected["video_loop"] if video_item is not None else True,
                        selected["video_random_start"] if video_item is not None else False,
                        selected["original_volume"] if video_item is not None else 0,
                    )
                if soundtrack_item is not None:
                    self._start_layer(
                        "soundtrack",
                        soundtrack_item,
                        selected["soundtrack_loop"],
                        selected["soundtrack_random_start"],
                        selected["soundtrack_volume"],
                    )
                # Both processes are created paused, then released together.
                # This prevents a slow second process from consuming seconds of
                # the first layer before the composition is ready.
                release_time = time.time()
                for layer in self.layers.values():
                    if layer.process is not None:
                        self._ipc(layer, ["set_property", "pause", False])
                        layer.paused = False
                        layer.started_at = release_time
                self.last_error = None
                result = self.status()
                result["composition"] = selected
                return result
            except Exception as exc:
                self.stop()
                self.last_error = getattr(exc, "code", "unavailable")  # never a path/message
                raise

    def pause(self, paused: bool, kind: str | None = None) -> dict[str, Any]:
        with self.lock:
            if kind is not None and kind not in self.layers:
                raise PlayerError("unknown playback layer")
            targets = [self.layers[kind]] if kind else list(self.layers.values())
            active = 0
            for layer in targets:
                self._refresh_layer(layer)
                if layer.process is not None:
                    self._ipc(layer, ["set_property", "pause", paused])
                    layer.paused = paused
                    active += 1
            if active == 0:
                raise PlayerError("no requested playback layer is active", "unavailable")
            return self.status()

    def shutdown(self) -> None:
        with self.lock:
            self.stop()


class AmbientHTTPServer(ThreadingHTTPServer):
    """Threaded server with at most MAX_CONNECTIONS live connections; extras are closed unanswered."""

    daemon_threads = True
    allow_reuse_address = True
    player: DualAmbientPlayer
    allowed_clients: frozenset[str]
    allowed_hosts: frozenset[str]
    auth_token: str
    slots: threading.BoundedSemaphore

    def process_request(self, request, client_address) -> None:
        if not self.slots.acquire(blocking=False):
            self.close_request(request)
            return
        super().process_request(request, client_address)

    def shutdown_request(self, request) -> None:
        super().shutdown_request(request)
        self.slots.release()


def default_hosts(host: str, port: int) -> frozenset[str]:
    """Host header values accepted when allowed_hosts is not configured."""
    names = {host, "localhost", "127.0.0.1"} if ipaddress.ip_address(host).is_loopback else {host}
    return frozenset(value for name in names for value in (name, f"{name}:{port}"))


def make_server(config: dict[str, Any]) -> AmbientHTTPServer:
    """Build the player and bind (not yet serve) the HTTP server for a validated config."""
    player = DualAmbientPlayer(config)
    server = AmbientHTTPServer((config["bind_host"], config["port"]), Handler)
    server.player = player
    server.allowed_clients = frozenset(config["allowed_clients"])
    server.auth_token = config["auth_token"]
    server.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
    server.allowed_hosts = frozenset(config["allowed_hosts"]) or default_hosts(
        config["bind_host"], server.server_port
    )
    return server


class Handler(BaseHTTPRequestHandler):
    server: AmbientHTTPServer
    protocol_version = "HTTP/1.1"
    timeout = 10  # socket read timeout: a stalled client cannot pin a handler thread

    def log_message(self, message: str, *args: Any) -> None:
        LOGGER.info("%s %s", self.client_address[0], message % args)

    def _send_json(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _rejection(self) -> tuple[HTTPStatus, str] | None:
        """Guard order is part of the API: IP 403, Host 421, bearer token 401, JSON body 415."""
        if self.client_address[0] not in self.server.allowed_clients:
            return HTTPStatus.FORBIDDEN, "forbidden"
        if (self.headers.get("Host") or "").lower() not in self.server.allowed_hosts:
            return HTTPStatus.MISDIRECTED_REQUEST, "host_not_allowed"
        presented = (self.headers.get("Authorization") or "").encode()
        if not hmac.compare_digest(presented, f"Bearer {self.server.auth_token}".encode()):
            return HTTPStatus.UNAUTHORIZED, "unauthorized"
        if (
            self.command in ("POST", "PUT", "PATCH")
            and self.headers.get("Content-Length", "0") != "0"
            and not (self.headers.get("Content-Type") or "").lower().startswith("application/json")
        ):
            return HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "unsupported_media_type"
        return None

    def _authorized(self) -> bool:
        rejection = self._rejection()
        if rejection is None:
            return True
        self.close_connection = True  # the unread body must not be parsed as the next request
        self._send_json(rejection[0], {"ok": False, "error": rejection[1]})
        return False

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError as exc:
            self.close_connection = True
            raise PlayerError("invalid Content-Length") from exc
        if not 0 <= length <= MAX_REQUEST_BYTES:
            self.close_connection = True
            raise PlayerError("request body is too large")
        if length == 0:
            return {}
        try:
            value = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise PlayerError("request body is not valid JSON") from exc
        if not isinstance(value, dict):
            raise PlayerError("request body must be a JSON object")
        return value

    def _handle_error(self, exc: Exception) -> None:
        if isinstance(exc, PlayerError):
            LOGGER.warning("%s %s rejected (%s): %s", self.command, self.path, exc.code, exc)
            self._send_json(HTTPStatus.CONFLICT, {"ok": False, "error": exc.code})
        else:
            LOGGER.error("%s %s failed", self.command, self.path, exc_info=exc)
            self.server.player.last_error = "unavailable"
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "error": "unavailable"},
            )

    def do_GET(self) -> None:
        if not self._authorized():
            return
        path = urlparse(self.path).path
        try:
            if path in ("/health", "/v2/status"):
                result = self.server.player.status()
            elif path == "/v2/catalog":
                result = self.server.player.catalog_public()
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
                return
            self._send_json(HTTPStatus.OK, result)
        except Exception as exc:  # noqa: BLE001 - any failure becomes a generic 409/500
            self._handle_error(exc)

    def do_POST(self) -> None:
        if not self._authorized():
            return
        path = urlparse(self.path).path
        try:
            body = self._body()
            if path == "/v2/stop":
                result = self.server.player.stop()
            elif path == "/v2/pause":
                result = self.server.player.pause(True)
            elif path == "/v2/resume":
                result = self.server.player.pause(False)
            elif path == "/v2/play":
                result = self.server.player.play(body)
            elif path == "/v2/catalog/refresh":
                result = self.server.player.refresh_catalog()
            elif path == "/v2/video/stop":
                result = self.server.player.stop("video")
            elif path == "/v2/soundtrack/stop":
                result = self.server.player.stop("soundtrack")
            elif path == "/v2/video/pause":
                result = self.server.player.pause(True, "video")
            elif path == "/v2/video/resume":
                result = self.server.player.pause(False, "video")
            elif path == "/v2/soundtrack/pause":
                result = self.server.player.pause(True, "soundtrack")
            elif path == "/v2/soundtrack/resume":
                result = self.server.player.pause(False, "soundtrack")
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
                return
            self._send_json(HTTPStatus.OK, result)
        except Exception as exc:  # noqa: BLE001 - see do_GET
            self._handle_error(exc)

    def do_PATCH(self) -> None:
        if not self._authorized():
            return
        path = urlparse(self.path).path
        try:
            body = self._body()
            if path == "/v2/state":
                result = self.server.player.patch_state(body)
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
                return
            self._send_json(HTTPStatus.OK, result)
        except Exception as exc:  # noqa: BLE001 - see do_GET
            self._handle_error(exc)


def config_from_dict(config: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalise a configuration mapping. Port 0 (ephemeral) is accepted here for tests only."""
    required = {
        "bind_host",
        "allowed_clients",
        "auth_token",
        "video_root",
        "soundtrack_root",
        "state_file",
    }
    missing = required - set(config)
    if missing:
        raise PlayerError(f"missing configuration keys: {sorted(missing)}")
    try:
        config["bind_host"] = str(ipaddress.ip_address(config["bind_host"]))
        config["allowed_clients"] = [
            str(ipaddress.ip_address(value)) for value in config["allowed_clients"]
        ]
        config["port"] = int(config.get("port", DEFAULT_PORT))
    except (TypeError, ValueError) as exc:
        raise PlayerError("bind_host and allowed_clients must be IP addresses; port an integer") from exc
    if not config["allowed_clients"]:
        raise PlayerError("allowed_clients must not be empty")
    if not 0 <= config["port"] <= 65535:
        raise PlayerError("port must be between 1 and 65535")
    token = config["auth_token"]
    if not isinstance(token, str) or len(token) < MIN_TOKEN_LENGTH:
        raise PlayerError(f"auth_token must be at least {MIN_TOKEN_LENGTH} characters")
    hosts = config.setdefault("allowed_hosts", [])
    if not isinstance(hosts, list) or not all(isinstance(host, str) and host for host in hosts):
        raise PlayerError("allowed_hosts must be a list of host[:port] names")
    config["allowed_hosts"] = [host.lower() for host in hosts]
    streams = config.get("soundtrack_streams", {})
    if not isinstance(streams, dict):
        raise PlayerError("soundtrack_streams must be a JSON object")
    for media_id, stream_url in streams.items():
        if not isinstance(media_id, str) or not ID_RE.fullmatch(media_id):
            raise PlayerError("soundtrack_streams contains an invalid media id")
        if not isinstance(stream_url, str):
            raise PlayerError(f"stream URL must be text: {media_id}")
        parsed = urlparse(stream_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise PlayerError(f"stream URL must be credential-free HTTPS: {media_id}")
    return config


def load_config(path: Path) -> dict[str, Any]:
    config = config_from_dict(json.loads(path.read_text(encoding="utf-8")))
    if config["port"] == 0:
        raise PlayerError("port must be between 1 and 65535")
    return config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config(args.config)
    if args.check:
        print(json.dumps(DualAmbientPlayer(config).status(), sort_keys=True))
        return 0
    server = make_server(config)
    player = server.player
    stopping = threading.Event()

    def stop_handler(_signum, _frame):
        if stopping.is_set():
            return
        stopping.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        player.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

