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
        try:
            raise OSError("server rejected WebSocket connection: wss://192.0.2.1:8002/x?token=123")
        except OSError:
            log.exception("Connect failed")  # the token is only in the traceback: the record is dropped
        log.debug("Connection closed.")
    assert caplog.messages == ["Connection closed."]


async def test_websockets_frames_and_url_with_the_tv_token_not_logged(hass, setup_entry, caplog):
    log = logging.getLogger("websockets.client")  # what samsungtvws' connection logs through, at debug
    with caplog.at_level(logging.DEBUG, "websockets"):
        log.debug("> GET %s HTTP/1.1", "/api/v2/channels/samsung.remote.control?name=SG9tZUh1Yg==&token=123")
        # a typed-text frame, cut at 75 characters before "SendInputString": the base64 text would still show
        log.debug("> %s", """TEXT '{"method": "ms.remote.control", "params": {"Cmd": "aHVudGVyMg==", "Da...' [150 bytes]""")
        log.debug("< %s", """TEXT '{"data": {"clients": [], "id": "x", "token": "123"}, "event": "ms.chann...' [90 bytes]""")
        log.debug("= connection is %s", "OPEN")
    assert caplog.messages == ["= connection is OPEN"]


async def test_youtube_lounge_secrets_not_logged(hass, setup_entry, caplog):
    log = logging.getLogger("pyytlounge")  # the logger tv.py hands to YtLoungeApi
    with caplog.at_level(logging.DEBUG, "pyytlounge"):
        log.info("Refreshed auth, lounge id token %s", "SECRET1")
        log.info("Subscribing to lounge id %s", "SECRET2")
        log.debug("Unprocessed event %s %s", "loungeStatus", [{"screenId": "SECRET3"}])
        try:
            raise ValueError("400, url='https://www.youtube.com/api/lounge/bc/bind?loungeIdToken=SECRET4'")
        except ValueError:
            log.exception("Command failed")  # the secret is only in the traceback: kept, without it
        log.info("Subscribe completed, status %i %s", 200, "OK")
    assert caplog.messages == ["Command failed", "Subscribe completed, status 200 OK"]
    assert "SECRET" not in caplog.text and all(r.exc_info is None for r in caplog.records)


async def test_remove_entry_deletes_stored_compositions(hass, setup_entry, hass_storage):
    await hass.services.async_call(DOMAIN, "set_favorite", {"kind": "visual", "media_id": "rain", "favorite": True},
                                   blocking=True)
    await hass.async_block_till_done()
    assert f"{DOMAIN}.data" in hass_storage
    assert await hass.config_entries.async_remove(setup_entry.entry_id)
    assert f"{DOMAIN}.data" not in hass_storage
