import http.client
import json
import socket
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "media-hub-coordinator"))
import media_hub_coordinator as co

TOKEN = "t" * 40


def start_server(allowed_clients=("127.0.0.1",)):
    cfg = co.Config.from_dict({
        "listen_host": "127.0.0.1", "listen_port": 0, "allowed_clients": list(allowed_clients), "auth_token": TOKEN,
        "clients": {"jellyfin": {"unit": "jellyfin-browser.service", "home_url": "http://127.0.0.1:8096/web/"}},
    })
    srv = co.make_server(cfg)  # returns a bound, not-yet-serving server
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


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
