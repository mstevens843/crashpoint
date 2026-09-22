"""Loopback receiver: existing LedgerState, serialized append + fsync before acknowledgment."""

from __future__ import annotations

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from crashpoint.ledger.core import LedgerState


def serve(directory: Path) -> None:
    directory.mkdir(exist_ok=True)
    store = directory / "effects.jsonl"
    with store.open("xb") as handle:
        handle.flush()
        os.fsync(handle.fileno())
    state = LedgerState(store)
    lock = threading.Lock()
    sealed = False

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            nonlocal sealed
            try:
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                with lock:
                    if self.path == "/seal":
                        sealed = True
                        result: dict[str, object] = {"ok": True, "sealed": True}
                    elif self.path == "/execute" and not sealed:
                        for key in ["action_id", "attempt_id", "run_id", "trial_id"]:
                            if not isinstance(request[key], str) or not request[key]:
                                raise ValueError(f"invalid {key}")
                        payload = request["payload"]
                        if not isinstance(payload, dict):
                            raise ValueError("invalid payload")
                        with (directory / "requests.jsonl").open("a") as handle:
                            handle.write(json.dumps(request, sort_keys=True) + "\n")
                            handle.flush()
                            os.fsync(handle.fileno())
                        state.execute(
                            request["action_id"], None, payload, attempt_id=request["attempt_id"]
                        )
                        with store.open("rb") as handle:
                            os.fsync(handle.fileno())
                        result = {"ok": True, "receipt": "receipt-ok", "fsync": True}
                    else:
                        raise ValueError("sealed or unknown endpoint")
                raw = (json.dumps(result) + "\n").encode()
                self.send_response(200)
            except (KeyError, ValueError, OSError) as exc:
                raw = (json.dumps({"ok": False, "error": str(exc)}) + "\n").encode()
                self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    (directory / "requests.jsonl").touch(exist_ok=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    print(
        json.dumps({"event": "receiver_ready", "pid": os.getpid(), "port": server.server_port}),
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("directory", type=Path)
    serve(ap.parse_args().directory)


if __name__ == "__main__":
    main()
