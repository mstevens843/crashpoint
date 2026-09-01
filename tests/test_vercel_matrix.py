"""Vercel Workflow harness helpers that do not require Node or a built fixture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crashpoint.canonical import receipt
from crashpoint.harness import vercel_matrix

_EVIDENCE = Path(__file__).resolve().parents[1] / "evidence" / "vercel.json"


def test_vercel_runtime_mapping_is_explicit() -> None:
    assert vercel_matrix.RUNTIME_IDS == (
        "r_vwf_naive",
        "r_vwf_idem",
        "r_vwf_nondet",
        "r_vwf_twophase",
    )


def test_bad_vercel_runtime_id_fails() -> None:
    with pytest.raises(ValueError):
        vercel_matrix._parse_runtime_ids("r_vwf_naive,r_restate_naive")


def test_fixture_env_sets_the_two_runtime_knobs(tmp_path: Path) -> None:
    config = vercel_matrix.FixtureConfig(tmp_path, port=4123)
    env = config.env(tmp_path / "world")
    assert env["WORKFLOW_TARGET_WORLD"] == "@workflow/world-local"
    assert env["WORKFLOW_INLINE_OWNERSHIP_LEASE_SECONDS"] == "1"
    assert env["WORKFLOW_LOCAL_BASE_URL"] == "http://127.0.0.1:4123"
    assert env["NITRO_PORT"] == "4123"
    assert env["WORKFLOW_LOCAL_DATA_DIR"] == str(tmp_path / "world")


def test_discover_run_id_ignores_crash_leftovers(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "wrun_01ABC.json").write_text('{"status": "pending"}')
    (runs / "wrun_01ABC.json.tmp.01XYZ").write_text("")
    assert vercel_matrix.discover_run_id(tmp_path) == "wrun_01ABC"


def test_discover_run_id_requires_exactly_one_run(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    with pytest.raises(RuntimeError):
        vercel_matrix.discover_run_id(tmp_path)
    (runs / "wrun_01AAA.json").write_text("{}")
    (runs / "wrun_01BBB.json").write_text("{}")
    with pytest.raises(RuntimeError):
        vercel_matrix.discover_run_id(tmp_path)


def test_durable_snapshot_counts_this_run_only(tmp_path: Path) -> None:
    (tmp_path / "runs").mkdir()
    (tmp_path / "events").mkdir()
    (tmp_path / "steps").mkdir()
    (tmp_path / "runs" / "wrun_01A.json").write_text('{"status": "running"}')
    (tmp_path / "runs" / "wrun_01A.json.tmp.01Q").write_text("")
    for i in range(3):
        (tmp_path / "events" / f"wrun_01A-evnt_{i}.json").write_text("{}")
    (tmp_path / "events" / "wrun_01B-evnt_0.json").write_text("{}")
    (tmp_path / "steps" / "wrun_01A-step_0.json").write_text("{}")
    snap = vercel_matrix.durable_snapshot(tmp_path, "wrun_01A")
    assert snap.run_status == "running"
    assert snap.events == 3
    assert snap.steps == 1
    assert snap.tmp_leftovers == 1


def test_checked_in_vercel_evidence_receipt() -> None:
    if not _EVIDENCE.exists():
        pytest.skip("Vercel Workflow evidence absent")
    record = json.loads(_EVIDENCE.read_text())
    body = {k: v for k, v in record.items() if k != "receipt"}
    assert receipt(body) == record["receipt"]
    substrate = record["substrate"]
    assert substrate["target_world"] == "@workflow/world-local"
    assert substrate["inline_ownership_lease_seconds"] == 1


def test_stalled_recovery_is_scored_void() -> None:
    from crashpoint.model.layers import Outcome

    snap = vercel_matrix.DurableSnapshot("running", 2, 0, 0)
    stalled = vercel_matrix.VercelTrial(
        "r_vwf_naive", "b1", Outcome.DUPLICATED, Outcome.VOID, "wrun_01A", snap, snap, 60.0,
        {"reason": "run did not complete within 60.0s", "server_log_tail": "fetch failed"},
    )
    record = stalled.as_dict()
    assert record["observed"] == "void"
    assert record["stalled"] is True
    assert isinstance(record["stall"], dict)
    clean = vercel_matrix.VercelTrial(
        "r_vwf_naive", "b1", Outcome.DUPLICATED, Outcome.DUPLICATED, "wrun_01B", snap, snap, 0.2
    )
    assert clean.as_dict()["stalled"] is False
    assert "stall" not in clean.as_dict()
