# Architecture

HomeHub Media is a Home Assistant custom integration (`custom_components/homehub_media`). It has four parts:

- a Samsung Tizen TV client;
- an ambience engine that combines lights with a video or soundtrack;
- a Plex and Jellyfin library;
- a voice assistant.

It ships one Lovelace card and one TV page. Two optional Ubuntu daemons live in `media-pc/`.
The rules in this file are binding. When code and this document disagree, fix the code or change this document in
the same commit.

## Layers

```
www/homehub-media-card.js ── websocket (subscribe/catalog/library) + callService ──┐
www/ambience.html ── polls a signed state URL ──────────────────────────────────────┤
                                                                                    ▼
api.py            HA surfaces: websocket commands, services, HTTP views (thin: validate → hub → answer)
entities          select / switch / sensor / conversation (thin: read hub state, call hub methods)
assistant.py      LLM tools API (HubTool, HomeHubLLMAPI): validate the model's arguments → hub
config_flow.py    pairing, reconfigure, reauth, options menu
        │
        ▼
hub.py            composition root + command routing + card snapshot
  ├── activity.py   ActivityManager: off / tv / box / pc, inputs, quiet volume
  ├── ambience.py   AmbienceManager: lights, playback backends, health, sleep timer, times of day
  └── store.py      HomeHubStore: per-mode compositions, favourites
        │
        ▼
device clients    tv.py (Samsung WS 8002, REST 8001, DIAL 8080, YouTube Lounge, Wake-on-LAN)
                  library.py (Plex, Jellyfin, YouTube thumbnails, `read_capped`) · pc.py (media PC) · keyboard.py (ESP32)
                  assistant.py (Ollama / OpenAI / Anthropic SDKs)
pure helpers      catalog.py (scanning, link parsing, composition validation) · const.py (contracts)
```

### Dependency rules

1. **Dependencies point downward.** A device client never imports `hub`, `ambience`, `activity`, `api` or an
   entity platform. `catalog.py` and `const.py` import nothing from the package except each other.
   `assistant.py` is the one module in two layers: its LLM tools API is a surface (like `api.py`, it finds the hub
   with `hub.loaded_hub` at call time), and its SDK half imports nothing above it. Device clients may import
   `library.read_capped`.
2. **`hub.py` is the only composition root.** It builds every client and manager once per config entry and stores
   itself in `entry.runtime_data`. Nothing else constructs clients, except `config_flow.py`, which builds
   short-lived ones to probe a device before saving.
3. **Surfaces are thin.** `api.py`, entities and the card validate input, call one hub or manager method, and
   report the result. Routing decisions live in `hub.py` (for example: TV or box or PC, entity or key, IME or keyboard
   bridge). Ambience logic lives in `ambience.py`.
4. **Each external protocol lives in exactly one client module** (see the table below). If two features need the
   same device, they share that client; they never open a second connection.
5. **`catalog.py` stays pure.** No HA objects and no I/O except `scan()`, which blocks and is always run in the
   executor.
6. **The frontend talks to the backend only through the websocket commands and services in `api.py`.** It never
   reads entity attributes for data the snapshot already carries, and it never calls another integration's services
   directly. Routing to the core Samsung TV entity happens in the hub.

## External systems

| System | Module | Transport | Timeout |
|---|---|---|---|
| Samsung TV remote | `tv.py` | WebSocket `wss://host:8002`, pairing token: typing, pointer, key holds, and keys when the core remote is unusable | connect 10 s, send 15 s |
| Samsung TV apps / power state | `tv.py` | REST `http://host:8001/api/v2/` | 5 s |
| YouTube on the TV | `tv.py` | DIAL `http://host:8080/ws/apps/YouTube` + YouTube Lounge (pyytlounge) | 5 s / 20 s start / 5 s disconnect |
| Wake-on-LAN | `tv.py` | UDP broadcast, executor | n/a |
| Core Samsung TV integration | `activity.py`, `hub.py` | HA services on the configured `media_player` and the `remote` on its device (and box remote, lights) via `activity.call_service` | 15 s |
| Plex / Jellyfin | `library.py` | HTTP(S) with token header | 10 s |
| YouTube thumbnails / oEmbed | `library.py`, `ambience.py` | HTTPS to fixed YouTube hosts | 10 s / 5 s |
| Media PC daemons | `pc.py` | HTTP + Bearer token | 10 s (play 30 s) |
| ESP32 keyboard bridge | `keyboard.py` | HTTP + shared key header | 15 s |
| LLM providers | `assistant.py` | provider SDK on HA's httpx client | 60 s |

**TV keys**: `Hub.send_tv_key` is the only path for a TV key press (the hub's remote, keyboard Enter, the activity's
power, HOME and input keys, ambience's autoplay OK and HOME, the `send_key` service). It finds the `remote` entity on
the configured TV `media_player`'s device in the entity registry (platform `samsungtv` first) and, when its state
exists and is not `unavailable`, sends the key with `remote.send_command`. Otherwise, and for key holds, it uses
`tv.send_key`. This keeps HomeHub's own WebSocket, which the TV asks to Allow after every power-on, closed until the
user types or moves the pointer. Managers and surfaces call `hub.send_tv_key`, never `tv.send_key`.

## Lifecycle

- **`async_setup`** (once per HA start): registers the websocket commands, services and views (`api.async_register`),
  the static path `/homehub_media_static` and the card's extra JS URL, and the samsungtvws and pyytlounge log
  filter.
- **`async_setup_entry`** runs in this order:
  1. creates `Hub` and registers `async_on_unload(hub.async_stop)`;
  2. awaits `hub.async_start()`, which loads the Store;
  3. starts the first TV, catalog and library loads as a background task, so a device that is off never fails or
     slows setup;
  4. registers the LLM API and forwards the platforms.
- **Unload** (`hub.async_stop`):
  1. runs the unsubscribe callbacks in reverse order;
  2. ambience shutdown, which stops the mode and restores the lights;
  3. activity shutdown;
  4. `tv.close()`: ends the YouTube Lounge session (cancels and awaits its subscription task), then closes the
     WebSocket.

  Every timer, interval, listener and background task must be registered so that this path cancels it.
- **Options changes** reload the entry (`OptionsFlowWithReload`). There is no update listener. Code that writes
  options at runtime (`apps.discovered`, `ambience.youtube`) updates `hub.options` itself and must not trigger a reload.

## State and persistence

| Where | What | Notes |
|---|---|---|
| `entry.data` | host, MAC, port, TV token, YouTube Lounge auth | written only by the config flow and by token rotation |
| `entry.options` | one section per options-menu item (K1 shapes in `const.py`) | secrets included; see `cybersecurity.md` |
| `HomeHubStore` (`.storage/homehub_media.data`) | mode compositions, favourites | validated on load; invalid entries are dropped with a warning |
| `RestoreEntity` | activity, quiet mode, auto ambience | flags only |
| memory only | light snapshot scene, page session id, lounge session | lost on restart, by design |

## Data flow

- **Card**: `homehub_media/subscribe` pushes `hub.snapshot()` (contract K4) on every `SIGNAL_UPDATE`.
  `catalog` and `library` are pulled on demand. Commands go through `callService` and update the snapshot through
  the next push. Images reach the card only as signed same-origin URLs.
- **Ambience start**, in this order:
  1. validate the composition;
  2. pick the visual (random from the mode's pool);
  3. choose the player (YouTube on the TV, or the configured backend);
  4. take the lock and bump the generation;
  5. stop the previous mode, keeping its light snapshot when the new mode also drives lights;
  6. switch activity if needed;
  7. lights: `scene.create` snapshot, then the mode's scene, then flicker;
  8. play;
  9. run a health check every 30 s.

  Stop reverses this order and always ends by restoring the snapshot.
- **Activity switch**: power on through the core TV entity (or Wake-on-LAN), wait, settle, switch input with
  verification, apply volume. The newest request cancels the running one.

## Concurrency rules

- **Ambience**: `AmbienceManager._lock` serialises start, stop, update and health stops. Callbacks scheduled for a run
  (health checks, sleep timer, fades) capture `_gen` and do nothing when it has moved on.
- **TV client**: `tv._lock` serialises WebSocket sends, and `tv._yt_lock` guards the YouTube Lounge session.
  Never await a lock-holding call from inside another holder of the same lock (`asyncio.Lock` is not re-entrant).
- **Activity**: requests use "mode: restart". A superseded caller returns `False` and must abort what it was about
  to do next.
- **Background work**: long-lived tasks are created with `entry.async_create_background_task`, or with
  `hass.async_create_background_task` when their owner cancels and awaits them itself (the flicker and PC fade in
  `ambience._cancel`, the YouTube Lounge subscription in `tv._drop_lounge`). Either way they are cancelled and
  awaited before lights are restored or the entry unloads.
- **Device I/O**: every await on a device or remote service has a timeout. A hung device must never hold a lock
  forever.
  A service call that runs past `activity.call_service`'s 15 s is cancelled, and so is the handler it runs (for
  example a scene or a `turn_on` automation that takes longer). Accepted: light and TV calls normally finish well
  under a second, and a hung integration must not hold the ambience lock or an activity switch.

## Conventions

- **Errors**:
  - bad user input or an unsupported action raises `ServiceValidationError`;
  - a device failure raises `HomeAssistantError` or a typed subclass (`CannotConnect`, `PairingDenied`, `InvalidAuth`,
    `PCAuthError`);
  - `PairingDenied` starts reauth once;
  - HTTP views map `ServiceValidationError` to 404 and device errors to 502.
- **Messages**: user-facing messages are plain English sentences and name the device or app, never an internal ID
  when a name is known.
- **Contracts**: K1–K8 in `const.py` and in the plan are frozen interfaces. Changing one means updating the card, the
  tests and the docs in the same change.
- **Translations**: config and options text, entity names, services and selectors go in `translations/en.json`.
  hassfest rejects URLs with domains in translation strings.
- **Style ("ponytail")**: the shortest correct code; reuse existing helpers before writing new ones; no speculative
  abstraction. A deliberate limit gets a `# ponytail:` comment that names the limit and the upgrade path.
- **Python**: 3.14, ruff (`pyproject.toml`, line length 110), no blocking I/O in the event loop. File scans,
  Wake-on-LAN and SDK construction go to the executor.
- **Tests**:
  - Python: `pytest-homeassistant-custom-component`, `asyncio_mode=auto`, one `tests/test_<module>.py` per module;
    media-PC tests in `media-pc/tests`.
  - Card and TV page: `node --test 'tests/js/*.test.mjs'`.
  - Every behaviour change ships with a test that fails without it.

## Adding a feature

1. Put protocol code in the client module that owns the device, or add a new client module if it is a new device.
2. Put routing in `hub.py` and ambience behaviour in `ambience.py`.
3. Expose it through a service or websocket command in `api.py`: schema, `services.yaml`, translations. Only then
   expose it in the card.
4. Persisted shapes go through `const.py` (options) or `store.py` (Store), with validation on read.
5. Follow `cybersecurity.md` for every new input, secret, outbound host or view.
6. Update `README.md` (user-facing) and this file (structure) in the same change.
