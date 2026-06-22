#!/usr/bin/env python3
"""Generate a PEP 503 simple index for the gh-pages branch.

Usage: gen_index.py <dist-dir> <output-dir>

Reads newly built artifacts from <dist-dir>, computes their SHA-256 hashes,
and fetches all published releases from the GitHub API to collect download URLs.
SHA-256 hashes are persisted in hashes.json on gh-pages so they accumulate
across runs — newly built artifacts are always hashed from disk, while
artifacts from previous runs rely on the stored hashes.json. Artifacts whose
hash is not yet known (e.g. releases that predate this workflow) are linked
without a sha256 fragment, which is valid per PEP 503.

Environment variables (GH_TOKEN or GITHUB_TOKEN must be set):
  GH_TOKEN      - GitHub token with contents:read permission (takes precedence)
  GITHUB_TOKEN  - fallback if GH_TOKEN is unset
  GH_REPO       - owner/repo slug (e.g. xchewtoyx/okf-core)
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PACKAGE_NAME = "okf-core"
_PACKAGE_PREFIX = PACKAGE_NAME.replace("-", "_").lower()


def _api(path: str, token: str) -> object:
    req = Request(
        f"https://api.github.com{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "okf-core/gen_index.py",
        },
    )
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _get_all_releases(repo: str, token: str) -> list[dict]:
    releases: list[dict] = []
    page = 1
    while True:
        batch = _api(f"/repos/{repo}/releases?per_page=100&page={page}", token)
        if not isinstance(batch, list):
            raise RuntimeError(f"Unexpected API response (expected list): {batch!r}")
        releases.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return releases


def _load_stored_hashes(repo: str, token: str) -> dict[str, str]:
    try:
        data = _api(f"/repos/{repo}/contents/hashes.json?ref=gh-pages", token)
        content = base64.b64decode(data["content"]).decode("utf-8")
        stored = json.loads(content)
        if not isinstance(stored, dict):
            raise RuntimeError(f"hashes.json is not a JSON object: {stored!r}")
        return stored
    except HTTPError as exc:
        if exc.code == 404:
            return {}
        raise


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_package_asset(name: str) -> bool:
    normalized = name.lower().replace("-", "_")
    return normalized.startswith(_PACKAGE_PREFIX) and (
        name.endswith(".whl") or name.endswith(".tar.gz")
    )


def _root_index_html() -> str:
    return (
        "<!DOCTYPE html>\n"
        "<html>\n"
        "  <head><title>Simple Index</title></head>\n"
        "  <body>\n"
        "    <h1>Simple Index</h1>\n"
        f'    <a href="{PACKAGE_NAME}/">{PACKAGE_NAME}</a>\n'
        "  </body>\n"
        "</html>\n"
    )


def _package_index_html(links: list[tuple[str, str, str]]) -> str:
    def _href(url: str, sha256: str) -> str:
        return f"{url}#sha256={sha256}" if sha256 else url

    entries = "\n".join(
        f'    <a href="{html.escape(_href(url, sha256))}">{html.escape(name)}</a>'
        for name, url, sha256 in sorted(links)
    )
    return (
        "<!DOCTYPE html>\n"
        "<html>\n"
        f"  <head><title>Links for {PACKAGE_NAME}</title></head>\n"
        "  <body>\n"
        f"    <h1>Links for {PACKAGE_NAME}</h1>\n"
        f"{entries}\n"
        "  </body>\n"
        "</html>\n"
    )


def main(dist_dir: str, output_dir: str) -> None:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("Error: GH_TOKEN or GITHUB_TOKEN environment variable must be set")
    repo = os.environ.get("GH_REPO")
    if not repo:
        sys.exit("Error: GH_REPO environment variable must be set")

    dist = Path(dist_dir)
    out = Path(output_dir)

    # Hash newly built artifacts
    new_hashes = {
        f.name: _sha256(f) for f in dist.iterdir() if _is_package_asset(f.name)
    }

    # Merge with hashes stored on gh-pages (avoids re-downloading old releases)
    hashes = _load_stored_hashes(repo, token)
    hashes.update(new_hashes)

    # Collect download links from all published non-prerelease releases
    links: list[tuple[str, str, str]] = []
    for release in _get_all_releases(repo, token):
        if release.get("draft") or release.get("prerelease"):
            continue
        tag = release["tag_name"]
        for asset in release.get("assets", []):
            name = asset["name"]
            if not _is_package_asset(name):
                continue
            url = f"https://github.com/{repo}/releases/download/{tag}/{name}"
            links.append((name, url, hashes.get(name, "")))

    # Write output
    simple = out / "simple"
    pkg = simple / PACKAGE_NAME
    pkg.mkdir(parents=True, exist_ok=True)
    (out / ".nojekyll").touch()

    (simple / "index.html").write_text(_root_index_html(), encoding="utf-8")
    (pkg / "index.html").write_text(_package_index_html(links), encoding="utf-8")
    (out / "hashes.json").write_text(
        json.dumps(hashes, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(f"Generated PEP 503 index: {len(links)} artifact(s) across all releases.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <dist-dir> <output-dir>", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
