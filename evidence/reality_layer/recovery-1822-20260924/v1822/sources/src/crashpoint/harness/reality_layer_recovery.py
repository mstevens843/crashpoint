"""Pinned old/new recovery comparison, composing the established capture and verifier.

Evidence validity, source-informed prediction agreement, and the recovery property are
separate outputs. No upstream source is distributed or executed by offline verification.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import reality_layer as capture_harness
from .reality_layer_common import (
    OWN_SOURCES,
    PIN,
    Invalid,
    Profile,
    confined,
    digest,
    encoded,
    json_read,
    need,
    read,
    strict_equal,
)
from .reality_layer_verify import obj, verify_bundle

FOUNDATION = "4c51e30972afb3ec272cfde0278c6ec56acf86ee"
CANDIDATE = "4213c479bd9333558522db1ca820d657b99effbf"
SOURCES = (
    *OWN_SOURCES,
    "src/crashpoint/harness/reality_layer_recovery.py",
    "tests/test_reality_layer_recovery.py",
    "handoff/reality-layer-recovery/independent-audit.py",
    "handoff/reality-layer-recovery/offline-check.py",
)
PROFILES = {
    "v181": Profile(
        pin=PIN,
        base=FOUNDATION,
        cases=(("clean", 1), ("post_crash", 3)),
        own_sources=SOURCES,
        followup=True,
    ),
    "v1822": Profile(
        pin=CANDIDATE,
        base=FOUNDATION,
        cases=(("clean", 1), ("post_crash", 3)),
        own_sources=SOURCES,
        followup=True,
        modern_reconciliation=True,
    ),
}
INVENTORY = [f"{version}/{tid}" for version, p in PROFILES.items() for tid in p.inventory]
PROTOCOL = [
    "Isolated clean tracked source, trial state, allowlisted environment and loopback receiver.",
    (
        "Public MCP plan via unmodified upstream client; fsync caller action ID and "
        "exact admitted IR before dispatch."
    ),
    (
        "Production executor; replace only virtual Beta execute with "
        "non-deduplicating fsynced receiver append."
    ),
    (
        "Hold explicit post-ack barrier; fresh observer binds exactly one effect; "
        "retain real STARTED bytes; SIGKILL and reap."
    ),
    (
        "Fresh server imports production startup against retained state; query same "
        "action; archive ledger and observe receiver."
    ),
    (
        "Retry original plan; archive rejection and independent effect readback. "
        "This does not test direct executor redispatch."
    ),
    (
        "Use version-appropriate reconciliation; candidate virtual adapter may "
        "remain UNKNOWN/REQUIRED."
    ),
    (
        "Candidate post_crash-0 only: forged outcome/evidence via MCP and legacy REST; "
        "status/ledger/receiver after each."
    ),
    (
        "Seal receiver, final fresh observer, terminate/reap all owned children and "
        "retain raw artifacts/receipts."
    ),
]
PREDICATE = {
    "post_crash": {
        "status": "UNKNOWN",
        "reconciliation_required": True,
        "reconciliation_state": "REQUIRED",
        "effect_count": 1,
        "original_plan_rejected": True,
        "retry_added_effects": 0,
        "restart_added_effects": 0,
        "reconciliation_added_effects": 0,
    },
    "clean": {
        "status": "SUCCEEDED",
        "reconciliation_required": False,
        "reconciliation_state": "NOT_REQUIRED",
        "effect_count": 1,
        "original_plan_rejected": True,
        "retry_added_effects": 0,
        "restart_added_effects": 0,
        "reconciliation_added_effects": 0,
    },
}


def freeze(subjects: dict[str, Path], destination: Path) -> None:
    need(not destination.exists(), "plan_already_exists")
    versions = {}
    for version, profile in PROFILES.items():
        # Reuse the historical freezer; temporary plans are kept beside the source-informed plan.
        part = destination.with_name(destination.name + f".{version}")
        capture_harness.freeze(subjects[version], part, profile)
        versions[version] = json_read(part.parent, part.name)
        part.unlink()
    capture_harness.write(
        destination,
        {
            "version": 1,
            "foundation": FOUNDATION,
            "frozen_at": dt.datetime.now(dt.UTC).isoformat(),
            "inventory": INVENTORY,
            "versions": versions,
            "protocol": PROTOCOL,
            "predicate": PREDICATE,
            "adjuncts": {"v1822/post_crash-0": ["forged_mcp", "forged_rest"]},
            "prediction": (
                "old STARTED/NOT_REQUIRED; new UNKNOWN/REQUIRED; both one "
                "effect and original-plan rejection"
            ),
            "limits": (
                "source-informed same-host post-fix verification, not blinded, "
                "not adoption, no reliability rate"
            ),
            "license": (
                "Neither subject pin has an established redistribution "
                "license; only pinned hash inventories retained"
            ),
        },
    )


def validate_plan(plan: dict[str, Any]) -> None:
    need(
        type(plan.get("version")) is int
        and plan["version"] == 1
        and plan.get("foundation") == FOUNDATION,
        "comparison_plan_version",
    )
    need(strict_equal(plan.get("inventory"), INVENTORY), "comparison_inventory")
    need(
        strict_equal(plan.get("protocol"), PROTOCOL)
        and strict_equal(plan.get("predicate"), PREDICATE),
        "comparison_protocol",
    )
    need(set(obj(plan.get("versions"), "versions")) == set(PROFILES), "version_inventory")
    for version, profile in PROFILES.items():
        part = plan["versions"][version]
        need(
            part.get("subject_pin") == profile.pin and part.get("base") == FOUNDATION,
            "comparison_subject_pin",
            version,
        )
        need(
            strict_equal(part.get("cases"), dict(profile.cases))
            and strict_equal(part.get("inventory"), profile.inventory),
            "comparison_case_inventory",
        )


def recovery_property(case: str, observation: dict[str, Any]) -> dict[str, Any]:
    """One version-independent predicate, applied only after raw evidence verification."""
    differences = {
        key: {"expected": value, "observed": observation.get(key)}
        for key, value in PREDICATE[case].items()
        if not strict_equal(observation.get(key), value)
    }
    return {"satisfied": not differences, "differences": differences}


def verify_comparison(bundle: Path) -> dict[str, Any]:
    plan = obj(json_read(bundle, "plan.json"), "comparison plan")
    validate_plan(plan)
    receipt = obj(json_read(bundle, "comparison-receipt.json"), "comparison receipt")
    need(
        strict_equal(
            receipt,
            {
                "version": 1,
                "plan_sha256": digest(read(bundle, "plan.json")),
                "environment_sha256": digest(read(bundle, "environment.json")),
                "manifests": {
                    version: digest(read(bundle, f"{version}/manifest.json"))
                    for version in PROFILES
                },
            },
        ),
        "comparison_receipt",
    )
    environment = obj(json_read(bundle, "environment.json"), "environment")
    need(
        environment.get("python_version") == "3.12.13"
        and environment.get("node_version") == "v22.22.1"
        and environment.get("REALITY_LIVE") == "0"
        and environment.get("PC_ADAPTER_DRY_RUN") == "1",
        "comparison_environment",
    )
    versions = {}
    actions: set[str] = set()
    attempts: set[str] = set()
    runs: set[str] = set()
    for version, profile in PROFILES.items():
        part = confined(bundle, version)
        need(
            strict_equal(json_read(part, "plan.json"), plan["versions"][version]),
            "comparison_plan_binding",
            version,
        )
        manifest = json_read(part, "manifest.json")
        need(
            dt.datetime.fromisoformat(plan["frozen_at"])
            < dt.datetime.fromisoformat(manifest["started_at"]),
            "freeze_before_capture",
        )
        verified = verify_bundle(part, profile)
        need(verified["run_id"] not in runs, "comparison_run_identity")
        runs.add(verified["run_id"])
        properties = {}
        predictions = {}
        for tid, finding in verified["summary"]["findings"].items():
            need(finding["action_id"] not in actions, "comparison_action_identity")
            actions.add(finding["action_id"])
            need(not (set(finding["attempt_ids"]) & attempts), "comparison_attempt_identity")
            attempts.update(finding["attempt_ids"])
            case = tid.rsplit("-", 1)[0]
            properties[tid] = recovery_property(case, finding)
            predictions[tid] = strict_equal(
                [finding["status"], finding["reconciliation_required"], finding["effect_count"]],
                plan["versions"][version]["predictions"][case],
            )
        versions[version] = {
            **verified,
            "subject_pin": profile.pin,
            "prediction_agreement": predictions,
            "recovery_property": properties,
        }
    return {
        "evidence_valid": True,
        "primary_trials": len(INVENTORY),
        "versions": versions,
        "independence": (
            "fresh observer processes and offline raw-byte derivation; same host/user/author"
        ),
    }


def capture(subjects: dict[str, Path], output: Path, plan_path: Path) -> int:
    need(not output.exists(), "output_exists")
    plan = obj(json_read(plan_path.parent, plan_path.name), "plan")
    validate_plan(plan)
    output.mkdir(parents=True)
    (output / "plan.json").write_bytes(encoded(plan))
    node = shutil.which("node")
    if node is None:
        raise Invalid("node_missing")
    capture_harness.write(
        output / "environment.json",
        {
            "python_version": ".".join(str(x) for x in sys.version_info[:3]),
            "python_executable": sys.executable,
            "node_version": subprocess.check_output(
                [node, "--version"], text=True, timeout=10
            ).strip(),
            "node_executable": node,
            "REALITY_LIVE": "0",
            "PC_ADAPTER_DRY_RUN": "1",
            "environment_policy": "allowlist via safe_env; no inherited provider/device secrets",
        },
    )
    exits = []
    for version, profile in PROFILES.items():
        part = output / f"{version}-plan.json"
        capture_harness.write(part, plan["versions"][version])
        exits.append(
            capture_harness.capture(subjects[version], output / version, part, profile=profile)
        )
        part.unlink()
    capture_harness.write(
        output / "comparison-receipt.json",
        {
            "version": 1,
            "plan_sha256": digest(read(output, "plan.json")),
            "environment_sha256": digest(read(output, "environment.json")),
            "manifests": {
                version: digest(read(output, f"{version}/manifest.json")) for version in PROFILES
            },
        },
    )
    if any(exits):
        return 1
    result = verify_comparison(output)
    print(json.dumps(result, sort_keys=True))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline", type=Path)
    ap.add_argument("--candidate", type=Path)
    ap.add_argument("--plan", type=Path)
    ap.add_argument("--freeze", action="store_true")
    ap.add_argument("--audit-source", action="store_true")
    ap.add_argument("--output", type=Path)
    ap.add_argument("--verify", type=Path)
    ap.add_argument("--property", choices=list(PROFILES))
    args = ap.parse_args()
    try:
        if args.verify:
            result = verify_comparison(args.verify.resolve())
            print(json.dumps(result, sort_keys=True))
            if args.property:
                return int(
                    not all(
                        p["satisfied"]
                        for p in result["versions"][args.property]["recovery_property"].values()
                    )
                )
            return 0
        need(
            args.baseline is not None and args.candidate is not None and args.plan is not None,
            "subjects_and_plan_required",
        )
        subjects = {"v181": args.baseline.resolve(), "v1822": args.candidate.resolve()}
        if args.freeze:
            freeze(subjects, args.plan.resolve())
            return 0
        if args.audit_source:
            plan = json_read(args.plan.parent, args.plan.name)
            validate_plan(plan)
            counts = {}
            for version, profile in PROFILES.items():
                inventory = capture_harness.source_inventory(subjects[version], profile)
                need(
                    strict_equal(inventory, plan["versions"][version]["subject_sources"]),
                    "source_audit",
                    version,
                )
                counts[version] = {
                    "pin": profile.pin,
                    "verified_files": len(inventory),
                    "inventory_sha256": digest(encoded(inventory)),
                }
            print(json.dumps(counts, sort_keys=True))
            return 0
        need(args.output is not None, "output_required")
        return capture(subjects, args.output.resolve(), args.plan.resolve())
    except (
        Invalid,
        OSError,
        KeyError,
        TypeError,
        ValueError,
        StopIteration,
        AttributeError,
    ) as exc:
        print(json.dumps({"evidence_valid": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
