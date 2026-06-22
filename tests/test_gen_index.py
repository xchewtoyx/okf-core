"""Tests for .github/scripts/gen_index.py — helper functions and main() integration."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

# Load gen_index.py from its non-package location
_SCRIPT = Path(__file__).parent.parent / ".github" / "scripts" / "gen_index.py"
_spec = importlib.util.spec_from_file_location("gen_index", _SCRIPT)
assert _spec and _spec.loader
gen_index = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen_index)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# _is_package_asset
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "okf_core-0.1.0-py3-none-any.whl",
        "okf-core-0.1.0.tar.gz",
        "OKF_CORE-0.1.0-py3-none-any.WHL",  # upper-case extension
        "okf_core-0.2.0-py3-none-any.whl",
    ],
)
def test_is_package_asset_accepts_valid(name: str) -> None:
    assert gen_index._is_package_asset(name)


@pytest.mark.parametrize(
    "name",
    [
        "other_package-0.1.0-py3-none-any.whl",
        "okf_core-0.1.0.zip",
        "README.md",
        "okf_core-0.1.0.tar.bz2",
        "source.tar.gz",  # no package prefix
        "okf_coreutils-0.1.0-py3-none-any.whl",  # prefix match but different package
        "okf_core_utils-0.1.0-py3-none-any.whl",  # underscore-separated suffix variant
    ],
)
def test_is_package_asset_rejects_non_package(name: str) -> None:
    assert not gen_index._is_package_asset(name)


# ---------------------------------------------------------------------------
# _sha256
# ---------------------------------------------------------------------------


def test_sha256_matches_hashlib(tmp_path: Path) -> None:
    content = b"hello world" * 1000
    f = tmp_path / "artifact.whl"
    f.write_bytes(content)
    expected = hashlib.sha256(content).hexdigest()
    assert gen_index._sha256(f) == expected


def test_sha256_empty_file(tmp_path: Path) -> None:
    f = tmp_path / "empty.whl"
    f.write_bytes(b"")
    assert gen_index._sha256(f) == hashlib.sha256(b"").hexdigest()


# ---------------------------------------------------------------------------
# _root_index_html
# ---------------------------------------------------------------------------


def test_root_index_html_contains_package_link() -> None:
    html = gen_index._root_index_html()
    assert 'href="okf-core/"' in html
    assert "okf-core" in html


def test_root_index_html_is_valid_structure() -> None:
    html = gen_index._root_index_html()
    assert html.startswith("<!DOCTYPE html>")
    assert "<title>Simple Index</title>" in html


# ---------------------------------------------------------------------------
# _package_index_html
# ---------------------------------------------------------------------------


def test_package_index_html_empty() -> None:
    html = gen_index._package_index_html([])
    assert "Links for okf-core" in html
    assert "<a " not in html


def test_package_index_html_includes_sha256_fragment() -> None:
    links = [("okf_core-0.1.0-py3-none-any.whl", "https://example.com/f.whl", "abc123")]
    html = gen_index._package_index_html(links)
    assert "#sha256=abc123" in html
    assert "okf_core-0.1.0-py3-none-any.whl" in html


def test_package_index_html_omits_fragment_when_no_hash() -> None:
    links = [("okf_core-0.1.0-py3-none-any.whl", "https://example.com/f.whl", "")]
    html = gen_index._package_index_html(links)
    assert "#sha256=" not in html
    assert "https://example.com/f.whl" in html


def test_package_index_html_escapes_special_chars() -> None:
    links = [("name<script>.whl", "https://example.com/f.whl?a=1&b=2", "")]
    html = gen_index._package_index_html(links)
    assert "<script>" not in html
    assert "&amp;" in html or "&lt;" in html


def test_package_index_html_sorted() -> None:
    links = [
        ("okf_core-0.2.0-py3-none-any.whl", "https://example.com/b.whl", ""),
        ("okf_core-0.1.0-py3-none-any.whl", "https://example.com/a.whl", ""),
    ]
    html = gen_index._package_index_html(links)
    pos_0_1 = html.index("0.1.0")
    pos_0_2 = html.index("0.2.0")
    assert pos_0_1 < pos_0_2


# ---------------------------------------------------------------------------
# _get_all_releases (patch.object to avoid real network calls)
# ---------------------------------------------------------------------------


def test_get_all_releases_single_page() -> None:
    releases = [{"tag_name": "v0.1.0", "assets": []}]
    with patch.object(gen_index, "_api", return_value=releases):
        result = gen_index._get_all_releases("owner/repo", "tok")
    assert result == releases


def test_get_all_releases_paginates() -> None:
    page1 = [{"tag_name": f"v0.{i}.0", "assets": []} for i in range(100)]
    page2 = [{"tag_name": "v1.0.0", "assets": []}]
    calls: list[str] = []

    def fake_api(path: str, token: str) -> Any:
        calls.append(path)
        return page1 if path.endswith("page=1") else page2

    with patch.object(gen_index, "_api", fake_api):
        result = gen_index._get_all_releases("owner/repo", "tok")

    assert len(result) == 101
    assert len(calls) == 2


def test_get_all_releases_raises_on_non_list() -> None:
    with patch.object(gen_index, "_api", return_value={"error": "bad token"}):
        with pytest.raises(RuntimeError, match="expected list"):
            gen_index._get_all_releases("owner/repo", "tok")


# ---------------------------------------------------------------------------
# _load_stored_hashes (patch.object to avoid real network calls)
# ---------------------------------------------------------------------------


def test_load_stored_hashes_returns_dict() -> None:
    stored = {"okf_core-0.1.0-py3-none-any.whl": "abc123"}
    encoded = base64.b64encode(json.dumps(stored).encode()).decode()
    with patch.object(gen_index, "_api", return_value={"content": encoded}):
        result = gen_index._load_stored_hashes("owner/repo", "tok")
    assert result == stored


def test_load_stored_hashes_returns_empty_on_404() -> None:
    def raise_404(path: str, token: str) -> Any:
        raise HTTPError(url="", code=404, msg="Not Found", hdrs=None, fp=None)  # type: ignore[arg-type]

    with patch.object(gen_index, "_api", raise_404):
        assert gen_index._load_stored_hashes("owner/repo", "tok") == {}


def test_load_stored_hashes_raises_on_non_dict() -> None:
    encoded = base64.b64encode(json.dumps([1, 2, 3]).encode()).decode()
    with patch.object(gen_index, "_api", return_value={"content": encoded}):
        with pytest.raises(RuntimeError, match="not a JSON object"):
            gen_index._load_stored_hashes("owner/repo", "tok")


def test_load_stored_hashes_reraises_non_404_http_error() -> None:
    def raise_500(path: str, token: str) -> Any:
        raise HTTPError(url="", code=500, msg="Server Error", hdrs=None, fp=None)  # type: ignore[arg-type]

    with patch.object(gen_index, "_api", raise_500):
        with pytest.raises(HTTPError):
            gen_index._load_stored_hashes("owner/repo", "tok")


# ---------------------------------------------------------------------------
# main (integration — no network)
# ---------------------------------------------------------------------------


def test_main_writes_index_files(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dist / "okf_core-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"fake wheel content")
    out = tmp_path / "pages"

    def fake_api(path: str, token: str) -> Any:
        if "contents/hashes.json" in path:
            raise HTTPError(url="", code=404, msg="Not Found", hdrs=None, fp=None)  # type: ignore[arg-type]
        return [
            {
                "draft": False,
                "prerelease": False,
                "tag_name": "v0.1.0",
                "assets": [
                    {
                        "name": "okf_core-0.1.0-py3-none-any.whl",
                        "browser_download_url": "https://example.com/okf_core-0.1.0-py3-none-any.whl",
                    }
                ],
            }
        ]

    with patch.object(gen_index, "_api", fake_api):
        env = {"GH_TOKEN": "fake", "GH_REPO": "owner/repo"}
        with patch.dict(os.environ, env):
            gen_index.main(str(dist), str(out))

    assert (out / ".nojekyll").exists()
    assert (out / "simple" / "index.html").exists()
    pkg_index = (out / "simple" / "okf-core" / "index.html").read_text()
    assert "okf_core-0.1.0-py3-none-any.whl" in pkg_index
    assert "#sha256=" in pkg_index
    hashes = json.loads((out / "hashes.json").read_text())
    assert "okf_core-0.1.0-py3-none-any.whl" in hashes


def test_main_exits_when_token_missing(tmp_path: Path) -> None:
    env = {"GH_REPO": "owner/repo"}
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(SystemExit):
            gen_index.main(str(tmp_path), str(tmp_path / "out"))


def test_main_exits_when_repo_missing(tmp_path: Path) -> None:
    env = {"GH_TOKEN": "fake"}
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(SystemExit):
            gen_index.main(str(tmp_path), str(tmp_path / "out"))


def test_main_skips_prerelease_and_draft(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    out = tmp_path / "pages"

    def fake_api(path: str, token: str) -> Any:
        if "contents/hashes.json" in path:
            raise HTTPError(url="", code=404, msg="Not Found", hdrs=None, fp=None)  # type: ignore[arg-type]
        return [
            {
                "draft": True,
                "prerelease": False,
                "tag_name": "v0.1.0-draft",
                "assets": [
                    {
                        "name": "okf_core-0.1.0-py3-none-any.whl",
                        "browser_download_url": "https://example.com/d.whl",
                    }
                ],
            },
            {
                "draft": False,
                "prerelease": True,
                "tag_name": "v0.2.0a1",
                "assets": [
                    {
                        "name": "okf_core-0.2.0a1-py3-none-any.whl",
                        "browser_download_url": "https://example.com/p.whl",
                    }
                ],
            },
        ]

    with patch.object(gen_index, "_api", fake_api):
        with patch.dict(os.environ, {"GH_TOKEN": "fake", "GH_REPO": "owner/repo"}):
            gen_index.main(str(dist), str(out))

    pkg_index = (out / "simple" / "okf-core" / "index.html").read_text()
    assert "<a " not in pkg_index
