"""Real protocol regressions and re-signed adversarial copies of the old/new comparison."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from crashpoint.harness import reality_layer as harness
from crashpoint.harness.reality_layer_common import digest
from crashpoint.harness.reality_layer_recovery import PROFILES, recovery_property, verify_comparison
from crashpoint.harness.reality_layer_verify import derive_trial, verify_bundle
from tests.test_reality_layer import get, jsonl, put, putlines, resign

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = Path(
    os.environ.get(
        "CRASHPOINT_RECOVERY_BUNDLE", str(ROOT / "evidence/reality_layer/recovery-1822-20260924")
    )
)


def cli(
    bundle: Path, *, source: Path | None = None, prop: str | None = None
) -> subprocess.CompletedProcess[str]:
    args = [
        sys.executable,
        "-m",
        "crashpoint.harness.reality_layer_recovery",
        "--verify",
        str(bundle),
    ]
    if prop:
        args += ["--property", prop]
    return subprocess.run(
        args,
        cwd=bundle.parent,
        env={**os.environ, "PYTHONPATH": str(source or ROOT / "src")},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def resign_comparison(bundle: Path) -> None:
    for version in PROFILES:
        part = bundle / version
        resign(part, get(part / "manifest.json"))
    receipt = get(bundle / "comparison-receipt.json")
    receipt["plan_sha256"] = digest((bundle / "plan.json").read_bytes())
    receipt["manifests"] = {
        v: digest((bundle / v / "manifest.json").read_bytes()) for v in PROFILES
    }
    put(bundle / "comparison-receipt.json", receipt)


def mutate(bundle: Path, label: str) -> None:
    part = bundle / ("v181" if label == "false_unknown" else "v1822")
    m = get(part / "manifest.json")
    t = m["trials"][1]
    d = part / "trials" / t["trial_id"]
    if label == "swapped_pin":
        m["subject_pin"] = PROFILES["v181"].pin
    elif label == "mislabelled_plan":
        plan = get(bundle / "plan.json")
        plan["versions"]["v1822"]["subject_pin"] = PROFILES["v181"].pin
        put(bundle / "plan.json", plan)
    elif label == "false_unknown":
        t["findings"].update(
            status="UNKNOWN", reconciliation_required=True, reconciliation_state="REQUIRED"
        )
    elif label == "dropped_trial":
        m["trials"].pop()
    elif label == "wrong_action":
        caller = get(d / "caller.json")
        caller["action_id"] = str(uuid.uuid4())
        put(d / "caller.json", caller)
    elif label == "wrong_payload":
        rows = jsonl(d / "receiver-requests.jsonl")
        rows[0]["payload"]["capability"] = "turn_off"
        putlines(d / "receiver-requests.jsonl", rows)
    elif label in {"missing_receiver", "missing_probe"}:
        name = (
            "receiver-before_recovery.jsonl"
            if label == "missing_receiver"
            else "receiver-probe_rest.jsonl"
        )
        (d / name).unlink()
        del t["artifacts"][name]
    elif label == "startup_status":
        raw = (d / "subject-before_recovery.raw").read_bytes()
        (d / "subject-after_restart.raw").write_bytes(raw)
        put(
            d / "subject-after_restart.json",
            {"availability": "readable", "bytes": len(raw), "sha256": digest(raw)},
        )
    elif label in {"raw_status", "boolean_required"}:
        wire = jsonl(d / "api-status.jsonl")
        response = json.loads(wire[1]["body"])
        value = response["result"]["structuredContent"]
        if label == "raw_status":
            value["action"]["status"] = "STARTED"
        else:
            value["reconciliation"]["required"] = 1
        wire[1]["body"] = json.dumps(response)
        putlines(d / "api-status.jsonl", wire)
        put(d / "client-status.stdout", {"ok": True, "value": value})
    elif label == "chronology":
        events = jsonl(d / "progress.jsonl")
        index = next(i for i, e in enumerate(events) if e.get("phase") == "after_restart")
        event = events.pop(index)
        index = next(i for i, e in enumerate(events) if e.get("phase") == "after_retry")
        events.insert(index + 1, event)
        putlines(d / "progress.jsonl", events)
    elif label == "forged_probe_summary":
        t["findings"]["trust_probes"]["rest"]["ok"] = False
    else:
        raise AssertionError(label)
    put(part / "manifest.json", m)
    resign_comparison(bundle)


CASES = [
    ("swapped_pin", "manifest_pin"),
    ("mislabelled_plan", "comparison_subject_pin"),
    ("false_unknown", "findings_recompute"),
    ("dropped_trial", "trial_inventory"),
    ("wrong_action", "ir_binding"),
    ("wrong_payload", "receiver_request_binding"),
    ("missing_receiver", "artifact_unavailable"),
    ("missing_probe", "artifact_unavailable"),
    ("startup_status", "startup_query_equality"),
    ("raw_status", "status_ledger_equality"),
    ("boolean_required", "schema_boolean"),
    ("chronology", "followup_chronology"),
    ("forged_probe_summary", "findings_recompute"),
]


@pytest.mark.parametrize(("label", "reason"), CASES)
def test_comparison_tampering(tmp_path: Path, label: str, reason: str) -> None:
    bundle = tmp_path / "bundle"
    shutil.copytree(BUNDLE, bundle)
    mutate(bundle, label)
    result = cli(bundle)
    assert result.returncode == 1, result.stdout
    assert reason + ":" in json.loads(result.stdout)["error"], (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr


def test_startup_evidence_guard_removal(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    shutil.copytree(BUNDLE, bundle)
    mutate(bundle, "startup_status")
    green = cli(bundle)
    assert green.returncode == 1 and "startup_query_equality:" in green.stdout
    mutant = tmp_path / "mutant"
    shutil.copytree(ROOT / "src/crashpoint", mutant / "crashpoint")
    common = mutant / "crashpoint/harness/reality_layer_common.py"
    source = common.read_text()
    needle = 'def need(condition: bool, code: str, detail: str = "") -> None:\n'
    assert source.count(needle) == 1
    common.write_text(
        source.replace(
            needle, needle + '    if code == "startup_query_equality":\n        return\n'
        )
    )
    red = cli(bundle, source=mutant)
    assert red.returncode == 0, (red.stdout, red.stderr)
    # This is the negative regression's intended failure, not an import/schema/pin error.
    with pytest.raises(AssertionError, match="startup evidence tampering must be rejected"):
        assert red.returncode == 1, "startup evidence tampering must be rejected"
    common.write_text(source)
    restored = cli(bundle, source=mutant)
    assert restored.returncode == 1 and "startup_query_equality:" in restored.stdout


def test_property_guard_removal(tmp_path: Path) -> None:
    mutant = tmp_path / "mutant"
    shutil.copytree(ROOT / "src/crashpoint", mutant / "crashpoint")
    path = mutant / "crashpoint/harness/reality_layer_recovery.py"
    source = path.read_text()
    needle = 'return {"satisfied": not differences, "differences": differences}'
    assert source.count(needle) == 1
    path.write_text(source.replace(needle, 'return {"satisfied": True, "differences": {}}'))
    red = cli(BUNDLE, source=mutant, prop="v181")
    assert json.loads(red.stdout)["evidence_valid"] is True
    with pytest.raises(AssertionError, match="baseline recovery property must be RED"):
        assert red.returncode == 1, "baseline recovery property must be RED"
    path.write_text(source)
    green = cli(BUNDLE, source=mutant, prop="v181")
    assert green.returncode == 1
    assert json.loads(green.stdout)["evidence_valid"] is True


def test_verified_old_red_new_green_and_historical_preservation() -> None:
    result = verify_comparison(BUNDLE)
    assert result["evidence_valid"] is True and result["primary_trials"] == 8
    for version in PROFILES:
        command = cli(BUNDLE, prop=version)
        assert command.returncode == (1 if version == "v181" else 0)
        value = json.loads(command.stdout)
        assert value["evidence_valid"] is True
        properties = value["versions"][version]["recovery_property"]
        assert properties["clean-0"]["satisfied"] is True
        for i in range(3):
            p = properties[f"post_crash-{i}"]
            assert p["satisfied"] is (version == "v1822")
            assert set(p["differences"]) == (
                {"status", "reconciliation_required", "reconciliation_state"}
                if version == "v181"
                else set()
            )
    old = ROOT / "evidence/reality_layer/reality-layer-retention-fixed-20260922"
    assert verify_bundle(old)["summary"]["trials"] == 9


@pytest.mark.parametrize("version", list(PROFILES))
@pytest.mark.parametrize("tid", ["clean-0", "post_crash-0"])
def test_real_profile_capture(tmp_path: Path, version: str, tid: str) -> None:
    variable = "CRASHPOINT_REALITY_SUBJECT" if version == "v181" else "CRASHPOINT_REALITY_CANDIDATE"
    configured = os.environ.get(variable)
    if not configured:
        pytest.skip(f"set {variable} for real process protocol regression")
    qa = Path(os.environ.get("CRASHPOINT_RECOVERY_QA", str(tmp_path)))
    qa.mkdir(parents=True, exist_ok=True)
    output = qa / f"{version}-{tid}-{uuid.uuid4()}"
    plan = tmp_path / "plan.json"
    profile = PROFILES[version]
    harness.freeze(Path(configured), plan, profile)
    try:
        status = harness.capture(
            Path(configured), output, plan, only=tid, exploratory=True, profile=profile
        )
    finally:
        events = jsonl(output / "trials" / tid / "progress.jsonl")
        alive = []
        for event in events:
            if event.get("event") == "spawn":
                try:
                    os.kill(event["pid"], 0)
                except ProcessLookupError:
                    continue
                alive.append(event["pid"])
        assert not alive, f"owned children remain: {alive}"
    m = get(output / "manifest.json")
    assert status == 0, m
    t = m["trials"][0]
    observation = derive_trial(output, t, profile)
    assert observation == t["findings"] and observation["effect_count"] == 1
    assert recovery_property(t["case"], observation)["satisfied"] is (
        tid == "clean-0" or version == "v1822"
    )
    request = (
        get(output / "trials" / tid / "api-reconcile.request.json")
        if tid == "post_crash-0"
        else None
    )
    if request:
        assert set(request["arguments"]) == (
            {"action_id"} if version == "v1822" else {"action_id", "outcome", "evidence_note"}
        )
    if version == "v1822" and tid == "post_crash-0":
        assert set(observation["trust_probes"]) == {"mcp", "rest"}
        assert all(
            p["trust_boundary_satisfied"] and p["route_prediction_agrees"]
            for p in observation["trust_probes"].values()
        )
        assert observation["reconciled_status"] == "UNKNOWN"
        assert observation["reconciled_required"] is True
