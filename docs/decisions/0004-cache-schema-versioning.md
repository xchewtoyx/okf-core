# ADR-0004: Cache schema versioning

- **Status:** ACCEPTED
- **Related:** `src/okf_core/cache_db.py` (the module this ADR governs);
  `okf migrate-db`; issue #158.

## Context

The opt-in SQLite cache (`okf_cache_dir`, one `okf-cache.db` per bundle,
#79) carried no record of which `okf-core` wrote it. Schema changes were
absorbed by probing: every open ran `CREATE TABLE IF NOT EXISTS` for the
core tables, then `PRAGMA table_info(concepts)` to decide whether to `ALTER
TABLE ... ADD COLUMN ctime_ns`, catching the "duplicate column"
`OperationalError` that the probe itself made possible. Three separate code
paths wrote schema to the same file with their own `sqlite3.connect`
calls -- the cache plugin (core tables), `search.py` (the `concept_fts`
virtual table), and `find_unlinked_mentions` in `graph.py` (the same FTS
table again) -- and none of them could tell an older file from a newer one,
or an empty file from one an older release had partly populated.

Issue #158 asks for the schema version to be recorded and checked on open,
for ordinary commands to stop performing implicit schema migrations on
existing files, and for an explicit `okf migrate-db` that upgrades an older
cache and reports structured success or failure. Two constraints shaped the
design beyond the issue text:

- `cache.py` imports `graph.py`, and `graph.py`'s `find_unlinked_mentions`
  needs to open the cache. Schema ownership therefore cannot live in
  `cache.py` without an import cycle; it needs a leaf module.
- `validate_bundle` maps every `ManifestProblem` to an error-severity
  finding. A "cache skipped" report routed through that channel would make
  a bundle of valid documents fail validation because of a stale cache.

Four candidate designs were compared before implementation. The pick and the
grafts below are recorded so the comparison does not have to be re-run.

## Decision

1. **`PRAGMA user_version` is the only version stamp.** No metadata table.
   `user_version` exists on every SQLite file, including a zero-byte one,
   reads under an ordinary shared lock, and is written transactionally, so
   a version stamp and the DDL it describes commit or roll back together.
   `CURRENT_SCHEMA_VERSION` is derived from a `MIGRATIONS` registry
   (`len(MIGRATIONS)`), and the runner stamps each version after its step
   runs, so a step cannot forget to stamp and the constant cannot drift
   from the steps.
2. **A file is classified into a closed set, not booleans.**
   `CacheSchemaState` is ABSENT (no file), UNINITIALIZED (a file with no
   `concepts` and no `concept_fts`), CURRENT, OUTDATED, or
   UNSUPPORTED_NEWER. `user_version == 0` is ambiguous -- every release
   before this one wrote 0 -- so it is disambiguated by table presence: a
   version-0 file holding `concepts` **or** `concept_fts` is OUTDATED; one
   holding neither is UNINITIALIZED.
3. **Ordinary commands never change the schema of an existing file.**
   `open_cache` creates an ABSENT or UNINITIALIZED file at the current
   version inside one `BEGIN IMMEDIATE` (re-classifying inside the lock, so
   concurrent first-time openers converge), opens a CURRENT file with a
   single `PRAGMA user_version` read and no DDL or write lock, and raises
   `CacheSchemaError` for OUTDATED and UNSUPPORTED_NEWER. Only
   `migrate_cache` (behind `okf migrate-db`) applies version steps to an
   existing file, and it applies them from the found version forward, each
   an idempotent step, inside one write transaction. Initialization runs
   the same steps from 0, so there is one DDL source of truth rather than a
   "current snapshot" plus "incremental steps" pair that must be kept in
   sync.
4. **`CacheDatabase` is a brand.** Only `open_cache` constructs one, and
   holding one is the proof that the schema is current. The cache plugin
   takes a `CacheDatabase` and does no directory creation, classification,
   or DDL of its own; readers and writers downstream run plain SQL with no
   shape probing.
5. **A skipped cache is a `CacheProblem`, never a `ManifestProblem`.** It is
   its own frozen dataclass carried on `BundleManifest.cache_problems`,
   `BundleGraph.cache_problems`, and `ContextPack.cache_problems`, surfaced
   as a `cache_problems` JSON field and one stderr line, and never an exit
   code. Optional commands (`scan`, `list-concepts`, `graph`, `context`)
   run without the cache; required commands (`search`,
   `unlinked-mentions`) fail with `SearchConfigError` carrying exactly
   `cache schema version X requires migration to Y; run okf migrate-db`.
6. **The FTS index is a derived overlay, not a version step.**
   `concept_fts` is created lazily by search paths, only on a
   `CacheDatabase` (so only on a current file) and only when refreshing;
   `--no-refresh` does not create or rebuild `concept_fts` and returns
   zero rows if the index does not exist. Dropping and rebuilding it is a refresh, not a migration, so
   FTS shape changes never need a `MIGRATIONS` entry. The one exception is
   historical: an unstamped file that holds only `concept_fts` was written
   by an older `okf-core`, and is OUTDATED (decision 2), so an ordinary open
   does not silently add core tables to a legacy file.
7. **One leaf module owns the file.** `src/okf_core/cache_db.py` imports
   only the standard library and `okf_core.config`, and owns the filename,
   connection PRAGMAs, classification, initialization, the migration
   registry, and the public `inspect_cache` / `open_cache` /
   `plan_cache_migration` / `migrate_cache` functions and their result
   types.

## Alternatives rejected

- **A sidecar `okf-search.db` for the FTS index** -- rejected. It would have
  let search DDL be argued out of the "implicit migration" question, but at
  the cost of a second file to version, lock, document, and migrate, and it
  contradicts the README's standing promise that search "does not create a
  separate search database". Decision 6 answers the same question without
  a second file.
- **Schema ownership in `cache.py`** -- rejected on a measured import cycle:
  `cache.py` already imports `graph.py`, which needs the opener. Hence the
  leaf module (decision 7).
- **A schema fingerprint engine** (compare the live `sqlite_master` /
  `table_info` shape against an expected shape and repair the difference)
  -- rejected. It keeps two sources of truth (the fingerprint and the
  steps), reintroduces the per-open probing this ADR removes, and cannot
  distinguish an older file from a newer one, which is exactly the
  distinction `UNSUPPORTED_NEWER` needs.
- **A metadata table (`schema_meta(version)`)** -- rejected. The table needs
  its own bootstrap ("does the version table exist yet?"), which is the
  version-0 ambiguity all over again, while `user_version` is present on
  every file from the first byte.
- **Implicit migration on ordinary open** (the status quo, extended with a
  stamp) -- rejected. Every reader becomes a schema writer, concurrent
  openers race on DDL, the user has no say in when their cache is rewritten,
  and #158 rules it out by name.
- **Treating an FTS-only version-0 file as UNINITIALIZED** -- rejected. It
  is a file an older `okf-core` wrote; classifying it as empty would have
  ordinary `scan` add `concepts` and `links` to a legacy file, the very
  behavior being removed.
- **`ManifestProblem` for a skipped cache** -- rejected because
  `validate_bundle` turns every manifest problem into an error finding.
- **A `CacheLockDisciplineError` runtime guard and a public
  `write_transaction` / raw `Connection` caller API, plus a broad
  corrupt-cache degrade path** -- rejected as scope. Lock discipline (the
  init lock is released before `open_cache` returns; no lock is held across
  `scan_bundle`) is enforced by structure and by tests, not by a runtime
  sentinel; the transaction helper stays private to `cache_db.py`; and the
  only unavailable-cache handling kept is the narrow `sqlite3.Error` ->
  `cache-unavailable` report in the hook manager.

## Revisit trigger

Reopen this decision if a schema change cannot be expressed as an
append-only, idempotent version step (for example a table redefinition that
must preserve rows through a rebuild), if a second cache file becomes
necessary, if running an older `okf-core` against a newer cache file
(downgrade support) becomes a requirement, or if the FTS index ever needs a
change that "drop and rebuild on the next refresh" cannot deliver, such as a
tokenizer change that has to be coordinated with a core-table change.
