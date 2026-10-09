from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homehub_media.const import DOMAIN

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
async def setup_entry(hass, tmp_path):
    """Loaded entry with a mocked TV (hub.SamsungTV stays patched for the test); media dir = tmp_path."""
    hass.config.media_dirs = {"local": str(tmp_path)}
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "192.0.2.1", "token": "t", "mac": None},
                            options={"ambience": {"backend": "tv", "radio": {}}})
    entry.add_to_hass(hass)
    with patch("custom_components.homehub_media.hub.SamsungTV", autospec=True) as tv_cls:
        tv = tv_cls.return_value
        tv.token, tv.available, tv.keyboard_active, tv.pointer_active, tv.mac = "t", True, False, False, None
        tv.discover_apps.return_value = {"youtube": "111299001912"}
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        yield entry
        if entry.state is ConfigEntryState.LOADED:
            assert await hass.config_entries.async_unload(entry.entry_id)
