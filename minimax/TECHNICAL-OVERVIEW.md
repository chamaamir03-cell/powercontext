# MiniMax Engineering Context — Technical Overview

## Purpose

For each coding task, retrieve the current, relevant engineering contract, implementation, and tests; give that evidence to MiniMax; validate its code change; and retain task and verification state for the next run.

## Module and data flow

```text
CLI request (project, task, task ID, test command)
                    |
                    v
       Project file scanner and lifecycle parser
                    |
                    v
    seekdb/OceanBase SQL index: source chunks,
       paths, kinds, statuses, and hashes
                    |
                    v
       Task-term retrieval and source ranking
                    |
          +---------+----------+
          |                    |
          v                    v
 PowerContext state      MiniMax Chat API
 read / task resume      task + source excerpts
          |                    |
          +--------------------+
                               v
                 Unified diff and path checks
                               |
                               v
                 git apply --check / apply
                               |
                               v
                User-selected test command
                               |
                               v
           PowerContext progress / result write
```

`minimax/engineering_context.py` exposes `index`, `prepare`, `implement`, `validate`, `status`, and `complete` commands. `prepare` reindexes current files before searching; it does not serve a fixed context snapshot. It emits source paths, line ranges, content hashes, lifecycle status, relevant excerpts, prior progress, and validation freshness. Accepted front matter distinguishes specifications and decisions; superseded, rejected, obsolete, inactive, and draft material is excluded. Unknown explicit lifecycle statuses fail closed.

Retrieval tokenizes English terms and overlapping Chinese bigrams, queries indexed source text and paths in seekdb/OceanBase, ranks matches, then includes the best available specification, decision, code, and test evidence within configured result and context bounds. Source paths and hashes make context reviewable and reproducible.

`implement` sends the task and bounded source excerpts to MiniMax-M3.1-Flash (region-specific OpenAI-compatible endpoint). The model returns a unified diff. The module rejects unsafe paths, secrets, symlinks, binary patches, and patches outside the worktree; Git validates the diff before application. A malformed diff may receive one correction request, but all safety checks run again. The explicitly supplied test command runs without a shell. Its actual exit status, duration, source fingerprints, changed paths, and model request metadata are persisted.

## Technology responsibilities

| Component | Responsibility |
|---|---|
| MiniMax API | Proposes the actual code change from task-specific retrieved engineering evidence. The sample records the real model request ID and diff. |
| seekdb / OceanBase | Stores and queries versioned project-source chunks. The sample uses the official embedded seekdb runtime with its MySQL protocol adapter; production deployments can use a seekdb or OceanBase MySQL endpoint. |
| PowerContext | Stores task progress, source references, implementation metadata, and validation results in a project scope. A later CLI process reads this state to resume work and determine whether prior verification is stale. |
| Git and project test runner | Enforce patch applicability and execute the same functional and regression test suite before completion. |

Validation freshness is tied to task text plus active requirements, code, and tests. A change to any of these invalidates an earlier pass; task completion requires a successful validation against current fingerprints.

## Verification

The reproducible run instructions, dependency versions, limitations, acceptance matrix, and logs are in [`README.md`](./README.md), [`ACCEPTANCE.md`](./ACCEPTANCE.md), and [`live-evidence-retry-2/`](./live-evidence-retry-2/). The demo's unchanged five-test suite has three functional failures before implementation and passes afterward. A separate real seekdb and PowerContext run verifies that changing and reindexing an active specification changes the retrieved excerpt; see [`spec-refresh-evidence/report.json`](./spec-refresh-evidence/report.json).
