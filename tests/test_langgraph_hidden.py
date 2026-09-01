from __future__ import annotations

import json
from pathlib import Path

import pytest

from crashpoint.canonical import receipt
from crashpoint.harness import langgraph_hidden

_EVIDENCE = Path(__file__).resolve().parents[1] / "evidence" / "langgraph_hidden.json"


def test_pre_first_checkpoint_hidden_trial_observes_lost() -> None:
    pytest.importorskip("langgraph")
    record = langgraph_hidden.run(1, "test_langgraph_hidden")
    assert record["barrier"] == "lg_pre_first_checkpoint"
    assert record["predicted"] == "lost"
    assert record["modal"] == "lost"
    assert record["agrees"] is True
    trials = record["trials"]
    assert isinstance(trials, list)
    assert len(trials) == 1
    assert trials[0]["effect_count"] == 0
    assert trials[0]["durable_checkpoints"] == 0
    assert trials[0]["recovery_error_type"] == "EmptyInputError"


def test_checked_in_langgraph_hidden_evidence_receipt() -> None:
    rec = json.loads(_EVIDENCE.read_text())
    body = dict(rec)
    body.pop("receipt")
    assert rec["receipt"] == receipt(body)
    assert rec["barrier"] == "lg_pre_first_checkpoint"
    assert rec["predicted"] == "lost"
    assert rec["modal"] == "lost"
    assert rec["agrees"] is True
