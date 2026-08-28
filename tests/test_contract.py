"""The purity contract, enforced against the model's source text itself.

A comment promising the predictor is pure is not enforcement. If a clock, a random draw, a
filesystem call, or a network call ever entered predict.py, the "prediction" would silently depend
on the machine it ran on and would no longer be a prediction. So this test parses each model module
with the ast library and fails if it imports or calls anything impure. Ported in spirit from the
durable-agent-outbox contract test, which reads its engine modules off disk and bans Date.now,
fetch, node:fs and friends; here the same discipline lands on a Python predictor.

ast is used rather than substring matching so that the prose in docstrings ("open()", "socket",
"the network") cannot trip the check: only real imports and real calls are inspected, never text.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

MODEL_DIR = Path(__file__).resolve().parent.parent / "src" / "crashpoint" / "model"

# The pure predictor modules. __main__.py is excluded on purpose: it prints for a human and is
# allowed to touch stdout, and it is not imported by anything the prediction depends on.
PURE_MODULES = ("layers.py", "runtimes.py", "barriers.py", "predict.py")

BANNED_IMPORT_ROOTS = frozenset(
    {
        "os", "sys", "io", "time", "datetime", "random", "secrets", "socket", "subprocess",
        "asyncio", "threading", "multiprocessing", "pathlib", "shutil", "tempfile", "ctypes",
        "http", "urllib", "requests", "httpx", "urllib3", "ssl", "select", "fcntl", "signal",
        "sqlite3", "pickle", "json",
    }
)
BANNED_CALL_NAMES = frozenset({"open", "eval", "exec", "__import__", "input", "compile"})


def _module_paths() -> list[Path]:
    return [MODEL_DIR / name for name in PURE_MODULES]


@pytest.mark.parametrize("path", _module_paths(), ids=[p.name for p in _module_paths()])
def test_module_exists_and_is_substantial(path: Path) -> None:
    # Without this a typo'd filename would make the whole suite pass while checking nothing.
    assert path.is_file(), f"pure model module missing: {path}"
    assert len(path.read_text()) > 400, f"suspiciously small model module: {path}"


@pytest.mark.parametrize("path", _module_paths(), ids=[p.name for p in _module_paths()])
def test_no_impure_imports(path: Path) -> None:
    tree = ast.parse(path.read_text(), filename=str(path))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in BANNED_IMPORT_ROOTS:
                    offenders.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in BANNED_IMPORT_ROOTS:
                offenders.append(f"from {node.module} import ...")
    assert not offenders, (
        f"{path.name} imports impure modules, so its prediction could depend on the machine it "
        f"runs on: {offenders}"
    )


@pytest.mark.parametrize("path", _module_paths(), ids=[p.name for p in _module_paths()])
def test_no_impure_calls(path: Path) -> None:
    tree = ast.parse(path.read_text(), filename=str(path))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in BANNED_CALL_NAMES:
                offenders.append(f"{fn.id}(...)")
    assert not offenders, f"{path.name} makes impure calls: {offenders}"


def test_predict_imports_only_model_siblings() -> None:
    # Exact-import pinning: predict.py may reach its own model data and the stdlib dataclasses,
    # nothing else. A new import here is a design decision that should be made deliberately, so
    # the test names the allowed set rather than a denylist.
    tree = ast.parse((MODEL_DIR / "predict.py").read_text())
    allowed = {".barriers", ".layers", ".runtimes", "dataclasses", "__future__"}
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            seen.add(("." if node.level else "") + (node.module or ""))
    assert seen <= allowed, f"predict.py imports outside the allowed model set: {seen - allowed}"


def test_predict_is_a_plain_function_not_a_generator() -> None:
    # The prediction must terminate with a value for every pair, so predict must not be async and
    # must not be a generator. Checked structurally so a refactor cannot quietly change it.
    src = (MODEL_DIR / "predict.py").read_text()
    tree = ast.parse(src)
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "predict" in fns
    assert not any(isinstance(n, ast.Yield | ast.YieldFrom) for n in ast.walk(fns["predict"]))
    assert not any(isinstance(n, ast.AsyncFunctionDef) for n in ast.walk(tree))
