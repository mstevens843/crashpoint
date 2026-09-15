# Recorded CrewAI evidence artifact

After tests, lint, and type checking pass, CI publishes
`crewai-retry-recorded-evidence-<run-id>-<attempt>` with these exact repository files:

- `evidence/crewai_retry.json`
- `results/11-crewai-retry-prediction.json`
- `results/11-crewai-retry.md`

`manifest.json` records each file's SHA-256 and size, the original receipt and
reported runtime revisions, and the publishing repository, commit, workflow,
run ID, run attempt, and job key. Packaging verifies the existing receipt and
checks that all three files match the checked-out Git commit byte for byte.

This CI run publishes a previously recorded experiment. It does not establish
when the 90 trials ran or attest the original checkout's cleanliness. The
`reported_crashpoint_commits` field preserves what the receipts originally
reported; the publication commit identifies the files uploaded now. CI's live
unit/integration tests are separate from the archived 90 trials.

The case of interest is `post_effect`: CrewAI 1.15.21, source revision
`a8d330de00812e52356f32d32c715b86392bfd41`, 30 recorded trials with two effects
per trial. The harness obtained the effect counts and IDs from its external
ledger after the worker exited. It measured the same-process tool retry with a
scripted local LLM. The full method and limits are in the original report.

## Verify and share

The CI job summary exposes the artifact ID, URL, and uploaded archive SHA-256.
That archive digest differs from the SHA-256 of `crewai_retry.json` inside it.
To resolve the numeric job ID, use the run's jobs API; `GITHUB_JOB` is a job key,
not a numeric ID:

```bash
gh run view RUN_ID --repo mstevens843/crashpoint --attempt ATTEMPT --json jobs
gh api repos/mstevens843/crashpoint/actions/runs/RUN_ID/artifacts
gh run download RUN_ID --repo mstevens843/crashpoint --name ARTIFACT_NAME --dir /tmp/crewai-artifact
```

Verify the per-file hashes against `manifest.json`, then compare the evidence
to `https://github.com/mstevens843/crashpoint/blob/COMMIT/evidence/crewai_retry.json`.
Artifacts have a requested retention of 90 days and can expire or be deleted;
the commit-pinned files provide a longer-lived reference.

Local packaging is available without CI credentials:

```bash
uv run python -m crashpoint.harness.crewai_retry_artifact --output /tmp/crewai-recorded-preview
```

The output directory must be new. Local manifests say `local_preview` and
contain no fabricated GitHub run or artifact identifiers.

SABLE can evaluate this bundle as a historical source record. Uploading it does
not make it a fresh SABLE-native execution or add before/after environment
hashes. Admission remains SABLE's determination. Its paid audit pilot is a
separate offer from this public evidence contribution.
