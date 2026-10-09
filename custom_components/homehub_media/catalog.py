"""Media catalog scanning, radio and composition validation (pure; scan() blocks, run it in an executor)."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")  # use .fullmatch(): "$" alone tolerates a trailing "\n"
YT_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")  # use .fullmatch()
YT_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"})
VISUALS_MAX = 50
VIDEO_EXT = frozenset({".mp4", ".mkv", ".webm", ".mov"})
AUDIO_EXT = frozenset({".mp3", ".ogg", ".opus", ".flac", ".m4a", ".wav"})
PREVIEW_EXT = (".webp", ".jpg", ".jpeg", ".png")
_EXT = {"visual": VIDEO_EXT, "soundtrack": AUDIO_EXT}
# Composition defaults (K2); visual_enabled defaults to `visual is not None`, so it is not listed here.
_DEFAULTS: dict[str, Any] = {
    "visual": None, "video_loop": True, "video_random_start": True, "original_volume": 20,
    "soundtrack": None, "soundtrack_loop": True, "soundtrack_random_start": True, "soundtrack_volume": 100,
    "visuals": [],  # pool for a random visual per start; empty = use "visual"
}
_IDS = ("visual", "soundtrack")
_VOLUMES = ("original_volume", "soundtrack_volume")


def slugify(stem: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")[:64].strip("_") or "media"


def title_from_stem(stem: str) -> str:
    return stem.replace("_", " ").strip()


def _unique(slug: str, used: set[str]) -> str:
    """slug, or slug_2, slug_3, ... (still at most 64 characters); recorded in `used`."""
    media_id, n = slug, 2
    while media_id in used:
        suffix = f"_{n}"
        media_id, n = f"{slug[:64 - len(suffix)]}{suffix}", n + 1
    used.add(media_id)
    return media_id


def _usable(path: Path, base: Path) -> bool:
    """Readable regular file whose symlinks (if any) resolve inside the (resolved) root."""
    real = path.resolve()
    return real.is_relative_to(base) and real.is_file() and os.access(real, os.R_OK)


def scan(root: Path, kind: str) -> list[dict]:
    # ponytail: ids derive from sorted filenames, so adding a same-stem file can shift ids (rain -> rain_2);
    # upgrade path = persist a path -> id map.
    exts, base, used, items = _EXT[kind], root.resolve(), set(), []
    paths = sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix().lower())
    pics: dict[Path, list[Path]] = {}  # previews keyed by extension-less path
    for p in paths:
        if p.suffix.lower() in PREVIEW_EXT:
            pics.setdefault(p.with_suffix(""), []).append(p)
    for path in paths:
        rel = path.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):  # hidden files/dirs, e.g. macOS "._Rain.mp4"
            continue
        if path.suffix.lower() not in exts or not _usable(path, base):
            continue
        media_id = _unique(slugify(path.stem), used)
        sides = sorted(pics.get(path.with_suffix(""), []), key=lambda p: PREVIEW_EXT.index(p.suffix.lower()))
        preview = next((p.relative_to(root).as_posix() for p in sides if _usable(p, base)), None)
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
            "live": True, "duration": None, "url": u.geturl(),  # normalised; server-side only, like "path"
        })
    return items


def youtube_id(value: str) -> str:
    """Video id of a YouTube watch/shorts/live/youtu.be link or of a bare id; ValueError otherwise."""
    value = value.strip() if isinstance(value, str) else ""
    if YT_ID_RE.fullmatch(value):
        return value
    u = urlparse(value if "://" in value else f"https://{value}")
    host, parts = (u.hostname or "").lower(), u.path.strip("/").split("/")
    vid = ""
    if u.scheme in ("http", "https") and host in ("youtu.be", "www.youtu.be"):
        vid = parts[0]
    elif u.scheme in ("http", "https") and host in YT_HOSTS:
        if parts == ["watch"]:
            vid = parse_qs(u.query).get("v", [""])[0]
        elif len(parts) >= 2 and parts[0] in ("shorts", "live", "embed"):
            vid = parts[1]
    if not YT_ID_RE.fullmatch(vid):
        raise ValueError(f"not a YouTube video link: {value[:100]}")
    return vid


def youtube_items(entries: dict[str, str], taken: frozenset[str] | set[str] = frozenset()) -> list[dict]:
    """Visuals for {name: YouTube link}, in the given order; ids avoid `taken` (the scanned files' ids)."""
    used, items = set(taken), []
    for name, url in entries.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("every YouTube video needs a name")
        vid = youtube_id(url)
        # a name without Latin letters (Hebrew, emoji) gets an id from its video, not an order-dependent media_2
        media_id = _unique(slugify(name) if re.search(r"[A-Za-z]", name) else f"yt_{vid.lower()}", used)
        items.append({
            "id": media_id, "kind": "visual", "title": name.strip(), "source_type": "youtube", "live": False,
            "duration": None, "youtube_id": vid, "preview": f"/api/homehub_media/art/youtube/{vid}",
            "link": url.strip(),  # the card's mode editor shows and edits it
        })
    return items


def validate_composition(data: dict, partial: bool = False) -> dict:
    """Strictly validate a composition; raises ValueError.

    partial=True checks only the given keys and skips the cross-field rule "visual_enabled requires a
    visual", so callers must fully re-validate the merged composition (partial=False) before using it.
    """
    if not isinstance(data, dict) or set(data) - _DEFAULTS.keys() - {"visual_enabled"}:
        raise ValueError("composition must be an object with known keys")
    comp = {}
    for key, value in data.items():
        if key in _IDS:
            ok = value is None or (isinstance(value, str) and ID_RE.fullmatch(value) is not None)
        elif key == "visuals":
            ok = (isinstance(value, list) and len(value) <= VISUALS_MAX
                  and all(isinstance(v, str) and ID_RE.fullmatch(v) for v in value))
            value = list(dict.fromkeys(value)) if ok else value  # de-duplicated, order kept
        elif key in _VOLUMES:
            # bool is an int; NaN fails the range check
            ok = isinstance(value, int | float) and not isinstance(value, bool) and 0 <= value <= 100
            value = round(value) if ok else value
        else:
            ok = type(value) is bool
        if not ok:
            raise ValueError(f"invalid value for {key}")
        comp[key] = value
    if partial:
        return comp
    comp = {**_DEFAULTS, **comp}
    comp["visuals"] = list(comp["visuals"])  # never share the default list
    comp.setdefault("visual_enabled", comp["visual"] is not None or bool(comp["visuals"]))
    if comp["visual_enabled"] and comp["visual"] is None and not comp["visuals"]:
        raise ValueError("visual_enabled requires a visual")
    return comp
