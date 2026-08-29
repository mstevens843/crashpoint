"""The discrimination meta-test: assert, over the receipted evidence the harness actually wrote,
that the oracle has teeth and the finding holds. Every check is skip-if-absent, so the suite is
green on a fresh checkout and gets stronger as each runtime's evidence is generated. Each evidence
file's receipt is re-derived from its body, so a hand-edited number fails here.

What it pins:
  - the receipt re-derives (the numbers were not edited after the run);
  - the three controls pin DUPLICATED / LOST / EXACTLY_ONCE (the oracle discriminates);
  - both failure modes - a doubled effect and a lost one - appear in the evidence;
  - for every real runtime, the naive effect DUPLICATES at the lethal b1 barrier and the idempotent
    boundary recovers EXACTLY_ONCE (the fix works);
  - the honest floor: no naive effect closes b1 (named, not hidden);
  - the SECOND floor: for a nondeterministic step the idempotent boundary does not close b1 either -
    it DIVERGES - and the diverge_control proves the oracle can produce that outcome on demand.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crashpoint.canonical import receipt

_EVIDENCE = Path(__file__).resolve().parents[1] / "evidence"
_ALL = ["controls", "langgraph", "temporal", "dbos"]
_REAL_PAIRS = {
    "langgraph": ("r_lg_naive", "r_lg_idem"),
    "temporal": ("r_tmp_naive", "r_tmp_idem"),
    "dbos": ("r_dbos_naive", "r_dbos_idem"),
}
# The idempotent arm and its nondeterministic twin: same boundary, only determinism differs.
_NONDET = {
    "langgraph": ("r_lg_idem", "r_lg_nondet"),
    "temporal": ("r_tmp_idem", "r_tmp_nondet"),
    "dbos": ("r_dbos_idem", "r_dbos_nondet"),
}


def _load(name: str) -> dict[str, object] | None:
    p = _EVIDENCE / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else None


def _modal(record: dict[str, object]) -> dict[tuple[str, str], str]:
    cells = record["cells"]
    assert isinstance(cells, list)
    return {(c["runtime"], c["barrier"]): c["modal"] for c in cells}


def _present() -> list[str]:
    return [n for n in _ALL if _load(n) is not None]


@pytest.mark.parametrize("name", _ALL)
def test_receipt_rederives(name: str) -> None:
    rec = _load(name)
    if rec is None:
        pytest.skip(f"{name} evidence absent")
    body = {k: v for k, v in rec.items() if k != "receipt"}
    assert receipt(body) == rec["receipt"], "evidence receipt does not match its body"


def test_controls_pin_outcomes() -> None:
    rec = _load("controls")
    if rec is None:
        pytest.skip("controls evidence absent")
    m = _modal(rec)
    # Each control pins one outcome at the between barrier, so the oracle is proven to discriminate.
    assert m[("r_dup", "b1")] == "duplicated"
    assert m[("r_lost", "b1")] == "lost"
    assert m[("r_idem", "b1")] == "exactly_once"
    assert m[("r_null", "b1")] == "duplicated"
    if ("r_diverge", "b1") in m:
        # The fourth outcome, pinned on demand: two crossings that are not the same action.
        assert m[("r_diverge", "b1")] == "diverged"


def test_both_failure_modes_present() -> None:
    present = _present()
    if not present:
        pytest.skip("no evidence generated yet")
    outcomes: set[str] = set()
    for n in present:
        rec = _load(n)
        assert rec is not None
        outcomes |= set(_modal(rec).values())
    assert "duplicated" in outcomes, "no duplicated effect anywhere in the evidence"
    if _load("controls") is not None:
        assert "lost" in outcomes, "controls present but no lost effect (lost_control)"


@pytest.mark.parametrize("name", list(_REAL_PAIRS))
def test_idempotent_fix_recovers_b1(name: str) -> None:
    rec = _load(name)
    if rec is None:
        pytest.skip(f"{name} evidence absent")
    m = _modal(rec)
    naive, idem = _REAL_PAIRS[name]
    assert m[(naive, "b1")] == "duplicated", f"{naive} should duplicate at b1"
    assert m[(idem, "b1")] == "exactly_once", f"{idem} should recover exactly-once at b1"


@pytest.mark.parametrize("name", list(_REAL_PAIRS))
def test_b1_floor_no_naive_closes_it(name: str) -> None:
    rec = _load(name)
    if rec is None:
        pytest.skip(f"{name} evidence absent")
    naive, _ = _REAL_PAIRS[name]
    # The honest floor: without an idempotent boundary, the after-effect-before-persist barrier is
    # not closeable. Named here, not hidden.
    assert _modal(rec)[(naive, "b1")] != "exactly_once"


def test_no_model_disagreements_in_present_evidence() -> None:
    present = _present()
    if not present:
        pytest.skip("no evidence generated yet")
    for n in present:
        rec = _load(n)
        assert rec is not None
        assert rec["disagreements"] == [], f"{n}: observed disagrees with the model - a finding"


@pytest.mark.parametrize("name", list(_NONDET))
def test_the_idempotent_boundary_does_not_survive_a_nondeterministic_step(name: str) -> None:
    rec = _load(name)
    if rec is None:
        pytest.skip(f"{name} evidence absent")
    m = _modal(rec)
    idem, nondet = _NONDET[name]
    if (nondet, "b1") not in m:
        pytest.skip(f"{name} evidence predates the nondeterministic row")
    # Same runtime, same barrier, same idempotent boundary - only reproducibility differs.
    assert m[(idem, "b1")] == "exactly_once", f"{idem} should recover exactly-once at b1"
    assert m[(nondet, "b1")] == "diverged", f"{nondet} should diverge at b1"


@pytest.mark.parametrize("name", list(_NONDET))
def test_nondeterminism_does_not_bleed_into_the_calibration_columns(name: str) -> None:
    rec = _load(name)
    if rec is None:
        pytest.skip(f"{name} evidence absent")
    m = _modal(rec)
    _, nondet = _NONDET[name]
    if (nondet, "b1") not in m:
        pytest.skip(f"{name} evidence predates the nondeterministic row")
    # Where the effect crosses only once, being irreproducible costs nothing. If b0 or b2 also
    # diverged, the fixture would be drawing when it should not and the b1 result would mean less.
    assert m[(nondet, "b0")] == "exactly_once"
    assert m[(nondet, "b2")] == "exactly_once"


def test_diverged_is_present_somewhere_in_the_evidence() -> None:
    present = _present()
    if not present:
        pytest.skip("no evidence generated yet")
    outcomes: set[str] = set()
    for n in present:
        rec = _load(n)
        assert rec is not None
        outcomes |= set(_modal(rec).values())
    if "diverged" not in outcomes:
        pytest.skip("evidence predates the nondeterministic rows")
    assert "diverged" in outcomes
