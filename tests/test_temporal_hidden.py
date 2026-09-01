"""Temporal hidden-barrier helpers that do not require a Temporal server."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crashpoint.canonical import receipt
from crashpoint.harness import temporal_hidden
from crashpoint.harness.temporal_hidden import HistoryRow, history_agrees, summarize_rows
from crashpoint.model.barriers import BARRIER_IDS

_EVIDENCE = Path(__file__).resolve().parents[1] / "evidence"
_FILES = {
    "tmp_activity_scheduled_before_worker_poll": "temporal_hidden_scheduled.json",
    "tmp_workflow_task_replay": "temporal_hidden_replay.json",
}


def test_hidden_barriers_are_disjoint_from_modeled_barriers() -> None:
    assert set(temporal_hidden.HIDDEN_BARRIERS).isdisjoint(BARRIER_IDS)
    for barrier in temporal_hidden.HIDDEN_BARRIERS:
        assert temporal_hidden._PREDICTED[barrier].value == "exactly_once"
        assert len(temporal_hidden._PREDICTION_RULES[barrier]) > 40
        assert len(temporal_hidden._HISTORY_RULES[barrier]) > 20


def _rows(*names: str, attempt: int = 1, failure: str = "") -> list[HistoryRow]:
    return [
        HistoryRow(n, attempt if n == "activity_task_started" else None,
                   failure if n == "activity_task_started" else "")
        for n in names
    ]


def test_summarize_rows_reads_attempt_and_counts() -> None:
    rows = _rows(
        "workflow_execution_started", "workflow_task_scheduled", "workflow_task_started",
        "workflow_task_completed", "activity_task_scheduled", "activity_task_started",
        "activity_task_completed", "workflow_task_scheduled", "workflow_task_started",
        "workflow_task_timed_out", "workflow_task_scheduled", "workflow_task_started",
        "workflow_task_completed", "workflow_execution_completed",
        attempt=1,
    )
    summary = summarize_rows(rows)
    assert summary["activity_task_scheduled"] == 1
    assert summary["activity_started_attempt"] == 1
    assert summary["workflow_task_timed_out"] == 1
    assert summary["activity_last_failure"] == ""
    counts = summary["event_counts"]
    assert isinstance(counts, dict)
    assert counts["workflow_task_completed"] == 2


def test_history_agrees_distinguishes_the_two_edges() -> None:
    scheduled_shape = summarize_rows(
        _rows("activity_task_scheduled", "activity_task_started", "activity_task_completed")
    )
    replay_shape = summarize_rows(
        _rows("activity_task_scheduled", "activity_task_started", "activity_task_completed",
              "workflow_task_timed_out")
    )
    assert history_agrees("tmp_activity_scheduled_before_worker_poll", scheduled_shape)
    assert not history_agrees("tmp_activity_scheduled_before_worker_poll", replay_shape)
    assert history_agrees("tmp_workflow_task_replay", replay_shape)
    assert not history_agrees("tmp_workflow_task_replay", scheduled_shape)
    # A retried attempt (attempt 2 after a timeout) would mean the body ran before the crash,
    # which is the b0/b1 shape, not either hidden edge.
    retried = summarize_rows(
        _rows("activity_task_scheduled", "activity_task_started", "activity_task_completed",
              attempt=2, failure="activity StartToClose timeout")
    )
    assert not history_agrees("tmp_activity_scheduled_before_worker_poll", retried)
    assert retried["activity_last_failure"] == "activity StartToClose timeout"


@pytest.mark.parametrize("barrier", list(_FILES))
def test_checked_in_temporal_hidden_evidence_receipt(barrier: str) -> None:
    path = _EVIDENCE / _FILES[barrier]
    if not path.exists():
        pytest.skip(f"{path.name} absent")
    rec = json.loads(path.read_text())
    body = dict(rec)
    body.pop("receipt")
    assert rec["receipt"] == receipt(body)
    assert rec["runtime"] == "temporal"
    assert rec["barrier"] == barrier
    assert rec["barrier_family"] == "hidden"
    assert rec["predicted"] == "exactly_once"
    assert rec["modal"] == "exactly_once"
    assert rec["agrees"] is True
    assert rec["history_agrees_count"] == rec["k"]
