"""Second raw-byte derivation using only stdlib, no Crashpoint capture/verifier imports.

Deliberately bounded to this fixture's ASCII, non-null receiver records. This is a second
implementation by the same author, not an independent organization or trusted attestation.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: object) -> bytes:
    # Retained fixture record/payload fields contain no nulls, floats or non-ASCII strings.
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()


def audit(root: Path) -> dict[str, object]:
    result = {}
    ids: set[str] = set()
    for version in ["v181", "v1822"]:
        observations = []
        for tid in ["clean-0", "post_crash-0", "post_crash-1", "post_crash-2"]:
            directory = root / version / "trials" / tid

            def read(name: str, directory: Path = directory) -> bytes:
                path = directory / name
                assert not path.is_symlink() and path.resolve().is_relative_to(root.resolve())
                return path.read_bytes()

            def data(name: str) -> object:
                return json.loads(read(name))

            caller = data("caller.json")
            assert isinstance(caller, dict)
            action_id = caller["action_id"]
            assert action_id not in ids
            ids.add(action_id)
            assert (
                caller["subject_pin"]
                == {
                    "v181": "c9d1ca86969f5567cf771ab8a0f3247770a1dfb7",
                    "v1822": "4213c479bd9333558522db1ca820d657b99effbf",
                }[version]
            )
            response = json.loads(read("api-status.jsonl").splitlines()[1])
            public = json.loads(response["body"])["result"]["structuredContent"]
            persisted = data("subject-after_restart.raw")
            assert persisted == [public["action"]]
            assert public["action"]["action_id"] == action_id
            effects = {}
            attempts = []
            phases = ["initial", "before_recovery", "after_restart", "after_retry", "final"]
            if version == "v1822" and tid == "post_crash-0":
                phases += ["probe_mcp", "probe_rest"]
            for phase in phases:
                rows = [json.loads(line) for line in read(f"receiver-{phase}.jsonl").splitlines()]
                assert len(rows) == (0 if phase == "initial" else 1)
                if rows:
                    row = rows[0]
                    record = row["record"]
                    assert row["i"] == 0 and type(row["i"]) is int
                    assert row["prev"] == "crashpoint-ledger-genesis-cp1"
                    assert row["hash"] == sha(row["prev"].encode() + b"|" + canonical(record))
                    assert record["intent_id"] == action_id
                    assert record["payload_digest"] == sha(canonical(caller["payload"]))
                    assert record["deduped"] is False and record["keyed"] is False
                    attempts.append(record["attempt_id"])
                effects[phase] = len(rows)
            assert len(set(attempts)) == 1
            request = json.loads(read("receiver-requests.jsonl"))
            assert request["action_id"] == action_id and request["payload"] == caller["payload"]
            assert request["attempt_id"] == attempts[0]
            assert request["run_id"] == caller["run_id"] and request["trial_id"] == tid
            retry = json.loads(read("api-retry.jsonl").splitlines()[1])
            assert json.loads(retry["body"])["result"]["structuredContent"]["ok"] is False
            progress = [json.loads(line) for line in read("progress.jsonl").splitlines()]
            if tid.startswith("post_crash"):
                before = data("subject-before_recovery.raw")
                assert isinstance(before, list) and before[0]["status"] == "STARTED"
                killed = next(e for e in progress if e["event"] == "killed")
                assert killed["returncode"] == -9
                first = next(
                    e
                    for e in progress
                    if e.get("name") == "runtime-first" and e["event"] == "spawn"
                )
                fresh = next(
                    e
                    for e in progress
                    if e.get("name") == "runtime-recovery" and e["event"] == "spawn"
                )
                assert first["pid"] == killed["pid"] and fresh["pid"] != first["pid"]
                events = [json.loads(line) for line in read("runtime-first.stdout").splitlines()]
                assert [e["event"] for e in events] == [
                    "runtime_ready",
                    "adapter_entered",
                    "effect_ack",
                    "post_effect_barrier",
                ]
                assert events[2]["ack"] == {"ok": True, "fsync": True, "receipt": "receipt-ok"}
                assert events[2]["attempt_id"] == attempts[0]
                observed = next(e for e in progress if e.get("phase") == "before_recovery")
                assert progress.index(observed) < progress.index(killed) < progress.index(fresh)
            status = public["action"]["status"]
            rec = public["reconciliation"]
            assert type(rec["required"]) is bool
            expected = "SUCCEEDED" if tid == "clean-0" else "UNKNOWN"
            satisfied = status == expected and rec["required"] is (tid != "clean-0")
            observations.append(
                {
                    "trial": tid,
                    "action_id": action_id,
                    "status": status,
                    "required": rec["required"],
                    "state": rec["state"],
                    "effects": effects,
                    "recovery_property": satisfied,
                }
            )
        result[version] = observations
    return {"raw_audit_valid": True, "primary_trials": 8, "versions": result}


if __name__ == "__main__":
    print(json.dumps(audit(Path(sys.argv[1]).resolve()), sort_keys=True, indent=2))
