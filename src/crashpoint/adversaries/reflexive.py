"""Attack the fix, the way the Terminal-Bench verifier was attacked: write the exploit and run it.
Two claims the out-of-process ledger makes, each turned into a demonstration a reader can rerun:

  A. OUT OF REACH. The system under test holds only the INVOKE socket, whose one verb is `execute`.
     It has no verb to read the ledger (`dump`), to zero the count (`reset`), or to close it
     (`seal`); those live on the CONTROL socket the harness keeps. And `execute` returns an
     impoverished receipt - the same opaque string for a first call and a deduped repeat - so the
     SUT cannot read its own exactly-once status off the wire and cannot forge it.

  B. TAMPER-EVIDENT. Even in the worst case where an attacker does reach the ledger file, editing
     any record breaks the hash chain, `LedgerState.verify` reports the first broken index, and the
     oracle turns that into VOID rather than the DUPLICATED the untampered record would have read -
     fail-closed, never a clean pass.

Impure (spawns the ledger daemon); run with `python -m crashpoint.adversaries.reflexive`.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ..harness.ledger_process import LedgerDaemon
from ..ledger.core import LedgerState
from ..ledger.daemon import control, execute
from ..ledger.oracle import classify
from ..model.layers import Outcome

_PAYLOAD = {"amount": 100, "to": "acct-attacker"}


def prove_out_of_reach() -> dict[str, object]:
    with tempfile.TemporaryDirectory() as d, LedgerDaemon(Path(d) / "led") as led:
        # The SUT holds only the invoke socket. Two naive effects: both receipts are the same opaque
        # string, so the count cannot be read off the wire.
        r1 = execute(led.invoke_path, "order-1", None, _PAYLOAD)
        r2 = execute(led.invoke_path, "order-1", None, _PAYLOAD)
        receipts = [r1.get("receipt"), r2.get("receipt")]
        # The SUT tries the control verbs on the invoke socket. Each is refused.
        denied = {op: control(led.invoke_path, op) for op in ("dump", "reset", "seal")}
        # The harness, on the control socket, sees the true count the SUT could not reach.
        harness_dump = control(led.control_path, "dump")
    dumped = harness_dump.get("dump", {})
    effects = dumped.get("side_effects", {}) if isinstance(dumped, dict) else {}
    n = effects.get("order-1") if isinstance(effects, dict) else None
    per_verb = {op: (not resp.get("ok")) for op, resp in denied.items()}
    return {
        "proof": "out_of_reach",
        "receipts_identical_and_opaque": receipts == ["receipt-ok", "receipt-ok"],
        "control_verbs_denied_on_invoke": per_verb,
        "all_control_verbs_denied": all(per_verb.values()),
        "harness_sees_true_count": n,
    }


def prove_tamper_evident() -> dict[str, object]:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "store.jsonl"
        led = LedgerState(path=p)
        # Two naive effects for one intent: the untampered ledger reads DUPLICATED.
        led.execute("order-1", None, _PAYLOAD)
        led.execute("order-1", None, _PAYLOAD)
        dump = led.dump()
        ok_before, _ = LedgerState.verify(p)
        verdict_clean = classify("order-1", dump, p)
        # An attacker edits record 0 in place (hide one of the two attempts).
        lines = p.read_text().splitlines()
        first = json.loads(lines[0])
        first["record"]["attempt"] = 999
        lines[0] = json.dumps(first, sort_keys=True)
        p.write_text("\n".join(lines) + "\n")
        ok_after, broken = LedgerState.verify(p)
        verdict_tampered = classify("order-1", dump, p)
    return {
        "proof": "tamper_evident",
        "chain_ok_before_edit": ok_before,
        "chain_ok_after_edit": ok_after,
        "first_broken_index": broken,
        "verdict_untampered": verdict_clean.value,
        "verdict_tampered": verdict_tampered.value,
        "flips_to_void": verdict_clean is Outcome.DUPLICATED and verdict_tampered is Outcome.VOID,
    }


def main() -> int:
    a = prove_out_of_reach()
    b = prove_tamper_evident()
    print("ADVERSARY A - out of reach:")
    print("  " + json.dumps(a, sort_keys=True))
    print("ADVERSARY B - tamper evident:")
    print("  " + json.dumps(b, sort_keys=True))
    ok = (
        bool(a["receipts_identical_and_opaque"])
        and bool(a["all_control_verbs_denied"])
        and a["harness_sees_true_count"] == 2
        and bool(b["flips_to_void"])
    )
    print(
        f"\nREFLEXIVE ADVERSARIES {'PASS' if ok else 'FAIL'}: "
        f"ledger out of reach (execute-only, opaque receipt) AND tamper-evident (edit -> VOID)"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
