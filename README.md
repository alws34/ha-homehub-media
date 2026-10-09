# HomeHub Media

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Latest release](https://img.shields.io/github/v/release/alws34/ha-homehub-media)](https://github.com/alws34/ha-homehub-media/releases)
[![Validate](https://github.com/alws34/ha-homehub-media/actions/workflows/validate.yml/badge.svg)](https://github.com/alws34/ha-homehub-media/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/github/license/alws34/ha-homehub-media)](LICENSE)

Control a Samsung TV, run ambience (video, soundtrack and lights), and browse Plex and Jellyfin from Home Assistant, with one Lovelace card.

| Remote | Ambience | Library | Drawer |
|---|---|---|---|
| ![Remote tab, dark theme](docs/screenshots/remote-dark.webp) | ![Ambience editor, light theme](docs/screenshots/ambience-editor-light.webp) | ![Library tab, dark theme](docs/screenshots/library-dark.webp) | ![Drawer mode, dark theme](docs/screenshots/drawer-dark.webp) |

## Features

- **Samsung TV control** over your local network. It does not need the core Samsung TV integration.
  - Power, D-pad, OK, back, home, volume, mute, play, pause, seek, and raw Samsung keys.
  - Launch or restart Netflix, YouTube, Prime Video, Disney+, Apple TV, Max, Plex, Jellyfin, Spotify, Twitch, Paramount+, and the TV's Browser.
  - Type into the TV's on-screen keyboard, and use a touchpad pointer in the TV browser.
- **Ambience**: a looping video (visual) and a separate soundtrack per mode, from local files or internet radio.
  - A mode can switch on a lighting scene and flicker chosen lights like a fire.
  - Change the visual or soundtrack while a mode runs, mark favourites, and mix the two volumes.
  - Sleep timer that fades out, and time-of-day auto ambience.
- **Plex and Jellyfin**: Continue Watching and Recently Added rows, plus a Now Playing strip with play, pause, and 30-second skip.
- **Activities**: Off, Samsung TV, Streaming box, or Media PC. Switching activity sets the TV input (where configured) and volume, and stops ambience that uses the TV.
- **Optional devices**: a streaming box on a TV input, an Ubuntu media PC for ambience and browser apps, and an ESP32 Bluetooth keyboard bridge.
- **Voice assistant**: a conversation agent that uses Ollama, OpenAI, or Claude. You pick the provider and model. HomeHub's tools are also available to Home Assistant's own Ollama, OpenAI, and Anthropic agents.
- **Blueprints** for stopping ambience when the TV changes, quiet hours, and sunset ambience.
- **One card** (`homehub-media-card`) with no third-party card dependencies. It follows the light or dark theme.

## Requirements

- Home Assistant 2026.9.0 or newer.
- [HACS](https://hacs.xyz/).
- A Samsung TV with Tizen OS on the same network as Home Assistant, reachable by IP address or host name.

Optional: Plex, Jellyfin, a streaming box, a media PC, an ESP32 keyboard bridge, and an OpenAI or Claude API key (or a local Ollama server) for the voice assistant.

## Installation

1. In HACS, open the **⋮** menu (top right) and choose **Custom repositories**.
2. Enter `https://github.com/alws34/ha-homehub-media`, set **Category** to **Integration**, and select **Add**.
3. Find **HomeHub Media**, select **Download**, and restart Home Assistant.
4. Go to **Settings › Devices & services › Add integration** and choose **HomeHub Media**.
5. Enter the TV's host name or IP address. The field is filled in if the Samsung Smart TV integration is set up. Select **Submit**, then press **Allow** on the TV within 30 seconds.

Or use these buttons from the Home Assistant instance you want to use:

[![Open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=alws34&repository=ha-homehub-media&category=integration)
[![Set up HomeHub Media](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=homehub_media)

Notes:

- Only one HomeHub Media entry can be set up.
- HomeHub pairs its own client, named **HomeHub**, with the TV. It does not change the Samsung TV integration's connection.
- The TV's MAC address is optional. With it, HomeHub can turn the TV on with Wake-on-LAN. If you leave it empty, HomeHub reads it from the TV.

## Configuration

Open **Settings › Devices & services › HomeHub Media › Configure**. Every section is optional. Pick a section, make your changes, and go back to the menu. Choose **Save and close** to apply all changes.

| Menu item | What it configures |
|---|---|
| **TV** | Optional TV media player (for switching inputs by source name and reading volume), an app entity for the current app, and the Home Assistant URL the TV uses to show ambience. |
| **Streaming apps** | Which apps appear on the remote, in what order, optional TV app ID overrides, and a rescan of installed apps. |
| **Plex and Jellyfin** | Plex URL and token, Jellyfin URL and API key, and the Jellyfin user. Enables Continue Watching, Recently Added, and Now Playing. |
| **Streaming box** | The remote entity that controls the box, an optional media player for channels, and the TV input the box uses. |
| **Media PC** | Host, ambient player and coordinator ports and tokens, the TV input the PC uses, and the apps the PC may open. See [media-pc/README.md](media-pc/README.md). |
| **Keyboard bridge** | Host and shared key of the ESP32 keyboard bridge. |
| **Ambience** | Where ambience plays (Samsung TV or Media PC), and internet radio stations for soundtracks. |
| **Ambience modes** | Add, edit, or delete modes: name, icon, lighting scene, flickering lights, and a lights-only switch. Seven starter modes are included: Fireplace, Dinner, Reading, Game Night, Chill, Movie Time (lights only), and DOG TV. |
| **Volume** | Normal volume (default 19) and quiet volume (default 10), plus an optional entity that blocks volume changes while it is on. |
| **Times of day** | Start time and ambience mode for morning, afternoon, evening, and night (defaults 06:00, 12:00, 18:00, 22:00), and the idle time before auto ambience starts (default 10 minutes). |
| **Voice assistant** | Provider (Ollama, OpenAI, or Claude), URL or API key, and model. |

Notes on secrets and addresses:

- Tokens and keys are never shown again after you save them.
- Leave a secret field empty to keep the saved value, but only while the address, port, or provider stays the same. If you change the address, enter the secret again.
- Plex needs a token (`X-Plex-Token`). Jellyfin needs an API key. The media PC needs both of its tokens.
- Leaving a device's address empty turns that device off.
- Switching inputs by source name needs the TV media player from the **TV** section. Switching by remote key does not.

### Entities

| Entity | What it is |
|---|---|
| `select.homehub_media_ambience_mode` | Off, or one of your modes. Selecting one starts it. |
| `select.homehub_media_activity` | Off, Samsung TV, Streaming box, or Media PC. |
| `switch.homehub_media_quiet_mode` | Turns quiet volume on or off. |
| `switch.homehub_media_auto_ambience` | When on, starts the time-of-day mode after the idle time, once the TV has been on and idle (no HomeHub command, no ambience, no Plex or Jellyfin session). |
| `sensor.homehub_media_ambience_sleep_timer` | The time the sleep timer stops ambience. |
| `sensor.homehub_media_now_playing` | The title of the current Plex or Jellyfin session. Attributes hold the server, progress, and image. |
| `conversation.homehub_media_assistant` | The voice assistant. Appears after you set a provider. |

## Ambience videos and soundtracks

HomeHub reads visuals and soundtracks from two folders and creates them automatically:

```text
/media/homehub/ambience/
├── videos/                    visuals, shown on the TV
│   ├── fireplace.mp4
│   ├── fireplace.webp         optional preview image
│   └── nature/
│       └── rain.mkv           sub-folders are scanned
└── soundtracks/               audio, played alongside a visual or on its own
    └── jazz_cafe.mp3
```

**Where the folder is**

- **Home Assistant OS**: the `media` share from the Samba share add-on (for example `\\homeassistant\media` on Windows or `smb://homeassistant/media` on macOS). You can also upload files with **Settings › Media › My media**. The folders appear there once HomeHub has created them.
- **Other installs**: the `homehub/ambience` folder inside your local media folder. If you have not set one, that is the `media` folder in your config directory.

**Supported files**

| Kind | Folder | Extensions |
|---|---|---|
| Visuals | `videos` | `.mp4`, `.mkv`, `.webm`, `.mov` |
| Soundtracks | `soundtracks` | `.mp3`, `.ogg`, `.opus`, `.flac`, `.m4a`, `.wav` |

**Recommended video format**: H.264 video, AAC audio, MP4 container, up to 1080p, with `+faststart` so playback starts quickly. A file the TV cannot decode shows a black picture. To convert a file:

```bash
ffmpeg -i in.mkv -c:v libx264 -preset slow -crf 20 -c:a aac -b:a 160k -movflags +faststart out.mp4
```

To cap the output at 1080p, add `-vf "scale=-2:'min(1080,ih)'"` before `-c:v`.

**Preview images**: put an image with the same file name next to the video, for example `fireplace.webp`. Accepted types are `.webp`, `.jpg`, `.jpeg`, and `.png`. If more than one exists, `.webp` is used first. Previews appear in the card's carousel.

**Names and IDs**

- The file name sets the ID and the title. The ID is the file name in lowercase, with anything other than letters, digits, and underscores replaced by `_`, up to 64 characters. For example, `Rain Forest.mp4` gets the ID `rain_forest` and the title "Rain Forest".
- If two files produce the same ID, the second gets `_2`, and so on.
- IDs are assigned in sorted file-name order. Adding a file whose name produces the same ID as an existing file can change which file a saved mode uses. Check your modes after you add files.
- Sub-folders are scanned, but only the file name is used for the ID.
- Files and folders whose names start with a dot are ignored, including the `._` files macOS creates.
- After you add files, press the refresh button in the Ambience tab, or call `homehub_media.refresh_catalog`.

**Radio stations** (Samsung TV backend): in **Ambience › Radio stations**, enter one station per line as `id=https://url`, for example `jazz=https://example.com/stream`. Use HTTPS only, and do not put a user name or password in the URL. Station IDs use lowercase letters, digits, `-`, and `_`. Stations appear in the soundtrack list with a live label.

**Assign visuals and soundtracks to a mode**

1. Open the **Ambience** tab in the card.
2. Select the gear button on the mode's tile.
3. Choose a visual and a soundtrack with the arrows or by swiping. Set the loop and random-start options and the mix.
4. Select **Save**.

If the mode is running, changes apply right away.

**Media PC users**: if ambience plays on the media PC, the videos and soundtracks live on that PC. Follow [media-pc/README.md](media-pc/README.md).

## Card

The card registers itself, so you do not add a Lovelace resource. On any dashboard, add a **Manual** card and paste one of the examples below.

Card options:

| Key | Values | Default | Meaning |
|---|---|---|---|
| `drawer` | `true` or `false` | `false` | Slide-out panel fixed to the right edge of the screen, with a handle. Press Esc to close it. |
| `tabs` | any of `remote`, `ambience`, `library` | all three | Tabs to show. `library` appears only when Plex or Jellyfin is set up. |
| `default_tab` | `remote`, `ambience`, `library` | `remote` | The tab that opens first. If it is not in `tabs`, the first tab in `tabs` opens. |
| `target` | `tv`, `box`, `pc` | the current activity, or `tv` | The target selected in the remote. Falls back to the TV if the chosen target is not set up. |

An invalid value shows a configuration error on the card.

Inline card:

```yaml
type: custom:homehub-media-card
```

Drawer:

```yaml
type: custom:homehub-media-card
drawer: true
```

Show only the Ambience and Remote tabs, in that order, open on Ambience, and select the media PC as the target:

```yaml
type: custom:homehub-media-card
tabs: [ambience, remote]
default_tab: ambience
target: pc
```

## Services

All services are in the `homehub_media` domain.

| Service | What it does |
|---|---|
| `remote` | Presses a remote button (`command`, for example `up` or `play_pause`) on the TV, streaming box, or media PC. `target` is optional. Volume always goes to the TV. |
| `send_key` | Sends a raw Samsung key such as `KEY_HDMI1`, with an optional `hold_secs` from 0 to 5. |
| `launch_app` | Opens an app by name (for example `netflix`) or by TV app ID. |
| `restart_app` | Closes and reopens an app. |
| `send_text` | Types up to 200 printable characters. `target` is `tv`, `pc`, or `keyboard`, and is optional. |
| `keyboard_key` | Presses `backspace`, `enter`, or `clear` on the active keyboard. |
| `pointer` | `action` is `move`, `click`, `right_click`, or `scroll`. `dx` and `dy` (-500 to 500) set the move distance. Scroll and right-click work on the media PC only. |
| `set_activity` | Switches to `off`, `tv`, `box`, or `pc`. `force` switches inputs again even if the activity is already active. |
| `channel` | Tunes the streaming box to a channel number of up to 4 digits. |
| `ambience_start` | Starts a mode by ID, for example `fireplace`. |
| `ambience_stop` | Stops ambience and restores the lights. |
| `ambience_update` | Changes the visual, soundtrack, loop, random-start, or volume settings of the running ambience. |
| `save_mode` | Saves the visual and soundtrack settings as a mode's default. |
| `set_favorite` | Marks or unmarks a visual or soundtrack as a favourite. |
| `ambience_auto` | Starts the mode for the current time of day. |
| `ambience_sleep_timer` | Stops ambience after `minutes` (0 to 600). 0 cancels the timer. |
| `refresh_catalog` | Rescans the ambience folders for new visuals and soundtracks. |
| `rescan_apps` | Asks the TV which streaming apps are installed. |
| `play_library_item` | Opens Plex or Jellyfin on the TV and plays an item, given `server` and `item_id`. |
| `pc_open` | Opens an app on the media PC by its name in the Media PC options. |
| `pc_close` | Closes the app open on the media PC. |

Example automation that starts Dinner at 19:00 and stops it after 90 minutes:

```yaml
alias: Dinner ambience
triggers:
  - trigger: time
    at: "19:00:00"
actions:
  - action: homehub_media.set_activity
    data:
      activity: tv
  - action: homehub_media.ambience_start
    data:
      mode: dinner
  - action: homehub_media.ambience_sleep_timer
    data:
      minutes: 90
mode: single
```

The sleep timer fades the volume to zero during the last minute before the end time, and stops ambience at that time. The end time is shown in `sensor.homehub_media_ambience_sleep_timer`.

## Blueprints

Import a blueprint with the button, then create an automation from it.

| Blueprint | What it does |
|---|---|
| **HomeHub Media - stop ambience when the TV moves on** | Stops ambience and restores the lights when the entity you choose changes, for example the TV's source, current app, or play state. Changes in the first 30 seconds after ambience starts are ignored. |
| **HomeHub Media - quiet mode on a schedule** | Turns quiet mode on at one time and off at another. |
| **HomeHub Media - ambience at sunset** | At sunset (with an optional offset), if the TV is off, starts the mode for the current time of day. |

[![Import stop_ambience_on_app](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Falws34%2Fha-homehub-media%2Fmain%2Fblueprints%2Fautomation%2Fhomehub_media%2Fstop_ambience_on_app.yaml)
[![Import quiet_mode_schedule](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Falws34%2Fha-homehub-media%2Fmain%2Fblueprints%2Fautomation%2Fhomehub_media%2Fquiet_mode_schedule.yaml)
[![Import sunset_ambience](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Falws34%2Fha-homehub-media%2Fmain%2Fblueprints%2Fautomation%2Fhomehub_media%2Fsunset_ambience.yaml)

## Voice assistant

You can use HomeHub's own assistant or your existing Ollama, OpenAI, or Anthropic agent.

**Option 1: HomeHub's assistant**

1. Open **Configure › Voice assistant** and choose a provider.
   - **Ollama** (local): enter the Ollama URL. No key.
   - **OpenAI** or **Claude**: enter an API key. Your requests go to that service.
2. Choose a model from the list. You can also type a model name.
3. Submit. The provider's software is installed on demand, which can take a minute. Ollama uses the OpenAI client software.
4. Go to **Settings › Voice assistants** and select **HomeHub Media Assistant** as the conversation agent.

**Option 2: your existing agent**

Open the options of your Ollama, OpenAI, or Anthropic conversation agent. Under **Control Home Assistant**, turn on the **HomeHub Media** API. The agent can then use HomeHub's tools.

The tools can open apps, press remote buttons, switch activity, start and stop ambience, set the sleep timer, turn quiet mode on and off, play a Plex or Jellyfin title by name, and report what is playing. They control only HomeHub.

## TV limitations

These come from the TV, not from HomeHub.

- **Pointer**: the touchpad works only where Samsung enables pointer input, such as the built-in Browser and some apps. On the TV, scroll and right-click are not available.
- **Typing**: text goes to a text field that has focus on the TV. Focus a search box on the TV first. Without a focused field, HomeHub types through the ESP32 keyboard bridge if you set one up.
- **Deep links**: opening a specific Netflix or Disney+ title is not reliable and is not supported. Use the Plex or Jellyfin library to play a title.
- **App IDs**: the TV cannot list its apps on 2020 and newer models, so HomeHub tries a list of known IDs for each app. IDs vary by region and year. Some built-in IDs were checked on one Samsung QE55Q60B, and others may differ on your TV. If an app is missing or opens the wrong app, use **Streaming apps** to rescan or set the ID.
- **Jellyfin**: if the Jellyfin app is not installed on the TV, HomeHub opens the Jellyfin web page in the TV browser instead.
- **Play and pause**: HomeHub sends play and pause as a toggle and tracks the last key it sent, not the real player state. If playback changes without HomeHub, for example with the TV remote, the next press can do the wrong thing.
- **Autoplay**: when TV ambience starts, HomeHub presses OK on the TV about 4 seconds later, so the browser allows sound. If the sound stays muted, press OK on the TV remote.

## Media PC

The media PC is optional. HomeHub Media does not install the Ubuntu side. Set it up with [media-pc/README.md](media-pc/README.md), which covers the user services, tokens, and firewall.

## Security

- Every service and WebSocket command goes through Home Assistant's authentication. Only signed-in users and authorised automations can call them.
- The TV ambience page and its media files use Home Assistant signed URLs that expire after 24 hours.
- Plex and Jellyfin tokens stay in Home Assistant. Artwork goes through an authenticated Home Assistant proxy, so the tokens never reach the browser or the TV.
- The media PC daemons accept a request only if it comes from an allowed IP address, names a configured host, carries the bearer token, and sends a JSON body. The coordinator requires a token of at least 32 printable characters with no spaces.
- Tip: on the media PC firewall, allow ports 8097 and 8098 only from the Home Assistant host.
- The ESP32 keyboard bridge key is sent over plain HTTP on your local network. The media PC requests (including the bearer tokens) also use plain HTTP. Anyone who can capture traffic on your LAN can read these values. Keep the network trusted, or put these devices on a separate network segment that only Home Assistant can reach.
- Keys and tokens are stored in Home Assistant's configuration. Anyone with access to the Home Assistant config folder can read them.

## Troubleshooting

**The TV does not show a pairing prompt.** On the TV, go to **Settings › General › External Device Manager › Device Connection Manager › Access Notification** and select **First time only**. Then open the device list in the same menu, remove any old **HomeHub** entries, and run the setup again. Menu names can vary by model.

**An app tile is missing, or it opens the wrong app.** Go to **Configure › Streaming apps**, tick **Rescan TV apps**, and submit. Then submit again with the box unticked to save the result. You can also call `homehub_media.rescan_apps`. To set an ID by hand, use the App IDs field.

**The ambience screen is black.** Check that the video is H.264 video in an MP4 container, as described above. Check the TV's auto power-off setting, because a TV that turns itself off ends ambience. Check that the **Home Assistant URL for the TV** in **Configure › TV** is reachable from the TV.

**Library rows are empty.** Check the Plex or Jellyfin URL and token or API key in **Configure › Plex and Jellyfin**.

**Enable debug logging.** Add this to `configuration.yaml`, restart or reload the logger, then check **Settings › System › Logs**:

```yaml
logger:
  default: warning
  logs:
    custom_components.homehub_media: debug
```

## Credits

HomeHub Media is based on the original HomeHub Media Controller & Ambience bundle by the original HomeHub author.

Released under the [MIT License](LICENSE).
