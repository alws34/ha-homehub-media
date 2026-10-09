"""Contract test: the integration's media-PC clients against the real daemon handlers over loopback HTTP.

Only hardware is seamed out (mpv, HDMI/D-Bus/PipeWire, systemctl, Mutter input), as in media-pc/tests, so every
request passes the daemons' real IP/Host/token/framing guards and their real body validation.
"""
import subprocess
import sys
import threading
from pathlib import Path
from unittest import mock

import aiohttp
import pytest

from custom_components.homehub_media.pc import AmbientPlayerClient, CoordinatorClient, PCError

MEDIA_PC = Path(__file__).parents[1] / "media-pc"
sys.path.insert(0, str(MEDIA_PC / "ambient-player"))
sys.path.insert(0, str(MEDIA_PC / "media-hub-coordinator"))
import ambient_player as ap
import media_hub_coordinator as co

TOKEN = "t" * 40
HARDWARE = ("_wait_for_hdmi", "_wake_display", "_configure_hdmi_audio", "_start_layer", "_ipc")


def systemctl(active: set):
    """`systemctl --user` stand-in through the Coordinator runner seam."""
    def run(argv, **_kwargs):
        action, unit = argv[2:4]
        if action == "start":
            active.add(unit)
        elif action == "stop":
            active.discard(unit)
        state = ("active" if unit in active else "inactive") if action == "is-active" else ""
        return subprocess.CompletedProcess(argv, 0, stdout=state)
    return run


@pytest.fixture
def daemons(socket_enabled, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    for folder, name in (("videos", "rain.mp4"), ("soundtracks", "jazz.mp3")):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / name).write_bytes(b"media")
    with (
        mock.patch.object(ap.Catalog, "_probe", return_value=(60.0, True)),
        mock.patch.multiple(ap.DualAmbientPlayer, **dict.fromkeys(HARDWARE, mock.DEFAULT)),
        mock.patch.object(co.MutterRemoteInput, "_with_session") as mutter,
    ):
        player = ap.make_server(ap.config_from_dict({
            "bind_host": "127.0.0.1", "port": 0, "allowed_clients": ["127.0.0.1"], "auth_token": TOKEN,
            "video_root": str(tmp_path / "videos"), "soundtrack_root": str(tmp_path / "soundtracks"),
            "state_file": str(tmp_path / "state.json"),
        }))
        cfg = co.Config.from_dict({
            "listen_host": "127.0.0.1", "listen_port": 0, "allowed_clients": ["127.0.0.1"], "auth_token": TOKEN,
            "clients": {"jellyfin": {"unit": "jellyfin-browser.service", "home_url": "http://127.0.0.1:8096/web/"}},
        })
        coordinator = co.make_server(cfg)
        coordinator.coordinator = co.Coordinator(cfg, runner=systemctl(set()))
        servers = (player, coordinator)
        threads = []
        try:
            for srv in servers:
                srv.daemon_threads = False  # server_close() joins every handler thread (HA's verify_cleanup)
                threads.append(threading.Thread(target=srv.serve_forever))
                threads[-1].start()
            yield player.server_port, coordinator.server_port, mutter
        finally:
            for srv, thread in zip(servers, threads):
                srv.shutdown()
                thread.join()
            for srv in servers:
                srv.server_close()


async def test_clients_and_daemons_agree(hass, daemons):
    player_port, coordinator_port, mutter = daemons
    # One connection per request: each handler thread ends with its response instead of idling on keep-alive.
    connector = aiohttp.TCPConnector(force_close=True)
    async with aiohttp.ClientSession(connector=connector, headers={"Connection": "close"}) as session:
        with mock.patch("custom_components.homehub_media.pc.async_get_clientsession", return_value=session):
            player = AmbientPlayerClient(hass, "127.0.0.1", player_port, TOKEN)
            coordinator = CoordinatorClient(hass, "127.0.0.1", coordinator_port, TOKEN)

        catalog = await player.catalog()
        assert [i["id"] for i in catalog["visuals"]] == ["rain"]
        assert [i["id"] for i in catalog["soundtracks"]] == ["jazz"]
        assert (await player.refresh())["ok"]
        played = await player.play({
            "visual": "rain", "visual_enabled": True, "video_loop": True, "video_random_start": False,
            "original_volume": 20, "soundtrack": "jazz", "soundtrack_loop": True,
            "soundtrack_random_start": True, "soundtrack_volume": 100,
        })
        assert played["composition"]["visual"] == "rain"
        for change in (  # the PATCH shapes ambience.py sends
            {"video": {"volume": 20}},
            {"video": {"enabled": False}},
            {"video": {"enabled": True, "media_id": "rain", "loop": True, "random_start": False, "volume": 40}},
            {"soundtrack": {"enabled": True, "media_id": "jazz", "loop": False, "random_start": True, "volume": 80}},
            {"soundtrack": {"volume": 30}},
            {"video": {"volume": 0}, "soundtrack": {"volume": 0}},  # sleep-timer fade
            {"soundtrack": {"enabled": False}},
        ):
            assert "patched" in await player.patch(change), change
        with pytest.raises(PCError):
            await player.patch({"video": {"volume": 101}})
        assert (await player.stop())["ok"]
        assert (await player.status())["state"] == "idle"

        await coordinator.open("jellyfin")
        assert (await coordinator.status())["mode"] == "jellyfin"
        await coordinator.remote("up")
        await coordinator.text("x" * 100)
        for delta in (160, -160):
            await coordinator.pointer_move(delta, -delta)
        for delta in (240, -240):
            await coordinator.scroll(delta)
        await coordinator.click()
        await coordinator.click("right")
        assert mutter.call_count == 8  # every accepted action reached the (seamed) Mutter input
        for call, args in (
            ("pointer_move", (161, 0)), ("pointer_move", (0, -160.5)),
            ("scroll", (241,)), ("scroll", (-241,)), ("text", ("x" * 101,)),
        ):
            with pytest.raises(PCError):
                await getattr(coordinator, call)(*args)
        assert mutter.call_count == 8  # rejected before any input was injected
        await coordinator.close()
        assert (await coordinator.status())["mode"] == "idle"
