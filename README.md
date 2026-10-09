# Home Hub Media Controller and Ambience Bundle

This is a privacy-safe sharing bundle for the Home Assistant media drawer, its ambience controller, and the Ubuntu backend that powers the D-pad, mouse pad, and text input.

It includes the custom visual controls, Home Assistant packages, a standalone media-dashboard card, the two Python backend services, configuration examples, and installation handoff. It does **not** include personal media, network addresses, API keys, device IDs, account data, or saved ambience state.

## What is included

| Area | Included files |
| --- | --- |
| Home Assistant UI | `home-assistant/dashboard/media-view.yaml`, five custom cards under `home-assistant/www/homehub-media-ui/` |
| Home Assistant logic | Four packages under `home-assistant/packages/` |
| Ambience backend | `ubuntu/ambient-player/` |
| D-pad, mouse-pad and text backend | `ubuntu/media-hub-coordinator/` |
| Ubuntu user services | `ubuntu/systemd-user/` |
| Setup guide | `HANDOFF.md` |

## Start here

Read [HANDOFF.md](HANDOFF.md). Replace every value whose name begins with `YOUR_` before enabling either Ubuntu service or reloading Home Assistant.

## What to install through HACS

Install these separately in HACS, then add their frontend resources if HACS does not do so automatically:

- Button Card
- Decluttering Card
- card-mod
- Mushroom
- my-slider-v2

The dashboard also uses Home Assistant's built-in `media-player-volume-slider` tile feature.

## Important boundaries

- The ambience player uses folders of local video and soundtrack files. Put your own files in the paths set in `ubuntu/ambient-player/config.example.json`.
- The preview cards look for optional image previews under `/local/Ambience`. Add your own `media-id.webp` or `media-id.png` files there if desired.
- The browser client templates are intentionally generic because every Ubuntu desktop may use a different browser executable, local ports, and display layout.
- Samsung-TV and ESP32 keyboard sections in `homehub_media_controller.yaml` are optional. Their hosts, entities, and shared key are placeholders.
