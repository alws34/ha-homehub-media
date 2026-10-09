import http.client
import json
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "ambient-player"))
import ambient_player as ap

TOKEN = "t" * 40


def config(root, **extra):
    for name in ("videos", "soundtracks"):
        (root / name).mkdir(exist_ok=True)
    return ap.config_from_dict({
        "bind_host": "127.0.0.1", "port": 0, "allowed_clients": ["127.0.0.1"], "auth_token": TOKEN,
        "video_root": str(root / "videos"), "soundtrack_root": str(root / "soundtracks"),
        "state_file": str(root / "state.json"), **extra,
    })


def start_server(root, **extra):
    srv = ap.make_server(config(root, **extra))  # bound, not yet serving; empty media roots need no mpv
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def request(port, method, path, headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = {"Host": f"127.0.0.1:{port}", "Authorization": f"Bearer {TOKEN}"}
    h.update(headers or {})
    c.request(method, path, body=body, headers={k: v for k, v in h.items() if v is not None})
    r = c.getresponse()
    return r.status, json.loads(r.read() or b"{}")


class AuthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.srv = start_server(Path(cls.tmp.name))
        cls.port = cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def req(self, method, path, headers=None, body=None):
        return request(self.port, method, path, headers, body)[0]

    def test_status_ok_with_token_and_empty_catalog(self):
        status, body = request(self.port, "GET", "/v2/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["catalog"], {"visuals": 0, "soundtracks": 0, "skipped": 0})

    def test_missing_token_401(self):
        self.assertEqual(self.req("GET", "/v2/status", {"Authorization": None}), 401)

    def test_wrong_token_401(self):
        self.assertEqual(self.req("GET", "/v2/status", {"Authorization": "Bearer nope"}), 401)

    def test_rebinding_host_421(self):
        self.assertEqual(self.req("GET", "/v2/status", {"Host": "evil.example"}), 421)

    def test_text_plain_csrf_415(self):
        self.assertEqual(self.req("POST", "/v2/play", {"Content-Type": "text/plain"},
                                  json.dumps({"visual": "rain"})), 415)

    def test_duplicate_content_length_400(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as s:  # http.client can't repeat a header
            s.sendall(f"POST /v2/play HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nAuthorization: Bearer {TOKEN}\r\n"
                      "Content-Type: application/json\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}".encode())
            self.assertEqual(s.recv(64).split()[1], b"400")

    def test_unknown_media_is_a_code_not_a_message(self):
        status, body = request(self.port, "POST", "/v2/play", {"Content-Type": "application/json"},
                               json.dumps({"visual": "rain"}))
        self.assertEqual((status, body), (409, {"ok": False, "error": "unknown_media"}))

    def test_removed_routes_404(self):
        self.assertEqual(self.req("GET", "/v2/profiles"), 404)
        self.assertEqual(self.req("POST", "/v1/play/rain"), 404)
        for path in ("/v2/pause", "/v2/resume", "/v2/video/stop", "/v2/soundtrack/resume"):
            self.assertEqual(self.req("POST", path), 404)

    def test_short_token_rejected(self):
        for token in ("short", "t" * 20 + " " + "t" * 20):
            with self.assertRaises(ap.PlayerError):
                config(Path(self.tmp.name), auth_token=token)

    def test_unlisted_peers_cannot_starve_allowlisted_clients(self):
        pairs = [socket.socketpair() for _ in range(ap.MAX_CONNECTIONS)]
        try:
            for server_end, _ in pairs:  # idle connections from a non-allowlisted LAN host
                self.srv.process_request(server_end, ("192.0.2.66", 40000))
            self.assertEqual(self.req("GET", "/v2/status"), 200)
        finally:
            for _, client_end in pairs:
                client_end.close()

    def test_ip_not_allowed_403_before_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            srv = start_server(Path(tmp), allowed_clients=["192.0.2.1"])
            try:
                status, _ = request(srv.server_address[1], "GET", "/v2/status", {"Authorization": None})
                self.assertEqual(status, 403)
            finally:
                srv.shutdown()
                srv.server_close()


class StateTest(unittest.TestCase):
    def test_v2_state_is_migrated_to_v3(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            catalog = {"video": {"rain": {"relative_path": "rain.mp4", "fingerprint": "f"}}, "soundtrack": {}}
            path.write_text(json.dumps({"version": 2, "catalog": catalog,
                                        "favorites": {"video": ["rain"], "soundtrack": []},
                                        "profiles": {"ambience": {"visual": "rain"}}}))
            ap.PersistentState(path)
            self.assertEqual(json.loads(path.read_text()), {"version": 3, "catalog": catalog})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_corrupt_state_is_set_aside(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{not json")
            ap.PersistentState(path)
            self.assertEqual(json.loads(path.read_text())["version"], 3)
            self.assertEqual((Path(tmp) / "state.json.corrupt").read_text(), "{not json")

    def test_stream_id_wins_over_colliding_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(Path(tmp), soundtrack_streams={"radio_x": "https://radio.example/x"})
            (Path(tmp) / "soundtracks" / "radio_x.mp3").write_bytes(b"audio")
            with mock.patch.object(ap.Catalog, "_probe", return_value=(10.0, True)):
                catalog = ap.Catalog(cfg, ap.PersistentState(Path(cfg["state_file"])))
            items = catalog.items["soundtrack"]
            self.assertEqual((items["radio_x"].source_type, items["radio_x_2"].source_type), ("stream", "file"))

    def test_unprobeable_file_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(Path(tmp))
            (Path(tmp) / "videos" / "broken.mp4").write_bytes(b"not a video")
            with mock.patch.object(ap.Catalog, "_probe", side_effect=ap.PlayerError("probe failed")):
                catalog = ap.Catalog(cfg, ap.PersistentState(Path(cfg["state_file"])))
            self.assertEqual((catalog.items["video"], catalog.skipped), ({}, 1))
