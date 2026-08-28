"""The out-of-process ledger daemon: a separate process the system under test cannot forge.

Two Unix sockets. The INVOKE socket (mode 0666) accepts one verb, `execute`, from the SUT - it can
record an effect and read back only an opaque receipt; it has no verb to read the ledger, to reset
it, or to advance anything. The CONTROL socket (mode 0600, in a 0700 dir) is the harness's:
`dump`, `reset`, `seal`. So the account of what happened is produced by a different process than
the one under test, over a channel the SUT cannot reach - the same separation as the Terminal-Bench
tool_server the ledger is ported from.

Run: `python -m crashpoint.ledger.daemon --invoke <p> --control <p> --store <p>`.
"""

from __future__ import annotations

import argparse
import json
import os
import socketserver
import threading
from pathlib import Path

from .core import LedgerState

_LOCK = threading.Lock()


class _Daemon:
    def __init__(self, store: Path) -> None:
        self.store = store
        self.sealed = False
        self.state = LedgerState(path=store)

    def handle(self, req: dict[str, object], *, privileged: bool) -> dict[str, object]:
        op = req.get("op")
        with _LOCK:
            if op == "execute":
                if self.sealed:
                    return {"ok": False, "error": "sealed"}
                intent = str(req.get("intent_id", "intent"))
                key = req.get("key")
                key_s = str(key) if key else None
                pl = req.get("payload")
                payload: dict[str, object] = pl if isinstance(pl, dict) else {}
                receipt = self.state.execute(intent, key_s, payload)
                return {"ok": True, "receipt": receipt, "outcome": "OK"}
            if not privileged:
                return {"ok": False, "error": "verb requires the control socket"}
            if op == "dump":
                return {"ok": True, "dump": self.state.dump()}
            if op == "seal":
                self.sealed = True
                return {"ok": True}
            if op == "reset":
                self.sealed = False
                if self.store.exists():
                    self.store.unlink()
                self.state = LedgerState(path=self.store)
                return {"ok": True}
            return {"ok": False, "error": f"unknown op {op!r}"}


def _make_handler(daemon: _Daemon, privileged: bool) -> type[socketserver.BaseRequestHandler]:
    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            for raw in self.rfile:
                try:
                    req = json.loads(raw)
                except json.JSONDecodeError:
                    self.wfile.write(b'{"ok": false, "error": "bad json"}\n')
                    continue
                resp = daemon.handle(dict(req), privileged=privileged)
                self.wfile.write((json.dumps(resp) + "\n").encode())
                self.wfile.flush()

    return Handler


class _Server(socketserver.ThreadingUnixStreamServer):
    allow_reuse_address = True


def serve(invoke_path: str, control_path: str, store_path: str) -> None:
    for p in (invoke_path, control_path):
        if os.path.exists(p):
            os.unlink(p)
    ctl_dir = os.path.dirname(control_path)
    if ctl_dir:
        os.makedirs(ctl_dir, exist_ok=True)
        os.chmod(ctl_dir, 0o700)
    daemon = _Daemon(Path(store_path))

    invoke = _Server(invoke_path, _make_handler(daemon, privileged=False))
    os.chmod(invoke_path, 0o666)  # the SUT must be able to call in
    control = _Server(control_path, _make_handler(daemon, privileged=True))
    os.chmod(control_path, 0o600)  # and must not be able to look in

    threading.Thread(target=invoke.serve_forever, daemon=True).start()
    print("LEDGER_READY", flush=True)
    control.serve_forever()


def _request(sock_path: str, req: dict[str, object]) -> dict[str, object]:
    import socket

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(sock_path)
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
    return dict(json.loads(buf)) if buf.strip() else {"ok": False, "error": "no response"}


def execute(
    invoke_path: str, intent_id: str, key: str | None, payload: dict[str, object]
) -> dict[str, object]:
    """SUT-side client: record one external effect over the invoke socket."""
    return _request(invoke_path, {"op": "execute", "intent_id": intent_id, "key": key,
                                  "payload": payload})


def control(control_path: str, op: str) -> dict[str, object]:
    """Harness-side client: dump / seal / reset over the control socket."""
    return _request(control_path, {"op": op})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--invoke", required=True)
    ap.add_argument("--control", required=True)
    ap.add_argument("--store", required=True)
    args = ap.parse_args(argv)
    serve(args.invoke, args.control, args.store)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
