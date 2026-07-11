# Issue #117: YAMLRocks comparison spike

This spike evaluates YAMLRocks 0.2.1 as the editing core for
`plan_frontmatter_merge`, compares it with the ruamel.yaml 0.19.1 prototype,
and probes whether YAMLRocks' JSON Schema support materially helps issue #50.
It is not imported by `okf_core` and does not change production dependencies
or public APIs.

## Reproduce

YAMLRocks requires Python 3.12 or newer. From an existing development venv:

```sh
python -m pip install -r spike/yamlrocks/requirements.txt
python -m pytest spike/yamlrocks/test_representation_probes.py \
  spike/yamlrocks/test_schema_prototype.py -q
python -m pytest spike/yamlrocks/test_round_trip_candidate_probes.py -q
python -m pytest spike/yamlrocks/test_frontmatter_patching_yamlrocks.py -q
python -m pytest spike/yamlrocks/test_frontmatter_patching_ruamel_current.py -q
python -m spike.yamlrocks.benchmark
```

The copied patching tests intentionally fail where an implementation differs
from the production byte-level contract. The focused probes encode observed
library behavior and must pass.

## Results

Tests were run on CPython 3.14.5 on Apple Silicon.

| Suite | Result |
|---|---:|
| Focused representation and schema probes | 25/25 pass |
| Focused cross-library round-trip probes | 5/5 pass |
| Current patching corpus with YAMLRocks | 47/53 pass |
| Current patching corpus with ruamel.yaml | 49/53 pass |
| Earlier ruamel spike against its then-current corpus | 42/49 pass |

The fair current-corpus ruamel baseline includes the same production key
validation and alias guard as the YAMLRocks prototype. That isolates the
editing engines rather than counting already-known wrapper omissions.

### Exact YAMLRocks corpus failures

1. Editing CRLF frontmatter changes its YAML lines to LF, although the closing
   delimiter and Markdown body remain CRLF.
2. Replacing an inline scalar with a list emits block style:

   ```diff
   -tags: [alpha, beta] # keep
   +tags: # keep
   +- alpha
   +- beta
   ```

3. Replacing a block list with a scalar changes the existing shape:

   ```diff
   -tags:
   -  single
   +tags: single
   ```

4. A list added to empty frontmatter uses two-space-indented sequence items
   instead of the production indentless style.
5. Editing an implicit null with an inline comment moves the comment:

   ```diff
   -owner: team # keep this comment
   +owner: team
   +# keep this comment
   ```

6. A `datetime.date` can be serialized by `yamlrocks.dumps`, but cannot be
   assigned to a round-trip document node. The prototype converts that
   `TypeError` into `DocumentChangePlanningError`.

The focused probes also demonstrate that editing any field normalizes
untargeted flow spacing from `[one,two]` to `[one, two]`. Unmodified documents,
including CRLF documents, do round-trip byte-for-byte.

### ruamel.yaml corpus failures

The current-corpus baseline fails four cases: CRLF editing, the same two shape
changes, and its default blank representation for a newly generated null
(`owner:` rather than `owner: null`). It also normalizes noncanonical
untargeted flow spacing.

### Behavior comparison

| Requirement | PyYAML + source splice | ruamel.yaml | YAMLRocks |
|---|---|---|---|
| Untargeted bytes after a focused edit | Yes | No | No |
| Unmodified byte round-trip | N/A | Generally | Yes in probes |
| Comments, quoting, order, multiline values | Spliced unchanged | Mostly | Mostly |
| Noncanonical flow spacing | Preserved | Normalized | Normalized |
| CRLF focused edit | Preserved | Not preserved | Not preserved |
| Duplicate-key error | Project loader | Native | Native with option |
| Alias-linked edit rejection | Project guard | Guard required | Guard required; node metadata helps |
| `date`/`datetime` focused assignment | Supported | Supported | Unsupported |
| YAML scalar resolution | PyYAML/YAML 1.1 behavior | YAML 1.2 | YAML 1.2 default; 1.1 option |

## Performance

Median microseconds per operation from three repeats:

| fixture | operation | PyYAML µs | ruamel µs | YAMLRocks µs |
|---|---:|---:|---:|---:|
| small | parse | 224.2 | 463.1 | 4.2 |
| small | parse+serialize | 382.6 | 780.6 | 5.2 |
| small | parse+mutate+serialize | 379.2 | 758.5 | 5.8 |
| representative | parse | 1328.9 | 2655.6 | 20.3 |
| representative | parse+serialize | 2130.5 | 4196.5 | 26.1 |
| representative | parse+mutate+serialize | 2739.5 | 4817.1 | 29.1 |
| large | parse | 17949.1 | 26459.9 | 183.3 |
| large | parse+serialize | 19587.0 | 37239.6 | 223.6 |
| large | parse+mutate+serialize | 19170.0 | 37233.3 | 237.5 |

YAMLRocks is dramatically faster, but all three are already sub-millisecond
for the small file-at-a-time workload. Performance does not compensate for a
failed preservation contract.

## Dependency and maintenance tradeoffs

| | ruamel.yaml 0.19.1 | YAMLRocks 0.2.1 |
|---|---|---|
| License | MIT | MIT |
| Required Python | 3.9+ | 3.12+ |
| Runtime form | Pure Python | Rust CPython extension |
| Installed size in this venv | 1.4 MB | 1.1 MB |
| Source build | Python packaging | Rust toolchain and maturin |
| Maturity | Established | Pre-1.0 alpha |

YAMLRocks publishes wheels for common supported platforms, but a missing wheel
turns installation into a Rust build. More importantly, its Python floor
excludes Python 3.10 and 3.11, both public `okf-core` runtime targets.

## Issue #50 schema findings

The proof-of-concept can express per-type required fields with `oneOf`, but
YAMLRocks 0.2.1 does not make this a good core implementation:

- `if`/`then` conditionals are silently ignored in the tested release.
- Structurally malformed schemas such as `{"type": 42}` are accepted without
  a diagnostic.
- Validation stops at the first failure.
- A missing type-specific field produces a generic `oneOf` error at `$`; the
  result does not identify `platform`.
- Direct property failures do expose useful `schema_path`, line, and column
  fields.
- JSON Schema has errors, not okf-core's error/warning policy.
- Profile and bundle-local precedence, permissive unknown fields, and
  translation to `ValidationFinding` remain project code.

JSON Schema therefore offers a useful vocabulary for thinking about #50, but
YAMLRocks' built-in validator neither replaces the existing focused helpers
nor improves their diagnostics. Issue #50 should keep its simple,
library-independent `_schema.yml`/profile model and structured findings.

## Code-removal estimate

The production merge implementation and its YAML span/dump helpers occupy
roughly 300 lines in `patching.py`. Either round-trip library could remove
about 150-180 lines covering node-span discovery, replacement serialization,
document-end stripping, and generated-alias scanning.

Both alternatives still need okf-core's update-type policy, exact-type no-op
comparison, safe planning/apply layer, post-merge validation, and explicit
alias mutation policy. YAMLRocks exposes alias metadata directly, making the
guard small, but its prototype is still 163 lines because wrapper and error
translation policy remain.

## Recommendation

If the project keeps the old byte-identical frontmatter contract, retain the
current PyYAML plus source-splice implementation. Both whole-document
round-trip libraries violate that contract.

If the project intentionally relaxes the YAML contract to semantic
object-mutate-reserialize with preserved comments and canonical formatting,
adopt ruamel.yaml in a separate implementation PR. It passes more of the
current corpus, supports the full Python range, supports accepted date and
datetime update values, and is more mature.

Do not adopt YAMLRocks now. It has two present-day blockers: unsupported
round-trip assignment for accepted date values and incompatibility with Python
3.10/3.11. Reconsider it only after it supports the project Python range (or
the project drops older versions), stabilizes its API, validates schemas
strictly, and supports all accepted mutation value types.

The durable issue #117 decision artifact is
`docs/spikes/issue-117-yaml-frontmatter-editing.md`.
