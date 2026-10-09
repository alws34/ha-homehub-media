# Bundle Manifest

## Home Assistant

- `home-assistant/dashboard/media-view.yaml` — Media UI with ambience and controller drawer, made independent of the old weather wrapper.
- `home-assistant/dashboard/decluttering-templates.yaml` — Placeholder-card template used by the media dashboard.
- `home-assistant/dashboard/theme-snippet.yaml` — Optional styling variables.
- `home-assistant/packages/homehub_ambience_ui.yaml` — Ambience catalog, selectors, profiles, favorites, and UI state.
- `home-assistant/packages/homehub_ambient_player.yaml` — HTTP bridge to the ambience player on port 8097.
- `home-assistant/packages/homehub_media_controller.yaml` — Media-controller logic, including the completed Jellyfin/Sonarr/Radarr launcher scripts.
- `home-assistant/packages/homehub_media_hub_pc_keyboard.yaml` — D-pad, keyboard and pointer REST commands on port 8098.
- `home-assistant/www/homehub-media-ui/` — The five project-owned custom-card modules.

## Ubuntu

- `ubuntu/ambient-player/ambient_player.py` — Dual MPV video/audio ambience backend, with portable IP validation.
- `ubuntu/ambient-player/audio_only.ppm` — Fullscreen safety image for soundtrack-only playback.
- `ubuntu/ambient-player/config.example.json` — Safe ambience configuration template.
- `ubuntu/media-hub-coordinator/media_hub_coordinator.py` — Allowlisted browser, keyboard, D-pad and pointer controller.
- `ubuntu/media-hub-coordinator/coordinator.example.json` — Safe coordinator configuration template.
- `ubuntu/systemd-user/` — User-service templates for the ambience backend, coordinator, and browser clients.
