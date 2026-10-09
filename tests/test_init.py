from homeassistant.config_entries import ConfigEntryState


async def test_entry_loads(hass, setup_entry):
    assert setup_entry.state is ConfigEntryState.LOADED
