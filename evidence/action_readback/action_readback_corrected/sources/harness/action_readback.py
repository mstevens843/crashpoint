"""Parent harness for the pre-dispatch action identity / external readback experiment.

WHAT THIS MEASURES. Whether a fresh, caller-minted logical-action identity, committed to a
caller-owned store and independently confirmed durable BEFORE dispatch, can be linked - after a
real process crash, a real fresh retry, or a deliberately injected fault - to what a receiver
(crashpoint's out-of-process ledger) actually retains, as read by a separate fresh observer
process with its own file handle. Six cases (see ``action_readback_receipt.CASES``), three trials
each, 18 total: a small, predeclared, non-statistical batch. This is a bounded reference fixture
for a future native-vs-SafeAgent-Control comparison, not that comparison itself - no third-party
package is under test here.

FOUR QUESTIONS, KEPT SEPARATE, exactly as the experiment's scope demands: what was admitted before
dispatch (``admission_payload_digest``, confirmed durable through an independent post-commit
connection - the "dispatch gate" - before Worker A is ever spawned); what the worker itself
claims and whether it ever got to say so (``worker_a_*``/``worker_b_*``, ``client_claim``); what
the receiver ledger actually retains (``effect_count``, ``effect_payload_digests``,
``effect_attempt_ids`` - read by a freshly spawned ``observe`` subprocess, never a live dump or a
worker's exit code); and whether that reading was complete enough to compare against the
admission at all (``observation_availability``, ``externally_verified``).

LIFECYCLE SAFETY. ``run_trial`` tracks every OS process it starts and guarantees, via ``finally``,
that none survive the call. Whatever partial state exists at the point of a hard failure is still
stamped into a receipt-shaped record (``observation_complete=False``, an explicit
``invalid_reason``) and written to disk before the original exception is re-raised - a failure is
retained as evidence, never silently discarded and never claimed as a clean PASS. ``run()`` writes
the manifest once, before trial 1, and appends one line per finished trial to a separate
``journal.jsonl`` immediately after that trial completes - two independently cross-checkable,
crash-resilient records of what happened, not one.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import selectors
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, cast

from ..canonical import receipt
from . import action_readback_runtime as runtime
from .action_readback_receipt import (
    ACTION_TYPE,
    CASES,
    LIMITATIONS,
    REQUIRED_EMBEDDED_LOCK_FILES,
    REQUIRED_EMBEDDED_SOURCE_FILES,
    ActionReadbackTrial,
    build_receipt,
)
from .action_readback_runtime import PREFIX
from .action_readback_verify import MANIFEST_SCHEMA
from .ledger_process import LedgerHandle

_ROOT = Path(__file__).resolve().parents[3]
_PREDICTION_PATH = _ROOT / "results" / "13-action-readback-prediction.json"
_BASE_SHA = "b66e205e69927626072cb4a130351258e9ac282d"

_BARRIER_TIMEOUT: float = 15.0
_EXIT_TIMEOUT: float = 15.0
_LEDGER_STARTUP_TIMEOUT: float = 10.0
_ROLE_TIMEOUT: float = 20.0
_K_PER_CELL: int = 3

_PAYLOAD: dict[str, object] = {"operation": ACTION_TYPE, "marker": "harmless"}


class TrialExecutionError(RuntimeError):
    """A trial could not complete; the caller retains whatever partial state exists."""


# --------------------------------------------------------------------------------------------
# Bounded ledger daemon startup - deliberately local to this module, not imported from another
# experiment and not routed through ledger_process.LedgerDaemon's blocking readline() inside a
# nominal timeout loop. LedgerHandle itself (the plain dataclass wrapper around the control
# socket calls) IS shared, proven infrastructure and is reused as-is.
# --------------------------------------------------------------------------------------------


class _BoundedLedgerDaemon:
    def __init__(self, work: Path) -> None:
        self.work = work
        work.mkdir(parents=True, exist_ok=True)
        (work / "ctl").mkdir(exist_ok=True)
        self.invoke = str(work / "inv.sock")
        self.control = str(work / "ctl" / "ctl.sock")
        self.store = str(work / "ledger.jsonl")
        self.proc: subprocess.Popen[str] | None = None

    def __enter__(self) -> LedgerHandle:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "crashpoint.ledger.daemon",
             "--invoke", self.invoke, "--control", self.control, "--store", self.store],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        assert self.proc.stdout is not None
        sel = selectors.DefaultSelector()
        sel.register(self.proc.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + _LEDGER_STARTUP_TIMEOUT
        buf = ""
        ready = False
        try:
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                for _key, _mask in sel.select(timeout=max(0.0, min(remaining, 0.2))):
                    chunk = os.read(self.proc.stdout.fileno(), 4096)
                    if not chunk:
                        ready = False
                        break
                    buf += chunk.decode("utf-8", errors="replace")
                    if "LEDGER_READY" in buf:
                        ready = True
                        break
                if ready or self.proc.poll() is not None:
                    break
        finally:
            with contextlib.suppress(KeyError, ValueError):
                sel.unregister(self.proc.stdout)
        if not ready:
            with contextlib.suppress(Exception):
                self.proc.kill()
                self.proc.wait(timeout=5)
            raise RuntimeError(
                f"ledger daemon did not become ready within {_LEDGER_STARTUP_TIMEOUT}s "
                "(bounded startup)"
            )
        return LedgerHandle(self.invoke, self.control, self.store, self.proc)

    def __exit__(self, *exc: object) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                with contextlib.suppress(Exception):
                    self.proc.wait(timeout=5)


def _reset_and_establish_baseline(ledger: LedgerHandle) -> None:
    """Reset the ledger, require a strictly verified empty result, then create and verify a REAL,
    on-disk, zero-byte ledger file at the store path before any worker can append to it - the
    raw-empty baseline the rest of the trial's observation is checked against. Raises rather than
    proceeding on anything less than full confirmation."""
    ledger.reset()
    dump = ledger.dump()
    count = dump.get("count")
    if not isinstance(count, int) or isinstance(count, bool) or count != 0:
        raise RuntimeError(
            f"ledger reset did not produce a strictly verified empty ledger: dump count={count!r}"
        )
    store_path = Path(ledger.store_path)
    try:
        store_path.touch(exist_ok=False)
    except FileExistsError as exc:
        raise RuntimeError(
            f"cannot establish a raw-empty baseline: {store_path} already existed immediately "
            "after a verified reset"
        ) from exc
    stat = store_path.stat()
    if stat.st_size != 0:
        raise RuntimeError(
            f"raw-empty baseline at {store_path} is not genuinely 0 bytes: {stat.st_size}"
        )


# --------------------------------------------------------------------------------------------
# Process lifecycle helpers.
# --------------------------------------------------------------------------------------------


def _kill_and_reap(proc: subprocess.Popen[str]) -> tuple[bool, int | None, str | None]:
    try:
        os.kill(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        return False, proc.poll(), "process had already exited before SIGKILL was sent"
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        return False, None, "process did not die within 5s of SIGKILL"
    code = proc.returncode
    expected = -int(signal.SIGKILL)
    if code != expected:
        return True, code, f"expected exit code {expected} (SIGKILL), got {code}"
    return True, code, None


def _reap_owned(owned: list[subprocess.Popen[str]]) -> list[str]:
    """Kill and reap every still-alive process this trial started. Idempotent and exception-safe
    per process, so one stubborn process cannot prevent reaping the rest. Never raises."""
    problems: list[str] = []
    for proc in owned:
        if proc.poll() is not None:
            continue
        try:
            os.kill(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
        except Exception as exc:
            problems.append(f"failed to signal owned pid {proc.pid}: {exc!r}")
            continue
        try:
            proc.wait(timeout=5)
        except Exception as exc:
            problems.append(f"owned pid {proc.pid} did not reap within 5s of SIGKILL: {exc!r}")
    return problems


def wait_for_barrier(
    proc: subprocess.Popen[str], expected_point: str, timeout: float
) -> tuple[list[dict[str, Any]], str, str | None]:
    """Bounded read of a worker's stdout via ``selectors`` - never a blocking readline inside a
    nominal timeout loop. Returns every parsed event seen, the raw stdout text accumulated so far
    (for retention as evidence, exactly like a run-to-completion worker's captured stdout), and,
    on success, None as the error."""
    assert proc.stdout is not None
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    buf = ""
    raw_text = ""
    events: list[dict[str, Any]] = []
    try:
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            for _key, _mask in sel.select(timeout=max(0.0, min(remaining, 0.2))):
                chunk = os.read(proc.stdout.fileno(), 4096)
                if not chunk:
                    return events, raw_text, (
                        "worker stdout closed before the expected barrier was observed"
                    )
                decoded = chunk.decode("utf-8", errors="replace")
                raw_text += decoded
                buf += decoded
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    if not line.startswith(PREFIX):
                        continue
                    try:
                        ev = json.loads(line[len(PREFIX):])
                    except json.JSONDecodeError:
                        continue
                    events.append(ev)
                    if ev.get("event") == "barrier" and ev.get("point") == expected_point:
                        return events, raw_text, None
                    if ev.get("event") == "fatal":
                        return events, raw_text, f"worker reported fatal: {ev.get('reason')}"
            if proc.poll() is not None:
                return events, raw_text, (
                    f"worker exited (code={proc.returncode}) before the barrier"
                )
    finally:
        with contextlib.suppress(KeyError, ValueError):
            sel.unregister(proc.stdout)
    return events, raw_text, "timed out waiting for the expected barrier"


def _wait_for_exit(proc: subprocess.Popen[str], timeout: float) -> tuple[str, str, int | None]:
    """Bounded wait for a worker to exit on its own (the clean/payload_mismatch/retry paths -
    none of which are ever killed). Returns (stdout, stderr, returncode); returncode is None if
    the bound was exceeded without the process exiting (the caller must then treat this as a
    hard failure, never wait unboundedly)."""
    try:
        out, err = proc.communicate(timeout=timeout)
        return out, err, proc.returncode
    except subprocess.TimeoutExpired:
        return "", "", None


def _drain_remaining(proc: subprocess.Popen[str]) -> tuple[str, str]:
    """Safe to call on an already-dead process: reads and returns whatever remained buffered on
    stdout/stderr and closes the pipes. Never raises or blocks unboundedly - a killed worker's
    pipes are always drained within this bound so the retained stdio file is complete."""
    try:
        out, err = proc.communicate(timeout=5)
        return out or "", err or ""
    except Exception:
        return "", ""


def _retain_worker_stdio(trial_root: Path, label: str, stdout_text: str, stderr_text: str) -> None:
    (trial_root / f"{label}.stdout").write_text(stdout_text)
    (trial_root / f"{label}.stderr").write_text(stderr_text)


def _parse_events(stdout: str) -> list[dict[str, Any]]:
    events = []
    for line in stdout.splitlines():
        if not line.startswith(PREFIX):
            continue
        with contextlib.suppress(json.JSONDecodeError):
            events.append(json.loads(line[len(PREFIX):]))
    return events


def _find_event(events: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for ev in events:
        if ev.get("event") == name:
            return ev
    return None


def _run_observer(ledger_path: Path, archive_out: Path, action_id: str) -> dict[str, Any]:
    """Spawn the fresh observer process, bounded, and return its reported readback_result event
    (or a synthetic NEVER_READ-shaped record if the observer itself could not be run/parsed -
    this is the harness's own launch failing, distinct from the observer's OWN honest report of a
    failed read)."""
    proc = subprocess.run(
        [sys.executable, "-m", "crashpoint.harness.action_readback_runtime", "observe",
         "--ledger-path", str(ledger_path), "--archive-out", str(archive_out),
         "--action-id", action_id],
        capture_output=True, text=True, timeout=_ROLE_TIMEOUT, check=False,
    )
    events = _parse_events(proc.stdout)
    result = _find_event(events, "readback_result")
    if result is None:
        return {
            "raw_state": "NEVER_READ",
            "raw_sha256": None,
            "byte_length": None,
            "chain_valid": None,
            "broken_at_index": None,
            "attempts": {},
            "side_effects": {},
            "effect_digests": {},
            "attempt_ids_by_intent": {},
            "_observer_pid": None,
            "_observer_exit_status": proc.returncode,
            "_observer_launch_error": f"no readback_result event: stdout={proc.stdout!r}",
        }
    result["_observer_exit_status"] = proc.returncode
    result["_observer_pid"] = _find_event(events, "observer_started")
    result["_observer_pid"] = (
        result["_observer_pid"].get("pid") if result["_observer_pid"] else None
    )
    return result


# --------------------------------------------------------------------------------------------
# Trial state: every field ActionReadbackTrial needs, defaulted BEFORE any risky operation so a
# mid-trial exception still has a receipt-shaped record to stamp and retain.
# --------------------------------------------------------------------------------------------


def _initial_trial_state(
    run_id: str, case: str, trial_id: str, action_id: str, receiver_ref: str,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "case": case,
        "trial_id": trial_id,
        "action_id": action_id,
        "receiver_ref": receiver_ref,
        "admitted_at_utc": None,
        "admission_payload_digest": None,
        "admission_journal_mode": None,
        "admission_synchronous": None,
        "admission_commit_confirmed": False,
        "worker_a_pid": None,
        "worker_a_attempt_id": f"{action_id}:worker-a",
        "worker_a_read_admission_confirmed": False,
        "worker_a_dispatch_payload_digest": None,
        "worker_a_killed": False,
        "worker_a_exit_status": None,
        "worker_a_barrier_observed": False,
        "worker_a_barrier_point": None,
        "worker_a_local_receipt": None,
        "worker_b_used": False,
        "worker_b_pid": None,
        "worker_b_attempt_id": None,
        "worker_b_exit_status": None,
        "worker_b_local_receipt": None,
        "injected_fault": None,
        "receiver_baseline_established": False,
        "receiver_seal_dump_count": None,
        "receiver_seal_dump_head": None,
        "effect_count": None,
        "effect_attempt_ids": [],
        "effect_payload_digests": [],
        "effect_ledger_classification": "UNVERIFIED",
        "digests_match_admission": None,
        "observer_pid": None,
        "observer_exit_status": None,
        "observer_raw_ledger_state": "NEVER_READ",
        "observer_raw_ledger_sha256": None,
        "protocol_valid": False,
        "observation_complete": False,
        "invalid_reason": None,
        "expected_result": {},
    }


def _finalize_trial(
    state: dict[str, Any], *, observation_complete: bool, invalid_reason: str | None,
    expected_result: dict[str, object], crashpoint_commit: str,
) -> dict[str, object]:
    state["observation_complete"] = observation_complete
    state["invalid_reason"] = invalid_reason
    state["expected_result"] = expected_result
    trial = ActionReadbackTrial(**state)
    return build_receipt(trial, crashpoint_commit=crashpoint_commit)


def _write_failure_record(
    state: dict[str, Any], trial_root: Path, reason: str, crashpoint_commit: str,
    prediction: dict[str, Any],
) -> dict[str, object]:
    """Honest partial-progress receipt from whatever state exists at the point of failure - never
    a fixed stub. Never raises: a failure to write the failure record itself is reported, not
    hidden, by the caller's own exception handling."""
    case = state.get("case")
    expected = prediction.get("cases", {}).get(case, {}) if isinstance(case, str) else {}
    rec = _finalize_trial(
        state, observation_complete=False, invalid_reason=reason,
        expected_result=expected, crashpoint_commit=crashpoint_commit,
    )
    trial_root.mkdir(parents=True, exist_ok=True)
    (trial_root / "receipt.json").write_text(json.dumps(rec, indent=2, sort_keys=True))
    return rec


def _confirm_receiver_effect(ledger: LedgerHandle, action_id: str) -> bool:
    """Live, independent confirmation - the harness's own control-socket connection, never the
    worker's effect_ack self-report - that the receiver has recorded at least one effect for this
    action_id. Used before killing Worker A in effect_before_lost_receipt/naive_retry, per "confirm
    the receiver has recorded the effect, then kill the worker"."""
    dump = ledger.dump()
    side_effects = dump.get("side_effects")
    if not isinstance(side_effects, dict):
        return False
    count = side_effects.get(action_id, 0)
    return isinstance(count, int) and count >= 1


def run_trial(
    case: str, index: int, prediction: dict[str, Any], ledger: LedgerHandle, run_id: str,
    crashpoint_commit: str, trial_root: Path,
) -> dict[str, object]:
    trial_id = f"{case}-{index}"
    action_id = str(uuid.uuid4())
    state = _initial_trial_state(run_id, case, trial_id, action_id, ledger.invoke_path)
    owned: list[subprocess.Popen[str]] = []
    note: str | None = None

    def note_invalid(reason: str) -> None:
        nonlocal note
        if note is None:
            note = reason

    try:
        _reset_and_establish_baseline(ledger)
        state["receiver_baseline_established"] = True

        admission_db = trial_root / "admission.sqlite"
        trial_root.mkdir(parents=True, exist_ok=True)
        runtime.init_admission_db(str(admission_db))
        admitted_at = runtime.utc_now_iso()
        digest = runtime.write_admission(
            str(admission_db), action_id=action_id, run_id=run_id, trial_id=trial_id,
            case_name=case, receiver_ref=ledger.invoke_path, payload=dict(_PAYLOAD),
            admitted_at_utc=admitted_at,
        )
        state["admitted_at_utc"] = admitted_at
        state["admission_payload_digest"] = digest

        durability_conn = runtime.connect_admission_db(str(admission_db))
        try:
            journal_mode, synchronous = runtime.admission_durability_config(durability_conn)
        finally:
            durability_conn.close()
        state["admission_journal_mode"] = journal_mode
        state["admission_synchronous"] = synchronous

        # The dispatch gate: an independent, fresh connection confirms the row is durably
        # committed BEFORE Worker A is ever spawned. Control flow itself (Worker A's Popen call
        # happens only after this succeeds) is the gate - a stronger guarantee than comparing
        # timestamps, since a real subprocess spawn cannot race backward in time.
        readback = runtime.read_admission(str(admission_db), action_id)
        if readback is None or readback.get("payload_digest") != digest:
            note_invalid(
                f"admission commit could not be independently confirmed: readback={readback!r}"
            )
        else:
            state["admission_commit_confirmed"] = True

        if not state["admission_commit_confirmed"]:
            raise TrialExecutionError(note or "admission commit not confirmed")

        fault = "payload-mismatch" if case == "payload_mismatch" else None
        if fault:
            state["injected_fault"] = "payload_mismatch"
        elif case == "unavailable_readback":
            state["injected_fault"] = "observer_evidence_denied"

        worker_a = subprocess.Popen(
            [sys.executable, "-m", "crashpoint.harness.action_readback_runtime", "dispatch",
             "--admission-db", str(admission_db), "--invoke", ledger.invoke_path,
             "--action-id", action_id, "--attempt-id", state["worker_a_attempt_id"],
             "--case", case, *(["--fault", fault] if fault else [])],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        owned.append(worker_a)
        state["worker_a_pid"] = worker_a.pid

        if case == "stopped_before_effect":
            events, raw_out, err = wait_for_barrier(worker_a, "pre_dispatch", _BARRIER_TIMEOUT)
            if err:
                note_invalid(f"Worker A did not reach the pre_dispatch barrier: {err}")
            else:
                state["worker_a_barrier_observed"] = True
                state["worker_a_barrier_point"] = "pre_dispatch"
                if _find_event(events, "admission_read") is not None:
                    state["worker_a_read_admission_confirmed"] = True
            killed, code, kill_err = _kill_and_reap(worker_a)
            state["worker_a_killed"] = killed
            state["worker_a_exit_status"] = code
            if kill_err:
                note_invalid(f"Worker A kill/reap problem: {kill_err}")
            extra_out, extra_err = _drain_remaining(worker_a)
            _retain_worker_stdio(trial_root, "worker_a", raw_out + extra_out, extra_err)
            state["protocol_valid"] = note is None

        elif case in {"effect_before_lost_receipt", "naive_retry"}:
            events, raw_out, err = wait_for_barrier(
                worker_a, "post_effect_pre_receipt", _BARRIER_TIMEOUT
            )
            if err:
                note_invalid(f"Worker A did not reach the post_effect_pre_receipt barrier: {err}")
            else:
                state["worker_a_barrier_observed"] = True
                state["worker_a_barrier_point"] = "post_effect_pre_receipt"
                if _find_event(events, "admission_read") is not None:
                    state["worker_a_read_admission_confirmed"] = True
                ack = _find_event(events, "effect_ack")
                if ack is not None:
                    state["worker_a_dispatch_payload_digest"] = ack.get("dispatch_payload_digest")
                if not _confirm_receiver_effect(ledger, action_id):
                    note_invalid(
                        "receiver did not show the recorded effect before Worker A was killed"
                    )
            killed, code, kill_err = _kill_and_reap(worker_a)
            state["worker_a_killed"] = killed
            state["worker_a_exit_status"] = code
            if kill_err:
                note_invalid(f"Worker A kill/reap problem: {kill_err}")
            extra_out, extra_err = _drain_remaining(worker_a)
            _retain_worker_stdio(trial_root, "worker_a", raw_out + extra_out, extra_err)

            if case == "naive_retry" and note is None:
                attempt_b = f"{action_id}:worker-b-retry"
                state["worker_b_used"] = True
                state["worker_b_attempt_id"] = attempt_b
                worker_b = subprocess.Popen(
                    [sys.executable, "-m", "crashpoint.harness.action_readback_runtime",
                     "dispatch", "--admission-db", str(admission_db), "--invoke",
                     ledger.invoke_path, "--action-id", action_id, "--attempt-id", attempt_b,
                     "--case", case],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                owned.append(worker_b)
                state["worker_b_pid"] = worker_b.pid
                out_b, err_b, code_b = _wait_for_exit(worker_b, _EXIT_TIMEOUT)
                state["worker_b_exit_status"] = code_b
                _retain_worker_stdio(trial_root, "worker_b", out_b, err_b)
                if code_b is None:
                    note_invalid("Worker B (retry) did not exit within the bound")
                elif code_b != 0:
                    note_invalid(f"Worker B (retry) exited {code_b}: stderr={err_b!r}")
                else:
                    completion = runtime.read_completion(str(admission_db), action_id, attempt_b)
                    state["worker_b_local_receipt"] = completion
            state["protocol_valid"] = note is None

        else:  # clean, payload_mismatch, unavailable_readback: Worker A runs to completion
            out_a, err_a, code_a = _wait_for_exit(worker_a, _EXIT_TIMEOUT)
            state["worker_a_exit_status"] = code_a
            state["worker_a_killed"] = False
            _retain_worker_stdio(trial_root, "worker_a", out_a, err_a)
            events = _parse_events(out_a)
            if _find_event(events, "admission_read") is not None:
                state["worker_a_read_admission_confirmed"] = True
            ack = _find_event(events, "effect_ack")
            if ack is not None:
                state["worker_a_dispatch_payload_digest"] = ack.get("dispatch_payload_digest")
            if code_a is None:
                note_invalid("Worker A did not exit within the bound")
            elif code_a != 0:
                note_invalid(f"Worker A exited {code_a}: stderr={err_a!r}")
            else:
                completion = runtime.read_completion(
                    str(admission_db), action_id, state["worker_a_attempt_id"]
                )
                state["worker_a_local_receipt"] = completion
            state["protocol_valid"] = note is None

        # --- terminal boundary: seal (rejects further effects), THEN dump (a race-free
        # post-seal anchor - no write can land between disabling writes and reading state) ---
        ledger.seal()
        seal_dump = ledger.dump()
        state["receiver_seal_dump_count"] = seal_dump.get("count")
        state["receiver_seal_dump_head"] = seal_dump.get("head")

        raw_ledger_path = Path(ledger.store_path)
        # The negative control: deny the observer's required evidence AFTER the effect, by
        # deleting the raw ledger file the observer is about to read. Labeled via injected_fault,
        # not left to look like an accidental infrastructure failure.
        if case == "unavailable_readback" and note is None and raw_ledger_path.exists():
            raw_ledger_path.unlink()

        archive_out = trial_root / "ledger.jsonl"
        obs = _run_observer(raw_ledger_path, archive_out, action_id)
        state["observer_pid"] = obs.get("_observer_pid")
        state["observer_exit_status"] = obs.get("_observer_exit_status")
        state["observer_raw_ledger_state"] = obs.get("raw_state", "NEVER_READ")
        state["observer_raw_ledger_sha256"] = obs.get("raw_sha256")
        (trial_root / "observer_report.json").write_text(
            json.dumps(obs, indent=2, sort_keys=True, default=str)
        )

        side_effects = cast(dict[str, Any], obs.get("side_effects") or {})
        effect_digests = cast(dict[str, Any], obs.get("effect_digests") or {})
        attempt_ids_by_intent = cast(dict[str, Any], obs.get("attempt_ids_by_intent") or {})
        raw_state = state["observer_raw_ledger_state"]
        chain_valid = obs.get("chain_valid")

        if raw_state in {"NEVER_READ", "READ_FAILED"}:
            state["effect_count"] = None
            state["effect_ledger_classification"] = "UNVERIFIED"
        elif chain_valid is False:
            state["effect_count"] = None
            state["effect_ledger_classification"] = "VOID"
        else:
            n = side_effects.get(action_id, 0)
            digests = effect_digests.get(action_id, [])
            attempt_ids = [a for a in attempt_ids_by_intent.get(action_id, []) if a is not None]
            state["effect_count"] = n
            state["effect_attempt_ids"] = attempt_ids
            state["effect_payload_digests"] = digests
            if n == 0:
                state["effect_ledger_classification"] = "ZERO"
                state["digests_match_admission"] = None
            else:
                state["effect_ledger_classification"] = (
                    "EXACTLY_ONCE" if n == 1
                    else ("DUPLICATED" if len(set(digests)) == 1 else "DIVERGED")
                )
                admitted_digest = state["admission_payload_digest"]
                state["digests_match_admission"] = (
                    len(digests) == n and all(d == admitted_digest for d in digests)
                )

        expected = prediction.get("cases", {}).get(case, {})
        rec = _finalize_trial(
            state, observation_complete=note is None, invalid_reason=note,
            expected_result=expected, crashpoint_commit=crashpoint_commit,
        )
        (trial_root / "receipt.json").write_text(json.dumps(rec, indent=2, sort_keys=True))
        return rec
    except Exception as exc:
        reap_problems = _reap_owned(owned)
        reason = f"{exc}" if note is None else note
        if reap_problems:
            reason = f"{reason} | cleanup problems: {reap_problems}"
        _write_failure_record(state, trial_root, reason, crashpoint_commit, prediction)
        if isinstance(exc, TrialExecutionError):
            raise
        raise TrialExecutionError(reason) from exc
    finally:
        _reap_owned(owned)


# --------------------------------------------------------------------------------------------
# Provenance embedding.
# --------------------------------------------------------------------------------------------


def _embed_sources(bundle_root: Path) -> dict[str, Any]:
    sources_dir = bundle_root / "sources"
    hashes: dict[str, str] = {}
    for rel in REQUIRED_EMBEDDED_SOURCE_FILES:
        src = _ROOT / "src" / "crashpoint" / rel
        dest = sources_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = src.read_bytes()
        dest.write_bytes(data)
        hashes[rel] = hashlib.sha256(data).hexdigest()
    for rel in REQUIRED_EMBEDDED_LOCK_FILES:
        src = _ROOT / rel
        dest = sources_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = src.read_bytes()
        dest.write_bytes(data)
        hashes[rel] = hashlib.sha256(data).hexdigest()

    prediction_bytes = _PREDICTION_PATH.read_bytes()
    (bundle_root / "prediction.json").write_bytes(prediction_bytes)
    prediction_sha256 = hashlib.sha256(prediction_bytes).hexdigest()
    return {
        "embedded_source_sha256": hashes,
        "prediction_embedded_sha256": prediction_sha256,
    }


def _build_manifest(
    name: str, run_id: str, status: str, crashpoint_commit: str, prediction_sha256: str,
    embedded: dict[str, Any], trial_dirs: dict[str, str], receipts: list[dict[str, Any]],
    execution_failures: list[dict[str, object]], cases: tuple[str, ...] = CASES,
    k_per_cell: int = _K_PER_CELL,
) -> dict[str, Any]:
    from collections import Counter

    cells_summary: dict[str, Any] = {}
    for case in cases:
        cell = [r for r in receipts if r["case"] == case]
        cells_summary[case] = {
            "case": case,
            "count": len(cell),
            "passing": sum(1 for r in cell if r["passed"]),
            "outcomes": dict(
                Counter(str(r["observed_result"].get("external_outcome")) for r in cell)
            ),
        }

    manifest: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "name": name,
        "run_id": run_id,
        "status": status,
        "experiment_family": "action_readback_pre_dispatch_identity",
        "claim": (
            "A fresh, caller-minted logical-action identity, committed to a caller-owned SQLite "
            "store and independently confirmed durable before dispatch, can be linked after a "
            "real process crash or a real fresh retry to what an out-of-process receiver ledger "
            "actually retains, as read by a separate fresh observer process. An effect recorded "
            "under the right action_id but a different payload is exposed as a mismatch, not "
            "folded into intended-action success; evidence deliberately denied after the effect "
            "is reported as unverified, never as zero effects or confirmed success."
        ),
        "base_sha": _BASE_SHA,
        "crashpoint_commit": crashpoint_commit,
        "prediction_path": "results/13-action-readback-prediction.json",
        "prediction_sha256": prediction_sha256,
        "prediction_embedded_path": "prediction.json",
        "prediction_embedded_sha256": embedded.get("prediction_embedded_sha256"),
        "embedded_source_sha256": embedded.get("embedded_source_sha256"),
        "cases": list(cases),
        "trials_per_cell": k_per_cell,
        "trial_count": len(receipts),
        "expected_trial_count": len(cases) * k_per_cell,
        "execution_failures": execution_failures,
        "trial_dirs": trial_dirs,
        "trials": receipts,
        "cells_summary": cells_summary,
        "all_agree": (
            bool(receipts) and not execution_failures and all(bool(r["passed"]) for r in receipts)
        ),
        "limitations": LIMITATIONS,
    }
    manifest["receipt"] = receipt(manifest)
    return manifest


def render(manifest: dict[str, Any]) -> str:
    lines = [
        f"Action readback evidence - {manifest['trial_count']}/{manifest['expected_trial_count']} "
        f"trials ({manifest['trials_per_cell']} per cell x {len(manifest['cases'])} cases), "
        f"status={manifest['status']}",
        f"all trials agree with the pre-registered prediction: {manifest['all_agree']}",
    ]
    if manifest["execution_failures"]:
        lines.append(f"EXECUTION FAILURES (not silently dropped): {manifest['execution_failures']}")
    for case in cast(list[str], manifest["cases"]):
        c = manifest["cells_summary"][case]
        lines.append(f"{case}: {c['passing']}/{c['count']} pass; outcomes={c['outcomes']}")
    return "\n".join(lines)


def run(
    name: str, *, out_root: Path | None = None, cases: tuple[str, ...] = CASES,
    k_per_cell: int = _K_PER_CELL,
) -> dict[str, Any]:
    import subprocess as _sp

    crashpoint_commit = _sp.run(
        ["git", "rev-parse", "HEAD"], cwd=_ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    prediction = json.loads(_PREDICTION_PATH.read_text())

    bundle_root = (out_root or (_ROOT / "evidence" / "action_readback")) / name
    if bundle_root.exists():
        raise FileExistsError(f"refusing to overwrite an existing bundle at {bundle_root}")
    bundle_root.mkdir(parents=True)
    embedded = _embed_sources(bundle_root)
    prediction_sha256 = hashlib.sha256(_PREDICTION_PATH.read_bytes()).hexdigest()
    run_id = f"{name}-{uuid.uuid4()}"

    # Persist the run manifest BEFORE launching trial 1: even a total crash during trial 1
    # leaves a manifest proving what was intended.
    manifest = _build_manifest(
        name, run_id, "RUNNING", crashpoint_commit, prediction_sha256, embedded, {}, [], [],
        cases=cases, k_per_cell=k_per_cell,
    )
    manifest_path = bundle_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    journal_path = bundle_root / "journal.jsonl"
    journal_path.touch()

    trial_dirs: dict[str, str] = {}
    receipts: list[dict[str, Any]] = []
    execution_failures: list[dict[str, object]] = []

    # A short, OS-tempdir-rooted work directory for the ledger's Unix sockets - never nested
    # under bundle_root, whose path (an evidence bundle under a deeply nested project tree) can
    # easily exceed the ~104-byte AF_UNIX path limit on macOS.
    import tempfile

    with (
        tempfile.TemporaryDirectory(prefix="cp-ar-ledger-") as ledger_tmp,
        _BoundedLedgerDaemon(Path(ledger_tmp) / "ledger") as ledger,
    ):
        for case in cases:
            for index in range(k_per_cell):
                trial_id = f"{case}-{index}"
                rel_dir = f"trials/{trial_id}"
                trial_root = bundle_root / rel_dir
                trial_dirs[trial_id] = rel_dir
                try:
                    rec = run_trial(
                        case, index, prediction, ledger, run_id, crashpoint_commit, trial_root,
                    )
                    receipts.append(rec)
                except TrialExecutionError as exc:
                    execution_failures.append({"trial_id": trial_id, "reason": str(exc)})
                    fallback = trial_root / "receipt.json"
                    if fallback.exists():
                        receipts.append(json.loads(fallback.read_text()))
                # Append to the journal and rewrite the manifest incrementally - two
                # independently cross-checkable, crash-resilient records.
                if receipts and receipts[-1].get("trial_id") == trial_id:
                    with journal_path.open("a", encoding="utf-8") as jf:
                        jf.write(
                            json.dumps({"trial_id": trial_id, "receipt": receipts[-1]},
                                       sort_keys=True) + "\n"
                        )
                status = "COMPLETE" if not execution_failures else "INCOMPLETE"
                manifest = _build_manifest(
                    name, run_id, status, crashpoint_commit, prediction_sha256, embedded,
                    dict(trial_dirs), list(receipts), list(execution_failures),
                    cases=cases, k_per_cell=k_per_cell,
                )
                manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))

    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="action_readback")
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    manifest = run(args.name, out_root=args.out_dir)
    print(render(manifest))
    print(f"\nreceipt: {manifest['receipt']}")
    bundle_root = (args.out_dir or (_ROOT / "evidence" / "action_readback")) / args.name
    print(f"wrote {bundle_root / 'manifest.json'}")
    return 0 if manifest["status"] == "COMPLETE" and manifest["all_agree"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
