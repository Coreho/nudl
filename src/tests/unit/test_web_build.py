"""The browser page's build: it must ship the real engine, and lock itself down.

The page's whole claim is "this is nudl's own clean.py, and nothing you paste can leave".
Both halves are properties of the build, so the build is what gets checked: the engine
files are byte-identical to src/, and the Content-Security-Policy allows exactly the one
inline script the page has — by hash, not with 'unsafe-inline'.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("PIL")  # the icons are rendered with Pillow, a dev dependency

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))

import build_web  # noqa: E402


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Path:
    return build_web.build(tmp_path_factory.mktemp("web") / "site")


def test_the_page_runs_the_real_engine_byte_for_byte(site: Path) -> None:
    assert (site / "engine" / "clean.py").read_bytes() == (ROOT / "src" / "clean.py").read_bytes()
    assert (site / "engine" / "rules.json").read_bytes() == (
        ROOT / "src" / "rules.json"
    ).read_bytes()


def test_the_csp_allows_exactly_the_one_inline_script(site: Path) -> None:
    page = (site / "index.html").read_text(encoding="utf-8")
    assert "'unsafe-inline'" not in re.search(r"script-src[^;]*", page).group(0)
    (script,) = re.findall(r'<script type="module">(.*?)</script>', page, re.S)
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    assert f"'sha256-{digest}'" in page
    assert "connect-src 'self' https://cdn.jsdelivr.net;" in page, "the page may talk elsewhere"


def test_every_placeholder_is_filled(site: Path) -> None:
    for name in ("index.html", "sw.js"):
        text = (site / name).read_text(encoding="utf-8")
        assert "__" + "BUILD__" not in text and "__NUDL_VERSION__" not in text, name


def test_the_worker_caches_what_the_page_needs(site: Path) -> None:
    worker = (site / "sw.js").read_text(encoding="utf-8")
    for path in ("./engine/clean.py", "./engine/rules.json", "./icon-192.png"):
        assert f'"{path}"' in worker
        built = (site / path.removeprefix("./")).exists()
        assert built, f"the worker caches {path}, which is not built"


def test_the_manifest_can_receive_a_shared_link(site: Path) -> None:
    manifest = json.loads((site / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert manifest["share_target"]["method"] == "GET"
    assert {"text", "url"} <= set(manifest["share_target"]["params"].values())
    for icon in manifest["icons"]:
        assert (site / icon["src"]).exists()


def test_both_pages_load_the_same_pyodide(site: Path) -> None:
    """The page and the worker must agree, or the worker caches a version nobody loads."""
    page = (site / "index.html").read_text(encoding="utf-8")
    worker = (site / "sw.js").read_text(encoding="utf-8")
    (version,) = set(re.findall(r"pyodide/(v[\d.]+)/full/", page))
    assert f"pyodide/{version}/full/" in worker
