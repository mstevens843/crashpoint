"""Parent harness for the CrewAI tool-retry reproduction.

WHAT THIS MEASURES. Whether CrewAI's actual retry path (``ToolUsage._use``, same process, same
``ToolUsage`` instance - see ``crewai_retry_receipt.ENTRY_POINT``) can execute a logical tool action
twice when the first attempt's harmless external effect already committed and the tool then fails
before returning success to CrewAI. This follows crewAIInc/crewAI#5802 and its proposed fix,
PR #5822.

THE SUBJECT IS UNTRUSTED, THE LEDGER IS NOT. The CrewAI subject (``crewai_retry_runtime.py``) is
launched in a fresh subprocess with only the ledger's execute (invoke) socket - never the control
socket - so it cannot read the effect count, reset the ledger, seal evidence, or forge the oracle's
answer. This harness queries and seals the ledger only after the subject process has exited. The
ledger applies no deduplication in this experiment (every ``execute`` call passes key=None), so two
crossings of one logical_action_id are admissible and counted, exactly as the DUPLICATED prediction
for ``post_effect`` requires.

WHAT COUNTS AS OBSERVED. A trial's oracle_classification and effect_count are trusted only when the
subject exited 0, emitted exactly one ``worker_started`` and one ``runtime_result`` event, and its
attempt/injection event order validates against the case's expected shape
(``validate_injection_order``). Anything short of that reports effect_count=null and classification
UNVERIFIED, never a PASS - the same fail-closed discipline as ``ledger.oracle.classify``, which this
module also defers to for VOID (a broken hash chain).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Final, cast

from ..canonical import receipt
from ..ledger.oracle import classify
from ..model.layers import Outcome
from .crewai_retry_receipt import (
    LIMITATIONS,
    CrewAIRetryTrial,
    build_receipt,
    validate_receipt,
)
from .crewai_retry_runtime import PREFIX
from .ledger_process import LedgerDaemon, LedgerHandle
from .wilson import wilson

CASES: Final[tuple[str, ...]] = ("clean", "pre_effect", "post_effect")
_TIMEOUT_DEFAULT: Final = 60.0

_ROOT = Path(__file__).resolve().parents[3]
_PREDICTION_PATH = _ROOT / "results" / "11-crewai-retry-prediction.json"
_STDERR_TAIL_CHARS: Final = 2000


def _load_prediction() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(_PREDICTION_PATH.read_text()))


def _git_commit(root: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root, capture_output=True, text=True, timeout=5, check=True,
        )
        return proc.stdout.strip()
    except Exception:
        return "unknown"


def parse_events(stdout: str) -> list[dict[str, Any]]:
    """Parse the subject's ``CRASHPOINT_CREWAI {...}`` stdout lines. Anything else on stdout -
    including CrewAI's own banners - is silently ignored by the prefix filter."""
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if not line.startswith(PREFIX):
            continue
        events.append(cast(dict[str, Any], json.loads(line[len(PREFIX) :])))
    return events


def _events_of(events: list[dict[str, Any]], name: str) -> list[tuple[int, dict[str, Any]]]:
    return [(i, e) for i, e in enumerate(events) if e.get("event") == name]


def validate_injection_order(
    events: list[dict[str, Any]], case: str, logical_action_id: str
) -> list[str]:
    """Check that the subject's own event stream shows CrewAI - not the harness - retrying the
    tool, and that the injected failure landed relative to the ledger effect as the case demands."""
    problems: list[str] = []

    for e in events:
        eid = e.get("logical_action_id")
        if eid is not None and eid != logical_action_id:
            problems.append(
                f"event {e.get('event')!r} carries logical_action_id {eid!r}, expected "
                f"{logical_action_id!r}"
            )

    tool_enters = _events_of(events, "tool_enter")
    injected = _events_of(events, "injected_failure")
    acks = _events_of(events, "effect_ack")
    returns = _events_of(events, "tool_return")

    expected_attempt_ids = [
        f"{logical_action_id}:attempt-{i}" for i in range(1, len(tool_enters) + 1)
    ]
    seen_attempt_ids = [e.get("attempt_id") for _, e in tool_enters]
    if seen_attempt_ids != expected_attempt_ids:
        problems.append(
            f"tool_enter attempt_ids out of order: {seen_attempt_ids} != {expected_attempt_ids}"
        )
    for _, e in tool_enters:
        if e.get("entry_point") != "crewai.tools.tool_usage.ToolUsage._use":
            problems.append(f"tool_enter did not originate inside ToolUsage._use: {e}")

    if case == "clean":
        if len(tool_enters) != 1 or injected or len(acks) != 1 or len(returns) != 1:
            problems.append(
                "clean case must have exactly one tool_enter/effect_ack/tool_return and no "
                "injected_failure"
            )
        return problems

    if len(tool_enters) != 2:
        problems.append(
            f"{case} case must retry exactly once (2 tool_enter), got {len(tool_enters)}"
        )
        return problems
    if tool_enters[1][1].get("run_attempt") != 2:
        problems.append(
            "the retried tool_enter must report run_attempt == 2, read live from CrewAI's own "
            "ToolUsage._run_attempts - this is what proves CrewAI's counter advanced, not a "
            "harness-side loop"
        )
    if len(injected) != 1:
        problems.append(f"{case} case must inject exactly one failure, got {len(injected)}")
        return problems
    injected_idx, injected_event = injected[0]

    if case == "pre_effect":
        if len(acks) != 1 or len(returns) != 1:
            problems.append("pre_effect must have exactly one effect_ack and one tool_return")
            return problems
        ack_idx, ack_event = acks[0]
        if injected_event.get("attempt_id") != expected_attempt_ids[0]:
            problems.append("pre_effect injected_failure must belong to attempt-1")
        if injected_event.get("point") != "before_effect":
            problems.append(
                f"pre_effect injected_failure point must be before_effect, got "
                f"{injected_event.get('point')!r}"
            )
        if ack_event.get("attempt_id") != expected_attempt_ids[1]:
            problems.append(
                "pre_effect effect_ack must belong to attempt-2: attempt-1 must commit no "
                "effect before the injected failure"
            )
        if not injected_idx < ack_idx:
            problems.append(
                "pre_effect event order must be injected_failure(1) before effect_ack(2)"
            )
    elif case == "post_effect":
        if len(acks) != 2 or len(returns) != 1:
            problems.append("post_effect must have exactly two effect_ack and one tool_return")
            return problems
        first_ack_idx, first_ack_event = acks[0]
        _, second_ack_event = acks[1]
        if first_ack_event.get("attempt_id") != expected_attempt_ids[0]:
            problems.append(
                "post_effect first effect_ack must belong to attempt-1: the effect must commit "
                "before the injected failure"
            )
        if injected_event.get("attempt_id") != expected_attempt_ids[0]:
            problems.append("post_effect injected_failure must belong to attempt-1")
        if injected_event.get("point") != "after_effect_before_tool_return":
            problems.append(
                "post_effect injected_failure point must be after_effect_before_tool_return, "
                f"got {injected_event.get('point')!r}"
            )
        if not first_ack_idx < injected_idx:
            problems.append(
                "post_effect event order must be effect_ack(1) before injected_failure(1): the "
                "commit must precede the injected failure"
            )
        if second_ack_event.get("attempt_id") != expected_attempt_ids[1]:
            problems.append("post_effect second effect_ack must belong to attempt-2")
    else:
        problems.append(f"unknown case: {case}")

    return problems


def _read_effect_ids(store_path: Path, intent_id: str) -> list[str | None]:
    """Read the raw ledger JSONL directly (filesystem access the parent harness already has,
    distinct from the subject's execute-only socket) to recover which attempt_id produced each
    distinct crossing recorded for this intent."""
    ids: list[str | None] = []
    if not store_path.exists():
        return ids
    with store_path.open(encoding="utf-8") as fh:
        for raw in fh:
            entry = json.loads(raw)
            record = entry.get("record", {})
            if record.get("op") == "execute" and record.get("intent_id") == intent_id:
                ids.append(record.get("attempt_id"))
    return ids


def run_trial(
    ledger: LedgerHandle,
    case: str,
    index: int,
    prediction: dict[str, Any],
    crashpoint_commit: str,
    timeout: float = _TIMEOUT_DEFAULT,
) -> dict[str, object]:
    logical_action_id = f"{case}-{index}"
    ledger.reset()
    argv = [
        sys.executable, "-m", "crashpoint.harness.crewai_retry_runtime",
        "--invoke", ledger.invoke_path,
        "--logical-action-id", logical_action_id,
        "--case", case,
    ]

    timed_out = False
    stdout = ""
    stderr = ""
    worker_exit_status: int | None
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        worker_exit_status = proc.returncode
        stdout = proc.stdout
        stderr = proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        worker_exit_status = None
        raw_out, raw_err = exc.stdout, exc.stderr
        stdout = raw_out if isinstance(raw_out, str) else ""
        stderr = raw_err if isinstance(raw_err, str) else ""

    # Only after the subject has exited (normally, killed by the timeout above, or otherwise) does
    # the harness touch the privileged control socket.
    ledger.seal()
    dump = ledger.dump()
    effect_ids: tuple[str | None, ...] = tuple(
        _read_effect_ids(Path(ledger.store_path), logical_action_id)
    )

    events: list[dict[str, Any]] = [] if timed_out else parse_events(stdout)
    worker_started = _events_of(events, "worker_started")
    runtime_results = _events_of(events, "runtime_result")

    injection_problems: tuple[str, ...]
    if timed_out:
        injection_problems = ("subprocess timed out before completion",)
    else:
        injection_problems = tuple(validate_injection_order(events, case, logical_action_id))

    observation_complete = (
        not timed_out
        and worker_exit_status == 0
        and len(worker_started) == 1
        and len(runtime_results) == 1
        and not injection_problems
    )

    effect_count: int | None
    classification: str
    if not observation_complete:
        effect_count = None
        classification = "UNVERIFIED"
    else:
        outcome = classify(logical_action_id, dump, Path(ledger.store_path), required=True)
        if outcome is Outcome.VOID:
            effect_count = None
            classification = "VOID"
        else:
            side_effects = dump.get("side_effects", {})
            effect_count = (
                int(side_effects.get(logical_action_id, 0))
                if isinstance(side_effects, dict)
                else None
            )
            classification = outcome.value.upper()

    runtime_reported_result = cast(
        dict[str, object], runtime_results[0][1] if runtime_results else {}
    )
    attempt_ids = tuple(str(e.get("attempt_id", "")) for _, e in _events_of(events, "tool_enter"))
    injection_point = "none"
    injected = _events_of(events, "injected_failure")
    if injected:
        injection_point = str(injected[0][1].get("point", "unknown"))

    expected_result = cast(dict[str, object], prediction["cases"][case])
    observed_result: dict[str, object] = {
        "effect_count": effect_count,
        "tool_attempts": runtime_reported_result.get("tool_attempts"),
        "llm_calls": runtime_reported_result.get("llm_calls"),
        "oracle_classification": classification,
        "runtime_result": runtime_reported_result.get("result"),
    }

    trial = CrewAIRetryTrial(
        case=case,
        logical_action_id=logical_action_id,
        attempt_ids=attempt_ids,
        injection_point=injection_point,
        worker_exit_status=worker_exit_status,
        runtime_reported_result=runtime_reported_result,
        observation_complete=observation_complete,
        effect_ids=effect_ids,
        effect_count=effect_count,
        expected_result=expected_result,
        observed_result=observed_result,
        oracle_classification=classification,
        injection_problems=injection_problems,
    )
    rec = build_receipt(trial, crashpoint_commit=crashpoint_commit)
    # Validation above already ran against the full events (including each one's runtime_state
    # cache/last-used-tool probe); the receipt keeps the ordering/identity fields that carry the
    # claim and drops that per-event diagnostic probe, which no check here or in
    # validate_injection_order ever reads, so 90 trials of it does not have to live in git.
    rec["events"] = [{k: v for k, v in e.items() if k != "runtime_state"} for e in events]
    rec["stderr_tail"] = stderr.strip()[-_STDERR_TAIL_CHARS:]
    return rec


def run(k: int, name: str, timeout: float = _TIMEOUT_DEFAULT) -> dict[str, object]:
    prediction = _load_prediction()
    crashpoint_commit = _git_commit(_ROOT)
    receipts: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory() as tmp, LedgerDaemon(Path(tmp) / "ledger") as ledger:
        for case in CASES:
            for index in range(k):
                receipts.append(
                    run_trial(ledger, case, index, prediction, crashpoint_commit, timeout)
                )

    for rec in receipts:
        problems = validate_receipt(rec)
        if problems:
            raise RuntimeError(
                f"invalid receipt for case={rec.get('case')!r} "
                f"logical_action_id={rec.get('logical_action_id')!r}: {problems}"
            )

    cases_summary: dict[str, object] = {}
    for case in CASES:
        case_receipts = [r for r in receipts if r["case"] == case]
        passing = sum(1 for r in case_receipts if r["passed"])
        classifications = Counter(str(r["oracle_classification"]) for r in case_receipts)
        expected = cast(dict[str, object], prediction["cases"][case])
        cases_summary[case] = {
            "k": k,
            "passing": passing,
            "pass_rate": round(passing / k, 4),
            "wilson95": list(wilson(passing, k)),
            "classifications": dict(classifications),
            "expected_classification": expected["oracle_classification"],
            "expected_effect_count": expected["effect_count"],
        }

    record: dict[str, object] = {
        "name": name,
        "runtime": "crewai",
        "experiment_family": "tool_retry_duplication",
        "framework_fix": False,
        "claim": (
            "CrewAI's built-in synchronous tool retry (ToolUsage._use, same process, same "
            "ToolUsage instance) re-invokes a tool after an injected mid-call failure; when the "
            "first invocation's external effect had already committed before that failure, the "
            "retry performs the effect again and the out-of-process ledger records two side "
            "effects for one logical action"
        ),
        "limitation": LIMITATIONS,
        "k_per_case": k,
        "prediction_schema": prediction["schema"],
        "prediction_registered_before_execution": prediction["registered_before_execution"],
        "cases": cases_summary,
        "all_agree": all(bool(r["passed"]) for r in receipts),
        "trials": receipts,
    }
    record["receipt"] = receipt(record)
    return record


def render(record: dict[str, object]) -> str:
    cases = cast(dict[str, dict[str, object]], record["cases"])
    lines = [
        f"CrewAI tool-retry evidence - k={record['k_per_case']} per case",
        f"all trials agree with the pre-registered prediction: {record['all_agree']}",
    ]
    for case in CASES:
        c = cases[case]
        lines.append(
            f"{case}: {c['passing']}/{c['k']} pass; classifications={c['classifications']} "
            f"(expected {c['expected_classification']})"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--k", type=int, default=30)
    parser.add_argument("--name", default="crewai_retry")
    parser.add_argument("--timeout", type=float, default=_TIMEOUT_DEFAULT)
    args = parser.parse_args(argv)
    if args.k < 1:
        parser.error("--k must be positive")

    record = run(args.k, args.name, timeout=args.timeout)
    print(render(record))
    output = _ROOT / "evidence" / f"{args.name}.json"
    output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(f"\nreceipt: {record['receipt']}\nwrote {output}")
    return 0 if record["all_agree"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
