from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from crashpoint.harness import safeagent_ttl as ttl
from crashpoint.harness.safeagent_ttl_receipt import CASES, RELEASES, validate_receipt
from crashpoint.harness.safeagent_ttl_runtime import PREFIX

_ROOT = Path(__file__).resolve().parents[1]
# safeagent_ttl_corrected_review2 is the current authoritative, fully-provenanced bundle,
# captured fresh after the 2026-09-19 second review's corrections
# (handoff/safeagent-ttl/CORRECTIONS.md). Two earlier bundles are preserved as historical
# evidence rather than overwritten: safeagent_ttl_corrected (first review's bundle - still
# byte-identical and still independently re-validated, see
# test_safeagent_ttl_verify.py's test_safeagent_ttl_corrected_round2_bundle_preserved_and_
# still_validates) and safeagent_ttl (the original, pre-hardening bundle - no embedded
# prediction/sources, fewer retained snapshots, checked here only by hash, not re-validated
# against a schema it predates).
_EVIDENCE_PATH = (
    _ROOT / "evidence" / "safeagent_ttl" / "safeagent_ttl_corrected_review2" / "manifest.json"
)
_HISTORICAL_EVIDENCE_DIR = _ROOT / "evidence" / "safeagent_ttl" / "safeagent_ttl"


def _require_safeagent_venvs() -> None:
    for release, python_bin in ttl.VENV_PYTHON.items():
        if not Path(python_bin).exists():
            pytest.skip(
                f"isolated SafeAgent venv for {release} not found at {python_bin} - this "
                "experiment installs safeagent-exec-guard into two per-release venvs outside "
                "the normal uv-managed environment; see results/12-safeagent-ttl.md"
            )


# --------------------------------------------------------------------------------------------
# _parse_events / _find_event
# --------------------------------------------------------------------------------------------


def test_parse_events_filters_non_prefixed_noise() -> None:
    stdout = "\n".join(
        [
            "",
            "some unrelated line safeagent-exec-guard prints on import",
            PREFIX + json.dumps({"event": "worker_started", "case": "settled_control"}),
            "another banner",
            PREFIX + json.dumps({"event": "claimed", "claim_result": True}),
            "",
        ]
    )
    events = ttl._parse_events(stdout)
    assert events == [
        {"event": "worker_started", "case": "settled_control"},
        {"event": "claimed", "claim_result": True},
    ]


def test_find_event_returns_the_last_match() -> None:
    events: list[dict[str, Any]] = [
        {"event": "effect_ack", "n": 1}, {"event": "other"}, {"event": "effect_ack", "n": 2},
    ]
    assert ttl._find_event(events, "effect_ack") == {"event": "effect_ack", "n": 2}


def test_find_event_returns_none_when_absent() -> None:
    assert ttl._find_event([{"event": "a"}], "b") is None


# --------------------------------------------------------------------------------------------
# _kill_and_reap: a real subprocess, no SafeAgent needed.
# --------------------------------------------------------------------------------------------


def test_kill_and_reap_real_process() -> None:
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], text=True)
    killed, exit_status, err = ttl._kill_and_reap(proc)
    assert killed is True
    assert exit_status == -9
    assert err is None


def test_kill_and_reap_already_exited_process() -> None:
    proc = subprocess.Popen([sys.executable, "-c", "pass"], text=True)
    proc.wait(timeout=5)
    killed, _exit_status, err = ttl._kill_and_reap(proc)
    assert killed is False
    assert err is not None and "already exited" in err


# --------------------------------------------------------------------------------------------
# TTL margin helpers: pure functions of a claimed_at timestamp and the clock.
# --------------------------------------------------------------------------------------------


def test_verify_before_ttl_passes_with_comfortable_margin() -> None:
    ok, age, reason = ttl._verify_before_ttl(claimed_at=time.time(), ttl=30.0)
    assert ok is True
    assert reason is None
    assert age < 1.0


def test_verify_before_ttl_fails_when_too_close_to_expiry() -> None:
    claimed_at = time.time() - 29.9  # ttl=30, margin=0.2 -> age must be < 29.8
    ok, _age, reason = ttl._verify_before_ttl(claimed_at=claimed_at, ttl=30.0)
    assert ok is False
    assert reason is not None and "insufficient margin" in reason


def test_verify_before_ttl_fails_when_claimed_at_missing() -> None:
    ok, _age, reason = ttl._verify_before_ttl(claimed_at=None, ttl=30.0)
    assert ok is False
    assert reason is not None


def test_wait_past_ttl_actually_waits_and_confirms_crossing() -> None:
    small_ttl = 0.3
    claimed_at = time.time()
    ok, age, reason = ttl._wait_past_ttl(claimed_at=claimed_at, ttl=small_ttl)
    assert ok is True
    assert reason is None
    assert age >= small_ttl + ttl._TTL_MARGIN_PAST - 0.05  # allow small scheduling slack


def test_wait_past_ttl_fails_when_claimed_at_missing() -> None:
    ok, _age, reason = ttl._wait_past_ttl(claimed_at=None, ttl=1.0)
    assert ok is False
    assert reason is not None


# --------------------------------------------------------------------------------------------
# wait_for_barrier: bounded stdout read against a fake subprocess. No SafeAgent needed - this
# exercises the exact same function the real Worker A barrier wait uses, against a stand-in
# process that emits the same PREFIX-marked protocol.
# --------------------------------------------------------------------------------------------


def _fake_worker_script(delay: float, point: str, then_block: bool) -> str:
    block = "import threading; threading.Event().wait()" if then_block else ""
    return (
        "import time, json, sys\n"
        f"time.sleep({delay})\n"
        f"print({PREFIX!r} + json.dumps({{'event': 'barrier', 'point': {point!r}, 'pid': 1}}), "
        "flush=True)\n"
        f"{block}\n"
    )


def test_wait_for_barrier_succeeds_when_barrier_appears() -> None:
    proc = subprocess.Popen(
        [sys.executable, "-c", _fake_worker_script(0.05, "post_effect_pending", then_block=True)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        events, err = ttl.wait_for_barrier(proc, "post_effect_pending", timeout=5.0)
        assert err is None
        assert any(e.get("event") == "barrier" for e in events)
    finally:
        ttl._kill_and_reap(proc)


def test_wait_for_barrier_times_out_when_process_never_emits_it() -> None:
    proc = subprocess.Popen(
        [sys.executable, "-c", "import threading; threading.Event().wait()"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        start = time.monotonic()
        _events, err = ttl.wait_for_barrier(proc, "post_effect_pending", timeout=0.5)
        elapsed = time.monotonic() - start
        assert err is not None and "timed out" in err
        # Bounded: must not hang anywhere near as long as an unbounded blocking readline would.
        assert elapsed < 2.0
    finally:
        ttl._kill_and_reap(proc)


def test_wait_for_barrier_reports_early_exit() -> None:
    proc = subprocess.Popen(
        [sys.executable, "-c", "print('nothing barrier-shaped here')"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    _events, err = ttl.wait_for_barrier(proc, "post_effect_pending", timeout=5.0)
    # The process exiting closes its stdout, which the select loop can observe (EOF) before or
    # instead of the separate proc.poll() check; either is a correct diagnosis of "went away
    # without ever reaching the barrier."
    assert err is not None and ("exited" in err or "stdout closed" in err)


# --------------------------------------------------------------------------------------------
# Checked-in evidence: the receipted 30-trial record must still validate and still match the
# pre-registered prediction, without re-running anything.
# --------------------------------------------------------------------------------------------


def test_checked_in_evidence_matches_prediction_and_validates() -> None:
    if not _EVIDENCE_PATH.exists():
        pytest.skip(f"no recorded evidence at {_EVIDENCE_PATH}")
    from crashpoint.canonical import receipt

    manifest = json.loads(_EVIDENCE_PATH.read_text())
    body = dict(manifest)
    recorded = body.pop("receipt")
    assert recorded == receipt(body)
    assert manifest["trial_count"] == 30
    assert manifest["all_agree"] is True
    for rec in manifest["trials"]:
        assert validate_receipt(rec) == []

    central = manifest["cells_summary"]
    assert central["pending_expired_swept|0.1.23"]["labels"] == {
        "duplicated_effect_after_reclaim": 3
    }
    assert central["pending_expired_swept|0.1.24"]["labels"] == {
        "blocked_pending_effect_already_recorded": 3
    }
    assert central["pre_effect_expired_swept|0.1.23"]["labels"] == {
        "reclaimed_and_settled_once": 3
    }
    assert central["pre_effect_expired_swept|0.1.24"]["labels"] == {
        "blocked_pending_task_unperformed": 3
    }
    assert central["pending_expired_swept|0.1.23"]["effect_counts"] == [2, 2, 2]
    assert central["pending_expired_swept|0.1.24"]["effect_counts"] == [1, 1, 1]
    assert central["pre_effect_expired_swept|0.1.24"]["effect_counts"] == [0, 0, 0]


def test_historical_pre_hardening_bundle_preserved_and_still_self_consistent() -> None:
    """The original bundle predates safeagent_ttl_receipt/verify's 2026-09-19 hardening (it has
    no embedded prediction/sources and fewer retained snapshots, so the CURRENT, stricter
    validate_receipt/verify_bundle correctly reject it - that is not evidence of tampering, and
    this test does not run them against it). What this checks instead: the historical bundle is
    still present, its own outer receipt hash still verifies (untouched bytes), and its central
    findings are unchanged - proving it was preserved, not silently edited or deleted, during the
    correction pass."""
    if not (_HISTORICAL_EVIDENCE_DIR / "manifest.json").exists():
        pytest.skip(f"no historical evidence at {_HISTORICAL_EVIDENCE_DIR}")
    from crashpoint.canonical import receipt

    manifest = json.loads((_HISTORICAL_EVIDENCE_DIR / "manifest.json").read_text())
    body = dict(manifest)
    recorded = body.pop("receipt")
    assert recorded == receipt(body)
    assert manifest["trial_count"] == 30
    assert manifest["all_agree"] is True
    central = manifest["cells_summary"]
    assert central["pending_expired_swept|0.1.23"]["labels"] == {
        "duplicated_effect_after_reclaim": 3
    }
    assert central["pending_expired_swept|0.1.24"]["labels"] == {
        "blocked_pending_effect_already_recorded": 3
    }
    assert central["pre_effect_expired_swept|0.1.24"]["labels"] == {
        "blocked_pending_task_unperformed": 3
    }


# --------------------------------------------------------------------------------------------
# Real regression: the actual production run_trial function, both releases, every case, k=1.
# Distinct from the frozen 30-trial confirmatory batch above; this re-exercises the real
# installed API on every test run rather than only re-reading a recorded file.
# --------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def regression_receipts() -> dict[str, dict[str, object]]:
    _require_safeagent_venvs()
    prediction = json.loads(ttl._PREDICTION_PATH.read_text())
    crashpoint_commit = ttl._git_commit(_ROOT)
    release_info = {r: ttl._release_info(ttl.VENV_PYTHON[r], r) for r in RELEASES}
    receipts: dict[str, dict[str, object]] = {}
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        with ttl._BoundedLedgerDaemon(tmp_p / "ledger") as ledger:
            for case in CASES:
                for release in RELEASES:
                    trial_root = tmp_p / "trials" / f"{case}-{release}"
                    rec = ttl.run_trial(
                        case, release, 0, prediction, ledger, crashpoint_commit, trial_root,
                        release_info[release],
                    )
                    receipts[f"{case}|{release}"] = rec
    return receipts


@pytest.mark.parametrize("case", list(CASES))
@pytest.mark.parametrize("release", ["0.1.23", "0.1.24"])
def test_regression_every_case_every_release_validates_and_matches_prediction(
    regression_receipts: dict[str, dict[str, object]], case: str, release: str
) -> None:
    rec = regression_receipts[f"{case}|{release}"]
    assert validate_receipt(rec) == []
    assert rec["passed"] is True, rec


def test_regression_post_effect_death_confirmed_expiry_sweep_and_fresh_retry_diverge_by_release(
    regression_receipts: dict[str, dict[str, object]],
) -> None:
    """post-effect death + confirmed expiry + production sweep + a fresh process retry, the
    exact shape this permanent regression is required to cover, on the real installed API."""
    old = regression_receipts["pending_expired_swept|0.1.23"]
    new = regression_receipts["pending_expired_swept|0.1.24"]
    assert old["worker_a_killed"] is True and new["worker_a_killed"] is True
    assert old["sweep_invoked"] is True and new["sweep_invoked"] is True
    assert old["effect_count"] == 2 and old["retry_claim_admitted"] is True
    assert new["effect_count"] == 1 and new["retry_claim_admitted"] is False


def test_regression_pre_effect_arm_never_reports_zero_effects_as_success(
    regression_receipts: dict[str, dict[str, object]],
) -> None:
    new = regression_receipts["pre_effect_expired_swept|0.1.24"]
    assert new["effect_count"] == 0
    assert new["task_completion_label"] == "blocked_pending_task_unperformed"


# --------------------------------------------------------------------------------------------
# Live fault-injection regressions, ported from the 2026-09-19 review's check_review.py
# reproduction (handoff/safeagent-ttl/CORRECTIONS.md). Each calls the actual production
# run_trial() with a targeted failure injected via unittest.mock.patch - not a reimplementation
# of run_trial's logic - and each is responsible for reaping whatever worker process it tracks,
# exactly as the review's own probe was.
# --------------------------------------------------------------------------------------------


def _one_release_info(release: str) -> dict[str, object]:
    return ttl._release_info(ttl.VENV_PYTHON[release], release)


def test_release_info_aborts_on_a_genuine_venv_version_mismatch() -> None:
    """_release_info's own installed_version != release guard, exercised against the real
    0.1.24 venv while deliberately asking it to confirm 0.1.23 - not a mock of
    importlib.metadata.version, the actual interpreter and the actual installed package. This
    guard is what stands between the harness and silently measuring the wrong release; until
    this test, nothing called it with a genuine mismatch."""
    _require_safeagent_venvs()
    with pytest.raises(RuntimeError, match="version mismatch"):
        ttl._release_info(ttl.VENV_PYTHON["0.1.24"], "0.1.23")


@contextlib.contextmanager
def _short_ledger_daemon() -> Any:
    """A _BoundedLedgerDaemon rooted in a short-path tempdir, not pytest's ``tmp_path`` (whose
    directory embeds the test function's own name and, for these long, descriptive test names,
    can push the ledger's Unix-socket paths past the ~104-byte AF_UNIX length limit - a real
    failure mode, distinct from anything under test here)."""
    with tempfile.TemporaryDirectory() as d, ttl._BoundedLedgerDaemon(Path(d) / "l") as ledger:
        yield ledger


def test_injected_deletion_of_a_real_known_empty_baseline_is_missing_evidence(
    tmp_path: Path,
) -> None:
    """check_remaining.py's deleted_known_empty_source (the second, sharper review): a REAL
    0-byte ledger file - the raw-empty baseline _reset_and_establish_baseline creates - exists
    on disk before the worker runs, then is deleted right after seal(). The corrected contract
    treats ANY missing file at observation time as missing evidence, unconditionally - not
    "confirmed empty" even though the daemon's own dump() also reports zero, and even though the
    real effect count for this case (pre_effect_expired_swept/0.1.24) is genuinely zero. The
    first correction pass's dump()-count cross-check alone was not sufficient: it never re-read
    the actual bytes, so a real baseline file's disappearance went unnoticed as long as dump()
    happened to agree. This test's whole point is that the OUTPUT now DOES change relative to
    that first pass - see CORRECTIONS.md's follow-up in the second review."""
    _require_safeagent_venvs()
    prediction = json.loads(ttl._PREDICTION_PATH.read_text())
    release = "0.1.24"
    info = _one_release_info(release)
    observed: dict[str, object] = {}
    with _short_ledger_daemon() as ledger:
        original_seal = ledger.seal

        def delete_known_empty_after_seal() -> None:
            original_seal()
            p = Path(ledger.store_path)
            observed["source_existed"] = p.exists()
            observed["source_bytes"] = p.stat().st_size if p.exists() else None
            p.unlink()

        with patch.object(ledger, "seal", delete_known_empty_after_seal):
            rec = ttl.run_trial(
                "pre_effect_expired_swept", release, 0, prediction, ledger, "test",
                tmp_path / "trial", info,
            )
    # The baseline was real: a genuine 0-byte file existed immediately before deletion, not an
    # inferred or assumed absence.
    assert observed == {"source_existed": True, "source_bytes": 0}
    assert rec["passed"] is False
    assert rec["observation_complete"] is False
    assert rec["effect_count"] is None
    assert rec["effect_ledger_classification"] == "UNVERIFIED"
    invalid_reason = rec["invalid_reason"]
    assert isinstance(invalid_reason, str) and "missing at observation time" in invalid_reason


def test_injected_missing_ledger_with_real_effects_is_caught(tmp_path: Path) -> None:
    """The dangerous version of the missing-ledger injection: the same deletion, applied instead
    to pending_expired_swept/0.1.23, which genuinely records 2 effects. The now-missing file is
    caught on its own (any missing file at observation time is always invalid, unconditionally);
    ledger_dump_count_before_seal is retained as a genuine, separately reported observation, not
    the reason the missing file is treated as invalid."""
    _require_safeagent_venvs()
    prediction = json.loads(ttl._PREDICTION_PATH.read_text())
    release = "0.1.23"
    info = _one_release_info(release)
    with _short_ledger_daemon() as ledger:
        original_seal = ledger.seal

        def missing_ledger() -> None:
            original_seal()
            Path(ledger.store_path).unlink(missing_ok=True)

        with patch.object(ledger, "seal", missing_ledger):
            rec = ttl.run_trial(
                "pending_expired_swept", release, 0, prediction, ledger, "test",
                tmp_path / "trial", info,
            )
    assert rec["passed"] is False
    assert rec["observation_complete"] is False
    assert rec["effect_count"] is None
    assert rec["ledger_dump_count_before_seal"] == 2
    invalid_reason = rec["invalid_reason"]
    assert isinstance(invalid_reason, str) and "missing at observation time" in invalid_reason


def test_injected_nonzero_observer_exit_is_not_authorized_as_pass(tmp_path: Path) -> None:
    """check_review.py's observer_exit_two: a real observer subprocess whose output still parses
    as valid JSON but whose exit status is nonzero must not authorize passed=True."""
    _require_safeagent_venvs()
    prediction = json.loads(ttl._PREDICTION_PATH.read_text())
    release = "0.1.24"
    info = _one_release_info(release)
    real_run = subprocess.run

    def nonzero_observer(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        outcome = real_run(*args, **kwargs)
        if "observe-ledger" in args[0]:
            outcome.returncode = 2
        return outcome

    with (
        _short_ledger_daemon() as ledger,
        patch.object(subprocess, "run", nonzero_observer),
    ):
        rec = ttl.run_trial(
            "pre_effect_expired_swept", release, 0, prediction, ledger, "test",
            tmp_path / "trial", info,
        )
    assert rec["passed"] is False
    assert rec["observation_complete"] is False
    assert rec["effect_count"] is None
    invalid_reason = rec["invalid_reason"]
    assert isinstance(invalid_reason, str) and "observer exited" in invalid_reason


def test_injected_pre_kill_snapshot_failure_does_not_leak_worker_or_lose_evidence(
    tmp_path: Path,
) -> None:
    """check_review.py's prekill_io_failure: an OSError raised from the pre-kill snapshot call
    must not leave Worker A running forever, and whatever was already known about the trial
    (a receipt stub, and the ledger's own pre-failure state) must still be retained, not lost
    along with the exception. This test reaps any process IT tracks via the same Popen-patch
    pattern as the review's own probe - responsibility for owned processes started during this
    test, separate from run_trial's own internal cleanup being what is under test here."""
    _require_safeagent_venvs()
    prediction = json.loads(ttl._PREDICTION_PATH.read_text())
    release = "0.1.23"
    info = _one_release_info(release)
    original_inspect = ttl._inspect
    workers: list[subprocess.Popen[str]] = []
    real_popen = subprocess.Popen

    def track_worker(*args: Any, **kwargs: Any) -> subprocess.Popen[str]:
        proc = real_popen(*args, **kwargs)
        if "claim-run" in args[0]:
            workers.append(proc)
        return proc

    def fail_pre_kill(*args: Any, **kwargs: Any) -> dict[str, Any]:
        snap = kwargs.get("snapshot_out")
        if snap is not None and Path(snap).name == "claim_pre_kill.sqlite":
            raise OSError("test: snapshot read failure before kill")
        return original_inspect(*args, **kwargs)

    trial_root = tmp_path / "trial"
    try:
        with (
            _short_ledger_daemon() as ledger,
            patch.object(subprocess, "Popen", track_worker),
            patch.object(ttl, "_inspect", fail_pre_kill),
            pytest.raises(OSError),
        ):
            ttl.run_trial(
                "pending_expired_swept", release, 0, prediction, ledger, "test",
                trial_root, info,
            )
        assert (trial_root / "receipt.json").exists()
        assert (trial_root / "ledger.jsonl").exists() or (
            trial_root / "ledger_dump_after_failure.json"
        ).exists()
        assert not any(p.poll() is None for p in workers)
    finally:
        for p in workers:
            if p.poll() is None:
                p.kill()
            with contextlib.suppress(Exception):
                p.communicate(timeout=5)


def test_injected_reset_failure_still_retains_a_receipt(tmp_path: Path) -> None:
    """check_remaining.py's reset_failure: an OSError injected into ledger.reset() itself must
    still leave a receipt in the allocated trial directory - reset used to run before the
    try/finally lifecycle, so this exact injection previously left nothing behind at all."""
    _require_safeagent_venvs()
    prediction = json.loads(ttl._PREDICTION_PATH.read_text())
    release = "0.1.24"
    info = _one_release_info(release)
    trial_root = tmp_path / "reset-failure"
    with (
        _short_ledger_daemon() as ledger,
        patch.object(ledger, "reset", side_effect=OSError("test: injected reset failure")),
        pytest.raises(OSError),
    ):
        ttl.run_trial(
            "pre_effect_expired_swept", release, 0, prediction, ledger, "test",
            trial_root, info,
        )
    receipt_path = trial_root / "receipt.json"
    assert receipt_path.exists()
    rec = json.loads(receipt_path.read_text())
    assert rec["passed"] is False
    assert rec["observation_complete"] is False
    assert rec["reset_confirmed"] is False
    invalid_reason = rec["invalid_reason"]
    assert isinstance(invalid_reason, str) and "injected reset failure" in invalid_reason


def test_run_level_aggregates_a_trial_failure_without_crashing_the_batch(tmp_path: Path) -> None:
    """check_remaining.py's point #2: a test that only calls run_trial() directly does not test
    run()'s own aggregation or incremental persistence. This calls the actual run() - the real
    production entry point - with a genuine injected failure in one of two trials, and checks
    run()'s own accounting: the batch continues past the failure, the failure is recorded (never
    silently dropped from trial_dirs), and the manifest reports INCOMPLETE/all_agree=False rather
    than a success-shaped partial result."""
    _require_safeagent_venvs()
    real_run_trial = ttl.run_trial

    def flaky_run_trial(
        case: str, release: str, index: int, *args: Any, **kwargs: Any
    ) -> dict[str, object]:
        if index == 0:
            raise RuntimeError("test: injected run_trial failure")
        return real_run_trial(case, release, index, *args, **kwargs)

    with patch.object(ttl, "run_trial", flaky_run_trial):
        manifest = ttl.run(
            name="run-level-test", out_root=tmp_path,
            cases=("settled_control",), releases=("0.1.24",), k_per_cell=2,
        )

    assert manifest["status"] == "INCOMPLETE"
    assert manifest["all_agree"] is False
    assert manifest["trial_count"] == 1  # only the second (index 1) trial succeeded
    assert manifest["expected_trial_count"] == 2
    assert len(manifest["execution_failures"]) == 1
    failure = manifest["execution_failures"][0]
    assert failure["trial_id"] == "settled_control-0.1.24-0"
    assert "injected run_trial failure" in failure["error"]
    # The failed trial's ID is still indexed - never silently dropped from accounting.
    assert set(manifest["trial_dirs"]) == {"settled_control-0.1.24-0", "settled_control-0.1.24-1"}
    # The manifest actually on disk matches what was returned: the final incremental write ran.
    on_disk = json.loads((tmp_path / "run-level-test" / "manifest.json").read_text())
    assert on_disk == manifest
