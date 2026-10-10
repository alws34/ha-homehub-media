# Cybersecurity

The security rules for HomeHub Media. They are binding for every change. A change that breaks a rule must either fix
the code or record an accepted risk here (section 9) with its reason.

## 1. Threat model

| Actor | Can do | Must not be able to |
|---|---|---|
| Authenticated non-admin HA user | Control the TV, ambience, activities and library; edit mode compositions and favourites; read the card feeds | Change integration configuration, install anything, make HA fetch arbitrary URLs, read any secret |
| HA admin | Everything above; configure devices, URLs, secrets | Trigger code execution or read secrets back through the UI |
| Device on the LAN (TV, media server, media PC, keyboard bridge, LLM server) | Return data to HA | Inject script into the card, crash or hang HA, make HA leak secrets, grow memory without bound |
| Anyone on the LAN without HA credentials | Reach the unauthenticated static files (card JS, TV page) | Reach any API, state or media, or replay a signed URL beyond its lifetime |
| Internet services (YouTube, LLM APIs, PyPI) | Return data | Same as LAN devices |

## 2. Input validation (trust boundaries)

Validate at the boundary, then again in the client that uses the value (defence in depth).

- **Services** use voluptuous schemas in `api.py`:
  - every string is length-bounded and regex-anchored (`\Z`);
  - every number is range-checked;
  - every list is capped;
  - enums are used wherever the set is known.

  Existing bounds:

  | Field | Rule |
  |---|---|
  | Text | 1–200 characters, no control characters (the media PC: 100 ASCII) |
  | Keys | `KEY_[A-Z0-9_]{1,32}` |
  | App IDs | `[A-Za-z0-9._-]{1,64}` |
  | Media IDs | `catalog.ID_RE` |
  | Pointer deltas | at most ±500 |
  | Videos per mode | at most 50 |

- **Websocket commands** take no free-form parameters. Any new parameter needs a schema.
- **The config and options flows** validate every field before saving:
  - hosts with `_host_ok`;
  - URLs with `_url_ok` (http or https only);
  - MACs, app IDs and slugs with regexes;
  - radio and YouTube entries through the `catalog.py` parsers.
- **HTTP views** validate path parameters with anchored regexes before any lookup or outbound request. The art
  proxy fetches only these allowlisted shapes:
  - a Plex key that decodes to `/library/metadata/<n>/(thumb|art)/<n>`;
  - a Jellyfin ID matching `ID_RE`;
  - a YouTube ID that is in the current catalog.
- **Device and server responses are untrusted**:
  - parse defensively, and catch the `FAILURES` tuple for malformed payloads;
  - coerce types;
  - filter IDs through `ID_RE`;
  - truncate titles;
  - act only on known TV events (the `FLAGS` map).
- **LLM tool calls** are untrusted input:
  - re-validate their arguments with the same schemas as the services;
  - tools expose only enumerated actions;
  - text sent back to the model is control-stripped and truncated.
- **Files**: catalog scanning skips hidden files and rejects any symlink that resolves outside the media root.

## 3. Secrets

The secrets are:
- the TV pairing token and the YouTube Lounge auth, kept in `entry.data`;
- the Plex token, the Jellyfin API key, the media-PC tokens, the keyboard bridge key and the LLM API key, kept in
  `entry.options`.

1. **Never log a secret.**
   - Pass any third-party logger that can see one (samsungtvws, pyytlounge) through the redacting log filter in
     `__init__.py`.
   - Exception messages that reach the UI or the log must not include URLs with credentials, headers or raw error
     bodies from authenticated endpoints.
2. **Never send a secret to the frontend.**
   - The snapshot, catalog, library feeds, entity attributes and option-form defaults carry no secrets. `_public()`
     strips `SECRETS` before a form is pre-filled.
   - Server-only fields (`path`, `url`, `youtube_id`, …) are stripped before data leaves the hub.
3. **A blank secret field keeps the saved value only while the origin is unchanged** (scheme, host and port, or the
   LLM provider). A saved token must never be sent to a new host without the admin typing it again.
4. **Secrets travel only in headers** (`X-Plex-Token`, `Authorization`, `X-HomeHub-Key`), never in query strings,
   because query strings end up in logs and browser history. The exception is the TV WebSocket token, which is part
   of the samsungtvws URL; see section 9.
5. **Nothing secret goes in the repo.** That includes tokens, LAN addresses of real devices, and personal media.
   Tests use documentation addresses (`192.0.2.x`).

## 4. Authentication and authorization

- **HTTP views** set `requires_auth = True`. The only unauthenticated content is the static card JS and the TV page
  HTML, which contain no data or secrets.
- **The TV page** gets its data only through a signed state URL:
  - the URL is bound to a random page-session ID (`sid`) and lasts 2 hours;
  - only the current `sid` is answered;
  - a replaced or stopped session gets 404 and the page goes black.
- **Signed URLs**:
  - signed for the caller's refresh token (websocket feeds) or the content user (TV page);
  - each lifetime is the shortest that works.
- **Authorization policy**:
  - **Any authenticated user** may use services that act on the TV, ambience, activities, library and their own
    modes and favourites.
  - **Admin only** (`ADMIN_SERVICES`, registered with `async_register_admin_service`): services that change the
    integration's configuration (`entry.options` or `entry.data`) or install or rescan anything system-wide.
  - Admin rights are enforced server-side. Hiding a control in the card is not a security control.

## 5. Network egress

- **Every outbound request has a timeout.** Also bound any HA service call made while holding a lock, so a hung
  device or integration cannot hold the lock forever.
- **Every response body read from a device or server is size-capped**:
  - read through the bounded helper `library.read_capped` (every client uses it);
  - art is capped at 5 MB and checked against the image content-type allowlist before the body is read;
  - JSON and text responses are capped at 1 MB.
- **Non-admin users can never choose a destination host.**
  - Hosts and URLs come only from the admin-set options or from fixed hosts in code: `i.ytimg.com`, YouTube
    oEmbed and Lounge, the provider APIs, PyPI.
  - Proxied paths are regex-restricted (section 2).
- **Redirects** are not followed to new hosts for authenticated requests.
- **Use HTTPS when the target supports it.** The admin chooses the scheme for LAN devices; the README recommends
  HTTPS for Plex and Jellyfin.

## 6. Frontend

- **Card output escaping**:
  - every dynamic string interpolated into card HTML goes through `esc()`;
  - URLs go through `safeUrl()` (same-origin only);
  - only constants and clamped numbers may be interpolated unescaped.
- **Toasts** use the `hass-notification` event, never HTML.
- **Image proxy responses** carry `X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'none'`
  and an image type from the `ART_TYPES` allowlist. SVG is never allowed.
- **The TV page**:
  - accepts only same-origin URLs (the single exception is an HTTPS radio stream on the soundtrack layer);
  - fetches with `redirect: "error"` and an abort timeout;
  - holds no secrets.
- **`localStorage`** holds UI preferences only, wrapped in try/catch, and the page must work without it.

## 7. Resource limits

- **Queues, lists and caches are bounded**:
  - the signed-URL `lru_cache`;
  - the card's key queue (10);
  - visuals per mode (50);
  - library rows (20);
  - LLM rounds (5) and `max_tokens`.
- **Retries are bounded in time.** For example, play-library waits at most 15 s and the YouTube start at most 20 s.
- **Background tasks, intervals and listeners are cancelled on unload** (`architecture.md`, "Lifecycle"). That
  includes the YouTube Lounge subscription when the TV client closes.
- **No blocking I/O in the event loop.**

## 8. Media-PC daemons (`media-pc/`)

- **Request checks run in a fixed order**:
  1. IP allowlist (403);
  2. `Host` allowlist (421);
  3. constant-time Bearer token comparison (401);
  4. framing (411 or 400);
  5. JSON content type (415).
- **Tokens and bodies**: tokens are at least 32 printable characters, and request bodies are size-capped.
- **Connection pools** are per class: 16 connections for allowlisted peers, 2 for everyone else.
- **Inputs** are enumerated or regex-checked.
- **Subprocesses**: commands run with absolute paths and no shell, and `systemctl` actions are limited to
  start/stop/is-active.
- **Error responses** carry only short error codes, never stack traces or paths.
- **systemd hardening**: `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=read-only`, `PrivateTmp`, no
  capabilities, `SystemCallFilter=@system-service`, `MemoryMax`, `TasksMax`. The firewall step in
  `media-pc/README.md` is mandatory.

## 9. Accepted risks

| Risk | Why it is accepted |
|---|---|
| The TV WebSocket uses TLS without certificate verification (samsungtvws `CERT_NONE`), and the token is part of the WS URL | Samsung TVs use self-signed certificates that cannot be pinned through the remote API. The token only controls the TV and can be revoked on the TV. The log filter redacts it. |
| Media-PC, keyboard bridge and some Plex/Jellyfin traffic is plain HTTP on the LAN | These are LAN devices without TLS. Each uses its own token, the media PC has a mandatory firewall, and the README recommends HTTPS where it is available. |
| Secrets are stored in plain text in HA's `.storage` | This is how Home Assistant stores config entries; protecting it is the host's job. |
| Signed media URLs for the TV page last 24 h | The TV streams long files with range requests. The URLs are re-minted on every poll, and the page itself is bound to a 2-hour session. |
| `ensure_sdk` installs the chosen LLM SDK from PyPI during setup | This is HA's standard requirements mechanism (`async_process_requirements`), only for the provider an admin picked. |
| Any authenticated user can send any `KEY_*` to the TV | Controlling the TV is the integration's purpose. Keys are regex-checked and cannot reach anything beyond the TV remote. |

## 10. Supply chain and CI

- **Runtime requirements** in `manifest.json` have lower bounds or exact pins. Test requirements are pinned exactly in
  `requirements_test.txt`.
- **GitHub Actions** are pinned to a full commit SHA, with the tag in a comment.
- **CI runs** HACS validation, hassfest, ruff, the Python tests and the card tests on every push.
- **Before every push**, scan the diff for secrets and real LAN addresses.

## 11. Review checklist

For every new input, secret, outbound call, view or frontend string, check that:
- the input has an anchored schema with bounds;
- no secret is logged or sent to the frontend, and secrets travel in headers;
- the outbound call has a timeout and a size cap;
- the host is fixed or admin-set, and paths are allowlisted;
- the view requires auth, and signed URLs use the shortest lifetime that works;
- dynamic HTML goes through `esc()` and URLs through `safeUrl()`;
- tasks and listeners are cancelled on unload;
- an admin-only action is registered as an admin service;
- there is a test for the rejection path, not just the happy path.
