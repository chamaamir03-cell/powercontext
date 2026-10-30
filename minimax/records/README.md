# Records

| File | Meaning |
|---|---|
| `minimax_session.md` | Human-readable MiniMax development record |
| `minimax_prompt.txt` | Exact prompt/context sent to MiniMax |
| `minimax_response.json` | Normalized UTF-8 `mcode exec --output-format json` result |
| `minimax_last_message.md` | Final MiniMax message |
| `code_before.py`, `code_after.py` | Before/after snapshots of `search.py` |
| `ingest.json` | Documents imported into seekdb |
| `seekdb_queries.log` | Actual SQL, row counts, and doc ids used by context |
| `context.json` | Final context output (v2 after spec update) |
| `context_v1.json` | Preserved v1 context captured before the spec update |
| `apply.json` | Patch application result |
| `test_target.log`, `test_regression.log` | Post-patch pytest logs |
| `test_before.log`, `test_after.log` | Combined before/after proof |
| `test_before_target.log`, etc. | Per-suite logs with command, exit code, version |
| `before_after.json` | Machine-readable before/after result |
| `powercontext_state.json` | State read in a new process |
| `spec_update.json` | Spec v2 import result |
| `validation.json` | All acceptance checks and command exit codes |
| `validation.log` | Captured stdout/stderr tails for validation commands (20k/10k chars) |

Generated state (`../.state/` and `../.work/`) is not committed.
