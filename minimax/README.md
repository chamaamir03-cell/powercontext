# MiniMax Engineering Context

This module provides an executable MiniMax coding workflow. It indexes real project files in seekdb or OceanBase, returns matching current requirements/design decisions/code/tests, asks the MiniMax API to produce a patch, applies the patch to the selected Git checkout, runs the test command you provide, and saves task progress and verification results through PowerContext.

The one-page module overview is in [TECHNICAL-OVERVIEW.md](./TECHNICAL-OVERVIEW.md); the competition acceptance matrix and run evidence are in [ACCEPTANCE.md](./ACCEPTANCE.md).

## What it does

- Re-indexes the current working tree, including modified files, instead of relying on a baked-in answer or stale repository snapshot.
- Retrieves source-linked excerpts from specifications, API contracts, accepted decisions, implementation files, and tests.
- Excludes documents marked `superseded`, `rejected`, `obsolete`, `inactive`, or `draft` in Markdown front matter. The current version at a path replaces the prior indexed version.
- Records task status, source references, database/retrieval metadata, changed file paths, MiniMax model/request/token usage, and validation outcome in a project-specific PowerContext scope.
- Captures task, requirements, code, and test fingerprints with each validation. A later change to any of them marks that result stale when context is prepared again.
- Refuses to complete a task without a passing validation for the current code and tests; a previously completed task reopens if those sources change.
- Executes validation commands as argument lists without a shell and persists the exit status even when tests fail.

The MiniMax request sends the task description and retrieved source excerpts to MiniMax. Do not use it on source you are not allowed to share with that provider. API keys are read from environment variables, never included in the prompt or saved state. PowerContext stores task metadata and source/hash references, not source excerpts or credentials.

## Live MiniMax links

- [MiniMax Open Platform](https://platform.minimax.io/) — create/manage the API key and enable the account's model access.
- [Official Chat Completions API documentation](https://platform.minimax.io/docs/api-reference/text-chat-openai) — OpenAI-compatible API.
- [MiniMax API endpoint](https://api.minimax.io/v1/chat/completions) — `POST`, model default `MiniMax-M3.1-Flash`.
- [MiniMax China Open Platform](https://platform.minimax.cn/docs/guides/quickstart-preparation) — China-region accounts use `https://api.minimax.cn/v1` and M Plan users use their subscription Key.

The client uses the documented HTTPS endpoint, Bearer authentication, JSON chat-completions request, and JSON response format. Model and endpoint can be configured for accounts in another region.

## Configure

Use Python 3.10+ and install [requirements.txt](./requirements.txt). For local embedded seekdb, use Python 3.11+ on a platform supported by [pylibseekdb](https://pypi.org/project/pylibseekdb/) and install [requirements-seekdb.txt](./requirements-seekdb.txt) instead:

```bash
python3 -m pip install -r minimax/requirements.txt
# Or, for embedded seekdb:
python3 -m pip install -r minimax/requirements-seekdb.txt
```

Set a MySQL-protocol URL for a seekdb or OceanBase database and PowerContext API credentials. Alternatively, set `ENGINEERING_SEEKDB_PATH` to an isolated local seekdb data directory; the module opens the official local runtime, connects using PyMySQL and closes it on exit. Set only one of these two database settings. Keep credentials in the environment or a local secret manager; do not commit them.

```bash
export ENGINEERING_DATABASE_URL='mysql+pymysql://USER:PASSWORD@HOST:2881/DATABASE?charset=utf8mb4'
export POWERCONTEXT_URL='http://127.0.0.1:8000'
export POWERCONTEXT_TOKEN='your-local-powercontext-token'
export MINIMAX_API_KEY='your-MiniMax-API-key'
```

### Credentials the evaluator must provide

The **live acceptance demo requires an active MiniMax API credential supplied by the evaluator**. Use the M Plan Subscription Key or a pay-as-you-go API key from the account that will run the demo. This repository does not include, request through source control, or provide a shared key. The demo sends one coding request and consumes that account's quota. For a China-platform key use `https://api.minimax.cn/v1`; for an international-platform key use `https://api.minimax.io/v1`. Use the endpoint for the account where the key was created.

Enter the key without echoing it, in the same terminal that will run the acceptance command:

```bash
read -r -s -p "MiniMax API key: " MINIMAX_API_KEY
echo
export MINIMAX_API_KEY
export MINIMAX_MODEL='MiniMax-M3.1-Flash'
# Choose exactly one endpoint for the account that issued the key:
export MINIMAX_BASE_URL='https://api.minimax.cn/v1'  # China platform
# export MINIMAX_BASE_URL='https://api.minimax.io/v1' # International platform
```

Also requires Python dependencies installed as below and a running, reachable PowerContext service. A local PowerContext 1.4.1 service and its random authentication token can be created with the instructions in the next section; alternatively configure `POWERCONTEXT_URL` and `POWERCONTEXT_TOKEN` for an existing service. The included embedded seekdb runtime is started and initialized by the demo and does not require an external database account. If network policy blocks MiniMax, PowerContext, or package downloads, the evaluator must provide an allowed network path; the script reports connection failures instead of claiming acceptance passed.

PowerContext integration was tested against version 1.4.1 using `/v1/scopes`, `/v1/atomic-memory/list`, and `/v1/memory/remember`. The scope is idempotent per project, and records are filtered by schema and task ID when reloaded. Service, authentication, and save-confirmation errors stop the operation rather than returning success-shaped defaults.

For a clean local PowerContext 1.4.1 setup without Docker, use a separate virtual environment and run the following in the same shell where you will run the demo:

```bash
python3 -m venv "$HOME/.venvs/powercontext-1.4.1"
"$HOME/.venvs/powercontext-1.4.1/bin/pip" install 'powercontext[cli,server]==1.4.1'
export POWERCONTEXT_HOME="$(mktemp -d)"
export POWERCONTEXT_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export POWERCONTEXT_URL='http://127.0.0.1:18081'
export POWERCONTEXT_SERVER_HTTP_HOST='127.0.0.1'
export POWERCONTEXT_SERVER_HTTP_PORT='18081'
export POWERCONTEXT_SERVER_AUTH_ENABLED='true'
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_TOKEN"
export POWERCONTEXT_SERVER_DATABASE_KIND='sqlite'
export POWERCONTEXT_SERVER_DATABASE_URL="sqlite+aiosqlite:///$POWERCONTEXT_HOME/powercontext.db"
export POWERCONTEXT_SERVER_MCP_ENABLED='false'
export POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_ROLE='all'
"$HOME/.venvs/powercontext-1.4.1/bin/powercontext" server run --no-env-file --host 127.0.0.1 --port 18081 --role all &
POWERCONTEXT_PID=$!
```

Wait for startup, then check `curl -fsS "$POWERCONTEXT_URL/health/ready"`. When done, stop this specific process with `kill "$POWERCONTEXT_PID"`. This temporary PowerContext database holds task state; it does not replace the required seekdb/OceanBase engineering index. If the service is already running, use its existing URL and token instead.

Optional MiniMax settings:

```bash
export MINIMAX_MODEL='MiniMax-M3.1-Flash'
export MINIMAX_BASE_URL='https://api.minimax.io/v1'
```

Mark requirements and decisions with front matter so superseded material cannot be treated as current:

```markdown
---
kind: spec
status: accepted
---

Current requirement text...
```

Use `status: superseded` for historical decisions/specifications; those files remain stored with their historical status but are omitted from current task context.

SQLite is supported for local tests and evaluation without a running database. It is not the default runtime backend:

```bash
export ENGINEERING_DATABASE_URL='sqlite:////tmp/engineering-context.db'
```

## Workflow

Index the actual project and prepare a task context:

```bash
python3 -m minimax.engineering_context index \
  --root /path/to/project --project seekdb

python3 -m minimax.engineering_context prepare \
  --root /path/to/project \
  --project seekdb \
  --task-id 'filter-by-tenant' \
  --task 'Add tenant filtering to document search while preserving existing ordering.'
```

`prepare` re-indexes before retrieval and prints JSON suitable for the MiniMax coding assistant. It includes each source path, line range, content hash, and excerpt, as well as current task state and whether previous verification is `not_run`, `current`, or `stale`. Unrecognized explicit lifecycle statuses fail indexing rather than being silently treated as active. Validation records from older versions without requirement or task fingerprints are conservatively treated as stale.

To perform the complete, live MiniMax code-change flow in one command, use a real test command from the target project's own documentation:

```bash
python3 -m minimax.engineering_context implement \
  --root /path/to/project \
  --project seekdb \
  --task-id 'filter-by-tenant' \
  --task 'Add tenant filtering to document search while preserving existing ordering.' \
  --test-command 'python3 -m pytest tests/path/to/test_search.py'

python3 -m minimax.engineering_context status \
  --project seekdb --task-id 'filter-by-tenant'
```

`implement` sends the current task plus matched source excerpts to MiniMax, validates and applies its unified diff within the selected project, runs the explicitly supplied test command without a shell, and records the model, modified paths, and actual test result. If Git rejects a syntactically malformed diff, the module asks MiniMax once to repair it with Git's validation error; the corrected diff must pass the same safety checks. It does not retry rejected paths or other safety violations. A passing test run marks the task complete; a failure is persisted and returned as a nonzero exit code while leaving an applied patch in the worktree for inspection. Patches to secrets, Git metadata, symlinks, binary files, and paths outside the project are rejected. Review the diff before committing it.

For manual editing, `validate` accepts a quoted command and persists its real exit status. If the task, requirements, code, or tests change after a passing run, the next `prepare` marks that result stale. Re-run validation before marking the task complete.

### Live acceptance demo with a disposable project

The bundled [demo](./demo/) deliberately starts with three functional assertion failures and two passing regression tests. It has a current spec, an accepted decision, a superseded spec, code, and tests. The script copies it to a temporary directory, indexes it using a fresh official embedded seekdb, sends the task and retrieved demo excerpts to MiniMax, and uses the running PowerContext instance. It consumes API quota; no production code is sent.

After installing the dependencies, starting/configuring PowerContext, and exporting credentials as described above, run from the repository root in that same terminal:

```bash
python3 -m minimax.run_live_demo --output minimax/live-evidence
```

The output directory must not already exist. To reset, choose a new output directory; the demo project and seekdb database are freshly initialized each run. Logs contain commands, exit codes, context sources, the MiniMax-generated diff and model/request metadata, the same test file, before/after code snapshots, and PowerContext state restored by a separate CLI process. The script checks the required sources, excludes the superseded source, checks that only `inventory.py` changed, reruns the same five tests, and checks restored validation freshness. Review the generated diff and logs before submission; tests alone do not prove full correctness. The required context sources are `specs/inventory.md`, `decisions/filtering.md`, `inventory.py`, and `test_inventory.py`.

## Validation and example

Run the module's tests using local SQLite and a fake PowerContext client:

```bash
python3 -m unittest -v minimax.test_engineering_context
```

The tests cover active versus superseded specifications, retrieval of related code/tests, re-indexing changed specifications, stale verification, PowerContext state restoration, MiniMax request formatting, patch safety/application, and running and recording a real test command. They use local SQLite and mock the external HTTP APIs, so they do not consume API credit or require credentials. For live verification, configure all three service credentials and run `implement` against a disposable checkout first.
