# Final gates

The final code and permanent tests were frozen before the eight-trial confirmatory capture.
No capture/verifier/test source changed afterward. The [checks.json](checks.json) inventory
retains exact argv, environment selections, cwd, timestamps, durations, exits and log hashes.
[Capture output](capture.log) and [derived results](results-derived.json) identify the final runs.

| Gate | Observed result | Log |
|---|---|---|
| Final confirmatory capture | Exit 0; 8 valid, 0 invalid primary trials | [capture](capture.log) |
| In-place raw evidence verification | Exit 0 | [verification](verify-in-place.log) |
| Version-independent old property | Expected exit 1; verified evidence; 3 failures on status/requirement/state only | [old RED](property-old.log) |
| Version-independent candidate property | Exit 0; all 3 crash trials and clean control satisfy predicate | [new GREEN](property-new.log) |
| Second implementation raw audit | Exit 0; all 8 rows match main derivation | [audit](independent-audit.log) |
| Pinned Git working-byte/source audit | Exit 0; 77 baseline and 80 candidate files | [source audit](source-audit.log) |
| Relocated retained verifier plus second auditor | Exit 0; `-I -S`; tested read/network/process denials | [relocation](relocated-offline.log) |
| Focused tests, real process environment enabled | **88 passed**, 0 skipped, 46.12s | [focused](focused-tests.log) |
| Full inherited suite, real process environment enabled | **503 passed, 22 skipped**, 68.87s | [full suite](full-tests.log) |
| Ruff | Exit 0 | [lint](ruff.log) |
| Fresh mypy (`--no-incremental`) | Exit 0; **90 source files** | [types](mypy.log) |
| Scoped formatting | Exit 0 | [format](format.log) |
| Modified Node client syntax | Exit 0 | [Node](node-client.log) |
| Git whitespace | Exit 0 | [whitespace](whitespace.log) |
| Formatter before publication | Expected exit 1; remote branch does not equal local HEAD | [refusal](formatter-unpublished.log) |

The 22 skips are optional inherited fixtures: 19 SafeAgent tests requiring isolated release
venvs, 1 native macOS isolation fixture, 2 TrueForge fixtures. No Reality Layer tests were skipped.
The old property exit 1 and formatter refusal are expected negative outcomes, not hidden gate
failures. The upstream project's entire test suite was not rerun; this gate concerns the stated
boundary and Crashpoint's capture/verifier regressions.

The 88 focused tests combine the unchanged 68 historical tests with 20 new comparison tests:
13 semantic mutations, 2 disposable guard-removal controls, 1 old/new plus historical preservation
check, and 4 real-process profile captures. Focused and full passes each ran the actual historical
failure/retention cases and the new protocol cases. QA artifacts are separated in ignored `work/`;
[run-catalog.json](run-catalog.json) records their identities, errors and classifications.

The final documentation and publication list were checked afterward for whitespace, artifact/link
existence, exact frozen-source equality, absence of unpublished upstream source, preservation of
sibling checkouts and process cleanup. No broad test suite was repeated after its final pass.
