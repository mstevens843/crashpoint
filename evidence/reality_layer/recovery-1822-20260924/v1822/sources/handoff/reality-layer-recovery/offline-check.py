"""Run retained verifier and second audit with original checkouts/network/processes denied.

The parent copies evidence first, then invokes this script with Python -I -S. It loads only
the relocated retained sources. Audit hooks enforce these restrictions inside this process.
"""

from __future__ import annotations

import importlib
import json
import os
import runpy
import socket
import subprocess
import sys
import sysconfig
from pathlib import Path

bundle = Path(sys.argv[1]).resolve()
original_parent = Path(sys.argv[2]).resolve()
source = bundle / "v1822/sources/src"
sys.path.insert(0, str(source))
stdlib = Path(sysconfig.get_path("stdlib")).resolve()
sys.dont_write_bytecode = True
blocked = {"process": 0, "network": 0, "original_read": 0}


def guard(event: str, args: tuple[object, ...]) -> None:
    if event.startswith(("socket.", "subprocess.", "os.exec", "os.spawn")) or event in {
        "os.system",
        "os.fork",
        "os.posix_spawn",
        "pty.spawn",
    }:
        blocked["network" if event.startswith("socket.") else "process"] += 1
        raise PermissionError(event)
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        path = Path(os.fsdecode(args[0])).resolve()
        if path.is_relative_to(original_parent):
            blocked["original_read"] += 1
            raise PermissionError("original checkout read")
        if not path.is_relative_to(bundle) and not path.is_relative_to(stdlib):
            raise PermissionError("read outside bundle/stdlib")


sys.addaudithook(guard)
for probe in [
    lambda: socket.socket(),
    lambda: subprocess.run(["/usr/bin/true"], check=True),
    lambda: (original_parent / "crashpoint/pyproject.toml").read_bytes(),
]:
    try:
        probe()
    except PermissionError:
        pass
    else:
        raise AssertionError("denial probe escaped")

reality_layer_recovery = importlib.import_module("crashpoint.harness.reality_layer_recovery")

loaded = Path(reality_layer_recovery.__file__).resolve()
assert loaded.is_relative_to(source.resolve()), loaded
result = reality_layer_recovery.verify_comparison(bundle)
audit_path = bundle / "v1822/sources/handoff/reality-layer-recovery/independent-audit.py"
namespace = runpy.run_path(str(audit_path))
audit = namespace["audit"](bundle)
print(
    json.dumps(
        {
            "loaded_retained_verifier": str(loaded.relative_to(bundle)),
            "site_packages_disabled": sys.flags.no_site == 1,
            "isolated_python": sys.flags.isolated == 1,
            "denial_probes": blocked,
            "result": result,
            "second_audit": audit,
        },
        sort_keys=True,
    )
)
