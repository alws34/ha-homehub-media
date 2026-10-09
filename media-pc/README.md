# HomeHub media PC (Ubuntu side)

Optional. Two small stdlib-only Python daemons for an Ubuntu GNOME PC wired to the TV over HDMI. The HomeHub Media
integration in Home Assistant talks to them; HACS does not install anything here.

- `ambient-player/ambient_player.py` (port 8097): ambience catalog + two mpv layers (fullscreen video, soundtrack).
- `media-hub-coordinator/media_hub_coordinator.py` (port 8098): opens Jellyfin/Sonarr/Radarr/... in a kiosk browser
  user service and sends constrained D-pad, keyboard, mouse and scroll input through GNOME Mutter.

Every request must come from an `allowed_clients` IP, carry `Host: <bind host>:<port>` and
`Authorization: Bearer <auth_token>`, and send JSON bodies as `application/json`.

## 1. Prerequisites

The coordinator uses the GNOME/Mutter D-Bus remote-input API, so the user must be logged into the graphical session
and the browser must run in that same session.

```bash
sudo apt update
sudo apt install mpv python3-gi gir1.2-glib-2.0
```

Create the media directories, or choose different paths and update both the ambience JSON and the `ReadOnlyPaths=`
lines in `systemd-user/ambient-player.service`:

```bash
sudo install -d -o "$USER" -g "$USER" \
  /srv/homehub/jellyfin-media/library/Ambience/Videos \
  /srv/homehub/jellyfin-media/library/Ambience/Soundtracks
```

Point Jellyfin's library at the media root if the same ambience videos/music should appear in Jellyfin.

## 2. Generate a token

```bash
python3 -c 'import secrets;print(secrets.token_urlsafe(32))'
```

Put it in both JSON configs (`auth_token`, at least 32 printable ASCII characters without spaces; the daemons
refuse to start otherwise) and in
Home Assistant → Settings → Devices & services → HomeHub Media → Configure → Media PC. Treat it like a password.

## 3. Install the ambience player

```bash
sudo install -d /opt/ambient-player /etc/ambient-player
sudo install -m 0755 media-pc/ambient-player/ambient_player.py /opt/ambient-player/ambient_player.py
sudo install -m 0600 -o "$USER" media-pc/ambient-player/config.example.json /etc/ambient-player/config.json
mkdir -p ~/.config/systemd/user ~/.local/state/ambient-player
install -m 0644 media-pc/systemd-user/ambient-player.service ~/.config/systemd/user/ambient-player.service
```

Edit `/etc/ambient-player/config.json` and replace every `YOUR_` value: `bind_host` is the Ubuntu PC's LAN IP,
`allowed_clients` the Home Assistant server's LAN IP (never a loopback address: any local process could then use
the API), `auth_token` the token from step 2. Optional keys:
`port` (default 8097), `allowed_hosts` (Host header values to accept; default `<bind_host>` and
`<bind_host>:<port>`, set it if Home Assistant reaches the PC by a host name), `video_aliases`,
`soundtrack_aliases`, and `soundtrack_streams` (credential-free HTTPS radio URLs). Files that cannot be read or probed
are skipped and logged; an empty catalog is fine.

Validate and start it from the Ubuntu graphical user session:

```bash
python3 /opt/ambient-player/ambient_player.py --config /etc/ambient-player/config.json --check
systemctl --user daemon-reload
systemctl --user enable --now ambient-player.service
```

## 4. Install the coordinator

```bash
sudo install -d /opt/homehub-media-hub /etc/homehub-media-hub
sudo install -m 0755 media-pc/media-hub-coordinator/media_hub_coordinator.py /opt/homehub-media-hub/media_hub_coordinator.py
sudo install -m 0600 -o "$USER" media-pc/media-hub-coordinator/coordinator.example.json /etc/homehub-media-hub/coordinator.json
install -m 0644 media-pc/systemd-user/homehub-media-hub-coordinator.service ~/.config/systemd/user/homehub-media-hub-coordinator.service
```

Edit `/etc/homehub-media-hub/coordinator.json`:

- `listen_host` / `allowed_clients`: the Ubuntu and Home Assistant IP addresses. Do not allowlist `127.0.0.1` or
  any other loopback address (the kiosk browser and every local process could then inject keystrokes).
- `auth_token`: the token from step 2 (`allowed_hosts` works as for the ambience player).
- `clients`: any set of `name: {unit, home_url, tab_navigation}` (names are lowercase slugs, e.g. `jellyfin`,
  `plex`); `home_url` must be a loopback `http://` URL, `unit` the user service that launches it. Set
  `"tab_navigation": true` for web apps that use browser focus order instead of TV-style arrows (Sonarr, Radarr):
  left/right then send Shift+Tab/Tab. A bare `POST /v1/open` opens the first configured client.

Create one browser service per client by copying `systemd-user/browser-client.service.template`: replace the app
name, browser executable and local URL, and save it under the unit name used in `coordinator.json`, for example
`~/.config/systemd/user/jellyfin-browser.service`.

```bash
systemctl --user daemon-reload
systemctl --user enable --now homehub-media-hub-coordinator.service
```

## 5. Check

From the Home Assistant machine (an allowed client):

```bash
curl -H "Authorization: Bearer $TOKEN" http://YOUR_UBUNTU_HOST_IP:8097/v2/status
curl -H "Authorization: Bearer $TOKEN" http://YOUR_UBUNTU_HOST_IP:8098/v1/status
```

Errors: 403 IP not allowed, 421 Host not allowed, 401 bad or missing token, 411 chunked body (send
`Content-Length`), 400 invalid `Content-Length`, 415 body not `application/json`, 409 `{"error": code}`.
Details are only in `journalctl --user -u ambient-player -u homehub-media-hub-coordinator`. Hosts outside
`allowed_clients` share 2 connections (enough for their 403); allowlisted hosts get 16 of their own.

## Firewall

Recommended: let only Home Assistant reach the two ports. The per-IP connection pools stop slow-request floods from
other LAN hosts, but not a SYN/accept-backlog flood, which only a firewall can drop.

```bash
sudo ufw default deny incoming
sudo ufw allow OpenSSH        # only if you manage this PC over SSH; do this before enabling
sudo ufw allow from YOUR_HOME_ASSISTANT_IP to any port 8097,8098 proto tcp
sudo ufw enable
```

## Upgrading from the YAML bundle

- `auth_token` is now required in both configs.
- Ambience profiles, favourites and legacy `/v1` scenes moved to Home Assistant; `legacy_scenes`,
  `default_profiles` and `audio_only_image` are ignored and can be deleted. The state file is migrated automatically.
- The coordinator ignores `ambient_stop_url` / `ambient_stop_timeout_seconds` (Home Assistant stops ambience before
  opening a browser client), and `stop` now sends Escape instead of Alt+Left. Add `"tab_navigation": true` to the
  `sonarr` and `radarr` clients to keep their Tab-based left/right navigation.
- Remove `127.0.0.1` from both `allowed_clients` lists.
- Re-copy both `.service` files (new sandboxing; the ambience unit needs `ReadWritePaths=%t` for mpv's sockets).
