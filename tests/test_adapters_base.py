"""Adapter primitives that sit outside the pure model."""

from __future__ import annotations

import sys

import pytest

from crashpoint.adapters.base import (
    ModelSamplerUnavailable,
    draw_memo,
    two_phase_key,
)


def test_uuid_memo_source_is_default_and_varies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRASHPOINT_NONDET_SOURCE", raising=False)
    assert draw_memo() != draw_memo()


def test_model_memo_source_requires_a_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRASHPOINT_NONDET_SOURCE", "model")
    monkeypatch.delenv("CRASHPOINT_MODEL_SAMPLER_CMD", raising=False)

    with pytest.raises(ModelSamplerUnavailable):
        draw_memo()


def test_model_memo_source_uses_configured_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRASHPOINT_NONDET_SOURCE", "model")
    monkeypatch.setenv(
        "CRASHPOINT_MODEL_SAMPLER_CMD",
        f"{sys.executable} -c \"import sys; sys.stdin.read(); print('sampled memo')\"",
    )

    assert draw_memo() == "sampled memo"


def test_unknown_memo_source_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRASHPOINT_NONDET_SOURCE", "clock")

    with pytest.raises(ModelSamplerUnavailable):
        draw_memo()


def test_two_phase_key_is_pre_call_and_stable() -> None:
    assert two_phase_key("order-1") == two_phase_key("order-1")
    assert two_phase_key("order-1") != two_phase_key("order-2")
