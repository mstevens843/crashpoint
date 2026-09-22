"""Relocate evidence; deny site packages, original files, network and process operations."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
source = root / "evidence/reality_layer/reality-layer-retention-fixed-20260922"
relocation = Path(tempfile.mkdtemp(prefix="reality-layer-portable-"))
bundle = relocation / "bundle"
shutil.copytree(source, bundle)
bootstrap = r"""
import json, pathlib, sys
bundle = pathlib.Path(sys.argv[1])
forbidden = [pathlib.Path(p).resolve() for p in sys.argv[2:]]
def audit(event, arguments):
    if event.startswith("socket.") or event in {"subprocess.Popen", "os.system", "os.posix_spawn"}:
        raise RuntimeError("network/process operation denied by relocation control")
    if event == "open" and isinstance(arguments[0], (str, bytes)):
        path = pathlib.Path(arguments[0]).resolve()
        if any(path.is_relative_to(p) for p in forbidden):
            raise RuntimeError("original checkout access denied by relocation control")
sys.addaudithook(audit)
from crashpoint.harness import reality_layer_verify as verifier
assert pathlib.Path(verifier.__file__).resolve().is_relative_to(bundle.resolve())
result = verifier.verify_bundle(bundle)
print(json.dumps({"evidence_valid": result["evidence_valid"], "run_id": result["run_id"],
    "trials": result["summary"]["trials"], "loaded_verifier_from_relocated_bundle": True,
    "site_packages_disabled": True, "original_checkout_reads_denied": True,
    "network_and_process_operations_denied": True, "node_on_path": False}, sort_keys=True))
"""
env = {
    "PATH": str(relocation / "empty-bin"),
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONPATH": str(bundle / "sources/src"),
    "LANG": "C.UTF-8",
}
result = subprocess.run(
    [
        sys.executable,
        "-S",
        "-c",
        bootstrap,
        str(bundle),
        str(root),
        str(root.parent / "reality-layer-c9d1ca8-2026-09-22"),
    ],
    cwd=relocation,
    env=env,
    capture_output=True,
    text=True,
    timeout=30,
    check=False,
)
print(result.stdout, end="")
if result.stderr:
    print(result.stderr, file=sys.stderr, end="")
# Retain the relocation directory locally; do not put private paths in publication output.
(root / "work/reality-layer-retention-fix/relocation-local-path.txt").write_text(
    str(relocation) + "\n"
)
raise SystemExit(result.returncode)
