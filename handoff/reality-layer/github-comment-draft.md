@shimjaemandu I ran the bounded process-death/readback check against Reality Layer v1.8.1 at `c9d1ca86969f5567cf771ab8a0f3247770a1dfb7`, using the real MCP routes and your upstream client, with a harmless out-of-process receiver replacing only the virtual-light adapter boundary.

- After the receiver committed and the runtime was SIGKILLed before completion, the original action remained queryable as `STARTED` with `reconciliation.required=false` / `NOT_REQUIRED` in 3/3 trials. A public reconciliation attempt returned `changed=false`.
- A kill before the receiver request retained the same status/flags, but fresh readback confirmed a readable empty receiver.
- Retrying the original plan was rejected without another effect. This does **not** reproduce a public duplicate-execution bypass.
- An ordinary injected exception after the effect produced `FAILED`; the explicit unknown-outcome signal produced `UNKNOWN`, which reconciled to `SUCCEEDED` without another effect. I treated the supplied evidence note as your disclosed test primitive.
- Missing/corrupt subject ledgers returned a missing-action response while independent receiver evidence still showed one effect.

The final frozen matrix has nine valid trials. The bundle includes raw API/ledger observations, an offline verifier, mutation and cleanup tests, and a proposed regression for recovered unresolved actions. No production runtime patch is included; the useful policy question is how recovered `STARTED` should enter reconciliation.

[Report]({{REPORT_URL}}) · [Evidence]({{EVIDENCE_URL}}) · [Reproduction and scope]({{REPRODUCE_URL}})

Scope: same-host non-production fixture, not provider reconciliation, distributed durability, or a general replay-safety guarantee. Runtime source is referenced by pin/hash inventory rather than redistributed; that source audit requires retrieval, while the retained evidence checks run offline.
