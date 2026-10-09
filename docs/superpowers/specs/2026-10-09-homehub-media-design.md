# HomeHub Media — HACS integration design

Date: 2026-10-09 · Status: approved in brainstorming, pending spec review

## Goal

Turn the HomeHub Media Controller & Ambience bundle (6.7k lines of HA YAML, 5 cards, 2 Ubuntu daemons,
5 third-party card dependencies) into **one public HACS integration repo with one bundled Lovelace card**,
keeping the end-user behaviour, fixing the audit findings, and supporting two households:

- **Owner (TV-only):** Samsung QE55Q60B (Tizen 6.5), Plex, Jellyfin. No PC.
- **Original author:** same Samsung TV + Ubuntu media PC on HDMI (mpv ambience + kiosk browser),
  Android-TV "streaming box" on HDMI, ESP32 BLE-HID keyboard bridge, Jellyfin/Sonarr/Radarr web clients.

Every target besides the Samsung TV is optional and configured in the UI.

## Repo layout

```
custom_components/homehub_media/   # what HACS installs
  __init__.py  manifest.json  config_flow.py  const.py  services.yaml
  tv.py            # Samsung WebSocket/REST client (keys, apps, text, pointer, browser)
  pc.py            # Media-PC clients (ambient player + coordinator, token auth)
  keyboard.py      # ESP32 bridge client
  ambience.py      # catalog, modes store, favourites, lighting, playback orchestration
  library.py       # Plex / Jellyfin continue-watching + recently-added
  activity.py      # Off / TV apps / streaming box / media PC state machine, quiet volume
  assistant.py     # llm.API tools + provider adapters (Ollama / OpenAI / Claude)
  select.py switch.py sensor.py conversation.py
  www/homehub-media-card.js   www/ambience.html
  brand/icon.png …   translations/en.json
media-pc/                          # NOT installed by HACS; original author's Ubuntu side
  ambient-player/  media-hub-coordinator/  systemd-user/
blueprints/automation/homehub_media/
docs/  README.md  LICENSE (MIT)  hacs.json  .github/workflows/validate.yml
```

Domain `homehub_media`; `single_config_entry: true`; `iot_class: local_push`;
requirements `samsungtvws[async,encrypted]>=3.0.5` (already present via core `samsungtv`; loose spec avoids
a downgrade fight), `plexapi` not required (plain aiohttp to Plex/Jellyfin APIs).

## Configuration (config flow + menu options flow)

Config flow: **TV** step — host/MAC prefilled from an existing `samsungtv` entry if present; the integration
pairs **its own** WebSocket client named "HomeHub" (user presses Allow on the TV once; token stored in our entry).
It never reads or mutates the core entry's token.

Options flow (menu), every section optional:

| Section | Fields |
|---|---|
| TV | power/volume/source `media_player` entity (e.g. SmartThings or core samsungtv; falls back to own client), current-app entity (optional, for tile highlight), input-switch method per input (`select_source` name or `KEY_HDMIn`) |
| Apps | which tiles to show + order; per-app Tizen ID override (built-in fallback chain per app: Netflix, YouTube, Prime Video, Disney+, Apple TV, Max, Plex, Jellyfin, Spotify, Twitch, Paramount+, Browser) |
| Media servers | Plex (URL + token), Jellyfin (URL + API key + user); each can be prefilled from the existing core entry with explicit consent |
| Streaming box | `remote` entity, `media_player` entity (channels), TV input it sits on |
| Media PC | host, ambient-player port + token, coordinator port + token, TV input, browser clients (name → unit, e.g. jellyfin/plex/sonarr/radarr) |
| Keyboard bridge | ESP32 host + shared key |
| Ambience | player backend: **TV browser** or **Media PC**; radio streams (HTTPS only) for TV backend |
| Modes | per mode: name, icon, scene (none = no lighting), flicker lights (none = no flicker), lights-only flag |
| Volume | normal %, quiet %, optional block entity (e.g. night-mode boolean that blocks everything but Off) |
| Dayparts | morning/afternoon/evening/night start times → mode; idle auto-start minutes |
| Assistant | provider (Ollama / OpenAI / Claude), URL or API key, model (live list) |

## Components

### TV client (`tv.py`)
One `SamsungTVWSAsyncRemote` on port 8002, `key_press_delay=0`, own token, reconnect on demand.
- `send_key(key, hold_secs?)`, `launch_app(app)` (WS `ed.apps.launch`, fallback REST `run`, walk ID chain,
  verify via REST status), `restart_app(app)` (REST close → run → verify; ports the original "refresh" button),
  `open_url(url)` (browser `NATIVE_LAUNCH`), `send_text(text)` (textReceived → `SendInputString` → `SendInputEnd`),
  `pointer_move(dx,dy)`, `pointer_click()`.
- Tracks `imeStart/imeEnd` and `touchEnable/touchDisable` events; exposes `keyboard_active`, `pointer_active`
  so the card can say "focus a search field first" / "pointer works in the browser" instead of failing silently.
- Text limited to 1–200 chars, no control characters.

### Activity (`activity.py`)
Ports `homehub_media_set_activity`: activities **Off**, **TV apps**, **Streaming box**, **Media PC**;
`mode: restart` semantics (newest request wins via a task that is cancelled on new requests);
block entity → only Off allowed; phases Ready / Transitioning / Needs reset / Blocked. Quiet mode applies
normal/quiet volume through the TV `media_player`. Entering any activity ends TV-owning ambience first.

### Ambience (`ambience.py`)
- **Catalog** comes from the backend:
  - TV backend: scan `/media/homehub/ambience/videos` and `/soundtracks` (same extensions and id rules as the
    original: slugified stem, `_2` suffix on collision, `ID_RE`), plus configured radio streams.
    Preview = sidecar image with the same stem (`.webp/.jpg/.png`).
  - PC backend: `GET /v2/catalog`; previews from `/config/www/Ambience/<id>.(webp|png)` as before.
- **Modes, compositions, favourites** live in HA (`helpers.storage.Store`), for both backends. Seeded with the
  original modes: Fireplace, Dinner, Reading, Game Night, Chill, Movie Time (lights-only), DOG TV.
  A composition = visual, visual_enabled, video_loop, video_random_start, original_volume, soundtrack,
  soundtrack_loop, soundtrack_random_start, soundtrack_volume (same fields/ranges as the original).
- **Start mode:** stop other ambience → snapshot affected lights (`scene.create` with group expansion) →
  activate configured scene → start flicker task if configured → switch TV input (PC backend) → play.
  **Stop:** stop media → cancel flicker → restore snapshot.
- **Live edits** (visual / soundtrack / loop / mix) apply immediately when editing the active mode.
- **TV browser player:** `www/ambience.html` served from the integration's static path (no secrets in it).
  Opened via `open_url` with a signed URL to a state endpoint. The page polls that endpoint (~1 s), plays a
  muted looping `<video>` and a separate `<audio>` with volumes from the composition, and reports a heartbeat.
  Media URLs are HA-signed (`async_sign_path`, content user, 24 h), refreshed on every state response.
- **Health:** PC backend polled via `/v2/status`; TV page heartbeat. If playback dies, HA's mode resets to Off
  (fixes the original's state drift).

### Library (`library.py`)
Per configured server: Continue Watching + Recently Added (Plex `/hubs/continueWatching/items`,
`/library/recentlyAdded`; Jellyfin `/UserItems/Resume`, `/Items/Latest`), polled every 5 min and on demand,
normalised to `{id, title, subtitle, progress, image, server}`. Artwork proxied through an authenticated HA view
so server tokens never reach the browser. Tap = launch the server's TV app, wait for its session/client, then play
(Plex `play_media` via core plex entity if present; Jellyfin `POST /Sessions/{id}/Playing` with resume ticks).
If remote play is unsupported by the TV app, the app is still opened and the card says so.
*As built:* if the Plex or Jellyfin TV app is not installed, `launch_app` opens that server's configured URL in the
TV browser instead (the web client then serves as the play target).

### Media PC & keyboard bridge clients
`pc.py`: async aiohttp clients for the ambient player (catalog, play, patch, stop, status) and coordinator
(open/close client, remote command, text, pointer move/click/scroll) sending `Authorization: Bearer <token>`.
The "stop ambience before opening a browser client" step moves from the coordinator into HA (both are HA-driven),
which removes the coordinator's `ambient_stop_url` and fixes the original loopback-vs-LAN-bind bug.
`keyboard.py`: ESP32 `/type`, `/key` with `X-HomeHub-Key` (cleartext; documented limitation).

### Sleep timer (feature 2)
`ambience_sleep_timer(minutes | 0 to cancel)`: fades both layers' volume to 0 during the last 60 s before the
end time (TV page: client-side ramp; PC: `PATCH /v2/state` steps), then at the end time runs the normal mode stop
(media stop, flicker cancel, light restore). Exposed as `sensor.homehub_media_ambience_sleep_timer` (device_class
`timestamp`, the end time) and timer chips (15/30/60/90) in the Ambience tab.

### Now Playing strip (feature 3)
Library pollers also read active sessions (Plex `/status/sessions`, Jellyfin `/Sessions`) every 10 s while any
server is configured; optional "player name contains" filter (default: all sessions of the configured user).
The card shows artwork, title, progress and play/pause/rewind/fast-forward. Controls send the `remote` service's
`play`/`pause`/`rewind`/`fast_forward` to the current activity's device (TV: `KEY_PLAY`/`KEY_PAUSE`/`KEY_REWIND`/
`KEY_FF`), so no per-client remote-control protocol is needed.
Also exposed as `sensor.homehub_media_now_playing` (state = title, attributes = server, player, subtitle, state,
position, duration; no image, since its URL is signed per caller).

### Time-of-day ambience (feature 4)
Options: daypart start times (morning / afternoon / evening / night) → mode per daypart, and an
"auto-start when idle" switch with N minutes. `homehub_media.ambience_auto` starts the current daypart's mode
(the card's **Auto** tile calls it). Idle auto-start (switch on) fires when, for N minutes, the TV is on in the
"TV apps" activity with no ambience, no Plex/Jellyfin session and no HomeHub command; if a current-app entity is
configured, it must also report the home screen / no app.
Entity: `switch.homehub_media_auto_ambience`.

### Blueprints (feature 5)
`blueprints/automation/homehub_media/`: *stop ambience when the TV switches to an app or input*,
*quiet mode on a schedule*, *start daypart ambience at sunset when the TV is off*. README import badges
(`my.home-assistant.io/redirect/blueprint_import`).

### Voice / AI assistant
- Options section **Assistant**: provider **Ollama** (base URL, no key), **OpenAI** (API key), or **Claude**
  (Anthropic API key); model dropdown filled live from the provider (Ollama `/api/tags`, OpenAI `models.list`,
  Anthropic `models.list`); key validated in the flow. Claude default model `claude-opus-5-5`.
- Tools are defined once as an `llm.API` (id `homehub_media_tools`, name "HomeHub Media"): `LaunchApp`, `Remote`
  (logical remote command to the current activity; replaces a raw `send_key` tool), `SetActivity`,
  `AmbienceStart` (only when modes exist; enum of mode ids), `AmbienceStop`, `AmbienceAuto`, `SleepTimer`,
  `SetQuietMode`, `PlayLibraryItem` (search by title in continue watching / recently added), `NowPlaying`.
  Registering the API also lets the core Ollama/OpenAI/Anthropic conversation agents use these tools.
- `conversation.homehub_media_assistant` entity: selectable in Settings → Voice assistants. It runs a minimal
  non-streaming tool loop (max 5 rounds) against the chosen provider with the official SDKs (`anthropic`,
  `openai`; Ollama via its OpenAI-compatible `/v1` endpoint). Only the chosen provider's SDK is installed, on
  demand (`async_process_requirements` when the options flow connects and at entry setup), not as a manifest
  requirement.
  Claude: `output_config.effort: "low"` for snappy voice replies, `tool_choice: auto`, server-side fallback
  (`fallbacks: "default"`), `stop_reason == "refusal"` handled with a spoken apology.
- Least privilege: the tools control only HomeHub; library titles passed to the model are data, and every tool
  call goes through the same validated service handlers as the card.

### Services
`homehub_media.` (K6, `api.py` / `services.yaml`): `remote`, `send_key`, `launch_app`, `restart_app`, `send_text`,
`keyboard_key`, `pointer`, `set_activity`, `channel`, `ambience_start`, `ambience_stop`, `ambience_update`,
`save_mode`, `set_favorite`, `ambience_auto`, `ambience_sleep_timer`, `refresh_catalog`, `rescan_apps`,
`play_library_item`, `pc_open`, `pc_close` (`remote` with a `target` replaces the planned `pc_command`).
All registered in `async_setup`, vol schemas with bounds, `ServiceValidationError` on bad input. Websocket
commands for the card: `homehub_media/subscribe`, `homehub_media/catalog`, `homehub_media/library`.

### Entities
One device, "HomeHub Media" (`has_entity_name` + translation keys), so every id is `homehub_media_*`:
`select.homehub_media_ambience_mode` (Off + modes), `select.homehub_media_activity`,
`switch.homehub_media_quiet_mode`, `switch.homehub_media_auto_ambience`, `sensor.homehub_media_ambience_sleep_timer`,
`sensor.homehub_media_now_playing`, `conversation.homehub_media_assistant` (when an assistant provider is configured).
Everything else that was an input_* helper becomes internal state or card-local UI state.

### Card (`homehub-media-card`)
Single vanilla web component, no build step, no third-party card dependencies, HA theme variables
(light/dark), `getGridOptions` for sections views, visual editor not required (YAML config: `drawer: true`,
`tabs: [...]`, plus `default_tab` and `target` as built). Tabs shown only for configured targets:
- **Remote:** target pills (TV / Streaming box / Media PC), power/home/back, D-pad (swipe + tap), volume/mute,
  play/pause, app grid with brand glyphs, keyboard field (TV IME, PC coordinator, or ESP32), touchpad pointer
  (TV when pointer active, PC always), streaming-box channel keypad with 1.5 s debounce.
- **Ambience:** mode tiles (ON state), editor with visual/soundtrack carousels (previews, favourites, swipe),
  mix slider, loops/random toggles, save.
- **Library:** Continue Watching / Recently Added rows per server.
- **Now Playing strip** above the tabs whenever a Plex/Jellyfin session is active; **Auto** tile and sleep-timer
  chips in the Ambience tab.
- **Drawer mode:** fixed right-edge slide-out with handle, like the original.
All rendered text escaped; DOM updated only when relevant state changes.

## Media-PC hardening (`media-pc/`)

- Both daemons: required bearer token (constant-time compare) **in addition to** the IP allowlist;
  `Host` header must match configured host(s) (DNS-rebinding); POST/PUT/PATCH require
  `Content-Type: application/json` (kills simple-request CSRF); handler socket timeout; bounded concurrency;
  generic error messages.
- Coordinator: remove `127.0.0.1` from the default allowlist; remove `ambient_stop_url`; clients become a free
  dict (adds `plex`) instead of exactly jellyfin/sonarr/radarr; fix Stop (was identical to Back).
- Ambient player: becomes catalog + player only (profiles/favourites/legacy scenes move to HA);
  startup no longer crash-loops on one bad file (skip + report); audio-only guard uses a small PNG instead of a
  6 MB PPM.
- systemd units: `ReadWritePaths=%t` for the ambient unit (mpv sockets), plus `RestrictAddressFamilies`,
  `RestrictNamespaces`, `LockPersonality`, `RestrictSUIDSGID`, `SystemCallFilter=@system-service`,
  `CapabilityBoundingSet=`.

## Dropped (dead or home-specific)

~45 dead YAML objects, legacy `/v1/play` scenes, hardcoded light presets for the author's rooms (replaced by
scenes), Tuya-specific fireplace services (replaced by generic flicker on chosen lights), cover/sun-glare logic
(an automation the user can write), the 5 third-party card dependencies, the swipe-helper card, the second
keyboard card.

## Error handling

TV unreachable → entities stay available, service raises `HomeAssistantError("TV is off or unreachable")`;
card shows an inline banner. Pairing denied → config flow error `pairing_denied`. Backend 401/403 → error
"Media PC rejected the token" (service error + card toast). All network calls have timeouts; no blocking I/O in the event loop (file scans in executor).

## Testing

- `tests/` with `pytest-homeassistant-custom-component`: config/options flow, services schema validation,
  ambience catalog id rules, activity state machine, mode start/stop snapshot ordering, sleep-timer fade,
  daypart selection, assistant tool loop against a stubbed provider for each of the three adapters.
- `media-pc/` stdlib `unittest`: auth (token, Host, Content-Type, allowlist) for both daemons.
- Card: a small node test for pure helpers (escaping, id → title).
- Live: HACS custom-repo install on the owner's HA, then an on-TV session (pairing, keys, apps, text,
  pointer in browser, ambience page autoplay/loop for > 1 h).
- CI: hassfest + HACS action + pytest.

## Delivery

Public GitHub repo `alws34/ha-homehub-media`, MIT, credits the original author, README with install,
configuration, ambience folder layout, TV limitations, media-PC setup guide, security notes. Release `v1.0.0`.

## Known TV limits (documented, not bugs)

Pointer only where Tizen enables it (built-in browser, some apps). Text input needs a focused text field.
App list query is unsupported on 2020+ TVs, hence ID fallback chains + overrides. Deep-linking into a specific
Netflix/Disney+/Prime title is not reliable and is out of scope.
