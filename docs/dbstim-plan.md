# dbstim Implementation Plan

> Agentic workers: use superpowers:executing-plans task-by-task. Checkboxes record progress.

**Goal:** implement the approved minimal experiment API and marimo demonstration.

**Architecture:** extend existing server assignments/events, then expose typed SDK objects.
Keep storage and presentation separate; preserve historical published experiments.

**Tech stack:** Python, FastAPI/SQLite server, standard-library HTTP client, marimo.

**Spec:** [dbstim-design.md](dbstim-design.md).

## Constraints

Preserve existing dirty work. No production experiment changes, automatic replay,
credentials in Git, new cloud resources, or unsupported image-presentation claims.
Each Markdown file stays ≤500 words. Native execution; no automatic commits/deploys.

## Review focus

- Fractional sharing and all-shared assignments must preserve exact run sizes.
- Lost acknowledgments and competing clients must not duplicate presentation records.
- Legacy block manifests and prior events must retain their identities.
- Foil/repeat media must preserve the actual assigned intervals in every access mode.
- Reactive notebook reevaluation must not create experiments or start sessions twice.

## 1. Assignment structure

Files: website `metric_study_allocation.py`, `experiments.py`, and new
`tests/test_experiment_structure.py`.

- [x] Write failing tests for uniform sessions/runs, 24 trials/4 foils, fixed repeats,
  reproducibility, shared roles, fractional rounding, and unchanged legacy publication.
- [x] Run those tests; confirm failures identify missing behavior.
- [x] Extend validated settings and publication with session/run identities;
  preserve legacy allocation when new settings are absent.
- [x] Run structure, allocation, complementary-foil, and experiment API tests.

## 2. Server progress and media

Files: new website `experiment_sessions.py`, integration in `experiment_routes.py`,
tests `test_experiment_sessions.py` and `test_experiment_api.py`.

- [x] Add failing tests for two-client resume, writer conflicts, idempotent writes,
  unresolved starts, publication/ownership checks, and completed-session invariants.
- [x] Implement persistent session state using existing publication/event identities.
- [x] Test and implement temporary authorized media access with byte ranges,
  rendered-half identity, expiration, and no authentication bypass.
- [x] Run focused server tests and preserve old API behavior.

## 3. Typed SDK

Files: SDK `src/dbstim/{client,models,workflow}.py`, `pyproject.toml`, tests.

- [x] Write failing contract tests against HTTP fixtures for all six objects.
- [x] Implement the approved methods, exact-name lookup, fixed revisions, uniform
  creation, server-owned resume, reusable verified downloads, and three media modes.
- [x] Run SDK tests including existing complementary-half/repeat regressions.

## 4. Notebook, documentation, rename

Files: SDK `notebooks/api.py`, README, existing docs, demo tests, known consumers.

- [x] Replace the Jupyter example with a short action-gated marimo demo; preserve
  user outputs outside Git before removing superseded material.
- [x] Test notebook syntax/structure; trim each Markdown document to ≤500 words.
- [x] Update imports/dependencies and rename GitHub repository to `lobennett/dbstim`
  using authenticated GitHub tooling; update the local remote without moving unrelated work.
- [x] Run full SDK and focused server suites, review the final diff, and report
  tested functionality separately from server deployment and unresolved limitations.
