# HomeHub Media Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `homehub_media`, a HACS custom integration plus bundled Lovelace card, that replaces the HomeHub YAML bundle, controls a Samsung Tizen TV directly, optionally drives the original Ubuntu media PC / streaming box / ESP32 keyboard, and adds Plex/Jellyfin library, ambience, sleep timer, dayparts, blueprints and an LLM voice assistant.

**Architecture:** One config entry owns a `Hub` (runtime_data) that composes small single-purpose clients (TV, media PC, keyboard bridge, library) and two managers (activity, ambience). Services, entities, a websocket API and the card all talk to the Hub through the contracts in section "Contracts". The Ubuntu side lives in `media-pc/` (not installed by HACS) and is hardened with bearer-token auth.

**Tech Stack:** Python 3.14, Home Assistant 2026.9.4 (`pytest-homeassistant-custom-component==0.13.367`), `samsungtvws[async,encrypted]` (already in HA), aiohttp, vanilla JS web component (no build step), stdlib-only Python for `media-pc/`.

**Spec:** `docs/superpowers/specs/2026-10-09-homehub-media-design.md`

**Ponytail rule for every worker:** the smallest code that satisfies the contract and its tests. No abstractions beyond the contracts below, no new dependencies, stdlib first. Deliberate shortcuts get a `# ponytail: <ceiling>, <upgrade path>` comment. Input validation, auth, and escaping are never simplified away.

**Note on code in this plan:** contract files, manifests, CI and tests are given in full. Module internals are specified as exact signatures + behaviour + tests; the worker writes the body (shortest version that passes). The original implementation to port from is cited by file:line (paths relative to repo root at commit `32557aa`, under `home-assistant/` and `ubuntu/`).

---

## Conventions (all tasks)

- Dev env (once per worktree): `uv venv .venv --python 3.14 && uv pip install -p .venv -r requirements_test.txt`
- Run tests: `.venv/bin/pytest -q` (single file: `.venv/bin/pytest -q tests/test_x.py`)
- Lint: `.venv/bin/ruff check custom_components tests media-pc`
- Never block the event loop: file system and `subprocess` work goes through `hass.async_add_executor_job`.
- HTTP from HA: `async_get_clientsession(hass)`; every request has `timeout=aiohttp.ClientTimeout(total=10)` (pairing: 31).
- Never log tokens/API keys. Never return raw server errors to the frontend.
- Raise `ServiceValidationError` for bad user input, `HomeAssistantError` for device/network failures.
- Commit after every green step: `git commit -m "<type>(<scope>): <what>"` and end the message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File map

```
custom_components/homehub_media/
  __init__.py        setup/unload; builds Hub; registers static path, frontend JS, views, ws, services
  manifest.json  hacs.json(root)  services.yaml  translations/en.json  brand/icon.png
  const.py           DOMAIN, option keys, defaults, logical command maps, app catalog
  hub.py             Hub: composition root, snapshot(), listeners, command routing
  activity.py        ActivityManager: off/tv/box/pc state machine, inputs, quiet volume
  ambience.py        AmbienceManager: modes, lighting snapshot/scene/flicker, playback, sleep, dayparts, health
  catalog.py         pure: media scan, ids, radio validation, composition validation
  store.py           persisted compositions + favourites (helpers.storage.Store)
  tv.py              SamsungTV client (WS + REST + WoL)
  pc.py              AmbientPlayerClient, CoordinatorClient (media-PC HTTP, bearer auth)
  keyboard.py        KeyboardBridge (ESP32)
  library.py         PlexServer, JellyfinServer, Library (poll, normalise, sessions, play)
  api.py             websocket commands, HTTP views (TV page state, artwork proxy), service registration
  assistant.py       llm.API tools + provider adapters (ollama/openai/claude)
  config_flow.py     config flow (pair TV) + options menu
  select.py switch.py sensor.py conversation.py
  www/homehub-media-card.js   www/ambience.html
media-pc/ambient-player/  media-pc/media-hub-coordinator/  media-pc/systemd-user/  media-pc/tests/
blueprints/automation/homehub_media/*.yaml
tests/conftest.py tests/test_*.py
docs/  README.md  LICENSE  hacs.json  requirements_test.txt  pyproject.toml  .github/workflows/validate.yml
```

---

## Contracts (frozen before Phase 1; every task codes against these)

### K1. `const.py` (full file, created in Task 0)

```python
"""Constants and frozen contracts for HomeHub Media."""
from __future__ import annotations

DOMAIN = "homehub_media"
STATIC_URL = f"/{DOMAIN}_static"
CARD_FILE = "homehub-media-card.js"
PAGE_FILE = "ambience.html"
SIGNAL_UPDATE = f"{DOMAIN}_update"

# config entry data
CONF_HOST = "host"
CONF_MAC = "mac"
CONF_TOKEN = "token"
CONF_PORT = "port"  # 8002 (wss) default
CLIENT_NAME = "HomeHub"

# option sections (entry.options[SECTION] is a dict)
OPT_TV = "tv"              # {"entity": str|None, "app_entity": str|None, "base_url": str|None}
OPT_APPS = "apps"          # {"enabled": [app_key...], "ids": {app_key: tizen_id}}
OPT_SERVERS = "servers"    # {"plex": {url, token, player}|None, "jellyfin": {url, api_key, user_id, player}|None}
OPT_BOX = "box"            # {"remote": str, "media_player": str|None, "input": INPUT}|None
OPT_PC = "pc"              # {"host", "ambient_port", "ambient_token", "coord_port", "coord_token", "input": INPUT, "clients": [str]}|None
OPT_KEYBOARD = "keyboard"  # {"host": str, "key": str}|None
OPT_AMBIENCE = "ambience"  # {"backend": "tv"|"pc", "radio": {id: https_url}}
OPT_MODES = "modes"        # [MODE...]
OPT_VOLUME = "volume"      # {"normal": int, "quiet": int, "block_entity": str|None}
OPT_DAYPARTS = "dayparts"  # {"morning": {"start": "06:00", "mode": id|None}, "afternoon":…, "evening":…, "night":…, "idle_minutes": int}
OPT_ASSISTANT = "assistant"  # {"provider": "ollama"|"openai"|"claude", "url": str|None, "api_key": str|None, "model": str}|None
# INPUT = {"method": "source"|"key", "value": str}   e.g. {"method": "source", "value": "HDMI1"} or {"method": "key", "value": "KEY_HDMI3"}
# MODE  = {"id": str, "name": str, "icon": str, "scene": str|None, "flicker": [light entity ids], "lights_only": bool}

ACTIVITIES = ("off", "tv", "box", "pc")
PHASES = ("ready", "transitioning", "needs_reset", "blocked")
TARGETS = ("tv", "box", "pc")

DEFAULT_MODES = [
    {"id": "fireplace", "name": "Fireplace", "icon": "mdi:fireplace", "scene": None, "flicker": [], "lights_only": False},
    {"id": "dinner", "name": "Dinner", "icon": "mdi:silverware-fork-knife", "scene": None, "flicker": [], "lights_only": False},
    {"id": "reading", "name": "Reading", "icon": "mdi:book-open-variant", "scene": None, "flicker": [], "lights_only": False},
    {"id": "game_night", "name": "Game Night", "icon": "mdi:dice-multiple", "scene": None, "flicker": [], "lights_only": False},
    {"id": "chill", "name": "Chill", "icon": "mdi:sofa", "scene": None, "flicker": [], "lights_only": False},
    {"id": "movie_time", "name": "Movie Time", "icon": "mdi:movie-open", "scene": None, "flicker": [], "lights_only": True},
    {"id": "dog_tv", "name": "DOG TV", "icon": "mdi:dog", "scene": None, "flicker": [], "lights_only": False},
]
PREFERRED_VISUAL = {"fireplace": "fireplace", "dinner": "aquarium", "reading": "hogwarts",
                    "game_night": "abstract", "chill": "rain", "dog_tv": "dog_tv"}
DEFAULT_VOLUME = {"normal": 19, "quiet": 10, "block_entity": None}
DEFAULT_DAYPARTS = {"morning": {"start": "06:00", "mode": None}, "afternoon": {"start": "12:00", "mode": None},
                    "evening": {"start": "18:00", "mode": None}, "night": {"start": "22:00", "mode": None},
                    "idle_minutes": 10}

# logical remote commands -> per-target key names
COMMANDS = ("up", "down", "left", "right", "select", "back", "home", "power", "play_pause", "play", "pause",
            "stop", "rewind", "fast_forward", "volume_up", "volume_down", "mute", "channel_up", "channel_down",
            "guide", "menu", "info", "exit")
TV_KEYS = {"up": "KEY_UP", "down": "KEY_DOWN", "left": "KEY_LEFT", "right": "KEY_RIGHT", "select": "KEY_ENTER",
           "back": "KEY_RETURN", "home": "KEY_HOME", "power": "KEY_POWER", "play": "KEY_PLAY", "pause": "KEY_PAUSE",
           "stop": "KEY_STOP", "rewind": "KEY_REWIND", "fast_forward": "KEY_FF", "volume_up": "KEY_VOLUP",
           "volume_down": "KEY_VOLDOWN", "mute": "KEY_MUTE", "channel_up": "KEY_CHUP", "channel_down": "KEY_CHDOWN",
           "guide": "KEY_GUIDE", "menu": "KEY_MENU", "info": "KEY_INFO", "exit": "KEY_EXIT"}
BOX_KEYS = {"up": "DPAD_UP", "down": "DPAD_DOWN", "left": "DPAD_LEFT", "right": "DPAD_RIGHT", "select": "DPAD_CENTER",
            "back": "BACK", "home": "HOME", "power": "POWER", "play_pause": "MEDIA_PLAY_PAUSE", "play": "MEDIA_PLAY",
            "pause": "MEDIA_PAUSE", "stop": "MEDIA_STOP", "rewind": "MEDIA_REWIND", "fast_forward": "MEDIA_FAST_FORWARD",
            "channel_up": "CHANNEL_UP", "channel_down": "CHANNEL_DOWN", "guide": "GUIDE", "menu": "MENU",
            "info": "INFO", "exit": "BACK"}
PC_COMMANDS = {"up": "up", "down": "down", "left": "left", "right": "right", "select": "select", "back": "back",
               "home": "home", "play_pause": "play_pause", "stop": "stop"}
VOLUME_COMMANDS = ("volume_up", "volume_down", "mute")  # always routed to the TV, whatever the target

# Tizen app catalog: candidate IDs in probe order (verified 2026-10-09 on QE55Q60B where noted)
APP_CATALOG: dict[str, dict] = {
    "netflix": {"name": "Netflix", "ids": ["3201907018807", "11101200001"]},
    "youtube": {"name": "YouTube", "ids": ["111299001912"]},                      # verified
    "prime_video": {"name": "Prime Video", "ids": ["3201910019365", "3201512006785"]},  # verified first
    "disney_plus": {"name": "Disney+", "ids": ["3201901017640", "3202204027038", "3202009021709"]},  # verified first
    "apple_tv": {"name": "Apple TV", "ids": ["3201807016597"]},                   # verified
    "max": {"name": "Max", "ids": ["3202301029760", "3201601007230"]},
    "plex": {"name": "Plex", "ids": ["3201512006963"], "server": "plex"},          # verified
    "jellyfin": {"name": "Jellyfin", "ids": ["3202007021208", "AprZAARz4r.Jellyfin"], "server": "jellyfin"},
    "spotify": {"name": "Spotify", "ids": ["3201606009684"]},                     # verified
    "twitch": {"name": "Twitch", "ids": ["3202203026841"]},
    "paramount_plus": {"name": "Paramount+", "ids": ["3202110025305", "3201710014981"]},
    "browser": {"name": "Browser", "ids": ["3202010022079", "org.tizen.browser"]},  # verified first
}
# An app with "server" that is not installed falls back to opening that server's web URL in the TV browser.

MEDIA_ROOT = "homehub/ambience"          # under the HA media dir (/media on HAOS)
VIDEO_DIR, SOUNDTRACK_DIR = "videos", "soundtracks"
TEXT_MAX = 200
```

### K2. Data shapes

```text
CatalogItem  = {"id": str(ID_RE), "kind": "visual"|"soundtrack", "title": str, "source_type": "file"|"stream",
                "live": bool, "duration": float|None, "favorite": bool, "preview": str|None (URL, signed when served)}
               TV backend also keeps (server-side only, never sent to the card): "path": str (relative to MEDIA_ROOT/kind dir)
Composition  = {"visual": id|None, "visual_enabled": bool, "video_loop": bool, "video_random_start": bool,
                "original_volume": 0-100, "soundtrack": id|None, "soundtrack_loop": bool,
                "soundtrack_random_start": bool, "soundtrack_volume": 0-100}
               defaults: visual_enabled = visual is not None, loops True, random_start True, original_volume 20,
                         soundtrack_volume 100   (HA-side defaults of the original, homehub_ambient_player.yaml:140-149)
LibraryItem  = {"id": str, "server": "plex"|"jellyfin", "title": str, "subtitle": str|None, "type": str,
                "progress": float 0..1|None, "image": str|None (signed /api/homehub_media/art/... URL)}
NowPlaying   = {"server": str, "player": str, "title": str, "subtitle": str|None, "state": "playing"|"paused",
                "position": float sec, "duration": float sec|None, "image": str|None}
```

### K3. `Hub` public interface (hub.py; used by api.py, entities, assistant)

```python
class Hub:
    hass: HomeAssistant; entry: ConfigEntry
    options: dict                             # {section: dict} = defaults (K1) overlaid with entry.options
    last_command: float                       # time.monotonic() of the last user command (idle detection)
    tv: SamsungTV; pc_ambient: AmbientPlayerClient | None; pc_coord: CoordinatorClient | None
    keyboard: KeyboardBridge | None; library: Library | None
    activity: ActivityManager; ambience: AmbienceManager
    apps: dict[str, str]                      # app_key -> installed tizen id (from options or discovery)
    def snapshot(self) -> dict                # K4
    def async_add_listener(self, cb: Callable[[], None]) -> Callable[[], None]
    @callback
    def async_notify(self) -> None            # calls listeners + dispatcher SIGNAL_UPDATE
    async def async_start(self) -> None ; async def async_stop(self) -> None
    async def remote(self, command: str, target: str | None = None) -> None   # target None = current activity
    async def send_key(self, key: str, hold_secs: float = 0) -> None           # raw TV key
    async def launch_app(self, app: str) -> None                               # app_key or raw id
    async def restart_app(self, app: str) -> None
    async def send_text(self, text: str, target: str | None = None) -> None    # target: tv|pc|keyboard|None(auto)
    async def keyboard_key(self, key: str, target: str | None = None) -> None  # backspace|enter|clear
    async def pointer(self, action: str, dx: float = 0, dy: float = 0, target: str | None = None) -> None
                                                                               # action: move|click|right_click|scroll
    async def channel(self, number: str) -> None                               # streaming box
    async def pc_open(self, client: str) -> None ; async def pc_close(self) -> None
    async def play_library_item(self, server: str, item_id: str) -> None
    async def rescan_apps(self) -> dict[str, str]
    def sign_path(self, path: str) -> str     # async_sign_path(hass, path, timedelta(hours=24), use_content_user=True)
```

### K4. `Hub.snapshot()` (sent to the card on `homehub_media/subscribe`)

```json
{
  "tv": {"available": true, "power": "on", "volume": 20, "muted": false,
         "keyboard_active": false, "pointer_active": false, "app": "netflix"},
  "activity": {"current": "tv", "phase": "ready", "quiet": false, "available": ["off", "tv", "box", "pc"]},
  "targets": {"box": false, "pc": false, "keyboard": false},
  "apps": [{"key": "netflix", "name": "Netflix", "installed": true}],
  "ambience": {"backend": "tv", "mode": "off", "error": null, "sleep_until": null, "auto": false,
               "daypart_mode": "chill",
               "modes": [{"id": "dinner", "name": "Dinner", "icon": "mdi:silverware-fork-knife",
                          "lights_only": false, "lighting": true, "flicker": false}],
               "compositions": {"dinner": {"...Composition": "..."}}},
  "now_playing": [],
  "servers": ["plex"],
  "pc": {"clients": ["jellyfin", "sonarr", "radarr"], "active": null}
}
```

### K5. Websocket commands (api.py)

| type | params | result |
|---|---|---|
| `homehub_media/subscribe` | – | subscription; sends `{"event": snapshot}` immediately and on every `async_notify` |
| `homehub_media/catalog` | `refresh: bool=false` | `{"visuals": [CatalogItem], "soundtracks": [CatalogItem]}` (previews signed for the caller) |
| `homehub_media/library` | – | `{"plex": {"continue": [LibraryItem], "recent": [...]}, "jellyfin": {...}}` (only configured servers) |

Signing for `<img>`/media URLs in ws results: `async_sign_path(hass, path, timedelta(hours=24), refresh_token_id=connection.refresh_token_id)`.

### K6. Services (domain services, all in `services.yaml`; handlers in api.py call the Hub)

| service | fields (vol schema) |
|---|---|
| `remote` | `command: In(COMMANDS)`, `target: In(TARGETS)` optional |
| `send_key` | `key: Match(r"^KEY_[A-Z0-9_]{1,32}$")`, `hold_secs: Range(0,5)` optional |
| `launch_app` / `restart_app` | `app: Match(r"^[A-Za-z0-9._-]{1,64}$")` |
| `send_text` | `text: str len 1..TEXT_MAX, no control chars`, `target: In(tv,pc,keyboard)` optional |
| `keyboard_key` | `key: In(backspace,enter,clear)`, `target` optional |
| `pointer` | `action: In(move,click,right_click,scroll)`, `dx,dy: Range(-500,500)` optional, `target: In(tv,pc)` optional |
| `set_activity` | `activity: In(ACTIVITIES)`, `force: bool` optional |
| `channel` | `number: Match(r"^\d{1,4}$")` |
| `ambience_start` | `mode: slug` |
| `ambience_stop` | – |
| `ambience_update` | any subset of Composition fields (validated with `catalog.validate_composition(partial=True)`) |
| `save_mode` | `mode: slug`, plus Composition fields |
| `set_favorite` | `kind: In(visual,soundtrack)`, `media_id: Match(ID_RE)`, `favorite: bool` |
| `ambience_auto` | – |
| `ambience_sleep_timer` | `minutes: Range(0, 600)` |
| `refresh_catalog` / `rescan_apps` | – |
| `play_library_item` | `server: In(plex,jellyfin)`, `item_id: Match(r"^[A-Za-z0-9-]{1,64}$")` |
| `pc_open` | `client: slug` ; `pc_close` – |

### K7. TV ambience page protocol (api.py view ↔ www/ambience.html)

- Page URL opened on the TV: `{base}{STATIC_URL}/{PAGE_FILE}?s={urlquote(signed_state_path)}` where `base` = `options.tv.base_url` or `get_url(hass, allow_external=False, allow_cloud=False, prefer_external=False)`.
- `GET /api/homehub_media/page/state` (HomeAssistantView, `requires_auth=True`, accessed via signed path) returns:

```json
{"version": 7, "stop": false, "fade_secs": null,
 "visual": {"url": "/media/local/homehub/ambience/videos/fire.mp4?authSig=...", "loop": true,
            "random_start": true, "volume": 0.2} ,
 "soundtrack": {"url": "...", "loop": true, "random_start": true, "volume": 1.0},
 "next": "/api/homehub_media/page/state?authSig=..."}
```
  `visual`/`soundtrack` may be `null`. `version` increments on every change. Each request records `last_seen` (heartbeat). `next` is a freshly signed state path (24 h) the page must use for its next poll.
- Page polls every 1000 ms; on new `version` applies changes without reload when only volumes/loop changed, reloads the element when the URL changed; `fade_secs` → linear volume ramp to 0; `stop: true` → pause both and show black.

### K8. Media-PC HTTP API after hardening (pc.py ↔ media-pc daemons)

Every request: `Authorization: Bearer <token>`, `Host: <configured host>:<port>`, JSON bodies with `Content-Type: application/json`.

Ambient player (port 8097): `GET /health`, `GET /v2/status`, `GET /v2/catalog`, `POST /v2/catalog/refresh`, `POST /v2/play` (Composition), `PATCH /v2/state` (`{"video": {...}, "soundtrack": {...}}` exactly as original AP:857-994), `POST /v2/stop`, `POST /v2/pause`, `POST /v2/resume`. Removed: `/v1/*`, `/v2/profiles*`, `/v2/favorites*`, `/v2/play-profile/*`.

Coordinator (port 8098): `GET /health`, `GET /v1/status`, `POST /v1/open/{client}`, `POST /v1/close`, `POST /v1/remote/{cmd}`, `POST /v1/keyboard/text {text, submit}`, `POST /v1/pointer/move {dx,dy}`, `POST /v1/pointer/click {button}`, `POST /v1/pointer/scroll {dy}`. Errors: 401 bad/missing token, 403 IP, 421 bad Host, 415 bad content type, 409 domain error `{"error": "..."}`.

---

## Phase 0 — Scaffolding (sequential, orchestrator)

### Task 0: Repo skeleton, test harness, CI

**Files:**
- Move: `ubuntu/` → `media-pc/` (`git mv ubuntu media-pc`)
- Create: `custom_components/homehub_media/{__init__.py,const.py,manifest.json,services.yaml,translations/en.json}`
- Create: `hacs.json`, `LICENSE`, `pyproject.toml`, `requirements_test.txt`, `tests/conftest.py`, `tests/test_init.py`, `.github/workflows/validate.yml`

- [ ] **Step 1: Move the Ubuntu side and add root files**

```bash
git mv ubuntu media-pc
```

`hacs.json`:
```json
{ "name": "HomeHub Media", "homeassistant": "2026.9.0" }
```

`requirements_test.txt`:
```
pytest-homeassistant-custom-component==0.13.367
samsungtvws[async,encrypted]==3.0.5
ruff
```

`pyproject.toml`:
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests", "media-pc/tests"]

[tool.ruff]
target-version = "py314"
line-length = 110
```

`LICENSE`: MIT, `Copyright (c) 2026 alws34 and the original HomeHub author`.

- [ ] **Step 2: Manifest and const**

`custom_components/homehub_media/manifest.json`:
```json
{
  "domain": "homehub_media",
  "name": "HomeHub Media",
  "after_dependencies": ["samsungtv", "plex", "jellyfin"],
  "codeowners": ["@alws34"],
  "config_flow": true,
  "dependencies": ["http", "frontend", "media_source", "conversation", "scene"],
  "documentation": "https://github.com/alws34/ha-homehub-media",
  "integration_type": "hub",
  "iot_class": "local_push",
  "issue_tracker": "https://github.com/alws34/ha-homehub-media/issues",
  "requirements": ["samsungtvws[async,encrypted]>=3.0.5", "wakeonlan>=3.0.0"],
  "single_config_entry": true,
  "version": "0.1.0"
}
```
`const.py`: exactly K1.

- [ ] **Step 3: Minimal `__init__.py` + test harness**

`custom_components/homehub_media/__init__.py` (Phase 2 Task 11 replaces the body):
```python
"""HomeHub Media integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return True
```

`tests/conftest.py`:
```python
import pytest

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield
```

`tests/test_init.py`:
```python
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homehub_media.const import DOMAIN


async def test_entry_loads(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "192.0.2.1", "token": "t", "mac": None})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
```

- [ ] **Step 4: Run tests**

Run: `uv venv .venv --python 3.14 && uv pip install -p .venv -r requirements_test.txt && .venv/bin/pytest -q tests/test_init.py`
Expected: `1 passed`

- [ ] **Step 5: CI workflow** `.github/workflows/validate.yml`:
```yaml
name: Validate
on: { push: {}, pull_request: {}, schedule: [{ cron: "0 3 * * 1" }], workflow_dispatch: {} }
permissions: { contents: read }
jobs:
  hacs:
    runs-on: ubuntu-latest
    steps:
      - uses: hacs/action@main
        with: { category: integration }
  hassfest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: home-assistant/actions/hassfest@master
  tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - run: uv venv .venv --python 3.14 && uv pip install -p .venv -r requirements_test.txt
      - run: .venv/bin/ruff check custom_components tests media-pc
      - run: .venv/bin/pytest -q
      - run: node --test tests/js/
```

- [ ] **Step 6: Commit** `chore: scaffold homehub_media integration, test harness, CI`

---

## Phase 1 — Independent modules (parallel; one worktree per task)

### Task 1: Media-PC hardening (`media-pc/`)

**Files:**
- Modify: `media-pc/ambient-player/ambient_player.py`, `media-pc/ambient-player/config.example.json`
- Modify: `media-pc/media-hub-coordinator/media_hub_coordinator.py`, `media-pc/media-hub-coordinator/coordinator.example.json`
- Modify: `media-pc/systemd-user/*.service`
- Delete: `media-pc/ambient-player/audio_only.ppm` (already untracked)
- Create: `media-pc/common_http.py` is NOT allowed (each daemon must stay a single deployable file); duplicate the ~30-line guard in both files.
- Test: `media-pc/tests/test_ambient_auth.py`, `media-pc/tests/test_coordinator_auth.py`

Behaviour (both daemons):
1. Config gains required `auth_token` (str, ≥ 32 chars, else config error) and optional `allowed_hosts` (list; default `[bind_host, f"{bind_host}:{port}"]`, plus `localhost`/`127.0.0.1` variants only if bind_host is loopback).
2. Request guard order, before any routing: IP allowlist → 403; `Host` header not in allowed_hosts → 421; `Authorization` != `Bearer <token>` (compare with `hmac.compare_digest`) → 401; for POST/PUT/PATCH with a body: `Content-Type` must start with `application/json` → 415. Bodies size caps unchanged.
3. `Handler.timeout = 10` (socket read timeout). Server subclass caps concurrency with `threading.BoundedSemaphore(16)`: in `process_request`, `acquire(blocking=False)` else close the socket; release in `shutdown_request`.
4. Error responses never include file names/paths: `PlayerError` messages are mapped to a fixed set of codes (`invalid_request`, `unknown_media`, `unavailable`, `busy`), the detail is logged.

Ambient player specifics:
5. Remove profiles, favourites, legacy scenes, `/v1/*` routes and their validation (AP:403-436, 1084-1101, 1150-1160, 1240-1342 routes listed in K8 as removed). State file keeps only `{"version": 3, "catalog": {...}}`; a v2 file is migrated by dropping the extra keys (no fatal error).
6. Catalog refresh skips (and logs) unreadable/unprobeable files instead of raising; an empty video catalog is allowed (status reports it).
7. Audio-only guard: replace the PPM file with mpv input `av://lavfi:color=c=black:s=1920x1080:r=1` (hardcoded constant; `audio_only_image` config key removed).
8. Port is configurable (drop the hard `== 8097` check AP:1368-1370; default 8097).

Coordinator specifics:
9. Remove `ambient_stop_url`, `ambient_stop_timeout_seconds` and `_stop_ambience` (CO:162-163, 497-515): HA now stops ambience before calling `/v1/open`.
10. `clients` becomes any non-empty dict `{name(slug): {unit, home_url}}` (same per-client validation as CO:146-160).
11. `stop` sends `Escape` (was Alt+Left, identical to back — CO:260-261).
12. Example config: no `127.0.0.1` in `allowed_clients`.

systemd (both units): add `ReadWritePaths=%t` to `ambient-player.service`, and to both: `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`, `RestrictNamespaces=true`, `LockPersonality=true`, `RestrictSUIDSGID=true`, `SystemCallFilter=@system-service`, `CapabilityBoundingSet=`.

- [ ] **Step 1: Write failing tests** `media-pc/tests/test_coordinator_auth.py` (ambient test mirrors it against `/v2/status`):

```python
import http.client, json, sys, threading, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "media-hub-coordinator"))
import media_hub_coordinator as co  # noqa: E402

TOKEN = "t" * 40


def start_server():
    cfg = co.Config.from_dict({
        "listen_host": "127.0.0.1", "listen_port": 0, "allowed_clients": ["127.0.0.1"], "auth_token": TOKEN,
        "clients": {"jellyfin": {"unit": "jellyfin-browser.service", "home_url": "http://127.0.0.1:8096/web/"}},
    })
    srv = co.make_server(cfg)  # returns a bound, not-yet-serving server
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class AuthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = start_server(); cls.port = cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def req(self, method, path, headers=None, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Host": f"127.0.0.1:{self.port}", "Authorization": f"Bearer {TOKEN}"}
        h.update(headers or {})
        c.request(method, path, body=body, headers={k: v for k, v in h.items() if v is not None})
        r = c.getresponse(); return r.status

    def test_health_ok_with_token(self):
        self.assertEqual(self.req("GET", "/health"), 200)

    def test_missing_token_401(self):
        self.assertEqual(self.req("GET", "/health", {"Authorization": None}), 401)

    def test_wrong_token_401(self):
        self.assertEqual(self.req("GET", "/health", {"Authorization": "Bearer nope"}), 401)

    def test_rebinding_host_421(self):
        self.assertEqual(self.req("GET", "/health", {"Host": "evil.example"}), 421)

    def test_text_plain_csrf_415(self):
        self.assertEqual(self.req("POST", "/v1/keyboard/text", {"Content-Type": "text/plain"},
                                  json.dumps({"text": "hi"})), 415)

    def test_short_token_rejected(self):
        with self.assertRaises(co.CoordinatorError):
            co.Config.from_dict({"listen_host": "127.0.0.1", "listen_port": 0, "allowed_clients": ["127.0.0.1"],
                                 "auth_token": "short", "clients": {"a": {"unit": "a.service",
                                 "home_url": "http://127.0.0.1/"}}})
```
(`Config.from_dict` and `make_server(cfg)` are new small seams: refactor the existing loader/`main` so tests can build a config from a dict and a server on port 0. Port 0 is allowed only via `from_dict`; the JSON loader still requires 1-65535.)

- [ ] **Step 2: Run, expect failures**: `.venv/bin/python -m pytest -q media-pc/tests` → FAIL (`AttributeError: from_dict` / 200 instead of 401).
- [ ] **Step 3: Implement behaviour 1-12 + systemd.** Keep every "done well" property from the audit (list-argv subprocess, ID_RE, HTTPS-only streams, bounded deltas, atomic 0600 writes).
- [ ] **Step 4: Run** `.venv/bin/python -m pytest -q media-pc/tests && python3 -m py_compile media-pc/*/*.py` → all pass.
- [ ] **Step 5: Update** `media-pc/README.md` (new file): install steps from the old `HANDOFF.md` §2-4, plus "generate a token: `python3 -c 'import secrets;print(secrets.token_urlsafe(32))'`, put it in both JSON configs and in HA → HomeHub Media → Configure → Media PC". Delete `HANDOFF.md`, `MANIFEST.md` at the repo root.
- [ ] **Step 6: Commit** `fix(media-pc): bearer auth, host/content-type checks, bounded server, drop HA-owned state`

### Task 2: Samsung TV client (`tv.py`)

**Files:** Create `custom_components/homehub_media/tv.py`; Test `tests/test_tv.py`

Interface:
```python
class PairingDenied(HomeAssistantError): ...
class CannotConnect(HomeAssistantError): ...

class SamsungTV:
    def __init__(self, hass, host: str, token: str | None, mac: str | None,
                 port: int = 8002, on_change: Callable[[], None] | None = None) -> None
    available: bool; keyboard_active: bool; pointer_active: bool; token: str | None
    async def pair(self) -> str                      # opens WS with token=None, returns new token (31 s timeout)
    async def device_info(self) -> dict              # REST GET http://host:8001/api/v2/ -> payload["device"]
    async def send_key(self, key: str, hold_secs: float = 0) -> None
    async def launch(self, app_id: str, meta_tag: str = "") -> None   # WS ed.apps.launch DEEP_LINK; REST POST fallback
    async def open_url(self, url: str) -> None       # browser launch with URL in metaTag (see step 5)
    async def app_status(self, app_id: str) -> dict | None   # REST GET applications/{id}; 404 -> None
    async def restart_app(self, app_id: str) -> None         # REST DELETE, wait 0.7 s, POST, poll running (PC:1831-2064)
    async def discover_apps(self, catalog: dict = APP_CATALOG) -> dict[str, str]  # key -> first id with status 200
    async def send_text(self, text: str) -> None     # textReceived broadcast once per IME session, SendInputString, SendInputEnd
    async def pointer_move(self, dx: float, dy: float) -> None   # ProcessMouseDevice Move, Time "0"
    async def pointer_click(self) -> None            # ProcessMouseDevice LeftClick
    async def wake(self) -> None                     # WoL magic packet to mac (executor), no-op without mac
    async def close(self) -> None
```

Behaviour:
- One `SamsungTVWSAsyncRemote(host, port=8002, token=..., name=CLIENT_NAME, key_press_delay=0, timeout=...)` (verify constructor/param names against the installed `samsungtvws` 3.0.5 source in `.venv/lib/python3.14/site-packages/samsungtvws/`), opened lazily on first command, `start_listening(self._on_event)` right after `open()`. On `ConnectionFailure`/`OSError`: mark `available=False`, drop the remote, raise `HomeAssistantError("TV is off or unreachable")`; the next command reconnects.
- Events: `ms.remote.imeStart` → keyboard_active True; `ms.remote.imeEnd`/`imeDone` → False; `ms.remote.touchEnable`/`touchDisable` → pointer_active; each change calls `on_change`.
- Key validation: `^KEY_[A-Z0-9_]{1,32}$` else `ServiceValidationError`. `hold_secs>0` uses `SendRemoteKey.hold` via `send_commands`.
- REST base `http://{host}:8001/api/v2/`, `aiohttp.ClientTimeout(total=5)`, HA shared session.
- Text: 1..TEXT_MAX chars, reject control characters (`unicodedata.category(c)[0] == "C"`).
- Pointer deltas clamped to ±500.
- `open_url`: try `launch("org.tizen.browser", meta_tag=url)` with `action_type="NATIVE_LAUNCH"`; if the TV raises/doesn't respond, `launch(self_browser_id, meta_tag=url)` with DEEP_LINK where `self_browser_id` = discovered `browser` id. (Which one works on Tizen 6.5 is confirmed in Task 17; keep both.)

- [ ] **Step 1: Failing tests** `tests/test_tv.py`:

```python
import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.homehub_media import tv as tvmod
from custom_components.homehub_media.const import APP_CATALOG


class FakeRemote:
    def __init__(self, *a, **kw):
        self.sent, self.kw = [], kw
    async def open(self): ...
    async def start_listening(self, cb): self.cb = cb
    async def send_command(self, cmd, key_press_delay=None): self.sent.append(cmd)
    async def send_commands(self, cmds, key_press_delay=None): self.sent.extend(cmds)
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
```
- [ ] **Step 2:** `.venv/bin/pytest -q tests/test_tv.py` → FAIL (module missing).
- [ ] **Step 3:** Implement `tv.py` per interface/behaviour (import `SamsungTVWSAsyncRemote` at module level so tests can monkeypatch it; command builders from `samsungtvws.remote`/`samsungtvws.command` as found in 3.0.5; build ProcessMouseDevice and the textReceived broadcast as raw `RemoteControlCommand`/`ChannelEmitCommand` dicts per the payloads below).

```json
{"method":"ms.remote.control","params":{"Cmd":"Move","Position":{"x":40,"y":-20,"Time":"0"},"TypeOfRemote":"ProcessMouseDevice"}}
{"method":"ms.remote.control","params":{"Cmd":"LeftClick","TypeOfRemote":"ProcessMouseDevice"}}
{"method":"ms.channel.emit","params":{"event":"custom.remote.textReceived","to":"broadcast"}}
{"method":"ms.remote.control","params":{"Cmd":"<base64 utf-8>","DataOfCmd":"base64","TypeOfRemote":"SendInputString"}}
{"method":"ms.remote.control","params":{"TypeOfRemote":"SendInputEnd"}}
{"method":"ms.channel.emit","params":{"event":"ed.apps.launch","to":"host","data":{"action_type":"DEEP_LINK","appId":"<id>","metaTag":""}}}
```
- [ ] **Step 4:** tests pass. **Step 5:** commit `feat(tv): Samsung Tizen client with keys, apps, text, pointer, browser`.

### Task 3: Catalog + store (`catalog.py`, `store.py`)

**Files:** Create `catalog.py`, `store.py`; Test `tests/test_catalog.py`, `tests/test_store.py`

`catalog.py` (pure; scan functions are blocking and called via executor):
```python
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
VIDEO_EXT = frozenset({".mp4", ".mkv", ".webm", ".mov"})
AUDIO_EXT = frozenset({".mp3", ".ogg", ".opus", ".flac", ".m4a", ".wav"})
PREVIEW_EXT = (".webp", ".jpg", ".jpeg", ".png")
def slugify(stem: str) -> str                       # AP:94-99 exactly
def title_from_stem(stem: str) -> str               # "_" -> " ", keeps case (AP:310)
def scan(root: Path, kind: str) -> list[dict]       # recursive, sorted by lowercased rel path; skip symlinks
                                                    # resolving outside root, unreadable files; ids slugified with
                                                    # _2/_3 suffix on collision; preview = sibling file same stem
                                                    # item keys: id, kind, title, source_type "file", live False,
                                                    # duration None, path (rel to root), preview_path (rel)|None
def radio_items(streams: dict[str, str]) -> list[dict]  # validate id ID_RE, url https + hostname + no userinfo
                                                        # + no fragment (AP:1371-1387) else ValueError; title per AP:324-350
def validate_composition(data: dict, partial: bool = False) -> dict  # strict types/ranges (AP:694-733),
                                                    # K2 defaults when not partial; unknown keys -> ValueError
```
`store.py`:
```python
class HomeHubStore:
    def __init__(self, hass): self._store = Store(hass, 1, f"{DOMAIN}.data")
    async def async_load(self) -> None                       # data = {"compositions": {}, "favorites": {"visual": [], "soundtrack": []}}
    def composition(self, mode_id: str) -> dict | None
    async def async_save_composition(self, mode_id: str, comp: dict) -> None   # validates, saves
    def favorites(self, kind: str) -> set[str]
    async def async_set_favorite(self, kind: str, media_id: str, favorite: bool) -> None
```

- [ ] **Step 1: Failing tests** `tests/test_catalog.py`:
```python
import os
from pathlib import Path
import pytest
from custom_components.homehub_media import catalog as c


def test_slugify():
    assert c.slugify("Cozy Fireplace 4K") == "cozy_fireplace_4k"
    assert c.slugify("!!!") == "media"
    assert len(c.slugify("a" * 200)) == 64


def test_scan_ids_collisions_previews_and_escape(tmp_path: Path):
    root = tmp_path / "videos"; (root / "sub").mkdir(parents=True)
    (root / "Rain.mp4").write_bytes(b"x"); (root / "sub" / "rain.mkv").write_bytes(b"x")
    (root / "Rain.webp").write_bytes(b"x"); (root / "notes.txt").write_text("x")
    outside = tmp_path / "secret.mp4"; outside.write_bytes(b"x")
    os.symlink(outside, root / "evil.mp4")
    items = c.scan(root, "visual")
    assert [i["id"] for i in items] == ["rain", "rain_2"]
    assert items[0]["preview_path"] == "Rain.webp" and items[0]["title"] == "Rain"
    assert all("evil" not in i["path"] for i in items)


@pytest.mark.parametrize("url", ["http://x/a", "https://u:p@x/a", "https://x/a#f", "file:///etc/passwd"])
def test_radio_rejects_bad_urls(url):
    with pytest.raises(ValueError):
        c.radio_items({"radio_x": url})


def test_composition_defaults_and_strictness():
    comp = c.validate_composition({"visual": "rain"})
    assert comp["visual_enabled"] is True and comp["original_volume"] == 20 and comp["soundtrack_volume"] == 100
    with pytest.raises(ValueError):
        c.validate_composition({"original_volume": 101})
    with pytest.raises(ValueError):
        c.validate_composition({"video_loop": "yes"})
    with pytest.raises(ValueError):
        c.validate_composition({"bogus": 1})
    assert c.validate_composition({"soundtrack_volume": 50}, partial=True) == {"soundtrack_volume": 50}
```
`tests/test_store.py`:
```python
from custom_components.homehub_media.store import HomeHubStore


async def test_store_roundtrip(hass, hass_storage):
    s = HomeHubStore(hass); await s.async_load()
    await s.async_save_composition("dinner", {"visual": "aquarium"})
    await s.async_set_favorite("visual", "aquarium", True)
    s2 = HomeHubStore(hass); await s2.async_load()
    assert s2.composition("dinner")["visual"] == "aquarium"
    assert s2.favorites("visual") == {"aquarium"}
```
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** run → PASS. **Step 5:** commit `feat(ambience): media catalog scanning and composition store`.

### Task 4: Library (`library.py`)

**Files:** Create `library.py`; Test `tests/test_library.py`

```python
class PlexServer:
    name = "plex"
    def __init__(self, hass, url: str, token: str, player: str | None)
    async def continue_watching(self) -> list[dict]   # GET {url}/hubs/continueWatching/items
    async def recently_added(self) -> list[dict]      # GET {url}/library/recentlyAdded?X-Plex-Container-Start=0&X-Plex-Container-Size=20
    async def sessions(self) -> list[dict]            # GET {url}/status/sessions -> NowPlaying
    async def fetch_art(self, key: str) -> tuple[bytes, str]   # GET {url}{thumb}?width=300&height=450 -> (body, content_type)
    async def play(self, item_id: str) -> bool        # True if a plex media_player entity matching `player` exists and
                                                      # media_player.play_media(media_content_type="movie",
                                                      # media_content_id=f"plex://{machine_id}/{item_id}?resume=1") succeeded
class JellyfinServer:
    name = "jellyfin"
    def __init__(self, hass, url: str, api_key: str, user_id: str, player: str | None)
    async def continue_watching(self)   # GET /UserItems/Resume?userId=..&limit=20&mediaTypes=Video
    async def recently_added(self)      # GET /Items/Latest?userId=..&limit=20
    async def sessions(self)            # GET /Sessions?activeWithinSeconds=120
    async def fetch_art(self, key: str) # GET /Items/{key}/Images/Primary?maxHeight=450
    async def play(self, item_id: str) -> bool   # newest session with SupportsMediaControl whose DeviceName/Client
                                                 # contains `player` (or any if None): POST
                                                 # /Sessions/{id}/Playing?playCommand=PlayNow&itemIds={item_id}&startPositionTicks={resume}
class Library:
    def __init__(self, hass, servers: list, on_change: Callable[[], None])
    items: dict[str, dict]            # {"plex": {"continue": [...], "recent": [...]}, ...}
    now_playing: list[dict]
    async def async_refresh(self) -> None          # all servers, errors logged per server, never raised
    async def async_refresh_sessions(self) -> None
    def async_start(self) -> Callable[[], None]    # track_time_interval: items 5 min, sessions 10 s; returns cancel
    def server(self, name: str)
```
Headers: Plex `X-Plex-Token`, `Accept: application/json`, `X-Plex-Client-Identifier: homehub-media`; Jellyfin `Authorization: MediaBrowser Token="<api_key>"`. Normalised items use `image` = `/api/homehub_media/art/{server}/{key}` (unsigned here; api.py signs). Plex art key = the `thumb` path base64url-encoded; Jellyfin key = item id. Item ids validated with `^[A-Za-z0-9-]{1,64}$` before use in URLs.

- [ ] **Step 1: Failing tests** (`aioclient_mock`):
```python
from custom_components.homehub_media.library import JellyfinServer, Library, PlexServer

PLEX = "http://plex.local:32400"
JF = "http://jf.local:8096"


async def test_plex_continue_normalised(hass, aioclient_mock):
    aioclient_mock.get(f"{PLEX}/hubs/continueWatching/items", json={"MediaContainer": {"Metadata": [
        {"ratingKey": "123", "type": "episode", "title": "Pilot", "grandparentTitle": "Severance",
         "viewOffset": 600000, "duration": 3000000, "thumb": "/library/metadata/123/thumb/1"}]}})
    items = await PlexServer(hass, PLEX, "tok", None).continue_watching()
    assert items[0]["id"] == "123" and items[0]["title"] == "Severance"
    assert items[0]["subtitle"] == "Pilot" and abs(items[0]["progress"] - 0.2) < 1e-6
    assert items[0]["image"].startswith("/api/homehub_media/art/plex/")
    assert aioclient_mock.mock_calls[0][3]["X-Plex-Token"] == "tok"


async def test_jellyfin_sessions_now_playing(hass, aioclient_mock):
    aioclient_mock.get(f"{JF}/Sessions", json=[{"Id": "s1", "DeviceName": "Samsung TV", "SupportsMediaControl": True,
        "NowPlayingItem": {"Id": "abc", "Name": "Dune", "RunTimeTicks": 100_000_000_000, "Type": "Movie"},
        "PlayState": {"PositionTicks": 50_000_000_000, "IsPaused": True}}])
    np = await JellyfinServer(hass, JF, "key", "u1", None).sessions()
    assert np == [{"server": "jellyfin", "player": "Samsung TV", "title": "Dune", "subtitle": None,
                   "state": "paused", "position": 5000.0, "duration": 10000.0,
                   "image": "/api/homehub_media/art/jellyfin/abc"}]


async def test_library_refresh_survives_server_error(hass, aioclient_mock):
    aioclient_mock.get(f"{JF}/UserItems/Resume", status=500)
    aioclient_mock.get(f"{JF}/Items/Latest", status=500)
    lib = Library(hass, [JellyfinServer(hass, JF, "key", "u1", None)], on_change=lambda: None)
    await lib.async_refresh()
    assert lib.items["jellyfin"] == {"continue": [], "recent": []}
```
- [ ] Steps 2-5 as usual. Commit `feat(library): Plex and Jellyfin continue watching, recent, sessions`.

### Task 5: Media-PC and keyboard-bridge clients (`pc.py`, `keyboard.py`)

**Files:** Create `pc.py`, `keyboard.py`; Test `tests/test_pc.py`

```python
class PCError(HomeAssistantError): ...
class AmbientPlayerClient:
    def __init__(self, hass, host: str, port: int, token: str)
    async def status(self) -> dict ; async def catalog(self) -> dict ; async def refresh(self) -> dict
    async def play(self, composition: dict) -> dict ; async def patch(self, changes: dict) -> dict
    async def stop(self) -> dict
class CoordinatorClient:
    def __init__(self, hass, host: str, port: int, token: str)
    async def status(self) -> dict ; async def open(self, client: str) -> None ; async def close(self) -> None
    async def remote(self, command: str) -> None ; async def text(self, text: str, submit: bool = False) -> None
    async def pointer_move(self, dx: float, dy: float) -> None ; async def click(self, button: str = "left") -> None
    async def scroll(self, dy: float) -> None
class KeyboardBridge:
    def __init__(self, hass, host: str, key: str)
    async def type(self, text: str) -> None   # POST http://{host}/type, text/plain body, X-HomeHub-Key header (PC:462-470)
    async def key(self, name: str) -> None    # POST http://{host}/key body backspace|enter|clear
```
K8 headers on every PC request. Non-2xx → `PCError` with message by status: 401/403 "Media PC rejected the token", 421 "Media PC host name mismatch", 409 server `error` code, else "Media PC request failed". Connection error → `PCError("Media PC is unreachable")`. `keyboard.py` with the same error style (`HomeAssistantError("Keyboard bridge ...")`).

- [ ] **Step 1: Failing tests**:
```python
import pytest
from custom_components.homehub_media.pc import AmbientPlayerClient, CoordinatorClient, PCError


async def test_bearer_and_json(hass, aioclient_mock):
    aioclient_mock.post("http://192.0.2.5:8097/v2/play", json={"ok": True})
    await AmbientPlayerClient(hass, "192.0.2.5", 8097, "secret").play({"visual": "rain"})
    _, _, body, headers = aioclient_mock.mock_calls[0]
    assert headers["Authorization"] == "Bearer secret" and body == {"visual": "rain"}


async def test_401_maps_to_token_error(hass, aioclient_mock):
    aioclient_mock.post("http://192.0.2.5:8098/v1/open/jellyfin", status=401)
    with pytest.raises(PCError, match="token"):
        await CoordinatorClient(hass, "192.0.2.5", 8098, "bad").open("jellyfin")
```
- [ ] Steps 2-5. Commit `feat(pc): media PC and keyboard bridge clients`.

### Task 6: Assistant providers + tools (`assistant.py`)

**Load first:** the `claude-api` skill (Python, `python/claude-api/README.md` + `tool-use.md`), and read `homeassistant/components/{anthropic,openai_conversation,ollama}/` at tag `2026.9.4` as reference for `ChatLog` usage.

**Files:** Create `assistant.py`; Test `tests/test_assistant.py`

```python
API_ID = f"{DOMAIN}_tools"
class HomeHubLLMAPI(llm.API):   # id=API_ID, name="HomeHub Media"; registered in __init__ via llm.async_register_api
    async def async_get_api_instance(self, llm_context) -> llm.APIInstance   # tools below, short api_prompt
TOOLS (llm.Tool subclasses, each calls the Hub from hass.config_entries runtime_data; voluptuous parameters):
  LaunchApp(app: In(APP_CATALOG)), Remote(command: In(COMMANDS)), SetActivity(activity: In(ACTIVITIES)),
  AmbienceStart(mode: str), AmbienceStop(), AmbienceAuto(), SleepTimer(minutes: Range(0,600)),
  SetQuietMode(on: bool), PlayLibraryItem(title: str) -> searches hub.library items by casefolded title,
  NowPlaying() -> returns hub.library.now_playing
async def list_models(hass, provider: str, url: str | None, api_key: str | None) -> list[str]
     # ollama: GET {url}/api/tags -> [m["name"]]; openai: AsyncOpenAI(api_key).models.list();
     # claude: AsyncAnthropic(api_key).models.list() -> ids. Raises HomeAssistantError("invalid_auth"|"cannot_connect")
async def ensure_sdk(hass, provider: str) -> None
     # homeassistant.requirements.async_process_requirements(hass, DOMAIN, [REQ[provider]])
     # REQ = {"openai": "openai>=2.45.0", "ollama": "openai>=2.45.0", "claude": "anthropic>=0.108.0"}
async def complete(hass, cfg: dict, system: str, messages: list[dict], tools: list[dict]) -> Reply
     # Reply = dataclass(text: str, tool_calls: list[tuple[id, name, args]])
     # openai/ollama: AsyncOpenAI(api_key=cfg.get("api_key") or "ollama", base_url=None or f"{url}/v1")
     #                .chat.completions.create(model, messages, tools=[{"type":"function","function":{...}}])
     # claude: AsyncAnthropic(api_key).messages.create(model, max_tokens=4096, system=system, messages=...,
     #          tools=[{"name","description","input_schema"}], tool_choice={"type": "auto"},
     #          output_config={"effort": "low"},
     #          extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"}, extra_body={"fallbacks": "default"})
     #          stop_reason "refusal" -> Reply(text="Sorry, I can't help with that.", tool_calls=[])
```
`messages` uses a neutral format `[{"role": "user"|"assistant"|"tool", "content": str, "tool_calls"?: [...], "tool_call_id"?: str}]`; each adapter converts. Tool JSON schemas come from `voluptuous_openapi.convert(tool.parameters)` (as core does). Imports of `openai`/`anthropic` are inside functions (installed on demand).

- [ ] **Step 1: Failing tests** with stubbed SDK clients (monkeypatch the factory functions `_openai_client(cfg)` and `_anthropic_client(cfg)` that `complete` uses):
```python
from types import SimpleNamespace
import pytest
from custom_components.homehub_media import assistant as a


async def test_claude_tool_call_parsed(hass, monkeypatch):
    msg = SimpleNamespace(stop_reason="tool_use", content=[
        SimpleNamespace(type="text", text="Starting"),
        SimpleNamespace(type="tool_use", id="t1", name="AmbienceStart", input={"mode": "dinner"})])
    class C:  # fake AsyncAnthropic
        class messages:
            @staticmethod
            async def create(**kw):
                assert kw["model"] == "claude-opus-5-5" and kw["tool_choice"] == {"type": "auto"}
                return msg
    monkeypatch.setattr(a, "_anthropic_client", lambda cfg: C)
    r = await a.complete(hass, {"provider": "claude", "api_key": "k", "model": "claude-opus-5-5"}, "sys",
                         [{"role": "user", "content": "dinner mode"}], [{"name": "AmbienceStart",
                         "description": "d", "parameters": {"type": "object", "properties": {}}}])
    assert r.text == "Starting" and r.tool_calls == [("t1", "AmbienceStart", {"mode": "dinner"})]


async def test_claude_refusal(hass, monkeypatch):
    class C:
        class messages:
            @staticmethod
            async def create(**kw):
                return SimpleNamespace(stop_reason="refusal", content=[])
    monkeypatch.setattr(a, "_anthropic_client", lambda cfg: C)
    r = await a.complete(hass, {"provider": "claude", "api_key": "k", "model": "m"}, "s",
                         [{"role": "user", "content": "x"}], [])
    assert r.tool_calls == [] and "can't" in r.text


async def test_openai_compatible_for_ollama(hass, monkeypatch):
    seen = {}
    fn = SimpleNamespace(name="Remote", arguments='{"command": "home"}')
    choice = SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[SimpleNamespace(id="c1", function=fn)]))
    class O:
        class chat:
            class completions:
                @staticmethod
                async def create(**kw):
                    seen.update(kw); return SimpleNamespace(choices=[choice])
    monkeypatch.setattr(a, "_openai_client", lambda cfg: (seen.setdefault("cfg", cfg), O)[1])
    r = await a.complete(hass, {"provider": "ollama", "url": "http://o:11434", "model": "llama3.2"}, "s",
                         [{"role": "user", "content": "go home"}], [])
    assert r.tool_calls == [("c1", "Remote", {"command": "home"})] and seen["model"] == "llama3.2"
```
- [ ] Steps 2-5. Commit `feat(assistant): LLM tools API and Ollama/OpenAI/Claude adapters`.

### Task 7: Lovelace card (`www/homehub-media-card.js`)

**Load first:** the `ui-ux-pro-max` skill for the visual design pass.

**Files:** Create `custom_components/homehub_media/www/homehub-media-card.js`, `dev/card-demo.html` (loads the card with a fake `hass` emitting a K4 snapshot, for local visual testing), `tests/js/card.test.mjs`

Requirements:
- `customElements.define("homehub-media-card", …)` guarded by `if (!customElements.get(...))`; push `{type: "homehub-media-card", name: "HomeHub Media", description: "Remote, ambience and library", preview: true}` to `window.customCards` once.
- Config: `{drawer?: bool=false, tabs?: ["remote","ambience","library"], default_tab?: "remote", target?: "tv"|"box"|"pc"}`; `getStubConfig() → {}`; `getGridOptions() → {columns: 12, min_columns: 6, rows: "auto"}`; `getCardSize() → 8`.
- Data: on first `hass` set, `this._unsub = await hass.connection.subscribeMessage(e => this._onSnapshot(e), {type: "homehub_media/subscribe"})`; `disconnectedCallback` unsubscribes. Catalog/library fetched with `hass.callWS` when their tab opens and after `refresh_catalog`. All actions via `hass.callService("homehub_media", <K6 service>, data)`; errors shown in a toast via `this.dispatchEvent(new CustomEvent("hass-notification", {detail: {message}, bubbles: true, composed: true}))`.
- Rendering: one shadow root, template literal strings with **every** dynamic value passed through `esc()` (`&<>"'` → entities) and URLs through `new URL(x, location.origin)` same-origin check; re-render a section only when its slice of the snapshot changed (compare `JSON.stringify` of the slice).
- Tabs (hidden when not applicable: `library` only if `servers.length`):
  - **Remote:** target pills (TV always; Box if `targets.box`; PC if `targets.pc`); swipe left/right on the pad cycles targets (threshold 55 px, as SWP); power/home/back row; circular D-pad (tap; long-press repeats up/down every 150 ms); volume −/mute/+; play/pause; app grid (only `installed` apps, brand-coloured tiles with the app name, active app glow from `tv.app`; long-press = restart app); keyboard field (Enter → `send_text`, Backspace on empty → `keyboard_key backspace`, × → `keyboard_key clear`; hint text "Focus a search box on the TV first" when target tv and `!tv.keyboard_active`); touchpad toggle (pointer moves batched every 36 ms, one request in flight, ×1.15 gain, two-finger scroll for PC, tap = click; on TV shows "Pointer works in the browser" when `!tv.pointer_active`) — port the batching from `home-assistant/www/homehub-media-ui/homehub-media-hub-control-pad.js:150-260`; box channel keypad with 1.5 s debounce → `channel`; PC client buttons from `pc.clients` → `pc_open`, Exit → `pc_close`.
  - **Ambience:** **Auto** tile (subtitle = daypart mode name) → `ambience_auto`; mode tiles (icon, name, "ON" when `ambience.mode === id`; tap toggles start/stop; ⚙ opens editor for non-lights-only modes); editor: visual & soundtrack carousels (preview image or icon fallback, title, ★ favourite → `set_favorite`, swipe/arrows to browse, tap selects), toggles (visual on, loop, random; audio on, loop, random), mix slider Ambience↔Soundtrack (0-100 → original_volume = 100−v, soundtrack_volume = v), Save → `save_mode`, Start → `ambience_start`; live edits while the edited mode is active → `ambience_update` (debounced 200 ms); sleep chips 15/30/60/90 + cancel → `ambience_sleep_timer`, remaining time from `sleep_until`; refresh ⟳ → `refresh_catalog`.
  - **Library:** per server rows "Continue watching" (progress bar) and "Recently added", horizontal scroll, poster images (`loading="lazy"`), tap → `play_library_item`.
- **Now Playing strip** above tabs when `now_playing.length`: artwork, title/subtitle, progress, −30 s (`remote rewind`), play/pause (`remote play`/`pause` by `state`), +30 s (`remote fast_forward`).
- **Drawer mode:** fixed to the right edge, vertically centred, 28 px handle with chevron, panel `min(380px, 100vw - 46px)`, z-index 5, slide transition 200 ms, `prefers-reduced-motion` respected; closed state persisted in `localStorage` (try/catch).
- Styling: HA theme variables only (`--card-background-color`, `--primary-color`, `--primary-text-color`, `--secondary-text-color`, `--divider-color`, `--ha-card-border-radius`, `--rgb-primary-color`), 44 px minimum touch targets, focus-visible rings, `aria-label` on icon buttons, keyboard operable D-pad (arrow keys/Enter when focused).
- Export pure helpers for tests via `window.__homehubTest = {esc, mixToVolumes, nextTarget}` only when `window.__HOMEHUB_TEST__` is set.

`tests/js/card.test.mjs` (node:test with a minimal DOM shim is unnecessary: test the helpers by evaluating the file in a `vm` context with stubbed `customElements`, `HTMLElement`, `window`):
```js
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const src = readFileSync(new URL("../../custom_components/homehub_media/www/homehub-media-card.js", import.meta.url), "utf8");
const ctx = { window: { __HOMEHUB_TEST__: true, customCards: [] }, customElements: { get: () => undefined, define() {} },
              HTMLElement: class {}, CustomEvent: class {}, localStorage: { getItem() {}, setItem() {} }, location: { origin: "http://ha" } };
ctx.window.window = ctx.window;
vm.createContext(ctx); vm.runInContext(src, ctx);
const { esc, mixToVolumes, nextTarget } = ctx.window.__homehubTest;

test("esc neutralises markup", () => {
  assert.equal(esc(`<img src=x onerror="a">'`), "&lt;img src=x onerror=&quot;a&quot;&gt;&#39;");
});
test("mix slider maps to volumes", () => {
  assert.deepEqual({ ...mixToVolumes(30) }, { original_volume: 70, soundtrack_volume: 30 });
});
test("target cycling wraps and skips unavailable", () => {
  assert.equal(nextTarget("tv", { box: false, pc: true }, 1), "pc");
  assert.equal(nextTarget("pc", { box: false, pc: true }, 1), "tv");
});
```
- [ ] Step 1 test → FAIL; Step 2 implement; Step 3 `node --test tests/js/` → PASS; Step 4 open `dev/card-demo.html` in a browser (via `python3 -m http.server` from repo root) and screenshot all tabs in light and dark; Step 5 commit `feat(card): HomeHub Media card with remote, ambience, library, drawer`.

### Task 8: TV ambience page (`www/ambience.html`)

**Files:** Create `custom_components/homehub_media/www/ambience.html`; Test `tests/js/page.test.mjs`

- Single self-contained HTML file (inline CSS/JS, no external requests), black background, `<video playsinline>` full-viewport `object-fit: cover`, `<audio>`, no visible UI except a small centred "Press OK for sound" hint shown only if unmuted autoplay was rejected (then play muted; the first `keydown`/`click` unmutes and hides the hint).
- Implements the K7 protocol: reads `s` from `location.search`, refuses any `s`/`next`/media URL that is not same-origin (`new URL(u, location.href).origin === location.origin`); polls with `fetch(next, {cache: "no-store"})` every 1000 ms; on fetch error keeps playing and retries with backoff up to 10 s; `random_start` → after `loadedmetadata`, `currentTime = Math.random() * Math.max(0, duration - 0.5)` once per URL; `loop` → element `loop` attribute; volumes clamped 0..1; `fade_secs` → requestAnimationFrame linear ramp of both volumes to 0; `stop` → pause both, clear `src`.
- Export `window.__ambienceTest = {sameOrigin, clampVolume}` when `window.__HOMEHUB_TEST__`.
- Test (`node --test`, vm context like Task 7) asserts `sameOrigin("http://evil/x")` is false, `sameOrigin("/api/x")` true, `clampVolume(1.7) === 1`, `clampVolume(-1) === 0`.
- Commit `feat(ambience): TV browser player page`.

---

## Phase 2 — Integration (parallel where marked; depends on Phase 1 merged)

### Task 9 (parallel): Activity manager (`activity.py`)

**Files:** Create `activity.py`; Test `tests/test_activity.py`

```python
class ActivityManager:
    def __init__(self, hass, hub)   # hub gives tv, options, ambience, async_notify
    current: str  # ACTIVITIES
    phase: str    # PHASES
    quiet: bool
    async def set(self, activity: str, force: bool = False) -> None
    async def set_quiet(self, on: bool) -> None
    async def apply_volume(self) -> None
```
Port `homehub_media_set_activity` (PC:1353-1688) and the volume policy (PC:794-819, 4159-4172):
- Serialisation: a running `set()` task is cancelled by a newer call (`mode: restart`): keep `self._task`, cancel it, await cancellation, start new.
- Block entity (`options.volume.block_entity`) state `on` → any activity except `off` sets phase `blocked` and raises `ServiceValidationError("Blocked by <entity>")`.
- Before switching: `await hub.ambience.stop()` if the running mode uses media (`not lights_only`); for `off` also stop lights-only modes.
- `off`: box `remote.turn_off` (if configured), TV off (`media_player.turn_off` on `options.tv.entity` else `tv.send_key("KEY_POWER")` if on); phase ready.
- `tv`: TV on (entity `turn_on` if it supports TURN_ON else `tv.wake()`), wait until `tv.available` or entity state `on` (≤ 10 s), `KEY_HOME`, `apply_volume()`; phase ready / needs_reset on timeout.
- `box`: TV + box on in parallel, then switch input with attempts 2 (warm) / 3 (cold) / 4 (force); `INPUT.method == "source"` → `media_player.select_source(source=value)` and verify the entity's `source` within 2 s; `"key"` → `tv.send_key(value)` (unverifiable, one attempt).
- `pc`: TV on, switch to `options.pc.input` (as above); phase ready.
- `apply_volume`: `media_player.volume_set(volume_level=(quiet or normal)/100)` on the TV entity, if configured.
- Every state change → `hub.async_notify()`.
- Wait/verify durations are module constants (`VERIFY_SECS = 2.0`, `POWER_WAIT_SECS = 10.0`) so tests patch them to `0.01`.

Tests (patch `activity.VERIFY_SECS`/`POWER_WAIT_SECS` to 0.01 via `monkeypatch`; use a stub hub object with `tv` = `AsyncMock`, `ambience` = `AsyncMock`, options dict; register service mocks with `async_mock_service`):
```python
from unittest.mock import AsyncMock, MagicMock
import pytest
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import async_mock_service
from custom_components.homehub_media.activity import ActivityManager


def make_hub(options):
    hub = MagicMock(); hub.options = options; hub.tv = AsyncMock(); hub.tv.available = True
    hub.ambience = AsyncMock(); hub.ambience.uses_media = True
    return hub


async def test_blocked_by_entity(hass):
    hass.states.async_set("input_boolean.night", "on")
    hub = make_hub({"volume": {"normal": 19, "quiet": 10, "block_entity": "input_boolean.night"}, "tv": {}})
    am = ActivityManager(hass, hub)
    with pytest.raises(ServiceValidationError):
        await am.set("tv")
    assert am.phase == "blocked"
    await am.set("off")  # off always allowed
    assert am.current == "off"


async def test_quiet_volume_applied(hass):
    calls = async_mock_service(hass, "media_player", "volume_set")
    hass.states.async_set("media_player.tv", "on")
    hub = make_hub({"volume": {"normal": 19, "quiet": 10, "block_entity": None}, "tv": {"entity": "media_player.tv"}})
    am = ActivityManager(hass, hub)
    await am.set_quiet(True)
    assert calls[-1].data["volume_level"] == 0.10


async def test_box_source_retry(hass):
    sel = async_mock_service(hass, "media_player", "select_source")
    async_mock_service(hass, "media_player", "turn_on"); async_mock_service(hass, "remote", "turn_on")
    hass.states.async_set("media_player.tv", "on", {"source": "TV"})
    hub = make_hub({"volume": {"normal": 19, "quiet": 10, "block_entity": None}, "tv": {"entity": "media_player.tv"},
                    "box": {"remote": "remote.box", "media_player": None, "input": {"method": "source", "value": "HDMI1"}}})
    am = ActivityManager(hass, hub)
    await am.set("box")
    assert len(sel) >= 2 and am.phase == "needs_reset"   # source never confirmed
```
Commit `feat(activity): activity state machine with input switching and quiet volume`.

### Task 10 (parallel): Ambience manager (`ambience.py`)

**Files:** Create `ambience.py`; Test `tests/test_ambience.py`

```python
FLICKER = [...]  # 17 (brightness_pct, transition_s) steps ported from PC:2847-2882
class AmbienceManager:
    def __init__(self, hass, hub, store: HomeHubStore)
    mode: str                      # "off" or mode id
    error: str | None
    sleep_until: datetime | None
    auto: bool
    uses_media: bool               # current mode not lights_only
    page_version: int; page_last_seen: float
    async def async_load(self) -> None                 # store load + initial catalog refresh (executor)
    def modes(self) -> list[dict]                      # options.modes or DEFAULT_MODES
    def composition(self, mode_id) -> dict             # store or default (PREFERRED_VISUAL if in catalog else first visual)
    async def refresh_catalog(self) -> None            # tv: scan media dir + radio; pc: client.catalog(); sets favourite flags
    def catalog(self) -> dict                          # {"visuals": [...], "soundtracks": [...]} K2 without server-only keys
    async def start(self, mode_id: str, composition: dict | None = None) -> None
    async def stop(self, restore_lights: bool = True) -> None
    async def update(self, changes: dict) -> None      # validate partial; merge into live composition; apply
    async def save_mode(self, mode_id: str, comp: dict) -> None
    async def set_favorite(self, kind, media_id, favorite) -> None
    async def auto_start(self) -> None                 # start(daypart_mode()) ; ServiceValidationError if none
    def daypart_mode(self, now: datetime | None = None) -> str | None
    async def set_sleep_timer(self, minutes: int) -> None
    def page_state(self) -> dict                        # K7 payload (URLs signed via hub.sign_path); records heartbeat
    def snapshot(self) -> dict                          # K4 "ambience"
```
Internals the tests rely on: the hub is stored as `self._hub`, the current catalog as `self._catalog` (K2 shape incl. server-only keys).

Behaviour:
- **Previews:** TV backend → sidecar `preview_path` served as signed `/media/local/{MEDIA_ROOT}/{dir}/{preview_path}`; PC backend → if `hass.config.path("www/Ambience/<id>.webp")` (or `.png`) exists, `/local/Ambience/<id>.webp` (original convention, AMC:155-163). File checks in the executor during `refresh_catalog`.
- **start:** stop any current mode (keep lights snapshot if switching between modes); snapshot = `scene.create` (`scene_id: homehub_media_snapshot`, `snapshot_entities`: scene's entities (from `hass.states.get(scene).attributes["entity_id"]`) ∪ flicker lights, each expanded if it has an `entity_id` attribute (light groups)); `scene.turn_on` the mode's scene; start flicker task (`hass.async_create_background_task`) looping FLICKER over the flicker lights with `light.turn_on`; if `not lights_only`: TV backend → `hub.activity.set("tv")` if TV off, then `hub.tv.open_url(page_url)` and 4 s later `hub.tv.send_key("KEY_ENTER")` (autoplay gesture); PC backend → `hub.activity.set("pc")`, `pc_ambient.play(comp)`. Errors → `self.error`, lights restored, mode "off", re-raise `HomeAssistantError`.
- **stop:** cancel sleep timer + flicker; TV backend: bump page version with `stop: true` then `tv.send_key("KEY_HOME")`; PC backend: `pc_ambient.stop()`; restore `scene.homehub_media_snapshot` (`scene.turn_on`) and remove it (`scene.delete` is only for UI scenes: instead keep it — `scene.create` overwrites on next start); mode "off".
- **update:** merge, bump version (TV) or `pc_ambient.patch(...)` mapping Composition → K8 PATCH (visual → `video`, soundtrack → `soundtrack`; `original_volume` → `video.volume`; `visual_enabled false` → `video.enabled false`).
- **health:** while active, every 30 s: PC → `status()`; if `state == "idle"` → `stop()`; TV → if `time.monotonic() - page_last_seen > 45` (after a 60 s grace from start) → `stop()` (user left the browser).
- **sleep timer:** `minutes == 0` cancels; else `async_call_later(minutes*60 - 60)` → fade: TV sets `fade_secs: 60`; PC steps volumes down in 6 PATCH calls over 60 s; then `stop()`. `sleep_until` updated + notify.
- **dayparts:** pick the latest daypart whose `start` ≤ now (wrapping past midnight to "night"); `mode` None → None. Idle auto-start: when `auto` is on, a 1-minute interval checks: TV entity `on`, activity `tv`, ambience off, `library.now_playing` empty, no hub command for `idle_minutes` (hub records `last_command` monotonic), and if `options.tv.app_entity` set its state ∈ {"", "unknown", "home", "Home", "TV", "off"} → `auto_start()`.
- Media URLs for the page: TV backend file items → `f"/media/local/{MEDIA_ROOT}/{dir}/{item['path']}"` signed through `sign`; radio → the https URL as-is (the page's same-origin rule exempts `soundtrack` URLs starting with `https://` only when `source_type == "stream"`; add `"stream": true` to that layer in the payload).
- Media dir: `hass.config.media_dirs.get("local")` (HAOS: `/media`); create `homehub/ambience/{videos,soundtracks}` on load if missing (executor).

Tests (stub hub with `AsyncMock` tv / activity / pc client; `async_mock_service` for scene/light):
```python
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from pytest_homeassistant_custom_component.common import async_mock_service
from custom_components.homehub_media.ambience import AmbienceManager
from custom_components.homehub_media.store import HomeHubStore


def hub_with(options):
    hub = MagicMock(); hub.options = options; hub.tv = AsyncMock(); hub.activity = AsyncMock()
    hub.pc_ambient = None; hub.library = None; hub.sign_path = lambda p: p; return hub


async def test_lights_only_mode_snapshots_and_restores(hass):
    create = async_mock_service(hass, "scene", "create"); on = async_mock_service(hass, "scene", "turn_on")
    hass.states.async_set("scene.movie", "scening", {"entity_id": ["light.a", "light.grp"]})
    hass.states.async_set("light.grp", "on", {"entity_id": ["light.b", "light.c"]})
    opts = {"ambience": {"backend": "tv", "radio": {}}, "modes": [{"id": "movie_time", "name": "Movie",
            "icon": "mdi:movie", "scene": "scene.movie", "flicker": [], "lights_only": True}]}
    store = HomeHubStore(hass); await store.async_load()
    am = AmbienceManager(hass, hub_with(opts), store)
    await am.start("movie_time")
    assert set(create[0].data["snapshot_entities"]) == {"light.a", "light.b", "light.c"}
    assert on[0].data["entity_id"] == "scene.movie" and am.mode == "movie_time"
    am._hub.tv.open_url.assert_not_called()
    await am.stop()
    assert on[-1].data["entity_id"] == "scene.homehub_media_snapshot" and am.mode == "off"


async def test_no_scene_means_no_lighting(hass):
    create = async_mock_service(hass, "scene", "create")
    opts = {"ambience": {"backend": "tv", "radio": {}}, "tv": {"base_url": "http://192.0.2.9:8123"},
            "modes": [{"id": "chill", "name": "Chill", "icon": "mdi:sofa", "scene": None, "flicker": [],
                       "lights_only": False}]}
    store = HomeHubStore(hass); await store.async_load()
    am = AmbienceManager(hass, hub_with(opts), store)
    am._catalog = {"visuals": [{"id": "rain", "kind": "visual", "path": "rain.mp4", "title": "Rain",
                   "source_type": "file", "live": False, "duration": None, "favorite": False, "preview": None}],
                   "soundtracks": []}
    await am.start("chill")
    assert create == [] and am._hub.tv.open_url.await_args.args[0].startswith("http://192.0.2.9:8123/homehub_media_static/ambience.html?s=")


def test_daypart_selection(hass):
    opts = {"dayparts": {"morning": {"start": "06:00", "mode": "reading"}, "afternoon": {"start": "12:00", "mode": None},
                         "evening": {"start": "18:00", "mode": "fireplace"}, "night": {"start": "22:00", "mode": "chill"},
                         "idle_minutes": 10}}
    am = AmbienceManager(hass, hub_with(opts), MagicMock())
    assert am.daypart_mode(datetime(2026, 1, 1, 7, 0)) == "reading"
    assert am.daypart_mode(datetime(2026, 1, 1, 13, 0)) is None
    assert am.daypart_mode(datetime(2026, 1, 1, 2, 0)) == "chill"      # wraps to night
    assert am.daypart_mode(datetime(2026, 1, 1, 19, 30)) == "fireplace"
```
Commit `feat(ambience): mode orchestration, lighting, flicker, sleep timer, dayparts, health`.

### Task 11: Hub, API, setup (`hub.py`, `api.py`, `__init__.py`, `services.yaml`)

Depends on Tasks 2-10 merged.

**Files:** Create `hub.py`, `api.py`, `services.yaml`; Modify `__init__.py`; Test `tests/test_api.py`

- `hub.py`: implements K3/K4. Builds clients from `entry.data` + `entry.options` (missing sections → `None`). `apps` = `options.apps.ids` if present else `await tv.discover_apps()` on start (store result back via `hass.config_entries.async_update_entry(options=...)` only if the user never set it). `remote()`: volume commands → TV; target tv → `TV_KEYS` (`play_pause` toggles between KEY_PLAY/KEY_PAUSE with `# ponytail: tracks last sent, not real player state`); box → `remote.send_command` with `BOX_KEYS`; pc → `pc_coord.remote(PC_COMMANDS[cmd])`; unsupported → `ServiceValidationError`. `launch_app()`: unknown key but valid raw id → launch raw; app not installed and catalog `server` configured → `tv.open_url(server url)`; activity set to `tv` first (as PC:1694-1822). `send_text(target=None)`: pc activity → coordinator; tv → TV IME if `keyboard_active` or no keyboard bridge, else bridge. `pointer` tv → move/click (scroll/right_click → `ServiceValidationError` on TV), pc → coordinator. `channel()`: `media_player.play_media(media_content_type="channel", media_content_id=number)` on `options.box.media_player`. `play_library_item`: `launch_app(server)` → wait up to 15 s polling `server.play(item_id)` every 3 s until True. Records `last_command = time.monotonic()` on every user command.
- `api.py`: `async_register(hass)` registers K5 websocket commands, K6 services (handlers resolve the loaded entry's Hub; none → `ServiceValidationError("HomeHub Media is not set up")`), and views: `PageStateView` (K7, `url="/api/homehub_media/page/state"`), `ArtView` (`/api/homehub_media/art/{server}/{key}`, validates server ∈ configured and key with `^[A-Za-z0-9_=-]{1,512}$`, streams `server.fetch_art`, `Cache-Control: private, max-age=3600`).
- `__init__.py`: `async_setup` → `api.async_register(hass)`, static path `StaticPathConfig(STATIC_URL, www_dir, cache_headers=False)` then `frontend.add_extra_js_url(hass, f"{STATIC_URL}/{CARD_FILE}?v={version}")`, `llm.async_register_api(hass, HomeHubLLMAPI(hass))`. `async_setup_entry` → `Hub(...)`, `await hub.async_start()` (TV unreachable is NOT a setup failure), `entry.runtime_data = hub`, forward platforms `select, switch, sensor` (+ `conversation` when `options.assistant`), `entry.async_on_unload(entry.add_update_listener(reload))`. Unload stops the hub.
- `services.yaml`: one block per K6 service with selectors (text/number/select/boolean).

Tests (`hass_ws_client`, `hass_client`, real Hub with patched `SamsungTV`):
```python
async def test_subscribe_sends_snapshot(hass, hass_ws_client, setup_entry):
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "homehub_media/subscribe"})
    assert (await ws.receive_json())["success"]
    ev = await ws.receive_json()
    assert ev["event"]["activity"]["current"] in ("off", "tv", "box", "pc") and "ambience" in ev["event"]


async def test_send_text_validation(hass, setup_entry):
    import pytest
    from homeassistant.exceptions import ServiceValidationError
    import voluptuous as vol
    with pytest.raises((vol.Invalid, ServiceValidationError)):
        await hass.services.async_call("homehub_media", "send_text", {"text": "a\x00b"}, blocking=True)


async def test_page_state_requires_auth(hass, hass_client_no_auth, setup_entry):
    client = await hass_client_no_auth()
    resp = await client.get("/api/homehub_media/page/state")
    assert resp.status == 401


async def test_art_rejects_unknown_server(hass, hass_client, setup_entry):
    client = await hass_client()
    resp = await client.get("/api/homehub_media/art/evil/abc")
    assert resp.status == 404
```
(`setup_entry` fixture in `tests/conftest.py`: MockConfigEntry with data `{"host": "192.0.2.1", "token": "t", "mac": None}`, options `{"ambience": {"backend": "tv", "radio": {}}}`, patches `custom_components.homehub_media.hub.SamsungTV` with an `AsyncMock` whose `discover_apps` returns `{"youtube": "111299001912"}`.)

Commit `feat: hub wiring, websocket API, services, page and art views`.

### Task 12 (parallel with 11 after 9/10): Entities (`select.py`, `switch.py`, `sensor.py`, `conversation.py`)

- All entities: `_attr_has_entity_name = True`, `unique_id = f"{entry.entry_id}_{key}"`, one device `DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="HomeHub Media", connections={("mac", mac)} if mac)`, subscribe via `async_dispatcher_connect(hass, SIGNAL_UPDATE, self.async_write_ha_state)`.
- `select.ambience_mode`: options `["off", *mode ids]` (translated names via `translation_key`), select → `ambience.start`/`stop`. `select.activity`: options = `snapshot.activity.available`, select → `activity.set`.
- `switch.quiet_mode` → `activity.set_quiet`; `switch.auto_ambience` → `ambience.auto` (persist with `RestoreEntity`).
- `sensor.ambience_sleep_timer`: `SensorDeviceClass.TIMESTAMP`, value `sleep_until`. `sensor.now_playing`: state = first title or `None`, attributes `server, player, subtitle, position, duration, state` (no image URL in attributes).
- `conversation.py`: `HomeHubConversationEntity(conversation.ConversationEntity)`, `supported_languages = "*"`, `_async_handle_message(user_input, chat_log)`: `await chat_log.async_provide_llm_data(user_input.as_llm_context(DOMAIN), [API_ID], PROMPT, user_input.extra_system_prompt)`; loop ≤ 5: convert `chat_log.content` to the neutral message list, `reply = await assistant.complete(...)`, append `AssistantContent(agent_id=user_input.agent_id, content=reply.text, tool_calls=[llm.ToolInput(id=..., tool_name=..., tool_args=...)])` via `async for _ in chat_log.async_add_assistant_content(content)`; stop when no tool calls; return `conversation.async_get_result_from_chat_log(user_input, chat_log)`. (Verify every name above against `homeassistant/components/conversation/__init__.py` and `chat_log.py` in the installed 2026.9.4 package.)
- Tests: `tests/test_entities.py` — select changes call the manager (AsyncMock), switch state restores, sensor timestamp; `tests/test_conversation.py` — monkeypatch `assistant.complete` to return one tool call then text; assert the tool ran (manager AsyncMock awaited) and the response speech equals the final text.
- Commit `feat: select, switch, sensor and conversation entities`.

### Task 13 (parallel with 11/12): Config + options flow (`config_flow.py`, `translations/en.json`)

- `async_step_user`: if a `samsungtv` entry exists, prefill host/mac from its `data`; fields `host` (required), `mac` (optional). Submit → `await tv.device_info()` (REST; error `cannot_connect`), then show `async_step_pair` (description: "Press **Allow** on the TV within 30 seconds") → `await tv.pair()`; `PairingDenied` → error `pairing_denied`; success → `async_create_entry(title=modelName, data={host, mac, token, port: 8002}, options={"ambience": {"backend": "tv", "radio": {}}, "modes": DEFAULT_MODES, "volume": DEFAULT_VOLUME, "dayparts": DEFAULT_DAYPARTS})`.
- `async_step_reconfigure`: host/mac edit + re-pair.
- Options flow (`OptionsFlowWithReload`), `async_step_init` = `async_show_menu` with steps: `tv`, `apps`, `servers`, `box`, `pc`, `keyboard`, `ambience`, `modes`, `volume`, `dayparts`, `assistant`, `done`. Each step edits only its section and returns to the menu (`done` → `async_create_entry(data=self._options)`).
  - `tv`: `entity` EntitySelector(media_player), `app_entity` EntitySelector(sensor/select/media_player), `base_url` url text.
  - `apps`: multi-select of APP_CATALOG keys (default: discovered), plus an "overrides" text field `key=id, key=id` parsed and validated (`^[A-Za-z0-9._-]{1,64}$`); a "Rescan" boolean triggers `tv.discover_apps()`.
  - `servers`: Plex url/token/player, Jellyfin url/api_key/user_id/player; on submit validate by calling `continue_watching()` (`cannot_connect`/`invalid_auth`); "Prefill from existing integration" boolean copies the Plex url+token from the `plex` entry's `data["server_config"]` with explicit user consent text.
  - `box`: remote EntitySelector(remote), media_player EntitySelector(media_player), input method select + value.
  - `pc`: host, ambient_port (8097), ambient_token (password), coord_port (8098), coord_token (password), input method + value, clients (comma list); validate with `status()` on both (`invalid_auth`, `cannot_connect`).
  - `keyboard`: host, key (password).
  - `ambience`: backend select (`pc` only offered when pc configured), radio (text area `id=https://url` lines, validated with `catalog.radio_items`).
  - `modes`: first a form "Mode to edit" select (existing modes + "Add new mode"), then a form with name, icon (IconSelector), scene (EntitySelector scene, optional), flicker (EntitySelector light, multiple, optional), lights_only, delete; id = slugify(name) for new modes.
  - `volume`: normal/quiet NumberSelector 0-100, block_entity EntitySelector(input_boolean/switch/binary_sensor) optional.
  - `dayparts`: four TimeSelector + mode select each, idle_minutes 1-240.
  - `assistant`: provider select (none/ollama/openai/claude) → second form: url (ollama, default `http://homeassistant.local:11434`) or api_key (password) → `await assistant.ensure_sdk(...)`, `await assistant.list_models(...)` → third form: model select (Claude default `claude-opus-5-5` when present).
- `translations/en.json`: every step title/field/description, errors `cannot_connect`, `pairing_denied`, `invalid_auth`, `invalid_url`, `invalid_id`, aborts `single_instance_allowed`, `reconfigure_successful`, plus `services` names/descriptions/fields for K6 and `entity` names for Task 12. No `[%key:%]` references.
- Tests `tests/test_config_flow.py`: happy path (patch `SamsungTV.device_info` and `.pair`), pairing denied error, options menu → volume step saves, assistant step lists models (patch `list_models`), servers step invalid auth error.
- Commit `feat: config flow with TV pairing and options menu`.

---

## Phase 3 — Finish (sequential, orchestrator + reviewers)

### Task 14: Blueprints

`blueprints/automation/homehub_media/stop_ambience_on_app.yaml` (inputs: TV media_player; trigger: `source`/`app_id`/`media_title` attribute change or `state` → `playing`; condition: `select.homehub_media_ambience_mode` not `off`; action: `homehub_media.ambience_stop`), `quiet_mode_schedule.yaml` (inputs: start/end time; turns `switch.homehub_media_quiet_mode` on/off), `sunset_ambience.yaml` (trigger sun sunset offset input; condition TV media_player `off`; action `homehub_media.ambience_auto`). Validate each loads: `.venv/bin/python -c "import yaml,sys;[yaml.safe_load(open(f)) for f in sys.argv[1:]]" blueprints/automation/homehub_media/*.yaml` (with a `!input` constructor registered). Commit `feat: automation blueprints`.

### Task 15: Docs, brand, cleanup

- `README.md` (for HA users): badges (HACS custom, release, validate), features, screenshots (from Task 7 step 4), install via HACS custom repository, setup (pairing), options sections table, **"Where to put ambience videos"** (`/media/homehub/ambience/videos` + `/soundtracks`, Samba `media` share or Media panel upload, H.264 MP4, sidecar previews), card YAML examples (inline + drawer), services list, blueprint import buttons, TV limitations, Media PC section linking `media-pc/README.md`, security notes (token handling, signed URLs, cleartext ESP32), credits to the original author, MIT.
- `custom_components/homehub_media/brand/icon.png` (256×256) + `icon@2x.png` (512×512): generate a simple glyph (rounded square, play-triangle + wave) with Pillow in a scratch script; not committed.
- Delete the old `home-assistant/` directory, `MANIFEST.md`, `HANDOFF.md`; keep `docs/superpowers/`.
- Commit `docs: README, brand icon; remove legacy YAML bundle`.

### Task 16: Verification + reviews

- [ ] `.venv/bin/ruff check custom_components tests media-pc && .venv/bin/pytest -q && node --test tests/js/` → all green (paste summary).
- [ ] Dispatch `security-auditor` on the new tree (focus: services/ws/views auth, signed URL scope, card/page XSS, media-pc auth). Fix Critical/High.
- [ ] Dispatch `code-reviewer` + `/ponytail-review` on the full diff from `32557aa`. Apply agreed fixes.
- [ ] Commit fixes.

### Task 17: Live test on the owner's HA + TV

- [ ] `gh repo create alws34/ha-homehub-media --public --source . --push --description "Samsung TV remote, ambience and Plex/Jellyfin hub for Home Assistant"`; add topics `home-assistant, hacs, samsung, tizen, plex, jellyfin`.
- [ ] Wait for CI green (`gh run watch`).
- [ ] With the user's OK: add the repo to HACS as a custom repository via the HA websocket API, install, restart HA (`homeassistant.restart`), confirm the integration loads with no errors in `system_log`.
- [ ] With the user next to the TV: config flow pairing (Allow), then verify and record in `docs/tv-compat.md`: keys, each app launch, Netflix ID, text input in YouTube search (IME events seen?), pointer in Browser (touchEnable seen? relative deltas?), `open_url` variant that works, ambience page autoplay + loop + sound gesture, 1 h run, sleep timer fade, Plex play_library_item, Jellyfin via browser fallback.
- [ ] Fix what fails; re-run tests; commit.

### Task 18: Release

- [ ] Bump `manifest.json` version to `1.0.0`; `git tag v1.0.0`; `gh release create v1.0.0 --title "HomeHub Media 1.0.0" --notes-file docs/release-notes-1.0.0.md` (notes: features, migration from the YAML bundle, breaking changes for media-PC users: token required, profiles move to HA).
- [ ] Remind the user to revoke the long-lived token used for testing.
