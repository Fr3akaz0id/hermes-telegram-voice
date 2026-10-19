"""Stub sidecar for the Telegram port (Phase 1).

Serves the same loopback HTTP API as the real sidecar with canned responses, so the
plugin layer, tools, and CLI can be exercised before the Telethon/ntgcalls transport
(Phase 2) lands. Start with:  hermes telegram-voice serve --stub
"""

from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# What a placed call walks through when polled via GET /calls/<id>.
_SEQUENCE = ["dialing", "ringing", "ringing", "in_progress", "completed"]
_TRANSCRIPT = [{"role": "owner", "text": "(stub) no real audio yet - transport lands in Phase 2."}]


class _Handler(BaseHTTPRequestHandler):
    stub: StubSidecar = None  # set by StubSidecar.serve

    def log_message(self, *_args) -> None:
        pass

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        if self.headers.get("Authorization") != f"Bearer {self.stub.token}":
            self._send(401, {"error": "bad or missing token"})
            return False
        return True

    def do_POST(self) -> None:
        if not self._authorized():
            return
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        with self.stub.lock:
            self.stub.n += 1
            cid = f"stub{self.stub.n}"
            self.stub.started[cid] = 0  # poll counter, keyed by call id
        self._send(202, {"id": cid, "status": "dialing"})

    def do_GET(self) -> None:
        if not self._authorized():
            return
        path = urllib.parse.urlparse(self.path).path
        if path == "/health":
            self._send(200, {"ok": True, "stub": True})
            return
        if path.startswith("/calls/stub"):
            cid = path.rsplit("/", 1)[1]
            with self.stub.lock:
                i = self.stub.started.get(cid, len(_SEQUENCE) - 1)
                self.stub.started[cid] = min(i + 1, len(_SEQUENCE) - 1)
                status = _SEQUENCE[self.stub.started[cid]]
            record = {"id": cid, "status": status, "stub": True}
            if status == "completed":
                record["transcript"] = _TRANSCRIPT
            self._send(200, record)
            return
        if path.startswith("/calls"):
            with self.stub.lock:
                ids = list(self.stub.started)
            self._send(200, {"calls": [{"id": i, "status": "completed", "stub": True} for i in ids[-5:]]})
            return
        self._send(404, {"error": "unknown path"})


class StubSidecar:
    def __init__(self, listen: str, token: str):
        host, _, port = listen.partition(":")
        self.token = token
        self.lock = threading.Lock()
        self.n = 0
        self.started: dict[str, int] = {}
        self.server = ThreadingHTTPServer((host, int(port)), _Handler)
        _Handler.stub = self

    def serve_forever(self) -> None:
        print(f"[stub sidecar] listening on http://{self.server.server_address[0]}:{self.server.server_address[1]}")
        print("[stub sidecar] placeholder transport - the Telethon/ntgcalls sidecar lands in Phase 2")
        self.server.serve_forever()

    def close(self) -> None:
        self.server.shutdown()


def serve(listen: str, token: str) -> None:
    StubSidecar(listen, token).serve_forever()
