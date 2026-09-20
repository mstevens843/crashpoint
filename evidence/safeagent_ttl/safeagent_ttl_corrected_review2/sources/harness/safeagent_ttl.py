"""Parent harness for the SafeAgent TTL / sweep / fresh-client experiment.

WHAT THIS MEASURES. Whether safeagent-exec-guard's ``SQLiteExecutionStore.sweep_stale_pending()``
lets an unsettled logical action become executable again after a real process death, a real
elapsed TTL, and an explicit sweep - and, separately, what changed about that boundary between
the released 0.1.23 and 0.1.24 distributions. Five cases (see ``safeagent_ttl_receipt.CASES``),
each run against both releases, three trials per cell, 30 trials total: a small, predeclared,
non-statistical batch.

PROCESS SHAPE. Worker A and Worker B (the fresh retry) each run in a subprocess of the release
under test's ISOLATED venv (``.local/safeagent-envs/<release>/``), never the shared crashpoint
dev venv, which never has SafeAgent installed. Worker A is a real OS process this harness
SIGKILLs after independently verifying, through the out-of-process ledger and a fresh,
non-mutating ``inspect`` call, that the claim and effect boundary match the case's shape - never
a caught exception standing in for a crash. The sweep and the retry are each fresh clients
opening the same durable claim database after Worker A is confirmed dead.

THREE QUESTIONS, KEPT SEPARATE, exactly as the experiment's scope demands: did the retry's claim
succeed (``retry_claim_admitted``), how many external effects the independent ledger actually
recorded (``effect_count``, read by a freshly spawned observer process, never off a claim return
value or a cached daemon counter), and what SafeAgent's own store reports as the final row
(``final_claim_row``). ``safeagent_ttl_receipt.derive_label`` turns those three into one
human-readable label, purely, so nothing is asserted that the three fields do not already show.

LIFECYCLE SAFETY. ``run_trial`` tracks every OS process it starts and guarantees, via
``finally``, that none survive the call - a snapshot/role/observer failure must not leave Worker A
blocked forever. Whatever partial state exists at the point of a hard failure is still stamped
into a receipt-shaped record (``observation_complete=False``, an explicit ``invalid_reason``) and
written to disk before the original exception is re-raised, so a failure is retained as evidence,
never silently discarded and never claimed as a clean PASS. This module was corrected on
2026-09-19 after an independent review (recorded in
``handoff/safeagent-ttl/CORRECTIONS.md``) found the original version could leak a live worker
process, silently convert a missing ledger file into a false "confirmed empty," and drop discarded
role/observer exit codes; the fixes below are scoped to those findings.
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
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, cast

from .. import canonical as canonical_module
from .. import ledger as ledger_package
from ..canonical import receipt
from .ledger_process import LedgerHandle
from .safeagent_ttl_receipt import (
    CASES,
    LIMITATIONS,
    RELEASES,
    REQUIRED_EMBEDDED_SOURCE_FILES,
    TTL_BEFORE_CONTROL_SECONDS,
    TTL_EXPIRED_SECONDS,
    SafeAgentTTLTrial,
    build_receipt,
    derive_label,
    validate_receipt,
)
from .safeagent_ttl_runtime import PREFIX
from .safeagent_ttl_verify import MANIFEST_SCHEMA, LedgerObservation, classify_effect_count

_ROOT = Path(__file__).resolve().parents[3]
_PREDICTION_PATH = _ROOT / "results" / "12-safeagent-ttl-prediction.json"
_BASE_SHA = "606893ebb353df5dab3ac68738051eb5fbb7286e"

# TTL_EXPIRED_SECONDS / TTL_BEFORE_CONTROL_SECONDS / case_requires_sweep / expected_ttl_for_case
# are imported, not redefined, from safeagent_ttl_receipt.py: the schema's own validate_receipt
# needs the identical values to check a trial's self-reported pending_ttl_seconds/sweep_invoked
# against the one true config, so there must be exactly one definition, not two that could drift.
_TTL_MARGIN_BEFORE: float = 0.2
_TTL_MARGIN_PAST: float = 0.4
_MAX_EXTRA_WAIT: float = 10.0
_BARRIER_TIMEOUT: float = 20.0
_ROLE_TIMEOUT: float = 20.0
_LEDGER_STARTUP_TIMEOUT: float = 10.0
_K_PER_CELL: int = 3

VENV_PYTHON: dict[str, str] = {
    "0.1.23": str(_ROOT / ".local/safeagent-envs/0.1.23/bin/python"),
    "0.1.24": str(_ROOT / ".local/safeagent-envs/0.1.24/bin/python"),
}

# Independently verified during this task's exploratory phase: downloaded directly from
# files.pythonhosted.org, hashed locally with sha256, and cross-checked against the sha256 the
# PyPI JSON API reports for the same file - both agreed. Hardcoded because a released wheel's
# bytes are immutable once published; re-downloading on every run would add fragility, not rigor.
# Cross-checked at runtime against the freshly re-hashed, bundle-embedded sqlite_store.py source
# (see ``_embed_sources``) so offline verification never has to trust this constant alone.
_WHEEL_SHA256: dict[str, str] = {
    "0.1.23": "ef28d06b4e32f6bff88fcf86f21556a5de7b0a40f8f4da754656e23c900b773b",
    "0.1.24": "45d2e238afe1e0e8773954849e2ae53a742d7275f72d337ed4af7f64c36c2708",
}

# The harness/runtime/receipt/verifier and independent ledger implementation source files whose
# identity this experiment's claims depend on are embedded byte-for-byte into every bundle (see
# ``_embed_sources``) because git HEAD alone does not identify what executed: these files were
# untracked relative to the base SHA for the entire original run. The exact required list is
# ``REQUIRED_EMBEDDED_SOURCE_FILES``, imported from safeagent_ttl_receipt.py so the runner that
# writes it and the verifier that requires it share one definition.


class TrialExecutionError(RuntimeError):
    pass


def _parse_events(stdout: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if not line.startswith(PREFIX):
            continue
        try:
            events.append(cast(dict[str, Any], json.loads(line[len(PREFIX) :])))
        except json.JSONDecodeError:
            continue
    return events


def _find_event(events: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for e in reversed(events):
        if e.get("event") == name:
            return e
    return None


def _git_commit(root: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=5,
            check=True,
        )
        return proc.stdout.strip()
    except Exception:
        return "unknown"


def _release_info(python_bin: str, release: str) -> dict[str, object]:
    script = (
        "import hashlib, importlib.metadata, inspect, json\n"
        "import safeagent_exec_guard.sqlite_store as m\n"
        "src = inspect.getsourcefile(m.SQLiteExecutionStore)\n"
        "data = open(src, 'rb').read()\n"
        "print(json.dumps({\n"
        "    'version': importlib.metadata.version('safeagent-exec-guard'),\n"
        "    'module_version_attr': getattr(\n"
        "        __import__('safeagent_exec_guard'), '__version__', None),\n"
        "    'sqlite_store_module_file': src,\n"
        "    'sqlite_store_sha256': hashlib.sha256(data).hexdigest(),\n"
        "    'has_count_stale_pending': hasattr(m.SQLiteExecutionStore, 'count_stale_pending'),\n"
        "}))\n"
    )
    proc = subprocess.run(
        [python_bin, "-c", script], capture_output=True, text=True, timeout=15, check=True
    )
    info = cast(dict[str, object], json.loads(proc.stdout.strip()))
    installed_version = info.get("version")
    if installed_version != release:
        raise RuntimeError(
            f"installed safeagent-exec-guard version mismatch in the {release!r} venv: "
            f"importlib.metadata.version() reports {installed_version!r}. Aborting rather than "
            "measuring the wrong release."
        )
    info["wheel_sha256"] = _WHEEL_SHA256[release]
    return info


# --------------------------------------------------------------------------------------------
# A locally bounded ledger-daemon startup. ``LedgerDaemon.__enter__`` (ledger_process.py, shared
# by every other experiment in this repo and deliberately left unmodified here) waits for
# "LEDGER_READY" with a blocking ``readline()`` inside a nominal ``while time.monotonic() <
# deadline`` loop - if the daemon never writes a line, the blocking call never returns control to
# the loop that is supposed to bound it. This experiment needs a genuinely bounded startup, so it
# uses its own ``selectors``-based wait (the same pattern as ``wait_for_barrier`` below) instead
# of changing shared infrastructure other experiments depend on.
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


# --------------------------------------------------------------------------------------------
# Role invocations: each spawns a subprocess in the release's isolated venv, parses its
# structured stdout, and returns the one event the caller needs plus the raw stdout/stderr text
# for evidence retention. A role that emits its expected event but still exits nonzero is treated
# as a hard failure, not a benign result with a discarded exit code.
# --------------------------------------------------------------------------------------------


def _run_role(
    python_bin: str, args: list[str], *, expect_event: str, timeout: float = _ROLE_TIMEOUT
) -> tuple[dict[str, Any], str, str, int]:
    proc = subprocess.run(
        [python_bin, "-m", "crashpoint.harness.safeagent_ttl_runtime", *args],
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    events = _parse_events(proc.stdout)
    evt = _find_event(events, expect_event)
    if evt is None:
        raise TrialExecutionError(
            f"role {args[0]!r} did not emit {expect_event!r}: exit={proc.returncode} "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
    if proc.returncode != 0:
        raise TrialExecutionError(
            f"role {args[0]!r} emitted {expect_event!r} but exited {proc.returncode}: "
            f"stderr={proc.stderr!r}"
        )
    return evt, proc.stdout, proc.stderr, proc.returncode


def _inspect(
    python_bin: str, db_path: Path, action_id: str, snapshot_out: Path | None = None
) -> dict[str, Any]:
    args = ["inspect", "--db", str(db_path), "--action-id", action_id]
    if snapshot_out is not None:
        args += ["--snapshot-out", str(snapshot_out)]
    evt, _, _, _ = _run_role(python_bin, args, expect_event="inspect_result")
    return evt


def _sweep(python_bin: str, db_path: Path, ttl: float) -> dict[str, Any]:
    evt, _, _, _ = _run_role(
        python_bin, ["sweep", "--db", str(db_path), "--ttl", str(ttl)],
        expect_event="sweep_result",
    )
    return evt


def _retry(
    python_bin: str, invoke: str, db_path: Path, ttl: float, action_id: str
) -> tuple[dict[str, Any], str, str]:
    evt, out, err, _ = _run_role(
        python_bin,
        ["retry", "--invoke", invoke, "--db", str(db_path), "--ttl", str(ttl),
         "--action-id", action_id],
        expect_event="retry_complete",
    )
    return evt, out, err


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
    per process, so one stubborn process cannot prevent reaping the rest. Returns problems, if
    any, for a caller that wants to note them - never raises."""
    problems: list[str] = []
    for proc in owned:
        if proc.poll() is not None:
            continue
        try:
            os.kill(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
        except Exception as exc:  # best-effort cleanup, must not mask the caller's own exception
            problems.append(f"failed to signal owned pid {proc.pid}: {exc!r}")
            continue
        try:
            proc.wait(timeout=5)
        except Exception as exc:
            problems.append(f"owned pid {proc.pid} did not reap within 5s of SIGKILL: {exc!r}")
    return problems


def wait_for_barrier(
    proc: subprocess.Popen[str], expected_point: str, timeout: float
) -> tuple[list[dict[str, Any]], str | None]:
    """Bounded read of Worker A's stdout via ``selectors`` - never a blocking readline inside a
    nominal timeout loop. Returns every parsed event seen and, on success, None as the error."""
    assert proc.stdout is not None
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    buf = ""
    events: list[dict[str, Any]] = []
    try:
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            for _key, _mask in sel.select(timeout=max(0.0, min(remaining, 0.2))):
                chunk = os.read(proc.stdout.fileno(), 4096)
                if not chunk:
                    return events, "worker stdout closed before the expected barrier was observed"
                buf += chunk.decode("utf-8", errors="replace")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    if not line.startswith(PREFIX):
                        continue
                    try:
                        ev = json.loads(line[len(PREFIX) :])
                    except json.JSONDecodeError:
                        continue
                    events.append(ev)
                    if ev.get("event") == "barrier" and ev.get("point") == expected_point:
                        return events, None
                    if ev.get("event") == "fatal":
                        return events, f"worker reported fatal: {ev.get('reason')}"
            if proc.poll() is not None:
                return events, f"worker exited (code={proc.returncode}) before the barrier"
    finally:
        with contextlib.suppress(KeyError, ValueError):
            sel.unregister(proc.stdout)
    return events, "timed out waiting for the expected barrier"


def _verify_before_ttl(claimed_at: float | None, ttl: float) -> tuple[bool, float, str | None]:
    if claimed_at is None:
        return False, 0.0, "no claimed_at available to check the TTL margin"
    age = time.time() - claimed_at
    if age >= ttl - _TTL_MARGIN_BEFORE:
        return False, age, (
            f"insufficient margin before TTL expiry: age={age:.3f}s ttl={ttl}s "
            f"margin={_TTL_MARGIN_BEFORE}s - a before-TTL case that crosses expiry is INVALID"
        )
    return True, age, None


def _wait_past_ttl(claimed_at: float | None, ttl: float) -> tuple[bool, float, str | None]:
    """Bounded by an independent ``time.monotonic()`` deadline, computed once up front and never
    derived from ``cutoff`` (a ``time.time()``-based value) - so the bound is reachable even if
    the wall clock itself misbehaves, instead of a deadline that can never fire because it is
    provably always later than the condition that already returns first."""
    if claimed_at is None:
        return False, 0.0, "no claimed_at available to derive TTL expiry"
    cutoff = claimed_at + ttl + _TTL_MARGIN_PAST
    monotonic_deadline = time.monotonic() + ttl + _TTL_MARGIN_PAST + _MAX_EXTRA_WAIT
    while True:
        now = time.time()
        if now >= cutoff:
            return True, now - claimed_at, None
        if time.monotonic() >= monotonic_deadline:
            return False, now - claimed_at, (
                "exceeded the bounded wait (independent monotonic deadline) before crossing "
                "TTL+margin"
            )
        time.sleep(min(0.05, max(0.0, cutoff - now)))


# --------------------------------------------------------------------------------------------
# Provenance: embed the actual executing source bytes into the bundle so "what code produced
# this" is self-contained and offline-checkable, never dependent on git HEAD (which predates
# these untracked files) or the original checkout's absolute paths.
# --------------------------------------------------------------------------------------------


def _embed_sources(bundle_root: Path, release_info: dict[str, dict[str, object]]) -> dict[str, Any]:
    """Embed the required source set for THIS run: every entry of
    ``REQUIRED_EMBEDDED_SOURCE_FILES`` that is not a per-release SafeAgent source, plus the
    per-release sqlite_store.py for exactly the releases present in ``release_info`` - both
    releases for the real production batch (matching the full constant exactly), fewer only for
    a deliberately small test-only run (see run()'s cases/releases/k_per_cell parameters). Raises
    rather than writing an incomplete bundle if any required source cannot be located."""
    sources_dir = bundle_root / "sources"
    harness_dir = Path(__file__).resolve().parent
    ledger_dir = Path(ledger_package.__file__).resolve().parent
    canonical_path = Path(canonical_module.__file__).resolve()

    def _source_path(rel: str) -> Path:
        if rel.startswith("harness/"):
            return harness_dir / rel.removeprefix("harness/")
        if rel.startswith("ledger/"):
            return ledger_dir / rel.removeprefix("ledger/")
        if rel == "canonical.py":
            return canonical_path
        if rel.startswith("safeagent_exec_guard/"):
            _prefix, release, _name = rel.split("/", 2)
            module_file = release_info.get(release, {}).get("sqlite_store_module_file")
            if not isinstance(module_file, str) or not module_file:
                raise RuntimeError(
                    f"cannot locate required embedded source {rel!r}: no "
                    f"sqlite_store_module_file recorded for release {release!r}"
                )
            return Path(module_file)
        raise RuntimeError(f"unrecognized required embedded source path: {rel!r}")

    required_for_this_run = tuple(
        rel for rel in REQUIRED_EMBEDDED_SOURCE_FILES
        if not rel.startswith("safeagent_exec_guard/") or rel.split("/")[1] in release_info
    )

    embedded: dict[str, str] = {}
    for rel in required_for_this_run:
        src = _source_path(rel)
        if not src.is_file():
            raise RuntimeError(f"required embedded source {rel!r} not found at {src}")
        dest = sources_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = src.read_bytes()
        dest.write_bytes(data)
        embedded[rel] = hashlib.sha256(data).hexdigest()

    if set(embedded) != set(required_for_this_run):
        raise RuntimeError(  # pragma: no cover - defensive; the loop above already covers this
            "embedded source set does not match the required set for this run"
        )

    prediction_bytes = _PREDICTION_PATH.read_bytes()
    (bundle_root / "prediction.json").write_bytes(prediction_bytes)

    return {
        "embedded_source_sha256": embedded,
        "prediction_embedded_sha256": hashlib.sha256(prediction_bytes).hexdigest(),
    }


# --------------------------------------------------------------------------------------------
# One trial. ``_run_trial_body`` holds the actual protocol; ``run_trial`` wraps it with owned-
# process tracking and guaranteed cleanup/partial-evidence retention.
# --------------------------------------------------------------------------------------------


def _initial_trial_state(
    case: str, release: str, trial_id: str, action_id: str, ttl: float,
    release_info: dict[str, object],
) -> dict[str, Any]:
    """Every SafeAgentTTLTrial field this trial could ever report, defaulted to "nothing known
    yet" BEFORE any risky operation runs. Mutated in place as the trial genuinely progresses, so
    a failure at any point can be finalized from whatever state was actually reached - never a
    fixed "everything failed" fiction, and never silently missing a field a later stage would
    have set."""
    return {
        "case": case, "release": release, "trial_id": trial_id, "action_id": action_id,
        "pending_ttl_seconds": ttl,
        "safeagent_version": cast(str, release_info.get("version", "unknown")),
        "safeagent_dist_sha256": cast(str, release_info.get("wheel_sha256", "0" * 64)),
        "safeagent_sqlite_store_sha256": cast(
            str, release_info.get("sqlite_store_sha256", "0" * 64)
        ),
        "reset_confirmed": False,
        "worker_a_pid": None, "worker_a_killed": False, "worker_a_exit_status": None,
        "barrier_observed": False, "barrier_point": None,
        "sweep_invoked": False, "sweep_return_value": None,
        "has_count_stale_pending": None, "stale_count_before_sweep": None,
        "claimed_at": None, "measured_age_at_decision": None, "ttl_boundary_confirmed": False,
        "retry_claim_admitted": None, "retry_performed_effect": None,
        "effect_count": None, "effect_ledger_classification": "UNVERIFIED",
        "ledger_dump_count_before_seal": None, "final_claim_row": None,
    }


def _finalize_trial(
    state: dict[str, Any], *, observation_complete: bool, invalid_reason: str | None,
    expected_result: dict[str, object], crashpoint_commit: str,
) -> dict[str, object]:
    """The one path both a clean completion and a caught mid-trial failure use to become a
    receipt, so the two can never diverge in shape - only in whether observation_complete is
    True."""
    final_row = state["final_claim_row"]
    final_status = final_row.get("status") if isinstance(final_row, dict) else None
    label = derive_label(
        observation_complete=observation_complete,
        effect_count=state["effect_count"],
        retry_claim_admitted=state["retry_claim_admitted"],
        final_status=final_status if isinstance(final_status, str) else None,
    )
    if not observation_complete:
        state["effect_ledger_classification"] = "UNVERIFIED"
    observed_result: dict[str, object] = {
        "retry_claim_admitted": state["retry_claim_admitted"],
        "effect_count": state["effect_count"],
        "effect_ledger_classification": state["effect_ledger_classification"],
        "final_claim_status": final_status,
        "task_completion_label": label,
    }
    trial = SafeAgentTTLTrial(
        **state,
        observation_complete=observation_complete, invalid_reason=invalid_reason,
        expected_result=expected_result, observed_result=observed_result,
    )
    return build_receipt(trial, crashpoint_commit=crashpoint_commit)


def _run_trial_body(
    case: str,
    release: str,
    index: int,
    prediction: dict[str, Any],
    ledger: LedgerHandle,
    crashpoint_commit: str,
    trial_root: Path,
    python_bin: str,
    ttl: float,
    trial_id: str,
    action_id: str,
    owned_procs: list[subprocess.Popen[str]],
    state: dict[str, Any],
) -> dict[str, object]:
    db_path = trial_root / "claim.sqlite"

    def snap(filename: str) -> None:
        _inspect(python_bin, db_path, action_id, snapshot_out=trial_root / filename)

    invalid_reason: str | None = None

    def note_invalid(reason: str) -> None:
        nonlocal invalid_reason
        if invalid_reason is None:
            invalid_reason = reason

    obs: LedgerObservation | None = None

    state["reset_confirmed"] = True  # _reset_and_establish_baseline already succeeded to get here

    if case == "settled_control":
        proc = subprocess.run(
            [python_bin, "-m", "crashpoint.harness.safeagent_ttl_runtime", "claim-run",
             "--invoke", ledger.invoke_path, "--db", str(db_path), "--ttl", str(ttl),
             "--action-id", action_id, "--case", case],
            capture_output=True, text=True, timeout=_ROLE_TIMEOUT, check=False,
        )
        (trial_root / "worker_a.stdout").write_text(proc.stdout)
        (trial_root / "worker_a.stderr").write_text(proc.stderr)
        state["worker_a_exit_status"] = proc.returncode
        events = _parse_events(proc.stdout)
        started = _find_event(events, "worker_started")
        state["worker_a_pid"] = cast(int | None, started.get("pid")) if started else None
        if proc.returncode != 0 or _find_event(events, "settled") is None:
            note_invalid(
                f"worker A did not complete settled_control cleanly (exit={proc.returncode})"
            )
        else:
            # "barrier" here means "reached its terminal claim state" - settled_control never
            # blocks at an intermediate barrier the way the kill-based cases do.
            state["barrier_observed"] = True
            state["barrier_point"] = "settled"
        snap("claim_post_settle.sqlite")
    else:
        argv = [python_bin, "-m", "crashpoint.harness.safeagent_ttl_runtime", "claim-run",
                "--invoke", ledger.invoke_path, "--db", str(db_path), "--ttl", str(ttl),
                "--action-id", action_id, "--case", case]
        worker_proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        owned_procs.append(worker_proc)
        state["worker_a_pid"] = worker_proc.pid
        expected_point = (
            "post_claim_pre_effect" if case == "pre_effect_expired_swept" else "post_effect_pending"
        )
        events, err = wait_for_barrier(worker_proc, expected_point, _BARRIER_TIMEOUT)
        state["barrier_observed"] = err is None
        state["barrier_point"] = expected_point if state["barrier_observed"] else None
        if err is not None:
            note_invalid(f"barrier not observed: {err}")

        pre_kill_dump = ledger.dump()
        pre_kill_side_effects = pre_kill_dump.get("side_effects", {})
        pre_kill_count = (
            int(pre_kill_side_effects.get(action_id, 0))
            if isinstance(pre_kill_side_effects, dict) else 0
        )
        expected_pre_kill = 0 if case == "pre_effect_expired_swept" else 1
        if pre_kill_count != expected_pre_kill:
            note_invalid(
                f"pre-kill effect boundary mismatch: expected {expected_pre_kill} effect(s) "
                f"before kill, ledger shows {pre_kill_count}"
            )

        snap("claim_pre_kill.sqlite")

        killed, exit_status, kill_err = _kill_and_reap(worker_proc)
        state["worker_a_killed"] = killed
        state["worker_a_exit_status"] = exit_status
        if kill_err is not None:
            note_invalid(kill_err)

        remaining_out, remaining_err = "", ""
        with contextlib.suppress(Exception):
            # Best-effort drain of whatever the killed process still had buffered; it is
            # already dead either way, so a failure here changes nothing about the trial.
            remaining_out, remaining_err = worker_proc.communicate(timeout=5)
        (trial_root / "worker_a.stdout").write_text(
            "\n".join(json.dumps(e, sort_keys=True) for e in events) + "\n" + (remaining_out or "")
        )
        (trial_root / "worker_a.stderr").write_text(remaining_err or "")

        snap("claim_post_kill.sqlite")

        insp = _inspect(python_bin, db_path, action_id)
        row = insp.get("row")
        if not isinstance(row, dict) or row.get("claimed_at") is None:
            note_invalid("claim row (or its claimed_at) missing immediately after kill")
        else:
            state["claimed_at"] = float(cast(float, row["claimed_at"]))
        claimed_at = state["claimed_at"]

        if case == "pending_before_ttl":
            ok, measured_age, reason = _verify_before_ttl(claimed_at, ttl)
            state["ttl_boundary_confirmed"] = ok
            state["measured_age_at_decision"] = measured_age
            if not ok and reason:
                note_invalid(reason)
            state["sweep_invoked"] = True
            sweep_evt = _sweep(python_bin, db_path, ttl)
            state["sweep_return_value"] = cast(int | None, sweep_evt.get("sweep_return_value"))
            state["has_count_stale_pending"] = cast(
                bool | None, sweep_evt.get("has_count_stale_pending")
            )
            state["stale_count_before_sweep"] = cast(
                int | None, sweep_evt.get("stale_count_before_sweep")
            )
            ok2, measured_age2, reason2 = _verify_before_ttl(claimed_at, ttl)
            state["ttl_boundary_confirmed"] = bool(state["ttl_boundary_confirmed"]) and ok2
            state["measured_age_at_decision"] = measured_age2
            if not ok2 and reason2:
                note_invalid(f"crossed TTL margin during/after sweep: {reason2}")
            snap("claim_post_sweep.sqlite")
        elif case == "pending_expired_no_sweep":
            ok, measured_age, reason = _wait_past_ttl(claimed_at, ttl)
            state["ttl_boundary_confirmed"] = ok
            state["measured_age_at_decision"] = measured_age
            if not ok and reason:
                note_invalid(reason)
            state["sweep_invoked"] = False
        else:  # pending_expired_swept, pre_effect_expired_swept
            ok, measured_age, reason = _wait_past_ttl(claimed_at, ttl)
            state["ttl_boundary_confirmed"] = ok
            state["measured_age_at_decision"] = measured_age
            if not ok and reason:
                note_invalid(reason)
            snap("claim_pre_sweep.sqlite")
            state["sweep_invoked"] = True
            sweep_evt = _sweep(python_bin, db_path, ttl)
            state["sweep_return_value"] = cast(int | None, sweep_evt.get("sweep_return_value"))
            state["has_count_stale_pending"] = cast(
                bool | None, sweep_evt.get("has_count_stale_pending")
            )
            state["stale_count_before_sweep"] = cast(
                int | None, sweep_evt.get("stale_count_before_sweep")
            )
            snap("claim_post_sweep.sqlite")

    # settled_control also ages past its TTL and sweeps, per its own case description, even
    # though nothing was killed - sweep only ever touches PENDING rows, so this is expected to
    # be a harmless no-op cross-check, not a mutation of the settled row.
    if case == "settled_control":
        insp0 = _inspect(python_bin, db_path, action_id)
        row0 = insp0.get("row")
        if isinstance(row0, dict) and row0.get("claimed_at") is not None:
            state["claimed_at"] = float(cast(float, row0["claimed_at"]))
        ok, measured_age, reason = _wait_past_ttl(state["claimed_at"], ttl)
        state["ttl_boundary_confirmed"] = ok
        state["measured_age_at_decision"] = measured_age
        if not ok and reason:
            note_invalid(reason)
        state["sweep_invoked"] = True
        sweep_evt = _sweep(python_bin, db_path, ttl)
        state["sweep_return_value"] = cast(int | None, sweep_evt.get("sweep_return_value"))
        state["has_count_stale_pending"] = cast(
            bool | None, sweep_evt.get("has_count_stale_pending")
        )
        state["stale_count_before_sweep"] = cast(
            int | None, sweep_evt.get("stale_count_before_sweep")
        )
        snap("claim_post_sweep.sqlite")

    retry_evt, retry_out, retry_err = _retry(
        python_bin, ledger.invoke_path, db_path, ttl, action_id
    )
    (trial_root / "worker_b.stdout").write_text(retry_out)
    (trial_root / "worker_b.stderr").write_text(retry_err)
    state["retry_claim_admitted"] = cast(bool | None, retry_evt.get("admitted"))
    state["retry_performed_effect"] = cast(bool | None, retry_evt.get("performed_effect"))
    state["final_claim_row"] = cast(dict[str, object] | None, retry_evt.get("final_row"))

    snap("claim_final.sqlite")

    # Capture the daemon's own live self-report BEFORE sealing, purely as an independent
    # cross-check - never as license to synthesize a substitute for missing bytes. The raw-empty
    # baseline established at reset time means the store path should exist (possibly still 0
    # bytes) all the way through; its disappearance is always missing evidence, full stop.
    pre_seal_dump = ledger.dump()
    pre_seal_side_effects = pre_seal_dump.get("side_effects", {})
    state["ledger_dump_count_before_seal"] = cast(int | None, pre_seal_dump.get("count"))
    dump_effect_count = (
        int(pre_seal_side_effects.get(action_id, 0))
        if isinstance(pre_seal_side_effects, dict) else None
    )

    ledger.seal()
    raw_ledger_path = Path(ledger.store_path)
    trial_ledger_copy = trial_root / "ledger.jsonl"
    if raw_ledger_path.exists():
        # A genuine read of the actual current bytes - whether that is the untouched 0-byte
        # baseline (nothing was ever appended) or real recorded content. Never synthesized.
        trial_ledger_copy.write_bytes(raw_ledger_path.read_bytes())
    else:
        note_invalid(
            "ledger source file missing at observation time: a real baseline was established "
            "at reset (see _reset_and_establish_baseline); its disappearance is missing "
            "evidence regardless of what the daemon's own dump() reported"
        )
        # trial_ledger_copy deliberately left absent - the fresh observer below must see
        # MISSING, never a fabricated confirmed-empty file standing in for lost evidence.

    observer = subprocess.run(
        [sys.executable, "-m", "crashpoint.harness.safeagent_ttl_verify", "observe-ledger",
         "--path", str(trial_ledger_copy)],
        capture_output=True, text=True, timeout=_ROLE_TIMEOUT, check=False,
    )
    try:
        obs_json = cast(dict[str, Any], json.loads(observer.stdout))
    except json.JSONDecodeError as exc:
        raise TrialExecutionError(
            f"fresh ledger observer produced unparseable output: {observer.stdout!r} "
            f"stderr={observer.stderr!r}"
        ) from exc
    obs = LedgerObservation(
        status=obs_json["status"], raw_sha256=obs_json["raw_sha256"],
        byte_length=obs_json["byte_length"], broken_at_index=obs_json["broken_at_index"],
        attempts=obs_json["attempts"], side_effects=obs_json["side_effects"],
        effect_digests=obs_json["effect_digests"], effect_keys=obs_json["effect_keys"],
        attempt_ids_by_intent=obs_json["attempt_ids_by_intent"],
    )
    if observer.returncode not in (0, 1):
        # 0 (OK/EMPTY_CONFIRMED) and 1 (MISSING/TAMPERED, per safeagent_ttl_verify.main's own
        # contract) are the only codes that module ever returns; anything else means the
        # observer itself crashed or was killed, and its output cannot be trusted even if it
        # happens to parse as JSON.
        note_invalid(f"ledger observer exited with an unexpected code {observer.returncode}")
        obs = LedgerObservation(
            status="UNVERIFIED", raw_sha256=obs.raw_sha256, byte_length=obs.byte_length,
            broken_at_index=obs.broken_at_index,
        )
    elif observer.returncode == 1 and obs.status not in {"MISSING", "TAMPERED"}:
        note_invalid(
            f"ledger observer exited 1 (failure) but reported status={obs.status!r}: "
            "exit code and reported status disagree"
        )

    if obs.status in {"OK", "EMPTY_CONFIRMED"} and observer.returncode == 0:
        state["effect_count"] = obs.side_effects.get(action_id, 0)
        state["effect_ledger_classification"] = classify_effect_count(action_id, obs)
        if dump_effect_count is not None and dump_effect_count != state["effect_count"]:
            note_invalid(
                f"effect_count contradiction: fresh ledger observer reports "
                f"{state['effect_count']}, pre-seal daemon dump reported {dump_effect_count}"
            )
    else:
        state["effect_count"] = None
        state["effect_ledger_classification"] = "UNVERIFIED"
        note_invalid(f"independent ledger observation failed: status={obs.status}")

    observation_complete = (
        invalid_reason is None
        and bool(state["barrier_observed"])
        and (bool(state["worker_a_killed"]) or case == "settled_control")
        and state["effect_count"] is not None
        and state["final_claim_row"] is not None
    )
    if not observation_complete and invalid_reason is None:
        # Every other branch above already names its own reason; this is a fail-closed backstop
        # for a combination this module's own author did not anticipate. effect_count is
        # deliberately left as whatever was actually captured (possibly a genuine non-null
        # value) rather than nulled here - an invalid trial still retains what it observed.
        note_invalid("observation incomplete for an unlisted reason")

    expected_result = cast(dict[str, object], prediction["cases"][case][release])
    rec = _finalize_trial(
        state, observation_complete=observation_complete, invalid_reason=invalid_reason,
        expected_result=expected_result, crashpoint_commit=crashpoint_commit,
    )
    (trial_root / "receipt.json").write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n")
    (trial_root / "ledger_observation.json").write_text(
        json.dumps(obs.to_json(), indent=2, sort_keys=True) + "\n"
    )
    return rec


def _write_failure_record(
    state: dict[str, Any], trial_root: Path, reason: str, crashpoint_commit: str,
    prediction: dict[str, Any],
) -> dict[str, object]:
    """The durable record for a trial that raised partway through: built from whatever ``state``
    genuinely accumulated before the failure (not a fixed "everything failed" fiction - a
    failure after a successful reset still reports reset_confirmed=True, a failure after the
    kill still reports the real worker_a_killed/exit_status, and so on), and always a
    schema-valid receipt, never a bare exception with no trace. A write failure here is caught
    and swallowed deliberately: cleanup must not depend on this succeeding."""
    expected_result = cast(
        dict[str, object], prediction["cases"][state["case"]][state["release"]]
    )
    rec = _finalize_trial(
        state, observation_complete=False, invalid_reason=reason,
        expected_result=expected_result, crashpoint_commit=crashpoint_commit,
    )
    with contextlib.suppress(Exception):
        (trial_root / "receipt.json").write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n")
    return rec


def _reset_and_establish_baseline(ledger: LedgerHandle) -> None:
    """Reset the ledger, require a strictly verified empty result (an int 0, never a truthy
    approximation or a missing/None count), then create and verify a REAL, on-disk, zero-byte
    ledger file at the store path before any worker can append to it. This is the raw-empty
    baseline the rest of the trial's observation is checked against - never a same-shaped stand-in
    written after the fact. Raises rather than proceeding on anything less than full confirmation;
    the caller is responsible for retaining this as a trial failure, not swallowing it."""
    ledger.reset()  # LedgerHandle.reset() returns None by design; verified below via dump()
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
            "after a verified reset (expected the daemon's reset to have removed it)"
        ) from exc
    stat = store_path.stat()
    if stat.st_size != 0:
        raise RuntimeError(
            f"raw-empty baseline at {store_path} is not genuinely 0 bytes: {stat.st_size}"
        )


def run_trial(
    case: str,
    release: str,
    index: int,
    prediction: dict[str, Any],
    ledger: LedgerHandle,
    crashpoint_commit: str,
    trial_root: Path,
    release_info: dict[str, object],
) -> dict[str, object]:
    trial_id = f"{case}-{release}-{index}"
    action_id = f"safeagent-ttl-{trial_id}"
    python_bin = VENV_PYTHON[release]
    ttl = TTL_BEFORE_CONTROL_SECONDS if case == "pending_before_ttl" else TTL_EXPIRED_SECONDS
    trial_root.mkdir(parents=True, exist_ok=False)  # "trial allocation"

    # Initialized before reset, not after: a reset failure (or anything later) must still be
    # able to report whatever was genuinely known at that point, never a fixed stub that claims
    # reset never happened when it may well have succeeded.
    state = _initial_trial_state(case, release, trial_id, action_id, ttl, release_info)

    owned_procs: list[subprocess.Popen[str]] = []
    try:
        _reset_and_establish_baseline(ledger)  # raises on anything less than full confirmation
        return _run_trial_body(
            case, release, index, prediction, ledger, crashpoint_commit, trial_root,
            python_bin, ttl, trial_id, action_id, owned_procs, state,
        )
    except Exception as exc:
        reap_problems = _reap_owned(owned_procs)
        note = f"unhandled exception during trial: {exc!r}"
        if reap_problems:
            note += f"; cleanup problems: {reap_problems}"
        with contextlib.suppress(Exception):
            exc.add_note(
                f"safeagent_ttl.run_trial: {note} (owned processes reaped: "
                f"{len(owned_procs) - len(reap_problems)}/{len(owned_procs)})"
            )
        # Best-effort: whatever the ledger daemon has recorded so far survives this trial's
        # failure (it is a separate, longer-lived process the trial does not own), so retain it
        # too - a failed trial should not lose real, already-captured effect evidence.
        with contextlib.suppress(Exception):
            (trial_root / "ledger_dump_after_failure.json").write_text(
                json.dumps(ledger.dump(), indent=2, sort_keys=True, default=str) + "\n"
            )
        with contextlib.suppress(Exception):
            raw_path = Path(ledger.store_path)
            if raw_path.exists():
                (trial_root / "ledger.jsonl").write_bytes(raw_path.read_bytes())
        _write_failure_record(state, trial_root, note, crashpoint_commit, prediction)
        raise
    finally:
        _reap_owned(owned_procs)


# --------------------------------------------------------------------------------------------
# The full 5 x 2 x 3 confirmatory batch.
# --------------------------------------------------------------------------------------------


def run(
    name: str = "safeagent_ttl",
    out_root: Path | None = None,
    *,
    cases: tuple[str, ...] = CASES,
    releases: tuple[str, ...] = RELEASES,
    k_per_cell: int = _K_PER_CELL,
) -> dict[str, Any]:
    """The full confirmatory batch by default (every case, every release, ``_K_PER_CELL`` each).
    ``cases``/``releases``/``k_per_cell`` exist so tests can exercise run()'s own aggregation -
    execution_failures accounting, status/all_agree derivation, and incremental manifest writes -
    on a small grid instead of only ever calling run_trial() directly, which tests nothing about
    run() itself. Production use (main()) always takes the defaults."""
    prediction = json.loads(_PREDICTION_PATH.read_text())
    prediction_sha256 = hashlib.sha256(_PREDICTION_PATH.read_bytes()).hexdigest()
    crashpoint_commit = _git_commit(_ROOT)
    bundle_root = (out_root or (_ROOT / "evidence" / "safeagent_ttl")) / name
    if bundle_root.exists():
        raise FileExistsError(
            f"evidence bundle already exists, refusing to overwrite: {bundle_root}"
        )
    trials_dir = bundle_root / "trials"
    trials_dir.mkdir(parents=True)

    release_info = {r: _release_info(VENV_PYTHON[r], r) for r in releases}
    embedded = _embed_sources(bundle_root, release_info)

    receipts: list[dict[str, Any]] = []
    trial_dirs: dict[str, str] = {}
    execution_failures: list[dict[str, object]] = []

    def _write_progress(status: str) -> dict[str, Any]:
        manifest = _build_manifest(
            name, status, crashpoint_commit, prediction_sha256, release_info, embedded,
            trial_dirs, receipts, execution_failures, cases, releases, k_per_cell,
        )
        (bundle_root / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        return manifest

    _write_progress("IN_PROGRESS")

    with (
        tempfile.TemporaryDirectory() as ledger_tmp,
        _BoundedLedgerDaemon(Path(ledger_tmp) / "ledger") as ledger,
    ):
        for case in cases:
            for release in releases:
                for i in range(k_per_cell):
                    trial_id = f"{case}-{release}-{i}"
                    trial_root = trials_dir / trial_id
                    try:
                        rec = run_trial(
                            case, release, i, prediction, ledger, crashpoint_commit, trial_root,
                            release_info[release],
                        )
                        receipts.append(rec)
                    except Exception as exc:
                        execution_failures.append(
                            {"trial_id": trial_id, "case": case, "release": release,
                             "index": i, "error": repr(exc)}
                        )
                    trial_dirs[trial_id] = f"trials/{trial_id}"
                    _write_progress("IN_PROGRESS")

    for rec in receipts:
        problems = validate_receipt(rec)
        if problems:
            raise RuntimeError(f"invalid receipt for trial {rec.get('trial_id')!r}: {problems}")

    expected_trial_count = len(cases) * len(releases) * k_per_cell
    status = (
        "COMPLETE"
        if not execution_failures and len(receipts) == expected_trial_count
        else "INCOMPLETE"
    )
    manifest = _write_progress(status)
    return manifest


def _build_manifest(
    name: str, status: str, crashpoint_commit: str, prediction_sha256: str,
    release_info: dict[str, dict[str, object]], embedded: dict[str, Any],
    trial_dirs: dict[str, str], receipts: list[dict[str, Any]],
    execution_failures: list[dict[str, object]],
    cases: tuple[str, ...] = CASES, releases: tuple[str, ...] = RELEASES,
    k_per_cell: int = _K_PER_CELL,
) -> dict[str, Any]:
    cells_summary: dict[str, Any] = {}
    for case in cases:
        for release in releases:
            key = f"{case}|{release}"
            cell = [r for r in receipts if r["case"] == case and r["release"] == release]
            cells_summary[key] = {
                "case": case,
                "release": release,
                "count": len(cell),
                "passing": sum(1 for r in cell if r["passed"]),
                "labels": dict(Counter(str(r["task_completion_label"]) for r in cell)),
                "effect_counts": [r["effect_count"] for r in cell],
            }

    manifest: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "name": name,
        "status": status,
        "experiment_family": "safeagent_ttl_sweep_boundary",
        "framework_fix": True,
        "claim": (
            "In safeagent-exec-guard 0.1.23, SQLiteExecutionStore.sweep_stale_pending() deletes "
            "a stale PENDING claim and a fresh client can re-claim and re-perform the logical "
            "action; the independent ledger records two effects when the original attempt had "
            "already performed its effect before dying. In 0.1.24, sweep_stale_pending() is an "
            "unconditional no-op (always returns 0, deletes nothing) even though the new "
            "count_stale_pending() correctly reports the row as stale; a stale PENDING claim is "
            "never released through this API, so the retry is always denied in the tested cases. "
            "That prevents the 0.1.23 duplication in the cases measured here; SafeAgent's own "
            "0.1.24 docstring states this is deliberate fail-closed behavior pending caller-side "
            "reconciliation, which this experiment does not implement or evaluate."
        ),
        "base_sha": _BASE_SHA,
        "crashpoint_commit": crashpoint_commit,
        "prediction_path": "results/12-safeagent-ttl-prediction.json",
        "prediction_sha256": prediction_sha256,
        "prediction_embedded_path": "prediction.json",
        "prediction_embedded_sha256": embedded.get("prediction_embedded_sha256"),
        "embedded_source_sha256": embedded.get("embedded_source_sha256"),
        "release_info": release_info,
        "cases": list(cases),
        "releases": list(releases),
        "trials_per_cell": k_per_cell,
        "trial_count": len(receipts),
        "expected_trial_count": len(cases) * len(releases) * k_per_cell,
        "execution_failures": execution_failures,
        "pending_ttl_seconds": {
            "default": TTL_EXPIRED_SECONDS, "pending_before_ttl": TTL_BEFORE_CONTROL_SECONDS,
        },
        "trial_dirs": trial_dirs,
        "trials": receipts,
        "cells_summary": cells_summary,
        "all_agree": (
            bool(receipts)
            and not execution_failures
            and all(bool(r["passed"]) for r in receipts)
        ),
        "limitations": LIMITATIONS,
    }
    manifest["receipt"] = receipt(manifest)
    return manifest


def render(manifest: dict[str, Any]) -> str:
    lines = [
        f"SafeAgent TTL evidence - {manifest['trial_count']}/{manifest['expected_trial_count']} "
        f"trials ({manifest['trials_per_cell']} per cell x {len(manifest['cases'])} cases x "
        f"{len(manifest['releases'])} releases), status={manifest['status']}",
        f"all trials agree with the pre-registered prediction: {manifest['all_agree']}",
    ]
    if manifest["execution_failures"]:
        lines.append(f"EXECUTION FAILURES (not silently dropped): {manifest['execution_failures']}")
    for case in cast(list[str], manifest["cases"]):
        for release in cast(list[str], manifest["releases"]):
            c = manifest["cells_summary"][f"{case}|{release}"]
            lines.append(
                f"{case} [{release}]: {c['passing']}/{c['count']} pass; labels={c['labels']} "
                f"effects={c['effect_counts']}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="safeagent_ttl")
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    manifest = run(args.name, out_root=args.out_dir)
    print(render(manifest))
    print(f"\nreceipt: {manifest['receipt']}")
    bundle_root = (args.out_dir or (_ROOT / "evidence" / "safeagent_ttl")) / args.name
    print(f"wrote {bundle_root / 'manifest.json'}")
    return 0 if manifest["status"] == "COMPLETE" and manifest["all_agree"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
