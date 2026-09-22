@shimjaemandu I ran the bounded process-death/readback check against Reality Layer v1.8.1 at `c9d1ca86969f5567cf771ab8a0f3247770a1dfb7`, using the real MCP routes and your upstream client, with a harmless out-of-process receiver replacing only the virtual-light adapter boundary.

- After the receiver committed and the runtime was SIGKILLed before completion, the original action remained queryable as `STARTED` with `reconciliation.required=false` / `NOT_REQUIRED` in 3/3 trials. A public reconciliation attempt returned `changed=false`.
- A kill before the receiver request retained the same status/flags, but fresh readback confirmed a readable empty receiver.
- Retrying the original plan was rejected without another effect. This does **not** reproduce a public duplicate-execution bypass.
- An ordinary injected exception after the effect produced `FAILED`; the explicit unknown-outcome signal produced `UNKNOWN`, which reconciled to `SUCCEEDED` without another effect. I treated the supplied evidence note as your disclosed test primitive.
- Missing/corrupt subject ledgers returned a missing-action response while independent receiver evidence still showed one effect.

The new frozen confirmatory matrix has nine valid trials; all nine original-plan retries were rejected with zero added effects. The bundle includes raw API/ledger observations, an offline verifier, mutation and cleanup tests, and a proposed regression for recovered unresolved actions. No production runtime patch is included; the useful policy question is how recovered `STARTED` should enter reconciliation.

An independent review also caught a retention gap in **Crashpoint's capture harness**: a final artifact read error could drop a completed trial's receipt and manifest entry after successful cleanup. I reproduced and fixed that path, added real control/fault and inventory regressions, and captured this new nine-trial bundle with corrected source hashes. The earlier measured results remain valid. The new evidence verifies offline and after relocation; 68 focused tests and the full suite (483 passed, 22 optional-fixture skips) pass. This is a source-informed repetition and a Crashpoint harness correction, not a Reality Layer patch.

[Report]({{REPORT_URL}}) · [Evidence]({{EVIDENCE_URL}}) · [Reproduction and scope]({{REPRODUCE_URL}})

Scope: same-host non-production fixture, not provider reconciliation, distributed durability, or a general replay-safety guarantee. Runtime source is referenced by pin/hash inventory rather than redistributed; that source audit requires retrieval, while the retained evidence checks run offline.
