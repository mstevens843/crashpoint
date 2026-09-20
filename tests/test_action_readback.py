from __future__ import annotations

import contextlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from crashpoint.harness import action_readback as ar
from crashpoint.harness import action_readback_runtime as runtime
from crashpoint.harness.action_readback_receipt import CASES, validate_receipt
from crashpoint.harness.action_readback_runtime import PREFIX

_ROOT = Path(__file__).resolve().parents[1]
_EVIDENCE_PATH = (
    _ROOT / "evidence" / "action_readback" / "action_readback_self_reviewed_v2" / "manifest.json"
)


@contextlib.contextmanager
def _short_ledger_daemon() -> Any:
    """A _BoundedLedgerDaemon rooted in a short-path tempdir, not pytest's ``tmp_path`` (whose
    directory embeds the test function's own long name and can push the ledger's Unix-socket
    paths past the ~104-byte AF_UNIX length limit on macOS - the exact bug the real action_readback
    run() itself hit before being fixed to use tempfile.TemporaryDirectory())."""
    with tempfile.TemporaryDirectory(prefix="cp-ar-test-") as d, ar._BoundedLedgerDaemon(
        Path(d) / "l"
    ) as ledger:
        yield ledger


# --------------------------------------------------------------------------------------------
# _parse_events / _find_event / _kill_and_reap / _reap_owned / _drain_remaining
# --------------------------------------------------------------------------------------------


def test_parse_events_filters_non_prefixed_noise() -> None:
    stdout = "\n".join([
        "", "some unrelated banner", PREFIX + json.dumps({"event": "worker_started", "pid": 1}),
        "trailing noise",
    ])
    events = ar._parse_events(stdout)
    assert events == [{"event": "worker_started", "pid": 1}]


def test_find_event_returns_none_when_absent() -> None:
    assert ar._find_event([{"event": "a"}], "b") is None


def test_kill_and_reap_process_already_exited_before_kill() -> None:
    proc = subprocess.Popen(["true"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    proc.wait()
    killed, _code, err = ar._kill_and_reap(proc)
    assert killed is False
    assert err is not None and "already exited" in err


def test_reap_owned_is_idempotent_and_reports_no_problems_for_dead_processes() -> None:
    proc = subprocess.Popen(["true"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    proc.wait()
    assert ar._reap_owned([proc]) == []
    assert ar._reap_owned([proc]) == []  # calling twice must not raise or double-report


def test_wait_for_barrier_bounded_timeout_on_a_process_that_never_emits_it() -> None:
    proc = subprocess.Popen(
        ["sleep", "5"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        events, _raw_out, err = ar.wait_for_barrier(proc, "never_happens", timeout=0.5)
        assert err is not None and "timed out" in err
        assert events == []
    finally:
        ar._reap_owned([proc])


# --------------------------------------------------------------------------------------------
# Live fault-injection regressions: each patches a specific real production call and asserts on
# the actual run_trial()/run() outcome, per the mandatory self-review's Part A.
# --------------------------------------------------------------------------------------------


def test_injected_admission_readback_confirmation_failure_is_a_clean_gate_rejection(
    tmp_path: Path,
) -> None:
    """If the independent post-commit read cannot confirm the row (the dispatch gate itself
    fails), Worker A must never be spawned at all, and the trial must fail honestly - not spawn a
    worker against an unconfirmed admission."""
    prediction = json.loads(ar._PREDICTION_PATH.read_text())
    with _short_ledger_daemon() as ledger, patch.object(
        runtime, "read_admission", return_value=None
    ), pytest.raises(ar.TrialExecutionError, match="admission commit could not be"):
        ar.run_trial(
            "clean", 0, prediction, ledger, "test-run", "deadbeef" * 5,
            tmp_path / "trial",
        )
    rec = json.loads((tmp_path / "trial" / "receipt.json").read_text())
    assert rec["observation_complete"] is False
    assert rec["passed"] is False
    assert rec["worker_a_pid"] is None  # never spawned - the gate held


def test_injected_receiver_baseline_establishment_failure_still_retains_a_receipt(
    tmp_path: Path,
) -> None:
    prediction = json.loads(ar._PREDICTION_PATH.read_text())
    with _short_ledger_daemon() as ledger:
        real_reset = ledger.reset

        def leave_a_stale_file() -> None:
            real_reset()
            Path(ledger.store_path).touch()

        with (
            patch.object(ledger, "reset", leave_a_stale_file),
            pytest.raises(ar.TrialExecutionError, match="already existed"),
        ):
            ar.run_trial(
                "clean", 0, prediction, ledger, "test-run", "deadbeef" * 5, tmp_path / "trial",
            )
    rec = json.loads((tmp_path / "trial" / "receipt.json").read_text())
    assert rec["observation_complete"] is False
    assert rec["receiver_baseline_established"] is False


def test_injected_worker_launch_failure_leaks_no_process_and_retains_a_receipt(
    tmp_path: Path,
) -> None:
    """A Popen() failure when spawning Worker A (e.g. exec itself fails) must not leave anything
    running and must still produce an honest partial receipt."""
    prediction = json.loads(ar._PREDICTION_PATH.read_text())
    real_popen = subprocess.Popen
    calls = {"n": 0}

    def flaky_popen(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("injected: exec failed")
        return real_popen(*args, **kwargs)

    with (
        _short_ledger_daemon() as ledger,
        patch("subprocess.Popen", side_effect=flaky_popen),
        pytest.raises(ar.TrialExecutionError, match="injected: exec failed"),
    ):
        ar.run_trial(
            "clean", 0, prediction, ledger, "test-run", "deadbeef" * 5, tmp_path / "trial",
        )
    rec = json.loads((tmp_path / "trial" / "receipt.json").read_text())
    assert rec["observation_complete"] is False
    assert rec["worker_a_pid"] is None


def test_injected_observer_launch_failure_is_never_read_not_a_crash(tmp_path: Path) -> None:
    """If the observer subprocess itself cannot even be run (subprocess.run raises), the trial
    must record NEVER_READ with the real launch error, never crash the whole trial or silently
    report a full observation."""
    prediction = json.loads(ar._PREDICTION_PATH.read_text())
    with _short_ledger_daemon() as ledger, patch(
        "crashpoint.harness.action_readback.subprocess.run",
        side_effect=OSError("injected: could not exec observer"),
    ):
        rec = ar.run_trial(
            "clean", 0, prediction, ledger, "test-run", "deadbeef" * 5, tmp_path / "trial",
        )
    assert rec["observer_raw_ledger_state"] == "NEVER_READ"
    assert rec["observation_availability"] == "UNAVAILABLE"
    assert rec["externally_verified"] is False
    assert rec["passed"] is False  # clean predicts FULL/verified; this trial cannot match that


def test_injected_journal_append_failure_records_execution_failure_not_a_crash(
    tmp_path: Path,
) -> None:
    """A journal.jsonl write failure for one trial must be recorded as its own
    execution_failures entry and the batch must continue - not crash the whole run(), and not
    silently drop the trial (which itself succeeded) from manifest.trials."""
    real_open = Path.open
    calls = {"n": 0}

    def flaky_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name == "journal.jsonl" and args and args[0] == "a":
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("injected: journal append failed")
        return real_open(self, *args, **kwargs)

    with patch.object(Path, "open", flaky_open):
        manifest = ar.run(
            name="journal-fault", out_root=tmp_path, cases=("clean",), k_per_cell=1,
        )
    assert manifest["status"] == "INCOMPLETE"
    assert len(manifest["execution_failures"]) == 1
    assert "journal append failed" in manifest["execution_failures"][0]["reason"]
    # The trial itself genuinely succeeded and must still be present in manifest.trials.
    assert manifest["trial_count"] == 1
    assert manifest["trials"][0]["passed"] is True


def test_injected_manifest_write_failure_is_fatal_not_silently_continued(tmp_path: Path) -> None:
    """Unlike a journal-append failure, a manifest.json write failure is treated as a genuine
    storage-is-broken signal (there is no reliable place left to durably record even the failure
    itself) and must raise, not be swallowed into a false COMPLETE status."""
    real_write_text = Path.write_text
    calls = {"n": 0}

    def flaky_write_text(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name == "manifest.json":
            calls["n"] += 1
            if calls["n"] == 2:  # let the pre-trial-1 manifest write succeed; fail the next one
                raise OSError("injected: disk full")
        return real_write_text(self, *args, **kwargs)

    with (
        patch.object(Path, "write_text", flaky_write_text),
        pytest.raises(RuntimeError, match="manifest finalization failed"),
    ):
        ar.run(name="manifest-fault", out_root=tmp_path, cases=("clean",), k_per_cell=1)


def test_run_level_aggregates_a_trial_failure_without_crashing_the_batch(tmp_path: Path) -> None:
    """A genuine run()-level test: patches run_trial to fail on the first of two trials and
    calls the real run(), not just run_trial() directly - asserting on the real batch-level
    accounting (status, all_agree, execution_failures), not merely on what a single trial
    returns."""
    real_run_trial = ar.run_trial

    def flaky_run_trial(case: str, index: int, *args: Any, **kwargs: Any) -> dict[str, object]:
        if index == 0:
            raise ar.TrialExecutionError("test: injected run_trial failure")
        return real_run_trial(case, index, *args, **kwargs)

    with patch.object(ar, "run_trial", flaky_run_trial):
        manifest = ar.run(
            name="run-level-test", out_root=tmp_path, cases=("clean",), k_per_cell=2,
        )
    assert manifest["status"] == "INCOMPLETE"
    assert manifest["all_agree"] is False
    assert manifest["trial_count"] == 1
    assert manifest["expected_trial_count"] == 2
    assert len(manifest["execution_failures"]) == 1
    assert manifest["execution_failures"][0]["trial_id"] == "clean-0"


# --------------------------------------------------------------------------------------------
# Sibling case not named in the prompt, exercising the same class of bug: does a hard failure
# during the RETRY worker (naive_retry's Worker B) also leak no process and retain evidence,
# the same way a Worker A failure does?
# --------------------------------------------------------------------------------------------


def test_injected_worker_b_launch_failure_in_naive_retry_leaks_no_process(tmp_path: Path) -> None:
    prediction = json.loads(ar._PREDICTION_PATH.read_text())
    real_popen = subprocess.Popen
    calls = {"n": 0}

    def flaky_popen(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:  # let Worker A (the 1st Popen) launch normally; fail Worker B
            raise OSError("injected: exec failed for worker B")
        return real_popen(*args, **kwargs)

    with (
        _short_ledger_daemon() as ledger,
        patch("subprocess.Popen", side_effect=flaky_popen),
        pytest.raises(ar.TrialExecutionError),
    ):
        ar.run_trial(
            "naive_retry", 0, prediction, ledger, "test-run", "deadbeef" * 5, tmp_path / "trial",
        )
    rec = json.loads((tmp_path / "trial" / "receipt.json").read_text())
    assert rec["observation_complete"] is False
    assert rec["worker_a_killed"] is True  # Worker A's own lifecycle completed fine


# --------------------------------------------------------------------------------------------
# Checked-in evidence and identity-substitution/non-merge properties, exercised via the real
# recorded batch rather than a fresh live run.
# --------------------------------------------------------------------------------------------


def test_checked_in_evidence_matches_prediction_and_validates() -> None:
    if not _EVIDENCE_PATH.exists():
        pytest.skip(f"no recorded evidence at {_EVIDENCE_PATH}")
    from crashpoint.canonical import receipt

    manifest = json.loads(_EVIDENCE_PATH.read_text())
    body = dict(manifest)
    recorded = body.pop("receipt")
    assert recorded == receipt(body)
    assert manifest["trial_count"] == 18
    assert manifest["all_agree"] is True
    for rec in manifest["trials"]:
        assert validate_receipt(rec) == []

    for case in CASES:
        cell = manifest["cells_summary"][case]
        assert cell["passing"] == 3


def test_checked_in_evidence_two_admitted_clean_trials_are_distinct_actions_same_payload() -> None:
    """Two intended actions with identical payloads must get distinct action IDs - checked
    against the real recorded batch, not a synthetic fixture."""
    if not _EVIDENCE_PATH.exists():
        pytest.skip(f"no recorded evidence at {_EVIDENCE_PATH}")
    manifest = json.loads(_EVIDENCE_PATH.read_text())
    clean_trials = [t for t in manifest["trials"] if t["case"] == "clean"]
    assert len(clean_trials) == 3
    action_ids = {t["action_id"] for t in clean_trials}
    digests = {t["admission_payload_digest"] for t in clean_trials}
    assert len(action_ids) == 3  # every trial minted its own fresh action_id
    assert len(digests) == 1  # ...despite dispatching the exact same harmless payload
