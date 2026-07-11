"""Small repeatable benchmark for the issue #117 comparison."""

from __future__ import annotations

import io
import statistics
import timeit
from collections.abc import Callable

import yaml
import yamlrocks
from ruamel.yaml import YAML

ROUND_TRIP = yamlrocks.OPT_ROUND_TRIP
RUAMEL = YAML(typ="rt")
RUAMEL.preserve_quotes = True
RUAMEL.width = 10_000


def _source(entries: int) -> bytes:
    lines = [
        "# benchmark fixture",
        "type: concept",
        'title: "Preserved title"',
        "tags: [docs,platform]",
        "description: |",
        "  first line",
        "  second line",
    ]
    lines.extend(f"custom_{index}: value_{index}" for index in range(entries))
    return ("\n".join(lines) + "\n").encode()


def _ruamel_parse(source: bytes) -> object:
    return RUAMEL.load(source.decode())


def _ruamel_dump(source: bytes) -> str:
    data = RUAMEL.load(source.decode())
    buffer = io.StringIO()
    RUAMEL.dump(data, buffer)
    return buffer.getvalue()


def _ruamel_mutate(source: bytes) -> str:
    data = RUAMEL.load(source.decode())
    data["title"] = "Updated"
    buffer = io.StringIO()
    RUAMEL.dump(data, buffer)
    return buffer.getvalue()


def _yamlrocks_mutate(source: bytes) -> bytes:
    data = yamlrocks.loads(source, option=ROUND_TRIP)
    data["title"] = "Updated"
    return data.to_yaml()


def _best_microseconds(call: Callable[[], object], number: int) -> float:
    samples = timeit.repeat(call, number=number, repeat=3)
    return statistics.median(samples) * 1_000_000 / number


def main() -> None:
    print("| fixture | operation | PyYAML µs | ruamel µs | YAMLRocks µs |")
    print("|---|---:|---:|---:|---:|")
    for name, entries, number in (
        ("small", 3, 200),
        ("representative", 50, 50),
        ("large", 500, 10),
    ):
        source = _source(entries)
        operations: tuple[
            tuple[
                str,
                Callable[[], object],
                Callable[[], object],
                Callable[[], object],
            ],
            ...,
        ] = (
            (
                "parse",
                lambda: yaml.safe_load(source),
                lambda: _ruamel_parse(source),
                lambda: yamlrocks.loads(source),
            ),
            (
                "parse+serialize",
                lambda: yaml.safe_dump(yaml.safe_load(source), sort_keys=False),
                lambda: _ruamel_dump(source),
                lambda: yamlrocks.loads(source, option=ROUND_TRIP).to_yaml(),
            ),
            (
                "parse+mutate+serialize",
                lambda: yaml.safe_dump(
                    {**yaml.safe_load(source), "title": "Updated"},
                    sort_keys=False,
                ),
                lambda: _ruamel_mutate(source),
                lambda: _yamlrocks_mutate(source),
            ),
        )
        for operation, pyyaml_call, ruamel_call, yamlrocks_call in operations:
            values = (
                _best_microseconds(pyyaml_call, number),
                _best_microseconds(ruamel_call, number),
                _best_microseconds(yamlrocks_call, number),
            )
            print(
                f"| {name} | {operation} | "
                f"{values[0]:.1f} | {values[1]:.1f} | {values[2]:.1f} |"
            )


if __name__ == "__main__":
    main()
