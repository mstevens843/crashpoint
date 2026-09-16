"""Offline verifier for a LangGraph non-crash control bundle.

Runs without LangGraph installed or imported, and without launching any runtime: every check here
either re-derives a fact from bytes retained inside the bundle (the ledger readback, the checkpoint
database, the admission database, the contract, the manifest, the observer report, the source module
copies) or recomputes a hash. Nothing here trusts a summary written by the worker, the observer, or
the harness that produced the bundle - each of those numbers is independently recomputed from the
retained artifact and cross-checked against what ``receipt.json`` claims. This module and
``langgraph_control_receipt.py`` share ``compute_agreement`` so the verifier can never silently
drift from what the harness itself considers a passing bundle.

PORTABILITY AND CONFINEMENT. Every path this module opens is resolved through the single choke
point ``_resolve_in_bundle``, which requires the declared bundle-relative path to resolve (after
following any symlinks) to the bundle root or a descendant of it. A hash check alone is not a
confinement check: ``Path.__truediv__`` silently DISCARDS the left operand when the right one is
absolute (``bundle_root / "/etc/passwd"`` is ``/etc/passwd``, not an error), so an absolute
``receipt.contract.path`` could otherwise point anywhere on the reviewer's filesystem and still
hash-match. ``_resolve_in_bundle`` rejects an absolute declared path outright and rejects a
relative one (via ``..`` traversal or a symlink inside the bundle) that resolves outside the bundle
root - see ``tests/test_langgraph_control_verify.py``'s confinement tests. A legitimately relocated,
self-contained bundle (no absolute/escaping paths) still verifies identically after being copied
anywhere, with no absolute path from the original machine read or required.

ROBUSTNESS. Every step that touches a retained file (JSON parse, UTF-8 decode, SQLite open) is
wrapped so a malformed or unreadable artifact becomes a reported problem, never an uncaught
exception - see ``main``'s top-level safety net for defense in depth against anything still
unanticipated.

WHAT THIS DOES NOT PROVE. A PASS here means the retained evidence is internally consistent and
supports what ``receipt.json`` claims: identity/count/reference facts re-derived from raw ledger and
SQLite bytes are genuinely recomputed independently, but recorded runtime metadata (versions,
platform, timestamps, PIDs) and the outer receipt hash are checksum-consistency checks, not
independent proof those values are true. None of this is an independent rerun, live authentication
of the effect, or proof the original execution happened exactly as narrated. See ``receipt.json``'s
own ``limitations`` field.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from ..canonical import receipt as canonical_receipt
from .langgraph_control_receipt import (
    REQUIRED_EXECUTING_SOURCE_BASENAMES,
    classify_control,
    compute_agreement,
    validate_receipt,
)
from .ledger_readback import LedgerBytesCorrupt, parse_ledger_bytes, sha256_bytes


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve_in_bundle(
    bundle_root: Path, rel_path: object, label: str
) -> tuple[Path | None, list[str]]:
    """The one checked path-resolution boundary every artifact this module reads must go through.
    Rejects a declared path that is absolute, that escapes the bundle via ``..`` traversal, or
    that resolves (following symlinks, including a symlinked directory inside the bundle) outside
    the bundle root. Returns ``(resolved_path, [])`` on success or ``(None, [problem])`` on
    rejection - callers must check for ``None`` before using the path, never fall back to an
    unchecked join."""
    if not isinstance(rel_path, str) or not rel_path:
        return None, [f"{label}: declared path is not a non-empty string: {rel_path!r}"]
    candidate = Path(rel_path)
    if candidate.is_absolute():
        return None, [
            f"{label}: declared path must be relative to the bundle, got absolute {rel_path!r}"
        ]
    resolved_root = bundle_root.resolve()
    resolved_candidate = (bundle_root / candidate).resolve()
    if not resolved_candidate.is_relative_to(resolved_root):
        return None, [f"{label}: declared path {rel_path!r} resolves outside the bundle"]
    return resolved_candidate, []


def _read_json_file(path: Path, label: str) -> tuple[dict[str, Any] | None, list[str]]:
    """Read and parse one retained JSON artifact, turning every expected failure mode (missing
    file, unreadable file, invalid UTF-8, invalid JSON, not an object) into a problem string rather
    than letting it propagate. ``path`` must already be a confinement-checked, resolved path -
    callers go through ``_resolve_in_bundle`` first."""
    if not path.is_file():
        return None, [f"missing required artifact: {label} not found at {path}"]
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, [f"{label} could not be read: {exc}"]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, [f"{label} is not valid UTF-8: {exc}"]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, [f"{label} is not valid JSON: {exc}"]
    if not isinstance(parsed, dict):
        return None, [f"{label} does not contain a JSON object"]
    return parsed, []


def _check_file_hash(
    bundle_root: Path, rel_path: object, declared_sha256: object, label: str
) -> list[str]:
    resolved, problems = _resolve_in_bundle(bundle_root, rel_path, label)
    if resolved is None:
        return problems
    if not resolved.is_file():
        return [f"missing required artifact: {label} not found at bundle path {rel_path!r}"]
    try:
        actual = _sha256_file(resolved)
    except OSError as exc:
        return [f"{label} could not be read: {exc}"]
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


def _deep_equal_problems(actual: object, expected: object, label: str) -> list[str]:
    if actual != expected:
        return [f"{label} does not match the retained artifact's actual content"]
    return []


def verify_bundle(bundle_root: Path) -> list[str]:
    """Return a list of problems; empty means the bundle is internally consistent. Never raises
    for incomplete or corrupt evidence - every expected failure mode becomes a problem string."""
    record, problems = _read_json_file(bundle_root.resolve() / "receipt.json", "receipt.json")
    if record is None:
        return problems

    problems += validate_receipt(record)
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
    source = record["source"]

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

    # Parse and validate the RETAINED contract/manifest CONTENTS, not only their hashes: a hash
    # match only proves the file has not changed since it was hashed, not that what it says agrees
    # with what the receipt embeds as "expected" or "source".
    contract_resolved, contract_path_problems = _resolve_in_bundle(
        bundle_root, contract["path"], "contract.json"
    )
    problems += contract_path_problems
    contract_body = None
    if contract_resolved is not None:
        contract_body, contract_problems = _read_json_file(contract_resolved, "contract.json")
        problems += contract_problems
    if contract_body is not None:
        problems += _deep_equal_problems(contract_body, result["expected"], "result.expected")

    manifest_resolved, manifest_path_problems = _resolve_in_bundle(
        bundle_root, manifest["path"], "manifest.json"
    )
    problems += manifest_path_problems
    manifest_body = None
    if manifest_resolved is not None:
        manifest_body, manifest_problems = _read_json_file(manifest_resolved, "manifest.json")
        problems += manifest_problems
    if manifest_body is not None:
        for field, declared in (
            ("base_commit", source.get("base_commit")),
            ("worktree_branch", source.get("worktree_branch")),
            ("dirty", source.get("dirty")),
            ("executing_source_files", source.get("executing_source_files")),
            ("uv_lock_sha256", source.get("uv_lock_sha256")),
        ):
            if manifest_body.get(field) != declared:
                problems.append(
                    f"manifest.json's {field!r} does not match receipt.source.{field}"
                )
        if manifest_body.get("contract_sha256") != contract["sha256"]:
            problems.append(
                "manifest.json's contract_sha256 does not match receipt.contract.sha256"
            )

    # Require the five execution/verifier module copies and verify each one's hash from the
    # retained copy - removing one, or a required source-list entry, must reject, not silently
    # verify a smaller set.
    if isinstance(source["executing_source_files"], list):
        retained_basenames: set[str] = set()
        for entry in source["executing_source_files"]:
            if not isinstance(entry, dict):
                continue
            basename = Path(str(entry.get("path", ""))).name
            retained, source_path_problems = _resolve_in_bundle(
                bundle_root, f"source/{basename}", f"source copy {basename!r}"
            )
            if retained is None:
                problems += source_path_problems
                continue
            if not retained.is_file():
                problems.append(
                    f"missing required artifact: retained source copy {basename!r} not in bundle"
                )
                continue
            retained_basenames.add(basename)
            actual = _sha256_file(retained)
            if actual != entry.get("sha256"):
                problems.append(
                    f"retained source copy {basename} hash mismatch: "
                    f"manifest={entry.get('sha256')!r} actual={actual!r}"
                )
        missing_required = REQUIRED_EXECUTING_SOURCE_BASENAMES - retained_basenames
        if missing_required:
            problems.append(
                f"bundle source/ is missing required module(s): {sorted(missing_required)}"
            )

    intent_id = identity["intent_id"]
    declared_status = observer["status"]
    archived_name = observer.get("archived_ledger_readback_path")

    if declared_status == "STORE_MISSING":
        if archived_name is not None:
            problems.append("observer.status is STORE_MISSING but an archived path is declared")
        if effect["observed_count"] is not None:
            problems.append("observer.status is STORE_MISSING but effect.observed_count is set")
    elif declared_status == "OBSERVER_ERROR":
        # The observer may have failed (timeout, exception) before it ever archived anything of
        # its own - that is exactly what OBSERVER_ERROR means, so no archived path is required
        # here. A parent-process fallback copy, if one was taken, is checked separately below and
        # is never treated as equivalent to the observer's own archive.
        if effect["observed_count"] is not None:
            problems.append("observer.status is OBSERVER_ERROR but effect.observed_count is set")
    elif not archived_name:
        problems.append(f"observer.status is {declared_status!r} but no archived path is declared")
    else:
        archive_resolved, archive_path_problems = _resolve_in_bundle(
            bundle_root, archived_name, "archived readback"
        )
        if archive_resolved is None:
            problems += archive_path_problems
        elif not archive_resolved.is_file():
            problems.append(
                f"missing required artifact: declared archived readback {archived_name!r} "
                "not in bundle"
            )
        else:
            try:
                raw = archive_resolved.read_bytes()
            except OSError as exc:
                problems.append(f"archived readback could not be read: {exc}")
                raw = None
            if raw is not None:
                actual_sha256 = sha256_bytes(raw)
                declared_archived_sha256 = observer.get("archived_ledger_readback_sha256")
                if actual_sha256 != declared_archived_sha256:
                    problems.append(
                        f"archived readback hash mismatch: declared="
                        f"{declared_archived_sha256!r} actual={actual_sha256!r}"
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

    # Require and cross-check the observer's own retained report when one was declared: its
    # identity, status, count, references, and readback hashes must all agree with what the
    # receipt claims - never trust a summary without independently checking it, the same
    # discipline applied to the ledger above. report_path is null only when the observer process
    # never produced a report file at all (e.g. killed by timeout before writing one); that is
    # legitimate exactly when observer.status is OBSERVER_ERROR, checked separately.
    report_path_declared = observer.get("report_path")
    report_body = None
    if report_path_declared is None:
        if declared_status != "OBSERVER_ERROR":
            problems.append(
                f"observer.report_path is null but observer.status is {declared_status!r}, "
                "not OBSERVER_ERROR"
            )
    else:
        report_resolved, report_path_problems = _resolve_in_bundle(
            bundle_root, report_path_declared, "observer report"
        )
        if report_resolved is None:
            problems += report_path_problems
        else:
            # The declared hash must always match the retained bytes, regardless of whether the
            # content parses as a usable report - a bundle cannot claim an untampered report file
            # while quietly substituting different bytes for it.
            problems += _check_file_hash(
                bundle_root, observer["report_path"], observer["report_sha256"], "observer report"
            )
            parsed_report, read_problems = _read_json_file(report_resolved, "observer report")
            if parsed_report is None:
                if declared_status != "OBSERVER_ERROR":
                    # A malformed/unreadable/non-object report is only expected when status is
                    # OBSERVER_ERROR - the harness treats exactly this as unusable and falls back
                    # to it (see langgraph_control._validate_observer_report_shape). For any other
                    # status, a report that does not even parse as an object IS an inconsistency.
                    problems += read_problems
                # else: an unusable report file is exactly what OBSERVER_ERROR honestly means -
                # not a problem. The retained (bad) bytes are still hash-verified above.
            else:
                report_body = parsed_report
    if report_body is not None:
        for field, declared in (
            ("status", observer.get("status")),
            ("pid", observer.get("pid")),
            ("chain_valid", observer.get("chain_valid")),
            ("first_broken_index", observer.get("first_broken_index")),
            ("record_count", observer.get("record_count")),
            ("observed_count", effect.get("observed_count")),
            ("effect_digests", effect.get("effect_digests")),
            ("effect_attempt_ids", effect.get("effect_attempt_ids")),
            ("original_readback_sha256", observer.get("original_readback_sha256")),
            ("archived_ledger_readback_sha256", observer.get("archived_ledger_readback_sha256")),
            ("archive_matches_original", observer.get("archive_matches_original")),
            ("error", observer.get("error")),
        ):
            if field in report_body and report_body[field] != declared:
                problems.append(
                    f"observer report's {field!r} ({report_body[field]!r}) does not match "
                    f"receipt.observer/.effect ({declared!r})"
                )
        if report_body.get("intent_id") not in (None, intent_id):
            problems.append(
                f"observer report's intent_id {report_body.get('intent_id')!r} does not match "
                f"identity.intent_id {intent_id!r}"
            )

    # A parent-process fallback archive, when declared, is checked the same way as any other
    # retained artifact - but it never substitutes for the observer's own archive/report above:
    # its presence does not change observer.status or unlock a PASS on its own.
    fallback_path_declared = observer.get("parent_fallback_archive_path")
    fallback_sha_declared = observer.get("parent_fallback_archive_sha256")
    if fallback_path_declared is not None:
        problems += _check_file_hash(
            bundle_root, fallback_path_declared, fallback_sha_declared,
            "parent-process fallback archive",
        )

    thread_id = identity["thread_id"]
    declared_cp_count = checkpoint["thread_scoped_checkpoint_count"]
    declared_cp_error = checkpoint["checkpoint_error"]
    if not checkpoint["checkpoint_db_sha256"]:
        if declared_cp_count is not None:
            problems.append(
                "checkpoint.checkpoint_db_sha256 is empty (no checkpoint db was produced) but "
                f"thread_scoped_checkpoint_count is {declared_cp_count!r}, not null"
            )
    else:
        checkpoint_resolved, checkpoint_path_problems = _resolve_in_bundle(
            bundle_root, cp_path, "checkpoint db"
        )
        if checkpoint_resolved is None:
            problems += checkpoint_path_problems
        elif not checkpoint_resolved.is_file():
            problems.append(f"missing required artifact: checkpoint db {cp_path!r} not in bundle")
        else:
            try:
                with sqlite3.connect(f"file:{checkpoint_resolved}?immutable=1", uri=True) as conn:
                    row = conn.execute(
                        "select count(*) from checkpoints where thread_id = ?", (thread_id,)
                    ).fetchone()
                recomputed_cp_count: int | None = int(row[0]) if row is not None else 0
                recomputed_cp_error: str | None = None
            except sqlite3.Error as exc:
                recomputed_cp_count = None
                recomputed_cp_error = f"{type(exc).__name__}: {exc}"
            if recomputed_cp_count != declared_cp_count:
                problems.append(
                    f"checkpoint.thread_scoped_checkpoint_count {declared_cp_count!r} does "
                    f"not match recomputed count {recomputed_cp_count!r}"
                )
            if (recomputed_cp_error is None) != (declared_cp_error is None):
                problems.append(
                    f"checkpoint.checkpoint_error {declared_cp_error!r} does not match whether "
                    f"the checkpoint db was independently readable (recomputed error: "
                    f"{recomputed_cp_error!r})"
                )

    admission_resolved, admission_path_problems = _resolve_in_bundle(
        bundle_root, admission["admission_db_path"], "admission db"
    )
    if admission_resolved is None:
        problems += admission_path_problems
    elif admission_resolved.is_file():
        try:
            admitted = _read_admission_raw(admission_resolved, thread_id)
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
    recomputed_agreement = compute_agreement(
        classification=recomputed_classification,
        admission_status_after=admission.get("status_after_run"),
        admission_accepted_before_invoke=admission.get("accepted_before_invoke"),
        worker_exit_status=runtime.get("worker_exit_status"),
        observer_exit_status=observer.get("exit_status"),
        terminal_state=runtime.get("worker_returned_state"),
        expected_terminal_state=result["expected"].get("expected_terminal_state"),
        invoke_count=runtime.get("invoke_count"),
        expected_invoke_count=result["expected"].get("invoke_count"),
        checkpoint_count=declared_cp_count,
    )
    if recomputed_agreement != result["agreement"]:
        problems.append(
            f"result.agreement {result['agreement']!r} does not match recomputed "
            f"{recomputed_agreement!r}"
        )
    if result["passed"] and not recomputed_agreement:
        problems.append("result.passed is True but recomputed agreement is False")
    if result["passed"] and not bool(observer.get("archive_matches_original")):
        problems.append("result.passed is True but observer.archive_matches_original is not True")

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

    try:
        problems = verify_bundle(args.bundle_root)
    except Exception as exc:  # last-resort safety net: never a bare traceback from this CLI
        print(
            f"FAIL: verifier encountered an unexpected {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2

    if problems:
        print(f"FAIL: {len(problems)} problem(s) found in {args.bundle_root}")
        for p in problems:
            print(f"  - {p}")
        return 1

    print(f"PASS: {args.bundle_root} is internally consistent with its retained evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
