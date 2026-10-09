import logging

from homeassistant.config_entries import ConfigEntryState

from custom_components.homehub_media.const import DOMAIN


async def test_entry_loads(hass, setup_entry):
    assert setup_entry.state is ConfigEntryState.LOADED


async def test_tv_library_secrets_not_logged(hass, setup_entry, caplog):
    log = logging.getLogger("samsungtvws.async_connection")
    with caplog.at_level(logging.DEBUG, "samsungtvws"):
        log.debug("WS url %s", "wss://192.0.2.1:8002/api/v2/channels/samsung.remote.control?token=123")
        log.debug("SamsungTVWS websocket command: %s", '{"TypeOfRemote": "SendInputString"}')
        log.info("command %s", '{"metaTag": "http://192.0.2.9:8123/x?s=signed"}')
        log.debug("Connection closed.")
    assert caplog.messages == ["Connection closed."]


async def test_remove_entry_deletes_stored_compositions(hass, setup_entry, hass_storage):
    await hass.services.async_call(DOMAIN, "set_favorite", {"kind": "visual", "media_id": "rain", "favorite": True},
                                   blocking=True)
    await hass.async_block_till_done()
    assert f"{DOMAIN}.data" in hass_storage
    assert await hass.config_entries.async_remove(setup_entry.entry_id)
    assert f"{DOMAIN}.data" not in hass_storage
