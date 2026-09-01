"""Linux UID-drop adversary for the ledger boundary.

The default macOS fixture proves a socket privilege boundary: the subject has only the invoke socket
and cannot use control verbs there. This module is the stronger Linux path. When run as root on
Linux with `setpriv`, it starts the ledger with:

  * invoke socket in a traversable public directory;
  * control socket and store in a private 0700 directory;
  * subject process dropped to the `nobody` uid/gid.

The dropped subject is deliberately told the forbidden paths and tries to dump/reset/seal, connect
to the control socket, and read/write the store. The proof passes only if execute succeeds through
the invoke socket and every privileged operation is denied.

On non-Linux hosts, non-root Linux shells, or systems without `setpriv`/`nobody`, this command emits
BLOCKED with the precise reason. Use `--require` when a Linux isolation proof is mandatory.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import pwd
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from ..canonical import receipt
from ..ledger.daemon import control, execute

Status = Literal["PASS", "FAIL", "BLOCKED"]

_PAYLOAD = {"amount": 100, "to": "acct-attacker"}


@dataclass(frozen=True)
class IsolationReport:
    status: Status
    reason: str
    details: dict[str, object]


def blocker() -> str | None:
    """Return why the strong isolation proof cannot run on this host, or None if it can."""
    if platform.system() != "Linux":
        return "requires Linux UID isolation; this host is not Linux"
    if os.geteuid() != 0:
        return "requires root so setpriv can drop the subject process to nobody"
    if shutil.which("setpriv") is None:
        return "requires the setpriv executable from util-linux"
    try:
        pwd.getpwnam("nobody")
    except KeyError:
        return "requires a nobody user for the dropped subject process"
    return None


def subject_passed(report: dict[str, object]) -> bool:
    """Did the dropped subject have execute-only access and nothing else?"""
    denied = report.get("control_verbs_denied_on_invoke")
    return (
        report.get("execute_ok") is True
        and isinstance(denied, dict)
        and all(denied.get(op) is True for op in ("dump", "reset", "seal"))
        and report.get("control_socket_denied") is True
        and report.get("store_read_denied") is True
        and report.get("store_write_denied") is True
    )


def _request_denied(fn: Callable[[], object]) -> bool:
    try:
        result = fn()
    except OSError:
        return True
    if isinstance(result, dict):
        return result.get("ok") is not True
    return False


def _subject(invoke_path: str, control_path: str, store_path: str, intent: str) -> int:
    execute_ok = False
    execute_error = ""
    try:
        execute_ok = bool(execute(invoke_path, intent, None, _PAYLOAD).get("ok"))
    except OSError as exc:
        execute_error = repr(exc)

    def denied_control_verb(op: str) -> bool:
        return _request_denied(lambda: control(invoke_path, op))

    denied = {
        op: denied_control_verb(op)
        for op in ("dump", "reset", "seal")
    }
    report: dict[str, object] = {
        "execute_ok": execute_ok,
        "execute_error": execute_error,
        "control_verbs_denied_on_invoke": denied,
        "control_socket_denied": _request_denied(lambda: control(control_path, "dump")),
        "store_read_denied": _request_denied(lambda: Path(store_path).read_text()),
        "store_write_denied": _request_denied(lambda: Path(store_path).write_text("tamper\n")),
        "uid": os.getuid(),
        "gid": os.getgid(),
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if subject_passed(report) else 1


def _wait_ready(proc: subprocess.Popen[str]) -> None:
    assert proc.stdout is not None
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if "LEDGER_READY" in line:
            return
    raise RuntimeError("ledger daemon did not become ready")


def _start_ledger(invoke: Path, control_path: Path, store: Path) -> subprocess.Popen[str]:
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "crashpoint.ledger.daemon",
            "--invoke",
            str(invoke),
            "--control",
            str(control_path),
            "--store",
            str(store),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    _wait_ready(proc)
    return proc


def _stop(proc: subprocess.Popen[str]) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def prove_uid_isolation() -> IsolationReport:
    blocked = blocker()
    if blocked is not None:
        return IsolationReport("BLOCKED", blocked, {})

    nobody = pwd.getpwnam("nobody")
    with tempfile.TemporaryDirectory(prefix="crashpoint-isolation-") as tmp:
        root = Path(tmp)
        os.chmod(root, 0o711)
        public = root / "public"
        private = root / "private"
        public.mkdir()
        private.mkdir()
        os.chmod(public, 0o755)
        os.chmod(private, 0o700)
        invoke_path = public / "inv.sock"
        control_path = private / "ctl.sock"
        store_path = private / "ledger.jsonl"
        led = _start_ledger(invoke_path, control_path, store_path)
        try:
            cmd = [
                "setpriv",
                "--reuid",
                str(nobody.pw_uid),
                "--regid",
                str(nobody.pw_gid),
                "--clear-groups",
                sys.executable,
                "-m",
                "crashpoint.adversaries.isolation",
                "--subject",
                "--invoke",
                str(invoke_path),
                "--control",
                str(control_path),
                "--store",
                str(store_path),
                "--intent",
                "order-1",
            ]
            subject = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
            try:
                subject_report = cast(dict[str, object], json.loads(subject.stdout))
            except json.JSONDecodeError:
                subject_report = {"bad_stdout": subject.stdout, "stderr": subject.stderr}
            dump = control(str(control_path), "dump").get("dump", {})
        finally:
            _stop(led)

    side_effects = dump.get("side_effects", {}) if isinstance(dump, dict) else {}
    count = side_effects.get("order-1") if isinstance(side_effects, dict) else None
    passed = subject.returncode == 0 and subject_passed(subject_report) and count == 1
    return IsolationReport(
        "PASS" if passed else "FAIL",
        "uid-dropped subject had execute-only access" if passed else "uid isolation proof failed",
        {
            "subject_returncode": subject.returncode,
            "subject": subject_report,
            "harness_side_effect_count": count,
            "dropped_uid": nobody.pw_uid,
            "dropped_gid": nobody.pw_gid,
        },
    )


def render(report: IsolationReport) -> str:
    lines = [f"ISOLATION {report.status}: {report.reason}"]
    if report.details:
        lines.append(json.dumps(report.details, indent=2, sort_keys=True))
    return "\n".join(lines)


def evidence_record(report: IsolationReport) -> dict[str, object]:
    record: dict[str, object] = {
        "name": "isolation_linux_uid",
        "proof": "uid_dropped_subject_execute_only",
        "status": report.status,
        "reason": report.reason,
        "details": report.details,
    }
    record["receipt"] = receipt(record)
    return record


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--require", action="store_true", help="exit nonzero if the proof is blocked")
    ap.add_argument("--json", action="store_true", help="emit receipted evidence JSON")
    ap.add_argument("--evidence-path", help="write receipted evidence JSON to this path")
    ap.add_argument("--subject", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--invoke", help=argparse.SUPPRESS)
    ap.add_argument("--control", help=argparse.SUPPRESS)
    ap.add_argument("--store", help=argparse.SUPPRESS)
    ap.add_argument("--intent", default="order-1", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if args.subject:
        if args.invoke is None or args.control is None or args.store is None:
            ap.error("--subject requires --invoke, --control, and --store")
        return _subject(args.invoke, args.control, args.store, args.intent)

    report = prove_uid_isolation()
    if args.json or args.evidence_path:
        out = json.dumps(evidence_record(report), indent=2, sort_keys=True)
        if args.evidence_path:
            Path(args.evidence_path).write_text(out)
        print(out)
    else:
        print(render(report))
    if report.status == "PASS":
        return 0
    if report.status == "BLOCKED":
        return 2 if args.require else 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
