#!/usr/bin/env python3
"""Benchmark script for SQLite FTS5 lexical search scaling and query latency."""

from __future__ import annotations

import math
import random
import shutil
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

from okf_core.config import BundleConfig
from okf_core.search import search_concepts


def generate_word_list() -> list[str]:
    """Return a list of common mock words to build concept bodies."""
    return [
        "knowledge",
        "catalog",
        "bundle",
        "concept",
        "document",
        "artifact",
        "relation",
        "search",
        "lexical",
        "index",
        "cache",
        "performance",
        "scaling",
        "latency",
        "benchmark",
        "database",
        "sqlite",
        "query",
        "refresh",
        "manifest",
        "config",
        "validation",
        "schema",
        "metadata",
        "frontmatter",
        "parsing",
        "serialization",
        "strategy",
        "resolver",
        "path",
        "directory",
        "identity",
        "stable",
        "mapping",
        "graph",
        "link",
        "inbound",
        "outbound",
        "orphan",
        "centrality",
        "pagerank",
        "damping",
        "transition",
        "probability",
        "boost",
        "weight",
        "metric",
        "density",
        "conformance",
        "specification",
        "versioning",
        "consequences",
        "upstream",
        "downstream",
        "reference",
        "implementation",
        "library",
        "hook",
        "pluggy",
        "dispatch",
        "lifecycle",
        "transaction",
        "observe",
        "measure",
        "profile",
        "report",
        "statistics",
        "median",
        "percentile",
        "average",
        "frequency",
        "term",
        "match",
        "snippet",
        "provenance",
        "retrieve",
        "pack",
        "context",
        "seed",
        "discovery",
        "progressive",
        "disclosure",
        "mechanism",
        "deterministic",
        "token",
        "api",
        "dependency",
        "hosted",
        "service",
        "embedding",
        "model",
        "standard",
        "library",
        "framework",
        "integration",
        "test",
        "suite",
        "mock",
    ]


def write_mock_concept(
    path: Path,
    title: str,
    body_words: list[str],
    extra_keywords: list[str],
) -> None:
    """Write a mock concept document with a specified title and body length."""
    path.parent.mkdir(parents=True, exist_ok=True)

    # Generate exactly 500 words total (random body + controlled keywords)
    word_count = max(0, 500 - len(extra_keywords))
    words = [random.choice(body_words) for _ in range(word_count)]
    # Mix in the specific query keywords
    words.extend(extra_keywords)
    random.shuffle(words)
    body = " ".join(words) + "\n"

    frontmatter = (
        "---\n"
        "type: concept\n"
        f"title: {title}\n"
        "description: Mock concept for benchmarking search scaling.\n"
        "tags:\n"
        "  - benchmark\n"
        "  - scale-test\n"
        "---\n"
    )

    path.write_text(frontmatter + body, encoding="utf-8", newline="\n")


def run_query_benchmark(
    bundle: BundleConfig,
    query: str,
    query_type: str,
    iterations: int = 100,
) -> dict[str, Any]:
    """Execute a query repeatedly and return latency statistics in milliseconds."""
    if iterations < 1:
        raise ValueError("iterations must be >= 1")
    latencies = []

    # Warm up FTS5 query execution; also initialises results before the loop
    results = search_concepts(bundle, query, refresh=False, limit=10)

    for _ in range(iterations):
        start = time.perf_counter()
        results = search_concepts(bundle, query, refresh=False, limit=10)
        duration = time.perf_counter() - start
        latencies.append(duration * 1000.0)  # Convert to milliseconds

    return {
        "type": query_type,
        "query": query,
        "results_count": len(results.results),
        "min": min(latencies),
        "max": max(latencies),
        "mean": statistics.mean(latencies),
        "median": statistics.median(latencies),
        "p95": sorted(latencies)[math.ceil(iterations * 0.95) - 1],
    }


def main() -> None:
    """Generate mock bundle, build/refresh caches, run queries, and print metrics."""
    print("=" * 60)
    print("OKF-CORE SEARCH SCALING & PERFORMANCE BENCHMARK")
    print("=" * 60)

    temp_dir = tempfile.mkdtemp()
    try:
        bundle_root = Path(temp_dir) / "bundle"
        cache_dir = Path(temp_dir) / "cache"
        bundle_root.mkdir(parents=True, exist_ok=True)
        cache_dir.mkdir(parents=True, exist_ok=True)

        words = generate_word_list()
        random.seed(42)

        print("Generating 1,000 mock concepts (~500 words each)...")
        start_gen = time.perf_counter()

        for i in range(1000):
            # Control keyword presence:
            # - "commonword" is in all 1000 documents
            # - "mediumword" is in every 20th document (50 documents total)
            # - "rareword" is in exactly 1 document (the first one)
            keywords = ["commonword"]
            if i % 20 == 0:
                keywords.append("mediumword")
            if i == 0:
                keywords.append("rareword")

            file_path = bundle_root / f"concepts/concept-{i:04d}.md"
            write_mock_concept(file_path, f"Concept {i}", words, keywords)

        gen_duration = time.perf_counter() - start_gen
        print(f"Generated 1,000 concepts in {gen_duration:.2f} seconds.\n")

        bundle = BundleConfig(
            name="benchmark-bundle",
            bundle_root=bundle_root,
            include=("**/*.md",),
            exclude=(),
            reserved_filenames=("index.md", "log.md"),
            concept_path_strategy="relative-path",
            okf_cache_dir=cache_dir,
        )

        # 1. Initial Indexing Build
        print("Running initial search indexing build (cold cache)...")
        start = time.perf_counter()
        search_concepts(bundle, "commonword", refresh=True, limit=0)
        initial_build_time = time.perf_counter() - start
        print(f"Initial Indexing time: {initial_build_time:.4f} seconds\n")

        # 2. No-op Refresh
        print("Running subsequent refresh check (no changes, warm cache)...")
        start = time.perf_counter()
        search_concepts(bundle, "commonword", refresh=True, limit=0)
        noop_refresh_time = time.perf_counter() - start
        print(f"No-op Refresh time: {noop_refresh_time:.4f} seconds\n")

        # 3. Incremental Refresh
        print("Running incremental refresh (1 document modified)...")
        # Modify the first concept
        keywords = ["commonword", "mediumword", "rareword", "modifiedkeyword"]
        write_mock_concept(
            bundle_root / "concepts/concept-0000.md",
            "Concept 0 Modified",
            words,
            keywords,
        )

        start = time.perf_counter()
        search_concepts(bundle, "commonword", refresh=True, limit=0)
        inc_refresh_time = time.perf_counter() - start
        print(f"Incremental Refresh time: {inc_refresh_time:.4f} seconds\n")

        # 4. FTS5 Query Benchmarks
        print("Running FTS5 Query Latency Benchmarks (100 iterations per query)...")

        benchmarks = [
            run_query_benchmark(
                bundle, "commonword", "High Frequency (~1,000 matches)"
            ),
            run_query_benchmark(bundle, "mediumword", "Medium Frequency (~50 matches)"),
            run_query_benchmark(bundle, "rareword", "Rare Frequency (1 match)"),
            run_query_benchmark(bundle, "nonexistentword", "Zero Matches (0 matches)"),
        ]

        separator = "-" * 88
        header = (
            f"{'Query Type':<30} | {'Query':<15} | {'Results':<7}"
            f" | {'Min (ms)':<8} | {'Mean (ms)':<9}"
            f" | {'Median (ms)':<11} | {'p95 (ms)':<8}"
        )
        print("\n" + separator)
        print(header)
        print(separator)
        for b in benchmarks:
            print(
                f"{b['type']:<30} | "
                f"{b['query']:<15} | "
                f"{b['results_count']:<7} | "
                f"{b['min']:8.3f} | "
                f"{b['mean']:9.3f} | "
                f"{b['median']:11.3f} | "
                f"{b['p95']:8.3f}"
            )
        print("-" * 88 + "\n")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        print("Benchmark complete (temp files removed best-effort).")
        print("=" * 60)


if __name__ == "__main__":
    main()
