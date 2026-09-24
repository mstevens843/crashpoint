# Bounded adversarial self-review

Implementation and review used one agent, without subagents. Independence here means fresh
observer processes, re-derivation from raw artifacts, and a second stdlib-only audit implementation.
It does not mean a separate author, organization, host or hostile-process security boundary.
See the [report](../../results/15-reality-layer-recovery-followup.md), [gates](GATES.md),
[tests](../../tests/test_reality_layer_recovery.py) and [closeout](closeout.json).

| Risk | Actual check | Observed result | Remaining limit |
|---|---|---|---|
| A global pin replacement erases historical behavior | Explicit immutable version profiles; original verifier defaults and all 68 historical tests retained | Published nine-case bundle verifies; inherited mutations and retention cases pass | Historical runtime is not recaptured as nine new primary trials |
| Prediction agreement is mistaken for recovery success | Same property function after successful raw verification; old/new CLI exit checks | Old evidence valid and prediction agrees, but all 3 old crashes are RED on status/requirement/state; candidate is GREEN | Three repetitions give no reliability rate |
| A synthetic STARTED record or direct recovery call fakes startup | Real MCP dispatch, receiver ack, explicit adapter barrier, pre-kill STARTED bytes, observer binding, -9 exit, fresh server and retained-state hash | Six genuine primary process deaths; same action ID queried after restart | Same-host process death, not host or power loss |
| Manifest summaries or false UNKNOWN assertions override raw bytes | Re-signed mutations of summaries, action/payload identities, status, pins, inventories and Boolean types | All 13 new mutations reject for their specific diagnostics | Coordinated rewriting of code, raw bytes and hashes remains outside the claim |
| A missing startup evidence guard permits a post-query transition to masquerade as startup recovery | Disposable source copy disables only `startup_query_equality`; mutated startup bytes remain STARTED while public query says UNKNOWN | Normal/restored verifier rejects with `startup_query_equality`; disabled check accepts, so the negative regression catches its absence | Other untargeted verifier omissions are not exhaustively modeled |
| A disabled verdict makes baseline falsely GREEN | Disposable recovery function returns unconditional satisfaction | Verified baseline incorrectly exits 0 in mutant; permanent regression catches intended RED failure; restored function exits 1 | This proves this specific regression has discriminatory power |
| API contract changes contaminate interpretation | Old 3-field reconcile request, new action-ID-only request; actual forged MCP and authenticated REST probes | MCP HTTP 200 schema tool error on `evidence_note`; REST HTTP 200 unchanged UNKNOWN; independent effects remain 1 | Only the virtual adapter; no Home Assistant or trusted terminal adapter |
| Probes or runtime failure are hard-coded into evidence validity | Probe outcomes and route prediction agreement are derived fields; subject property verdict is separate from artifact validity | Both final adjuncts satisfy trust boundary and predicted route behavior | Transport/schema incompleteness can still invalidate an attempted fixture observation |
| Hashes are mistaken for independent effect truth | Fresh receiver observers plus independently written stdlib raw auditor that imports no Crashpoint implementation | All 8 status/requirement/effect rows agree; raw request/action/attempt and receiver-chain bindings checked | Same author/user/host can modify all files |
| Offline verifier silently reads original source or executes runtime | Relocation with `-I -S`, actual loaded module-path assertion, tested audit-hook denials | Retained verifier and second audit pass with original reads, socket creation and external processes blocked | Evidence hashes cannot authenticate omitted unlicensed runtime source |
| Retention or cleanup fails after measurement | Inherited real entry-point failpoints: artifact read, both receipt destinations, finalizer, cleanup-close; confinement/receipt mutations retained | Final focused 88 and full 503 tests pass; invalid identified attempts remain in writable manifests; no missing bytes counted as zero | Disk-wide unwritability cannot guarantee persistence |
| Existing user work is overwritten | Separate worktree; before/after byte inventories and Git status checks | 2,272 historical evidence files and all 9,111 original experiment files preserved; both subjects and main unchanged | Ignored unrelated caches are outside the inventory; no task action touched them |
| Leaked processes or private configuration | Allowlists, copied pinned source, loopback receiver, finally cleanup; inspect every observed spawned PID | 112 primary children reaped; all 800 primary/development/QA PIDs absent at closeout | Local user isolation only |
| Draft claims publication before a real push | Read-only formatter checks actual remote SHA and every publication file against that commit | Prepublication execution refuses with exit 1; draft links marked UNPUBLISHED | Successful pushed-commit output necessarily awaits the user's publication |

Development observations were kept separately. The first candidate smoke capture completed the
measurements but was marked invalid because the verifier expected `arguments.outcome is not
allowed`; sorted request keys actually produce `arguments.evidence_note is not allowed`. That
was a verifier expectation defect, corrected from retained wire bytes. Its original invalid
receipt was not rewritten. A test collection error was fixed by importing helpers through the
existing `tests` package. Initial lint/type feedback was corrected before final freezing.

A later review tightened the startup-state hash/chronology and pre-kill action/payload binding,
and separated the adjunct probe verdict from evidence validity. A post-reconciliation snapshot
makes that separation explicit. Affected real-process captures were rerun before the final
freeze. The [catalog](run-catalog.json) preserves 26 development/superseded trials, including the
one invalid candidate attempt, separately from the final 8. Broad final gates ran once after
implementation was stable. Detailed development logs and bundles remain under ignored
`work/reality-layer-recovery/` and are not in the publication file list.

The final claim was checked against the original consumed/lost-plan limitation: rejection proves
this original plan did not create another effect. Direct executor redispatch, concurrent writers,
terminal-history eviction, provider readback attribution, other patch changes and production
readiness are explicitly untested. No adoption pilot, automatic publication or upstream patching
was performed.
