# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

All recipes use `just`, which invokes `.venv/bin/python` directly — no manual venv activation needed.

```sh
just install       # create .venv and install package + all deps
just test          # run pytest
just fmt           # format with black (destructive)
just check         # black --check (non-destructive)
just lint          # ruff + mypy + actionlint
just ci            # check + lint + test (full local CI equivalent — run before pushing)
just test-matrix   # run pytest across all Python versions via Docker
```

Run a single test file or test function:
```sh
.venv/bin/python -m pytest tests/test_listing.py -v
.venv/bin/python -m pytest tests/test_listing.py::test_list_concepts_identifies_orphan_concepts -v
```

If `.venv` is not set up (e.g. in a CI/remote environment), fall back to the system `python3` with `PYTHONPATH=src`:
```sh
python3 -m pytest tests/test_listing.py -v
```

## Architecture

**okf-core** is a library-first Python toolkit for [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md) bundles — directories of Markdown files with YAML frontmatter. The CLI (`okf`) is a thin layer on top of the public library API.

### Core data flow

```
okf-core.toml ──► load_config() ──► BundleConfig
                                          │
                                    scan_bundle() ──► BundleManifest
                                          │                │
                               build_bundle_graph()        │
                                          │                │
                                     BundleGraph      list_concepts()
                                                      search_concepts()
                                                      build_context_pack()
```

All core operations return **frozen dataclasses with a `.problems` tuple** (not exceptions) so callers always see what was skipped. Exceptions are reserved for functions where any failure makes the result meaningless (e.g. `parse_concept_document`, `load_config`).

### Key modules

| Module | Responsibility |
|---|---|
| `config.py` | Parse `okf-core.toml` → `OkfConfig` / `BundleConfig` (Pydantic) |
| `manifest.py` | Scan bundle files → `BundleManifest` + `ConceptManifestEntry` |
| `graph.py` | Resolve Markdown links → `BundleGraph` with `ConceptLink`s |
| `listing.py` | Filter/annotate manifest entries → `BundleListing` for seed discovery |
| `context.py` | BFS-expand seeds via graph → `ContextPack` for LLM context windows |
| `search.py` | SQLite FTS5 lexical search → `BundleSearchResults` |
| `cache.py` | `pluggy` plugin: SQLite caching for scan + graph; contains `compute_pagerank()` |
| `hooks.py` | Hook specs; `get_hook_manager(bundle)` wires the cache plugin |
| `documents.py` | Parse/validate individual Markdown+YAML concept documents |
| `paths.py` | Bidirectional concept_id ↔ filesystem path conversion |
| `cli.py` | Click commands; emits JSON to stdout, summaries to stderr |

### Plugin / hook system

`pluggy` is used for extensibility. All hook names follow `okf_verb_noun`:
- `start`/`end`/`abort` — whole-phase lifecycle (once per operation)
- `fetch` — substitution/caching hook (returns value or `None` to fall through)
- `enter`/`exit` — per-item observation (always fires both)

The only built-in plugin is `SqliteCachePlugin` in `cache.py`, registered automatically when `bundle.okf_cache_dir` is set.

### Design constraints (abridged)

- All non-standard settings (e.g. `stable_id_field`) are bundle-level only — never in `ProjectDefaults`.
- Unknown OKF frontmatter fields must be preserved unless a change explicitly targets them.
- No mandatory LLM API calls or hosted model dependencies.
- Base OKF spec conformance is the floor; extensions must be opt-in per bundle.

## Coding Etiquette

### Git Commit Messages

Before writing a commit message, apply this test to every sentence:

> "Could a reviewer infer this by reading the diff?"

If yes, cut it. Commit messages answer **why** the change was needed and **why this approach** was chosen — not what the code does. Avoid bullet lists that describe the implementation.

A good commit message completes: *"This change was necessary because ___, and this approach was chosen because ___."*

### Pull Request Descriptions

Lead with the one thing the reviewer most needs to understand that is **not** obvious from reading the diff. A reviewer should finish knowing:

1. What problem this solves and why it matters now
2. The key design decision or tradeoff, and why
3. Anything surprising the diff won't make obvious

Do not restate what the code already shows. To flag a follow-up, link to an existing issue — don't ask the reviewer to investigate.

### Pull Request Comments

When addressing review comments, reply to the thread with the reasoning for your change (or why you chose not to address it) — don't just push fixes silently.

## Delivery Rules

- Tests are mandatory for delivered behavior.
- User-facing behavior changes must update `README.md` and affected docstrings in the same commit.
- PRs only; do not push directly to `main`. Do not merge PRs — the human reviewer merges.
- Do not force-push. Push new commits instead.
- Link issues explicitly (`Closes #N`, `Refs #N`) in PR bodies; do not try to align PR and issue numbers.
- Do not close story issues until the implementation PR has been approved by a human and merged.
