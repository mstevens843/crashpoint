"""Fresh observer of real bytes. Missing/unreadable never means zero effects.

The verifier separately parses the archived bytes; it does not trust these counts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from crashpoint.canonical import canonicalize


def observe(source: Path, archive: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"version": 1, "pid": os.getpid(), "availability": "unavailable"}
    try:
        raw = source.read_bytes()
    except OSError as exc:
        return {**result, "error_type": type(exc).__name__, "errno": exc.errno}
    # Failure archiving the bytes is an observer failure, not unavailable receiver evidence.
    with archive.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    result.update(availability="readable", sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
    try:
        head = "crashpoint-ledger-genesis-cp1"
        attempts = []
        for index, line in enumerate(raw.splitlines()):
            row = json.loads(line)
            record = row["record"]
            expected = hashlib.sha256((head + "|" + canonicalize(record)).encode()).hexdigest()
            if row["i"] != index or row["prev"] != head or row["hash"] != expected:
                raise ValueError("chain/order mismatch")
            if record["keyed"] is not False or record["deduped"] is not False:
                raise ValueError("unexpected deduplication")
            attempts.append(record)
            head = expected
        result.update(valid_chain=True, effects=len(attempts), attempts=attempts, head=head)
    except (ValueError, TypeError, KeyError) as exc:
        result.update(valid_chain=False, error_type=type(exc).__name__, error=str(exc))
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path)
    ap.add_argument("archive", type=Path)
    ap.add_argument("--failpoint", default="")
    args = ap.parse_args()
    if args.failpoint == "observer_nonzero":
        print(json.dumps({"injected": "observer_nonzero", "pid": os.getpid()}), flush=True)
        return 72
    if args.failpoint == "observer_malformed":
        print("{invalid observer output", flush=True)
        return 0
    print(json.dumps(observe(args.source, args.archive), sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
