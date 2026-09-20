from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from crashpoint.canonical import chain, receipt
from crashpoint.harness import safeagent_ttl_verify as verify
from crashpoint.harness.safeagent_ttl_receipt import REQUIRED_EMBEDDED_SOURCE_FILES
from crashpoint.ledger.core import GENESIS, LedgerState

_ROOT = Path(__file__).resolve().parents[1]
# safeagent_ttl_corrected_review2 is the current authoritative, fully-provenanced bundle (embedded
# prediction/sources, case-specific snapshot cross-checks, per-field schema checks, trial_count/
# embedded-source-inventory/retry-event/attempt-order cross-checks, etc.), captured fresh after
# the 2026-09-19 second review's corrections. Two earlier bundles are deliberately retained,
# byte-identical, as historical evidence rather than deleted or overwritten:
#   - safeagent_ttl_corrected (first review's bundle): still passes the current, stricter
#     verify_bundle unchanged (see test_safeagent_ttl_corrected_round2_bundle_preserved_and_
#     still_validates below) - its capture was already correct, only the checks were missing.
#   - safeagent_ttl (the original, pre-hardening bundle): predates embedded prediction/sources
#     entirely, so it is checked by hash in tests/test_safeagent_ttl.py's
#     test_historical_pre_hardening_bundle_preserved_and_still_self_consistent, not by
#     re-running the current verifier against a shape it was never built to satisfy.
_REAL_BUNDLE = _ROOT / "evidence" / "safeagent_ttl" / "safeagent_ttl_corrected_review2"
_ROUND2_BUNDLE = _ROOT / "evidence" / "safeagent_ttl" / "safeagent_ttl_corrected"
_HISTORICAL_BUNDLE = _ROOT / "evidence" / "safeagent_ttl" / "safeagent_ttl"


def _write_ledger(
    path: Path, intent_id: str, effects: list[tuple[str | None, dict[str, Any]]]
) -> None:
    """Build a real, correctly hash-chained ledger file using crashpoint's own pure ledger
    logic - the same class the daemon wraps - so tests exercise a genuine chain, not a
    hand-rolled approximation of one."""
    state = LedgerState(path=path)
    for i, (key, payload) in enumerate(effects):
        state.execute(intent_id, key, payload, attempt_id=f"{intent_id}:attempt-{i + 1}")


# --------------------------------------------------------------------------------------------
# observe_ledger: MISSING vs EMPTY_CONFIRMED vs OK vs TAMPERED must never be conflated.
# --------------------------------------------------------------------------------------------


def test_observe_ledger_missing_file_is_not_empty(tmp_path: Path) -> None:
    obs = verify.observe_ledger(tmp_path / "does-not-exist.jsonl")
    assert obs.status == "MISSING"
    assert obs.raw_sha256 is None
    assert obs.side_effects == {}


def test_observe_ledger_genuinely_empty_file_is_distinguishable_from_missing(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ledger.jsonl"
    path.touch()
    obs = verify.observe_ledger(path)
    assert obs.status == "EMPTY_CONFIRMED"
    assert obs.raw_sha256 is not None  # sha256 of zero bytes, a real hash, not None


def test_observe_ledger_single_effect_is_ok_and_exactly_once(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    _write_ledger(path, "intent-1", [(None, {"op": "safeagent_ttl_local_action"})])
    obs = verify.observe_ledger(path)
    assert obs.status == "OK"
    assert obs.side_effects == {"intent-1": 1}
    assert verify.classify_effect_count("intent-1", obs) == "EXACTLY_ONCE"


def test_observe_ledger_two_identical_effects_is_duplicated(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    payload = {"operation": "safeagent_ttl_local_action", "marker": "harmless"}
    _write_ledger(path, "intent-1", [(None, payload), (None, payload)])
    obs = verify.observe_ledger(path)
    assert obs.side_effects == {"intent-1": 2}
    assert verify.classify_effect_count("intent-1", obs) == "DUPLICATED"


def test_observe_ledger_two_different_effects_is_diverged(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    _write_ledger(
        path, "intent-1",
        [(None, {"marker": "first"}), (None, {"marker": "second"})],
    )
    obs = verify.observe_ledger(path)
    assert obs.side_effects == {"intent-1": 2}
    assert verify.classify_effect_count("intent-1", obs) == "DIVERGED"


def test_observe_ledger_zero_effects_for_unknown_intent_is_zero_not_exactly_once(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ledger.jsonl"
    _write_ledger(path, "intent-1", [(None, {"marker": "harmless"})])
    obs = verify.observe_ledger(path)
    # A different intent that never crossed at all: zero, never EXACTLY_ONCE.
    assert verify.classify_effect_count("intent-absent", obs) == "ZERO"


def test_observe_ledger_detects_a_tampered_record(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    _write_ledger(path, "intent-1", [(None, {"marker": "harmless"})])
    lines = path.read_text().splitlines()
    entry = json.loads(lines[0])
    entry["record"]["payload_digest"] = "0" * 64  # edit the record without recomputing hash
    path.write_text(json.dumps(entry) + "\n")
    obs = verify.observe_ledger(path)
    assert obs.status == "TAMPERED"
    assert obs.broken_at_index == 0
    assert verify.classify_effect_count("intent-1", obs) == "VOID"


def test_observe_ledger_detects_malformed_json_line(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    path.write_text("{not valid json\n")
    obs = verify.observe_ledger(path)
    assert obs.status == "TAMPERED"
    assert obs.broken_at_index == 0


# --------------------------------------------------------------------------------------------
# read_claim_row: raw sqlite3 read, no SafeAgent import.
# --------------------------------------------------------------------------------------------


def _make_claim_db(path: Path, rows: list[dict[str, Any]]) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE execution_requests (request_id TEXT PRIMARY KEY, action TEXT, "
        "status TEXT, result TEXT, claimed_at REAL, committed_at REAL, agent_id TEXT)"
    )
    for row in rows:
        conn.execute(
            "INSERT INTO execution_requests "
            "(request_id, action, status, result, claimed_at, committed_at, agent_id) "
            "VALUES (:request_id, :action, :status, :result, :claimed_at, :committed_at, "
            ":agent_id)",
            row,
        )
    conn.commit()
    conn.close()


def test_read_claim_row_missing_snapshot_returns_none(tmp_path: Path) -> None:
    assert verify.read_claim_row(tmp_path / "absent.sqlite", "x") is None


def test_read_claim_row_reads_back_a_pending_row(tmp_path: Path) -> None:
    db = tmp_path / "claim.sqlite"
    _make_claim_db(
        db,
        [
            {"request_id": "action-1", "action": "safeagent_ttl_local_action", "status": "PENDING",
             "result": None, "claimed_at": 100.0, "committed_at": None, "agent_id": None}
        ],
    )
    row = verify.read_claim_row(db, "action-1")
    assert row is not None
    assert row["status"] == "PENDING"
    assert row["result"] is None


def test_read_claim_row_deserializes_json_result(tmp_path: Path) -> None:
    db = tmp_path / "claim.sqlite"
    _make_claim_db(
        db,
        [
            {"request_id": "action-1", "action": "a", "status": "COMMITTED",
             "result": json.dumps({"outcome": "ok"}), "claimed_at": 100.0, "committed_at": 101.0,
             "agent_id": None}
        ],
    )
    row = verify.read_claim_row(db, "action-1")
    assert row is not None
    assert row["result"] == {"outcome": "ok"}


def test_read_claim_row_wrong_action_id_returns_none(tmp_path: Path) -> None:
    db = tmp_path / "claim.sqlite"
    _make_claim_db(
        db,
        [{"request_id": "action-1", "action": "a", "status": "PENDING", "result": None,
          "claimed_at": 100.0, "committed_at": None, "agent_id": None}],
    )
    assert verify.read_claim_row(db, "someone-elses-action") is None


# --------------------------------------------------------------------------------------------
# verify_bundle against the REAL recorded 30-trial evidence: baseline pass, then mutation tests
# that must be caught for their SPECIFIC reason even after the attacker recomputes the outer
# manifest receipt hash (proving the check is a raw-bytes cross-check, not a stale checksum).
# --------------------------------------------------------------------------------------------


def _copy_real_bundle(tmp_path: Path) -> Path:
    if not _REAL_BUNDLE.exists():
        pytest.skip(f"recorded evidence bundle not found at {_REAL_BUNDLE}")
    dest = tmp_path / "bundle"
    shutil.copytree(_REAL_BUNDLE, dest)
    return dest


def _remanifest(bundle_dir: Path, mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Load manifest.json, apply `mutate` in place, recompute the outer receipt hash so the
    mutated manifest is internally self-consistent, and write it back. Returns the new manifest."""
    manifest_path = bundle_dir / "manifest.json"
    manifest: dict[str, Any] = json.loads(manifest_path.read_text())
    mutate(manifest)
    body = dict(manifest)
    body.pop("receipt", None)
    manifest["receipt"] = receipt(body)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def test_verify_bundle_recorded_evidence_has_no_problems(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    report = verify.verify_bundle(bundle)
    assert report["manifest_found"] is True
    assert report["manifest_receipt_valid"] is True
    assert report["trial_count"] == 30
    assert report["problems"] == []


def test_verify_bundle_survives_relocation(tmp_path: Path) -> None:
    """An untouched, relocated bundle must still pass - portability is part of the contract."""
    bundle = _copy_real_bundle(tmp_path)
    relocated = tmp_path / "elsewhere" / "moved-bundle"
    relocated.parent.mkdir(parents=True)
    shutil.move(str(bundle), str(relocated))
    report = verify.verify_bundle(relocated)
    assert report["problems"] == []


def test_safeagent_ttl_corrected_round2_bundle_preserved_and_still_validates() -> None:
    """The first review's bundle (safeagent_ttl_corrected) is superseded as the authoritative
    capture by safeagent_ttl_corrected_review2, but - unlike the original pre-hardening bundle -
    its capture was already correct; only the checks were missing at the time. So this both
    confirms it was preserved untouched (outer receipt hash still verifies against its own
    bytes) AND that it still passes today's stricter verify_bundle unchanged, in place, without
    copying or modification."""
    if not _ROUND2_BUNDLE.exists():
        pytest.skip(f"no round-2 evidence at {_ROUND2_BUNDLE}")
    manifest = json.loads((_ROUND2_BUNDLE / "manifest.json").read_text())
    body = dict(manifest)
    recorded = body.pop("receipt")
    assert recorded == receipt(body)
    report = verify.verify_bundle(_ROUND2_BUNDLE)
    assert report["trial_count"] == 30
    assert report["problems"] == []


def test_verify_bundle_missing_manifest(tmp_path: Path) -> None:
    empty = tmp_path / "empty-bundle"
    empty.mkdir()
    report = verify.verify_bundle(empty)
    assert report["manifest_found"] is False
    assert any("manifest.json not found" in p["reason"] for p in report["problems"])


def test_mutation_effect_count_contradicts_raw_ledger(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    target = None

    def mutate(manifest: dict[str, Any]) -> None:
        nonlocal target
        for t in manifest["trials"]:
            if t["case"] == "pending_expired_swept" and t["release"] == "0.1.23":
                target = t["trial_id"]
                t["effect_count"] = 1  # really 2; understating a duplication
                break

    _remanifest(bundle, mutate)
    report = verify.verify_bundle(bundle)
    assert report["manifest_receipt_valid"] is True  # the checksum alone was recomputed to agree
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == target]
    assert any("effect_count contradicts raw ledger bytes" in r for r in reasons)


def test_mutation_claim_status_contradicts_snapshot(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    target = None

    def mutate(manifest: dict[str, Any]) -> None:
        nonlocal target
        for t in manifest["trials"]:
            if t["case"] == "pre_effect_expired_swept" and t["release"] == "0.1.24":
                target = t["trial_id"]
                t["final_claim_row"]["status"] = "COMMITTED"  # really stuck PENDING
                break

    _remanifest(bundle, mutate)
    report = verify.verify_bundle(bundle)
    assert report["manifest_receipt_valid"] is True
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == target]
    assert any("final_claim_row contradicts claim_final.sqlite" in r for r in reasons)


def test_mutation_action_id_changed(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    target = None

    def mutate(manifest: dict[str, Any]) -> None:
        nonlocal target
        t = manifest["trials"][0]
        target = t["trial_id"]
        t["action_id"] = "a-different-action-id-not-in-any-raw-file"

    _remanifest(bundle, mutate)
    report = verify.verify_bundle(bundle)
    assert report["manifest_receipt_valid"] is True
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == target]
    assert reasons  # the raw ledger/claim files no longer agree with the renamed action_id


def test_mutation_ledger_bytes_tampered_on_disk(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    t = next(t for t in manifest["trials"] if t["case"] == "settled_control")
    trial_dir = bundle / manifest["trial_dirs"][t["trial_id"]]
    ledger_path = trial_dir / "ledger.jsonl"
    lines = ledger_path.read_text().splitlines()
    entry = json.loads(lines[0])
    entry["record"]["payload_digest"] = "f" * 64
    ledger_path.write_text(json.dumps(entry) + "\n" + "\n".join(lines[1:]))
    # Manifest itself (and its receipt) are untouched - only the raw evidence file was edited.
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == t["trial_id"]]
    assert any("contradicts raw ledger bytes" in r for r in reasons)


def test_mutation_summary_label_contradicts_recomputed_inputs(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    target = None

    def mutate(manifest: dict[str, Any]) -> None:
        nonlocal target
        for t in manifest["trials"]:
            if t["case"] == "pending_expired_swept" and t["release"] == "0.1.24":
                target = t["trial_id"]
                t["task_completion_label"] = "blocked_pending_task_unperformed"  # wrong summary
                break

    _remanifest(bundle, mutate)
    report = verify.verify_bundle(bundle)
    assert report["manifest_receipt_valid"] is True
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == target]
    assert any("task_completion_label contradicts recomputed inputs" in r for r in reasons)


def test_mutation_outer_receipt_alone_not_recomputed_is_caught_too(tmp_path: Path) -> None:
    """The ordinary case: someone edits the manifest and does NOT bother recomputing the outer
    hash. manifest_receipt_valid must go False."""
    bundle = _copy_real_bundle(tmp_path)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["trials"][0]["effect_count"] = 999
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    report = verify.verify_bundle(bundle)
    assert report["manifest_receipt_valid"] is False


def test_verify_bundle_rejects_trial_dir_escape(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)

    def mutate(manifest: dict[str, Any]) -> None:
        first_id = manifest["trials"][0]["trial_id"]
        manifest["trial_dirs"][first_id] = "../../../../etc"

    _remanifest(bundle, mutate)
    report = verify.verify_bundle(bundle)
    assert any("resolves outside the bundle root" in p["reason"] for p in report["problems"])


def test_verify_bundle_rejects_absolute_trial_dir_escape(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)

    def mutate(manifest: dict[str, Any]) -> None:
        first_id = manifest["trials"][0]["trial_id"]
        manifest["trial_dirs"][first_id] = "/etc"

    _remanifest(bundle, mutate)
    report = verify.verify_bundle(bundle)
    assert any("is absolute" in p["reason"] for p in report["problems"])


# --------------------------------------------------------------------------------------------
# Permanent negative controls ported directly from the 2026-09-19 independent review's
# check_review.py reproduction (see handoff/safeagent-ttl/CORRECTIONS.md), run against a FRESH
# valid fixture (the corrected bundle), not the pre-hardening archive - an old bundle failing the
# new, stricter schema is not the same claim as a mutation being caught.
# --------------------------------------------------------------------------------------------


def test_mutation_fixed_filename_symlink_escape_not_just_trial_dirs(tmp_path: Path) -> None:
    """A fixed child filename (ledger.jsonl) replaced with a symlink escaping the bundle, with
    trial_dirs itself left untouched - distinct from the trial_dirs-escape tests above, which
    only exercise _safe_join on the manifest-declared directory, not each file within it."""
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    t = manifest["trials"][0]
    trial_dir = bundle / manifest["trial_dirs"][t["trial_id"]]
    ledger_path = trial_dir / "ledger.jsonl"
    outside = tmp_path / "outside-ledger.jsonl"
    shutil.copyfile(ledger_path, outside)
    ledger_path.unlink()
    ledger_path.symlink_to(outside)
    # manifest.json and its receipt are untouched - only a file on disk was swapped for a symlink.
    report = verify.verify_bundle(bundle)
    assert any(
        "symlink" in p["reason"] and p["trial_id"] == t["trial_id"] for p in report["problems"]
    )


def test_mutation_drop_twenty_trials_but_claim_full_batch(tmp_path: Path) -> None:
    """check_review.py's drop_twenty: keep only 1 trial per cell out of 3, leave
    trial_count/trials_per_cell/cells_summary claiming the full 30. Must be caught by cell
    accounting, not merely by counting 10 distinct cell names."""
    bundle = _copy_real_bundle(tmp_path)

    def mutate(manifest: dict[str, Any]) -> None:
        manifest["trials"] = [t for t in manifest["trials"] if t["trial_id"].endswith("-0")]

    _remanifest(bundle, mutate)
    report = verify.verify_bundle(bundle)
    assert report["trial_count"] == 10
    assert any("expected" in p["reason"] and "trial" in p["reason"] for p in report["problems"])


def test_mutation_protocol_fields_contradicted_end_to_end(tmp_path: Path) -> None:
    """check_review.py's contradict_protocol, run through verify_bundle end to end (not just
    validate_receipt directly): every trial's kill/barrier/TTL/sweep fields falsified while
    passed stays true."""
    bundle = _copy_real_bundle(tmp_path)

    def mutate(manifest: dict[str, Any]) -> None:
        for t in manifest["trials"]:
            t.update(
                worker_a_killed=False, worker_a_exit_status=0, barrier_observed=False,
                barrier_point=None, ttl_boundary_confirmed=False, measured_age_at_decision=-100,
                sweep_invoked=False, sweep_return_value=999,
            )

    _remanifest(bundle, mutate)
    report = verify.verify_bundle(bundle)
    assert report["problems"]  # caught (by schema-level structural checks, at minimum)


def test_mutation_cells_summary_and_all_agree_fabricated(tmp_path: Path) -> None:
    """check_review.py's contradict_summary: an invented cells_summary and all_agree=false must
    be caught by comparing against the summary verify_bundle recomputes from manifest.trials,
    not merely accepted as an unread field."""
    bundle = _copy_real_bundle(tmp_path)

    def mutate(manifest: dict[str, Any]) -> None:
        manifest["cells_summary"] = {"invented": {"count": 999}}
        manifest["all_agree"] = False

    _remanifest(bundle, mutate)
    report = verify.verify_bundle(bundle)
    assert any("cells_summary" in p["reason"] for p in report["problems"])
    assert any("all_agree" in p["reason"] for p in report["problems"])


def test_mutation_provenance_fabricated_end_to_end(tmp_path: Path) -> None:
    """check_review.py's contradict_provenance: zeroed prediction hash, emptied release_info,
    and fabricated per-trial version/hash fields, run through verify_bundle end to end."""
    bundle = _copy_real_bundle(tmp_path)

    def mutate(manifest: dict[str, Any]) -> None:
        manifest["prediction_sha256"] = "0" * 64
        manifest["release_info"] = {}
        for t in manifest["trials"]:
            t["safeagent_version"] = "0.0.0"
            t["safeagent_dist_sha256"] = "0" * 64

    _remanifest(bundle, mutate)
    report = verify.verify_bundle(bundle)
    assert any("prediction_sha256" in p["reason"] or "prediction" in p["reason"].lower()
               for p in report["problems"])
    assert any("release_info" in p["reason"] for p in report["problems"])


def test_mutation_remove_all_support_files_keeps_only_ledger_and_final_snapshot(
    tmp_path: Path,
) -> None:
    """check_review.py's remove_support: delete every retained file for a trial except
    ledger.jsonl and claim_final.sqlite. Despite the summary's claims about prior states,
    barriers, and replay, the required evidence for those claims is simply gone."""
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    for _trial_id, rel_dir in manifest["trial_dirs"].items():
        trial_dir = bundle / rel_dir
        for p in trial_dir.iterdir():
            if p.is_file() and p.name not in {"ledger.jsonl", "claim_final.sqlite"}:
                p.unlink()
    report = verify.verify_bundle(bundle)
    assert any("missing required evidence file" in p["reason"] for p in report["problems"])


def test_malformed_effect_count_through_verify_bundle_is_not_a_crash(tmp_path: Path) -> None:
    """check_review.py's malformed_count, through the actual verify_bundle entry point (not
    validate_receipt called directly): must never raise."""
    bundle = _copy_real_bundle(tmp_path)

    def mutate(manifest: dict[str, Any]) -> None:
        manifest["trials"][0]["effect_count"] = "bad"

    _remanifest(bundle, mutate)
    verify.verify_bundle(bundle)  # must not raise


def test_mutation_declared_trial_count_disagrees_with_actual_trials(tmp_path: Path) -> None:
    """check_remaining.py's declared_trial_count_999: manifest.trial_count claims 999 while
    manifest.trials still holds the real 30 records. The cell/index accounting already checks
    len(manifest.trials) against the hardcoded expected total; it never checked the manifest's
    own self-reported trial_count field for internal consistency against the list it is
    supposedly describing."""
    bundle = _copy_real_bundle(tmp_path)

    def mutate(manifest: dict[str, Any]) -> None:
        manifest.update(trial_count=999)

    _remanifest(bundle, mutate)
    report = verify.verify_bundle(bundle)
    assert any(
        "trial_count=999" in p["reason"] and "does not match" in p["reason"]
        for p in report["problems"]
    )


def test_mutation_omit_harness_and_ledger_sources_from_embedded_inventory(
    tmp_path: Path,
) -> None:
    """check_remaining.py's omit_harness_ledger_sources: every non-sqlite_store entry (the
    harness/ledger/canonical sources) is dropped from manifest.embedded_source_sha256 and its
    sources/ file deleted, leaving only the two safeagent_exec_guard sqlite_store.py copies. The
    old loop only ever validated whatever manifest.embedded_source_sha256 itself listed, so an
    entry omitted from that dict (and its file) was never missed."""
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    removed = [
        name for name in manifest["embedded_source_sha256"]
        if not name.startswith("safeagent_exec_guard/")
    ]
    # The mutation removes exactly the required, non-release-specific sources - confirming the
    # bundle under test has no drift against the same imported constant the fix now checks
    # against, so this test cannot pass merely because the bundle happens to omit the check.
    assert set(removed) == {
        p for p in REQUIRED_EMBEDDED_SOURCE_FILES if not p.startswith("safeagent_exec_guard/")
    }

    def mutate(manifest: dict[str, Any]) -> None:
        for name in removed:
            del manifest["embedded_source_sha256"][name]

    _remanifest(bundle, mutate)
    for name in removed:
        (bundle / "sources" / name).unlink()
    report = verify.verify_bundle(bundle)
    for name in removed:
        assert any(
            name in p["reason"] and "missing" in p["reason"].lower()
            for p in report["problems"]
        ), f"omitted required source {name!r} was not flagged"


def test_mutation_missing_retry_claim_event_in_worker_b_stdout(tmp_path: Path) -> None:
    """check_remaining.py's missing_retry_claim_event: the 'retry_claim' line is stripped from
    a passing trial's retained worker_b.stdout, leaving 'retry_complete' intact. The old check
    only inspected retry_claim's *content* when present and never required the event to exist,
    so a passing trial with the event removed outright went unnoticed."""
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    t = next(
        t for t in manifest["trials"]
        if t["case"] == "pending_expired_swept" and t["release"] == "0.1.24" and t["passed"]
    )
    trial_dir = bundle / manifest["trial_dirs"][t["trial_id"]]
    stdout_path = trial_dir / "worker_b.stdout"
    lines = [ln for ln in stdout_path.read_text().splitlines() if '"retry_claim"' not in ln]
    stdout_path.write_text("\n".join(lines) + "\n")
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == t["trial_id"]]
    assert any("no 'retry_claim' event" in r for r in reasons)


def test_mutation_reversed_raw_attempt_order_in_ledger(tmp_path: Path) -> None:
    """check_remaining.py's reversed_raw_attempt_order: for a trial with two real ledger
    effects, the raw ledger.jsonl records are rewritten in reverse order (with a freshly
    recomputed, internally valid hash chain) so worker-b-retry's crossing appears to precede
    worker-a's original attempt. classify_effect_count only compares counts/digests and would
    not notice; lineage must be checked directly against the re-parsed attempt sequence."""
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    t = next(
        t for t in manifest["trials"]
        if t["case"] == "pending_expired_swept" and t["release"] == "0.1.23"
    )
    trial_dir = bundle / manifest["trial_dirs"][t["trial_id"]]
    ledger_path = trial_dir / "ledger.jsonl"
    records = [json.loads(line)["record"] for line in ledger_path.read_text().splitlines()]
    assert len(records) == 2  # sanity: this is genuinely a two-effect trial
    head = GENESIS
    entries = []
    for record in reversed(records):
        h = chain(head, record)
        entries.append({"prev": head, "record": record, "hash": h})
        head = h
    ledger_path.write_text("".join(json.dumps(entry) + "\n" for entry in entries))
    obs = verify.observe_ledger(ledger_path)
    (trial_dir / "ledger_observation.json").write_text(json.dumps(obs.to_json()))
    # Manifest/receipt untouched - only the raw ledger and its saved observation were edited, so
    # this isolates the attempt-order check from the raw-vs-saved-observation consistency check.
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == t["trial_id"]]
    assert any("attempt order" in r and "reversed" in r for r in reasons)
