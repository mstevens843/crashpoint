from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from crashpoint.canonical import receipt
from crashpoint.harness import crewai_retry_artifact as artifact


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    for path in artifact.FILES:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((artifact.ROOT / path).read_bytes())
    return root


def test_bundle_preserves_every_source_byte_and_records_file_hashes(tmp_path: Path) -> None:
    output = tmp_path / "artifact"
    manifest = artifact.package_evidence(output, env={})
    assert manifest == json.loads((output / "manifest.json").read_text())
    assert manifest["publication"] == {
        "kind": "local_preview",
        "experiment_executed_in_this_job": False,
    }
    assert manifest["recorded_experiment"]["trial_count"] == 90
    assert manifest["recorded_experiment"]["case_names"] == ["clean", "post_effect", "pre_effect"]
    for file in manifest["files"]:
        data = (output / file["path"]).read_bytes()
        assert data == (artifact.ROOT / file["path"]).read_bytes()
        assert file["sha256"] == hashlib.sha256(data).hexdigest()
        assert file["bytes"] == len(data)
    assert len(list(output.rglob("*json"))) == 3


def test_corrupt_evidence_cannot_be_published(source: Path, tmp_path: Path) -> None:
    path = source / artifact.EVIDENCE
    record = json.loads(path.read_text())
    record["trials"][0]["effect_count"] = 99
    path.write_text(json.dumps(record))
    output = tmp_path / "artifact"
    with pytest.raises(ValueError, match="receipt does not match"):
        artifact.package_evidence(output, root=source, env={})
    assert not output.exists()


def test_null_count_cannot_pass_even_with_a_recomputed_receipt(
    source: Path, tmp_path: Path
) -> None:
    path = source / artifact.EVIDENCE
    record = json.loads(path.read_text())
    record["trials"][0]["effect_count"] = None
    del record["receipt"]
    record["receipt"] = receipt(record)
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="invalid recorded trial"):
        artifact.package_evidence(tmp_path / "artifact", root=source, env={})


def test_missing_source_fails_before_creating_bundle(source: Path, tmp_path: Path) -> None:
    (source / artifact.FILES[1]).unlink()
    output = tmp_path / "artifact"
    with pytest.raises(FileNotFoundError):
        artifact.package_evidence(output, root=source, env={})
    assert not output.exists()


def test_existing_output_is_not_reused(tmp_path: Path) -> None:
    output = tmp_path / "artifact"
    output.mkdir()
    sentinel = output / "old.json"
    sentinel.write_text("old evidence")
    with pytest.raises(FileExistsError):
        artifact.package_evidence(output, env={})
    assert sentinel.read_text() == "old evidence"


def _ci_environment() -> dict[str, str]:
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=artifact.ROOT, text=True
    ).strip()
    return {
        "GITHUB_ACTIONS": "true",
        "GITHUB_SHA": commit,
        "GITHUB_REPOSITORY": "example/crashpoint",
        "GITHUB_RUN_ID": "1234",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_JOB": "test",
        "GITHUB_WORKFLOW": "CI",
        "GITHUB_SERVER_URL": "https://github.com",
    }


def test_ci_binds_committed_bytes_and_keeps_original_revision(tmp_path: Path) -> None:
    env = _ci_environment()
    manifest = artifact.package_evidence(tmp_path / "artifact", env=env)
    publication = manifest["publication"]
    assert publication["commit"] == env["GITHUB_SHA"]
    assert publication["run_id"] == 1234
    assert publication["run_attempt"] == 2
    assert publication["job_key"] == "test"
    assert "job_id" not in publication
    assert publication["experiment_executed_in_this_job"] is False
    assert publication["run_url"].endswith("/actions/runs/1234/attempts/2")
    record = json.loads((artifact.ROOT / artifact.EVIDENCE).read_bytes())
    assert manifest["recorded_experiment"]["reported_crashpoint_commits"] == sorted(
        {trial["crashpoint_commit"] for trial in record["trials"]}
    )


def test_ci_rejects_a_different_checkout(tmp_path: Path) -> None:
    env = _ci_environment()
    env["GITHUB_SHA"] = "0" * 40
    with pytest.raises(ValueError, match="checkout does not match"):
        artifact.package_evidence(tmp_path / "artifact", env=env)


def test_ci_rejects_uncommitted_file_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_read = Path.read_bytes

    def changed_read(path: Path) -> bytes:
        data = original_read(path)
        return data + b"\n" if path == artifact.ROOT / artifact.EVIDENCE else data

    monkeypatch.setattr(Path, "read_bytes", changed_read)
    with pytest.raises(ValueError, match="file differs from publication commit"):
        artifact.package_evidence(tmp_path / "artifact", env=_ci_environment())


def test_missing_ci_provenance_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing CI provenance"):
        artifact.package_evidence(tmp_path / "artifact", env={"GITHUB_ACTIONS": "true"})


@pytest.mark.parametrize("key", ["GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"])
def test_ci_rejects_nonpositive_run_identity(key: str, tmp_path: Path) -> None:
    env = _ci_environment()
    env[key] = "0"
    with pytest.raises(ValueError, match="must be positive"):
        artifact.package_evidence(tmp_path / "artifact", env=env)
