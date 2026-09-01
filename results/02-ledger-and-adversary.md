# 02 - The out-of-process ledger, the oracle, and the reflexive adversary

Working record. The ground-truth machinery was built before any real runtime was crashed: a separate
process that records the true side-effect count, a fail-closed classifier over it, and adversaries
that exercise whether it can be read or forged from the subject's side. Every line below was
produced by running the command shown and reading its output.

Current note (2026-09-01): a stronger Linux UID-drop adversary now exists in
`src/crashpoint/adversaries/isolation.py`. It reports BLOCKED on the macOS host used for this run,
so the current macOS claim remains the socket-privilege boundary, not OS-level UID isolation.

## What was built

`src/crashpoint/ledger/` - the ground truth the system under test cannot forge:
- `core.py` - `LedgerState`. Records EVERY `execute` as an attempt, and separately counts DISTINCT
  side effects: a keyless (naive) call is always a distinct effect, a keyed (idempotent) call is
  deduped by key. Every record is hash-chained `h_i = sha256(h_{i-1} || canonical(record))`, so any
  edit, delete, or reorder breaks the chain. The returned receipt is a constant opaque string.
- `daemon.py` - two Unix sockets. The INVOKE socket (0666) accepts one verb, `execute`, from the
  SUT. The CONTROL socket (0600, in a 0700 dir) is the harness's: `dump`, `seal`, `reset`. The
  account of what happened is produced by a different process than the one under test.
- `oracle.py` - `classify`. Verifies the chain first (broken -> VOID), then reads the outcome off the
  distinct side-effect count: 0 -> LOST, 1 -> EXACTLY_ONCE, >=2 -> DUPLICATED. Fail-closed.
- `idempotency.py` - `derive_idempotency_key(namespace, subject, intent_version, payload)` plus
  `assert_no_forbidden_identity_fields`, which throws if a per-attempt field (`attempt`, `retry`,
  `nonce`, `timestamp`, ...) leaks into the key - the classic idempotency bug pre-blocked.

`src/crashpoint/adversaries/reflexive.py` - attack the fix by running it.

## The reflexive adversary (`uv run python -m crashpoint.adversaries.reflexive`)

```
ADVERSARY A - out of reach:
  {"all_control_verbs_denied": true, "control_verbs_denied_on_invoke": {"dump": true, "reset": true,
   "seal": true}, "harness_sees_true_count": 2, "proof": "out_of_reach",
   "receipts_identical_and_opaque": true}
ADVERSARY B - tamper evident:
  {"chain_ok_after_edit": false, "chain_ok_before_edit": true, "first_broken_index": 0,
   "flips_to_void": true, "proof": "tamper_evident", "verdict_tampered": "void",
   "verdict_untampered": "duplicated"}

REFLEXIVE ADVERSARIES PASS: ledger out of reach (execute-only, opaque receipt) AND tamper-evident
```

Read directly:
- **A - out of reach.** The subject holds only the invoke socket. Two naive effects both return the
  same opaque `receipt-ok`, so it cannot read its own exactly-once status off the wire. All three
  control verbs (`dump`, `reset`, `seal`) are refused on the invoke socket. The harness, on the
  control socket, reads the true count (2) the subject could not reach.
- **B - tamper evident.** An untampered two-effect ledger classifies DUPLICATED. Editing record 0 in
  place breaks the chain at index 0, and the same classification flips to VOID - fail-closed, never a
  clean pass. The oracle never trusts a ledger whose chain does not verify.

## Checks

- `uv run pytest` green, including `test_ledger` (record/attempt/dedup/chain), `test_oracle` (the
  measured outcomes + VOID on a broken chain), and the idempotency forbidden-field test (a key built over a
  per-attempt field is rejected).
- ruff clean, mypy --strict clean.

Next: the LangGraph adapter and the crash injector, graded against this ledger.
