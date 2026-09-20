"""Offline verifier for the pre-dispatch action identity / external readback bundle.

Runs without importing or launching action_readback.py/action_readback_runtime.py or the ledger
daemon, and works after the bundle is relocated to another local directory. Every decisive fact
(effect counts, payload digests, attempt order, admission durability) is independently re-derived
here from retained raw bytes - the admission SQLite file and the raw ledger.jsonl copy - never
trusted from the manifest's own summaries or booleans, and never imported from
action_readback_runtime.py's own parsing (a deliberate, small duplication: this module's ledger
JSONL parser is written fresh, not shared, so a bug in the producer's parser cannot also hide from
its own verifier). Sharing the schema/vocabulary definitions with action_readback_receipt.py (the
required-fields list, the closed enums, ``validate_receipt`` itself) is reasonable - those are
canonicalization/schema, not a classification the verifier is asked to trust.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast

from ..canonical import canonicalize, chain, receipt
from .action_readback_receipt import (
    CASES,
    REQUIRED_EMBEDDED_LOCK_FILES,
    REQUIRED_EMBEDDED_SOURCE_FILES,
    validate_receipt,
)

MANIFEST_SCHEMA: Final = "crashpoint.action_readback.manifest.v1"
_EXPECTED_TRIALS_PER_CELL: Final = 3
_EXPECTED_TOTAL_TRIALS: Final = len(CASES) * _EXPECTED_TRIALS_PER_CELL
_GENESIS: Final = "crashpoint-ledger-genesis-cp1"


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
    is_link = candidate.is_symlink()
    resolved = candidate.resolve()
    root_resolved = bundle_root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        kind = "a symlink whose target" if is_link else "a path that"
        raise BundleEscape(
            f"{filename!r} in trial dir {trial_dir} is {kind} resolves outside the bundle "
            f"root ({resolved}) - rejected before opening"
        ) from exc
    return resolved


@dataclass
class Problem:
    trial_id: str | None
    reason: str

    def to_json(self) -> dict[str, str | None]:
        return {"trial_id": self.trial_id, "reason": self.reason}


# Evidence files every trial of each case must retain, beyond the always-required set. Only
# unavailable_readback deliberately lacks ledger.jsonl - its absence IS the evidence, not a gap.
_ALWAYS_REQUIRED_FILES: Final = (
    "receipt.json", "admission.sqlite", "worker_a.stdout", "worker_a.stderr",
    "observer_report.json",
)
_REQUIRED_EXTRA_FILES_BY_CASE: Final[dict[str, tuple[str, ...]]] = {
    "clean": ("ledger.jsonl",),
    "effect_before_lost_receipt": ("ledger.jsonl",),
    "stopped_before_effect": ("ledger.jsonl",),
    "naive_retry": ("ledger.jsonl", "worker_b.stdout", "worker_b.stderr"),
    "payload_mismatch": ("ledger.jsonl",),
    "unavailable_readback": (),
}


# --------------------------------------------------------------------------------------------
# Independent ledger re-parse - a fresh implementation, not imported from action_readback_runtime.
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LedgerObservation:
    status: str  # "MISSING" | "EMPTY_CONFIRMED" | "OK" | "TAMPERED"
    raw_sha256: str | None
    byte_length: int | None
    broken_at_index: int | None
    side_effects: dict[str, int]
    effect_digests: dict[str, list[str]]
    attempt_ids_by_intent: dict[str, list[Any]]


def observe_ledger(path: Path) -> LedgerObservation:
    if not path.exists():
        return LedgerObservation("MISSING", None, None, None, {}, {}, {})
    raw = path.read_bytes()
    raw_sha256 = hashlib.sha256(raw).hexdigest()
    if len(raw) == 0:
        return LedgerObservation("EMPTY_CONFIRMED", raw_sha256, 0, None, {}, {}, {})

    head = _GENESIS
    side_effects: dict[str, int] = {}
    effect_digests: dict[str, list[str]] = {}
    attempt_ids_by_intent: dict[str, list[Any]] = {}
    lines = raw.decode("utf-8").splitlines()
    if not lines:
        return LedgerObservation("EMPTY_CONFIRMED", raw_sha256, len(raw), None, {}, {}, {})

    for idx, line in enumerate(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            return LedgerObservation("TAMPERED", raw_sha256, len(raw), idx, {}, {}, {})
        if not isinstance(entry, dict) or entry.get("prev") != head:
            return LedgerObservation("TAMPERED", raw_sha256, len(raw), idx, {}, {}, {})
        record = entry.get("record", {})
        if not isinstance(record, dict):
            return LedgerObservation("TAMPERED", raw_sha256, len(raw), idx, {}, {}, {})
        expected_hash = chain(head, record)
        if entry.get("hash") != expected_hash:
            return LedgerObservation("TAMPERED", raw_sha256, len(raw), idx, {}, {}, {})
        head = expected_hash
        if record.get("op") == "execute":
            intent_raw = record.get("intent_id")
            if not isinstance(intent_raw, str) or not intent_raw:
                return LedgerObservation("TAMPERED", raw_sha256, len(raw), idx, {}, {}, {})
            intent = intent_raw
            attempt_id = record.get("attempt_id")
            prior = attempt_ids_by_intent.get(intent, [])
            if attempt_id is not None and attempt_id in prior:
                return LedgerObservation("TAMPERED", raw_sha256, len(raw), idx, {}, {}, {})
            attempt_ids_by_intent.setdefault(intent, []).append(attempt_id)
            if not record.get("deduped", False):
                digest = record.get("payload_digest")
                if not isinstance(digest, str) or not digest:
                    return LedgerObservation("TAMPERED", raw_sha256, len(raw), idx, {}, {}, {})
                side_effects[intent] = side_effects.get(intent, 0) + 1
                effect_digests.setdefault(intent, []).append(digest)

    return LedgerObservation(
        "OK", raw_sha256, len(raw), None, side_effects, effect_digests, attempt_ids_by_intent
    )


def classify_effect_count(intent_id: str, obs: LedgerObservation) -> str:
    if obs.status == "TAMPERED":
        return "VOID"
    if obs.status == "MISSING":
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
# Independent admission-store re-read: raw sqlite3, no ORM, no import of action_readback_runtime.
# --------------------------------------------------------------------------------------------


def read_admission_row(snapshot_path: Path, action_id: str) -> dict[str, object] | None:
    if not snapshot_path.exists():
        return None
    conn = sqlite3.connect(f"file:{snapshot_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM admissions WHERE action_id = ?", (action_id,)
            ).fetchone()
        except sqlite3.DatabaseError:
            return None
        return dict(row) if row is not None else None
    finally:
        conn.close()


def read_completion_rows(snapshot_path: Path, action_id: str) -> list[dict[str, object]]:
    if not snapshot_path.exists():
        return []
    conn = sqlite3.connect(f"file:{snapshot_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM completions WHERE action_id = ? ORDER BY attempt_id", (action_id,)
            ).fetchall()
        except sqlite3.DatabaseError:
            return []
        return [dict(r) for r in rows]
    finally:
        conn.close()


def read_foreign_completion_rows(
    snapshot_path: Path, action_id: str
) -> list[dict[str, object]]:
    """Every completion row in this trial's OWN admission.sqlite that does NOT belong to this
    trial's own action_id - a direct check for cross-trial contamination, deliberately a
    different query from read_completion_rows (which is itself scoped to `action_id` and so can
    never observe a foreign row by construction)."""
    if not snapshot_path.exists():
        return []
    conn = sqlite3.connect(f"file:{snapshot_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM completions WHERE action_id != ? ORDER BY attempt_id", (action_id,)
            ).fetchall()
        except sqlite3.DatabaseError:
            return []
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _parse_worker_events(stdout_text: str) -> list[dict[str, Any]]:
    from .action_readback_runtime import PREFIX

    events = []
    for line in stdout_text.splitlines():
        if not line.startswith(PREFIX):
            continue
        try:
            events.append(json.loads(line[len(PREFIX):]))
        except json.JSONDecodeError:
            continue
    return events


def _find_event(events: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for ev in events:
        if ev.get("event") == name:
            return ev
    return None


# --------------------------------------------------------------------------------------------
# Top-level bundle verification.
# --------------------------------------------------------------------------------------------


def verify_bundle(bundle_root: Path) -> dict[str, Any]:
    problems: list[Problem] = []
    manifest_path = bundle_root / "manifest.json"
    if not manifest_path.exists():
        return {
            "manifest_found": False,
            "manifest_receipt_valid": False,
            "trial_count": 0,
            "problems": [Problem(None, "manifest.json not found in bundle root").to_json()],
        }

    try:
        manifest = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as exc:
        return {
            "manifest_found": True,
            "manifest_receipt_valid": False,
            "trial_count": 0,
            "problems": [Problem(None, f"manifest.json is not valid JSON: {exc}").to_json()],
        }
    if not isinstance(manifest, dict):
        return {
            "manifest_found": True,
            "manifest_receipt_valid": False,
            "trial_count": 0,
            "problems": [Problem(None, "manifest.json does not contain a JSON object").to_json()],
        }

    body = dict(manifest)
    declared_receipt = body.pop("receipt", None)
    recomputed_receipt = receipt(body)
    manifest_receipt_valid = declared_receipt == recomputed_receipt
    if not manifest_receipt_valid:
        problems.append(
            Problem(None, f"manifest.receipt={declared_receipt!r} does not match the "
                          f"recomputed hash {recomputed_receipt!r} of its own body")
        )

    if manifest.get("schema") != MANIFEST_SCHEMA:
        problems.append(Problem(None, f"unexpected manifest.schema: {manifest.get('schema')!r}"))

    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        problems.append(Problem(None, "manifest.run_id must be a non-empty string"))

    # --- required embedded source/lock inventory, independent of what the manifest's own dict
    # lists ---
    embedded_hashes = manifest.get("embedded_source_sha256")
    if not isinstance(embedded_hashes, dict):
        problems.append(
            Problem(None, "manifest.embedded_source_sha256 is missing or not an object")
        )
        embedded_hashes = {}
    required_sources = tuple(REQUIRED_EMBEDDED_SOURCE_FILES) + tuple(REQUIRED_EMBEDDED_LOCK_FILES)
    for rel_path in required_sources:
        if rel_path not in embedded_hashes:
            problems.append(
                Problem(None, f"manifest.embedded_source_sha256 is missing a required entry: "
                              f"{rel_path!r}")
            )
        try:
            src_path = _safe_join(bundle_root, f"sources/{rel_path}")
        except BundleEscape as exc:
            problems.append(Problem(None, str(exc)))
            continue
        if not src_path.exists():
            problems.append(
                Problem(None, f"required embedded source is absent from the bundle on disk: "
                              f"sources/{rel_path}")
            )
            continue
        actual_hash = hashlib.sha256(src_path.read_bytes()).hexdigest()
        claimed_hash = embedded_hashes.get(rel_path)
        if claimed_hash is not None and actual_hash != claimed_hash:
            problems.append(
                Problem(None, f"embedded source sources/{rel_path} sha256={actual_hash} does "
                              f"not match manifest.embedded_source_sha256={claimed_hash!r}")
            )

    prediction_path = bundle_root / "prediction.json"
    prediction: dict[str, Any] = {}
    if not prediction_path.exists():
        problems.append(Problem(None, "prediction.json is missing from the bundle root"))
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
            problems.append(
                Problem(None, f"embedded prediction sha256={prediction_hash} does not match "
                              f"manifest.prediction_sha256={manifest.get('prediction_sha256')!r}")
            )
        try:
            loaded_prediction = json.loads(prediction_bytes)
        except json.JSONDecodeError:
            problems.append(Problem(None, "embedded prediction.json is not valid JSON"))
        else:
            if isinstance(loaded_prediction, dict):
                prediction = loaded_prediction
            else:
                problems.append(Problem(None, "embedded prediction.json is not a JSON object"))

    # --- cell/trial-count accounting: both the actual trials list AND the manifest's own
    # self-reported declared counts, checked independently of each other ---
    trials = manifest.get("trials", [])
    if not isinstance(trials, list):
        problems.append(Problem(None, "manifest.trials must be a list"))
        trials = []
    cells: dict[str, list[dict[str, Any]]] = {c: [] for c in CASES}
    for t in trials:
        if isinstance(t, dict) and t.get("case") in cells:
            cells[t["case"]].append(t)
    for case, cell_trials in cells.items():
        if len(cell_trials) != _EXPECTED_TRIALS_PER_CELL:
            problems.append(
                Problem(None, f"case {case!r} has {len(cell_trials)} trial(s), expected "
                              f"{_EXPECTED_TRIALS_PER_CELL}")
            )
        indices = {
            t["trial_id"].rsplit("-", 1)[-1]
            for t in cell_trials if isinstance(t.get("trial_id"), str)
        }
        expected_indices = {str(i) for i in range(_EXPECTED_TRIALS_PER_CELL)}
        if indices != expected_indices:
            problems.append(
                Problem(None, f"case {case!r} trial indices {sorted(indices)} do not match the "
                              f"expected {sorted(expected_indices)}")
            )
    trial_ids = [t.get("trial_id") for t in trials if isinstance(t, dict)]
    if len(set(trial_ids)) != len(trial_ids):
        problems.append(Problem(None, "duplicate trial_id values in manifest.trials"))
    if len(trials) != _EXPECTED_TOTAL_TRIALS:
        problems.append(
            Problem(None, f"manifest.trials has {len(trials)} entries, expected "
                          f"{_EXPECTED_TOTAL_TRIALS}")
        )
    declared_trial_count = manifest.get("trial_count")
    if declared_trial_count != len(trials):
        problems.append(
            Problem(None, f"manifest.trial_count={declared_trial_count!r} does not match "
                          f"len(manifest.trials)={len(trials)}")
        )
    if manifest.get("trials_per_cell") != _EXPECTED_TRIALS_PER_CELL:
        problems.append(
            Problem(None, f"manifest.trials_per_cell={manifest.get('trials_per_cell')!r}, "
                          f"expected {_EXPECTED_TRIALS_PER_CELL}")
        )
    if manifest.get("expected_trial_count") != _EXPECTED_TOTAL_TRIALS:
        problems.append(
            Problem(None, f"manifest.expected_trial_count="
                          f"{manifest.get('expected_trial_count')!r}, expected "
                          f"{_EXPECTED_TOTAL_TRIALS}")
        )

    # --- cells_summary / all_agree recomputed from manifest.trials, never trusted as-declared ---
    recomputed_all_agree = (
        bool(trials) and not manifest.get("execution_failures")
        and all(isinstance(t, dict) and bool(t.get("passed")) for t in trials)
    )
    if manifest.get("all_agree") is not recomputed_all_agree:
        problems.append(
            Problem(None, f"manifest.all_agree={manifest.get('all_agree')!r} does not match "
                          f"the recomputed value {recomputed_all_agree!r}")
        )
    cells_summary = manifest.get("cells_summary")
    if isinstance(cells_summary, dict):
        for case, cell_trials in cells.items():
            declared_cell = cells_summary.get(case, {})
            if declared_cell.get("count") != len(cell_trials):
                problems.append(
                    Problem(None, f"cells_summary[{case!r}].count={declared_cell.get('count')!r} "
                                  f"does not match len(trials in that cell)={len(cell_trials)}")
                )
            recomputed_passing = sum(1 for t in cell_trials if t.get("passed"))
            if declared_cell.get("passing") != recomputed_passing:
                problems.append(
                    Problem(None, f"cells_summary[{case!r}].passing="
                                  f"{declared_cell.get('passing')!r} does not match recomputed "
                                  f"{recomputed_passing!r}")
                )
    else:
        problems.append(Problem(None, "manifest.cells_summary must be an object"))

    # --- journal.jsonl: a separate, independently cross-checked record of the same trials ---
    journal_path = bundle_root / "journal.jsonl"
    if not journal_path.exists():
        problems.append(Problem(None, "journal.jsonl is missing from the bundle root"))
    else:
        journal_entries: dict[str, Any] = {}
        for lineno, line in enumerate(journal_path.read_text().splitlines()):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                problems.append(Problem(None, f"journal.jsonl line {lineno} is not valid JSON"))
                continue
            if not isinstance(entry, dict) or "trial_id" not in entry:
                problems.append(
                    Problem(None, f"journal.jsonl line {lineno} is missing trial_id")
                )
                continue
            journal_entries[entry["trial_id"]] = entry.get("receipt")
        for t in trials:
            if not isinstance(t, dict):
                continue
            tid = t.get("trial_id")
            if tid not in journal_entries:
                problems.append(
                    Problem(tid, "manifest.trials has this trial but journal.jsonl does not")
                )
            elif journal_entries[tid] != t:
                problems.append(
                    Problem(tid, "journal.jsonl's record for this trial_id differs from "
                                 "manifest.trials' record for the same trial_id")
                )

    # --- per-trial verification ---
    trial_dirs = manifest.get("trial_dirs", {})
    if not isinstance(trial_dirs, dict):
        problems.append(Problem(None, "manifest.trial_dirs must be an object"))
        trial_dirs = {}

    for trial in trials:
        if not isinstance(trial, dict):
            problems.append(Problem(None, f"a manifest.trials entry is not an object: {trial!r}"))
            continue
        trial_id = trial.get("trial_id")
        action_id = trial.get("action_id")
        trial_case = trial.get("case")

        for reason in validate_receipt(trial):
            problems.append(Problem(trial_id, f"receipt schema: {reason}"))

        # The receipt's own expected_result must match what the FROZEN, embedded prediction file
        # actually declares for this case - not merely be internally self-consistent with the
        # receipt's own passed/observed_result fields (validate_receipt already checked that).
        predicted_cases = prediction.get("cases") if isinstance(prediction, dict) else None
        if (
            isinstance(predicted_cases, dict) and trial_case in predicted_cases
            and trial.get("expected_result") != predicted_cases[trial_case]
        ):
            problems.append(
                Problem(trial_id, f"receipt expected_result={trial.get('expected_result')!r} "
                                  f"does not match the embedded prediction's declared result "
                                  f"for case {trial_case!r}: {predicted_cases[trial_case]!r}")
            )

        rel_dir = trial_dirs.get(trial_id)
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
            cast(str, trial_case), ()
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
        # unavailable_readback must NOT retain ledger.jsonl - its absence is the evidence.
        if trial_case == "unavailable_readback":
            try:
                stray = _confined_file(bundle_root, trial_dir, "ledger.jsonl")
            except BundleEscape:
                stray = None
            if stray is not None and stray.exists():
                problems.append(
                    Problem(trial_id, "case 'unavailable_readback' retains ledger.jsonl, but "
                                       "its deliberate absence is exactly what this negative "
                                       "control is supposed to demonstrate")
                )

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

        # --- independent admission-store re-read ---
        if "admission.sqlite" in confined and isinstance(action_id, str):
            row = read_admission_row(confined["admission.sqlite"], action_id)
            if row is None:
                problems.append(
                    Problem(trial_id, "admission.sqlite has no row for this trial's action_id")
                )
            else:
                canonical_payload = row.get("canonical_payload")
                recomputed_digest = None
                if isinstance(canonical_payload, str):
                    try:
                        recomputed_digest = hashlib.sha256(
                            canonicalize(json.loads(canonical_payload)).encode("utf-8")
                        ).hexdigest()
                    except (json.JSONDecodeError, TypeError):
                        problems.append(
                            Problem(trial_id, "admission.sqlite canonical_payload is not "
                                               "parseable JSON")
                        )
                stored_digest = row.get("payload_digest")
                if recomputed_digest is not None and recomputed_digest != stored_digest:
                    problems.append(
                        Problem(trial_id, f"admission.sqlite payload_digest={stored_digest!r} "
                                          f"does not match the recomputed digest "
                                          f"{recomputed_digest!r} of its own canonical_payload")
                    )
                if stored_digest != trial.get("admission_payload_digest"):
                    problems.append(
                        Problem(trial_id, f"receipt admission_payload_digest="
                                          f"{trial.get('admission_payload_digest')!r} does not "
                                          f"match admission.sqlite payload_digest="
                                          f"{stored_digest!r}")
                    )
                if row.get("run_id") != run_id:
                    problems.append(
                        Problem(trial_id, f"admission.sqlite run_id={row.get('run_id')!r} does "
                                          f"not match manifest.run_id={run_id!r} (mixed run IDs)")
                    )
                if row.get("action_type") != trial.get("action_type"):
                    problems.append(
                        Problem(trial_id, "admission.sqlite action_type does not match receipt "
                                           "action_type")
                    )

            # Two distinct admitted actions with identical arguments must never be merged: this
            # trial's own admission.sqlite must contain no completion row for any OTHER action_id.
            foreign = read_foreign_completion_rows(confined["admission.sqlite"], action_id)
            if foreign:
                problems.append(
                    Problem(trial_id, f"admission.sqlite completions include {len(foreign)} "
                                       f"row(s) for a different action_id than this trial's own")
                )

        # --- independent ledger re-parse ---
        obs: LedgerObservation | None = None
        if "ledger.jsonl" in confined:
            obs = observe_ledger(confined["ledger.jsonl"])
            recomputed_count = obs.side_effects.get(cast(str, action_id), 0) if obs.status in {
                "OK", "EMPTY_CONFIRMED"
            } else None
            recomputed_classification = classify_effect_count(cast(str, action_id), obs)
            stored_count = trial.get("effect_count")
            if recomputed_count != stored_count:
                problems.append(
                    Problem(trial_id, f"effect_count contradicts raw ledger bytes: receipt says "
                                       f"{stored_count!r}, independent re-parse says "
                                       f"{recomputed_count!r} (ledger status={obs.status})")
                )
            if recomputed_classification != trial.get("effect_ledger_classification"):
                problems.append(
                    Problem(trial_id, f"effect_ledger_classification contradicts raw ledger "
                                       f"bytes: receipt says "
                                       f"{trial.get('effect_ledger_classification')!r}, "
                                       f"recomputed {recomputed_classification!r}")
                )
            if isinstance(action_id, str) and obs.status == "OK":
                digests = obs.effect_digests.get(action_id, [])
                admitted_digest = trial.get("admission_payload_digest")
                recomputed_match: bool | None = (
                    None if not digests else all(d == admitted_digest for d in digests)
                )
                if recomputed_match != trial.get("digests_match_admission"):
                    problems.append(
                        Problem(trial_id, f"digests_match_admission="
                                          f"{trial.get('digests_match_admission')!r} does not "
                                          f"match the recomputed value {recomputed_match!r} "
                                          f"(admitted digest {admitted_digest!r} vs retained "
                                          f"effect digests {digests!r})")
                    )
                # The receipt's own effect_attempt_ids must match the raw ledger's recorded
                # attempt sequence exactly (full attempt_id strings, in raw order) - not just
                # agree on the set or the count.
                raw_attempt_ids = obs.attempt_ids_by_intent.get(action_id, [])
                if raw_attempt_ids != trial.get("effect_attempt_ids"):
                    problems.append(
                        Problem(trial_id, f"effect_attempt_ids="
                                          f"{trial.get('effect_attempt_ids')!r} does not match "
                                          f"the raw ledger's recorded attempt sequence "
                                          f"{raw_attempt_ids!r}")
                    )

                # Lineage, not just count: worker-a's attempt_id can never legitimately follow
                # worker-b-retry's in the raw ledger, by this experiment's own construction.
                suffixes = [
                    a.rsplit(":", 1)[1] if isinstance(a, str) and ":" in a else a
                    for a in raw_attempt_ids
                ]
                known = {"worker-a", "worker-b-retry"}
                unrecognized = [s for s in suffixes if s not in known]
                if unrecognized:
                    problems.append(
                        Problem(trial_id, f"raw ledger attempt IDs include unrecognized "
                                          f"attempt(s) {unrecognized!r}")
                    )
                elif (
                    "worker-a" in suffixes and "worker-b-retry" in suffixes
                    and suffixes.index("worker-a") > suffixes.index("worker-b-retry")
                ):
                    problems.append(
                        Problem(trial_id, "raw ledger attempt order is reversed: "
                                           "worker-b-retry precedes worker-a")
                    )

        # --- worker protocol events: cross-checked against retained raw stdout, and REQUIRED to
        # exist for a passing trial, not merely checked if present ---
        if "worker_a.stdout" in confined:
            a_events = _parse_worker_events(confined["worker_a.stdout"].read_text())
            started = _find_event(a_events, "worker_started")
            if trial.get("passed") is True and started is None:
                problems.append(
                    Problem(trial_id, "passed=true but retained worker_a.stdout shows no "
                                       "'worker_started' event")
                )
            if started is not None and started.get("pid") != trial.get("worker_a_pid"):
                problems.append(
                    Problem(trial_id, f"worker_a_pid={trial.get('worker_a_pid')!r} disagrees "
                                       f"with retained worker_a.stdout worker_started.pid="
                                       f"{started.get('pid')!r}")
                )
            read_evt = _find_event(a_events, "admission_read")
            if trial.get("worker_a_read_admission_confirmed") is True and read_evt is None:
                problems.append(
                    Problem(trial_id, "worker_a_read_admission_confirmed=true but retained "
                                       "worker_a.stdout shows no 'admission_read' event")
                )
            if trial_case in {"clean", "payload_mismatch"} and trial.get("passed") is True:
                complete_evt = _find_event(a_events, "worker_complete")
                if complete_evt is None:
                    problems.append(
                        Problem(trial_id, "passed=true but retained worker_a.stdout shows no "
                                           "'worker_complete' event")
                    )
            not_killed_cases = {"clean", "payload_mismatch"}
            if trial_case not in not_killed_cases and trial.get("worker_a_killed") is True:
                barrier_evt = _find_event(a_events, "barrier")
                if barrier_evt is None:
                    problems.append(
                        Problem(trial_id, "worker_a_killed=true but retained worker_a.stdout "
                                           "shows no 'barrier' event")
                    )
                elif barrier_evt.get("point") != trial.get("worker_a_barrier_point"):
                    problems.append(
                        Problem(trial_id, f"worker_a_barrier_point="
                                          f"{trial.get('worker_a_barrier_point')!r} disagrees "
                                          f"with retained barrier event point="
                                          f"{barrier_evt.get('point')!r}")
                    )

        if trial_case == "naive_retry" and "worker_b.stdout" in confined:
            b_events = _parse_worker_events(confined["worker_b.stdout"].read_text())
            if trial.get("passed") is True and _find_event(b_events, "worker_complete") is None:
                problems.append(
                    Problem(trial_id, "passed=true but retained worker_b.stdout shows no "
                                       "'worker_complete' event")
                )

        # --- observer: exit status checked, never just the presence of a nice-looking report ---
        if "observer_report.json" in confined:
            try:
                obs_report = json.loads(confined["observer_report.json"].read_text())
            except json.JSONDecodeError:
                problems.append(Problem(trial_id, "observer_report.json is not valid JSON"))
                obs_report = {}
            if obs_report.get("raw_state") != trial.get("observer_raw_ledger_state"):
                problems.append(
                    Problem(trial_id, "observer_report.json raw_state disagrees with receipt "
                                       "observer_raw_ledger_state")
                )
            ledger_read_ok = obs is not None and obs.status in {"OK", "EMPTY_CONFIRMED"}
            if (
                "ledger.jsonl" in confined and ledger_read_ok
                and obs_report.get("raw_sha256") != cast(LedgerObservation, obs).raw_sha256
            ):
                problems.append(
                    Problem(trial_id, "observer_report.json raw_sha256 does not match an "
                                       "independent re-hash of the retained ledger.jsonl bytes")
                )
            observer_exit = trial.get("observer_exit_status")
            if observer_exit != 0 and trial.get("observation_complete") is True:
                avail = trial.get("observed_result", {}).get("observation_availability")
                if avail == "FULL":
                    problems.append(
                        Problem(trial_id, f"observer_exit_status={observer_exit!r} (nonzero) "
                                          "but observed_result.observation_availability=FULL - "
                                          "a nonzero observer exit must never be trusted as a "
                                          "full observation")
                    )

    return {
        "manifest_found": True,
        "manifest_receipt_valid": manifest_receipt_valid,
        "trial_count": len(trials),
        "problems": [p.to_json() for p in problems],
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_verify = sub.add_parser("verify-bundle")
    p_verify.add_argument("--dir", required=True, type=Path)

    args = ap.parse_args(argv)
    if args.cmd == "verify-bundle":
        report = verify_bundle(args.dir)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if not report["problems"] else 1
    raise AssertionError(f"unhandled cmd {args.cmd!r}")  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
