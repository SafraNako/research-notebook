import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from arastirma_defteri.http import HttpClient, HttpError


class Server:
    """A scripted local server: ``script[path]`` is a list of (status, body, headers) answered in order; the last entry repeats."""

    def __init__(self, script):
        self.script, self.requests = {k: list(v) for k, v in script.items()}, []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.requests.append({"path": self.path, "headers": dict(self.headers)})
                queue = outer.script.get(self.path.split("?")[0], [(404, b"", {})])
                status, body, headers = queue.pop(0) if len(queue) > 1 else queue[0]
                if status == "slow":
                    time.sleep(1.0)
                    status, body, headers = 200, b"late", {}
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def serve():
    servers = []

    def make(script):
        s = Server(script)
        servers.append(s)
        return s
    yield make
    for s in servers:
        s.close()


def test_user_agent_is_sent_and_identifies_the_program(serve):
    s = serve({"/x": [(200, b"ok", {})]})
    HttpClient().get(s.base + "/x", headers={"Accept": "application/atom+xml"})
    h = s.requests[0]["headers"]
    assert h["User-Agent"].startswith("research-notebook/") and h["Accept"] == "application/atom+xml"


def test_throttling_and_server_errors_are_retried_with_exponential_backoff(serve):
    s = serve({"/x": [(429, b"Rate exceeded.", {}), (503, b"", {}), (200, b"fine", {})]})
    sleeps = []
    r = HttpClient(retries=3, backoff=3.0, sleep=sleeps.append).get(s.base + "/x")
    assert r.status == 200 and r.text() == "fine" and r.attempts == 3 and sleeps == [3.0, 6.0]


def test_retry_after_is_honoured_up_to_a_cap(serve):
    s = serve({"/x": [(429, b"", {"Retry-After": "5"}), (429, b"", {"Retry-After": "999"}), (200, b"ok", {})]})
    sleeps = []
    HttpClient(retries=2, backoff=1.0, sleep=sleeps.append).get(s.base + "/x")
    assert sleeps == [5.0, 60.0]


def test_client_errors_are_returned_not_retried_and_persistent_failures_return_the_last_response(serve):
    s = serve({"/gone": [(404, b"nope", {})], "/down": [(500, b"", {})]})
    assert HttpClient(retries=3).get(s.base + "/gone").status == 404 and len(s.requests) == 1
    r = HttpClient(retries=2, sleep=lambda x: None).get(s.base + "/down")
    assert r.status == 500 and len(s.requests) == 4


def test_timeouts_connection_failures_size_cap_and_allow_list(serve):
    s = serve({"/slow": [("slow", b"", {})], "/big": [(200, b"x" * 5000, {})]})
    with pytest.raises(HttpError):
        HttpClient(timeout=0.2, retries=0).get(s.base + "/slow")
    with pytest.raises(HttpError):
        HttpClient(retries=0).get("http://127.0.0.1:1/x")
    sleeps = []
    with pytest.raises(HttpError) as e:
        HttpClient(max_bytes=1000, retries=3, sleep=sleeps.append).get(s.base + "/big")
    assert "larger" in str(e.value) and sleeps == [] and [r["path"] for r in s.requests].count("/big") == 1                    # a permanent failure is not retried
    with pytest.raises(HttpError):
        HttpClient(allow_hosts={"example.org"}).get(s.base + "/big")
    assert HttpClient(allow_hosts={"127.0.0.1"}).get(s.base + "/big").status == 200


def test_per_host_minimum_interval(serve):
    s = serve({"/x": [(200, b"ok", {})]})
    now = [100.0]
    sleeps = []
    client = HttpClient(min_interval={"127.0.0.1": 3.0}, sleep=lambda d: (sleeps.append(d), now.__setitem__(0, now[0] + d)), clock=lambda: now[0])
    for _ in range(3):
        client.get(s.base + "/x")
    assert sleeps == [3.0, 3.0] and client.requests_made == 3
    other = HttpClient(min_interval={"example.org": 3.0}, sleep=lambda d: sleeps.append(d))
    other.get(s.base + "/x")
    assert sleeps == [3.0, 3.0]                                                   # an interval set for another host does not slow this one


def test_query_parameters_are_encoded(serve):
    s = serve({"/x": [(200, b"ok", {})]})
    HttpClient().get(s.base + "/x", params={"search_query": 'all:"a b" AND cat:x', "n": "5"})
    assert s.requests[0]["path"] == "/x?search_query=all%3A%22a%20b%22%20AND%20cat%3Ax&n=5"
