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


def test_vercel_workflow_blocker_records_local_world_failure() -> None:
    [vercel] = [r for r in DEFERRED_RUNTIMES if r.adapter_id_prefix == "r_vercel_workflow_"]
    assert "workflow@5.0.0-beta.47" in vercel.blocker
    assert 'Invalid version string: "bundled"' in vercel.blocker
