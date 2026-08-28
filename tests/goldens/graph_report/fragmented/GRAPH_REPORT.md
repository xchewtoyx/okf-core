# Graph report

## Provenance

- Bundle: fragmented
- OKF version: 0.2
- Generated at: 2026-01-15T12:00:00Z
- Git revision: deadbeefcafebabe
- Edge-interpretation policy: directed unique-edge; self-links + unlisted-endpoint off unique edges; fragments not recovered; WCC/AP on undirected unique-edge projection

### Source commands

```sh
.venv/bin/okf graph --bundle fragmented
.venv/bin/okf list-concepts --bundle fragmented --with-graph-counts
```

## Graph overview

- Concepts: 7
- Link instances: 4
- Unique directed edges: 4
- Density: 0.095238
- Reciprocal-edge ratio: 0.000000
- Mean in-degree: 0.571429
- Median in-degree: 1.000000
- Mean out-degree: 0.571429
- Median out-degree: 1.000000
- Weakly connected components: 3
- Component sizes: 4, 2, 1

## Graph health

These lists are diagnostic signals, not quotas. Authoring rules permit notes with no outbound links.

### Broken links

- a (a.md); target ../outside.md; path ../outside.md; text missing
- a (a.md); target /root-rel.md; path /root-rel.md; text missing

### Problems

- a (a.md): parse-error: frontmatter is not a mapping

### Orphans

- untitled

### Zero inbound

- a
- d
- untitled

### Zero outbound

- e
- Hub sink (h)
- untitled

### Other component memberships

Largest-component membership is omitted.

- d, e
- untitled

## High-centrality concepts

A high-centrality concept may be a foundational hub or an over-broad note behaving like a forbidden hub. Rankings use verbatim OKF PageRank and inbound link count.

### Top by PageRank

1. Hub sink (h) — 0.900000
2. c — 0.300000
3. b — 0.200000
4. a — 0.100000
5. d — 0.050000
6. e — 0.050000
7. untitled — 0.000000

### Top by inbound degree

1. b — 1.000000
2. c — 1.000000
3. e — 1.000000
4. Hub sink (h) — 1.000000
5. a — 0.000000
6. d — 0.000000
7. untitled — 0.000000

## Bridge concepts

Articulation points on the undirected unique-edge projection, and the regions they connect.

### b

- Region (2): c, Hub sink (h)
- Region (1): a

### c

- Region (2): a, b
- Region (1): Hub sink (h)

## Suggested inspections

Condition-driven follow-up commands. One command per observed condition.

### Articulation point

Inspect the neighborhood of b.

```sh
.venv/bin/okf graph --bundle fragmented --concept b --depth 2
```

### High-PageRank concept with no outbound links

Inspect the content around Hub sink (h).

```sh
.venv/bin/okf context --bundle fragmented --seed h
```

### Orphans (1)

Inspect the first orphan, untitled.

```sh
.venv/bin/okf context --bundle fragmented --seed untitled
```

### Broken link

Inspect the neighborhood of a.

```sh
.venv/bin/okf graph --bundle fragmented --concept a --depth 2
```

## Communities

Community analysis is deferred until CCP-260.
