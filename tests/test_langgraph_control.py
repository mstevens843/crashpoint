from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

pytest.importorskip("langgraph")

from crashpoint.harness import langgraph_control as lc
from crashpoint.harness import langgraph_control_observer as observer_mod
from crashpoint.harness.langgraph_admission import initialize_admission_ledger
from crashpoint.harness.langgraph_control_receipt import REQUIRED_EXECUTING_SOURCE_MODULES
from crashpoint.harness.langgraph_control_verify import verify_bundle


def _section(record: dict[str, object], key: str) -> dict[str, Any]:
    return cast(dict[str, Any], record[key])


@pytest.fixture(scope="module")
def primary_bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One real non-crash LangGraph execution, run once and reused (read-only) by every test in
    this module that only needs a valid bundle to inspect - the small real executions the
    assignment permits for focused regression tests, not a benchmark-sized campaign."""
    out = tmp_path_factory.mktemp("primary") / "bundle"
    lc.run_primary_control(out, name="test_primary")
    return out


def test_real_execution_produces_one_matching_effect_and_verifies_offline(
    primary_bundle: Path,
) -> None:
    record = json.loads((primary_bundle / "receipt.json").read_text())

    assert record["runtime"]["crash_injected"] is False
    assert record["runtime"]["worker_ok"] is True
    assert record["runtime"]["worker_returned_state"] == {"done": True}
    assert record["observer"]["status"] == "OBSERVED"
    assert record["observer"]["chain_valid"] is True
    assert record["observer"]["archive_matches_original"] is True
    assert record["effect"]["observed_count"] == 1
    assert record["checkpoint"]["thread_scoped_checkpoint_count"] > 0
    assert record["admission"]["status_after_run"] == "completed"
    assert record["result"]["oracle_classification"] == "EXACTLY_ONCE"
    assert record["result"]["passed"] is True

    problems = verify_bundle(primary_bundle)
    assert problems == []


def test_admission_identity_persisted_before_invoke_and_bound_to_effect(
    primary_bundle: Path,
) -> None:
    record = json.loads((primary_bundle / "receipt.json").read_text())
    thread_id = record["identity"]["thread_id"]
    intent_id = record["identity"]["intent_id"]
    assert record["identity"]["admission_id"] == thread_id == intent_id

    # The accepted-run row and its 'accepted' event exist independently of receipt.json's claims.
    with sqlite3.connect(primary_bundle / "admission.sqlite") as conn:
        row = conn.execute(
            "select input_json from accepted_runs where thread_id = ?", (thread_id,)
        ).fetchone()
        events = [
            r[0]
            for r in conn.execute(
                "select event from admission_events where thread_id = ? order by sequence",
                (thread_id,),
            ).fetchall()
        ]
    assert row is not None
    assert events == ["accepted", "completed"]

    # The effect actually recorded in the ledger readback is keyed by that same intent_id.
    raw = (primary_bundle / "ledger-readback.jsonl").read_bytes()
    assert intent_id.encode() in raw


def test_separate_runs_have_distinct_identities(tmp_path: Path) -> None:
    first = lc.run_primary_control(
        tmp_path / "run-a", name="distinct_a", worker_timeout=20, observer_timeout=10
    )
    second = lc.run_primary_control(
        tmp_path / "run-b", name="distinct_b", worker_timeout=20, observer_timeout=10
    )
    first_identity, second_identity = _section(first, "identity"), _section(second, "identity")
    assert first_identity["admission_id"] != second_identity["admission_id"]
    assert first_identity["thread_id"] != second_identity["thread_id"]


def test_existing_output_directory_is_refused_before_any_side_effect(primary_bundle: Path) -> None:
    before = sorted(p.name for p in primary_bundle.iterdir())
    with pytest.raises(FileExistsError, match="already exists"):
        lc.run_primary_control(primary_bundle)
    # refused before touching anything: the directory's contents are byte-for-byte untouched
    assert sorted(p.name for p in primary_bundle.iterdir()) == before


def test_no_owned_processes_remain_after_a_run(tmp_path: Path) -> None:
    lc.run_primary_control(tmp_path / "cleanup-check", worker_timeout=20, observer_timeout=10)
    # the ledger daemon and any worker/observer subprocess are all gone once the call returns
    leftover = subprocess.run(
        ["pgrep", "-f", "crashpoint.ledger.daemon"], capture_output=True, text=True, check=False
    )
    assert leftover.returncode != 0, f"a ledger daemon is still running: {leftover.stdout!r}"


def test_worker_timeout_still_retains_partial_evidence_and_cannot_pass(tmp_path: Path) -> None:
    out = tmp_path / "timeout-run"
    record = lc.run_primary_control(out, worker_timeout=0.001, observer_timeout=10)

    # Runtime failure (here: the worker never got a chance to run) still yields a full receipt,
    # never a crash, and partial evidence written before the failure survives.
    assert (out / "contract.json").is_file()
    assert (out / "manifest.json").is_file()
    assert (out / "admission.sqlite").is_file()
    runtime, result, admission = (
        _section(record, "runtime"), _section(record, "result"), _section(record, "admission")
    )
    assert runtime["worker_ok"] is False
    assert runtime["worker_error_type"] == "TimeoutExpired"
    assert result["oracle_classification"] == "UNVERIFIED"
    assert result["passed"] is False
    # admission was accepted before dispatch but never reaches "completed" if the worker never ran
    assert admission["status_after_run"] == "accepted"

    problems = verify_bundle(out)
    assert problems == []  # internally consistent: correctly and honestly reports a failed run


def test_worker_reports_admission_not_found_when_admission_row_is_missing(tmp_path: Path) -> None:
    admission_db = tmp_path / "admission.sqlite"
    checkpoint_db = tmp_path / "checkpoint.sqlite"
    initialize_admission_ledger(admission_db)  # created, but nothing accepted into it

    bogus_invoke_path = "/nonexistent/should/not/be/used.sock"
    proc = subprocess.run(
        [
            sys.executable, "-m", "crashpoint.harness.langgraph_control", "--worker",
            "--checkpoint", str(checkpoint_db), "--effect-ledger", bogus_invoke_path,
            "--admission-db", str(admission_db),
            "--thread-id", "missing-thread", "--intent", "missing-thread",
        ],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert proc.returncode == 0  # the worker still exits 0: it ran and reported a failure
    report = json.loads(proc.stdout.strip().splitlines()[-1])
    assert report["ok"] is False
    assert report["error_type"] == "AdmissionNotFound"


def test_required_source_modules_are_retained_and_hashed(primary_bundle: Path) -> None:
    record = json.loads((primary_bundle / "receipt.json").read_text())
    exec_files = {f["path"]: f["sha256"] for f in record["source"]["executing_source_files"]}
    assert set(exec_files) == set(REQUIRED_EXECUTING_SOURCE_MODULES)
    for rel_path, declared_sha256 in exec_files.items():
        basename = Path(rel_path).name
        retained = primary_bundle / "source" / basename
        assert retained.is_file(), f"retained copy missing for {basename}"
        import hashlib

        assert hashlib.sha256(retained.read_bytes()).hexdigest() == declared_sha256


def test_executing_source_files_recorded_regardless_of_git_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The publication review found the old git-status-derived source list would silently come
    back empty on a clean (fully committed) checkout. The fixed list must be hashed unconditionally
    - simulate a clean `git status --porcelain` (as a real commit would produce, without actually
    committing anything) and confirm all five required modules are still recorded."""
    real_git = lc._git

    def fake_git(root: Path, *args: str) -> str:
        if args[:2] == ("status", "--porcelain=v1"):
            return ""  # simulates a fully committed, clean working tree
        return real_git(root, *args)

    monkeypatch.setattr(lc, "_git", fake_git)
    manifest = lc._build_manifest(lc._ROOT, tmp_path / "contract.json", "0" * 64)
    assert manifest["dirty"] is False
    exec_files = cast("list[dict[str, str]]", manifest["executing_source_files"])
    exec_paths = {f["path"] for f in exec_files}
    assert exec_paths == set(REQUIRED_EXECUTING_SOURCE_MODULES)


def test_checkpoint_table_missing_is_reported_not_raised(tmp_path: Path) -> None:
    """Reproduces the publication review's disk-pressure finding: a checkpoint database that
    exists (the worker opened a connection) but was never set up (died before
    SqliteSaver.setup()) must not crash checkpoint counting."""
    db = tmp_path / "malformed-checkpoint.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("create table dummy(x)")
        conn.commit()

    count, error = lc._safe_thread_checkpoint_count(db, "some-thread")
    assert count is None
    assert error is not None
    assert "checkpoints" in error


def test_observer_internal_exception_produces_observer_error_report(tmp_path: Path) -> None:
    """Deterministic reproduction of an observer-side failure (no timing dependency): the archive
    destination's parent is blocked by a plain file, so observe() raises partway through, and
    main()'s own exception handler must turn that into an OBSERVER_ERROR report and exit 2 - never
    a raw traceback."""
    store = tmp_path / "store.jsonl"
    store.write_text(
        '{"i": 0, "prev": "crashpoint-ledger-genesis-cp1", "record": {"op": "noop"}, '
        '"hash": "irrelevant"}\n'
    )
    blocker = tmp_path / "blocker-file"
    blocker.write_text("not a directory")
    archive_to = blocker / "sub" / "archive.jsonl"
    report_out = tmp_path / "observer-report.json"

    rc = observer_mod.main(
        [
            "--ledger-store", str(store), "--intent", "some-intent",
            "--archive-to", str(archive_to), "--report-out", str(report_out),
        ]
    )
    assert rc == 2
    report = json.loads(report_out.read_text())
    assert report["status"] == "OBSERVER_ERROR"
    assert report["error"]


def test_observer_timeout_yields_honest_void_receipt_with_parent_fallback_archive(
    tmp_path: Path,
) -> None:
    """The worker completes and writes one real effect; the observer is then given an impossibly
    short timeout so it cannot run at all. The harness must not crash, must classify VOID (never a
    false PASS and never a false LOST/zero), and must preserve the ledger bytes via a clearly
    labeled parent-process fallback archive since the observer never got to make its own."""
    out = tmp_path / "observer-timeout-run"
    record = lc.run_primary_control(out, worker_timeout=20, observer_timeout=0.001)

    assert _section(record, "runtime")["worker_ok"] is True  # the worker itself succeeded
    observer = _section(record, "observer")
    assert observer["status"] == "OBSERVER_ERROR"
    assert observer["report_path"] is None
    assert observer["report_sha256"] is None
    assert observer["archived_ledger_readback_path"] is None
    assert observer["parent_fallback_archive_path"] is not None
    fallback_file = out / str(observer["parent_fallback_archive_path"])
    assert fallback_file.is_file()
    import hashlib

    assert hashlib.sha256(fallback_file.read_bytes()).hexdigest() == (
        observer["parent_fallback_archive_sha256"]
    )
    result = cast(dict[str, Any], record["result"])
    assert result["oracle_classification"] == "VOID"
    assert result["passed"] is False

    problems = verify_bundle(out)
    assert problems == []  # honestly reports an unmeasured run; still internally consistent


@pytest.mark.parametrize(
    ("payload_label", "corrupt_content"),
    [
        ("empty-list", "[]"),
        ("null", "null"),
        ("empty-object", "{}"),
        ("invalid-json", "{not valid json"),
    ],
)
def test_malformed_observer_report_through_production_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload_label: str, corrupt_content: str
) -> None:
    """Production-path reproduction, not a parser unit test: the REAL worker and the REAL observer
    both execute - the worker's effect genuinely crosses the ledger, and the observer genuinely
    reads and archives it - before the observer subprocess's OWN report file is overwritten with
    malformed content, simulating a corrupted/replaced result. Reproduces Codex's finding that an
    unvalidated ``cast(dict, json.loads(...))`` crashed ``run_primary_control()`` with
    ``AttributeError`` after a successful worker execution, with no receipt ever written."""
    real_run = subprocess.run

    def intercepting_run(
        argv: list[str], *args: Any, **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        completed = real_run(argv, *args, **kwargs)
        if "crashpoint.harness.langgraph_control_observer" in argv:
            # The real observer subprocess has already read the real ledger bytes and (normally)
            # written a valid report by this point; overwrite it to simulate corruption/tampering
            # of the subprocess's result, not a parser fed synthetic input directly.
            report_out = argv[argv.index("--report-out") + 1]
            Path(report_out).write_text(corrupt_content)
        return completed

    monkeypatch.setattr(subprocess, "run", intercepting_run)

    out = tmp_path / f"malformed-report-{payload_label}"
    record = lc.run_primary_control(out, worker_timeout=20, observer_timeout=15)  # must not raise

    runtime = _section(record, "runtime")
    observer = _section(record, "observer")
    result = _section(record, "result")
    effect = _section(record, "effect")

    assert runtime["worker_ok"] is True  # the real worker genuinely completed successfully
    assert observer["status"] == "OBSERVER_ERROR"
    assert observer["error"]  # an honest, non-empty diagnostic reason is retained in the receipt
    assert effect["observed_count"] is None  # never inferred, never coerced to a false zero
    assert result["oracle_classification"] == "VOID"
    assert result["passed"] is False

    # A receipt was actually written - the bug this reproduces meant run_primary_control() raised
    # before ever reaching that write, losing the chance to record anything at all.
    assert (out / "receipt.json").is_file()

    # The original bad report is retained byte-for-byte on disk, not discarded or silently
    # replaced with something that looks better.
    assert (out / "observer-report.json").read_text() == corrupt_content

    # The worker's real effect crossed the ledger before the report was corrupted; those bytes
    # must survive regardless - via the observer's own archive (if it got that far before its
    # report was corrupted) or via the labeled parent fallback otherwise. Either way, something
    # preserving the real bytes must be declared and present.
    archived = observer.get("archived_ledger_readback_path")
    fallback = observer.get("parent_fallback_archive_path")
    assert archived is not None or fallback is not None
    preserved_name = archived if archived is not None else fallback
    assert (out / str(preserved_name)).is_file()

    problems = verify_bundle(out)
    assert problems == []  # an honest VOID receipt is still internally consistent, not "failed"


def test_malformed_observer_report_would_crash_without_shape_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Red/green proof for the fix above, not a restatement of it: with
    ``_validate_observer_report_shape`` neutralized to always say "fine" - simulating the pre-fix
    code, which trusted ``cast(dict, json.loads(...))`` with no runtime check at all - the exact
    same production path (real worker, real observer, report corrupted to ``[]`` after the
    subprocess completes) crashes with ``AttributeError``. This confirms the validation step is
    what actually prevents the crash in the test above, not some unrelated change."""
    monkeypatch.setattr(lc, "_validate_observer_report_shape", lambda parsed: None)

    real_run = subprocess.run

    def intercepting_run(
        argv: list[str], *args: Any, **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        completed = real_run(argv, *args, **kwargs)
        if "crashpoint.harness.langgraph_control_observer" in argv:
            report_out = argv[argv.index("--report-out") + 1]
            Path(report_out).write_text("[]")
        return completed

    monkeypatch.setattr(subprocess, "run", intercepting_run)

    out = tmp_path / "would-crash-without-validation"
    with pytest.raises(AttributeError):
        lc.run_primary_control(out, worker_timeout=20, observer_timeout=15)
