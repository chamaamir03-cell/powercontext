# MiniMax Engineering Context Acceptance

The competition brief defines six mandatory checks; any failed check means the submission does not pass. This record reflects the live run in `live-evidence-retry-2/`.

| Requirement | Result | Evidence |
|---|---|---|
| Runs from delivered instructions | Pass: the live acceptance script runs with a temporary project and a fresh embedded seekdb database; PowerContext 1.4.1 runs as a local authenticated service. | `README.md`, `run_live_demo.py`, `live-evidence-retry-2/run-metadata.json` |
| MiniMax participates in the actual code change | Pass: MiniMax-M3.1-Flash returned and applied a change to `inventory.py`. Request ID is recorded; no API key or private model reasoning is stored. | `live-evidence-retry-2/run-metadata.json`, `live-evidence-retry-2/change.diff`, `live-evidence-retry-2/implementation.log` |
| Relevant context is correct and responds to a spec update | Pass: retrieved the active specification, accepted decision, code, and tests; excluded the superseded specification. A separate live re-indexing run against embedded seekdb changed the specification, queried again, and retrieved the changed excerpt. | `live-evidence-retry-2/context.log`, `spec-refresh-evidence/report.json`, `spec-refresh-evidence/before-context.json`, `spec-refresh-evidence/after-context.json` |
| seekdb is used for project context | Pass: the live run records the SQL dialect as `mysql`; the bundled runtime is embedded seekdb 1.4.0, installed using `pylibseekdb` 1.4.0.post2. | `live-evidence-retry-2/run-metadata.json`, `live-evidence-retry-2/context.log` |
| PowerContext state is saved and reused | Pass: implementation and test results were persisted and independently read back as completed/current. | `live-evidence-retry-2/state.log`, `live-evidence-retry-2/restored-context.log` |
| Code change is effective, with tests unchanged | Pass: the same five tests have three functional assertion failures before the change and all pass afterward. The live script verifies that only `inventory.py` changed and the test file hash is unchanged. | `live-evidence-retry-2/before-tests.log`, `live-evidence-retry-2/after-tests.log`, `live-evidence-retry-2/test_inventory.py`, `live-evidence-retry-2/run-metadata.json` |

The test set covers the existing default behavior and warehouse filtering, plus supplier filtering, combined filters, and no-match behavior. The implementation preserves input order and uses AND semantics when both filters are supplied. The spec-refresh run used real seekdb and PowerContext without calling the model again; the code-change run used the actual MiniMax API.

## Reproducibility

Follow the environment setup and run command in [`README.md`](./README.md#live-acceptance-demo-with-a-disposable-project). Every run uses a temporary working tree, fresh seekdb database, and unique PowerContext project scope; choose a new evidence directory each time. Model responses may differ. The script checks sources, the re-imported spec, actual changed paths, the same functional tests, and persisted validation freshness.

Earlier attempts are retained for transparent diagnostics: `live-evidence/implementation.log` records a malformed model diff rejected before application, and `live-evidence-retry-1/context.log` records a transient PowerContext timeout. The succeeding live run is `live-evidence-retry-2/`.

## Automated regression tests

```bash
.venv/bin/python -m unittest -v minimax.test_engineering_context
```

The suite covers current versus superseded specifications, updated-spec indexing, task/spec/code/test validation freshness, state restoration, actual test-command execution, safe patch handling and repair, and PowerContext API response behavior.
