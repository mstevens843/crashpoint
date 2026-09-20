# Native field mapping, for a future SafeAgent Control comparison

**Status: adapter/comparison NOT RUN.** This document maps this experiment's own native receipt
fields (`crashpoint.action_readback.receipt.v1`) to the *categories* a future comparison against a
hypothetical "SafeAgent Control" would need, so that comparison can be scoped without guessing at
field names on either side. No SafeAgent Control command exists in this repository, no SABLE
schema field is invented below, and nothing here should be read as having executed or even
attempted that comparison. An external maintainer re-running their own tool against this
fixture's evidence is a different, separate act from this repo's own offline verification
(`action_readback_verify.verify_bundle`), and would need to be run and its output retained before
any "confirmed"/"matches" language could be used about it.

| Category | This experiment's native field(s) | Notes for a future mapping |
|---|---|---|
| Caller action identity | `action_id`, `action_type`, `run_id`, `trial_id` | `action_id` is a fresh UUID, opaque to the receiver; a comparison tool would need to know which of its own fields is the pre-dispatch identity vs. any identity the receiver assigns |
| Caller action status | `admission_commit_confirmed`, `protocol_valid`, `observation_complete`, `invalid_reason`, `passed` | These are layered: commit confirmation is about the ADMISSION store; protocol/observation-complete are about whether THIS trial's own mechanics ran as designed; passed is agreement with a frozen prediction, not a general health signal |
| Caller action input | `admission_payload_digest`, `admission_journal_mode`, `admission_synchronous`, `admitted_at_utc` | Only a digest is retained natively, never the raw payload bytes themselves in the manifest (the raw payload lives in `admission.sqlite`'s `canonical_payload` column, retained per trial) |
| External event reference | `receiver_ref` (the ledger's invoke-socket path in this fixture) | A real external system would have its own reference shape (e.g. a chain address, an HTTP endpoint, a provider transaction ID) - `receiver_ref` is deliberately a free string for exactly this reason |
| External event digest | `effect_payload_digests`, `effect_ledger_classification`, `digests_match_admission` | `effect_payload_digests` is a list (order = raw recorded order) since more than one effect is a real, measured outcome here (`naive_retry`), not an error case to be collapsed to one field |
| Attempt evidence | `worker_a_attempt_id`, `worker_b_attempt_id`, `effect_attempt_ids`, `worker_a_killed`, `worker_a_exit_status`, `worker_b_used`, `worker_b_exit_status`, `worker_a_local_receipt`, `worker_b_local_receipt` | Attempt IDs are `{action_id}:worker-a` / `{action_id}:worker-b-retry` by this fixture's own construction; a comparison tool's own attempt-numbering scheme would need its own mapping, not an assumed 1:1 |
| Observation completeness | `observer_raw_ledger_state`, `observer_exit_status`, `observed_result.observation_availability`, `observed_result.externally_verified` | `externally_verified` is the single field most likely to need a matching concept on the other side - it is deliberately NOT the same thing as `passed` (see CLAIM-MATRIX.md) |
| Bounded expected classification | `observed_result.external_outcome` (closed enum: `NO_EFFECT` / `ONE_EFFECT_MATCHING` / `ONE_EFFECT_MISMATCHED` / `MULTIPLE_EFFECTS_MATCHING` / `MULTIPLE_EFFECTS_DIVERGED` / `INDETERMINATE`), `observed_result.client_claim` (`SUCCESS`/`FAILURE`/`LOST`) | Deliberately small and closed rather than an open string, so a future mapping has to explicitly decide what an unrecognized value on either side means, rather than silently passing one through |

## What a real comparison would still need, not provided here

- A pinned, public SafeAgent Control tool/command actually installed and run against this
  fixture's retained evidence (or vice versa), with its raw output retained alongside this
  bundle - not summarized from memory.
- An explicit decision on whether SafeAgent Control's own claim/sweep semantics (measured
  separately in `results/12-safeagent-ttl.md`) are even the right comparison point for THIS
  experiment's admission-identity focus, or a different boundary entirely.
- Nothing above should be read as asserting compatibility, superiority, or interoperability with
  SafeAgent - it is a same-repo cross-reference of category names, offered so a future comparison
  does not have to re-derive this fixture's own schema from scratch.
