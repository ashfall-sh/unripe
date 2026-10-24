"""The kept-alive transport, against a real HTTP server on this machine."""

import http.server
import json
import threading
import urllib.error

import pytest

from unripe import registries

# the real transport, before the no_network fixture swaps it out
SEND = registries.send


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen: list = []

    def log_message(self, *args):
        pass

    def answer(self, status: int, body: bytes = b"", headers: dict | None = None) -> None:
        self.send_response(status)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        Handler.seen.append((self.path, self.client_address[1], self.headers.get("Authorization")))
        if self.path == "/moved":
            self.answer(301, headers={"Location": "/json"})
        elif self.path == "/away":
            self.answer(302, headers={"Location": self.server.other + "/json"})
        elif self.path == "/loop":
            self.answer(302, headers={"Location": "/loop"})
        elif self.path == "/missing":
            self.answer(404, b"no")
        elif self.path == "/drop":
            # an idle timeout: the server hangs up without saying so first
            self.answer(200, b"{}")
            self.close_connection = True
        elif self.path == "/close":
            # answers, then drops the connection the client thinks it can keep
            self.answer(200, b"{}", {"Connection": "close"})
            self.close_connection = True
        else:
            self.answer(200, json.dumps({"path": self.path}).encode(), {"Last-Modified": "Mon, 20 Oct 2026 12:00:00 GMT"})

    do_HEAD = do_GET


@pytest.fixture
def servers(monkeypatch):
    monkeypatch.setattr(registries, "send", SEND)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(registries.urllib.request, "getproxies", lambda: {})
    Handler.seen = []
    started = []
    for _ in range(2):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        started.append(server)
    first, second = (f"http://127.0.0.1:{server.server_port}" for server in started)
    started[0].other, started[1].other = second, first
    registries.POOL.__dict__.clear()
    yield first, second
    for server in started:
        server.shutdown()
        server.server_close()
    registries.POOL.__dict__.clear()


def test_one_connection_is_kept_for_many_requests(servers):
    base, _ = servers
    for number in range(5):
        assert registries.fetch(f"{base}/json?n={number}") == {"path": f"/json?n={number}"}
    ports = {port for _, port, _ in Handler.seen}
    assert len(Handler.seen) == 5 and len(ports) == 1


def test_redirects_are_followed(servers):
    base, _ = servers
    assert registries.fetch(f"{base}/moved") == {"path": "/json"}


def test_a_token_never_follows_a_redirect_to_another_host(servers, monkeypatch):
    base, other = servers
    response = SEND(registries.urllib.request.Request(f"{base}/away", headers={"Authorization": "Bearer secret"}), 5)
    assert json.loads(response.read()) == {"path": "/json"}
    assert Handler.seen[0][2] == "Bearer secret" and Handler.seen[1][2] is None


def test_errors_and_redirect_loops(servers):
    base, _ = servers
    with pytest.raises(registries.Missing):
        registries.fetch(f"{base}/missing")
    with pytest.raises(urllib.error.URLError, match="too many redirects"):
        SEND(registries.urllib.request.Request(f"{base}/loop"), 5)


def test_head_requests_and_closed_connections(servers):
    base, _ = servers
    assert registries.fetchmodified(f"{base}/pom").year == 2026
    assert registries.fetch(f"{base}/close") == {}
    # the server closed that connection; the next request opens a new one
    assert registries.fetch(f"{base}/json") == {"path": "/json"}


def test_a_connection_the_server_dropped_is_opened_again(servers):
    base, _ = servers
    assert registries.fetch(f"{base}/drop") == {}
    assert registries.fetch(f"{base}/json") == {"path": "/json"}
    assert len({port for _, port, _ in Handler.seen}) == 2


def test_an_answer_too_big_to_hold_is_refused(servers, monkeypatch):
    import gzip as gz

    base, _ = servers
    monkeypatch.setattr(registries, "LIMIT", 10)
    with pytest.raises(registries.Unreachable, match="more than"):
        registries.fetch(f"{base}/json?padding=0123456789")
    monkeypatch.setattr(registries, "send", lambda request, timeout: Zipped(gz.compress(b"{" + b" " * 100 + b"}")))
    with pytest.raises(registries.Unreachable, match="more than"):
        registries.fetch(f"{base}/zipped")


class Zipped:
    """A gzipped answer that is small on the wire and big unpacked."""

    def __init__(self, body: bytes):
        self.body, self.headers = body, {"Content-Encoding": "gzip"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, limit: int = -1) -> bytes:
        return self.body


def test_a_certificate_error_says_what_to_set_and_is_not_retried(monkeypatch):
    import ssl

    calls = []

    def refuse(request, timeout):
        calls.append(request.full_url)
        raise urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed: unable to get local issuer certificate"))

    monkeypatch.setattr(registries, "send", refuse)
    with pytest.raises(registries.Unreachable, match="set UNRIPE_CA_BUNDLE or SSL_CERT_FILE"):
        registries.fetch("https://registry.example/thing")
    assert len(calls) == 1


def test_credentials_never_follow_a_redirect_to_another_host_through_a_proxy():
    """Through a proxy, urllib follows redirects itself, and on its own it keeps the Authorization header."""
    import io as stream
    import urllib.request

    handler = registries.Redirects()
    request = urllib.request.Request("https://npm.example.com/left-pad", headers={"Authorization": "Bearer s3cret"})
    elsewhere = handler.redirect_request(request, stream.BytesIO(), 302, "Found", {}, "https://cdn.example.net/left-pad")
    assert not elsewhere.has_header("Authorization")
    same = handler.redirect_request(request, stream.BytesIO(), 302, "Found", {}, "https://npm.example.com/other")
    assert same.get_header("Authorization") == "Bearer s3cret"
