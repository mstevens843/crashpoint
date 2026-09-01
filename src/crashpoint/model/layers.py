"""The vocabulary the whole model is derived from.

WHAT THIS IS. The enumerated outcome of a crash-and-recover trial, plus the orthogonal properties of
a runtime and of a crash barrier that decide, together, what that outcome will be. Every other file
in ``model/`` is built out of these, and ``predict.py`` is a pure function over them.

WHY IT EXISTS SEPARATELY. The point is to model the durable-execution lifecycle before crashing a
single runtime (the discipline that corrected the model in public in the two sibling projects). The
model has to name its terms once so the predicted outcome matrix is a derivation over declared
properties, not a hand-filled table a reviewer cannot check.

WHAT WAS REJECTED. A single "durability score" per runtime. It is a lie: whether an external side
effect duplicates depends on the ORDER of the effect and the persist write, on whether the effect is
idempotent, and on WHERE the crash lands - three orthogonal axes, not one scalar. The whole finding
lives in their combination, so they are kept separate on purpose.

WHAT THIS IS NOT. It is not a description of any runtime's internals. It is the abstract model those
runtimes are instances of; the real crash injection lives in ``adapters/`` and ``crash/`` and the
harness exists to catch the model being wrong.
"""

from __future__ import annotations

from enum import Enum


class Outcome(Enum):
    """What the out-of-process ledger says happened to the external side effect after a crash and a
    recovery.

    EXACTLY_ONCE - the effect crossed the boundary once. The ledger holds one entry for the intent.
    DUPLICATED   - the effect crossed two or more times. Recovery re-ran a step whose effect had
                   already happened, and nothing deduped it. This is the money failure: a customer
                   charged twice.
    LOST         - the effect never crossed, though it was required. Recovery skipped a step marked
                   complete before its effect actually ran. A customer never charged for a completed
                   order.
    DIVERGED     - the effect crossed two or more times AND the crossings differ in content. Not a
                   duplicate: two DIFFERENT charges, not one charge twice. Strictly worse, because
                   the usual reconciliations do not reach it - you cannot dedup by matching, and you
                   cannot refund "the second one" without deciding which was real. This is what a
                   crash does to a step whose effect payload was not reproducible from the step's
                   durable inputs. See ``Determinism``.
    VOID         - the ledger cannot certify the trial: the system under test touched the ledger's
                   storage, the hash chain broke, or the ledger's own integrity was in doubt.
                   Fail-closed - doubt is never scored EXACTLY_ONCE. This is the IN_DOUBT/UNKNOWN
                   outcome from the durable-agent-outbox, and it is only ever produced by the oracle
                   or the reflexive adversary, never predicted for a normal crash.
    """

    EXACTLY_ONCE = "exactly_once"
    DUPLICATED = "duplicated"
    DIVERGED = "diverged"
    LOST = "lost"
    VOID = "void"


class Durability(Enum):
    """Does the runtime persist a step's completion at all?

    DURABLE - it writes a completion marker/checkpoint, and recovery consults it.
    NONE    - it does not; on a crash the whole thing is re-run from the top (the null baseline).
    """

    DURABLE = "durable"
    NONE = "none"


class PersistOrder(Enum):
    """The order in which the runtime does the external effect and writes the completion marker.
    This is the axis the whole matrix turns on.

    EFFECT_THEN_PERSIST - the natural at-least-once shape: do the effect, then mark it done. A crash
                          between them makes recovery re-run the effect (DUPLICATED unless idem).
    PERSIST_THEN_EFFECT - the inverse bug: mark it done, then do the effect. A crash between them
                          makes recovery SKIP the effect that never ran (LOST). The lost_control.
    RACE                - the two writes are unordered on a shared executor, so which one wins - and
                          therefore whether recovery replays or re-executes - is host-dependent.
                          This is langchain-ai/langgraph#8039. Predicts DUPLICATED for a naive (the
                          failure the issue reports); the harness measures the actual host-dependent
                          rate.
    """

    EFFECT_THEN_PERSIST = "effect_then_persist"
    PERSIST_THEN_EFFECT = "persist_then_effect"
    RACE = "race"


class EffectMode(Enum):
    """How the external effect identifies itself at the ledger boundary.

    NAIVE      - the effect fires directly; a re-run crosses the boundary again.
    IDEMPOTENT - the effect is keyed by a content-derived idempotency key and deduped at the ledger,
                 so a deterministic re-run records a second ATTEMPT but only one side effect.
    TWO_PHASE  - the effect uses an identity prepared before the nondeterministic draw and carried
                 through the external effect. A replay can redraw the payload, but it reuses the
                 already-prepared identity, so the second attempt is deduped before it crosses.
    """

    NAIVE = "naive"
    IDEMPOTENT = "idempotent"
    TWO_PHASE = "two_phase"


class Determinism(Enum):
    """Is the step's external effect reproducible from the step's DURABLE INPUTS alone?

    This axis was added after the model's first four rows were measured, in response to
    @vasilisnasopoulos on langchain-ai/langgraph#8039: an idempotency key derived from what the
    action IS only survives a crash if replaying the step actually reproduces that action. The test
    he named is the definition used here - can a process that did not run the step recompute the
    effect's identity from the durable inputs alone?

    DETERMINISTIC    - yes. Replay reproduces the same effect, so a content-derived key is stable
                       across the crash and the ledger dedups the re-run.
    NONDETERMINISTIC - no. The effect's content depends on a draw made DURING the step - a model
                       call, a sampled value, a clock read - which does not exist until after the
                       step has already run. Replay produces a DIFFERENT effect, so the re-derived
                       key differs, the dedup misses, and the second crossing is not even the same
                       action. The idempotent boundary does not fail loudly here; it silently stops
                       applying.
    """

    DETERMINISTIC = "deterministic"
    NONDETERMINISTIC = "nondeterministic"


class Phase(Enum):
    """Where the crash lands, relative to the runtime's (effect, persist) sequence. The three
    barriers are named by phase so a column is comparable across runtimes with different orders.

    BEFORE  - before anything durable-relevant has happened (no marker written, effect not done).
    BETWEEN - the dangerous middle: exactly one of {effect, persist-marker} has happened, not both.
    AFTER   - after both the effect and its completion marker.
    """

    BEFORE = "before"
    BETWEEN = "between"
    AFTER = "after"
