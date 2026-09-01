"""The trial harness must fail loudly on adapter process failures."""

from __future__ import annotations

import signal
import subprocess

import pytest

from crashpoint.harness.trial import TrialExecutionError, _run_checked


def test_expected_sigkill_crash_run_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            ["python"], -int(signal.SIGKILL), stdout="", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    _run_checked(
        ["python", "-m", "adapter"],
        runtime_id="r_dup",
        barrier="b1",
        phase="crash",
        timeout=1.0,
        expected_returncode=-int(signal.SIGKILL),
    )


def test_unexpected_successful_crash_run_reports_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            ["python"], 0, stdout="adapter output", stderr="adapter error"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(TrialExecutionError) as exc:
        _run_checked(
            ["python", "-m", "adapter"],
            runtime_id="r_dup",
            barrier="b1",
            phase="crash",
            timeout=1.0,
            expected_returncode=-int(signal.SIGKILL),
        )

    msg = str(exc.value)
    assert "runtime=r_dup barrier=b1" in msg
    assert "expected signal 9 (SIGKILL), got exit 0" in msg
    assert "stdout:\nadapter output" in msg
    assert "stderr:\nadapter error" in msg


def test_recovery_failure_reports_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            ["python"], 2, stdout="", stderr="No module named temporalio"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(TrialExecutionError) as exc:
        _run_checked(
            ["python", "-m", "adapter"],
            runtime_id="r_tmp_naive",
            barrier="b1",
            phase="recovery",
            timeout=1.0,
            expected_returncode=0,
        )

    assert "adapter process failed during recovery phase" in str(exc.value)
    assert "No module named temporalio" in str(exc.value)


def test_timeout_reports_partial_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(
            ["python"], timeout=1.0, output="partial stdout", stderr=b"partial stderr"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(TrialExecutionError) as exc:
        _run_checked(
            ["python", "-m", "adapter"],
            runtime_id="r_dbos_naive",
            barrier="b2",
            phase="crash",
            timeout=1.0,
            expected_returncode=-int(signal.SIGKILL),
        )

    msg = str(exc.value)
    assert "got timeout" in msg
    assert "partial stdout" in msg
    assert "partial stderr" in msg
