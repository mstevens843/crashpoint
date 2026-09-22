"""Adversarial retained-evidence tests; real-process failpoints are explicit opt-in."""

from __future__ import annotations

import copy
import json
import os
import shutil
import signal
import subprocess
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from crashpoint.canonical import chain
from crashpoint.harness.reality_layer_common import digest, encoded
from crashpoint.harness.reality_layer_verify import verify_bundle

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = Path(
    os.environ.get(
        "CRASHPOINT_REALITY_BUNDLE",
        str(ROOT / "evidence/reality_layer/reality-layer-final-20260922"),
    )
)
Mutation = Callable[[Path, dict[str, Any]], None]


def get(path: Path) -> Any:
    return json.loads(path.read_bytes())


def put(path: Path, value: Any) -> None:
    path.write_bytes(encoded(value))


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def putlines(path: Path, rows: list[Any]) -> None:
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))


def resign(bundle: Path, m: dict[str, Any], *, journal: bool = True) -> None:
    for trial in m["trials"]:
        directory = bundle / "trials" / trial["trial_id"]
        # Retain the declared inventory; tests explicitly remove/add entries when needed.
        for name in trial["artifacts"]:
            path = directory / name
            if path.is_file():
                trial["artifacts"][name] = digest(path.read_bytes())
        put(directory / "receipt.json", trial)
    if journal:
        putlines(bundle / "journal.jsonl", m["trials"])
    m["plan_sha256"] = digest((bundle / "plan.json").read_bytes())
    put(bundle / "manifest.json", m)
    put(
        bundle / "bundle-receipt.json",
        {
            "version": 1,
            "run_id": m["run_id"],
            "manifest_sha256": digest((bundle / "manifest.json").read_bytes()),
        },
    )


def finding(field: str, value: Any) -> Mutation:
    def apply(bundle: Path, m: dict[str, Any]) -> None:
        m["trials"][0]["findings"][field] = value

    return apply


def wire_flag(bundle: Path, m: dict[str, Any]) -> None:
    p = bundle / "trials/clean-0/api-status.jsonl"
    rows = jsonl(p)
    value = json.loads(rows[1]["body"])
    value["result"]["structuredContent"]["reconciliation"]["required"] = True
    rows[1]["body"] = json.dumps(value)
    putlines(p, rows)


def local_status(bundle: Path, m: dict[str, Any]) -> None:
    d = bundle / "trials/clean-0"
    for phase in ["after_query", "final"]:
        p = d / f"subject-{phase}.raw"
        rows = get(p)
        rows[0]["status"] = "FAILED"
        put(p, rows)
        put(
            d / f"subject-{phase}.json",
            {
                "availability": "readable",
                "bytes": p.stat().st_size,
                "sha256": digest(p.read_bytes()),
            },
        )


def effect(field: str, value: Any) -> Mutation:
    def apply(bundle: Path, m: dict[str, Any]) -> None:
        d = bundle / "trials/clean-0"
        for phase in ["before_recovery", "after_retry", "final"]:
            p = d / f"receiver-{phase}.jsonl"
            rows = jsonl(p)
            rows[0]["record"][field] = value
            rows[0]["hash"] = chain(rows[0]["prev"], rows[0]["record"])
            putlines(p, rows)
            report = get(d / f"observation-{phase}.json")
            report.update(
                attempts=[r["record"] for r in rows],
                head=rows[-1]["hash"],
                sha256=digest(p.read_bytes()),
                bytes=p.stat().st_size,
            )
            put(d / f"observation-{phase}.json", report)
            put(d / f"observer-{phase}.stdout", report)

    return apply


def incremental(bundle: Path, m: dict[str, Any]) -> None:
    rows = jsonl(bundle / "journal.jsonl")
    rows[0]["errors"] = [{"fabricated": True}]
    putlines(bundle / "journal.jsonl", rows)


def missing_trial(bundle: Path, m: dict[str, Any]) -> None:
    m["trials"].pop()


def duplicate_trial(bundle: Path, m: dict[str, Any]) -> None:
    m["trials"].append(copy.deepcopy(m["trials"][0]))


def wrong_counts(bundle: Path, m: dict[str, Any]) -> None:
    m["cases"]["post_crash"] = 2


def summary(bundle: Path, m: dict[str, Any]) -> None:
    m["summary"]["valid"] = 999


def missing_evidence(bundle: Path, m: dict[str, Any]) -> None:
    (bundle / "trials/pre_crash-0/receiver-before_recovery.jsonl").unlink()
    del m["trials"][4]["artifacts"]["receiver-before_recovery.jsonl"]


def malformed_prediction(bundle: Path, m: dict[str, Any]) -> None:
    plan = get(bundle / "plan.json")
    plan["predictions"]["clean"][2] = True
    put(bundle / "plan.json", plan)


def missing_source_inventory(bundle: Path, m: dict[str, Any]) -> None:
    del m["source_hashes"]["runtime/reality-layer/shim.cjs"]
    plan = get(bundle / "plan.json")
    plan["own_sources"] = m["source_hashes"]
    put(bundle / "plan.json", plan)


def missing_source(bundle: Path, m: dict[str, Any]) -> None:
    (bundle / "sources/runtime/reality-layer/shim.cjs").unlink()


def empty_events(bundle: Path, m: dict[str, Any]) -> None:
    (bundle / "trials/clean-0/runtime-first.stdout").write_bytes(b"")


def source_hash(bundle: Path, m: dict[str, Any]) -> None:
    (bundle / "sources/runtime/reality-layer/shim.cjs").write_text("// altered source\n")


def source_inventory(bundle: Path, m: dict[str, Any]) -> None:
    m["source_hashes"]["unexpected.js"] = "0" * 64
    plan = get(bundle / "plan.json")
    plan["own_sources"] = m["source_hashes"]
    put(bundle / "plan.json", plan)


def malformed_boolean(bundle: Path, m: dict[str, Any]) -> None:
    d = bundle / "trials/clean-0"
    p = d / "api-status.jsonl"
    rows = jsonl(p)
    response = json.loads(rows[1]["body"])
    value = response["result"]["structuredContent"]
    value["reconciliation"]["required"] = 0
    rows[1]["body"] = json.dumps(response)
    putlines(p, rows)
    client = get(d / "client-status.stdout")
    client["value"] = value
    put(d / "client-status.stdout", client)


def malformed_integer(bundle: Path, m: dict[str, Any]) -> None:
    d = bundle / "trials/clean-0"
    for name in ["observation-final.json", "observer-final.stdout"]:
        value = get(d / name)
        value["effects"] = True
        put(d / name, value)


def path_escape(bundle: Path, m: dict[str, Any]) -> None:
    m["trials"][0]["artifacts"]["../../../../escaped"] = "0" * 64


def symlink_escape(bundle: Path, m: dict[str, Any]) -> None:
    p = bundle / "trials/clean-0/caller.json"
    outside = bundle.parent / "outside-caller.json"
    shutil.copyfile(p, outside)
    p.unlink()
    p.symlink_to(outside)


def fixed_symlink(bundle: Path, m: dict[str, Any]) -> None:
    p = bundle / "plan.json"
    outside = bundle.parent / "outside-plan.json"
    shutil.copyfile(p, outside)
    p.unlink()
    p.symlink_to(outside)


def internal_symlink(bundle: Path, m: dict[str, Any]) -> None:
    p = bundle / "trials/clean-0/caller.json"
    target = bundle / "trials/clean-0/alias-caller.json"
    shutil.copyfile(p, target)
    p.unlink()
    p.symlink_to(target)
    m["trials"][0]["artifacts"]["alias-caller.json"] = digest(target.read_bytes())


def reordered_trials(bundle: Path, m: dict[str, Any]) -> None:
    m["trials"][0], m["trials"][1] = m["trials"][1], m["trials"][0]


def boolean_index(bundle: Path, m: dict[str, Any]) -> None:
    d = bundle / "trials/clean-0"
    for phase in ["before_recovery", "after_retry", "final"]:
        p = d / f"receiver-{phase}.jsonl"
        rows = jsonl(p)
        rows[0]["i"] = False
        putlines(p, rows)
        for name in [f"observation-{phase}.json", f"observer-{phase}.stdout"]:
            report = get(d / name)
            report.update(sha256=digest(p.read_bytes()), bytes=p.stat().st_size)
            put(d / name, report)


def reordered_attempt(bundle: Path, m: dict[str, Any]) -> None:
    # All empirical trials have <=1 attempt. Alter the retained raw order index, not a sort.
    d = bundle / "trials/clean-0/receiver-final.jsonl"
    rows = jsonl(d)
    rows[0]["i"] = 1
    putlines(d, rows)


def reordered_two_attempts(bundle: Path, m: dict[str, Any]) -> None:
    d = bundle / "trials/clean-0"
    p = d / "receiver-final.jsonl"
    first = jsonl(p)[0]["record"]
    second = {**first, "attempt": 2, "attempt_id": str(uuid.uuid4())}
    head = "crashpoint-ledger-genesis-cp1"
    rows = []
    for i, record in enumerate([second, first]):
        next_head = chain(head, record)
        rows.append({"i": i, "prev": head, "hash": next_head, "record": record})
        head = next_head
    putlines(p, rows)
    for name in ["observation-final.json", "observer-final.stdout"]:
        report = get(d / name)
        report.update(
            sha256=digest(p.read_bytes()),
            bytes=p.stat().st_size,
            effects=2,
            attempts=[second, first],
            head=head,
        )
        put(d / name, report)


def cross_trial(bundle: Path, m: dict[str, Any]) -> None:
    d = bundle / "trials/clean-0/receiver-requests.jsonl"
    rows = jsonl(d)
    rows[0]["trial_id"] = "post_crash-0"
    putlines(d, rows)


def null_shape(bundle: Path, m: dict[str, Any]) -> None:
    put(bundle / "trials/clean-0/caller.json", None)


def malformed_json(bundle: Path, m: dict[str, Any]) -> None:
    (bundle / "trials/clean-0/caller.json").write_bytes(b"{broken")


def unsupported_version(bundle: Path, m: dict[str, Any]) -> None:
    m["version"] = 2


def journal_missing(bundle: Path, m: dict[str, Any]) -> None:
    (bundle / "journal.jsonl").write_bytes(b"")


MUTATIONS: list[tuple[str, Mutation, str]] = [
    ("false_status", finding("status", "FAILED"), "findings_recompute"),
    ("false_flag", finding("reconciliation_required", True), "findings_recompute"),
    ("false_count", finding("effect_count", 2), "findings_recompute"),
    ("wire_flag", wire_flag, "client_wire_equality"),
    ("local_status", local_status, "status_ledger_equality"),
    ("payload", effect("payload_digest", "0" * 64), "effect_binding"),
    ("action_id", effect("intent_id", str(uuid.uuid4())), "effect_binding"),
    ("attempt_sequence", effect("attempt", 2), "attempt_order"),
    ("incremental", incremental, "journal_equality"),
    ("missing_trial", missing_trial, "trial_inventory"),
    ("duplicate_trial", duplicate_trial, "trial_inventory"),
    ("wrong_counts", wrong_counts, "case_inventory"),
    ("summary", summary, "summary_recompute"),
    ("missing_evidence", missing_evidence, "artifact_unavailable"),
    ("missing_source", missing_source, "artifact_unavailable"),
    ("missing_source_inventory", missing_source_inventory, "source_inventory"),
    ("malformed_prediction", malformed_prediction, "schema_integer"),
    ("source_hash", source_hash, "source_hash"),
    ("source_inventory", source_inventory, "source_inventory"),
    ("boolean", malformed_boolean, "schema_boolean"),
    ("integer", malformed_integer, "schema_integer"),
    ("escape", path_escape, "path_escape"),
    ("symlink", symlink_escape, "path_symlink"),
    ("fixed_symlink", fixed_symlink, "path_symlink"),
    ("order", reordered_attempt, "effect_order"),
    ("reordered_two_attempts", reordered_two_attempts, "attempt_order"),
    ("boolean_index", boolean_index, "schema_integer"),
    ("reordered_trials", reordered_trials, "trial_inventory"),
    ("internal_symlink", internal_symlink, "path_symlink"),
    ("cross_trial", cross_trial, "receiver_request_binding"),
    ("null", null_shape, "schema_object"),
    ("malformed_json", malformed_json, "json_parse"),
    ("version", unsupported_version, "manifest_version"),
    ("empty_journal", journal_missing, "journal_equality"),
    ("empty_events", empty_events, "runtime_events_empty"),
]


def cli(bundle: Path, pythonpath: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "crashpoint.harness.reality_layer_verify", str(bundle)],
        env={**os.environ, "PYTHONPATH": str(pythonpath or ROOT / "src")},
        cwd=bundle.parent,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


@pytest.mark.parametrize(("label", "mutate", "reason"), MUTATIONS, ids=[x[0] for x in MUTATIONS])
def test_semantic_mutation_rejected(
    tmp_path: Path, label: str, mutate: Mutation, reason: str
) -> None:
    bundle = tmp_path / "bundle"
    shutil.copytree(BUNDLE, bundle)
    m = get(bundle / "manifest.json")
    mutate(bundle, m)
    resign(bundle, m, journal=label not in {"incremental", "empty_journal"})
    result = cli(bundle)
    assert result.returncode == 1, result.stdout
    assert reason + ":" in get_result(result)["error"], result.stdout
    assert "Traceback" not in result.stderr


def get_result(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    value = json.loads(result.stdout)
    assert isinstance(value, dict)
    return value


# Disabling just these guards in disposable verifier source must make the corresponding
# negative regression RED (bad evidence accepted). Restoring it makes the regression GREEN.
GUARD_CASES = [
    "false_status",
    "local_status",
    "payload",
    "attempt_sequence",
    "incremental",
    "summary",
    "source_hash",
    "source_inventory",
    "internal_symlink",
    "cross_trial",
    "wrong_counts",
    "reordered_trials",
    "boolean_index",
    "version",
]


@pytest.mark.parametrize("label", GUARD_CASES)
def test_guard_removal_red_green(tmp_path: Path, label: str) -> None:
    _, mutate, guard = next(row for row in MUTATIONS if row[0] == label)
    bundle = tmp_path / "bundle"
    shutil.copytree(BUNDLE, bundle)
    m = get(bundle / "manifest.json")
    mutate(bundle, m)
    resign(bundle, m, journal=label != "incremental")
    green = cli(bundle)
    assert green.returncode == 1 and guard + ":" in get_result(green)["error"]
    mutant = tmp_path / "mutant"
    shutil.copytree(ROOT / "src/crashpoint", mutant / "crashpoint")
    common = mutant / "crashpoint/harness/reality_layer_common.py"
    original = common.read_text()
    needle = 'def need(condition: bool, code: str, detail: str = "") -> None:\n'
    assert needle in original
    common.write_text(
        original.replace(needle, needle + f'    if code == "{guard}":\n        return\n')
    )
    red = cli(bundle, mutant)
    assert red.returncode == 0, (guard, red.stdout, red.stderr)
    common.write_text(original)
    restored = cli(bundle, mutant)
    assert restored.returncode == 1 and guard + ":" in get_result(restored)["error"]


def test_actual_recorded_public_findings() -> None:
    result = verify_bundle(BUNDLE)
    findings = result["summary"]["findings"]
    assert findings["clean-0"]["status"] == "SUCCEEDED"
    for i in range(3):
        trial = findings[f"post_crash-{i}"]
        assert trial["status"] == "STARTED" and trial["reconciliation_required"] is False
        assert trial["reconcile_changed"] is False and trial["effect_count"] == 1
    assert findings["pre_crash-0"]["effect_count"] == 0
    assert findings["ordinary_error-0"]["failed_with_observed_effect"]
    assert findings["unknown_error-0"]["reconciled_status"] == "SUCCEEDED"
    for case in ["corrupt", "missing"]:
        assert findings[f"{case}-0"]["history_missing_with_effect"]
    assert all(
        t["original_plan_rejected"] and t["retry_added_effects"] == 0 for t in findings.values()
    )


FAILPOINTS = [
    "source",
    "manifest",
    "startup",
    "reset",
    "status",
    "observer_malformed",
    "observer_nonzero",
    "incremental",
    "retention",
]


@pytest.mark.parametrize("failpoint", FAILPOINTS)
def test_real_harness_failure_cleanup(tmp_path: Path, failpoint: str) -> None:
    subject = os.environ.get("CRASHPOINT_REALITY_SUBJECT")
    if not subject:
        pytest.skip("set CRASHPOINT_REALITY_SUBJECT for explicit real-process failure-path tests")
    qa_root = Path(os.environ.get("CRASHPOINT_REALITY_QA", str(tmp_path)))
    qa_root.mkdir(parents=True, exist_ok=True)
    output = qa_root / f"{failpoint}-{uuid.uuid4()}"
    plan = tmp_path / "plan.json"
    frozen = subprocess.run(
        [
            sys.executable,
            "-m",
            "crashpoint.harness.reality_layer",
            "--subject",
            subject,
            "--plan",
            str(plan),
            "--freeze",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert frozen.returncode == 0, frozen.stderr
    command = [
        sys.executable,
        "-m",
        "crashpoint.harness.reality_layer",
        "--subject",
        subject,
        "--plan",
        str(plan),
        "--output",
        str(output),
        "--only",
        "clean-0",
        "--exploratory",
        "--failpoint",
        failpoint,
    ]
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True
    )
    stdout = stderr = ""
    try:
        stdout, stderr = process.communicate(timeout=45)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)
        # Examine ALL spawned children before any assertion can exit the test.
        live = []
        progress = output / "trials/clean-0/progress.jsonl"
        spawns = (
            [r for r in jsonl(progress) if r.get("event") == "spawn"] if progress.exists() else []
        )
        for child in spawns:
            try:
                os.kill(child["pid"], 0)
            except ProcessLookupError:
                continue
            live.append(child)
        assert not live, f"owned child processes remain: {live}"
    assert process.returncode == 1, (stdout, stderr)
    m = get(output / ("partial-manifest.json" if failpoint == "manifest" else "manifest.json"))
    assert m["state"] == "invalid"
    if failpoint == "source":
        assert m["errors"] and m["trials"] == [] and not spawns
        return
    assert len(m["trials"]) == 1
    t = m["trials"][0]
    assert t["processes"] and all(p["reaped"] for p in t["processes"])
    assert t["errors"] or m["errors"]
    if failpoint in {"incremental", "manifest"}:
        assert t["evidence_valid"] is True and t["findings"]["effect_count"] == 1
        if failpoint == "incremental":
            assert (output / "journal.jsonl").read_bytes() == b""
        else:
            assert len(jsonl(output / "journal.jsonl")) == 1
            assert not (output / "bundle-receipt.json").exists()
    else:
        assert t["evidence_valid"] is False
    if failpoint == "retention":
        assert (output / "trials/clean-0/partial-receipt.json").exists()
        assert not (output / "trials/clean-0/receipt.json").exists()
    if failpoint in {"observer_malformed", "observer_nonzero", "status"}:
        raw = output / "trials/clean-0/controller-fallback-effects.raw"
        assert len(jsonl(raw)) == 1
        assert not (output / "trials/clean-0/observation-final.json").exists()
