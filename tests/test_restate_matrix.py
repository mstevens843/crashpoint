"""Restate harness helpers that do not require a live Restate server."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crashpoint.canonical import receipt
from crashpoint.harness import restate_matrix

_EVIDENCE = Path(__file__).resolve().parents[1] / "evidence" / "restate.json"


def test_restate_runtime_mapping_is_explicit() -> None:
    assert restate_matrix.RUNTIME_IDS == (
        "r_restate_naive",
        "r_restate_idem",
        "r_restate_nondet",
        "r_restate_twophase",
    )


def test_bad_restate_runtime_id_fails() -> None:
    with pytest.raises(ValueError):
        restate_matrix._parse_runtime_ids("r_restate_naive,r_dbos_naive")


def test_checked_in_restate_evidence_receipt() -> None:
    if not _EVIDENCE.exists():
        pytest.skip("Restate evidence absent")
    record = json.loads(_EVIDENCE.read_text())
    body = {k: v for k, v in record.items() if k != "receipt"}
    assert receipt(body) == record["receipt"]
