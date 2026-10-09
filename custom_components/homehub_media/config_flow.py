"""Config flow for HomeHub Media (replaced in Task 13)."""
from __future__ import annotations

from homeassistant.config_entries import ConfigFlow

from .const import DOMAIN


class HomeHubConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
