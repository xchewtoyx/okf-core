# Issue #117: YAML frontmatter editing spike

> **Status: superseded.** The `ruamel.yaml` migration recommended below was
> descoped when #148 was closed as not planned — see
> [`148-round-trip-refactor-descope.md`](148-round-trip-refactor-descope.md).
> The evidence remains valid; the PyYAML span-splice engine stays.
>
> **Current adoption status:** see
> [`docs/decisions/0002-canonical-serialization-form.md`](../decisions/0002-canonical-serialization-form.md)
> ("YAML side"). The `ruamel.yaml` migration recommended below is now
> ACCEPTED — this spike's evidence table is the basis for that decision.

## Problem statement

`plan_frontmatter_merge()` currently edits YAML frontmatter by composing with
PyYAML, finding source spans for targeted top-level values, and splicing only
those spans. That works surprisingly well for preserving manually edited files,
but it also means okf-core owns a lot of YAML-specific machinery: node span
discovery, scalar-vs-collection rendering, generated-alias rejection, and
formatting edge cases such as zero-width implicit nulls.

This spike evaluated whether frontmatter editing should instead follow a YAML
serialization model:

1. load frontmatter into a structured object,
2. mutate the object,
3. reserialize the frontmatter in a predictable style.

That model is a better fit for YAML than byte-splicing, provided it can still
preserve human annotations such as comments and avoid silently corrupting
anchors, aliases, or malformed input.

Markdown edits should keep their existing byte/span-oriented approach. Markdown
is semi-structured prose where source location and local replacement are the
feature. YAML is a serialization format, so the maintainable long-term
contract can be semantic correctness plus documented formatting, not permanent
byte identity for every untargeted token.

## Candidates evaluated

| Candidate | Summary |
|---|---|
| Current PyYAML source-splice | Best byte preservation, but keeps custom span and serialization code in okf-core. |
| `ruamel.yaml` 0.19.1 | Mature round-trip loader/dumper with comment preservation and Python 3.9+ support. |
| YAMLRocks 0.2.1 | Very fast Rust-backed round-trip parser with schema support, but Python 3.12+ only. |

Both round-trip candidates were evaluated through isolated spike code under
`spike/`. The prototypes reuse okf-core's planning layer, update-value policy,
exact-type no-op comparison, post-merge validation, and explicit alias guard so
the comparison focuses on the YAML editing engine.

## Evidence

Tested locally with CPython 3.14.5 on Apple Silicon.

| Probe or corpus | Result |
|---|---:|
| Focused YAMLRocks representation and schema probes | 25/25 pass |
| Focused cross-library round-trip probes | 5/5 pass |
| Current patching corpus with YAMLRocks prototype | 47/53 pass |
| Current patching corpus with ruamel prototype | 49/53 pass |

The current production corpus still encodes the byte-splice contract, so its
failures are not all blockers under a canonical YAML contract. They are useful
because they show exactly which behavior would change.

| Behavior | Current PyYAML splice | `ruamel.yaml` | YAMLRocks |
|---|---|---|---|
| Semantic targeted changes | Yes | Yes | Mostly |
| Preserves comments on ordinary edits | Yes | Yes | Yes |
| Preserves exact untargeted bytes | Yes | No | No |
| Normalizes noncanonical flow spacing after edit | No | Yes | Yes |
| Preserves CRLF after edit | Yes | No | No |
| Native duplicate-key error | Project loader | Yes | Yes |
| Alias-linked edit rejection | Project guard | Guard required | Guard required; metadata helps |
| Accepted `date`/`datetime` assignment | Yes | Yes | No for round-trip node assignment |
| Python versions compatible with okf-core | Yes | Yes | No; requires Python >=3.12 |

Performance is not the deciding factor. In the local benchmark YAMLRocks was
orders of magnitude faster than both PyYAML and ruamel, but frontmatter edits
are file-at-a-time operations and all candidates are fast enough in absolute
terms for normal okf-core usage.

## Schema findings

YAMLRocks' schema support is interesting but does not replace okf-core's
validation policy:

- `if`/`then` conditionals were ignored in 0.2.1.
- malformed schemas such as `{"type": 42}` were accepted without diagnostic.
- validation stops at the first failure.
- missing type-specific fields reported generic `oneOf` failures at `$`.
- JSON Schema has errors, while okf-core needs its own error/warning policy and
  permissive unknown-field behavior.

The schema feature is therefore not a reason to adopt YAMLRocks for core
frontmatter editing today.

## Recommended future contract

If okf-core migrates away from source-splicing, the new documented
`plan_frontmatter_merge()` contract should be:

- parse frontmatter into a round-trip YAML object;
- reject malformed YAML, duplicate keys, and targeted alias-linked fields with
  `DocumentChangePlanningError`;
- apply shallow top-level updates only;
- preserve comments, key order, quoted strings, and block scalar content where
  the library can attach them reliably;
- reserialize the whole frontmatter using canonical LF YAML formatting;
- allow benign representation changes such as normalized flow spacing,
  canonical list indentation, and documented null rendering;
- keep exact no-op detection so semantically identical typed values do not
  rewrite a file.

Under this contract, line ending behavior should be explicit: edited YAML
frontmatter is emitted with LF. The Markdown body and delimiter handling should
be specified in the follow-up implementation. This avoids fragile CRLF
post-processing around comment tokens.

## Recommendation

Adopt the canonical object-mutate-reserialize direction in a separate
implementation PR, and use `ruamel.yaml` as the migration candidate.

`ruamel.yaml` is the safer choice because it:

- preserves comments and common human-authored representation well enough for
  the desired contract;
- supports the full current okf-core Python range;
- supports accepted `date` and `datetime` update values;
- is mature and pure Python;
- removes most of okf-core's hand-written YAML span machinery.

Do not adopt YAMLRocks now. It is impressively fast, but it currently requires
Python 3.12+, cannot assign accepted Python `date` values into a round-trip
document, has pre-1.0 dependency risk, and its schema support does not replace
okf-core validation.

Do not fold the migration into this spike. The production change should be a
separate reviewable PR because it intentionally changes a user-visible
formatting contract.

## Follow-up implementation scope

The follow-up PR should:

- add `ruamel.yaml` as a runtime dependency;
- replace the YAML editing core inside `plan_frontmatter_merge()` with
  round-trip object mutation while keeping the existing planning/apply safety
  layer;
- keep okf-core's update-value validation, no-op equality, post-merge
  validation, and explicit alias-linked edit rejection;
- document the new canonical YAML formatting contract in README and relevant
  docstrings;
- update current byte-specific tests whose expectations are intentionally
  relaxed;
- add production tests for comment preservation, canonical formatting, duplicate
  key errors, alias rejection, null policy, CRLF policy, and date/datetime
  updates.
