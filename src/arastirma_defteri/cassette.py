"""Record a real session and replay it later: the network responses of one live run stored as a compressed JSON file, and an HTTP client that serves them back by URL.
The replay exercises the real parsers, the real watch logic and the real duplicate resolution on genuine responses, with no network at all."""
from __future__ import annotations

import gzip
import json

from .http import HttpClient, HttpError, Response


class RecordingClient(HttpClient):
    """An ``HttpClient`` that remembers every response it gets from the network."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tape = {}

    def _once(self, url, headers, attempt):
        response = super()._once(url, headers, attempt)
        if response.status == 200:
            self.tape[url] = {"status": response.status, "body": response.body.decode("utf-8")}
        return response


class ReplayClient(HttpClient):
    """An ``HttpClient`` that answers from a recorded tape and never touches the network."""

    def __init__(self, tape, **kwargs):
        super().__init__(**kwargs)
        self.tape = tape

    def _once(self, url, headers, attempt):
        entry = self.tape.get(url)
        if entry is None:
            raise HttpError(url, "not in the cassette", retryable=False)
        return Response(url, entry["status"], {}, entry["body"].encode("utf-8"), attempts=attempt)


def save(path, tape, meta):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump({"meta": meta, "tape": tape}, f, ensure_ascii=False, separators=(",", ":"))


def load(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        data = json.load(f)
    return data["meta"], data["tape"]
