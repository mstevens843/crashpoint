"""The fresh observer process for the LangGraph non-crash control.

Spawned by ``langgraph_control.py`` as a brand-new ``python -m`` subprocess, AFTER the worker has
exited and the ledger daemon has been sealed and stopped - never called as a function inside the
worker, and never reading the daemon's live ``dump()``. This process opens the ledger's on-disk
JSONL store with its own fresh file handle, derives everything from those bytes via
``ledger_readback``, and never imports LangGraph.

THE MISSING-VS-EMPTY DISTINCTION IS CHECKED FIRST, BEFORE ANY PARSING. A store path that does not
exist is ``STORE_MISSING`` (an incomplete observation - null counts, never zero). A store that
exists and parses to zero crossings for this intent is a real, valid ``OBSERVED`` finding of zero -
a failed positive control, not a missing one. See ``ledger_readback.py`` for why this can't be left
to ``LedgerState.verify``.

A PID IS DIAGNOSTIC, NOT PROOF. This process is a distinct OS process from the worker (a real
capability boundary: it opens its own file handle rather than reusing the worker's), but it runs
under the same OS user with no additional sandboxing. Its ``pid`` is retained for diagnostics only,
never treated as cryptographic evidence of isolation - see the ``pid_note`` this module includes.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .ledger_readback import LedgerBytesCorrupt, parse_ledger_bytes, sha256_bytes

PREFIX = "CRASHPOINT_LANGGRAPH_CONTROL_OBSERVER "

REPRESENTATION_NOTE = (
    "each JSONL record stores intent_id directly in plaintext and the effect payload only as a "
    "SHA-256 payload_digest; the raw payload bytes are not persisted by the ledger and are not "
    "recoverable from this readback"
)


def observe(ledger_store: Path, intent_id: str, archive_to: Path) -> dict[str, Any]:
    """Pure-ish orchestration over real filesystem paths: check existence first, read fresh bytes,
    parse independently, archive an exact copy, and re-hash the copy to confirm it agrees with the
    original. Returns the observer's structured report (JSON-shaped)."""
    report: dict[str, Any] = {
        "status": None,
        "pid": os.getpid(),
        "pid_note": "diagnostic metadata only, not cryptographic proof of independence",
        "intent_id": intent_id,
        "ledger_store_path_read": str(ledger_store),
        "original_readback_sha256": None,
        "byte_length": None,
        "record_count": None,
        "chain_valid": False,
        "first_broken_index": -1,
        "archived_ledger_readback_path": None,
        "archived_ledger_readback_sha256": None,
        "archive_matches_original": False,
        "observed_count": None,
        "effect_digests": [],
        "effect_attempt_ids": [],
        "representation_note": REPRESENTATION_NOTE,
        "diagnostic_only": {},
        "error": None,
    }

    if not ledger_store.exists():
        report["status"] = "STORE_MISSING"
        report["error"] = f"ledger store does not exist at {ledger_store}"
        return report

    raw = ledger_store.read_bytes()
    original_sha256 = sha256_bytes(raw)
    report["original_readback_sha256"] = original_sha256
    report["byte_length"] = len(raw)

    archive_to.parent.mkdir(parents=True, exist_ok=True)
    archive_to.write_bytes(raw)
    archived_raw = archive_to.read_bytes()
    archived_sha256 = sha256_bytes(archived_raw)
    report["archived_ledger_readback_path"] = str(archive_to)
    report["archived_ledger_readback_sha256"] = archived_sha256
    report["archive_matches_original"] = archived_sha256 == original_sha256

    try:
        readback = parse_ledger_bytes(raw)
    except LedgerBytesCorrupt as exc:
        report["status"] = "CORRUPT"
        report["error"] = f"malformed ledger record: {exc}"
        return report

    report["record_count"] = readback.record_count
    report["chain_valid"] = readback.chain_valid
    report["first_broken_index"] = readback.first_broken_index

    if not readback.chain_valid:
        report["status"] = "CORRUPT"
        report["error"] = f"ledger hash chain broken at record index {readback.first_broken_index}"
        # Best-effort numbers are kept ONLY as an unauthoritative diagnostic; a broken chain means
        # nothing derived past that point is trusted for the receipt's effect.observed_count.
        report["diagnostic_only"] = {
            "unverified_side_effects": readback.side_effects,
            "unverified_attempts": readback.attempts,
        }
        return report

    report["status"] = "OBSERVED"
    report["observed_count"] = readback.effect_count(intent_id)
    report["effect_digests"] = list(readback.effect_digests.get(intent_id, []))
    report["effect_attempt_ids"] = list(readback.effect_attempt_ids.get(intent_id, []))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger-store", required=True, type=Path)
    parser.add_argument("--intent", required=True)
    parser.add_argument("--archive-to", required=True, type=Path)
    parser.add_argument("--report-out", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        report = observe(args.ledger_store, args.intent, args.archive_to)
    except Exception as exc:  # the observer's OWN failure, distinct from a STORE_MISSING/CORRUPT
        # finding it would otherwise report normally.
        failure = {
            "status": "OBSERVER_ERROR",
            "pid": os.getpid(),
            "pid_note": "diagnostic metadata only, not cryptographic proof of independence",
            "intent_id": args.intent,
            "error": f"{type(exc).__name__}: {exc}",
        }
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(json.dumps(failure, indent=2, sort_keys=True) + "\n")
        print(PREFIX + json.dumps(failure, sort_keys=True), flush=True)
        return 2

    args.report_out.parent.mkdir(parents=True, exist_ok=True)
    args.report_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(PREFIX + json.dumps(report, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
