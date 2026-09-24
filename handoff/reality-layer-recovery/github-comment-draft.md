I reran the original post-effect crash boundary against v1.8.1 (`c9d1ca86969f5567cf771ab8a0f3247770a1dfb7`) and v1.8.2.2 (`4213c479bd9333558522db1ca820d657b99effbf`), with three fresh crash trials and one clean control per version.

After an independently observed receiver commit, SIGKILL, and fresh server startup, all three old trials returned `STARTED / NOT_REQUIRED`; all three candidate trials returned `UNKNOWN / REQUIRED`. Each retained exactly one receiver effect. Original-plan retries were rejected without additional effects; both clean controls completed as `SUCCEEDED`.

Candidate reconciliation conservatively remained unresolved. Forged MCP assertions were rejected by schema validation; legacy REST ignored them. Neither resolved the action or added an effect.

This verifies the narrow startup recovery fix in the same-host fixture. It is not an adoption pilot, reliability estimate, or universal exactly-once claim.

[Report - UNPUBLISHED](../../results/15-reality-layer-recovery-followup.md) | [Evidence - UNPUBLISHED](../../evidence/reality_layer/recovery-1822-20260924)
