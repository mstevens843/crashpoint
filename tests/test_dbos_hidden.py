"""DBOS hidden-barrier helpers that do not require Postgres or DBOS."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from crashpoint.canonical import receipt
from crashpoint.harness import dbos_hidden
from crashpoint.harness.dbos_hidden import DbSnapshot, db_agrees
from crashpoint.model.barriers import BARRIER_IDS

_EVIDENCE = Path(__file__).resolve().parents[1] / "evidence"
_FILES = {
    "dbos_step_output_uncommitted": ("dbos_hidden_uncommitted.json", "duplicated"),
    "dbos_step_output_committed_before_resume": ("dbos_hidden_committed.json", "exactly_once"),
    "dbos_workflow_outcome_uncommitted": ("dbos_hidden_outcome.json", "exactly_once"),
    "dbos_duplicate_workflow_name_recovery": ("dbos_hidden_dupname.json", "diverged"),
}


def test_hidden_barriers_are_disjoint_and_predicted() -> None:
    assert set(dbos_hidden.HIDDEN_BARRIERS).isdisjoint(BARRIER_IDS)
    for barrier in dbos_hidden.HIDDEN_BARRIERS:
        assert dbos_hidden._PREDICTED[barrier].value == _FILES[barrier][1]
        assert len(dbos_hidden._PREDICTION_RULES[barrier]) > 40
        assert len(dbos_hidden._DB_RULES[barrier]) > 20


def test_duplicate_name_modules_are_valid_python(tmp_path: Path) -> None:
    dbos_hidden.write_duplicate_modules(tmp_path)
    for name in ("cp_billing.py", "cp_shipping.py"):
        source = (tmp_path / name).read_text()
        tree = ast.parse(source)
        names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        assert "process" in names, name
    assert '"module": "billing"' in (tmp_path / "cp_billing.py").read_text()
    assert '"module": "shipping"' in (tmp_path / "cp_shipping.py").read_text()
    # Only the billing step crashes, and only when the subject process arms it.
    assert "SIGKILL" in (tmp_path / "cp_billing.py").read_text()
    assert "SIGKILL" not in (tmp_path / "cp_shipping.py").read_text()


def _snap(status: str, *outputs: str) -> DbSnapshot:
    return DbSnapshot(status, "hidden_workflow", 0, tuple(outputs))


def test_db_agrees_names_each_mechanism() -> None:
    both = ("effect_step", "sentinel_step")
    assert db_agrees(
        "dbos_step_output_uncommitted", _snap("PENDING"), _snap("SUCCESS", *both), "done", ""
    )
    assert not db_agrees(
        "dbos_step_output_uncommitted",
        _snap("PENDING", "effect_step"),
        _snap("SUCCESS", *both),
        "done",
        "",
    )
    assert db_agrees(
        "dbos_step_output_committed_before_resume",
        _snap("PENDING", "effect_step"),
        _snap("SUCCESS", *both),
        "done",
        "",
    )
    assert db_agrees(
        "dbos_workflow_outcome_uncommitted",
        _snap("PENDING", *both),
        _snap("SUCCESS", *both),
        "done",
        "",
    )
    assert not db_agrees(
        "dbos_workflow_outcome_uncommitted", _snap("SUCCESS", *both), _snap("SUCCESS", *both),
        "done", "",
    )
    assert db_agrees(
        "dbos_duplicate_workflow_name_recovery",
        _snap("PENDING"),
        _snap("SUCCESS", "shipping_charge"),
        "shipping",
        "cp_shipping",
    )
    assert not db_agrees(
        "dbos_duplicate_workflow_name_recovery",
        _snap("PENDING"),
        _snap("SUCCESS", "billing_charge"),
        "billing",
        "cp_billing",
    )


def test_sqlalchemy_url_uses_psycopg_driver() -> None:
    assert dbos_hidden._sqlalchemy_url("postgresql://u:p@h:5433/db").startswith(
        "postgresql+psycopg://"
    )
    assert dbos_hidden._sqlalchemy_url("sqlite:///x.db") == "sqlite:///x.db"


@pytest.mark.parametrize("barrier", list(_FILES))
def test_checked_in_dbos_hidden_evidence_receipt(barrier: str) -> None:
    filename, predicted = _FILES[barrier]
    path = _EVIDENCE / filename
    if not path.exists():
        pytest.skip(f"{filename} absent")
    rec = json.loads(path.read_text())
    body = dict(rec)
    body.pop("receipt")
    assert rec["receipt"] == receipt(body)
    assert rec["runtime"] == "dbos"
    assert rec["barrier"] == barrier
    assert rec["barrier_family"] == "hidden"
    assert rec["predicted"] == predicted
    assert rec["modal"] == predicted
    assert rec["agrees"] is True
    assert rec["db_agrees_count"] == rec["k"]
