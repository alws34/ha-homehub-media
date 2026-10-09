"""Media catalog scanning, radio and composition validation (pure; scan() blocks, run it in an executor)."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")  # use .fullmatch(): "$" alone tolerates a trailing "\n"
VIDEO_EXT = frozenset({".mp4", ".mkv", ".webm", ".mov"})
AUDIO_EXT = frozenset({".mp3", ".ogg", ".opus", ".flac", ".m4a", ".wav"})
PREVIEW_EXT = (".webp", ".jpg", ".jpeg", ".png")
_EXT = {"visual": VIDEO_EXT, "soundtrack": AUDIO_EXT}
# Composition defaults (K2); visual_enabled defaults to `visual is not None`, so it is not listed here.
_DEFAULTS: dict[str, Any] = {
    "visual": None, "video_loop": True, "video_random_start": True, "original_volume": 20,
    "soundtrack": None, "soundtrack_loop": True, "soundtrack_random_start": True, "soundtrack_volume": 100,
}
_IDS = ("visual", "soundtrack")
_VOLUMES = ("original_volume", "soundtrack_volume")


def slugify(stem: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")[:64].strip("_") or "media"


def title_from_stem(stem: str) -> str:
    return stem.replace("_", " ").strip()


def _usable(path: Path, base: Path) -> bool:
    """Readable regular file whose symlinks (if any) resolve inside the (resolved) root."""
    real = path.resolve()
    return real.is_relative_to(base) and real.is_file() and os.access(real, os.R_OK)


def scan(root: Path, kind: str) -> list[dict]:
    exts, base, used, items = _EXT[kind], root.resolve(), set(), []
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix().lower()):
        if path.suffix.lower() not in exts or not _usable(path, base):
            continue
        rel = path.relative_to(root)
        slug = media_id = slugify(path.stem)
        n = 2
        while media_id in used:
            suffix = f"_{n}"
            media_id, n = f"{slug[:64 - len(suffix)]}{suffix}", n + 1
        used.add(media_id)
        previews = (e for e in PREVIEW_EXT if _usable(path.with_suffix(e), base))
        preview = next((rel.with_suffix(e).as_posix() for e in previews), None)
        items.append({
            "id": media_id, "kind": kind, "title": title_from_stem(path.stem), "source_type": "file",
            "live": False, "duration": None, "path": rel.as_posix(), "preview_path": preview,
        })
    return items


def radio_items(streams: dict[str, str]) -> list[dict]:
    items = []
    for media_id, url in sorted(streams.items()):
        if not isinstance(media_id, str) or not ID_RE.fullmatch(media_id):
            raise ValueError(f"invalid radio id: {media_id!r}")
        u = urlparse(url if isinstance(url, str) else "")
        if u.scheme != "https" or not u.hostname or u.fragment or "@" in u.netloc:
            raise ValueError(f"radio URL must be credential-free HTTPS: {media_id}")
        label = title_from_stem(media_id)
        if label.startswith("radio "):
            label = f"{label[6:]} live"
        items.append({
            "id": media_id, "kind": "soundtrack", "title": label.title(), "source_type": "stream",
            "live": True, "duration": None, "url": url,  # url: server-side only, like "path"
        })
    return items


def validate_composition(data: dict, partial: bool = False) -> dict:
    if not isinstance(data, dict):
        raise ValueError("composition must be an object")  # noqa: TRY004 - callers catch ValueError only
    if unknown := set(data) - _DEFAULTS.keys() - {"visual_enabled"}:
        raise ValueError(f"unknown composition keys: {sorted(map(str, unknown))}")
    for key, value in data.items():
        if key in _IDS:
            ok = value is None or (isinstance(value, str) and ID_RE.fullmatch(value) is not None)
        elif key in _VOLUMES:
            ok = type(value) is int and 0 <= value <= 100
        else:
            ok = type(value) is bool
        if not ok:
            raise ValueError(f"invalid value for {key}")
    if partial:
        return dict(data)
    comp = {**_DEFAULTS, **data}
    comp.setdefault("visual_enabled", comp["visual"] is not None)
    if comp["visual_enabled"] and comp["visual"] is None:
        raise ValueError("visual_enabled requires a visual")
    return comp
