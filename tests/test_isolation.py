"""Linux UID isolation adversary helpers."""

from __future__ import annotations

import json
import os
import platform
import shutil
from pathlib import Path

import pytest

from crashpoint.adversaries import isolation
from crashpoint.canonical import receipt

_EVIDENCE = Path(__file__).resolve().parents[1] / "evidence" / "isolation_linux.json"
_MACOS_EVIDENCE = Path(__file__).resolve().parents[1] / "evidence" / "isolation_macos.json"


def test_subject_passed_requires_execute_only_access() -> None:
    good = {
        "execute_ok": True,
        "control_verbs_denied_on_invoke": {"dump": True, "reset": True, "seal": True},
        "control_socket_denied": True,
        "store_read_denied": True,
        "store_write_denied": True,
    }
    assert isolation.subject_passed(good)

    bad = dict(good)
    bad["store_read_denied"] = False
    assert not isolation.subject_passed(bad)


def test_blocker_reports_unsupported_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    assert isolation.blocker() == "requires Linux or macOS UID isolation; this host is Windows"


@pytest.mark.parametrize("system", ["Linux", "Darwin"])
def test_blocker_reports_missing_root(monkeypatch: pytest.MonkeyPatch, system: str) -> None:
    monkeypatch.setattr(platform, "system", lambda: system)
    monkeypatch.setattr(os, "geteuid", lambda: 501)
    assert isolation.blocker() == "requires root so the subject process can drop to nobody"


def test_darwin_root_needs_only_nobody(monkeypatch: pytest.MonkeyPatch) -> None:
    # No setpriv on macOS: Python drops the uid itself, so root plus a nobody user is enough.
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    assert isolation.blocker() is None
    assert isolation.drop_method() == "python-setreuid"


def test_darwin_evidence_is_named_natively() -> None:
    report = isolation.IsolationReport(
        "PASS",
        "uid-dropped subject had execute-only access",
        {"platform": "Darwin", "drop_method": "python-setreuid"},
    )
    assert isolation.evidence_record(report)["name"] == "isolation_macos_uid"
    linux = isolation.IsolationReport("PASS", "ok", {"platform": "Linux"})
    assert isolation.evidence_record(linux)["name"] == "isolation_linux_uid"


def test_blocker_reports_missing_setpriv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    assert isolation.blocker() == "requires the setpriv executable from util-linux"


def test_isolation_evidence_record_has_canonical_receipt() -> None:
    report = isolation.IsolationReport(
        "PASS",
        "uid-dropped subject had execute-only access",
        {"subject_returncode": 0, "harness_side_effect_count": 1},
    )
    record = isolation.evidence_record(report)
    body = dict(record)
    body.pop("receipt")
    assert record["receipt"] == receipt(body)


def test_checked_in_linux_isolation_evidence_receipt() -> None:
    rec = json.loads(_EVIDENCE.read_text())
    body = dict(rec)
    body.pop("receipt")
    assert rec["receipt"] == receipt(body)
    assert rec["status"] == "PASS"
    assert rec["reason"] == "uid-dropped subject had execute-only access"
    details = rec["details"]
    assert isinstance(details, dict)
    assert details["harness_side_effect_count"] == 1
    subject = details["subject"]
    assert isinstance(subject, dict)
    assert isolation.subject_passed(subject)


def test_checked_in_macos_isolation_evidence_receipt() -> None:
    if not _MACOS_EVIDENCE.exists():
        pytest.skip("native macOS isolation evidence absent")
    rec = json.loads(_MACOS_EVIDENCE.read_text())
    body = dict(rec)
    body.pop("receipt")
    assert rec["receipt"] == receipt(body)
    assert rec["name"] == "isolation_macos_uid"
    assert rec["status"] == "PASS"
    details = rec["details"]
    assert isinstance(details, dict)
    assert details["platform"] == "Darwin"
    assert details["drop_method"] == "python-setreuid"
    assert details["harness_side_effect_count"] == 1
    subject = details["subject"]
    assert isinstance(subject, dict)
    assert isolation.subject_passed(subject)
