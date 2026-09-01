from crashpoint.harness.deferred_runtimes import DEFERRED_RUNTIMES, deferred_prefixes
from crashpoint.model.runtimes import RUNTIME_IDS


def test_deferred_runtimes_have_actionable_blockers() -> None:
    assert DEFERRED_RUNTIMES
    for runtime in DEFERRED_RUNTIMES:
        assert runtime.blocker
        assert runtime.next_step
        assert runtime.source
        assert "validated" in runtime.blocker or "faithful" in runtime.blocker


def test_deferred_runtimes_are_not_silent_model_rows() -> None:
    for prefix in deferred_prefixes():
        assert not any(runtime_id.startswith(prefix) for runtime_id in RUNTIME_IDS)


def test_vercel_local_world_is_modeled_and_managed_world_stays_deferred() -> None:
    # The Local World rows entered the model once they had a faithful crash harness and evidence;
    # the managed Vercel World has neither, so it is the entry that remains deferred.
    for rid in ("r_vwf_naive", "r_vwf_idem", "r_vwf_nondet", "r_vwf_twophase"):
        assert rid in RUNTIME_IDS
    [vercel] = [r for r in DEFERRED_RUNTIMES if r.adapter_id_prefix == "r_vwf_managed_"]
    assert "world-vercel" in vercel.blocker
    assert "vercel_matrix" in vercel.blocker
    assert not any(rid.startswith("r_vwf_managed_") for rid in RUNTIME_IDS)
