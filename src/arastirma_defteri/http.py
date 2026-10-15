"""A small, polite HTTP client: identifying User-Agent, timeouts, bounded retries with back-off (``Retry-After`` honoured), a minimum interval per host, a size
cap and an optional host allow-list. TLS verification is always on."""
from __future__ import annotations

import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

USER_AGENT = "research-notebook/0.1 (personal literature tracker; https://github.com/SafraNako/research-notebook)"
RETRY_STATUSES = {429, 500, 502, 503, 504}


class HttpError(Exception):
    """A request that could not be completed at all: DNS failure, refused, timed out, too large, host not allowed."""

    def __init__(self, url, reason, retryable=True):
        super().__init__(f"{url}: {reason}")
        self.url, self.reason, self.retryable = url, reason, retryable


@dataclass
class Response:
    url: str
    status: int
    headers: dict
    body: bytes
    attempts: int = 1
    from_tape: bool = field(default=False, repr=False)

    def text(self):
        return self.body.decode("utf-8", errors="replace")

    def json(self):
        import json
        return json.loads(self.body.decode("utf-8"))


class HttpClient:
    def __init__(self, user_agent=USER_AGENT, timeout=30.0, retries=3, backoff=2.0, min_interval=None, max_bytes=40_000_000, allow_hosts=None,
                 sleep=time.sleep, clock=time.monotonic):
        self.user_agent, self.timeout, self.retries, self.backoff = user_agent, timeout, retries, backoff
        self.min_interval = dict(min_interval or {})          # host -> seconds between two requests to it
        self.max_bytes, self.allow_hosts = max_bytes, allow_hosts
        self._sleep, self._clock = sleep, clock
        self._last, self._lock = {}, threading.Lock()
        self.requests_made = 0

    def _wait_turn(self, host):
        interval = self.min_interval.get(host, 0.0)
        if interval <= 0:
            return
        with self._lock:
            now = self._clock()
            delay = max(0.0, self._last.get(host, -1e18) + interval - now)
            self._last[host] = now + delay
        if delay:
            self._sleep(delay)

    def get(self, url, params=None, headers=None):
        """GET ``url``. Returns a ``Response`` for any HTTP status once the transient ones (429, 5xx) have been retried; raises ``HttpError`` when there is no response."""
        full = url if not params else url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        host = urllib.parse.urlparse(full).hostname or ""
        if self.allow_hosts is not None and host not in self.allow_hosts:
            raise HttpError(full, f"host {host!r} is not allowed in this client")
        request_headers = {"User-Agent": self.user_agent, "Accept": "*/*", "Accept-Encoding": "identity"}
        request_headers.update(headers or {})
        last_error, response = None, None
        for attempt in range(1, self.retries + 2):
            self._wait_turn(host)
            self.requests_made += 1
            try:
                response = self._once(full, request_headers, attempt)
                last_error = None
            except HttpError as exc:
                if not exc.retryable:                          # a response that is too large will be too large again: retrying only wastes time
                    raise
                last_error, response = exc, None
            if response is not None and response.status not in RETRY_STATUSES:
                return response
            if attempt <= self.retries:
                delay = self.backoff * (2 ** (attempt - 1))
                if response is not None and "retry-after" in response.headers:
                    try:
                        delay = min(float(response.headers["retry-after"]), 60.0)
                    except ValueError:
                        pass
                self._sleep(delay)
        if response is None:
            raise last_error
        return response

    def _once(self, url, headers, attempt):
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = r.read(self.max_bytes + 1)
                status, hdrs = r.status, {k.lower(): v for k, v in r.headers.items()}
        except urllib.error.HTTPError as e:
            body = e.read(self.max_bytes + 1) if e.fp else b""
            status, hdrs = e.code, {k.lower(): v for k, v in (e.headers or {}).items()}
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
            raise HttpError(url, f"{type(e).__name__}: {getattr(e, 'reason', e)}")
        if len(body) > self.max_bytes:
            raise HttpError(url, f"response larger than {self.max_bytes} bytes", retryable=False)
        return Response(url, status, hdrs, body, attempts=attempt)
