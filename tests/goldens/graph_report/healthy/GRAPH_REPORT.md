# Graph report

## Provenance

- Bundle: healthy
- OKF version: 0.2
- Generated at: 2026-01-15T12:00:00Z
- Git revision: deadbeefcafebabe
- Edge-interpretation policy: directed unique-edge; self-links + unlisted-endpoint off unique edges; fragments not recovered; WCC/AP on undirected unique-edge projection

### Source commands

```sh
.venv/bin/okf graph --bundle healthy
.venv/bin/okf list-concepts --bundle healthy --with-graph-counts
```

## Graph overview

- Concepts: 5
- Link instances: 12
- Unique directed edges: 12
- Density: 0.600000
- Reciprocal-edge ratio: 1.000000
- Mean in-degree: 2.400000
- Median in-degree: 2.000000
- Mean out-degree: 2.400000
- Median out-degree: 2.000000
- Weakly connected components: 1
- Component sizes: 5

## Graph health

These lists are diagnostic signals, not quotas. Authoring rules permit notes with no outbound links.

### Broken links

None.

### Problems

None.

### Orphans

None.

### Zero inbound

None.

### Zero outbound

None.

### Other component memberships

Largest-component membership is omitted.

None.

## High-centrality concepts

A high-centrality concept may be a foundational hub or an over-broad note behaving like a forbidden hub. Rankings use verbatim OKF PageRank and inbound link count.

### Top by PageRank

1. Shared hub (m) — 0.500000
2. x — 0.150000
3. y — 0.150000
4. a — 0.100000
5. b — 0.100000

### Top by inbound degree

1. Shared hub (m) — 4.000000
2. a — 2.000000
3. b — 2.000000
4. x — 2.000000
5. y — 2.000000

## Bridge concepts

Articulation points on the undirected unique-edge projection, and the regions they connect.

### Shared hub (m)

- Region (2): a, b
- Region (2): x, y

## Suggested inspections

Condition-driven follow-up commands. One command per observed condition.

### Sole component bridge

Inspect the neighborhood of Shared hub (m).

```sh
.venv/bin/okf graph --bundle healthy --concept m --depth 2
```

## Communities

Community analysis is deferred until CCP-260.
