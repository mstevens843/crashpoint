"""Offline verifier for the SafeAgent TTL experiment's evidence bundle.

Requires neither an installed SafeAgent release nor a running ledger daemon: every check here
either re-parses raw bytes with the standard library (``sqlite3``, ``json``) or reuses
crashpoint's own dependency-free ``canonical`` hashing module. It is meant to be run against a
RELOCATED COPY of the bundle - every single file this module opens (not only the ``trial_dirs``
entry) is resolved and confirmed to stay under the bundle root; a fixed child filename that has
itself been replaced with a symlink pointing outside is rejected, not followed.

WHAT "VERIFIED" MEANS HERE, KEPT SEPARATE ON PURPOSE (see ``verify_bundle``'s return shape):

  - evidence complete/consistent: every required file for a trial's case is present, parses, and
    cross-checks with its receipt and with every other file that touches the same claim.
  - prediction matched: the receipt's ``observed_result`` matches the retained, embedded
    prediction's ``expected_result`` for that (case, release).
  - measured safety/task outcome: a trial matching its prediction is not the same claim as "the
    task safely completed" - a 0-effect PENDING trial can match its prediction and still mean the
    task never ran. ``passed`` never means more than "matched the prediction, with complete,
    internally- and cross-file-consistent evidence."

A contradiction is reported with the specific field/file it contradicts, not a single generic
checksum failure, so a reader - or a mutation test - can tell which claim broke. This module was
substantially expanded on 2026-09-19 after an independent review (recorded in
``handoff/safeagent-ttl/CORRECTIONS.md``) found the original version accepted a bundle missing
20 of its 30 claimed trials, a bundle whose protocol/summary/provenance fields were entirely
fabricated, a bundle with most of its retained evidence files deleted, and a ledger file replaced
with a symlink escaping the bundle - and crashed instead of reporting a malformed field.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from ..canonical import canonicalize, chain, receipt
from ..ledger.core import GENESIS
from .safeagent_ttl_receipt import CASES, RELEASES, derive_label, validate_receipt
from .safeagent_ttl_runtime import PREFIX

MANIFEST_SCHEMA = "crashpoint.safeagent_ttl.manifest.v1"
_EXPECTED_TRIALS_PER_CELL = 3
_EXPECTED_TOTAL_TRIALS = len(CASES) * len(RELEASES) * _EXPECTED_TRIALS_PER_CELL


# --------------------------------------------------------------------------------------------
# Ledger: independent re-parse of the raw JSONL bytes.
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LedgerObservation:
    status: str  # "MISSING" | "EMPTY_CONFIRMED" | "OK" | "TAMPERED" | "UNVERIFIED"
    raw_sha256: str | None
    byte_length: int | None
    broken_at_index: int | None
    attempts: dict[str, int] = field(default_factory=dict)
    side_effects: dict[str, int] = field(default_factory=dict)
    effect_digests: dict[str, list[str]] = field(default_factory=dict)
    effect_keys: dict[str, list[Any]] = field(default_factory=dict)
    attempt_ids_by_intent: dict[str, list[Any]] = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        return {
            "status": self.status,
            "raw_sha256": self.raw_sha256,
            "byte_length": self.byte_length,
            "broken_at_index": self.broken_at_index,
            "attempts": self.attempts,
            "side_effects": self.side_effects,
            "effect_digests": self.effect_digests,
            "effect_keys": self.effect_keys,
            "attempt_ids_by_intent": self.attempt_ids_by_intent,
        }


def observe_ledger(path: Path) -> LedgerObservation:
    """Independently parse retained ledger bytes through this process's own file handle.

    Missing bytes and a genuinely observed empty ledger are DIFFERENT statuses on purpose - a
    helper that treats a missing file as trivially empty would hide the difference between "the
    evidence was never captured" and "the ledger recorded zero crossings". Also validates each
    record's operation/action-ID schema and the ordering/uniqueness of attempt IDs, and confirms
    every crossing of one intent shares one constant payload digest unless it is genuinely
    DIVERGED - not just that a count matches."""
    if not path.exists():
        return LedgerObservation(
            status="MISSING", raw_sha256=None, byte_length=None, broken_at_index=None
        )
    raw = path.read_bytes()
    raw_sha256 = hashlib.sha256(raw).hexdigest()
    if len(raw) == 0:
        return LedgerObservation(
            status="EMPTY_CONFIRMED", raw_sha256=raw_sha256, byte_length=0, broken_at_index=None
        )

    head = GENESIS
    attempts: dict[str, int] = {}
    side_effects: dict[str, int] = {}
    effect_digests: dict[str, list[str]] = {}
    effect_keys: dict[str, list[Any]] = {}
    attempt_ids_by_intent: dict[str, list[Any]] = {}
    lines = raw.decode("utf-8").splitlines()
    if not lines:
        return LedgerObservation(
            status="EMPTY_CONFIRMED", raw_sha256=raw_sha256, byte_length=len(raw),
            broken_at_index=None,
        )

    for idx, line in enumerate(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            return LedgerObservation(
                status="TAMPERED", raw_sha256=raw_sha256, byte_length=len(raw),
                broken_at_index=idx,
            )
        if not isinstance(entry, dict) or entry.get("prev") != head:
            return LedgerObservation(
                status="TAMPERED", raw_sha256=raw_sha256, byte_length=len(raw),
                broken_at_index=idx,
            )
        record = entry.get("record", {})
        if not isinstance(record, dict):
            return LedgerObservation(
                status="TAMPERED", raw_sha256=raw_sha256, byte_length=len(raw),
                broken_at_index=idx,
            )
        expected_hash = chain(head, record)
        if entry.get("hash") != expected_hash:
            return LedgerObservation(
                status="TAMPERED", raw_sha256=raw_sha256, byte_length=len(raw),
                broken_at_index=idx,
            )
        head = expected_hash
        if record.get("op") == "execute":
            intent_raw = record.get("intent_id")
            if not isinstance(intent_raw, str) or not intent_raw:
                return LedgerObservation(
                    status="TAMPERED", raw_sha256=raw_sha256, byte_length=len(raw),
                    broken_at_index=idx,
                )
            intent = intent_raw
            attempt_id = record.get("attempt_id")
            prior_attempt_ids = attempt_ids_by_intent.get(intent, [])
            if attempt_id is not None and attempt_id in prior_attempt_ids:
                # Attempt IDs must be distinct and, by this experiment's own construction,
                # strictly ordered (worker-a before worker-b-retry); a repeat is a schema
                # violation, not a legitimate second crossing.
                return LedgerObservation(
                    status="TAMPERED", raw_sha256=raw_sha256, byte_length=len(raw),
                    broken_at_index=idx,
                )
            attempts[intent] = attempts.get(intent, 0) + 1
            attempt_ids_by_intent.setdefault(intent, []).append(attempt_id)
            if not record.get("deduped", False):
                digest = record.get("payload_digest")
                if not isinstance(digest, str) or not digest:
                    return LedgerObservation(
                        status="TAMPERED", raw_sha256=raw_sha256, byte_length=len(raw),
                        broken_at_index=idx,
                    )
                side_effects[intent] = side_effects.get(intent, 0) + 1
                effect_digests.setdefault(intent, []).append(digest)
                effect_keys.setdefault(intent, []).append(record.get("key"))

    return LedgerObservation(
        status="OK", raw_sha256=raw_sha256, byte_length=len(raw), broken_at_index=None,
        attempts=attempts, side_effects=side_effects, effect_digests=effect_digests,
        effect_keys=effect_keys, attempt_ids_by_intent=attempt_ids_by_intent,
    )


def classify_effect_count(intent_id: str, obs: LedgerObservation) -> str:
    """The same EXACTLY_ONCE/DUPLICATED/DIVERGED vocabulary as ``ledger.oracle.classify``,
    applied only where it unambiguously fits (count >= 1) or where the chain itself is broken
    (VOID). Zero effects is reported as ZERO, never EXACTLY_ONCE - crashpoint's oracle has no
    term for "correctly withheld, not a loss", and inventing one by reusing EXACTLY_ONCE would
    misreport an unperformed task as a success."""
    if obs.status == "TAMPERED":
        return "VOID"
    if obs.status in {"MISSING", "UNVERIFIED"}:
        return "UNVERIFIED"
    if obs.status == "EMPTY_CONFIRMED":
        return "ZERO"
    n = obs.side_effects.get(intent_id, 0)
    if n == 0:
        return "ZERO"
    if n == 1:
        return "EXACTLY_ONCE"
    digests = obs.effect_digests.get(intent_id, [])
    if len(digests) != n:
        return "VOID"
    return "DIVERGED" if len(set(digests)) > 1 else "DUPLICATED"


# --------------------------------------------------------------------------------------------
# Claim store: raw sqlite3 read of a retained snapshot. No SafeAgent import.
# --------------------------------------------------------------------------------------------


def read_claim_row(snapshot_path: Path, action_id: str) -> dict[str, object] | None:
    """Read ``execution_requests`` directly. Returns None if the snapshot is missing, the table
    doesn't exist, or no row matches - each distinguishable via the caller checking
    ``snapshot_path.exists()`` first when that distinction matters."""
    if not snapshot_path.exists():
        return None
    conn = sqlite3.connect(f"file:{snapshot_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM execution_requests WHERE request_id = ?", (action_id,)
            ).fetchone()
        except sqlite3.DatabaseError:
            # Covers both "no such table" and a genuinely corrupt SQLite file - a missing table
            # is not proof of an absent claim row, so this is surfaced by the caller as
            # `row_read_error`, not silently folded into "no row".
            return None
        if row is None:
            return None
        out = dict(row)
        if out.get("result"):
            try:
                out["result"] = json.loads(cast(str, out["result"]))
            except (json.JSONDecodeError, TypeError):
                out["result"] = {"__malformed_result_json__": out["result"]}
        return out
    finally:
        conn.close()


def _claim_snapshot_readable(snapshot_path: Path) -> bool:
    """True iff the file exists and is a queryable SQLite database (possibly with zero rows).
    Used to distinguish 'file missing/not evidence' from 'file present but unreadable/corrupt'."""
    if not snapshot_path.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{snapshot_path}?mode=ro", uri=True)
        try:
            conn.execute("PRAGMA schema_version").fetchone()
            return True
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return False


# --------------------------------------------------------------------------------------------
# Bundle-level verification.
# --------------------------------------------------------------------------------------------


class BundleEscape(ValueError):
    """A path (a trial_dirs entry, or a fixed child filename within one) tried to reference
    something outside the bundle root, including via a symlink."""


def _safe_join(root: Path, rel: str) -> Path:
    """Confine a manifest-declared relative path. Rejects a resolved absolute escape, a `..`
    walk-out, or a symlink whose target resolves outside the root."""
    if Path(rel).is_absolute():
        raise BundleEscape(f"path {rel!r} is absolute, not relative to the bundle root")
    candidate = (root / rel).resolve()
    root_resolved = root.resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise BundleEscape(f"path {rel!r} resolves outside the bundle root") from exc
    return candidate


def _confined_file(bundle_root: Path, trial_dir: Path, filename: str) -> Path:
    """Confine one FIXED CHILD FILENAME inside an already-confined trial_dir. A trial_dir being
    safe does not make everything under it safe - the file itself can be a symlink."""
    candidate = trial_dir / filename
    resolved = candidate.resolve()
    root_resolved = bundle_root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise BundleEscape(
            f"{filename!r} in trial dir {trial_dir} resolves outside the bundle root "
            f"({resolved}) - rejected before opening"
        ) from exc
    return resolved


@dataclass
class Problem:
    trial_id: str | None
    reason: str

    def to_json(self) -> dict[str, str | None]:
        return {"trial_id": self.trial_id, "reason": self.reason}


# Evidence files a complete trial of each case must retain, beyond the always-required
# receipt.json/ledger.jsonl/ledger_observation.json/claim_final.sqlite/worker_b.stdout.
_REQUIRED_EXTRA_FILES_BY_CASE: dict[str, tuple[str, ...]] = {
    "settled_control": ("worker_a.stdout", "claim_post_settle.sqlite", "claim_post_sweep.sqlite"),
    "pending_before_ttl": (
        "worker_a.stdout", "claim_pre_kill.sqlite", "claim_post_kill.sqlite",
        "claim_post_sweep.sqlite",
    ),
    "pending_expired_no_sweep": (
        "worker_a.stdout", "claim_pre_kill.sqlite", "claim_post_kill.sqlite",
    ),
    "pending_expired_swept": (
        "worker_a.stdout", "claim_pre_kill.sqlite", "claim_post_kill.sqlite",
        "claim_pre_sweep.sqlite", "claim_post_sweep.sqlite",
    ),
    "pre_effect_expired_swept": (
        "worker_a.stdout", "claim_pre_kill.sqlite", "claim_post_kill.sqlite",
        "claim_pre_sweep.sqlite", "claim_post_sweep.sqlite",
    ),
}
_ALWAYS_REQUIRED_FILES = (
    "receipt.json", "ledger.jsonl", "ledger_observation.json", "claim_final.sqlite",
    "worker_b.stdout",
)


def _parse_worker_events(text: str) -> list[dict[str, Any]]:
    """Retained worker stdout comes in two shapes this experiment's own runner produces: raw
    subprocess capture (PREFIX-marked lines, for settled_control and Worker B) and a
    kill-based Worker A's re-serialized already-parsed event list (bare JSON lines, no PREFIX,
    because the barrier-capture loop strips it before the kill). Both are accepted."""
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        payload = line[len(PREFIX) :] if line.startswith(PREFIX) else line
        payload = payload.strip()
        if not payload:
            continue
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


def _find_event(events: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for e in reversed(events):
        if e.get("event") == name:
            return e
    return None


def _expected_post_sweep_row_present(case: str, release: str) -> bool:
    """Whether the row should still exist in claim_post_sweep.sqlite. False means sweep should
    have deleted it (0.1.23's stale-PENDING delete, on the two cases that actually cross expiry
    and sweep); True means it must still be there (nothing was stale, or 0.1.24's sweep is a
    no-op either way)."""
    if case in {"pending_before_ttl"}:
        return True  # nothing was stale yet; sweep is a no-op on both releases
    if case in {"pending_expired_swept", "pre_effect_expired_swept"}:
        return release != "0.1.23"
    return True  # pending_expired_no_sweep never snapshots post-sweep; unreachable in practice


def _type_check(
    trial: dict[str, Any], field_name: str, expected: type | tuple[type, ...], *,
    allow_none: bool = True,
) -> str | None:
    value = trial.get(field_name)
    if value is None:
        return None if allow_none else f"{field_name} is null but must not be"
    if isinstance(value, bool) and expected is not bool and not (
        isinstance(expected, tuple) and bool in expected
    ):
        return f"{field_name} must be {expected}, got a bool ({value!r})"
    if not isinstance(value, expected):
        return f"{field_name} must be {expected}, got {type(value).__name__} ({value!r})"
    return None


def verify_bundle(bundle_root: Path) -> dict[str, Any]:
    problems: list[Problem] = []
    manifest_path = bundle_root / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.exists():
        return {
            "bundle_root": str(bundle_root),
            "manifest_found": False,
            "manifest_receipt_valid": False,
            "trial_count": 0,
            "problems": [
                Problem(None, "manifest.json not found (or is a symlink) in bundle root").to_json()
            ],
        }
    try:
        manifest = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as exc:
        return {
            "bundle_root": str(bundle_root),
            "manifest_found": True,
            "manifest_receipt_valid": False,
            "trial_count": 0,
            "problems": [Problem(None, f"manifest.json is not valid JSON: {exc}").to_json()],
        }
    if not isinstance(manifest, dict):
        return {
            "bundle_root": str(bundle_root),
            "manifest_found": True,
            "manifest_receipt_valid": False,
            "trial_count": 0,
            "problems": [Problem(None, "manifest.json top level is not a JSON object").to_json()],
        }

    if manifest.get("schema") != MANIFEST_SCHEMA:
        problems.append(Problem(None, f"unexpected manifest schema: {manifest.get('schema')!r}"))
    if manifest.get("status") != "COMPLETE":
        problems.append(
            Problem(None, f"manifest status is {manifest.get('status')!r}, not COMPLETE: an "
                          "incomplete/aborted run cannot be a publication-ready bundle")
        )
    if manifest.get("execution_failures"):
        problems.append(
            Problem(None, f"manifest records execution_failures: {manifest['execution_failures']}")
        )

    body = dict(manifest)
    recorded_receipt = body.pop("receipt", None)
    recomputed_receipt = receipt(body)
    manifest_receipt_valid = recorded_receipt == recomputed_receipt
    if not manifest_receipt_valid:
        problems.append(
            Problem(
                None,
                f"manifest receipt mismatch: recorded={recorded_receipt!r} "
                f"recomputed={recomputed_receipt!r} (the manifest body was edited after hashing)",
            )
        )

    # --- provenance: embedded prediction bytes, bound to prediction_sha256 and to every trial's
    # expected_result, not merely a per-trial copy trusted on its own word ---
    prediction: dict[str, Any] | None = None
    try:
        prediction_rel = manifest.get("prediction_embedded_path", "prediction.json")
        prediction_path = _safe_join(bundle_root, prediction_rel)
    except BundleEscape as exc:
        problems.append(Problem(None, str(exc)))
        prediction_path = None
    if prediction_path is not None:
        if not prediction_path.exists():
            problems.append(Problem(None, "embedded prediction.json is missing from the bundle"))
        else:
            prediction_bytes = prediction_path.read_bytes()
            prediction_hash = hashlib.sha256(prediction_bytes).hexdigest()
            if prediction_hash != manifest.get("prediction_embedded_sha256"):
                problems.append(
                    Problem(None, f"embedded prediction.json sha256={prediction_hash} does not "
                                  f"match manifest.prediction_embedded_sha256="
                                  f"{manifest.get('prediction_embedded_sha256')!r}")
                )
            if prediction_hash != manifest.get("prediction_sha256"):
                declared = manifest.get("prediction_sha256")
                problems.append(
                    Problem(None, f"embedded prediction sha256={prediction_hash} does not "
                                  f"match manifest.prediction_sha256={declared!r}: the bundle's "
                                  "retained prediction and its declared hash disagree")
                )
            try:
                prediction = json.loads(prediction_bytes)
            except json.JSONDecodeError:
                problems.append(Problem(None, "embedded prediction.json is not valid JSON"))
                prediction = None

    # --- provenance: release_info sanity + embedded source hashes ---
    release_info = manifest.get("release_info")
    if not isinstance(release_info, dict) or set(release_info) != set(RELEASES):
        problems.append(
            Problem(None, f"release_info must have exactly the keys {list(RELEASES)}, got "
                          f"{release_info!r}")
        )
        release_info = {}
    for rel, info in release_info.items():
        if not isinstance(info, dict):
            problems.append(Problem(None, f"release_info[{rel!r}] is not an object"))
            continue
        if info.get("version") != rel:
            problems.append(
                Problem(None, f"release_info[{rel!r}].version={info.get('version')!r} does not "
                              f"match the release key")
            )
        for hash_field in ("wheel_sha256", "sqlite_store_sha256"):
            v = info.get(hash_field)
            if not isinstance(v, str) or len(v) != 64:
                problems.append(
                    Problem(None, f"release_info[{rel!r}].{hash_field} is not a 64-hex-char "
                                  f"sha256 string: {v!r}")
                )

    embedded_hashes = manifest.get("embedded_source_sha256")
    if not isinstance(embedded_hashes, dict) or not embedded_hashes:
        problems.append(Problem(None, "manifest.embedded_source_sha256 is missing or empty"))
        embedded_hashes = {}
    for rel_path, claimed_hash in embedded_hashes.items():
        try:
            src_path = _safe_join(bundle_root, f"sources/{rel_path}")
        except BundleEscape as exc:
            problems.append(Problem(None, str(exc)))
            continue
        if not src_path.exists():
            problems.append(Problem(None, f"embedded source missing: sources/{rel_path}"))
            continue
        actual_hash = hashlib.sha256(src_path.read_bytes()).hexdigest()
        if actual_hash != claimed_hash:
            problems.append(
                Problem(None, f"embedded source sources/{rel_path} sha256={actual_hash} does not "
                              f"match manifest.embedded_source_sha256={claimed_hash!r}")
            )
    for rel in RELEASES:
        info = release_info.get(rel, {})
        claimed_module_hash = info.get("sqlite_store_sha256") if isinstance(info, dict) else None
        embedded_module_hash = embedded_hashes.get(f"safeagent_exec_guard/{rel}/sqlite_store.py")
        if claimed_module_hash is not None and embedded_module_hash is not None:
            if claimed_module_hash != embedded_module_hash:
                problems.append(
                    Problem(None, f"release_info[{rel!r}].sqlite_store_sha256 does not match the "
                                  "hash of the embedded sqlite_store.py source for that release")
                )
        elif embedded_module_hash is None:
            problems.append(
                Problem(None, f"no embedded sqlite_store.py source retained for release {rel!r}")
            )

    trial_dirs = manifest.get("trial_dirs", {})
    trials = manifest.get("trials", [])
    if not isinstance(trials, list):
        problems.append(Problem(None, "manifest.trials is not a list"))
        trials = []
    cells: dict[str, dict[str, Any]] = {}
    cell_indices: dict[str, set[Any]] = {}

    for trial in trials:
        if not isinstance(trial, dict):
            problems.append(Problem(None, f"a trial entry is not an object: {trial!r}"))
            continue
        trial_id = trial.get("trial_id")
        case = trial.get("case")
        rel = trial.get("release")
        cell_key = f"{case}|{rel}"
        cell = cells.setdefault(
            cell_key, {"case": case, "release": rel, "count": 0, "labels": {}, "effect_counts": []}
        )
        cell["count"] += 1
        cell["labels"][trial.get("task_completion_label")] = (
            cell["labels"].get(trial.get("task_completion_label"), 0) + 1
        )
        cell["effect_counts"].append(trial.get("effect_count"))
        trial_index = trial_id.rsplit("-", 1)[-1] if isinstance(trial_id, str) else None
        cell_indices.setdefault(cell_key, set()).add(trial_index)

        schema_problems = validate_receipt(trial)
        for p in schema_problems:
            problems.append(Problem(trial_id, f"receipt schema: {p}"))
        if schema_problems:
            continue  # cross-file checks below assume the receipt shape is at least sane

        for tcheck in (
            _type_check(trial, "action_id", str, allow_none=False),
            _type_check(trial, "effect_count", int),
            _type_check(trial, "worker_a_killed", bool, allow_none=False),
            _type_check(trial, "barrier_observed", bool, allow_none=False),
            _type_check(trial, "observation_complete", bool, allow_none=False),
            _type_check(trial, "passed", bool, allow_none=False),
            _type_check(trial, "final_claim_row", dict),
            _type_check(trial, "observed_result", dict, allow_none=False),
            _type_check(trial, "expected_result", dict, allow_none=False),
        ):
            if tcheck:
                problems.append(Problem(trial_id, f"malformed field: {tcheck}"))
        if any(
            _type_check(trial, f, t) for f, t in (
                ("action_id", str), ("effect_count", int), ("worker_a_killed", bool),
                ("barrier_observed", bool), ("observation_complete", bool), ("passed", bool),
            )
        ):
            continue  # a malformed core field makes every downstream comparison meaningless

        action_id = cast(str, trial["action_id"])

        rel_dir = trial_dirs.get(trial_id) if isinstance(trial_dirs, dict) else None
        if not isinstance(rel_dir, str):
            problems.append(Problem(trial_id, "no trial_dirs entry for this trial_id"))
            continue
        try:
            trial_dir = _safe_join(bundle_root, rel_dir)
        except BundleEscape as exc:
            problems.append(Problem(trial_id, str(exc)))
            continue
        if not trial_dir.is_dir():
            problems.append(Problem(trial_id, f"trial directory missing: {rel_dir}"))
            continue

        required_files = _ALWAYS_REQUIRED_FILES + _REQUIRED_EXTRA_FILES_BY_CASE.get(
            cast(str, case), ()
        )
        confined: dict[str, Path] = {}
        for filename in required_files:
            try:
                confined_path = _confined_file(bundle_root, trial_dir, filename)
            except BundleEscape as exc:
                problems.append(Problem(trial_id, str(exc)))
                continue
            if confined_path.is_symlink():
                problems.append(
                    Problem(trial_id, f"{filename} is a symlink; rejected before opening")
                )
                continue
            if not confined_path.exists():
                problems.append(Problem(trial_id, f"missing required evidence file: {filename}"))
                continue
            confined[filename] = confined_path

        if "receipt.json" in confined:
            try:
                on_disk_receipt = json.loads(confined["receipt.json"].read_text())
            except json.JSONDecodeError:
                problems.append(Problem(trial_id, "trial receipt.json is not valid JSON"))
                on_disk_receipt = None
            if on_disk_receipt is not None and on_disk_receipt != trial:
                problems.append(
                    Problem(trial_id, "manifest's embedded trial record differs from the "
                                       "trial's own retained receipt.json")
                )

        # --- independent ledger re-parse ---
        obs: LedgerObservation | None = None
        recomputed_count: int | None = None
        recomputed_classification = "UNVERIFIED"
        if "ledger.jsonl" in confined:
            obs = observe_ledger(confined["ledger.jsonl"])
            if obs.status in {"OK", "EMPTY_CONFIRMED"}:
                recomputed_count = obs.side_effects.get(action_id, 0)
            recomputed_classification = classify_effect_count(action_id, obs)
            stored_count = trial.get("effect_count")
            if recomputed_count != stored_count:
                problems.append(
                    Problem(
                        trial_id,
                        f"effect_count contradicts raw ledger bytes: receipt says "
                        f"{stored_count!r}, independent re-parse of {rel_dir}/ledger.jsonl says "
                        f"{recomputed_count!r} (ledger status={obs.status})",
                    )
                )
            if recomputed_classification != trial.get("effect_ledger_classification"):
                problems.append(
                    Problem(
                        trial_id,
                        f"effect_ledger_classification contradicts raw ledger bytes: receipt "
                        f"says {trial.get('effect_ledger_classification')!r}, recomputed "
                        f"{recomputed_classification!r}",
                    )
                )

        if "ledger_observation.json" in confined and obs is not None:
            try:
                saved_obs = json.loads(confined["ledger_observation.json"].read_text())
            except json.JSONDecodeError:
                problems.append(Problem(trial_id, "ledger_observation.json is not valid JSON"))
                saved_obs = None
            if saved_obs is not None and (
                saved_obs.get("raw_sha256") != obs.raw_sha256
                or saved_obs.get("byte_length") != obs.byte_length
            ):
                problems.append(
                    Problem(trial_id, "saved ledger_observation.json does not match the raw "
                                       "ledger.jsonl bytes retained alongside it")
                )

        # --- independent claim-store re-read: final, plus every case-specific snapshot ---
        recomputed_row: dict[str, object] | None = None
        if "claim_final.sqlite" in confined:
            if not _claim_snapshot_readable(confined["claim_final.sqlite"]):
                problems.append(Problem(trial_id, "claim_final.sqlite exists but is not a "
                                                   "readable SQLite database"))
            recomputed_row = read_claim_row(confined["claim_final.sqlite"], action_id)
            stored_row = trial.get("final_claim_row")
            if _normalize_row(recomputed_row) != _normalize_row(stored_row):
                problems.append(
                    Problem(
                        trial_id,
                        f"final_claim_row contradicts claim_final.sqlite: receipt says "
                        f"{stored_row!r}, snapshot read gives {recomputed_row!r}",
                    )
                )
            if recomputed_row is not None and recomputed_row.get("request_id") != action_id:
                problems.append(
                    Problem(
                        trial_id,
                        f"action ID mismatch: receipt action_id={action_id!r}, "
                        f"claim_final.sqlite row request_id="
                        f"{recomputed_row.get('request_id')!r}",
                    )
                )

        receipt_claimed_at = trial.get("claimed_at")
        for snap_name in ("claim_pre_kill.sqlite", "claim_post_kill.sqlite"):
            if snap_name in confined:
                row = read_claim_row(confined[snap_name], action_id)
                if row is None or row.get("status") != "PENDING":
                    problems.append(
                        Problem(trial_id, f"{snap_name} does not show a PENDING row for "
                                           f"{action_id!r} (row={row!r})")
                    )
                elif (
                    receipt_claimed_at is not None
                    and row.get("claimed_at") != receipt_claimed_at
                ):
                    problems.append(
                        Problem(trial_id, f"{snap_name} claimed_at={row.get('claimed_at')!r} "
                                           f"disagrees with receipt claimed_at="
                                           f"{receipt_claimed_at!r}")
                    )
        if "claim_pre_sweep.sqlite" in confined:
            row = read_claim_row(confined["claim_pre_sweep.sqlite"], action_id)
            if row is None or row.get("status") != "PENDING":
                problems.append(
                    Problem(trial_id, f"claim_pre_sweep.sqlite does not show a PENDING row "
                                       f"(row={row!r})")
                )
        if "claim_post_sweep.sqlite" in confined and case in _REQUIRED_EXTRA_FILES_BY_CASE:
            row = read_claim_row(confined["claim_post_sweep.sqlite"], action_id)
            should_be_present = _expected_post_sweep_row_present(cast(str, case), cast(str, rel))
            if should_be_present and (row is None or row.get("status") != "PENDING"):
                problems.append(
                    Problem(trial_id, f"claim_post_sweep.sqlite expected a PENDING row for "
                                       f"case={case!r} release={rel!r} but found {row!r}")
                )
            if not should_be_present and row is not None:
                problems.append(
                    Problem(trial_id, f"claim_post_sweep.sqlite expected sweep to have deleted "
                                       f"the row for case={case!r} release={rel!r} (0.1.23 "
                                       f"stale-delete) but found {row!r}")
                )
        if "claim_post_settle.sqlite" in confined:
            row = read_claim_row(confined["claim_post_settle.sqlite"], action_id)
            if row is None or row.get("status") != "COMMITTED":
                problems.append(
                    Problem(trial_id, f"claim_post_settle.sqlite does not show a COMMITTED row "
                                       f"(row={row!r})")
                )

        # --- cross-check protocol claims against retained raw worker stdout, independent of
        # the receipt that makes the same claims ---
        if "worker_a.stdout" in confined:
            a_events = _parse_worker_events(confined["worker_a.stdout"].read_text())
            started = _find_event(a_events, "worker_started")
            if started is not None and started.get("pid") != trial.get("worker_a_pid"):
                problems.append(
                    Problem(trial_id, f"worker_a_pid={trial.get('worker_a_pid')!r} disagrees "
                                       f"with retained worker_a.stdout worker_started.pid="
                                       f"{started.get('pid')!r}")
                )
            barrier_evt = _find_event(a_events, "barrier")
            settled_evt = _find_event(a_events, "settled")
            if case == "settled_control":
                if trial.get("passed") is True and settled_evt is None:
                    problems.append(
                        Problem(trial_id, "passed=true but retained worker_a.stdout shows no "
                                           "'settled' event")
                    )
            else:
                if trial.get("barrier_observed") is True and barrier_evt is None:
                    problems.append(
                        Problem(trial_id, "barrier_observed=true but retained worker_a.stdout "
                                           "shows no 'barrier' event")
                    )
                if (
                    barrier_evt is not None
                    and barrier_evt.get("point") != trial.get("barrier_point")
                ):
                    problems.append(
                        Problem(trial_id, f"barrier_point={trial.get('barrier_point')!r} "
                                           f"disagrees with retained barrier event point="
                                           f"{barrier_evt.get('point')!r}")
                    )
                if trial.get("passed") is True and trial.get("worker_a_killed") is not True:
                    problems.append(
                        Problem(trial_id, "passed=true on a kill-based case but "
                                           "worker_a_killed is not true")
                    )

        if "worker_b.stdout" in confined:
            b_events = _parse_worker_events(confined["worker_b.stdout"].read_text())
            claim_evt = _find_event(b_events, "retry_claim")
            receipt_admitted = trial.get("retry_claim_admitted")
            if claim_evt is not None and claim_evt.get("admitted") != receipt_admitted:
                problems.append(
                    Problem(trial_id, f"retry_claim_admitted={receipt_admitted!r} disagrees "
                                       f"with retained worker_b.stdout retry_claim.admitted="
                                       f"{claim_evt.get('admitted')!r}")
                )
            complete_evt = _find_event(b_events, "retry_complete")
            if complete_evt is not None:
                if complete_evt.get("performed_effect") != trial.get("retry_performed_effect"):
                    problems.append(
                        Problem(trial_id, "retry_performed_effect disagrees with retained "
                                           "worker_b.stdout retry_complete.performed_effect")
                    )
                if _normalize_row(complete_evt.get("final_row")) != _normalize_row(
                    trial.get("final_claim_row")
                ):
                    problems.append(
                        Problem(trial_id, "final_claim_row disagrees with retained "
                                           "worker_b.stdout retry_complete.final_row")
                    )
            elif trial.get("passed") is True:
                problems.append(
                    Problem(trial_id, "passed=true but retained worker_b.stdout shows no "
                                       "'retry_complete' event")
                )

        # --- TTL-age consistency, recomputed from claimed_at/config, not trusted from the
        # ttl_boundary_confirmed boolean; these are locally recorded timestamps, not externally
        # attested time, and are only ever checked for internal consistency here ---
        ttl_seconds = manifest.get("pending_ttl_seconds", {})
        expected_ttl = (
            ttl_seconds.get("pending_before_ttl")
            if case == "pending_before_ttl"
            else ttl_seconds.get("default")
        )
        measured_age = trial.get("measured_age_at_decision")
        if (
            isinstance(expected_ttl, (int, float))
            and isinstance(measured_age, (int, float))
            and not isinstance(measured_age, bool)
        ):
            if case == "pending_before_ttl":
                if measured_age >= expected_ttl and trial.get("passed") is True:
                    problems.append(
                        Problem(trial_id, f"case requires age < ttl before decision, but "
                                           f"measured_age_at_decision={measured_age} >= "
                                           f"ttl={expected_ttl}")
                    )
            elif measured_age < expected_ttl and trial.get("passed") is True:
                problems.append(
                    Problem(trial_id, f"case requires age >= ttl at decision, but "
                                       f"measured_age_at_decision={measured_age} < "
                                       f"ttl={expected_ttl}")
                )

        # --- recompute the summary label, observed_result and passed from the (independently
        # recomputed) raw inputs, bound to the retained prediction, not the receipt's own claim
        # ---
        recomputed_status = recomputed_row.get("status") if recomputed_row is not None else None
        recomputed_label = derive_label(
            observation_complete=bool(trial.get("observation_complete")),
            effect_count=recomputed_count,
            retry_claim_admitted=cast(bool | None, trial.get("retry_claim_admitted")),
            final_status=recomputed_status if isinstance(recomputed_status, str) else None,
        )
        if recomputed_label != trial.get("task_completion_label"):
            problems.append(
                Problem(
                    trial_id,
                    f"task_completion_label contradicts recomputed inputs: receipt says "
                    f"{trial.get('task_completion_label')!r}, recomputed {recomputed_label!r}",
                )
            )
        recomputed_observed = {
            "retry_claim_admitted": trial.get("retry_claim_admitted"),
            "effect_count": recomputed_count,
            "effect_ledger_classification": recomputed_classification,
            "final_claim_status": recomputed_status,
            "task_completion_label": recomputed_label,
        }
        if recomputed_observed != trial.get("observed_result"):
            problems.append(
                Problem(trial_id, f"observed_result contradicts recomputed inputs: receipt says "
                                   f"{trial.get('observed_result')!r}, recomputed "
                                   f"{recomputed_observed!r}")
            )
        if prediction is not None:
            try:
                retained_expected = prediction["cases"][case][rel]
            except (KeyError, TypeError):
                retained_expected = None
                problems.append(
                    Problem(trial_id, f"no prediction entry for case={case!r} release={rel!r} "
                                       "in the retained, embedded prediction")
                )
            if retained_expected is not None and retained_expected != trial.get("expected_result"):
                problems.append(
                    Problem(trial_id, f"expected_result={trial.get('expected_result')!r} does "
                                       f"not match the retained prediction's entry "
                                       f"{retained_expected!r} for this case/release")
                )
            if retained_expected is not None:
                recomputed_passed = (
                    bool(trial.get("observation_complete"))
                    and trial.get("invalid_reason") is None
                    and recomputed_observed == retained_expected
                )
                if recomputed_passed != bool(trial.get("passed")):
                    problems.append(
                        Problem(trial_id, f"passed={trial.get('passed')!r} disagrees with "
                                           f"recomputed evaluation against the retained "
                                           f"prediction ({recomputed_passed!r})")
                    )

    expected_cells = len(CASES) * len(RELEASES)
    if len(cells) != expected_cells:
        problems.append(
            Problem(
                None,
                f"expected {expected_cells} (case, release) cells, found {len(cells)}: "
                f"{sorted(cells)}",
            )
        )
    for cell_key, cell in cells.items():
        if cell["count"] != _EXPECTED_TRIALS_PER_CELL:
            problems.append(
                Problem(None, f"cell {cell_key!r} has {cell['count']} trial(s), expected "
                              f"{_EXPECTED_TRIALS_PER_CELL}")
            )
        expected_indices = {str(i) for i in range(_EXPECTED_TRIALS_PER_CELL)}
        actual_indices = cell_indices.get(cell_key, set())
        if actual_indices != expected_indices:
            problems.append(
                Problem(None, f"cell {cell_key!r} trial indices {sorted(actual_indices)} do not "
                              f"match the expected {sorted(expected_indices)} (missing, "
                              "duplicated, or remapped trial IDs)")
            )
    trial_ids = [t.get("trial_id") for t in trials if isinstance(t, dict)]
    if len(set(trial_ids)) != len(trial_ids):
        problems.append(Problem(None, "duplicate trial_id values in manifest.trials"))
    if len(trials) != _EXPECTED_TOTAL_TRIALS:
        problems.append(
            Problem(None, f"manifest declares {len(trials)} trials, expected "
                          f"{_EXPECTED_TOTAL_TRIALS} ({_EXPECTED_TRIALS_PER_CELL} per cell x "
                          f"{expected_cells} cells)")
        )

    # --- recompute cells_summary / all_agree from validated observations and bind to the
    # manifest's own claimed summary - a summary is not just a count of distinct cell names ---
    recomputed_cells_summary = {
        key: {
            "case": c["case"], "release": c["release"], "count": c["count"],
            "passing": sum(
                1 for t in trials
                if isinstance(t, dict) and f"{t.get('case')}|{t.get('release')}" == key
                and t.get("passed") is True
            ),
            "labels": c["labels"], "effect_counts": c["effect_counts"],
        }
        for key, c in cells.items()
    }
    manifest_cells_summary = manifest.get("cells_summary")
    if manifest_cells_summary != recomputed_cells_summary:
        problems.append(
            Problem(None, "manifest.cells_summary does not match the summary recomputed from "
                          "manifest.trials")
        )
    recomputed_all_agree = bool(trials) and all(
        isinstance(t, dict) and t.get("passed") is True for t in trials
    )
    if manifest.get("all_agree") != recomputed_all_agree:
        problems.append(
            Problem(None, f"manifest.all_agree={manifest.get('all_agree')!r} does not match the "
                          f"recomputed value {recomputed_all_agree!r}")
        )

    return {
        "bundle_root": str(bundle_root),
        "manifest_found": True,
        "manifest_receipt_valid": manifest_receipt_valid,
        "trial_count": len(trials),
        "cells": cells,
        "problems": [p.to_json() for p in problems],
    }


def _normalize_row(row: object) -> object:
    """Result may be embedded (dict) on one side and JSON-round-tripped on the other; normalize
    key order and JSON-serialize nested result for a stable comparison."""
    if not isinstance(row, dict):
        return row
    return canonicalize({k: v for k, v in row.items()})


# --------------------------------------------------------------------------------------------
# CLI: usable both as the "fresh observer" the parent spawns mid-run, and for later, fully
# offline, post-hoc bundle verification (including after the bundle has been relocated).
# --------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_obs = sub.add_parser("observe-ledger")
    p_obs.add_argument("--path", required=True)

    p_ver = sub.add_parser("verify-bundle")
    p_ver.add_argument("--dir", required=True)

    args = ap.parse_args(argv)
    if args.cmd == "observe-ledger":
        obs = observe_ledger(Path(args.path))
        print(json.dumps(obs.to_json(), indent=2, sort_keys=True))
        return 0 if obs.status in {"OK", "EMPTY_CONFIRMED"} else 1
    if args.cmd == "verify-bundle":
        report = verify_bundle(Path(args.dir))
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return 0 if not report["problems"] else 1
    raise AssertionError(f"unhandled cmd {args.cmd!r}")  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
