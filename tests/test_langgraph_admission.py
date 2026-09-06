from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from crashpoint.canonical import receipt
from crashpoint.harness import langgraph_admission

_ROOT = Path(__file__).resolve().parents[1]
_EVIDENCE = _ROOT / "evidence" / "langgraph_admission.json"


def test_admission_ledger_preserves_input_and_event_order(tmp_path: Path) -> None:
    path = tmp_path / "admission.sqlite"
    langgraph_admission.initialize_admission_ledger(path)
    assert langgraph_admission.read_admission(path, "run-1").status == "missing"

    original_input: dict[str, object] = {"done": False, "request": "invoice-7"}
    langgraph_admission.accept_run(path, "run-1", original_input)
    langgraph_admission.append_admission_event(path, "run-1", "recovery_required")
    langgraph_admission.append_admission_event(path, "run-1", "completed")

    snapshot = langgraph_admission.read_admission(path, "run-1")
    assert snapshot.accepted is True
    assert snapshot.original_input == original_input
    assert snapshot.events == ("accepted", "recovery_required", "completed")
    assert snapshot.status == "completed"


def test_admission_identity_and_input_are_immutable(tmp_path: Path) -> None:
    path = tmp_path / "admission.sqlite"
    langgraph_admission.initialize_admission_ledger(path)
    langgraph_admission.accept_run(path, "run-1", {"done": False})
    with pytest.raises(sqlite3.IntegrityError):
        langgraph_admission.accept_run(path, "run-1", {"done": True})


def test_post_acceptance_event_requires_accepted_run(tmp_path: Path) -> None:
    path = tmp_path / "admission.sqlite"
    langgraph_admission.initialize_admission_ledger(path)
    with pytest.raises(sqlite3.IntegrityError):
        langgraph_admission.append_admission_event(path, "missing", "recovery_required")
    with pytest.raises(ValueError, match="invalid post-acceptance event"):
        langgraph_admission.append_admission_event(path, "missing", "accepted")


def test_two_arm_admission_containment() -> None:
    pytest.importorskip("langgraph")
    record = langgraph_admission.run(1, "test_langgraph_admission")
    assert record["framework_fix"] is False
    assert record["all_agree"] is True
    assert record["k_per_arm"] == 1

    trials = record["trials"]
    assert isinstance(trials, list)
    assert len(trials) == 2
    by_arm = {trial["arm"]: trial for trial in trials}

    baseline = by_arm["runtime_only"]
    assert baseline["decision"] == "unverified"
    assert baseline["checkpoints_before_recovery"] == 0
    assert baseline["checkpoints_after_recovery"] == 0
    assert baseline["effects_after_recovery"] == 0
    assert baseline["recovery_error_type"] == "EmptyInputError"
    assert baseline["replayed"] is False

    contained = by_arm["external_admission_ledger"]
    assert contained["decision"] == "admitted_runtime_evidence_missing"
    assert contained["checkpoints_before_recovery"] == 0
    assert contained["checkpoints_after_recovery"] > 0
    assert contained["effects_after_recovery"] == 1
    assert contained["recovery_error_type"] == "EmptyInputError"
    assert contained["replayed"] is True
    assert contained["admission_after_recovery"]["events"] == [
        "accepted",
        "recovery_required",
        "completed",
    ]


def test_checked_in_admission_evidence_receipt() -> None:
    record = json.loads(_EVIDENCE.read_text())
    body = dict(record)
    body.pop("receipt")
    assert record["receipt"] == receipt(body)
    assert record["experiment_family"] == "application_admission_containment"
    assert record["framework_fix"] is False
    assert record["all_agree"] is True
    assert record["arms"]["runtime_only"]["decisions"] == {"unverified": 30}
    assert record["arms"]["external_admission_ledger"]["decisions"] == {
        "admitted_runtime_evidence_missing": 30
    }
