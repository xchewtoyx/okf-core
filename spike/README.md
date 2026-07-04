# Issue #117 spike: ruamel.yaml as the frontmatter-merge editing core

Evaluates whether `ruamel.yaml`'s round-trip loader/dumper can replace the
hand-rolled node-mark span-splicing in `src/okf_core/patching.py`'s
`_merge_frontmatter()`. This is a spike only — not wired into the package,
not intended to ship as-is.

## What's here

- `patching_ruamel_prototype.py` — a ~120-line `plan_frontmatter_merge_ruamel()`
  that reuses the existing infra unchanged (`_plan_document_change`,
  `_validate_frontmatter_update_value`, `_yaml_values_equal`, no-op detection)
  and only replaces the YAML-editing core with `ruamel.yaml`.
- `test_frontmatter_patching_ruamel.py` — the actual PR #116 test file
  (`tests/test_frontmatter_patching.py`), copied verbatim with only the
  `plan_frontmatter_merge` import swapped to the prototype.
- `explore.py` — small standalone probes of specific ruamel.yaml behaviors
  (comments, quoting, duplicate keys, anchors/aliases, dates, zero-width
  nulls, mixed line endings) used to build the findings below.

## Reproducing

```
python3 -m venv .venv-spike
.venv-spike/bin/pip install -e . pytest ruamel.yaml
.venv-spike/bin/python -m pytest spike/test_frontmatter_patching_ruamel.py -q
```

## Result: 42/49 of the existing test corpus passes unmodified

The 7 failures, categorized:

1. **Untouched flow-collection spacing gets renormalized** on whole-document
   dump (`[docs,platform]` -> `[docs, platform]`) — a real gap against the
   "untargeted bytes stay byte-identical" guarantee, though narrow (only
   affects flow collections with non-canonical spacing).
2. **Shape changes re-render in ruamel's canonical style** rather than our
   current exact-column reindent (scalar -> list, list -> scalar). Not wrong,
   just a different, currently-undocumented output convention.
3. **`None` dumps as blank** (`owner:`) instead of PyYAML's literal `null`.
   Fixable with a custom representer; a representation decision either way.
4. **CRLF handling needs deliberate design.** Naive `\n` -> line_ending
   post-processing double-CRs some lines because ruamel retains a literal
   `\r` in some comment-adjacent tokens when parsing `\r\n` source. Solvable,
   but not free — today's raw text splice sidesteps this entirely.
5. **Editing the anchor-defining key silently reassigns the anchor** to the
   other key instead of rejecting the edit. Conflicts with the explicit
   alias-rejection contract shipped for issue #111 (PR #116) — an
   identity-based alias guard equivalent to `_alias_linked_keys()` would need
   to be re-added regardless of library choice.

## What ruamel.yaml removes for free

Duplicate-key detection (native `DuplicateKeyError` — could also retire
`documents.py`'s hand-written `_UniqueKeySafeLoader`), zero-width-null
splicing (no manual "insert a separating space" workaround), and everything
in `_compose_frontmatter` / `_top_level_nodes` / `_node_key_line` /
`_serialize_replacement_value` / `_dump_yaml*` / `_strip_yaml_document_end` /
`_reject_generated_yaml_aliases` — roughly 150-180 of the ~305 lines in the
frontmatter-merge module.

## What it does not remove

`_validate_frontmatter_update_value` and `_yaml_values_equal` encode our own
mutation-contract policy (accepted value types, exact-type no-op semantics)
independent of the underlying YAML library, and some form of the alias
identity guard (finding 5) still needs to exist on top of ruamel.

## Dependency and performance

`ruamel.yaml` 0.19.1: MIT license, zero required transitive dependencies,
declares support through Python 3.14 (comfortably covers this project's
3.10-3.13 CI matrix). ~2.4x slower per parse than PyYAML's `compose()` on a
tiny document (619us vs 262us) — negligible in absolute terms for
file-at-a-time operations.

## Recommendation

Worth adopting, but not a drop-in swap: migration should be its own
separately reviewable change (per issue #117's acceptance criteria) that
deliberately decides whether to relax "byte-identical forever" to something
weaker, designs CRLF handling on purpose, re-adds an explicit alias guard,
and picks a null-representation policy. Do this after PR #116 merges, not
folded into it.
