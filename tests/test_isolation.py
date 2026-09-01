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


def test_blocker_reports_non_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    assert isolation.blocker() == "requires Linux UID isolation; this host is not Linux"


def test_blocker_reports_missing_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(os, "geteuid", lambda: 501)
    assert isolation.blocker() == "requires root so setpriv can drop the subject process to nobody"


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
