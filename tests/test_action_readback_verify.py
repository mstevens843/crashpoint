from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from crashpoint.canonical import chain, receipt
from crashpoint.harness import action_readback_verify as verify
from crashpoint.harness.action_readback_runtime import PREFIX
from crashpoint.ledger.core import GENESIS

_ROOT = Path(__file__).resolve().parents[1]
_REAL_BUNDLE = _ROOT / "evidence" / "action_readback" / "action_readback_self_reviewed_v2"


def _copy_real_bundle(tmp_path: Path) -> Path:
    if not _REAL_BUNDLE.exists():
        pytest.skip(f"recorded evidence bundle not found at {_REAL_BUNDLE}")
    dest = tmp_path / "bundle"
    shutil.copytree(_REAL_BUNDLE, dest)
    return dest


def _remanifest(bundle_dir: Path, mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Load manifest.json, apply `mutate` in place, sync every trial's own retained receipt.json
    to match (so a mutation to manifest.trials cannot be caught only by a receipt.json/manifest
    disagreement rather than the semantic check it is meant to exercise), recompute the outer
    receipt hash, and write it back."""
    manifest_path = bundle_dir / "manifest.json"
    manifest: dict[str, Any] = json.loads(manifest_path.read_text())
    mutate(manifest)
    for row in manifest.get("trials", []):
        if not isinstance(row, dict):
            continue
        trial_dir = manifest.get("trial_dirs", {}).get(row.get("trial_id"))
        if trial_dir is None:
            continue
        receipt_path = bundle_dir / trial_dir / "receipt.json"
        if receipt_path.exists():
            receipt_path.write_text(json.dumps(row))
    body = dict(manifest)
    body.pop("receipt", None)
    manifest["receipt"] = receipt(body)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def _trial_by_id(manifest: dict[str, Any], trial_id: str) -> dict[str, Any]:
    return next(t for t in manifest["trials"] if t["trial_id"] == trial_id)


def test_verify_bundle_recorded_evidence_has_no_problems(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    report = verify.verify_bundle(bundle)
    assert report["manifest_found"] is True
    assert report["manifest_receipt_valid"] is True
    assert report["trial_count"] == 18
    assert report["problems"] == []


def test_verify_bundle_survives_relocation(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    relocated = tmp_path / "elsewhere" / "moved-bundle"
    relocated.parent.mkdir(parents=True)
    shutil.move(str(bundle), str(relocated))
    report = verify.verify_bundle(relocated)
    assert report["problems"] == []


def test_verify_bundle_missing_manifest(tmp_path: Path) -> None:
    empty = tmp_path / "empty-bundle"
    empty.mkdir()
    report = verify.verify_bundle(empty)
    assert report["manifest_found"] is False
    assert any("manifest.json not found" in p["reason"] for p in report["problems"])


# --------------------------------------------------------------------------------------------
# Counts / duplicates / declared-vs-actual.
# --------------------------------------------------------------------------------------------


def test_mutation_omit_one_trial(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)

    def mutate(m: dict[str, Any]) -> None:
        m["trials"] = [t for t in m["trials"] if t["trial_id"] != "clean-2"]

    _remanifest(bundle, mutate)
    report = verify.verify_bundle(bundle)
    assert any("expected 3" in p["reason"] for p in report["problems"])
    assert any("manifest.trials has 17 entries" in p["reason"] for p in report["problems"])


def test_mutation_duplicate_trial(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)

    def mutate(m: dict[str, Any]) -> None:
        m["trials"].append(dict(_trial_by_id(m, "clean-0")))

    _remanifest(bundle, mutate)
    report = verify.verify_bundle(bundle)
    assert any("duplicate trial_id" in p["reason"] for p in report["problems"])


def test_mutation_wrong_declared_trial_count(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    _remanifest(bundle, lambda m: m.update(trial_count=999))
    report = verify.verify_bundle(bundle)
    assert any(
        "manifest.trial_count=999" in p["reason"] and "does not match" in p["reason"]
        for p in report["problems"]
    )


def test_mutation_mixed_run_id(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)

    def mutate(m: dict[str, Any]) -> None:
        _trial_by_id(m, "clean-0")["run_id"] = "not-the-real-run-id"

    _remanifest(bundle, mutate)
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "clean-0"]
    assert any("journal.jsonl" in r for r in reasons)


# --------------------------------------------------------------------------------------------
# False status / changed identity / effect-identity substitution.
# --------------------------------------------------------------------------------------------


def test_mutation_false_admission_commit_confirmed(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    _remanifest(bundle, lambda m: _trial_by_id(m, "clean-0").update(
        admission_commit_confirmed=False
    ))
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "clean-0"]
    assert any("admission_commit_confirmed=True" in r for r in reasons)


def test_mutation_changed_admission_payload_digest(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    _remanifest(bundle, lambda m: _trial_by_id(m, "clean-0").update(
        admission_payload_digest="f" * 64
    ))
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "clean-0"]
    assert any("does not match admission.sqlite payload_digest" in r for r in reasons)
    assert any("digests_match_admission" in r for r in reasons)


def test_mutation_effect_identity_substituted_for_admitted_action_id(tmp_path: Path) -> None:
    """A different action_id substituted into the receipt while the raw admission/ledger evidence
    still concerns the real one - effect identity must not be trusted as pre-dispatch identity
    just because the receipt claims a match."""
    bundle = _copy_real_bundle(tmp_path)
    _remanifest(bundle, lambda m: _trial_by_id(m, "clean-0").update(
        action_id="22222222-2222-2222-2222-222222222222"
    ))
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "clean-0"]
    assert any("admission.sqlite has no row for this trial's action_id" in r for r in reasons)
    assert any("effect_count contradicts raw ledger bytes" in r for r in reasons)


def test_mutation_two_distinct_admitted_actions_with_identical_payload_are_not_merged(
    tmp_path: Path,
) -> None:
    """Two different real trials (distinct action_ids, identical harmless payload) must never be
    treated as one admission - each trial's own admission.sqlite completions must only ever
    belong to that trial's own action_id."""
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    clean0 = _trial_by_id(manifest, "clean-0")
    clean1 = _trial_by_id(manifest, "clean-1")
    assert clean0["action_id"] != clean1["action_id"]
    assert clean0["admission_payload_digest"] == clean1["admission_payload_digest"]
    db0 = bundle / manifest["trial_dirs"][clean0["trial_id"]] / "admission.sqlite"
    conn = sqlite3.connect(db0)
    try:
        conn.execute(
            "INSERT INTO completions VALUES (?, ?, ?, ?)",
            (clean1["action_id"], f"{clean1['action_id']}:worker-a", "OK", "2026-01-01T00:00:00Z"),
        )
        conn.commit()
    finally:
        conn.close()
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "clean-0"]
    assert any("different action_id" in r for r in reasons)


# --------------------------------------------------------------------------------------------
# Ledger-side: fabricated counts, reversed order, changed digests.
# --------------------------------------------------------------------------------------------


def test_mutation_fabricated_zero_effect_count(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    _remanifest(bundle, lambda m: _trial_by_id(m, "clean-0").update(
        effect_count=0, effect_ledger_classification="ZERO"
    ))
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "clean-0"]
    assert any("effect_attempt_ids must be a list of length effect_count" in r for r in reasons)


def test_mutation_reversed_raw_attempt_order(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    trial = _trial_by_id(manifest, "naive_retry-0")
    trial_dir = bundle / manifest["trial_dirs"][trial["trial_id"]]
    ledger_path = trial_dir / "ledger.jsonl"
    records = [json.loads(line)["record"] for line in ledger_path.read_text().splitlines()]
    assert len(records) == 2
    head = GENESIS
    entries = []
    for record in reversed(records):
        h = chain(head, record)
        entries.append({"prev": head, "record": record, "hash": h})
        head = h
    ledger_path.write_text("".join(json.dumps(e) + "\n" for e in entries))
    # Manifest/receipt untouched - only the raw ledger was edited, isolating the attempt-order
    # check from the manifest-level receipt-hash/journal checks.
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "naive_retry-0"]
    assert any("attempt order is reversed" in r for r in reasons)
    assert any("does not match the raw ledger's recorded attempt sequence" in r for r in reasons)


def test_mutation_changed_effect_payload_digest_on_disk(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    trial = _trial_by_id(manifest, "clean-0")
    trial_dir = bundle / manifest["trial_dirs"][trial["trial_id"]]
    ledger_path = trial_dir / "ledger.jsonl"
    lines = ledger_path.read_text().splitlines()
    entry = json.loads(lines[0])
    entry["record"]["payload_digest"] = "e" * 64
    entry["hash"] = chain(GENESIS, entry["record"])
    ledger_path.write_text(json.dumps(entry) + "\n" + "\n".join(lines[1:]))
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "clean-0"]
    assert any("digests_match_admission" in r for r in reasons)


# --------------------------------------------------------------------------------------------
# Readback availability: missing/truncated ledger, malformed sqlite, missing worker events,
# nonzero observer exit trusted as a full observation.
# --------------------------------------------------------------------------------------------


def test_mutation_missing_ledger_file(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    trial_dir = bundle / manifest["trial_dirs"]["clean-0"]
    (trial_dir / "ledger.jsonl").unlink()
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "clean-0"]
    assert any("missing required evidence file: ledger.jsonl" in r for r in reasons)


def test_mutation_truncated_ledger_file(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    trial_dir = bundle / manifest["trial_dirs"]["clean-0"]
    ledger_path = trial_dir / "ledger.jsonl"
    ledger_path.write_bytes(ledger_path.read_bytes()[:10])
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "clean-0"]
    assert any("contradicts raw ledger bytes" in r for r in reasons)


def test_mutation_malformed_admission_sqlite(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    trial_dir = bundle / manifest["trial_dirs"]["clean-0"]
    (trial_dir / "admission.sqlite").write_bytes(b"not a sqlite database")
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "clean-0"]
    assert any("admission.sqlite has no row" in r for r in reasons)


def test_mutation_missing_worker_started_event(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    trial_dir = bundle / manifest["trial_dirs"]["clean-0"]
    p = trial_dir / "worker_a.stdout"
    lines = [ln for ln in p.read_text().splitlines() if '"worker_started"' not in ln]
    p.write_text("\n".join(lines) + "\n")
    report = verify.verify_bundle(bundle)
    reasons = [p2["reason"] for p2 in report["problems"] if p2["trial_id"] == "clean-0"]
    assert any("no 'worker_started' event" in r for r in reasons)


def test_mutation_nonzero_observer_exit_with_success_report(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    _remanifest(bundle, lambda m: _trial_by_id(m, "clean-0").update(observer_exit_status=1))
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "clean-0"]
    assert any("nonzero observer exit must never be trusted" in r for r in reasons)


# --------------------------------------------------------------------------------------------
# Receipt-level booleans / numbers.
# --------------------------------------------------------------------------------------------


def test_mutation_false_externally_verified_on_unavailable_readback(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)

    def mutate(m: dict[str, Any]) -> None:
        t = _trial_by_id(m, "unavailable_readback-0")
        t["observed_result"]["externally_verified"] = True
        t["expected_result"]["externally_verified"] = True

    _remanifest(bundle, mutate)
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "unavailable_readback-0"]
    assert any("must be False" in r for r in reasons)


def test_malformed_effect_count_type_through_verify_bundle_is_not_a_crash(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    _remanifest(bundle, lambda m: _trial_by_id(m, "clean-0").update(effect_count=1.5))
    verify.verify_bundle(bundle)  # must not raise


# --------------------------------------------------------------------------------------------
# Provenance: required source inventory, prediction hash/content, path confinement.
# --------------------------------------------------------------------------------------------


def test_mutation_omit_verifier_source_from_embedded_inventory(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    target = "harness/action_readback_verify.py"
    assert target in manifest["embedded_source_sha256"]

    def mutate(m: dict[str, Any]) -> None:
        del m["embedded_source_sha256"][target]

    _remanifest(bundle, mutate)
    (bundle / "sources" / target).unlink()
    report = verify.verify_bundle(bundle)
    assert any(
        target in p["reason"] and "missing a required entry" in p["reason"]
        for p in report["problems"]
    )
    assert any(
        target in p["reason"] and "absent from the bundle on disk" in p["reason"]
        for p in report["problems"]
    )


def test_mutation_altered_prediction_sha256(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    _remanifest(bundle, lambda m: m.update(prediction_sha256="0" * 64))
    report = verify.verify_bundle(bundle)
    assert any(
        "does not match manifest.prediction_sha256" in p["reason"] for p in report["problems"]
    )


def test_mutation_altered_retained_prediction_content(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    p = bundle / "prediction.json"
    data = json.loads(p.read_text())
    data["cases"]["clean"]["external_outcome"] = "TAMPERED"
    p.write_text(json.dumps(data))
    report = verify.verify_bundle(bundle)
    reasons = [pr["reason"] for pr in report["problems"]]
    assert any("does not match manifest.prediction_embedded_sha256" in r for r in reasons)
    assert any(
        pr["trial_id"] == "clean-0" and "does not match the embedded prediction" in pr["reason"]
        for pr in report["problems"]
    )


def test_verify_bundle_rejects_absolute_trial_dir_escape(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    manifest["trial_dirs"]["clean-0"] = "/etc/passwd"
    body = dict(manifest)
    body.pop("receipt", None)
    manifest["receipt"] = receipt(body)
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "clean-0"]
    assert any("is absolute, not relative to the bundle root" in r for r in reasons)


def test_mutation_fixed_filename_symlink_escape(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    trial_dir = bundle / manifest["trial_dirs"]["clean-0"]
    p = trial_dir / "ledger.jsonl"
    p.unlink()
    p.symlink_to("/etc/passwd")
    report = verify.verify_bundle(bundle)
    reasons = [p2["reason"] for p2 in report["problems"] if p2["trial_id"] == "clean-0"]
    assert any("symlink whose target resolves outside the bundle root" in r for r in reasons)


def test_mutation_unavailable_readback_must_not_retain_ledger_jsonl(tmp_path: Path) -> None:
    """The real bundle correctly has no ledger.jsonl for unavailable_readback trials. If one is
    added back (e.g. a bundle author trying to make the case 'look' more complete), that itself
    must be flagged - the deliberate absence IS the evidence for this negative control."""
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    trial_dir = bundle / manifest["trial_dirs"]["unavailable_readback-0"]
    assert not (trial_dir / "ledger.jsonl").exists()
    (trial_dir / "ledger.jsonl").write_bytes(b"")
    report = verify.verify_bundle(bundle)
    reasons = [
        p["reason"] for p in report["problems"] if p["trial_id"] == "unavailable_readback-0"
    ]
    assert any("its deliberate absence is exactly what this negative control" in r for r in reasons)


# --------------------------------------------------------------------------------------------
# Journal cross-check.
# --------------------------------------------------------------------------------------------


def test_mutation_journal_content_disagrees_with_manifest(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    journal_path = bundle / "journal.jsonl"
    lines = journal_path.read_text().splitlines()
    out = []
    for line in lines:
        entry = json.loads(line)
        if entry["trial_id"] == "clean-0":
            entry["receipt"] = dict(entry["receipt"], effect_count=999)
        out.append(json.dumps(entry))
    journal_path.write_text("\n".join(out) + "\n")
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "clean-0"]
    assert any("journal.jsonl's record for this trial_id differs" in r for r in reasons)


def test_mutation_journal_missing_a_trial_present_in_the_manifest(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    journal_path = bundle / "journal.jsonl"
    lines = [
        ln for ln in journal_path.read_text().splitlines()
        if json.loads(ln)["trial_id"] != "clean-0"
    ]
    journal_path.write_text("\n".join(lines) + "\n")
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "clean-0"]
    assert any("journal.jsonl does not" in r for r in reasons)


def test_verify_bundle_missing_journal_file(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    (bundle / "journal.jsonl").unlink()
    report = verify.verify_bundle(bundle)
    assert any("journal.jsonl is missing" in p["reason"] for p in report["problems"])


# --------------------------------------------------------------------------------------------
# Second-pass findings: an external review (Codex) ran its own independent mutation script
# against this same bundle and found 12 real gaps the first self-review pass missed - 9 silently
# accepted, 3 crashed instead of a structured diagnostic. Each is ported here as its own
# permanent test, matching that review's exact scenario.
# --------------------------------------------------------------------------------------------


def test_mutation_deleted_admission_completion_row(tmp_path: Path) -> None:
    """The receipt still claims worker_a_local_receipt, but the underlying completions table
    (the only place that claim can be independently checked against) has been emptied."""
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    trial_dir = bundle / manifest["trial_dirs"]["clean-0"]
    conn = sqlite3.connect(trial_dir / "admission.sqlite")
    try:
        conn.execute("DELETE FROM completions")
        conn.commit()
    finally:
        conn.close()
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "clean-0"]
    assert any(
        "claims a local completion receipt" in r and "no row for that attempt_id" in r
        for r in reasons
    )


def test_mutation_false_local_receipt_omits_a_real_completion(tmp_path: Path) -> None:
    """The inverse of the above: the receipt claims NO local receipt, but the completions table
    genuinely has one for that attempt_id - a real completion silently dropped from the receipt."""
    bundle = _copy_real_bundle(tmp_path)

    def mutate(m: dict[str, Any]) -> None:
        _trial_by_id(m, "clean-0")["worker_a_local_receipt"] = None

    _remanifest(bundle, mutate)
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "clean-0"]
    assert any(
        "is null" in r and "completions table DOES have a row" in r for r in reasons
    )


def test_mutation_wrong_admission_read_event_identity(tmp_path: Path) -> None:
    """worker_a.stdout's own retained admission_read event is falsified to a different
    action_id/digest than the trial's own - checking only that the event EXISTS, never its
    content, would miss this."""
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    trial_dir = bundle / manifest["trial_dirs"]["clean-0"]
    p = trial_dir / "worker_a.stdout"
    prefix = "CRASHPOINT_ACTION_READBACK "
    lines = p.read_text().splitlines()
    out = []
    for line in lines:
        if line.startswith(prefix) and '"admission_read"' in line:
            event = json.loads(line[len(prefix):])
            event["action_id"] = "unrelated-action"
            event["admission_payload_digest"] = "0" * 64
            out.append(prefix + json.dumps(event))
        else:
            out.append(line)
    p.write_text("\n".join(out) + "\n")
    report = verify.verify_bundle(bundle)
    reasons = [p2["reason"] for p2 in report["problems"] if p2["trial_id"] == "clean-0"]
    assert any("admission_read.action_id" in r for r in reasons)
    assert any("admission_read.admission_payload_digest" in r for r in reasons)


def test_mutation_false_effect_payload_digests(tmp_path: Path) -> None:
    """The receipt's own effect_payload_digests list is falsified directly - digests_match_
    admission alone recomputing correctly is not enough if the raw list itself is never
    compared against what the ledger actually retains."""
    bundle = _copy_real_bundle(tmp_path)
    _remanifest(bundle, lambda m: _trial_by_id(m, "clean-0").update(
        effect_payload_digests=["0" * 64]
    ))
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "clean-0"]
    assert any(
        "effect_payload_digests" in r
        and "does not match the raw ledger's own retained digests" in r
        for r in reasons
    )


def test_mutation_false_terminal_anchor(tmp_path: Path) -> None:
    """receiver_seal_dump_count/receiver_seal_dump_head (the post-seal anchor) are falsified;
    nothing previously cross-checked them against an independent re-parse of the raw ledger."""
    bundle = _copy_real_bundle(tmp_path)
    _remanifest(bundle, lambda m: _trial_by_id(m, "clean-0").update(
        receiver_seal_dump_count=999, receiver_seal_dump_head="0" * 64
    ))
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "clean-0"]
    assert any("receiver_seal_dump_count=999" in r for r in reasons)
    assert any("receiver_seal_dump_head=" in r and "does not match" in r for r in reasons)


def test_mutation_duplicate_journal_trial_id(tmp_path: Path) -> None:
    """journal.jsonl has two entries for the same trial_id - a dict-keyed parse would silently
    let the second overwrite the first rather than reporting the duplicate itself."""
    bundle = _copy_real_bundle(tmp_path)
    journal_path = bundle / "journal.jsonl"
    lines = journal_path.read_text().splitlines()
    journal_path.write_text(journal_path.read_text() + lines[0] + "\n")
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] is not None]
    assert any("entries for this trial_id, expected exactly 1" in r for r in reasons)


def test_mutation_extra_journal_trial_id_not_in_manifest(tmp_path: Path) -> None:
    """journal.jsonl has an entry for a trial_id that was never run at all - the old check only
    verified manifest.trials -> journal.jsonl coverage, never the reverse direction."""
    bundle = _copy_real_bundle(tmp_path)
    journal_path = bundle / "journal.jsonl"
    journal_path.write_text(
        journal_path.read_text()
        + json.dumps({"trial_id": "never-run", "receipt": {}}) + "\n"
    )
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "never-run"]
    assert any("does not appear anywhere in manifest.trials" in r for r in reasons)


def test_mutation_prediction_json_symlink_escape(tmp_path: Path) -> None:
    """prediction.json at the BUNDLE ROOT (not inside a trial dir) replaced with a symlink to a
    file outside the bundle - the old code read this path directly, with no confinement at all."""
    bundle = _copy_real_bundle(tmp_path)
    external = tmp_path / "outside-prediction.json"
    shutil.copy2(bundle / "prediction.json", external)
    (bundle / "prediction.json").unlink()
    (bundle / "prediction.json").symlink_to(external)
    report = verify.verify_bundle(bundle)
    assert any(
        "prediction.json" in p["reason"] and "resolves outside the bundle root" in p["reason"]
        for p in report["problems"]
    )


def test_mutation_journal_jsonl_symlink_escape(tmp_path: Path) -> None:
    """Same as above for journal.jsonl - also a bundle-root fixed file, also unconfined before
    this fix."""
    bundle = _copy_real_bundle(tmp_path)
    external = tmp_path / "outside-journal.jsonl"
    shutil.copy2(bundle / "journal.jsonl", external)
    (bundle / "journal.jsonl").unlink()
    (bundle / "journal.jsonl").symlink_to(external)
    report = verify.verify_bundle(bundle)
    assert any(
        "journal.jsonl" in p["reason"] and "resolves outside the bundle root" in p["reason"]
        for p in report["problems"]
    )


def test_malformed_observer_report_array_is_not_a_crash(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    trial_dir = bundle / manifest["trial_dirs"]["clean-0"]
    (trial_dir / "observer_report.json").write_text("[]")
    report = verify.verify_bundle(bundle)  # must not raise
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "clean-0"]
    assert any("observer_report.json must be a JSON object" in r for r in reasons)


def test_malformed_cells_summary_entry_array_is_not_a_crash(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    _remanifest(bundle, lambda m: m["cells_summary"].update(clean=[]))
    report = verify.verify_bundle(bundle)  # must not raise
    assert any(
        "cells_summary['clean'] must be an object" in p["reason"] for p in report["problems"]
    )


def test_malformed_ledger_invalid_utf8_is_not_a_crash(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    trial_dir = bundle / manifest["trial_dirs"]["clean-0"]
    (trial_dir / "ledger.jsonl").write_bytes(b"\xff")
    report = verify.verify_bundle(bundle)  # must not raise
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "clean-0"]
    assert any("contradicts raw ledger bytes" in r for r in reasons)


# --------------------------------------------------------------------------------------------
# Round 3: a self-audit performed the same way Codex's review was performed - enumerate every
# event the runtime emits and every field/column the schema declares, then check which are
# actually cross-checked. Found by asking "what would Codex's own methodology find next", not by
# waiting for another external pass. 11 further gaps, all real (empirically confirmed accepted
# before each fix, all confirmed rejected after).
# --------------------------------------------------------------------------------------------


def _rewrite_worker_events(
    path: Path, mutator: Callable[[dict[str, Any]], dict[str, Any] | None]
) -> None:
    lines = path.read_text().splitlines()
    out = []
    for line in lines:
        if not line.startswith(PREFIX):
            out.append(line)
            continue
        event = json.loads(line[len(PREFIX):])
        result = mutator(event)
        if result is None:
            continue
        out.append(PREFIX + json.dumps(result))
    path.write_text("\n".join(out) + "\n")


def test_mutation_worker_b_started_pid_falsified(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    p = bundle / manifest["trial_dirs"]["naive_retry-0"] / "worker_b.stdout"
    _rewrite_worker_events(
        p, lambda e: {**e, "pid": 999999} if e.get("event") == "worker_started" else e
    )
    report = verify.verify_bundle(bundle)
    reasons = [p2["reason"] for p2 in report["problems"] if p2["trial_id"] == "naive_retry-0"]
    assert any("worker_b_pid=" in r and "worker_started.pid=999999" in r for r in reasons)


def test_mutation_worker_b_admission_read_dropped(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    p = bundle / manifest["trial_dirs"]["naive_retry-0"] / "worker_b.stdout"
    _rewrite_worker_events(p, lambda e: None if e.get("event") == "admission_read" else e)
    report = verify.verify_bundle(bundle)
    reasons = [p2["reason"] for p2 in report["problems"] if p2["trial_id"] == "naive_retry-0"]
    assert any("no 'admission_read' event" in r for r in reasons)


def test_mutation_effect_ack_dispatch_digest_falsified(tmp_path: Path) -> None:
    """The worker's OWN retained claim of what it dispatched (worker_a.stdout's effect_ack
    event), independent of the receiver-side ledger check that already exists."""
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    p = bundle / manifest["trial_dirs"]["clean-0"] / "worker_a.stdout"
    _rewrite_worker_events(
        p,
        lambda e: {**e, "dispatch_payload_digest": "0" * 64}
        if e.get("event") == "effect_ack" else e,
    )
    report = verify.verify_bundle(bundle)
    reasons = [p2["reason"] for p2 in report["problems"] if p2["trial_id"] == "clean-0"]
    assert any("effect_ack.dispatch_payload_digest" in r for r in reasons)


def test_mutation_observer_pid_falsified(tmp_path: Path) -> None:
    """Cross-checked against observer.stdout's own retained 'observer_started' event - genuinely
    independent raw process output, not observer_report.json (which this same harness process
    wrote from the same in-memory data as the receipt's own observer_pid field)."""
    bundle = _copy_real_bundle(tmp_path)
    _remanifest(bundle, lambda m: _trial_by_id(m, "clean-0").update(observer_pid=999999))
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "clean-0"]
    assert any(
        "observer_pid=999999" in r and "observer.stdout observer_started.pid=" in r
        for r in reasons
    )


def test_mutation_trial_crashpoint_commit_disagrees_with_manifest(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    _remanifest(bundle, lambda m: _trial_by_id(m, "clean-0").update(
        crashpoint_commit="0" * 40
    ))
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "clean-0"]
    assert any("mixed provenance" in r for r in reasons)


def test_mutation_admission_receiver_ref_falsified(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    trial = _trial_by_id(manifest, "clean-0")
    db = bundle / manifest["trial_dirs"]["clean-0"] / "admission.sqlite"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE admissions SET receiver_ref = ? WHERE action_id = ?",
            ("not-the-real-receiver", trial["action_id"]),
        )
        conn.commit()
    finally:
        conn.close()
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "clean-0"]
    assert any("admission.sqlite receiver_ref=" in r for r in reasons)


def test_mutation_admission_timestamp_falsified(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    trial = _trial_by_id(manifest, "clean-0")
    db = bundle / manifest["trial_dirs"]["clean-0"] / "admission.sqlite"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE admissions SET admitted_at_utc = ? WHERE action_id = ?",
            ("1970-01-01T00:00:00+00:00", trial["action_id"]),
        )
        conn.commit()
    finally:
        conn.close()
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "clean-0"]
    assert any("admission.sqlite admitted_at_utc=" in r for r in reasons)


def test_mutation_barrier_observed_flag_disagrees_with_retained_event(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    _remanifest(bundle, lambda m: _trial_by_id(m, "stopped_before_effect-0").update(
        worker_a_barrier_observed=False
    ))
    report = verify.verify_bundle(bundle)
    reasons = [
        p["reason"] for p in report["problems"] if p["trial_id"] == "stopped_before_effect-0"
    ]
    assert any("worker_a_barrier_observed=False disagrees" in r for r in reasons)


def test_mutation_admission_trial_id_column_falsified(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    trial = _trial_by_id(manifest, "clean-0")
    db = bundle / manifest["trial_dirs"]["clean-0"] / "admission.sqlite"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE admissions SET trial_id = ? WHERE action_id = ?",
            ("not-the-real-trial-id", trial["action_id"]),
        )
        conn.commit()
    finally:
        conn.close()
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "clean-0"]
    assert any("admission.sqlite trial_id=" in r for r in reasons)


def test_mutation_admission_case_name_column_falsified(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    trial = _trial_by_id(manifest, "clean-0")
    db = bundle / manifest["trial_dirs"]["clean-0"] / "admission.sqlite"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE admissions SET case_name = ? WHERE action_id = ?",
            ("not-the-real-case", trial["action_id"]),
        )
        conn.commit()
    finally:
        conn.close()
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "clean-0"]
    assert any("admission.sqlite case_name=" in r for r in reasons)


def test_mutation_status_incomplete_when_actually_complete(tmp_path: Path) -> None:
    """The inverse of an existing check: a genuinely complete, all-passing run relabeled
    INCOMPLETE. Caught only indirectly (via all_agree) for the fabricated-failures direction;
    this direction had no check of any kind before this fix."""
    bundle = _copy_real_bundle(tmp_path)
    _remanifest(bundle, lambda m: m.update(status="INCOMPLETE"))
    report = verify.verify_bundle(bundle)
    assert any(
        "manifest.status='INCOMPLETE' does not match the recomputed value 'COMPLETE'" in p["reason"]
        for p in report["problems"]
    )


def test_mutation_cases_list_omits_a_real_case(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    _remanifest(bundle, lambda m: m.update(
        cases=[c for c in m["cases"] if c != "naive_retry"]
    ))
    report = verify.verify_bundle(bundle)
    assert any(
        "manifest.cases=" in p["reason"] and "does not match the canonical case set" in p["reason"]
        for p in report["problems"]
    )


def test_mutation_cases_list_adds_a_fake_case(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    _remanifest(bundle, lambda m: m.update(cases=[*m["cases"], "not_a_real_case"]))
    report = verify.verify_bundle(bundle)
    assert any(
        "manifest.cases=" in p["reason"] and "does not match the canonical case set" in p["reason"]
        for p in report["problems"]
    )


def test_mutation_completion_row_full_content_falsified(tmp_path: Path) -> None:
    """Only comparing completions.outcome (not the full row) would miss a falsified
    completed_at_utc while outcome still happens to match."""
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    trial = _trial_by_id(manifest, "clean-0")
    db = bundle / manifest["trial_dirs"]["clean-0"] / "admission.sqlite"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE completions SET completed_at_utc = ? WHERE action_id = ?",
            ("1970-01-01T00:00:00+00:00", trial["action_id"]),
        )
        conn.commit()
    finally:
        conn.close()
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "clean-0"]
    assert any(
        "does not match the full admission.sqlite completions row" in r for r in reasons
    )


# --------------------------------------------------------------------------------------------
# Round 5 self-audit, continued: the observer's own raw stdout is now retained (matching
# worker_a.stdout/worker_b.stdout) and required, closing the gap where observer_pid was only
# ever cross-checked against observer_report.json - a file this same harness process wrote from
# the same in-memory data, not genuinely independent evidence.
# --------------------------------------------------------------------------------------------


def test_verify_bundle_requires_observer_stdout_file(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    (bundle / manifest["trial_dirs"]["clean-0"] / "observer.stdout").unlink()
    report = verify.verify_bundle(bundle)
    reasons = [p["reason"] for p in report["problems"] if p["trial_id"] == "clean-0"]
    assert any("missing required evidence file: observer.stdout" in r for r in reasons)


def test_mutation_observer_stdout_missing_readback_result_event(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    p = bundle / manifest["trial_dirs"]["clean-0"] / "observer.stdout"
    lines = [ln for ln in p.read_text().splitlines() if '"readback_result"' not in ln]
    p.write_text("\n".join(lines) + "\n")
    report = verify.verify_bundle(bundle)
    reasons = [p2["reason"] for p2 in report["problems"] if p2["trial_id"] == "clean-0"]
    assert any("no 'readback_result' event" in r for r in reasons)


def test_mutation_observer_stdout_raw_state_disagrees_with_receipt(tmp_path: Path) -> None:
    bundle = _copy_real_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    p = bundle / manifest["trial_dirs"]["clean-0"] / "observer.stdout"
    lines = p.read_text().splitlines()
    out = []
    for line in lines:
        if line.startswith(PREFIX) and '"readback_result"' in line:
            event = json.loads(line[len(PREFIX):])
            event["raw_state"] = "READ_FAILED"
            out.append(PREFIX + json.dumps(event))
        else:
            out.append(line)
    p.write_text("\n".join(out) + "\n")
    report = verify.verify_bundle(bundle)
    reasons = [p2["reason"] for p2 in report["problems"] if p2["trial_id"] == "clean-0"]
    assert any("readback_result.raw_state=" in r for r in reasons)
