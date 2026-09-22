"""Offline verification of captured bytes; never imports or executes the subject runtime."""

from __future__ import annotations

import argparse
import json
import re
from itertools import pairwise
from pathlib import Path
from typing import Any

from crashpoint.canonical import canonicalize, chain

from .reality_layer_common import (
    BASE,
    CASES,
    INVENTORY,
    OWN_SOURCES,
    PIN,
    SUBJECT_REQUIRED,
    Invalid,
    confined,
    digest,
    json_read,
    need,
    parse,
    read,
    strict_equal,
)

STATUSES = {"STARTED", "SUCCEEDED", "FAILED", "UNKNOWN"}
PHASES = ["initial", "before_recovery", "after_retry", "final"]
SUBJECT_PHASES = ["before_recovery", "before_restart", "after_restart", "after_query", "final"]
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")


def required_bytes(value: bytes | None) -> bytes:
    if not isinstance(value, bytes):
        raise Invalid("required_subject_bytes: unavailable")
    return value


def obj(value: Any, label: str) -> dict[str, Any]:
    need(type(value) is dict, "schema_object", label)
    return dict(value)


def arr(value: Any, label: str) -> list[Any]:
    need(type(value) is list, "schema_array", label)
    return list(value)


def integer(value: Any, label: str, minimum: int = 0) -> None:
    need(type(value) is int and value >= minimum, "schema_integer", label)


def boolean(value: Any, label: str) -> None:
    need(type(value) is bool, "schema_boolean", label)


def uuid(value: Any, label: str) -> None:
    need(type(value) is str and UUID.fullmatch(value) is not None, "schema_uuid", label)


def hash_value(value: Any, label: str) -> None:
    need(type(value) is str and HEX.fullmatch(value) is not None, "schema_hash", label)


def lines(raw: bytes) -> list[dict[str, Any]]:
    need(not raw or raw.endswith(b"\n"), "jsonl_truncated")
    return [obj(parse(line), "jsonl row") for line in raw.splitlines()]


def action(value: Any, caller: dict[str, Any]) -> dict[str, Any]:
    a = obj(value, "action")
    need(a.get("action_id") == caller["action_id"], "action_binding")
    need(type(a.get("status")) is str and a["status"] in STATUSES, "status_vocabulary")
    need(a.get("schema") == "reality-action-outcome/1.0", "action_schema")
    need(strict_equal(a.get("provenance"), caller["action_ir"]["provenance"]), "provenance_binding")
    integer(a.get("attempt"), "action.attempt", 1)
    arr(a.get("evidence"), "action.evidence")
    return a


def reconciliation(value: Any) -> dict[str, Any]:
    r = obj(value, "reconciliation")
    boolean(r.get("required"), "reconciliation.required")
    need(r.get("state") in {"REQUIRED", "NOT_REQUIRED", "RESOLVED"}, "reconciliation_vocabulary")
    return r


def derive_trial(bundle: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    trial_id = receipt["trial_id"]
    case = receipt["case"]
    prefix = f"trials/{trial_id}/"

    def raw(name: str) -> bytes:
        return read(bundle, prefix + name)

    def data(name: str) -> Any:
        return parse(raw(name))

    def api(name: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        request = obj(data(f"api-{name}.request.json"), "api request")
        need(
            strict_equal(request, {"name": tool, "arguments": arguments}),
            "api_request_binding",
            name,
        )
        events = lines(raw(f"api-{name}.jsonl"))
        need(
            len(events) == 2
            and events[0].get("event") == "request"
            and events[1].get("event") == "response",
            "api_response_missing",
            name,
        )
        sent = obj(parse(events[0]["body"]), "wire request")
        need(
            sent.get("method") == "tools/call" and sent.get("jsonrpc") == "2.0", "mcp_request", name
        )
        params = obj(sent.get("params"), "wire params")
        need(
            params.get("name") == tool and strict_equal(params.get("arguments"), arguments),
            "wire_request_binding",
            name,
        )
        headers = obj(events[0].get("headers"), "headers")
        need(
            headers.get("Mcp-Name") == tool
            and headers.get("Mcp-Method") == "tools/call"
            and headers.get("MCP-Protocol-Version") == "2026-07-28",
            "mcp_headers",
        )
        need(
            type(events[0].get("url")) is str
            and re.fullmatch(r"http://127\.0\.0\.1:[0-9]+/mcp", events[0]["url"]) is not None,
            "loopback_api",
        )
        integer(events[1].get("status"), "http status", 100)
        need(events[1]["status"] == 200, "api_http_status", name)
        response = obj(parse(events[1]["body"]), "wire response")
        need(
            response.get("id") == sent.get("id") and response.get("jsonrpc") == "2.0",
            "mcp_response",
        )
        result = obj(response.get("result"), "mcp result")
        value = obj(result.get("structuredContent"), "structuredContent")
        boolean(value.get("ok"), "api.ok")
        client = obj(data(f"client-{name}.stdout"), "client output")
        boolean(client.get("ok"), "client.ok")
        if client["ok"]:
            need(strict_equal(client.get("value"), value), "client_wire_equality", name)
        else:
            need(
                result.get("isError") is True and client.get("error") == value.get("error"),
                "client_wire_equality",
                name,
            )
        process = next((p for p in receipt["processes"] if p["name"] == f"client-{name}"), None)
        need(
            process is not None and process["returncode"] == (0 if client["ok"] else 2),
            "client_exit",
            name,
        )
        return value

    declared = obj(json_read(bundle, "plan.json"), "plan")["subject_sources"]
    execution_sources = {
        name: sha
        for name, sha in declared.items()
        if name in {"server.js", "package.json"} or name.startswith(("src/", "integrations/"))
    }
    need(
        strict_equal(data("execution-source-hashes.json"), execution_sources),
        "execution_source_binding",
    )
    caller = obj(data("caller.json"), "caller")
    need(caller.get("version") == 1 and type(caller.get("version")) is int, "caller_version")
    need(
        caller.get("run_id") == receipt["run_id"]
        and caller.get("trial_id") == trial_id
        and caller.get("subject_pin") == PIN,
        "caller_binding",
    )
    uuid(caller.get("action_id"), "caller action_id")
    need(type(caller.get("plan_id")) is str and bool(caller["plan_id"]), "plan_id_type")
    ir = obj(caller.get("action_ir"), "action ir")
    need(
        ir.get("id") == caller["action_id"] and ir.get("schema") == "reality-action/1.0",
        "ir_binding",
    )
    need(
        obj(ir.get("provenance"), "provenance").get("plan_id") == caller["plan_id"],
        "ir_plan_binding",
    )
    need(
        ir.get("operation") == "write"
        and strict_equal(ir.get("input"), {"value": "on"})
        and obj(ir.get("capability"), "capability").get("id") == "power",
        "ir_operation_binding",
    )
    payload = obj(caller.get("payload"), "payload")
    need(
        strict_equal(payload, {"device_id": "bedroom_light", "capability": "turn_on", "args": {}}),
        "intended_payload",
    )
    need(
        ir.get("target", {}).get("device_id") == payload["device_id"]
        and ir.get("compatibility", {}).get("legacy_capability") == payload["capability"]
        and strict_equal(ir.get("compatibility", {}).get("legacy_args"), payload["args"]),
        "ir_payload_binding",
    )
    plan = api("plan", "reality.plan", {"text": "침실 불 켜줘"})
    need(
        plan.get("ok") is True
        and plan.get("decision") == "auto"
        and plan.get("plan_id") == caller["plan_id"],
        "admitted_plan",
    )
    steps = arr(plan.get("steps"), "plan steps")
    need(len(steps) == 1 and strict_equal(steps[0].get("action_ir"), ir), "caller_plan_equality")
    progress = lines(raw("progress.jsonl"))
    need(bool(progress), "progress_empty")
    names = [p.get("event") for p in progress]
    need(
        names[0] == "trial_started" and names[-1] == "trial_observations_complete",
        "progress_boundary",
    )
    caller_index = names.index("caller_retained")
    dispatch_index = next(i for i, p in enumerate(progress) if p.get("name") == "client-execute")
    need(caller_index < dispatch_index, "predispatch_order")
    need(
        progress[caller_index].get("action_id") == caller["action_id"]
        and progress[caller_index].get("plan_id") == caller["plan_id"],
        "predispatch_binding",
    )
    processes = arr(receipt.get("processes"), "processes")
    need(bool(processes), "processes_empty")
    need(len({p.get("name") for p in processes}) == len(processes), "process_duplicate")
    for p in processes:
        integer(p.get("pid"), "pid", 1)
        need(type(p.get("returncode")) is int and p.get("reaped") is True, "process_cleanup")
        raw(f"{p['name']}.stdout")
        need(raw(f"{p['name']}.stderr") == b"", "child_stderr", p["name"])
    need(len({p["pid"] for p in processes}) == len(processes), "process_pid_reused")
    spawns = [{"name": p["name"], "pid": p["pid"]} for p in progress if p.get("event") == "spawn"]
    need(
        strict_equal(spawns, [{"name": p["name"], "pid": p["pid"]} for p in processes]),
        "process_inventory",
    )
    runtime_events = lines(raw("runtime-first.stdout"))
    need(bool(runtime_events), "runtime_events_empty")
    ready = runtime_events[0]
    need(
        ready.get("event") == "runtime_ready"
        and ready.get("node") == "v22.22.1"
        and ready.get("live") == "0"
        and ready.get("dry_run") == "1",
        "runtime_safety",
    )
    first = next(p for p in processes if p["name"] == "runtime-first")
    receiver = next(p for p in processes if p["name"] == "receiver")
    need(ready.get("pid") == first["pid"], "runtime_identity")
    rr = lines(raw("receiver.stdout"))
    need(
        len(rr) == 1
        and rr[0].get("event") == "receiver_ready"
        and rr[0].get("pid") == receiver["pid"],
        "receiver_identity",
    )
    if case in {"post_crash", "pre_crash", "corrupt", "missing"}:
        recovery = next(p for p in processes if p["name"] == "runtime-recovery")
        ev = lines(raw("runtime-recovery.stdout"))
        need(bool(ev), "recovery_events_empty")
        need(
            ev[0].get("event") == "runtime_ready"
            and ev[0].get("pid") == recovery["pid"]
            and recovery["pid"] != first["pid"],
            "fresh_runtime",
        )
        runtime_events += ev[1:]
    entries = [e for e in runtime_events if e.get("event") == "adapter_entered"]
    acks = [e for e in runtime_events if e.get("event") == "effect_ack"]
    need(bool(entries), "adapter_never_entered")
    for e in entries:
        uuid(e.get("attempt_id"), "attempt_id")
        need(
            e.get("action_id") == caller["action_id"] and strict_equal(e.get("payload"), payload),
            "adapter_binding",
        )
    need(len({e["attempt_id"] for e in entries}) == len(entries), "attempt_duplicate")
    for ack in acks:
        need(ack.get("ack") == {"ok": True, "receipt": "receipt-ok", "fsync": True}, "receiver_ack")
    payload_digest = digest(canonicalize(payload).encode())
    observed: dict[str, list[dict[str, Any]]] = {}
    for phase in PHASES:
        source = raw(f"receiver-{phase}.jsonl")
        rows = lines(source)
        head = "crashpoint-ledger-genesis-cp1"
        records = []
        for index, row in enumerate(rows):
            integer(row.get("i"), "chain index")
            need(row["i"] == index and row.get("prev") == head, "effect_order")
            record = obj(row.get("record"), "effect")
            integer(record.get("attempt"), "attempt", 1)
            boolean(record.get("keyed"), "keyed")
            boolean(record.get("deduped"), "deduped")
            need(
                record.get("intent_id") == caller["action_id"]
                and record.get("payload_digest") == payload_digest,
                "effect_binding",
            )
            need(
                record.get("op") == "execute"
                and record["keyed"] is False
                and record["deduped"] is False,
                "effect_semantics",
            )
            need(record["attempt"] == index + 1, "attempt_order")
            uuid(record.get("attempt_id"), "effect attempt_id")
            head = chain(head, record)
            need(row.get("hash") == head, "effect_chain")
            records.append(record)
        report = obj(data(f"observation-{phase}.json"), "observation")
        actual_output = data(f"observer-{phase}.stdout")
        need(strict_equal(report, actual_output), "observer_output_equality")
        proc = next(p for p in processes if p["name"] == f"observer-{phase}")
        need(
            proc["returncode"] == 0
            and report.get("pid") == proc["pid"]
            and proc["pid"] not in {first["pid"], receiver["pid"]},
            "observer_process",
        )
        integer(report.get("version"), "observer.version", 1)
        need(report["version"] == 1, "observer_version")
        integer(report.get("effects"), "observer.effects")
        integer(report.get("bytes"), "observer.bytes")
        need(
            report.get("availability") == "readable" and report.get("valid_chain") is True,
            "observer_availability",
        )
        need(
            report.get("sha256") == digest(source)
            and report["bytes"] == len(source)
            and report.get("head") == head
            and report["effects"] == len(records)
            and strict_equal(report.get("attempts"), records),
            "observer_recompute",
        )
        observed[phase] = records
    need(not observed["initial"], "initial_not_empty")
    need(len(observed["before_recovery"]) == (0 if case == "pre_crash" else 1), "effect_premise")
    for earlier, later in pairwise(PHASES):
        need(
            strict_equal(observed[later][: len(observed[earlier])], observed[earlier]),
            "effect_history_prefix",
        )
    attempts = [r["attempt_id"] for r in observed["final"]]
    need(attempts == [e["attempt_id"] for e in acks], "attempt_event_order")
    expected_entries = [] if case == "pre_crash" else [e["attempt_id"] for e in entries]
    need(attempts == expected_entries, "attempt_dispatch_order")
    requests = lines(raw("receiver-requests.jsonl"))
    need(len(requests) == len(attempts), "receiver_request_count")
    for index, request in enumerate(requests):
        need(
            request.get("run_id") == receipt["run_id"]
            and request.get("trial_id") == trial_id
            and request.get("action_id") == caller["action_id"]
            and strict_equal(request.get("payload"), payload),
            "receiver_request_binding",
        )
        need(request.get("attempt_id") == attempts[index], "receiver_request_order")
    need(strict_equal(data("seal.json"), {"ok": True, "sealed": True}), "receiver_seal")
    need(
        names.index("receiver_sealed")
        < next(
            i
            for i, p in enumerate(progress)
            if p.get("event") == "observed" and p.get("phase") == "final"
        ),
        "seal_order",
    )
    need(strict_equal(observed["after_retry"], observed["final"]), "reconciliation_added_effect")
    local: dict[str, bytes | None] = {}
    for phase in SUBJECT_PHASES:
        capture = obj(data(f"subject-{phase}.json"), "subject capture")
        if capture.get("availability") == "unavailable":
            need(
                case == "missing"
                and phase in {"before_restart", "after_restart"}
                and capture.get("error_type") == "FileNotFoundError"
                and capture.get("errno") == 2,
                "subject_unavailable_unexpected",
                phase,
            )
            need(
                f"subject-{phase}.raw" not in receipt["artifacts"], "unavailable_subject_has_bytes"
            )
            local[phase] = None
        else:
            need(capture.get("availability") == "readable", "subject_capture_availability")
            source = raw(f"subject-{phase}.raw")
            integer(capture.get("bytes"), "subject bytes")
            need(
                capture.get("sha256") == digest(source) and capture["bytes"] == len(source),
                "subject_capture_hash",
            )
            local[phase] = source
    need(local["before_recovery"] is not None, "prior_subject_unavailable")
    prior = arr(parse(required_bytes(local["before_recovery"])), "prior subject ledger")
    need(len(prior) == 1, "subject_prior_count")
    prior_action = action(prior[0], caller)
    if case in {"pre_crash", "post_crash"}:
        need(prior_action["status"] == "STARTED", "started_premise")
        barrier = obj(data("barrier.json"), "barrier")
        event_name = "pre_effect_barrier" if case == "pre_crash" else "post_effect_barrier"
        need(
            barrier in runtime_events
            and barrier.get("event") == event_name
            and barrier.get("attempt_id") == entries[0]["attempt_id"],
            "barrier_premise",
        )
        need(
            first["returncode"] == -9
            and any(
                p.get("event") == "killed"
                and p.get("pid") == first["pid"]
                and p.get("returncode") == -9
                for p in progress
            ),
            "real_kill",
        )
        observed_index = next(
            i
            for i, p in enumerate(progress)
            if p.get("event") == "observed" and p.get("phase") == "before_recovery"
        )
        need(
            observed_index < names.index("kill_requested") < names.index("killed"),
            "kill_observation_order",
        )
        execute = lines(raw("api-execute.jsonl"))
        need(
            len(execute) == 2 and execute[1].get("event") == "transport_error",
            "crash_dispatch_transport",
        )
        need(
            strict_equal(
                data("api-execute.request.json"),
                {"name": "reality.execute", "arguments": {"plan_id": caller["plan_id"]}},
            ),
            "crash_request_binding",
        )
        failed_client = obj(data("client-execute.stdout"), "crashed request client")
        need(
            failed_client.get("ok") is False
            and failed_client.get("error") == execute[1].get("error"),
            "crash_client_transport",
        )
        cp = next(p for p in processes if p["name"] == "client-execute")
        need(cp["returncode"] == 2, "crash_client_exit")
        sent = parse(execute[0]["body"])
        need(
            sent["params"]["name"] == "reality.execute"
            and sent["params"]["arguments"] == {"plan_id": caller["plan_id"]},
            "crash_dispatch_binding",
        )
    else:
        executed = api("execute", "reality.execute", {"plan_id": caller["plan_id"]})
        if case in {"clean", "corrupt", "missing"}:
            need(
                executed.get("ok") is True
                and executed.get("status") == "completed"
                and executed.get("action", {}).get("action_id") == caller["action_id"],
                "clean_control",
            )
        else:
            need(
                executed.get("ok") is False and executed.get("status") == "stopped",
                "injected_error_premise",
            )
    status = api("status", "reality.action.get", {"action_id": caller["action_id"]})
    reported_status = None
    required = None
    if status["ok"]:
        queried = action(status.get("action"), caller)
        reconc = reconciliation(status.get("reconciliation"))
        reported_status = queried["status"]
        required = reconc["required"]
        retained = arr(parse(required_bytes(local["after_query"])), "queried subject ledger")
        need(len(retained) == 1 and strict_equal(retained[0], queried), "status_ledger_equality")
        need(
            reconc["state"] == ("REQUIRED" if required else "NOT_REQUIRED"),
            "reconciliation_consistency",
        )
    else:
        need(
            status.get("action_id") == caller["action_id"] and type(status.get("error")) is str,
            "missing_status_binding",
        )
    if case in {"corrupt", "missing"}:
        need(
            prior_action["status"] == "SUCCEEDED" and "subject_storage_mutation" in names,
            "storage_mutation_premise",
        )
        if case == "corrupt":
            need(
                local["before_restart"] == b'{"truncated":'
                and local["after_restart"] == b'{"truncated":',
                "corrupt_bytes_premise",
            )
        else:
            need(local["before_restart"] is None, "missing_bytes_premise")
    else:
        need(
            local["before_recovery"] == local["before_restart"] == local["after_restart"],
            "restart_storage_equality",
        )
    after_bytes = local["after_query"]
    need(after_bytes is not None, "query_storage_unavailable")
    try:
        parsed_after = parse(required_bytes(after_bytes))
        storage_after_query = "empty_array" if parsed_after == [] else "json_nonempty"
    except Invalid:
        storage_after_query = "malformed_bytes_retained"
    retried = api("retry", "reality.execute", {"plan_id": caller["plan_id"]})
    reconciled_status = None
    changed = None
    if case in {"post_crash", "pre_crash", "unknown_error"}:
        before_report = data("observation-before_recovery.json")
        count = len(observed["before_recovery"])
        resolved = api(
            "reconcile",
            "reality.action.reconcile",
            {
                "action_id": caller["action_id"],
                "outcome": "SUCCEEDED" if count == 1 else "FAILED",
                "evidence_note": (
                    f"fixture receiver sha256={before_report['sha256']}; effects={count}"
                ),
            },
        )
        need(resolved.get("ok") is True, "reconcile_response")
        boolean(resolved.get("changed"), "reconcile.changed")
        changed = resolved["changed"]
        action(resolved.get("action"), caller)
        reconciliation(resolved.get("reconciliation"))
        final_status = api("status_final", "reality.action.get", {"action_id": caller["action_id"]})
        final_action = action(final_status.get("action"), caller)
        reconciliation(final_status.get("reconciliation"))
        reconciled_status = final_action["status"]
        need(strict_equal(resolved["action"], final_action), "reconciled_query_equality")
        retained_final = arr(parse(required_bytes(local["final"])), "final ledger")
        need(
            len(retained_final) == 1 and strict_equal(retained_final[0], final_action),
            "reconciled_ledger_equality",
        )
    if case not in {"post_crash", "pre_crash", "unknown_error"}:
        need(local["final"] == local["after_query"], "final_subject_equality")
    return {
        "subject_storage_after_query": storage_after_query,
        "action_id": caller["action_id"],
        "plan_id": caller["plan_id"],
        "effect_count": len(observed["final"]),
        "effect_observation": "observed" if attempts else "bounded_readable_empty",
        "attempt_ids": attempts,
        "invocation_attempt_ids": [e["attempt_id"] for e in entries],
        "status": reported_status,
        "reconciliation_required": required,
        "original_plan_rejected": retried["ok"] is False,
        "retry_added_effects": len(observed["after_retry"]) - len(observed["before_recovery"]),
        "reconcile_changed": changed,
        "reconciled_status": reconciled_status,
        "unresolved_marked_not_required": reported_status == "STARTED" and required is False,
        "failed_with_observed_effect": reported_status == "FAILED" and bool(attempts),
        "history_missing_with_effect": reported_status is None and bool(attempts),
    }


def verify_bundle(bundle: Path) -> dict[str, Any]:
    manifest_raw = read(bundle, "manifest.json")
    m = obj(parse(manifest_raw), "manifest")
    need(type(m.get("version")) is int and m["version"] == 1, "manifest_version")
    uuid(m.get("run_id"), "run_id")
    need(m.get("base") == BASE and m.get("subject_pin") == PIN, "manifest_pin")
    need(
        m.get("kind") == "confirmatory" and m.get("state") == "complete" and m.get("errors") == [],
        "run_not_complete",
    )
    need(
        strict_equal(m.get("cases"), CASES) and strict_equal(m.get("inventory"), INVENTORY),
        "case_inventory",
    )
    plan_raw = read(bundle, "plan.json")
    plan = obj(parse(plan_raw), "plan")
    need(
        plan.get("version") == 1
        and type(plan["version"]) is int
        and plan.get("subject_pin") == PIN
        and plan.get("base") == BASE,
        "plan_version_pin",
    )
    need(
        strict_equal(plan.get("cases"), CASES) and strict_equal(plan.get("inventory"), INVENTORY),
        "plan_inventory",
    )
    predictions = obj(plan.get("predictions"), "plan predictions")
    need(set(predictions) == set(CASES), "prediction_inventory")
    for case, value in predictions.items():
        prediction = arr(value, f"prediction {case}")
        need(len(prediction) == 3, "prediction_shape", case)
        need(
            prediction[0] is None or (type(prediction[0]) is str and prediction[0] in STATUSES),
            "prediction_status",
            case,
        )
        if prediction[1] is not None:
            boolean(prediction[1], f"prediction {case} reconciliation")
        integer(prediction[2], f"prediction {case} effects")
    need(m.get("plan_sha256") == digest(plan_raw), "plan_hash")
    hashes = obj(m.get("source_hashes"), "source hashes")
    need(
        set(hashes) == set(OWN_SOURCES) and strict_equal(hashes, plan.get("own_sources")),
        "source_inventory",
    )
    for name in OWN_SOURCES:
        hash_value(hashes[name], name)
        need(digest(read(bundle, "sources/" + name)) == hashes[name], "source_hash", name)
    subject_hashes = obj(plan.get("subject_sources"), "subject source hashes")
    need(set(SUBJECT_REQUIRED) <= set(subject_hashes), "subject_source_inventory")
    for name, sha in subject_hashes.items():
        confined(bundle, name)  # references are relative even though retrieval is separate
        hash_value(sha, name)
    trials = arr(m.get("trials"), "trials")
    need([obj(t, "trial").get("trial_id") for t in trials] == INVENTORY, "trial_inventory")
    journal = lines(read(bundle, "journal.jsonl"))
    need(strict_equal(journal, trials), "journal_equality")
    result: dict[str, Any] = {}
    all_actions: set[str] = set()
    all_plans: set[str] = set()
    all_attempts: set[str] = set()
    for t in trials:
        tid = t["trial_id"]
        need(
            type(t.get("version")) is int and t["version"] == 1 and t.get("run_id") == m["run_id"],
            "trial_binding",
        )
        need(t.get("case") == tid.rsplit("-", 1)[0], "trial_case_binding")
        need(
            t.get("evidence_valid") is True and t.get("errors") == [] and t.get("failpoint") == "",
            "trial_not_valid",
        )
        prefix = f"trials/{tid}/"
        need(strict_equal(json_read(bundle, prefix + "receipt.json"), t), "receipt_equality")
        artifacts = obj(t.get("artifacts"), "artifacts")
        # This validates all references, even an unexpected entry or a fixed artifact symlink.
        for relative, sha in artifacts.items():
            confined(confined(bundle, f"trials/{tid}"), relative)
            hash_value(sha, relative)
        actual_dir = confined(bundle, f"trials/{tid}")
        actual_names = {p.name for p in actual_dir.iterdir() if p.name != "receipt.json"}
        need(set(artifacts) == actual_names, "artifact_inventory")
        for relative, sha in artifacts.items():
            need(digest(read(bundle, prefix + relative)) == sha, "artifact_hash", relative)
        derived = derive_trial(bundle, t)
        need(strict_equal(t.get("findings"), derived), "findings_recompute", tid)
        need(
            derived["action_id"] not in all_actions and derived["plan_id"] not in all_plans,
            "cross_trial_identity",
        )
        need(not (set(derived["invocation_attempt_ids"]) & all_attempts), "cross_trial_attempt")
        all_actions.add(derived["action_id"])
        all_plans.add(derived["plan_id"])
        all_attempts.update(derived["invocation_attempt_ids"])
        result[tid] = derived
    summary = {"trials": len(trials), "valid": len(trials), "invalid": 0, "findings": result}
    need(strict_equal(m.get("summary"), summary), "summary_recompute")
    receipt = obj(json_read(bundle, "bundle-receipt.json"), "bundle receipt")
    need(
        strict_equal(
            receipt, {"version": 1, "run_id": m["run_id"], "manifest_sha256": digest(manifest_raw)}
        ),
        "bundle_receipt",
    )
    return {
        "evidence_valid": True,
        "run_id": m["run_id"],
        "summary": summary,
        "subject_source_verification": "not performed offline; separate pinned retrieval required",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle", type=Path)
    args = ap.parse_args()
    try:
        result = verify_bundle(args.bundle.resolve())
    except (
        Invalid,
        OSError,
        KeyError,
        TypeError,
        ValueError,
        StopIteration,
        AttributeError,
    ) as exc:
        print(
            json.dumps(
                {"evidence_valid": False, "error": f"{type(exc).__name__}: {exc}"}, sort_keys=True
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
