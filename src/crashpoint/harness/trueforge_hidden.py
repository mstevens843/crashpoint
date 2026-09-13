"""Measure TrueForge at the MCP-effect / durable-tool-response boundary.

The published TrueForge server is treated as a black box and configured only through its public
HTTP API. A deterministic local model emits one MCP tool call. The remote MCP sidecar commits the
effect through crashpoint's execute-only ledger socket, then kills the TrueForge process before it
can return the tool response.

Fresh-process inspection records the orphaned turn before doing anything to it. A second, explicit
turn then exercises the documented previous-running-turn path. This is not described as automatic
recovery: it is an application retry, measured with naive and idempotent receivers.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from ..canonical import receipt
from ..ledger.oracle import classify
from ..model.layers import Outcome
from .ledger_process import LedgerDaemon, LedgerHandle
from .wilson import wilson

ReceiverMode = Literal["naive", "idempotent"]
RECEIVER_MODES: tuple[ReceiverMode, ...] = ("naive", "idempotent")

_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FIXTURE_DIR = _ROOT / "runtime" / "trueforge"
_TRUEFORGE_VERSION = "0.2.0-rc.5"
_INTENT = "trueforge-effect"
_EXPECTED_CRASH = -int(signal.SIGKILL)
_LOG_TAIL_CHARS = 3_000


@dataclass(frozen=True)
class HttpResult:
    status: int
    body: object


@dataclass
class ManagedProcess:
    process: subprocess.Popen[str]
    log_path: Path

    def log_tail(self) -> str:
        try:
            value = self.log_path.read_text(errors="replace").strip()
        except OSError:
            return "<no log>"
        return value[-_LOG_TAIL_CHARS:] if value else "<empty>"


@dataclass(frozen=True)
class TurnSnapshot:
    status: str
    reason: str | None
    event_types: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason": self.reason,
            "event_types": list(self.event_types),
        }


@dataclass(frozen=True)
class TrueForgeTrial:
    receiver: ReceiverMode
    subject_returncode: int
    effects_before_restart: int
    attempts_before_restart: int
    orphan_after_restart: TurnSnapshot
    subscription_status_after_restart: int
    first_turn_after_retry: TurnSnapshot
    retry_turn: TurnSnapshot
    effects_after_retry: int
    attempts_after_retry: int
    observed: Outcome
    agrees: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "receiver": self.receiver,
            "subject_returncode": self.subject_returncode,
            "effects_before_restart": self.effects_before_restart,
            "attempts_before_restart": self.attempts_before_restart,
            "orphan_after_restart": self.orphan_after_restart.as_dict(),
            "subscription_status_after_restart": self.subscription_status_after_restart,
            "first_turn_after_retry": self.first_turn_after_retry.as_dict(),
            "retry_turn": self.retry_turn.as_dict(),
            "effects_after_retry": self.effects_after_retry,
            "attempts_after_retry": self.attempts_after_retry,
            "observed": self.observed.value,
            "agrees": self.agrees,
        }


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _decode_body(raw: bytes) -> object:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw.decode("utf-8", errors="replace")


def http_json(
    url: str,
    payload: object | None = None,
    *,
    timeout: float = 10.0,
) -> HttpResult:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {} if data is None else {"content-type": "application/json"}
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return HttpResult(int(response.status), _decode_body(response.read()))
    except urllib.error.HTTPError as error:
        return HttpResult(int(error.code), _decode_body(error.read()))


def _start_process(argv: list[str], env: dict[str, str], log_path: Path) -> ManagedProcess:
    log = log_path.open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            argv,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
    finally:
        log.close()
    return ManagedProcess(process, log_path)


def _stop(process: ManagedProcess | None) -> None:
    if process is None or process.process.poll() is not None:
        return
    process.process.terminate()
    try:
        process.process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.process.kill()
        process.process.wait(timeout=5)


def _wait_http(url: str, process: ManagedProcess, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    last = "no response"
    while time.monotonic() < deadline:
        if process.process.poll() is not None:
            raise RuntimeError(
                f"process exited with {process.process.returncode}; log:\n{process.log_tail()}"
            )
        try:
            result = http_json(url, timeout=1)
            if result.status == 200:
                return
            last = f"HTTP {result.status}: {result.body!r}"
        except (OSError, TimeoutError, urllib.error.URLError) as error:
            last = str(error)
        time.sleep(0.05)
    raise RuntimeError(f"timed out waiting for {url}: {last}; log:\n{process.log_tail()}")


def _trueforge_env(db: Path, port: int) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "STANDALONE": "true",
            "NODE_ENV": "production",
            "HOST": "127.0.0.1",
            "PORT": str(port),
            "SQLITE_PATH": str(db),
            "APP_DATA_DIR_SUFFIX": "crashpoint",
        }
    )
    return env


def start_trueforge(fixture: Path, db: Path, port: int, log_path: Path) -> ManagedProcess:
    executable = fixture / "node_modules" / ".bin" / "trueforge"
    if not executable.is_file():
        raise RuntimeError(f"TrueForge fixture is not installed: run npm install in {fixture}")
    process = _start_process(
        [str(executable), "--port", str(port)],
        _trueforge_env(db, port),
        log_path,
    )
    _wait_http(f"http://127.0.0.1:{port}/healthz", process)
    return process


def start_sidecar(
    fixture: Path,
    port: int,
    ledger: LedgerHandle,
    trueforge_pid: int,
    crash_marker: Path,
    receiver: ReceiverMode,
    log_path: Path,
) -> ManagedProcess:
    env = dict(os.environ)
    env.update(
        {
            "SIDECAR_PORT": str(port),
            "CRASHPOINT_LEDGER_INVOKE": ledger.invoke_path,
            "CRASHPOINT_SUBJECT_PID": str(trueforge_pid),
            "CRASHPOINT_CRASH_MARKER": str(crash_marker),
            "CRASHPOINT_EFFECT_MODE": receiver,
        }
    )
    process = _start_process(["node", str(fixture / "sidecar.mjs")], env, log_path)
    _wait_http(f"http://127.0.0.1:{port}/health", process)
    return process


def _expect(result: HttpResult, status: int, operation: str) -> dict[str, object]:
    if result.status != status or not isinstance(result.body, dict):
        raise RuntimeError(
            f"{operation} failed: expected HTTP {status}, got {result.status}: {result.body!r}"
        )
    return cast(dict[str, object], result.body)


def configure_trueforge(base_url: str, sidecar_url: str) -> None:
    model = {
        "manifest": {
            "type": "custom",
            "name": "crashpoint",
            "base_url": f"{sidecar_url}/v1",
            "models": [
                {
                    "model_id": "crashpoint-model",
                    "name": "crashpoint-model",
                    "properties": {"context_length": 4096, "max_output_tokens": 512},
                }
            ],
        }
    }
    mcp = {
        "manifest": {
            "type": "remote",
            "name": "crashpoint",
            "url": f"{sidecar_url}/mcp",
            "description": "Credential-free crashpoint effect fixture",
        }
    }
    _expect(
        http_json(f"{base_url}/api/v1/settings/model-providers", model),
        201,
        "create model provider",
    )
    _expect(
        http_json(f"{base_url}/api/v1/settings/mcp-servers", mcp),
        201,
        "create MCP server",
    )


def create_session(base_url: str) -> str:
    body = {
        "agent": {
            "spec": {
                "model": {"name": "crashpoint/crashpoint-model", "params": {"temperature": 0}},
                "instructions": "Call the effect tool exactly once, then report completion.",
                "mcp_servers": [
                    {
                        "name": "crashpoint",
                        "preload": True,
                        "require_approval_for_tools": [],
                    }
                ],
                "config": {
                    "iteration_limit": 4,
                    "sandbox": {"enabled": False},
                    "dynamic_sub_agents": {"enabled": False},
                    "context_management": {
                        "compaction": {"enabled": False},
                        "large_tool_response": {"enabled": False},
                    },
                    "generative_ui": {"enabled": False},
                    "ask_user_questions": {"enabled": False},
                },
            }
        }
    }
    response = _expect(http_json(f"{base_url}/api/v1/sessions", body), 201, "create session")
    data = response.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("id"), str):
        raise RuntimeError(f"create session response has no id: {response!r}")
    return str(data["id"])


def create_turn(base_url: str, session_id: str, message: str) -> str:
    body = {
        "input": [{"type": "user.message", "content": message}],
        "stream": False,
    }
    response = _expect(
        http_json(f"{base_url}/api/v1/sessions/{session_id}/turns", body, timeout=30),
        200,
        "create turn",
    )
    data = response.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("id"), str):
        raise RuntimeError(f"create turn response has no id: {response!r}")
    return str(data["id"])


def _turn_state(body: object) -> tuple[str, str | None]:
    if not isinstance(body, dict) or not isinstance(body.get("data"), dict):
        return "<invalid>", None
    state = body["data"].get("state")
    if not isinstance(state, dict):
        return "<invalid>", None
    reason = state.get("reason")
    return str(state.get("status", "<missing>")), str(reason) if reason is not None else None


def snapshot_turn(base_url: str, session_id: str, turn_id: str) -> TurnSnapshot:
    turn = http_json(f"{base_url}/api/v1/sessions/{session_id}/turns/{turn_id}")
    if turn.status != 200:
        raise RuntimeError(f"get turn failed: HTTP {turn.status}: {turn.body!r}")
    status, reason = _turn_state(turn.body)
    events = http_json(
        f"{base_url}/api/v1/sessions/{session_id}/turns/{turn_id}/events?limit=100&order=asc"
    )
    event_types: list[str] = []
    if events.status == 200 and isinstance(events.body, dict):
        data = events.body.get("data")
        if isinstance(data, list):
            event_types = [str(event.get("type")) for event in data if isinstance(event, dict)]
    return TurnSnapshot(status, reason, tuple(event_types))


def wait_terminal(
    base_url: str,
    session_id: str,
    turn_id: str,
    timeout: float = 30.0,
) -> TurnSnapshot:
    deadline = time.monotonic() + timeout
    last = TurnSnapshot("<unread>", None, ())
    while time.monotonic() < deadline:
        last = snapshot_turn(base_url, session_id, turn_id)
        if last.status != "running":
            return last
        time.sleep(0.05)
    raise RuntimeError(f"turn {turn_id} did not become terminal: {last.as_dict()!r}")


def _ledger_count(dump: dict[str, object], field: str) -> int:
    values = dump.get(field)
    if not isinstance(values, dict):
        return -1
    return int(values.get(_INTENT, 0))


def run_trial(
    ledger: LedgerHandle,
    root: Path,
    index: int,
    receiver: ReceiverMode,
    fixture: Path = DEFAULT_FIXTURE_DIR,
) -> TrueForgeTrial:
    trial_root = root / f"{receiver}-{index}"
    trial_root.mkdir(parents=True)
    db = trial_root / "trueforge.sqlite"
    crash_marker = trial_root / "crashed"
    trueforge_port = _free_port()
    sidecar_port = _free_port()
    trueforge_log = trial_root / "trueforge.log"
    sidecar_log = trial_root / "sidecar.log"
    base_url = f"http://127.0.0.1:{trueforge_port}"
    sidecar_url = f"http://127.0.0.1:{sidecar_port}"

    ledger.reset()
    server: ManagedProcess | None = None
    recovery: ManagedProcess | None = None
    sidecar: ManagedProcess | None = None
    try:
        server = start_trueforge(fixture, db, trueforge_port, trueforge_log)
        sidecar = start_sidecar(
            fixture,
            sidecar_port,
            ledger,
            server.process.pid,
            crash_marker,
            receiver,
            sidecar_log,
        )
        configure_trueforge(base_url, sidecar_url)
        session_id = create_session(base_url)
        first_turn_id = create_turn(base_url, session_id, "execute the fixture effect")

        try:
            returncode = server.process.wait(timeout=30)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                f"TrueForge was not killed at the effect boundary; logs:\n"
                f"trueforge:\n{server.log_tail()}\nsidecar:\n{sidecar.log_tail()}"
            ) from error
        before = ledger.dump()
        effects_before = _ledger_count(before, "side_effects")
        attempts_before = _ledger_count(before, "attempts")

        recovery = start_trueforge(fixture, db, trueforge_port, trueforge_log)
        orphan = snapshot_turn(base_url, session_id, first_turn_id)
        subscription = http_json(
            f"{base_url}/api/v1/sessions/{session_id}/turns/{first_turn_id}/subscribe",
            timeout=2,
        )

        retry_turn_id = create_turn(base_url, session_id, "retry the interrupted logical action")
        retry_turn = wait_terminal(base_url, session_id, retry_turn_id)
        first_after_retry = snapshot_turn(base_url, session_id, first_turn_id)

        ledger.seal()
        after = ledger.dump()
        effects_after = _ledger_count(after, "side_effects")
        attempts_after = _ledger_count(after, "attempts")
        observed = classify(_INTENT, after, Path(ledger.store_path))
        expected = Outcome.DUPLICATED if receiver == "naive" else Outcome.EXACTLY_ONCE
        agrees = (
            returncode == _EXPECTED_CRASH
            and effects_before == 1
            and attempts_before == 1
            and orphan.status == "running"
            and "turn.created" in orphan.event_types
            and "model.message" in orphan.event_types
            and "tool.response" not in orphan.event_types
            and subscription.status == 412
            and first_after_retry.status == "cancelled"
            and retry_turn.status == "done"
            and attempts_after == 2
            and observed is expected
        )
        return TrueForgeTrial(
            receiver=receiver,
            subject_returncode=returncode,
            effects_before_restart=effects_before,
            attempts_before_restart=attempts_before,
            orphan_after_restart=orphan,
            subscription_status_after_restart=subscription.status,
            first_turn_after_retry=first_after_retry,
            retry_turn=retry_turn,
            effects_after_retry=effects_after,
            attempts_after_retry=attempts_after,
            observed=observed,
            agrees=agrees,
        )
    finally:
        _stop(recovery)
        _stop(server)
        _stop(sidecar)


def run(k: int, name: str, fixture: Path = DEFAULT_FIXTURE_DIR) -> dict[str, object]:
    trials: list[TrueForgeTrial] = []
    with tempfile.TemporaryDirectory() as tmp, LedgerDaemon(Path(tmp) / "ledger") as ledger:
        root = Path(tmp)
        for receiver in RECEIVER_MODES:
            for index in range(k):
                trials.append(run_trial(ledger, root, index, receiver, fixture))

    arms: dict[str, object] = {}
    for receiver in RECEIVER_MODES:
        selected = [trial for trial in trials if trial.receiver == receiver]
        passing = sum(trial.agrees for trial in selected)
        arms[receiver] = {
            "k": k,
            "passing": passing,
            "pass_rate": round(passing / k, 4),
            "wilson95": list(wilson(passing, k)),
            "outcomes": dict(Counter(trial.observed.value for trial in selected)),
        }

    record: dict[str, object] = {
        "name": name,
        "runtime": "trueforge",
        "runtime_version": _TRUEFORGE_VERSION,
        "experiment_family": "mcp_effect_before_tool_response_persist",
        "barrier": "after_receiver_effect_before_mcp_response",
        "claim": (
            "a hard kill after the MCP receiver commits but before tool.response persistence leaves "
            "the durable turn running with an unresolved tool call; an explicit successor turn "
            "retries the action, duplicating a naive receiver while a stable receiver key deduplicates"
        ),
        "limitation": (
            "the successor turn is an explicit application retry, not automatic TrueForge recovery; "
            "standalone SQLite is measured, while hosted Postgres/Redis remains unmeasured"
        ),
        "k_per_receiver": k,
        "arms": arms,
        "all_agree": all(trial.agrees for trial in trials),
        "trials": [trial.as_dict() for trial in trials],
    }
    record["receipt"] = receipt(record)
    return record


def render(record: dict[str, object]) -> str:
    arms = cast(dict[str, dict[str, object]], record["arms"])
    lines = [
        f"TrueForge hidden MCP boundary - k={record['k_per_receiver']} per receiver",
        f"all trials agree: {record['all_agree']}",
    ]
    for receiver in RECEIVER_MODES:
        arm = arms[receiver]
        lines.append(
            f"{receiver}: {arm['passing']}/{arm['k']} pass; outcomes={arm['outcomes']}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--name", default="trueforge_hidden")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE_DIR)
    args = parser.parse_args(argv)
    if args.k < 1:
        parser.error("--k must be positive")
    record = run(args.k, args.name, args.fixture)
    print(render(record))
    output = _ROOT / "evidence" / f"{args.name}.json"
    output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(f"\nreceipt: {record['receipt']}\nwrote {output}")
    return 0 if record["all_agree"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
