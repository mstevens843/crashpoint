"""Linux UID isolation adversary helpers."""

from __future__ import annotations

import os
import platform
import shutil

import pytest

from crashpoint.adversaries import isolation


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
