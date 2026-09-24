"""Small, strict evidence primitives for the bounded Reality Layer experiment."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

PIN = "c9d1ca86969f5567cf771ab8a0f3247770a1dfb7"
BASE = "bb9cd47c4b0b02527aab7b369d17b32829cc4e20"
CASES = {
    "clean": 1,
    "post_crash": 3,
    "pre_crash": 1,
    "ordinary_error": 1,
    "unknown_error": 1,
    "corrupt": 1,
    "missing": 1,
}
INVENTORY = [f"{case}-{i}" for case, count in CASES.items() for i in range(count)]
OWN_SOURCES = [
    "src/crashpoint/harness/reality_layer.py",
    "src/crashpoint/harness/reality_layer_common.py",
    "src/crashpoint/harness/reality_layer_receiver.py",
    "src/crashpoint/harness/reality_layer_observer.py",
    "src/crashpoint/harness/reality_layer_verify.py",
    "tests/test_reality_layer.py",
    "runtime/reality-layer/contract_regression.py",
    "src/crashpoint/canonical.py",
    "src/crashpoint/ledger/core.py",
    "runtime/reality-layer/shim.cjs",
    "runtime/reality-layer/client.cjs",
    "runtime/reality-layer/dependencies.json",
    "LICENSE",
    "pyproject.toml",
    "uv.lock",
]
SUBJECT_REQUIRED = [
    "server.js",
    "package.json",
    "src/action-ledger.js",
    "src/executor.js",
    "src/action-ir.js",
    "src/adapters/index.js",
    "src/adapters/virtual-light-beta.js",
    "integrations/langgraph/reality-layer-adapter.js",
    "docs/EXTERNAL-RUNTIME.md",
    "tests/v1.8.test.js",
    "tests/v1.8.1.test.js",
]


class Invalid(ValueError):
    """Invalid or incomplete archived evidence, never a subject-runtime verdict."""


def need(condition: bool, code: str, detail: str = "") -> None:
    if not condition:
        raise Invalid(f"{code}: {detail}")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def encoded(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode()


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        need(key not in result, "json_duplicate_key", key)
        result[key] = value
    return result


def parse(raw: bytes | str) -> Any:
    try:
        return json.loads(
            raw, object_pairs_hook=_pairs, parse_constant=lambda s: need(False, "json_constant", s)
        )
    except (ValueError, UnicodeError) as exc:
        raise Invalid(f"json_parse: {exc}") from exc


def strict_equal(a: Any, b: Any) -> bool:
    # Python's True == 1 is not schema equality.
    return encoded(a) == encoded(b)


def confined(root: Path, relative: str) -> Path:
    need(type(relative) is str, "path_type")
    p = PurePosixPath(relative)
    need(
        bool(relative) and not p.is_absolute() and ".." not in p.parts and "\\" not in relative,
        "path_escape",
        relative,
    )
    current = root
    for part in p.parts:
        current = current / part
        need(not current.is_symlink(), "path_symlink", relative)
    need(current.resolve().is_relative_to(root.resolve()), "path_escape", relative)
    return current


def read(root: Path, relative: str) -> bytes:
    try:
        return confined(root, relative).read_bytes()
    except OSError as exc:
        raise Invalid(f"artifact_unavailable: {relative}: {exc.strerror}") from exc


def json_read(root: Path, relative: str) -> Any:
    return parse(read(root, relative))


@dataclass(frozen=True)
class Profile:
    """Explicit bounded protocol; historical defaults retain their original inventory."""

    pin: str = PIN
    base: str = BASE
    cases: tuple[tuple[str, int], ...] = tuple(CASES.items())
    own_sources: tuple[str, ...] = tuple(OWN_SOURCES)
    followup: bool = False
    modern_reconciliation: bool = False

    @property
    def inventory(self) -> list[str]:
        return [f"{case}-{i}" for case, count in self.cases for i in range(count)]

    def reconciliation_args(self, action_id: str, sha: str, count: int) -> dict[str, Any]:
        if self.modern_reconciliation:
            return {"action_id": action_id}
        return {
            "action_id": action_id,
            "outcome": "SUCCEEDED" if count == 1 else "FAILED",
            "evidence_note": f"fixture receiver sha256={sha}; effects={count}",
        }

    def probes(self, trial_id: str) -> bool:
        return self.modern_reconciliation and trial_id == "post_crash-0"


HISTORICAL = Profile()
FORGED = {"outcome": "SUCCEEDED", "evidence_note": "forged caller assertion"}
