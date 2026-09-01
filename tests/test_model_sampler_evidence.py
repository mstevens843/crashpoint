"""Receipted evidence for the optional real-model sampler arm."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from crashpoint.canonical import receipt

_EVIDENCE = Path(__file__).resolve().parents[1] / "evidence" / "langgraph_model.json"


def _load() -> dict[str, object]:
    if not _EVIDENCE.exists():
        pytest.skip("model-backed evidence absent")
    return cast(dict[str, object], json.loads(_EVIDENCE.read_text()))


def _modal(record: dict[str, object]) -> dict[tuple[str, str], str]:
    cells = record["cells"]
    assert isinstance(cells, list)
    return {(c["runtime"], c["barrier"]): c["modal"] for c in cells}


def test_model_sampler_evidence_receipt_rederives() -> None:
    record = _load()
    body = {k: v for k, v in record.items() if k != "receipt"}
    assert receipt(body) == record["receipt"]


def test_model_sampler_evidence_is_marked_model_backed() -> None:
    record = _load()
    sampler = record["sampler"]
    assert isinstance(sampler, dict)
    assert sampler["nondeterministic_source"] == "model"
    assert sampler["model"] == "claude-haiku-4-5-20251001"
    assert sampler["sampler_cmd"] == "python scripts/anthropic_sampler.py"
    assert sampler["prompt_sha256"]


def test_model_sampler_evidence_observes_diverged_and_two_phase_recovery() -> None:
    record = _load()
    m = _modal(record)
    assert m[("r_lg_nondet", "b1")] == "diverged"
    assert m[("r_lg_twophase", "b1")] == "exactly_once"
    assert record["disagreements"] == []
