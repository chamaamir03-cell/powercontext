# MiniMax 600077 — SDD Context Module

This directory contains the submission for the MiniMax × OceanBase task
**“规有所依——面向软件持续演进的 SDD 上下文管理系统”**.

The module turns a development task into a verifiable context pack:

1. specs, code, and tests are imported into **seekdb**;
2. a task context is selected with SQL queries and deterministic ranking;
3. **MiniMax** receives that context and changes the demo code;
4. **PowerContext** stores the task state, and a new process reads it back;
5. target tests fail before the change and pass after it, with regression tests
   green.

The recorded MiniMax session produced the committed patch. The validation flow
uses the recorded patch (replay mode) so the evaluator does not need a MiniMax
account to reproduce the result.

## Layout

```text
minimax/
  run.py                     CLI entry point
  sdd/                       context module
    config.py                paths, task id, required sources
    store.py                 embedded seekdb document store and SQL search
    context.py               context selection and prompt rendering
    state.py                 PowerContext task-state save/load
    minimax_client.py        MiniMax live mode and recorded-patch replay
    workflow.py              init/context/apply/test/status/validate
    cli.py                   argparse commands
  specs/inventory_search.md  v1 specification
  validation/
    task.md                  validation sample
    spec_update.md           v2 specification used for the re-import check
    required_sources.json    expected sources and markers
  demo_project/              inventory project after the MiniMax change
  baseline/                  same project before the change
  patches/price_filter.patch recorded MiniMax diff
  records/                   MiniMax, seekdb, PowerContext and test evidence
  scripts/                   prompt/patch helpers used for the live session
```

## Versions

| Component | Version / source |
|---|---|
| Python | 3.12.12 (validated); 3.11+ expected |
| PowerContext | `powercontext[builtin]==1.2.0` (Memory runtime) |
| seekdb | `pylibseekdb==1.4.0.post1` embedded |
| MiniMax Code | `0.6.13`, OAuth managed login |
| MiniMax model | `MiniMax-M3.1-Flash` (thinking variant) |
| pytest | 9.0.3 |

## Setup

Install the Python dependencies from the repository root. `powercontext`
and the embedded seekdb wheel are pinned in `minimax/requirements.txt`:

```bash
python -m venv .venv
. .venv/bin/activate              # Linux/macOS
pip install -r minimax/requirements.txt
```

Notes:

- `pylibseekdb` provides Linux/macOS wheels only; on Windows use WSL2 or a
  Linux/macOS evaluator.
- If the competition checkout already provides PowerContext, keep that source
  tree on `PYTHONPATH` instead of installing the pin:

```bash
export PYTHONPATH="$PWD/src:$PWD/minimax:$PYTHONPATH"
```

- `run.py` automatically adds the repository `src/` when it exists; the
  requirements pin is the fallback for a clean environment.

## Data initialization

All generated data lives under `minimax/.state/` and is ignored by git.

- `minimax/.state/seekdb/` — embedded seekdb database with the
  `engineering_docs` table (spec, code, and test documents).
- `minimax/.state/powercontext.db` — PowerContext SQLite database holding the
  task-state Memory artifact.
- `minimax/.work/` — throw-away workspaces created by `apply`.

Initialize from a clean state:

```bash
python minimax/run.py reset --yes
python minimax/run.py init
```

`init` imports eight documents:

- `minimax/specs/inventory_search.md`
- the four `baseline/inventory/*.py` files (code before the MiniMax patch)
- the three `baseline/tests/*.py` files

## CLI entry point

The module exposes a CLI; every command prints JSON.

```bash
# 1. Build the context for the task. This queries seekdb and loads state from PowerContext.
python minimax/run.py context \
  --task "Add optional min_price and max_price filters to inventory.search.search_products; keep existing behavior."

# 2. Apply the recorded MiniMax patch to a clean baseline workspace.
python minimax/run.py apply --mode replay

# 3. Run the target and regression tests.
python minimax/run.py test

# 4. Read the saved state in a new process (the process restarts each command).
python minimax/run.py status

# 5. Replace the spec in seekdb and rebuild the context; it must use v2.
python minimax/run.py spec-update --file minimax/validation/spec_update.md
python minimax/run.py context \
  --task "Add optional min_price and max_price filters to inventory.search.search_products; keep existing behavior."

# 6. Prove the target tests fail before and pass after the patch.
python minimax/run.py before-after

# 7. Run every acceptance check end to end.
python minimax/run.py validate
```

`context` output includes:

- `specs`, `code`, and `tests` with real `path`, `doc_id`, `version`, and
  `symbols`;
- `sources` with required and ranked documents;
- `db.queries` with the SQL actually executed against seekdb and the returned
  document IDs;
- `task_state` and `powercontext` metadata, including the number of saved state
  entries and whether a prepared PowerContext excerpt contains the state;
- `context_text`, the exact prompt material a coding agent receives.

### Spec update check

`spec-update` replaces the seekdb row `spec:specs/inventory_search.md` with
`validation/spec_update.md` (marker `SPEC_VERSION: v2` and `REQ-PRICE-STRICT`).
The next `context` call reports `spec_marker: "v2"` and a new `spec_version`.
`records/validation.log` shows the v1 context and the v2 context in the same
run.

### PowerContext state check

`init`, `context`, `apply`, `test`, and `status` are separate processes. `status`
opens `powercontext.db`, reads the Memory artifact, and prints the state written
by the earlier processes. `records/powercontext_state.json` is the captured
result. The state contains completed steps, pending steps, and validation
results.

## Live MiniMax mode

The recorded session is replayable without credentials. To run MiniMax live:

```bash
mcode login --region cn
python minimax/scripts/build_prompt.py
python minimax/run.py apply --mode live --prompt-file minimax/records/minimax_prompt.txt
python minimax/run.py test
```

`apply --mode live` calls `mcode exec` with the context prompt, records the
response in `records/minimax_response.json`, and writes the resulting diff to
`records/live.patch`. Set `MCODE_BIN` if `mcode` is not on `PATH`.

## Tests

```bash
# Target tests (12 cases) and regression tests (12 cases) after the patch.
python -m pytest minimax/demo_project/tests/test_price_filter.py -q
python -m pytest minimax/demo_project/tests/test_regression.py -q

# Before/after proof.
python minimax/run.py before-after
```

The committed evidence is:

- `records/test_before.log` — target tests fail with
  `TypeError: search_products() got an unexpected keyword argument 'min_price'`;
  regression tests pass.
- `records/test_after.log` — target and regression tests pass (12 + 12).
- `records/test_before_target.log`, `records/test_after_target.log` — detailed
  pytest logs with command, exit code, Python version, and timestamp.

## Evidence map

| Acceptance item | Evidence |
|---|---|
| Runnable | `run.py validate` completes; `records/validation.json` has all checks true |
| MiniMax participation | `records/minimax_session.md`, `records/minimax_prompt.txt`, `records/minimax_response.json`, `patches/price_filter.patch` |
| Context correctness | `records/context.json`, `records/validation.log` (v1 and v2 runs) |
| seekdb participation | `records/ingest.json`, `records/seekdb_queries.log`, `db.queries` in context |
| State reuse | `records/powercontext_state.json`, `records/validation.log` (`status` command) |
| Effective code change | `records/test_before.log`, `records/test_after.log`, `records/before_after.json` |
| One-page design | `TECH.md` |

## Known limitations

- Embedded seekdb is a single-writer database; the module opens one store per
  CLI process and does not support concurrent writers.
- Context selection is deterministic but heuristic. The validation task names
  the required sources; other tasks may need additional ranking terms.
- Live MiniMax mode requires a logged-in MiniMax Code CLI or `MCODE_BIN`.
  The acceptance flow uses replay mode and the recorded patch.
- PowerContext is an external competition dependency; it is not vendored into
  `minimax/`. `run.py` uses the parent `src/` when present.

## Reset

```bash
python minimax/run.py reset --yes
```

This deletes only `minimax/.state/` and `minimax/.work/`; source files and
records are kept.
