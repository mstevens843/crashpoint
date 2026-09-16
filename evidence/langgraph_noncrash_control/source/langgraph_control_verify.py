"""Offline verifier for a LangGraph non-crash control bundle.

Runs without LangGraph installed or imported, and without launching any runtime: every check here
either re-derives a fact from bytes retained inside the bundle (the ledger readback, the
checkpoint database, the admission database, the source module copies) or recomputes a hash.
Nothing here trusts a summary written by the worker, the observer, or the harness that produced
the bundle - each of those numbers is independently recomputed from the retained artifact and
cross-checked against what ``receipt.json`` claims.

PORTABILITY. Every path this module opens is resolved as ``bundle_root / <bundle-relative path
from receipt.json>``. No absolute path from the machine that produced the bundle is read or
required, so a copied or relocated bundle verifies identically - see
``tests/test_langgraph_control_verify.py``'s relocation test.

WHAT THIS DOES NOT PROVE. A PASS here means the retained evidence is internally consistent and
supports what ``receipt.json`` claims - not that the original execution happened exactly as
described, was authenticated in real time, or can be independently rerun. A checksum agreeing with
itself does not prove an external effect actually happened in the world; it proves the retained
bytes have not changed and are self-consistent. See ``receipt.json``'s own ``limitations`` field.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from ..canonical import receipt as canonical_receipt
from .langgraph_control_receipt import classify_control, validate_receipt
from .ledger_readback import LedgerBytesCorrupt, parse_ledger_bytes, sha256_bytes


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_file_hash(
    bundle_root: Path, rel_path: object, declared_sha256: object, label: str
) -> list[str]:
    if not isinstance(rel_path, str) or not rel_path:
        return [f"{label}: declared path is not a non-empty string: {rel_path!r}"]
    full = bundle_root / rel_path
    if not full.is_file():
        return [f"missing required artifact: {label} not found at bundle path {rel_path!r}"]
    actual = _sha256_file(full)
    if actual != declared_sha256:
        return [f"{label} hash mismatch: declared={declared_sha256!r} actual={actual!r}"]
    return []


def _read_admission_raw(path: Path, thread_id: str) -> tuple[dict[str, object], list[str]] | None:
    """A minimal, standalone reread of the admission ledger's two tables, deliberately NOT
    importing ``langgraph_admission.read_admission``: this module must stay provably free of
    anything LangGraph-adjacent in its import graph, not merely free of a LangGraph import that
    happens not to fire yet, so it keeps working where LangGraph is not installed at all."""
    with sqlite3.connect(f"file:{path}?immutable=1", uri=True) as conn:
        row = conn.execute(
            "select input_json from accepted_runs where thread_id = ?", (thread_id,)
        ).fetchone()
        events = [
            str(r[0])
            for r in conn.execute(
                "select event from admission_events where thread_id = ? order by sequence",
                (thread_id,),
            ).fetchall()
        ]
    if row is None:
        return None
    decoded = json.loads(str(row[0]))
    return (decoded if isinstance(decoded, dict) else {}), events


def verify_bundle(bundle_root: Path) -> list[str]:
    """Return a list of problems; empty means the bundle is internally consistent. Never raises
    for incomplete or corrupt evidence - every expected failure mode becomes a problem string."""
    receipt_path = bundle_root / "receipt.json"
    if not receipt_path.is_file():
        return [f"missing required artifact: receipt.json not found under {bundle_root}"]
    try:
        record = json.loads(receipt_path.read_text())
    except json.JSONDecodeError as exc:
        return [f"receipt.json is not valid JSON: {exc}"]
    if not isinstance(record, dict):
        return ["receipt.json does not contain a JSON object"]

    problems = validate_receipt(record)
    if problems:
        return problems  # structural/type problems: the checks below assume the shape holds

    body = {k: v for k, v in record.items() if k != "receipt"}
    recomputed_receipt = canonical_receipt(body)
    if record.get("receipt") != recomputed_receipt:
        problems.append(
            f"receipt hash mismatch: recorded={record.get('receipt')!r} "
            f"recomputed={recomputed_receipt!r}"
        )

    contract = record["contract"]
    manifest = record["manifest"]
    admission = record["admission"]
    checkpoint = record["checkpoint"]
    observer = record["observer"]
    effect = record["effect"]
    identity = record["identity"]
    result = record["result"]
    runtime = record["runtime"]

    problems += _check_file_hash(bundle_root, contract["path"], contract["sha256"], "contract")
    problems += _check_file_hash(bundle_root, manifest["path"], manifest["sha256"], "manifest")
    problems += _check_file_hash(
        bundle_root, admission["admission_db_path"], admission["admission_db_sha256"],
        "admission db",
    )
    cp_path = checkpoint["checkpoint_db_path"]
    if checkpoint["checkpoint_db_sha256"]:
        problems += _check_file_hash(
            bundle_root, cp_path, checkpoint["checkpoint_db_sha256"], "checkpoint db"
        )

    for entry in record["source"]["new_source_files"]:
        if not isinstance(entry, dict):
            continue
        basename = Path(str(entry.get("path", ""))).name
        retained = bundle_root / "source" / basename
        if not retained.is_file():
            continue  # not every recorded source file is necessarily retained as a bundle copy
        actual = _sha256_file(retained)
        if actual != entry.get("sha256"):
            problems.append(
                f"retained source copy {basename} hash mismatch: "
                f"manifest={entry.get('sha256')!r} actual={actual!r}"
            )

    intent_id = identity["intent_id"]
    declared_status = observer["status"]
    archived_name = observer.get("archived_ledger_readback_path")

    if declared_status == "STORE_MISSING":
        if archived_name is not None:
            problems.append("observer.status is STORE_MISSING but an archived path is declared")
        if effect["observed_count"] is not None:
            problems.append("observer.status is STORE_MISSING but effect.observed_count is set")
    elif not archived_name:
        problems.append(f"observer.status is {declared_status!r} but no archived path is declared")
    else:
        archive_path = bundle_root / archived_name
        if not archive_path.is_file():
            problems.append(
                f"missing required artifact: declared archived readback {archived_name!r} "
                "not in bundle"
            )
        else:
            raw = archive_path.read_bytes()
            actual_sha256 = sha256_bytes(raw)
            declared_archived_sha256 = observer.get("archived_ledger_readback_sha256")
            if actual_sha256 != declared_archived_sha256:
                problems.append(
                    f"archived readback hash mismatch: declared={declared_archived_sha256!r} "
                    f"actual={actual_sha256!r}"
                )
            declared_original_sha256 = observer.get("original_readback_sha256")
            if actual_sha256 != declared_original_sha256:
                problems.append(
                    "archived readback does not match the declared original (pre-archive) "
                    f"hash: archived={actual_sha256!r} original={declared_original_sha256!r}"
                )

            try:
                readback = parse_ledger_bytes(raw)
            except LedgerBytesCorrupt as exc:
                if declared_status == "OBSERVED":
                    problems.append(
                        f"declared observer.status is OBSERVED but retained readback bytes "
                        f"are corrupt: {exc}"
                    )
            else:
                if declared_status == "OBSERVED":
                    if not readback.chain_valid:
                        problems.append(
                            "declared observer.status is OBSERVED but the recomputed hash "
                            "chain is invalid (first broken at index "
                            f"{readback.first_broken_index})"
                        )
                    if readback.chain_valid != observer.get("chain_valid"):
                        problems.append(
                            "observer.chain_valid does not match the recomputed chain validity"
                        )
                    recomputed_count = readback.effect_count(intent_id)
                    if recomputed_count != effect["observed_count"]:
                        problems.append(
                            f"effect.observed_count {effect['observed_count']!r} does not "
                            f"match count {recomputed_count!r} recomputed from retained bytes"
                        )
                    recomputed_digests = readback.effect_digests.get(intent_id, [])
                    if recomputed_digests != effect["effect_digests"]:
                        problems.append(
                            "effect.effect_digests does not match digests recomputed from "
                            "retained bytes"
                        )
                    recomputed_attempt_ids = readback.effect_attempt_ids.get(intent_id, [])
                    if recomputed_attempt_ids != effect["effect_attempt_ids"]:
                        problems.append(
                            "effect.effect_attempt_ids does not match references recomputed "
                            "from retained bytes"
                        )
                elif declared_status == "CORRUPT" and readback.chain_valid:
                    problems.append(
                        "declared observer.status is CORRUPT but the recomputed chain is valid"
                    )

    thread_id = identity["thread_id"]
    declared_cp_count = checkpoint["thread_scoped_checkpoint_count"]
    if not checkpoint["checkpoint_db_sha256"]:
        # No checkpoint db was ever produced (e.g. the worker never reached _build_app). The only
        # honest declared count in that case is 0 - there is no file to independently recompute
        # against, and requiring one here would be exactly the missing-vs-empty conflation this
        # control is built to avoid on the ledger side (see ledger_readback.py).
        if declared_cp_count != 0:
            problems.append(
                "checkpoint.checkpoint_db_sha256 is empty (no checkpoint db was produced) but "
                f"thread_scoped_checkpoint_count is {declared_cp_count!r}, not 0"
            )
    else:
        checkpoint_db_path = bundle_root / cp_path if isinstance(cp_path, str) else None
        if checkpoint_db_path is None or not checkpoint_db_path.is_file():
            problems.append(f"missing required artifact: checkpoint db {cp_path!r} not in bundle")
        else:
            try:
                with sqlite3.connect(f"file:{checkpoint_db_path}?immutable=1", uri=True) as conn:
                    row = conn.execute(
                        "select count(*) from checkpoints where thread_id = ?", (thread_id,)
                    ).fetchone()
                recomputed_cp_count = int(row[0]) if row is not None else 0
            except sqlite3.Error as exc:
                problems.append(f"could not independently read checkpoint db: {exc}")
            else:
                if recomputed_cp_count != declared_cp_count:
                    problems.append(
                        f"checkpoint.thread_scoped_checkpoint_count {declared_cp_count!r} does "
                        f"not match recomputed count {recomputed_cp_count!r}"
                    )

    admission_db_path = bundle_root / admission["admission_db_path"]
    if admission_db_path.is_file():
        try:
            admitted = _read_admission_raw(admission_db_path, thread_id)
        except sqlite3.Error as exc:
            problems.append(f"could not independently read admission db: {exc}")
            admitted = None
        if admitted is None:
            problems.append(f"no accepted_runs row for thread_id={thread_id!r} in admission db")
        else:
            recomputed_input, recomputed_events = admitted
            if recomputed_input != admission.get("original_input"):
                problems.append("admission.original_input does not match the retained db row")
            if recomputed_events != admission.get("events_after_run"):
                problems.append("admission.events_after_run does not match the retained db")
            recomputed_status = recomputed_events[-1] if recomputed_events else "missing"
            if recomputed_status != admission.get("status_after_run"):
                problems.append("admission.status_after_run does not match the retained db")

    is_list = isinstance(effect["effect_digests"], list)
    valid_digests = [d for d in effect["effect_digests"] if isinstance(d, str)] if is_list else []
    observed_count = effect["observed_count"]
    recomputed_classification = classify_control(
        worker_ok=bool(runtime["worker_ok"]),
        observer_status=str(observer["status"]),
        effect_count=observed_count if isinstance(observed_count, int) else None,
        effect_digests=valid_digests,
    )
    if recomputed_classification != result["oracle_classification"]:
        problems.append(
            f"result.oracle_classification {result['oracle_classification']!r} does not match "
            f"recomputed {recomputed_classification!r}"
        )
    recomputed_agreement = (
        recomputed_classification == "EXACTLY_ONCE"
        and admission.get("status_after_run") == "completed"
        and runtime["worker_returned_state"] == result["expected"].get("expected_terminal_state")
    )
    if recomputed_agreement != result["agreement"]:
        problems.append(
            f"result.agreement {result['agreement']!r} does not match recomputed "
            f"{recomputed_agreement!r}"
        )
    if result["passed"] and not recomputed_agreement:
        problems.append("result.passed is True but recomputed agreement is False")

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "bundle_root", type=Path, help="directory containing receipt.json and its artifacts"
    )
    args = parser.parse_args(argv)

    if not args.bundle_root.is_dir():
        print(f"FAIL: bundle root is not a directory: {args.bundle_root}", file=sys.stderr)
        return 2

    problems = verify_bundle(args.bundle_root)
    if problems:
        print(f"FAIL: {len(problems)} problem(s) found in {args.bundle_root}")
        for p in problems:
            print(f"  - {p}")
        return 1

    print(f"PASS: {args.bundle_root} is internally consistent with its retained evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
