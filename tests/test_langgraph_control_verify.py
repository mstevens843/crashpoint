from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

pytest.importorskip("langgraph")

from crashpoint.canonical import receipt as canonical_receipt
from crashpoint.harness import langgraph_control as lc
from crashpoint.harness.langgraph_control_verify import verify_bundle
from crashpoint.harness.ledger_readback import (
    parse_ledger_bytes,
    sha256_bytes,
)
from crashpoint.ledger.core import LedgerState

_SRC = Path(__file__).resolve().parents[1] / "src" / "crashpoint"

# The offline verifier's own transitive closure of local imports. Listed explicitly (not
# discovered dynamically) so this test fails loudly if the verifier ever grows a new import that
# needs to be added here and checked, rather than silently skipping it.
_VERIFIER_CLOSURE: tuple[Path, ...] = (
    _SRC / "harness" / "langgraph_control_verify.py",
    _SRC / "harness" / "langgraph_control_receipt.py",
    _SRC / "harness" / "ledger_readback.py",
    _SRC / "canonical.py",
    _SRC / "ledger" / "core.py",
)


def test_verifier_closure_never_imports_langgraph() -> None:
    """Static proof: parse every module the verifier can reach through its own local imports and
    assert none of them names langgraph anywhere. Mirrors tests/test_contract.py's AST-based
    purity check. A static list can miss a FUTURE dynamic/conditional import, though - see
    ``test_verifier_subprocess_cannot_import_langgraph_even_if_it_tried`` below for a runtime proof
    that does not depend on this list staying exhaustive."""
    for path in _VERIFIER_CLOSURE:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    # Exact package-root match only: sibling modules like
                    # langgraph_control_receipt legitimately start with the same prefix.
                    assert alias.name.split(".")[0] != "langgraph", (
                        f"{path.name} imports {alias.name!r}"
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert module.split(".")[0] != "langgraph", (
                    f"{path.name} imports from {module!r}"
                )


def test_verifier_subprocess_cannot_import_langgraph_even_if_it_tried(valid_bundle: Path) -> None:
    """A dynamic, runtime proof rather than a static one: poison sys.modules['langgraph'] = None
    BEFORE the verifier runs, in a fresh subprocess (never the pytest process itself, so this
    cannot pollute other tests). Assigning None to a sys.modules entry makes Python raise
    ImportError immediately for that name or anything under it, regardless of caching, regardless
    of whether the import is direct, indirect, or added later - so this does not rely on the fixed
    AST list above staying exhaustive of every future import path. If verify_bundle (or anything
    it calls, now or in the future) ever imports langgraph, this subprocess fails loudly."""
    script = (
        "import sys; sys.modules['langgraph'] = None\n"
        "from pathlib import Path\n"
        "from crashpoint.harness.langgraph_control_verify import verify_bundle\n"
        f"problems = verify_bundle(Path({str(valid_bundle)!r}))\n"
        "import json; print(json.dumps(problems))\n"
        "raise SystemExit(0 if not problems else 1)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30, check=False
    )
    assert proc.returncode == 0, (
        f"verifier failed with langgraph blocked at import time: "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert json.loads(proc.stdout) == []


@pytest.fixture(scope="module")
def valid_bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("verify-fixture") / "bundle"
    lc.run_primary_control(out, name="verify_fixture")
    return out


def _load(bundle: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((bundle / "receipt.json").read_text()))


def _save(bundle: Path, record: dict[str, Any], *, recompute_hash: bool) -> None:
    if recompute_hash:
        body = {k: v for k, v in record.items() if k != "receipt"}
        record["receipt"] = canonical_receipt(body)
    (bundle / "receipt.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")


def _copy_bundle(valid_bundle: Path, dest: Path) -> Path:
    shutil.copytree(valid_bundle, dest)
    return dest


def _sync_observer_report(bundle: Path, record: dict[str, Any]) -> None:
    """Rewrite observer-report.json to agree with a test's already-mutated
    receipt.observer/.effect/.identity fields, and refresh receipt.observer.report_sha256 to
    match. Used where a test wants to isolate ONE specific deeper check (e.g. the ledger-bytes
    recomputation) rather than incidentally also tripping the independent observer-report
    cross-check - a coherent alternate bundle needs every retained artifact to agree, not just the
    one the test is targeting."""
    report_path = bundle / str(record["observer"]["report_path"])
    report: dict[str, Any] = json.loads(report_path.read_text()) if report_path.is_file() else {}
    report.update(
        {
            "status": record["observer"]["status"],
            "chain_valid": record["observer"]["chain_valid"],
            "first_broken_index": record["observer"]["first_broken_index"],
            "record_count": record["observer"]["record_count"],
            "observed_count": record["effect"]["observed_count"],
            "effect_digests": record["effect"]["effect_digests"],
            "effect_attempt_ids": record["effect"]["effect_attempt_ids"],
            "original_readback_sha256": record["observer"]["original_readback_sha256"],
            "archived_ledger_readback_sha256": (
                record["observer"]["archived_ledger_readback_sha256"]
            ),
            "archive_matches_original": record["observer"]["archive_matches_original"],
            "intent_id": record["identity"]["intent_id"],
        }
    )
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    record["observer"]["report_sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()


def test_valid_bundle_verifies_clean(valid_bundle: Path) -> None:
    assert verify_bundle(valid_bundle) == []


def test_valid_bundle_survives_relocation(valid_bundle: Path, tmp_path: Path) -> None:
    relocated = tmp_path / "somewhere" / "else" / "entirely" / "moved-bundle"
    relocated.parent.mkdir(parents=True)
    shutil.copytree(valid_bundle, relocated)
    assert verify_bundle(relocated) == []


def test_verifier_cli_runs_as_a_subprocess_without_launching_langgraph(valid_bundle: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "crashpoint.harness.langgraph_control_verify", str(valid_bundle)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert proc.returncode == 0
    assert proc.stdout.startswith("PASS:")


def test_missing_bundle_root_reports_actionable_error_not_traceback(tmp_path: Path) -> None:
    nonexistent = str(tmp_path / "nope")
    proc = subprocess.run(
        [sys.executable, "-m", "crashpoint.harness.langgraph_control_verify", nonexistent],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert proc.returncode == 2
    assert "Traceback" not in proc.stderr
    assert "not a directory" in proc.stderr


def test_malformed_receipt_json_reports_actionable_error(
    valid_bundle: Path, tmp_path: Path
) -> None:
    bundle = _copy_bundle(valid_bundle, tmp_path / "malformed")
    (bundle / "receipt.json").write_text("{not valid json")
    problems = verify_bundle(bundle)
    assert len(problems) == 1
    assert "not valid JSON" in problems[0]


def test_invalid_utf8_receipt_returns_a_diagnostic_not_a_traceback_api(
    valid_bundle: Path, tmp_path: Path
) -> None:
    bundle = _copy_bundle(valid_bundle, tmp_path / "invalid-utf8")
    original = (bundle / "receipt.json").read_bytes()
    (bundle / "receipt.json").write_bytes(b"\xff\xfe" + original)
    problems = verify_bundle(bundle)  # must not raise UnicodeDecodeError
    assert len(problems) == 1
    assert "not valid UTF-8" in problems[0]


def test_invalid_utf8_receipt_returns_a_diagnostic_not_a_traceback_cli(
    valid_bundle: Path, tmp_path: Path
) -> None:
    bundle = _copy_bundle(valid_bundle, tmp_path / "invalid-utf8-cli")
    original = (bundle / "receipt.json").read_bytes()
    (bundle / "receipt.json").write_bytes(b"\xff\xfe" + original)
    proc = subprocess.run(
        [sys.executable, "-m", "crashpoint.harness.langgraph_control_verify", str(bundle)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert proc.returncode == 1
    assert "Traceback" not in proc.stdout and "Traceback" not in proc.stderr
    assert "not valid UTF-8" in proc.stdout


def test_result_expected_wrong_type_returns_a_diagnostic_not_a_traceback_api(
    valid_bundle: Path, tmp_path: Path
) -> None:
    bundle = _copy_bundle(valid_bundle, tmp_path / "bad-expected-type")
    record = _load(bundle)
    record["result"]["expected"] = []
    _save(bundle, record, recompute_hash=True)
    problems = verify_bundle(bundle)  # must not raise AttributeError
    assert any("result.expected must be an object" in p for p in problems)


def test_result_expected_wrong_type_returns_a_diagnostic_not_a_traceback_cli(
    valid_bundle: Path, tmp_path: Path
) -> None:
    bundle = _copy_bundle(valid_bundle, tmp_path / "bad-expected-type-cli")
    record = _load(bundle)
    record["result"]["expected"] = []
    _save(bundle, record, recompute_hash=True)
    proc = subprocess.run(
        [sys.executable, "-m", "crashpoint.harness.langgraph_control_verify", str(bundle)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert proc.returncode == 1
    assert "Traceback" not in proc.stdout and "Traceback" not in proc.stderr
    assert "result.expected must be an object" in proc.stdout


def test_missing_required_nullable_field_cannot_pass(valid_bundle: Path, tmp_path: Path) -> None:
    bundle = _copy_bundle(valid_bundle, tmp_path / "missing-field")
    record = _load(bundle)
    del record["effect"]["observed_count"]  # removed entirely, not set to null
    _save(bundle, record, recompute_hash=True)
    problems = verify_bundle(bundle)
    assert any("missing required field: effect.observed_count" in p for p in problems)


def test_stale_receipt_hash_is_caught_without_any_deeper_recompute(
    valid_bundle: Path, tmp_path: Path
) -> None:
    bundle = _copy_bundle(valid_bundle, tmp_path / "stale-hash")
    record = _load(bundle)
    record["case"] = "tampered_case_name"
    _save(bundle, record, recompute_hash=False)  # deliberately leave the outer hash stale
    problems = verify_bundle(bundle)
    assert any("receipt hash mismatch" in p for p in problems)


def test_missing_readback_cannot_pass(valid_bundle: Path, tmp_path: Path) -> None:
    bundle = _copy_bundle(valid_bundle, tmp_path / "missing-readback")
    (bundle / "ledger-readback.jsonl").unlink()
    record = _load(bundle)
    _save(bundle, record, recompute_hash=True)  # hash still matches; only the artifact is gone
    problems = verify_bundle(bundle)
    assert any("missing required artifact" in p and "readback" in p for p in problems)


def test_corrupt_readback_bytes_cannot_pass(valid_bundle: Path, tmp_path: Path) -> None:
    bundle = _copy_bundle(valid_bundle, tmp_path / "corrupt-readback")
    garbage = b"this is not jsonl at all\n"
    (bundle / "ledger-readback.jsonl").write_bytes(garbage)
    record = _load(bundle)
    digest = sha256_bytes(garbage)
    record["observer"]["archived_ledger_readback_sha256"] = digest
    record["observer"]["original_readback_sha256"] = digest
    _save(bundle, record, recompute_hash=True)
    problems = verify_bundle(bundle)
    assert any("corrupt" in p.lower() for p in problems)


def test_wrong_identity_cannot_pass(valid_bundle: Path, tmp_path: Path) -> None:
    bundle = _copy_bundle(valid_bundle, tmp_path / "wrong-identity")
    record = _load(bundle)
    bogus = "lgnc-0000000000000000000000000000ff"
    record["identity"] = {
        "admission_id": bogus, "thread_id": bogus, "intent_id": bogus,
        "identity_binding_note": record["identity"]["identity_binding_note"],
    }
    _save(bundle, record, recompute_hash=True)
    problems = verify_bundle(bundle)
    assert any("does not match count recomputed" in p for p in problems) or any(
        "no accepted_runs row" in p for p in problems
    )


def test_altered_effect_count_cannot_pass(valid_bundle: Path, tmp_path: Path) -> None:
    """A count that disagrees with its own digests/classification is already caught by
    validate_receipt's shallow self-consistency checks. To specifically exercise the DEEPER
    byte-recomputation path (not just re-verify the shallow structural one), make the declared
    count/digests/classification internally self-consistent with EACH OTHER - a coherent-looking
    zero-effect claim - so only recomputing from the retained readback bytes (and independently
    from the observer report, synced here to the same false claim) can catch that it disagrees
    with what actually happened on disk."""
    bundle = _copy_bundle(valid_bundle, tmp_path / "altered-count")
    record = _load(bundle)
    record["effect"]["observed_count"] = 0
    record["effect"]["effect_digests"] = []
    record["effect"]["effect_attempt_ids"] = []
    record["result"]["oracle_classification"] = "LOST"
    record["result"]["measured"]["effect_count"] = 0
    record["result"]["agreement"] = False
    record["result"]["passed"] = False
    _sync_observer_report(bundle, record)
    _save(bundle, record, recompute_hash=True)
    problems = verify_bundle(bundle)
    assert any("observed_count" in p and "does not match" in p for p in problems)


def test_altered_effect_reference_cannot_pass(valid_bundle: Path, tmp_path: Path) -> None:
    bundle = _copy_bundle(valid_bundle, tmp_path / "altered-digest")
    record = _load(bundle)
    record["effect"]["effect_digests"] = ["0" * 64]
    _save(bundle, record, recompute_hash=True)
    problems = verify_bundle(bundle)
    assert any("effect_digests does not match" in p for p in problems)


@pytest.mark.parametrize("bad_count", [True, 1.5])
def test_bool_or_float_is_not_a_valid_integer_count(
    valid_bundle: Path, tmp_path: Path, bad_count: object
) -> None:
    bundle = _copy_bundle(valid_bundle, tmp_path / f"badtype-{bad_count!r}")
    record = _load(bundle)
    record["effect"]["observed_count"] = bad_count
    _save(bundle, record, recompute_hash=True)
    problems = verify_bundle(bundle)
    assert any("observed_count must be a non-negative int" in p for p in problems)


def test_runtime_success_with_unavailable_observer_evidence_cannot_pass(
    valid_bundle: Path, tmp_path: Path
) -> None:
    """Section 6, point 6: even if a bundle claims worker_ok=True and leaves the old PASS-shaped
    result section untouched, marking the observer STORE_MISSING must not verify clean - the
    recomputed classification/agreement disagree with what is declared."""
    bundle = _copy_bundle(valid_bundle, tmp_path / "runtime-ok-observer-missing")
    record = _load(bundle)
    record["observer"]["status"] = "STORE_MISSING"
    record["observer"]["archived_ledger_readback_path"] = None
    record["effect"]["observed_count"] = None
    record["effect"]["effect_digests"] = []
    record["effect"]["effect_attempt_ids"] = []
    # result.oracle_classification/passed/agreement deliberately left as the original PASS values
    _save(bundle, record, recompute_hash=True)
    problems = verify_bundle(bundle)
    assert problems  # cannot verify clean
    assert any("oracle_classification" in p or "agreement" in p for p in problems)


def _ledger_bytes_for_zero_effect(tmp_path: Path, other_intent: str) -> bytes:
    path = tmp_path / "zero.jsonl"
    state = LedgerState(path=path)
    # A record for a DIFFERENT intent: the store is non-empty and its chain is valid, but the
    # control's own intent legitimately never crossed - a real, valid zero, not a missing store.
    state.execute(other_intent, None, {"unrelated": True}, attempt_id=None)
    return path.read_bytes()


def test_valid_observed_zero_fails_the_control_without_being_mislabeled_missing(
    valid_bundle: Path, tmp_path: Path
) -> None:
    bundle = _copy_bundle(valid_bundle, tmp_path / "valid-zero")
    record = _load(bundle)
    intent_id = record["identity"]["intent_id"]
    raw = _ledger_bytes_for_zero_effect(tmp_path, other_intent="some-other-run")
    (bundle / "ledger-readback.jsonl").write_bytes(raw)

    digest = sha256_bytes(raw)
    record["observer"]["archived_ledger_readback_sha256"] = digest
    record["observer"]["original_readback_sha256"] = digest
    record["observer"]["record_count"] = 1
    record["effect"]["observed_count"] = 0
    record["effect"]["effect_digests"] = []
    record["effect"]["effect_attempt_ids"] = []
    record["result"]["oracle_classification"] = "LOST"
    record["result"]["measured"]["effect_count"] = 0
    record["result"]["agreement"] = False
    record["result"]["passed"] = False
    _sync_observer_report(bundle, record)
    _save(bundle, record, recompute_hash=True)

    problems = verify_bundle(bundle)
    assert problems == []  # internally consistent
    final = _load(bundle)
    assert final["observer"]["status"] == "OBSERVED"  # not STORE_MISSING - the store was read fine
    assert final["result"]["oracle_classification"] == "LOST"
    assert intent_id not in raw.decode()


def test_valid_duplicate_effect_fails_the_control_without_being_mislabeled_missing(
    valid_bundle: Path, tmp_path: Path
) -> None:
    bundle = _copy_bundle(valid_bundle, tmp_path / "valid-duplicate")
    record = _load(bundle)
    intent_id = record["identity"]["intent_id"]

    path = tmp_path / "dup.jsonl"
    state = LedgerState(path=path)
    state.execute(intent_id, None, {"amount": 1}, attempt_id=f"{intent_id}:attempt-1")
    state.execute(intent_id, None, {"amount": 1}, attempt_id=f"{intent_id}:attempt-2")
    raw = path.read_bytes()
    (bundle / "ledger-readback.jsonl").write_bytes(raw)

    readback = parse_ledger_bytes(raw)
    digest = sha256_bytes(raw)
    record["observer"]["archived_ledger_readback_sha256"] = digest
    record["observer"]["original_readback_sha256"] = digest
    record["observer"]["record_count"] = readback.record_count
    record["effect"]["observed_count"] = 2
    record["effect"]["effect_digests"] = readback.effect_digests[intent_id]
    record["effect"]["effect_attempt_ids"] = readback.effect_attempt_ids[intent_id]
    record["result"]["oracle_classification"] = "DUPLICATED"
    record["result"]["measured"]["effect_count"] = 2
    record["result"]["agreement"] = False
    record["result"]["passed"] = False
    _sync_observer_report(bundle, record)
    _save(bundle, record, recompute_hash=True)

    problems = verify_bundle(bundle)
    assert problems == []
    assert _load(bundle)["result"]["oracle_classification"] == "DUPLICATED"


# --- The six false accepts from the publication review (CODEX-PUBLICATION-PROBE-RESULTS.json) ---
# Every one of these recomputes the outer receipt hash after mutating, so rejecting a stale
# checksum alone is not what is being tested here - the fix has to be a real semantic check.


def test_false_accept_not_accepted_before_invoke(valid_bundle: Path, tmp_path: Path) -> None:
    bundle = _copy_bundle(valid_bundle, tmp_path / "false-accept-not-accepted")
    record = _load(bundle)
    record["admission"]["accepted_before_invoke"] = False
    _save(bundle, record, recompute_hash=True)
    problems = verify_bundle(bundle)
    assert problems, "accepted_before_invoke=False with passed=True must be rejected"
    assert any("agreement" in p for p in problems)


def test_false_accept_worker_exit_77_with_worker_ok_true(
    valid_bundle: Path, tmp_path: Path
) -> None:
    bundle = _copy_bundle(valid_bundle, tmp_path / "false-accept-exit-77")
    record = _load(bundle)
    record["runtime"]["worker_exit_status"] = 77
    _save(bundle, record, recompute_hash=True)
    problems = verify_bundle(bundle)
    assert problems, "worker_exit_status=77 with worker_ok=True must be rejected"
    assert any("cannot coherently have self-reported ok" in p for p in problems)


def test_false_accept_invoke_count_2(valid_bundle: Path, tmp_path: Path) -> None:
    bundle = _copy_bundle(valid_bundle, tmp_path / "false-accept-invoke-2")
    record = _load(bundle)
    record["runtime"]["invoke_count"] = 2
    _save(bundle, record, recompute_hash=True)
    problems = verify_bundle(bundle)
    assert problems, "invoke_count=2 disagreeing with the contract must be rejected"
    assert any("invoke_count" in p and "does not match" in p for p in problems)


def test_false_accept_contract_disagrees_with_retained_file(
    valid_bundle: Path, tmp_path: Path
) -> None:
    """Change the RETAINED contract.json's expected_effect_count, update only
    receipt.contract.sha256 to match the new file, and recompute the receipt - but leave
    result.expected and manifest.json untouched, exactly as the publication review specified."""
    bundle = _copy_bundle(valid_bundle, tmp_path / "false-accept-contract-disagrees")
    contract_path = bundle / "contract.json"
    contract_body = json.loads(contract_path.read_text())
    contract_body["expected_effect_count"] = 2
    contract_path.write_text(json.dumps(contract_body, indent=2, sort_keys=True) + "\n")

    record = _load(bundle)
    record["contract"]["sha256"] = hashlib.sha256(contract_path.read_bytes()).hexdigest()
    # result.expected and manifest.json deliberately left untouched.
    _save(bundle, record, recompute_hash=True)

    problems = verify_bundle(bundle)
    assert problems, "a contract.json that disagrees with result.expected must be rejected"
    assert any("result.expected" in p for p in problems)
    assert any("manifest.json's contract_sha256" in p for p in problems)


def test_false_accept_missing_observer_report(valid_bundle: Path, tmp_path: Path) -> None:
    bundle = _copy_bundle(valid_bundle, tmp_path / "false-accept-missing-observer-report")
    (bundle / "observer-report.json").unlink()
    record = _load(bundle)
    _save(bundle, record, recompute_hash=True)  # receipt still declares the (now-absent) report
    problems = verify_bundle(bundle)
    assert problems, "a bundle missing its declared observer report must be rejected"
    assert any("observer report" in p and "not found" in p for p in problems)


def test_false_accept_missing_source_module(valid_bundle: Path, tmp_path: Path) -> None:
    bundle = _copy_bundle(valid_bundle, tmp_path / "false-accept-missing-source")
    (bundle / "source" / "langgraph_control.py").unlink()
    record = _load(bundle)
    _save(bundle, record, recompute_hash=True)  # receipt still declares all 5 modules
    problems = verify_bundle(bundle)
    assert problems, "removing a required source module must be rejected, not silently skipped"
    assert any("langgraph_control.py" in p for p in problems)
    assert any("missing required module" in p for p in problems)


# --- Bundle path confinement (a second publication review found a hash check alone is not a
# confinement check: Codex set receipt.contract.path to an absolute path outside the copied
# bundle, recomputed the outer hash, and verify_bundle returned []). Every test below points a
# declared path at a REAL file with a CORRECTLY matching hash outside the bundle - the exploit is
# never "the hash is wrong", it is "this path should not be readable from here at all". ---


def test_absolute_contract_path_escapes_bundle_confinement(
    valid_bundle: Path, tmp_path: Path
) -> None:
    """Reproduces the exact finding: Path.__truediv__ silently discards the left operand when the
    right one is absolute (``bundle_root / "/etc/passwd"`` is ``/etc/passwd``, not an error), so
    an absolute declared path could otherwise point anywhere on the reviewer's filesystem and
    still hash-match."""
    bundle = _copy_bundle(valid_bundle, tmp_path / "absolute-path-escape")
    outside_dir = tmp_path / "outside-absolute"
    outside_dir.mkdir()
    outside_file = outside_dir / "not-actually-the-contract.json"
    outside_file.write_bytes((bundle / "contract.json").read_bytes())

    record = _load(bundle)
    record["contract"]["path"] = str(outside_file.resolve())  # absolute, outside the bundle
    record["contract"]["sha256"] = hashlib.sha256(outside_file.read_bytes()).hexdigest()
    _save(bundle, record, recompute_hash=True)

    problems = verify_bundle(bundle)
    assert problems, "an absolute contract path outside the bundle must be rejected"
    assert any("absolute" in p for p in problems)


def test_relative_traversal_path_escapes_bundle_confinement(
    valid_bundle: Path, tmp_path: Path
) -> None:
    bundle = _copy_bundle(valid_bundle, tmp_path / "traversal-escape")
    outside_dir = tmp_path / "outside-traversal"
    outside_dir.mkdir()
    outside_file = outside_dir / "not-actually-the-contract.json"
    outside_file.write_bytes((bundle / "contract.json").read_bytes())

    traversal_path = os.path.relpath(outside_file, bundle)
    assert traversal_path.startswith("..")  # sanity: this really is an escaping relative path

    record = _load(bundle)
    record["contract"]["path"] = traversal_path
    record["contract"]["sha256"] = hashlib.sha256(outside_file.read_bytes()).hexdigest()
    _save(bundle, record, recompute_hash=True)

    problems = verify_bundle(bundle)
    assert problems, "a relative '..' traversal path escaping the bundle must be rejected"
    assert any("resolves outside the bundle" in p for p in problems)


def test_symlink_inside_bundle_escaping_it_is_rejected(valid_bundle: Path, tmp_path: Path) -> None:
    """A path string with no ``..`` and no leading ``/`` can still escape if the file it names
    inside the bundle is itself a symlink to somewhere else - a hash check alone would not catch
    this either, since the symlink target's bytes are exactly what gets hashed."""
    bundle = _copy_bundle(valid_bundle, tmp_path / "symlink-escape")
    outside_dir = tmp_path / "outside-symlink"
    outside_dir.mkdir()
    outside_file = outside_dir / "secret.json"
    outside_file.write_bytes((bundle / "contract.json").read_bytes())

    symlink_path = bundle / "contract-symlink.json"
    symlink_path.symlink_to(outside_file)

    record = _load(bundle)
    record["contract"]["path"] = "contract-symlink.json"
    record["contract"]["sha256"] = hashlib.sha256(outside_file.read_bytes()).hexdigest()
    _save(bundle, record, recompute_hash=True)

    problems = verify_bundle(bundle)
    assert problems, "a symlink inside the bundle pointing outside it must be rejected"
    assert any("resolves outside the bundle" in p for p in problems)


def test_symlinked_source_copy_escaping_the_bundle_is_rejected(
    valid_bundle: Path, tmp_path: Path
) -> None:
    """The same confinement boundary applies to the fixed source/ module list, not just to
    receipt-declared top-level paths - a basename alone (already stripped of any '..' by
    Path.name) is not enough if the actual file at that name is a symlink escaping the bundle."""
    bundle = _copy_bundle(valid_bundle, tmp_path / "symlink-source-escape")
    real_copy = bundle / "source" / "langgraph_control.py"
    real_bytes = real_copy.read_bytes()
    outside_dir = tmp_path / "outside-source-symlink"
    outside_dir.mkdir()
    outside_file = outside_dir / "langgraph_control.py"
    outside_file.write_bytes(real_bytes)

    real_copy.unlink()
    real_copy.symlink_to(outside_file)

    record = _load(bundle)
    # The declared hash still matches (the symlink target has identical bytes) - confinement,
    # not content, is what must catch this.
    _save(bundle, record, recompute_hash=False)

    problems = verify_bundle(bundle)
    assert problems, "a symlinked source copy escaping the bundle must be rejected"
    assert any("resolves outside the bundle" in p for p in problems)


def test_legitimately_relocated_self_contained_bundle_still_verifies(
    valid_bundle: Path, tmp_path: Path
) -> None:
    """The confinement boundary must not reject ordinary, honest bundle-relative paths - only
    ones that are absolute or that resolve outside the bundle. Verified after relocation to a
    brand-new local directory (not literally a different machine - see REPRODUCE.md's wording
    correction)."""
    relocated = tmp_path / "clean" / "relocated" / "bundle"
    relocated.parent.mkdir(parents=True)
    shutil.copytree(valid_bundle, relocated)
    assert verify_bundle(relocated) == []


# --- Observer exit status (a third publication review finding: exit_status=2 with an otherwise
# valid OBSERVED report still yielded passed=true). ---


def test_false_accept_observer_nonzero_exit_with_valid_observed_report(
    valid_bundle: Path, tmp_path: Path
) -> None:
    """A well-formed, byte-verified OBSERVED report is not enough for a clean PASS if the observer
    process itself did not exit 0 - reproduced by Codex directly against a real bundle's own
    genuinely-correct report content."""
    bundle = _copy_bundle(valid_bundle, tmp_path / "false-accept-observer-exit-2")
    record = _load(bundle)
    record["observer"]["exit_status"] = 2
    _save(bundle, record, recompute_hash=True)

    problems = verify_bundle(bundle)
    assert problems, "observer.exit_status=2 with an OBSERVED report must not verify as passed"
    assert any("agreement" in p for p in problems)

    # The genuine readback evidence itself must still be intact and reported - a process exit
    # failure does not retroactively make the effect not have happened.
    final = _load(bundle)
    assert final["observer"]["status"] == "OBSERVED"
    assert final["effect"]["observed_count"] == 1
