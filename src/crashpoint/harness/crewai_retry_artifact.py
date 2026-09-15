"""Publish a byte-preserving bundle of the recorded CrewAI experiment and CI provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from crashpoint.canonical import receipt
from crashpoint.harness.crewai_retry_receipt import validate_receipt

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = "evidence/crewai_retry.json"
FILES = (
    EVIDENCE,
    "results/11-crewai-retry-prediction.json",
    "results/11-crewai-retry.md",
)


def _git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=root)


def _publication(root: Path, files: Mapping[str, bytes], env: Mapping[str, str]) -> dict[str, Any]:
    if env.get("GITHUB_ACTIONS") != "true":
        return {"kind": "local_preview", "experiment_executed_in_this_job": False}

    required = (
        "GITHUB_SHA",
        "GITHUB_REPOSITORY",
        "GITHUB_RUN_ID",
        "GITHUB_RUN_ATTEMPT",
        "GITHUB_JOB",
        "GITHUB_WORKFLOW",
        "GITHUB_SERVER_URL",
    )
    missing = [key for key in required if not env.get(key)]
    if missing:
        raise ValueError(f"missing CI provenance: {', '.join(missing)}")
    commit = env["GITHUB_SHA"]
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ValueError("GITHUB_SHA must be a full commit SHA")
    if _git(root, "rev-parse", "HEAD").decode().strip() != commit:
        raise ValueError("checkout does not match GITHUB_SHA")
    for path, data in files.items():
        if _git(root, "show", f"{commit}:{path}") != data:
            raise ValueError(f"file differs from publication commit: {path}")
    run_id = int(env["GITHUB_RUN_ID"])
    run_attempt = int(env["GITHUB_RUN_ATTEMPT"])
    if min(run_id, run_attempt) < 1:
        raise ValueError("CI run ID and attempt must be positive")
    repository_url = f"{env['GITHUB_SERVER_URL']}/{env['GITHUB_REPOSITORY']}"
    return {
        "kind": "github_actions_publication_of_recorded_evidence",
        "experiment_executed_in_this_job": False,
        "repository": env["GITHUB_REPOSITORY"],
        "commit": commit,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "run_url": f"{repository_url}/actions/runs/{run_id}/attempts/{run_attempt}",
        "workflow": env["GITHUB_WORKFLOW"],
        # GitHub exposes the job key here. The numeric job ID is resolved through the run API.
        "job_key": env["GITHUB_JOB"],
        "evidence_url": f"{repository_url}/blob/{commit}/{EVIDENCE}",
    }


def package_evidence(
    output: Path, *, root: Path = ROOT, env: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Check the stored receipt and copy exact bytes into a new artifact directory."""
    if output.exists():
        raise FileExistsError(f"artifact output already exists: {output}")
    files = {path: (root / path).read_bytes() for path in FILES}
    record = json.loads(files[EVIDENCE])
    body = dict(record)
    recorded_receipt = body.pop("receipt")
    if recorded_receipt != receipt(body):
        raise ValueError("recorded CrewAI evidence receipt does not match its contents")
    trials = record["trials"]
    if not trials:
        raise ValueError("recorded CrewAI evidence contains no trials")
    for trial in trials:
        problems = validate_receipt(trial)
        if problems:
            raise ValueError(f"invalid recorded trial {trial.get('logical_action_id')}: {problems}")

    manifest = {
        "schema": "crashpoint.recorded_evidence_artifact.v1",
        "publication": _publication(root, files, os.environ if env is None else env),
        "recorded_experiment": {
            "name": record["name"],
            "receipt": recorded_receipt,
            "trial_count": len(trials),
            "case_names": sorted({trial["case"] for trial in trials}),
            "reported_crewai_versions": sorted({trial["crewai_version"] for trial in trials}),
            "reported_crewai_commits": sorted({trial["crewai_source_commit"] for trial in trials}),
            "reported_crashpoint_commits": sorted({trial["crashpoint_commit"] for trial in trials}),
        },
        "scope": (
            "This bundle preserves an existing experiment. CI checks the recorded receipt and "
            "publishes exact file bytes. The publication commit identifies the uploaded files; "
            "reported experiment commits are copied from the original receipts. This publication "
            "does not attest the original execution time or a clean original checkout, rerun the "
            "90 trials, or reread their original effect ledger. The file hashes describe evidence "
            "files, not before/after environment state."
        ),
        "files": [
            {"path": path, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            for path, data in files.items()
        ],
    }
    output.mkdir(parents=True, exist_ok=False)
    for path, data in files.items():
        target = output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = package_evidence(args.output)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
