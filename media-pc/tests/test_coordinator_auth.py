import http.client
import json
import socket
import subprocess
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "media-hub-coordinator"))
import media_hub_coordinator as co

TOKEN = "t" * 40
JELLYFIN = {"jellyfin": {"unit": "jellyfin-browser.service", "home_url": "http://127.0.0.1:8096/web/"}}
CLIENTS = {  # plex first, so "first configured" cannot be confused with a jellyfin default
    "plex": {"unit": "plex-browser.service", "home_url": "http://127.0.0.1:32400/web/"},
    "sonarr": {"unit": "sonarr-browser.service", "home_url": "http://127.0.0.1:8989/", "tab_navigation": True},
}


def config(allowed_clients=("127.0.0.1",), clients=JELLYFIN):
    return co.Config.from_dict({
        "listen_host": "127.0.0.1", "listen_port": 0, "allowed_clients": list(allowed_clients), "auth_token": TOKEN,
        "clients": clients,
    })


def start_server(allowed_clients=("127.0.0.1",), clients=JELLYFIN, runner=None):
    cfg = config(allowed_clients, clients)
    srv = co.make_server(cfg)  # returns a bound, not-yet-serving server
    if runner:
        srv.coordinator = co.Coordinator(cfg, runner=runner)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class FakeSystemctl:
    """Stands in for `systemctl --user` through the Coordinator runner seam."""

    def __init__(self, active=(), error=None):
        self.active, self.error = set(active), error

    def __call__(self, argv, **kwargs):
        if self.error:
            raise self.error
        action, unit = argv[2:4]
        if action == "start":
            self.active.add(unit)
        elif action == "stop":
            self.active.discard(unit)
        state = "active" if unit in self.active else "inactive"
        return subprocess.CompletedProcess(argv, 0, stdout=state if action == "is-active" else "")


class FakeKeys:
    """Records commands instead of injecting them through GNOME Mutter."""

    def __init__(self):
        self.sent = []

    def send(self, command):
        self.sent.append(command)


def request(port, method, path, headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = {"Host": f"127.0.0.1:{port}", "Authorization": f"Bearer {TOKEN}"}
    h.update(headers or {})
    c.request(method, path, body=body, headers={k: v for k, v in h.items() if v is not None})
    return c.getresponse().status


class AuthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = start_server()
        cls.port = cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def req(self, method, path, headers=None, body=None):
        return request(self.port, method, path, headers, body)

    def test_health_ok_with_token(self):
        self.assertEqual(self.req("GET", "/health"), 200)

    def test_missing_token_401(self):
        self.assertEqual(self.req("GET", "/health", {"Authorization": None}), 401)

    def test_wrong_token_401(self):
        self.assertEqual(self.req("GET", "/health", {"Authorization": "Bearer nope"}), 401)

    def test_rebinding_host_421(self):
        self.assertEqual(self.req("GET", "/health", {"Host": "evil.example"}), 421)

    def test_text_plain_csrf_415(self):
        self.assertEqual(self.req("POST", "/v1/keyboard/text", {"Content-Type": "text/plain"},
                                  json.dumps({"text": "hi"})), 415)

    def test_body_framing_and_strict_json_type(self):
        json_body = {"Content-Type": "application/json"}
        self.assertEqual(self.req("POST", "/v1/nope", {**json_body, "Transfer-Encoding": "chunked"}, "{}"), 411)
        self.assertEqual(self.req("POST", "/v1/nope", {**json_body, "Content-Length": "abc"}, ""), 400)
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as s:  # http.client can't repeat a header
            s.sendall(f"POST /v1/nope HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nAuthorization: Bearer {TOKEN}\r\n"
                      "Content-Type: application/json\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}".encode())
            self.assertEqual(s.recv(64).split()[1], b"400")
        self.assertEqual(self.req("POST", "/v1/nope", {"Content-Type": "application/jsonp"}, "{}"), 415)
        self.assertEqual(self.req("POST", "/v1/nope", {"Content-Type": " Application/JSON; charset=utf-8"}, "{}"), 404)

    def test_short_token_rejected(self):
        for token in ("short", "t" * 20 + " " + "t" * 20):
            with self.assertRaises(co.CoordinatorError):
                co.Config.from_dict({"listen_host": "127.0.0.1", "listen_port": 0, "allowed_clients": ["127.0.0.1"],
                                     "auth_token": token, "clients": {"a": {"unit": "a.service",
                                     "home_url": "http://127.0.0.1/"}}})

    def test_unlisted_peers_cannot_starve_allowlisted_clients(self):
        pairs = [socket.socketpair() for _ in range(co.MAX_CONNECTIONS)]
        try:
            for server_end, _ in pairs:  # idle connections from a non-allowlisted LAN host
                self.srv.process_request(server_end, ("192.0.2.66", 40000))
            self.assertEqual(self.req("GET", "/health"), 200)
        finally:
            for _, client_end in pairs:
                client_end.close()

    def test_ip_not_allowed_403_before_token(self):
        srv = start_server(["192.0.2.1"])
        try:
            self.assertEqual(request(srv.server_address[1], "GET", "/health", {"Authorization": None}), 403)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_connections_beyond_cap_are_closed(self):
        srv = start_server()
        address = ("127.0.0.1", srv.server_address[1])
        idle = [socket.create_connection(address, timeout=5) for _ in range(co.MAX_CONNECTIONS)]
        try:
            with socket.create_connection(address, timeout=5) as extra:
                self.assertEqual(extra.recv(1), b"")  # closed unanswered; the idle 16 hold every slot
        finally:
            for s in idle:
                s.close()
            srv.shutdown()
            srv.server_close()


class CoordinatorTest(unittest.TestCase):
    def serve(self, runner):
        srv = start_server(clients=CLIENTS, runner=runner)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv.server_address[1]

    def test_bare_open_starts_first_configured_client(self):
        systemctl = FakeSystemctl()
        self.assertEqual(request(self.serve(systemctl), "POST", "/v1/open"), 200)
        self.assertEqual(systemctl.active, {"plex-browser.service"})

    def test_systemctl_missing_or_timing_out_is_409(self):
        for error in (FileNotFoundError("systemctl"), subprocess.TimeoutExpired("systemctl", 20)):
            self.assertEqual(request(self.serve(FakeSystemctl(error=error)), "GET", "/v1/status"), 409)

    def test_tab_navigation_only_for_flagged_clients(self):
        for unit, expected in (("plex-browser.service", ["left", "right", "up"]),
                               ("sonarr-browser.service", ["shift_tab", "tab", "up"])):
            keys = FakeKeys()
            coordinator = co.Coordinator(config(clients=CLIENTS), runner=FakeSystemctl([unit]), remote_input=keys)
            for command in ("left", "right", "up"):
                coordinator.remote(command)
            self.assertEqual(keys.sent, expected)
