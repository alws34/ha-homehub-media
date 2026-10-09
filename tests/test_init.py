from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homehub_media.const import DOMAIN


async def test_entry_loads(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "192.0.2.1", "token": "t", "mac": None})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
