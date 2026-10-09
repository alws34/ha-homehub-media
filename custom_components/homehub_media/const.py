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
