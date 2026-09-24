"""Bounded real-server MCP crash/readback capture. No subject decision logic is patched."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, BinaryIO

from crashpoint.canonical import canonicalize

from .reality_layer_common import (
    FORGED,
    HISTORICAL,
    INVENTORY,
    PIN,
    SUBJECT_REQUIRED,
    Profile,
    digest,
    encoded,
    need,
    parse,
)

ROOT = Path(__file__).resolve().parents[3]
DEADLINE = 20.0


def write(path: Path, value: Any) -> None:
    with path.open("wb") as handle:
        handle.write(encoded(value))
        handle.flush()
        os.fsync(handle.fileno())


def append(path: Path, value: Any) -> None:
    with path.open("ab") as handle:
        handle.write(json.dumps(value, sort_keys=True, allow_nan=False).encode() + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def safe_env() -> dict[str, str]:
    # Allowlist: never inherit model keys, live provider configuration, or NODE_OPTIONS.
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "PYTHONPATH": str(ROOT / "src"),
        "REALITY_LIVE": "0",
        "PC_ADAPTER_DRY_RUN": "1",
        "REALITY_MCP_TOKEN": secrets.token_hex(32),
    }


def source_inventory(subject: Path, profile: Profile = HISTORICAL) -> dict[str, str]:
    head = subprocess.check_output(
        ["git", "-C", str(subject), "rev-parse", "HEAD"], text=True, timeout=15
    )
    need(head.strip() == profile.pin, "subject_pin")
    paths = subprocess.check_output(
        ["git", "-C", str(subject), "ls-tree", "-rz", "--name-only", profile.pin], timeout=15
    ).split(b"\0")
    inventory = {}
    for raw in paths:
        if not raw:
            continue
        name = raw.decode()
        actual = (subject / name).read_bytes()
        committed = subprocess.check_output(
            ["git", "-C", str(subject), "show", f"{profile.pin}:{name}"], timeout=15
        )
        need(actual == committed, "dirty_subject", name)
        inventory[name] = digest(actual)
    need(set(SUBJECT_REQUIRED) <= inventory.keys(), "subject_inventory")
    return inventory


def freeze(subject: Path, plan_path: Path, profile: Profile = HISTORICAL) -> None:
    need(not plan_path.exists(), "plan_already_exists")
    subject_hashes = source_inventory(subject, profile)
    own = {name: digest((ROOT / name).read_bytes()) for name in profile.own_sources}
    predictions = {
        "clean": ["SUCCEEDED", False, 1],
        "post_crash": ["STARTED", False, 1],
        "pre_crash": ["STARTED", False, 0],
        "ordinary_error": ["FAILED", False, 1],
        "unknown_error": ["UNKNOWN", True, 1],
        "corrupt": [None, None, 1],
        "missing": [None, None, 1],
    }
    if profile.modern_reconciliation:
        predictions["post_crash"] = ["UNKNOWN", True, 1]
    predictions = {case: predictions[case] for case, _ in profile.cases}
    write(
        plan_path,
        {
            "version": 1,
            "frozen_at": dt.datetime.now(dt.UTC).isoformat(),
            "base": profile.base,
            "subject_pin": profile.pin,
            "cases": dict(profile.cases),
            "inventory": profile.inventory,
            "own_sources": own,
            "subject_sources": subject_hashes,
            "predictions": predictions,
            "method": "source-informed, not blinded; public MCP via upstream client",
            "barriers": {
                "pre_crash": "adapter entered; STARTED raw bytes; readable empty receiver",
                "post_crash": "receiver fsync ack; fresh observer read; raw STARTED before SIGKILL",
            },
            "deadline_seconds": DEADLINE,
            "properties": {
                "clean": "queryable terminal identity and one matching effect",
                "crashes": "unresolved retained action should require reconciliation",
                "retry": "original consumed/lost plan must not cause another effect",
                "errors": (
                    "FAILED does not establish external non-execution; compare explicit UNKNOWN"
                ),
                "storage": "missing/corrupt subject history must not be mistaken for no effect",
            },
            "falsification": (
                "Different API status/flag/effect counts falsify the prediction. "
                "Missing barrier, binding, process kill, or observer evidence "
                "invalidates the trial."
            ),
            "source_distribution": (
                "No upstream license found; source omitted. Pinned hash inventory "
                "requires separate retrieval; evidence verification is offline."
            ),
        },
    )


class Owned:
    def __init__(self, directory: Path, env: dict[str, str]) -> None:
        self.directory = directory
        self.env = env
        self.children: list[tuple[str, subprocess.Popen[bytes]]] = []
        self.handles: list[BinaryIO] = []

    def start(
        self, name: str, command: list[str], extra: dict[str, str] | None = None
    ) -> subprocess.Popen[bytes]:
        stdout = (self.directory / f"{name}.stdout").open("wb")
        self.handles.append(stdout)
        stderr = (self.directory / f"{name}.stderr").open("wb")
        self.handles.append(stderr)
        process = subprocess.Popen(
            command,
            cwd=self.directory,
            env={**self.env, **(extra or {})},
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
        )
        self.children.append((name, process))
        append(
            self.directory / "progress.jsonl", {"event": "spawn", "name": name, "pid": process.pid}
        )
        return process

    def wait_event(self, name: str, process: subprocess.Popen[bytes], event: str) -> dict[str, Any]:
        deadline = time.monotonic() + DEADLINE
        while time.monotonic() < deadline:
            raw = (self.directory / f"{name}.stdout").read_bytes()
            for line in raw.splitlines():
                try:
                    value = parse(line)
                except ValueError:
                    continue
                if isinstance(value, dict) and value.get("event") == event:
                    return value
            need(
                process.poll() is None,
                "child_exited_before_barrier",
                f"{name}: {process.returncode}",
            )
            time.sleep(0.02)
        raise TimeoutError(f"deadline waiting for {name}/{event}")

    def stop(self, process: subprocess.Popen[bytes], *, kill: bool = False) -> None:
        if process.poll() is None:
            if kill:
                process.kill()
            else:
                process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        else:
            process.wait(timeout=3)

    def close(self) -> list[dict[str, Any]]:
        errors = []
        for name, process in reversed(self.children):
            try:
                self.stop(process)
            except (OSError, subprocess.TimeoutExpired) as exc:
                errors.append(f"{name}: {type(exc).__name__}")
        for handle in self.handles:
            try:
                handle.close()
            except OSError as exc:
                errors.append(f"log close: {type(exc).__name__}")
        records = self.process_records()
        need(not errors and all(x["reaped"] for x in records), "cleanup_failed", str(errors))
        return records

    def process_records(self) -> list[dict[str, Any]]:
        return [
            {"name": name, "pid": p.pid, "returncode": p.poll(), "reaped": p.poll() is not None}
            for name, p in self.children
        ]


def post(url: str, value: Any) -> dict[str, Any]:
    request = urllib.request.Request(url, encoded(value), {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:
        result = parse(response.read())
    need(isinstance(result, dict), "receiver_response_shape")
    return dict(result)


def snapshot(directory: Path, ledger: Path, phase: str) -> None:
    try:
        raw = ledger.read_bytes()
    except OSError as exc:
        write(
            directory / f"subject-{phase}.json",
            {"availability": "unavailable", "error_type": type(exc).__name__, "errno": exc.errno},
        )
    else:
        (directory / f"subject-{phase}.raw").write_bytes(raw)
        write(
            directory / f"subject-{phase}.json",
            {"availability": "readable", "sha256": digest(raw), "bytes": len(raw)},
        )


def trial_record(run_id: str, trial_id: str, failpoint: str = "") -> dict[str, Any]:
    """Allocate identity before entering a trial, so finalization cannot erase the attempt."""
    return {
        "version": 1,
        "run_id": run_id,
        "trial_id": trial_id,
        "case": trial_id.rsplit("-", 1)[0],
        "evidence_valid": False,
        "errors": [],
        "processes": [],
        "findings": None,
        "failpoint": failpoint,
    }


def retention_error(record: dict[str, Any], stage: str, error: Exception, artifact: str) -> None:
    record["evidence_valid"] = False
    record["findings"] = None
    record["errors"].append(
        {
            "stage": stage,
            "artifact": artifact,
            "type": type(error).__name__,
            "errno": error.errno if isinstance(error, OSError) else None,
            # OSError's filename can contain private paths; artifact names are retained above.
            "message": (error.strerror or str(error)) if isinstance(error, OSError) else str(error),
        }
    )


def artifact_inventory(directory: Path, record: dict[str, Any]) -> None:
    """Keep every available hash, including hashes obtained before an iterator fails."""
    artifacts: dict[str, str] = {}
    record["artifacts"] = artifacts
    try:
        for path in directory.iterdir():
            if path.name in {"receipt.json", "partial-receipt.json"}:
                continue
            try:
                mode = path.stat(follow_symlinks=False).st_mode
            except OSError as exc:
                retention_error(record, "artifact_stat", exc, path.name)
                continue
            if not stat.S_ISREG(mode):
                retention_error(
                    record, "artifact_classification", ValueError("not a regular file"), path.name
                )
                continue
            try:
                artifacts[path.name] = digest(path.read_bytes())
            except OSError as exc:
                retention_error(record, "artifact_read", exc, path.name)
    except OSError as exc:
        retention_error(record, "artifact_enumeration", exc, ".")


def retain_trial_receipt(directory: Path, record: dict[str, Any], failpoint: str = "") -> None:
    try:
        if failpoint == "retention":
            raise OSError("injected final receipt retention failure")
        write(directory / "receipt.json", record)
    except Exception as exc:
        retention_error(record, "receipt_primary", exc, "receipt.json")
        try:
            write(directory / "partial-receipt.json", record)
        except Exception as fallback_exc:
            retention_error(record, "receipt_fallback", fallback_exc, "partial-receipt.json")
            # The caller still owns this record and can retain it in the batch manifest.
            print(f"Neither receipt destination writable: {record['trial_id']}", file=sys.stderr)


def finalize_trial(
    bundle: Path,
    directory: Path,
    record: dict[str, Any],
    failpoint: str = "",
    profile: Profile = HISTORICAL,
) -> None:
    artifact_inventory(directory, record)
    if record["evidence_valid"]:
        from .reality_layer_verify import derive_trial

        try:
            record["findings"] = derive_trial(bundle, record, profile)
        except Exception as exc:
            retention_error(record, "derive_trial", exc, ".")
    retain_trial_receipt(directory, record, failpoint)


def trial(
    bundle: Path,
    subject: Path,
    scratch: Path,
    run_id: str,
    trial_id: str,
    failpoint: str = "",
    *,
    record: dict[str, Any] | None = None,
    profile: Profile = HISTORICAL,
) -> dict[str, Any]:
    case = trial_id.rsplit("-", 1)[0]
    directory = bundle / "trials" / trial_id
    if record is None:
        record = trial_record(run_id, trial_id, failpoint)
    owned = Owned(directory, safe_env())
    runtime: subprocess.Popen[bytes] | None = None
    receiver_url: str | None = None
    work = scratch / trial_id
    ledger = work / "subject" / "data" / "action-ledger.json"

    def event(event_name: str, **fields: Any) -> None:
        append(directory / "progress.jsonl", {"event": event_name, **fields})

    def observe(phase: str) -> dict[str, Any]:
        process = owned.start(
            f"observer-{phase}",
            [
                sys.executable,
                "-m",
                "crashpoint.harness.reality_layer_observer",
                str(work / "receiver" / "effects.jsonl"),
                str(directory / f"receiver-{phase}.jsonl"),
                "--failpoint",
                failpoint if phase == "before_recovery" else "",
            ],
        )
        rc = process.wait(timeout=DEADLINE)
        need(rc == 0, "observer_exit", str(rc))
        result = parse((directory / f"observer-{phase}.stdout").read_bytes())
        need(isinstance(result, dict) and result.get("pid") == process.pid, "observer_identity")
        need(
            result.get("availability") == "readable" and result.get("valid_chain") is True,
            "observer_unverified",
        )
        write(directory / f"observation-{phase}.json", result)
        event("observed", phase=phase, pid=process.pid, sha256=result["sha256"])
        return dict(result)

    def start_runtime(name: str) -> tuple[subprocess.Popen[bytes], str]:
        if profile.followup:
            event(
                "runtime_start",
                name=name,
                state_key=f"{run_id}/{trial_id}",
                ledger_sha256=digest(ledger.read_bytes()) if ledger.exists() else None,
            )
        p = owned.start(
            name,
            ["node", str(ROOT / "runtime/reality-layer/shim.cjs")],
            {
                "CP_SUBJECT": str(work / "subject"),
                "CP_CALLER": str(directory / "caller.json"),
                "CP_RECEIVER": receiver_url or "",
                "CP_CASE": case,
                "CP_FAILPOINT": failpoint,
            },
        )
        ready = owned.wait_event(name, p, "runtime_ready")
        need(ready["node"] == "v22.22.1", "node_pin")
        return p, f"http://127.0.0.1:{ready['port']}"

    def call(
        name: str, tool: str, arguments: dict[str, Any], url: str, *, asynchronous: bool = False
    ) -> Any:
        write(directory / f"api-{name}.request.json", {"name": tool, "arguments": arguments})
        p = owned.start(
            f"client-{name}",
            ["node", str(ROOT / "runtime/reality-layer/client.cjs")],
            {
                "CP_SUBJECT": str(work / "subject"),
                "CP_BASE_URL": url,
                "CP_REQUEST": str(directory / f"api-{name}.request.json"),
                "CP_TRANSCRIPT": str(directory / f"api-{name}.jsonl"),
            },
        )
        if asynchronous:
            return p
        p.wait(timeout=DEADLINE)
        result = parse((directory / f"client-{name}.stdout").read_bytes())
        event("api_completed", name=name, returncode=p.returncode)
        return result

    try:
        directory.mkdir(parents=True)
        event("trial_started")
        work.mkdir()
        # Only clean tracked source files, no user data/configuration or launch script.
        source = work / "subject"
        source.mkdir()
        expected_sources = parse((bundle / "plan.json").read_bytes())["subject_sources"]
        execution_hashes = {}
        for name, sha in expected_sources.items():
            if name in {"server.js", "package.json"} or name.startswith(("src/", "integrations/")):
                raw_source = (subject / name).read_bytes()
                need(digest(raw_source) == sha, "trial_source_changed", name)
                target = source / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw_source)
                execution_hashes[name] = digest(target.read_bytes())
        write(directory / "execution-source-hashes.json", execution_hashes)
        (source / "data").mkdir()
        event("source_copied")
        receiver = owned.start(
            "receiver",
            [
                sys.executable,
                "-m",
                "crashpoint.harness.reality_layer_receiver",
                str(work / "receiver"),
            ],
        )
        ready = owned.wait_event("receiver", receiver, "receiver_ready")
        receiver_url = f"http://127.0.0.1:{ready['port']}"
        if failpoint == "reset":
            raise OSError("injected initial receiver reset failure")
        initial = observe("initial")
        need(initial["effects"] == 0, "receiver_not_empty")
        runtime, url = start_runtime("runtime-first")
        planned = call("plan", "reality.plan", {"text": "침실 불 켜줘"}, url)
        need(planned["ok"] is True, "plan_client_failed")
        plan = planned["value"]
        need(
            plan["ok"] is True and plan["decision"] == "auto" and len(plan["steps"]) == 1,
            "plan_not_single_auto",
        )
        step = plan["steps"][0]
        ir = step["action_ir"]
        caller = {
            "version": 1,
            "run_id": run_id,
            "trial_id": trial_id,
            "plan_id": plan["plan_id"],
            "action_id": ir["id"],
            "action_ir": ir,
            "payload": {
                "device_id": step["device"],
                "capability": step["capability"],
                "args": step.get("args", {}),
            },
            "subject_pin": profile.pin,
        }
        write(directory / "caller.json", caller)
        need(parse((directory / "caller.json").read_bytes()) == caller, "caller_readback")
        event("caller_retained", action_id=ir["id"], plan_id=plan["plan_id"])
        dispatch = call(
            "execute", "reality.execute", {"plan_id": caller["plan_id"]}, url, asynchronous=True
        )
        if case in {"pre_crash", "post_crash"}:
            barrier = owned.wait_event(
                "runtime-first",
                runtime,
                "pre_effect_barrier" if case == "pre_crash" else "post_effect_barrier",
            )
            write(directory / "barrier.json", barrier)
            snapshot(directory, ledger, "before_recovery")
            before = observe("before_recovery")
            need(before["effects"] == (0 if case == "pre_crash" else 1), "crash_effect_premise")
            stored = parse(ledger.read_bytes())
            need(
                len(stored) == 1
                and stored[0]["action_id"] == caller["action_id"]
                and stored[0]["status"] == "STARTED",
                "crash_started_premise",
            )
            if profile.followup:
                need(
                    before["attempts"][0]["intent_id"] == caller["action_id"]
                    and before["attempts"][0]["payload_digest"]
                    == digest(canonicalize(caller["payload"]).encode())
                    and before["attempts"][0]["attempt_id"] == barrier["attempt_id"],
                    "prekill_observer_binding",
                )
                event(
                    "started_readback",
                    action_id=caller["action_id"],
                    sha256=digest(ledger.read_bytes()),
                )
            event("kill_requested", pid=runtime.pid, signal="SIGKILL")
            owned.stop(runtime, kill=True)
            need(runtime.returncode == -9, "kill_not_observed")
            event("killed", pid=runtime.pid, returncode=runtime.returncode)
        dispatch.wait(timeout=DEADLINE)
        if case not in {"pre_crash", "post_crash"}:
            snapshot(directory, ledger, "before_recovery")
            before = observe("before_recovery")
        if case in {"corrupt", "missing"}:
            owned.stop(runtime)
            if case == "corrupt":
                ledger.write_bytes(b'{"truncated":')
            else:
                ledger.unlink()
            event(
                "subject_storage_mutation", operation=case, preserved="subject-before_recovery.raw"
            )
        snapshot(directory, ledger, "before_restart")
        if case in {"pre_crash", "post_crash", "corrupt", "missing"}:
            runtime, url = start_runtime("runtime-recovery")
        snapshot(directory, ledger, "after_restart")
        if failpoint == "status":
            owned.stop(runtime)
        call("status", "reality.action.get", {"action_id": caller["action_id"]}, url)
        if failpoint == "status":
            raise OSError("injected post-dispatch status transport failure; transcript retained")
        snapshot(directory, ledger, "after_query")
        if profile.followup:
            observe("after_restart")
        call("retry", "reality.execute", {"plan_id": caller["plan_id"]}, url)
        observe("after_retry")
        if case in {"pre_crash", "post_crash", "unknown_error"}:
            call(
                "reconcile",
                "reality.action.reconcile",
                profile.reconciliation_args(
                    caller["action_id"], before["sha256"], before["effects"]
                ),
                url,
            )
            call("status_final", "reality.action.get", {"action_id": caller["action_id"]}, url)
            if profile.followup:
                snapshot(directory, ledger, "after_reconcile")
        if profile.probes(trial_id):
            for route in ["mcp", "rest"]:
                arguments = {"action_id": caller["action_id"], **FORGED}
                if route == "rest":
                    arguments["evidence"] = {"source": "caller", "note": "forged evidence"}
                call(
                    f"forged_{route}",
                    "reality.action.reconcile" if route == "mcp" else "/api/actions/reconcile",
                    arguments,
                    url,
                )
                call(
                    f"status_{route}", "reality.action.get", {"action_id": caller["action_id"]}, url
                )
                snapshot(directory, ledger, f"probe_{route}")
                observe(f"probe_{route}")
        seal = post(receiver_url + "/seal", {})
        write(directory / "seal.json", seal)
        event("receiver_sealed")
        observe("final")
        shutil.copyfile(work / "receiver" / "requests.jsonl", directory / "receiver-requests.jsonl")
        snapshot(directory, ledger, "final")
        event("trial_observations_complete")
        record["evidence_valid"] = True
    except Exception as exc:
        # Keep exception detail without private paths; raw stderr is separately archived.
        record["errors"].append(
            {"type": type(exc).__name__, "message": str(exc).replace(str(work), "<scratch>")}
        )
    finally:
        try:
            record["processes"] = owned.close()
        except Exception as exc:
            record["processes"] = owned.process_records()
            retention_error(record, "cleanup", exc, ".")
        # Best-effort raw fallback is explicitly controller capture, never an observer report.
        if not record["evidence_valid"]:
            for source_file, name in [
                (work / "receiver" / "effects.jsonl", "controller-fallback-effects.raw"),
                (ledger, "controller-fallback-subject.raw"),
            ]:
                try:
                    (directory / name).write_bytes(source_file.read_bytes())
                except OSError as exc:
                    record["errors"].append(
                        {"type": type(exc).__name__, "capture": name, "errno": exc.errno}
                    )
        finalize_trial(bundle, directory, record, failpoint, profile)
    return record


def capture(
    subject: Path,
    output: Path,
    plan_path: Path,
    *,
    failpoint: str = "",
    only: str | None = None,
    exploratory: bool = False,
    profile: Profile = HISTORICAL,
) -> int:
    need(not output.exists(), "output_exists", str(output))
    output.mkdir(parents=True)
    run_id = str(uuid.uuid4())
    manifest: dict[str, Any] = {
        "version": 1,
        "run_id": run_id,
        "base": profile.base,
        "subject_pin": profile.pin,
        "state": "running",
        "kind": "exploratory" if exploratory else "confirmatory",
        "inventory": [only] if only else profile.inventory,
        "cases": dict(profile.cases),
        "plan_sha256": None,
        "source_hashes": {},
        "started_at": dt.datetime.now(dt.UTC).isoformat(),
        "trials": [],
        "errors": [],
        "summary": None,
    }
    write(output / "manifest.json", manifest)  # before any child/trial
    (output / "journal.jsonl").touch()
    scratch = ROOT / "work" / f"reality-layer-{run_id}"
    try:
        if failpoint == "source":
            raise OSError("injected source read failure")
        plan_raw = plan_path.read_bytes()
        plan = parse(plan_raw)
        need(
            plan["subject_sources"] == source_inventory(subject, profile), "subject_source_changed"
        )
        for name in profile.own_sources:
            need(
                plan["own_sources"][name] == digest((ROOT / name).read_bytes()),
                "frozen_source_changed",
                name,
            )
        need(
            subprocess.check_output(["node", "--version"], text=True, timeout=10).strip()
            == "v22.22.1",
            "node_pin",
        )
        need(sys.version_info[:3] == (3, 12, 13), "python_pin")
        manifest["plan_sha256"] = digest(plan_raw)
        manifest["source_hashes"] = plan["own_sources"]
        (output / "plan.json").write_bytes(plan_raw)
        write(output / "manifest.json", manifest)
        scratch.mkdir(parents=True)
        for name in profile.own_sources:
            target = output / "sources" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        for trial_id in manifest["inventory"]:
            record = trial_record(run_id, trial_id, failpoint)
            manifest["trials"].append(record)
            write(output / "manifest.json", manifest)
            try:
                trial(
                    output,
                    subject,
                    scratch,
                    run_id,
                    trial_id,
                    failpoint,
                    record=record,
                    profile=profile,
                )
            except Exception as exc:
                # A finalizer bug must not remove the attempt or its in-memory observations.
                retention_error(record, "trial_finalization", exc, ".")
                retain_trial_receipt(output / "trials" / trial_id, record)
            try:
                if failpoint == "incremental":
                    raise OSError("injected incremental write failure")
                append(output / "journal.jsonl", record)
            except OSError as exc:
                manifest["errors"].append(
                    {"stage": "incremental", "type": type(exc).__name__, "message": str(exc)}
                )
            write(output / "manifest.json", manifest)
        valid = all(t["evidence_valid"] for t in manifest["trials"]) and not manifest["errors"]
        manifest["state"] = "complete" if valid else "invalid"
        manifest["summary"] = {
            "trials": len(manifest["trials"]),
            "valid": sum(t["evidence_valid"] is True for t in manifest["trials"]),
            "invalid": sum(t["evidence_valid"] is not True for t in manifest["trials"]),
            "findings": {t["trial_id"]: t["findings"] for t in manifest["trials"]},
        }
    except Exception as exc:
        manifest["state"] = "invalid"
        manifest["errors"].append({"stage": "run", "type": type(exc).__name__, "message": str(exc)})
    finally:
        try:
            if failpoint == "manifest":
                raise OSError("injected final manifest retention failure")
            write(output / "manifest.json", manifest)
            write(
                output / "bundle-receipt.json",
                {
                    "version": 1,
                    "run_id": run_id,
                    "manifest_sha256": digest((output / "manifest.json").read_bytes()),
                },
            )
        except OSError as exc:
            manifest["state"] = "invalid"
            manifest["errors"].append(
                {"stage": "final_retention", "type": type(exc).__name__, "message": str(exc)}
            )
            write(output / "partial-manifest.json", manifest)
            print("Final retention failed; partial-manifest.json retained", file=sys.stderr)
    print(
        json.dumps(
            {"run_id": run_id, "state": manifest["state"], "summary": manifest["summary"]},
            sort_keys=True,
        )
    )
    return 0 if manifest["state"] == "complete" else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True, type=Path)
    ap.add_argument("--plan", required=True, type=Path)
    ap.add_argument("--freeze", action="store_true")
    ap.add_argument("--audit-source", action="store_true")
    ap.add_argument("--output", type=Path)
    ap.add_argument(
        "--failpoint",
        choices=[
            "source",
            "manifest",
            "startup",
            "reset",
            "status",
            "observer_malformed",
            "observer_nonzero",
            "incremental",
            "retention",
        ],
        default="",
    )
    ap.add_argument("--only", choices=INVENTORY)
    ap.add_argument("--exploratory", action="store_true")
    args = ap.parse_args()
    try:
        if args.audit_source:
            inventory = source_inventory(args.subject.resolve())
            need(inventory == parse(args.plan.read_bytes())["subject_sources"], "source_audit")
            print(json.dumps({"subject_pin": PIN, "verified_files": len(inventory)}))
            return 0
        if args.freeze:
            freeze(args.subject.resolve(), args.plan.resolve())
            return 0
        need(args.output is not None, "output_required")
        need(not (args.only or args.failpoint) or args.exploratory, "partial_requires_exploratory")
        return capture(
            args.subject.resolve(),
            args.output.resolve(),
            args.plan.resolve(),
            failpoint=args.failpoint,
            only=args.only,
            exploratory=args.exploratory,
        )
    except Exception as exc:
        print(f"capture failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
