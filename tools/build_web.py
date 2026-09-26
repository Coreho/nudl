"""Build the "try it in your browser" page into dist/web/, ready for GitHub Pages.

The page runs nudl's real engine: this copies src/clean.py and src/rules.json in beside
it, byte for byte, and Pyodide runs them in the browser. One engine, not a JavaScript
port that would slowly start disagreeing with the Python one.

Also stamps the build: the nudl version and rules date into the page, a content hash
into the service worker's cache name (so a new build replaces the old cache), and the
inline script's SHA-256 into the Content-Security-Policy in place of 'unsafe-inline'
— the page may run exactly that script and nothing else.

    python tools/build_web.py [output dir]
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from make_icon import icon_image  # noqa: E402
from PIL import Image  # noqa: E402

WEB = ROOT / "web"
SRC = ROOT / "src"


def build(out: Path) -> Path:
    if out.exists():
        shutil.rmtree(out)
    (out / "engine").mkdir(parents=True)

    shutil.copyfile(SRC / "clean.py", out / "engine" / "clean.py")
    shutil.copyfile(SRC / "rules.json", out / "engine" / "rules.json")
    shutil.copyfile(WEB / "manifest.webmanifest", out / "manifest.webmanifest")
    (out / ".nojekyll").write_text("", encoding="utf-8")  # serve files as they are

    for size in (192, 512):
        icon_image(size).save(out / f"icon-{size}.png")
    # Maskable: the launcher crops to a circle or squircle, so the tile fills the canvas
    # and the glyph sits inside the middle 80%.
    tile = Image.new("RGBA", (512, 512), (22, 24, 29, 255))
    glyph = icon_image(410)
    tile.alpha_composite(glyph, (51, 51))
    tile.save(out / "icon-maskable-512.png")

    version = re.search(r'__version__ = "(.+)"', (SRC / "__init__.py").read_text()).group(1)
    rules_date = json.loads((SRC / "rules.json").read_text(encoding="utf-8")).get("last_updated")
    page = (WEB / "index.html").read_text(encoding="utf-8")
    page = page.replace("__NUDL_VERSION__", version).replace("__RULES_DATE__", rules_date or "")

    scripts = re.findall(r"<script type=\"module\">(.*?)</script>", page, re.S)
    if len(scripts) != 1:
        raise SystemExit(f"expected exactly one inline module script, found {len(scripts)}")
    digest = base64.b64encode(hashlib.sha256(scripts[0].encode("utf-8")).digest()).decode()
    if "script-src 'self' 'unsafe-inline'" not in page:
        raise SystemExit("the CSP no longer has the placeholder this build replaces")
    page = page.replace("script-src 'self' 'unsafe-inline'", f"script-src 'self' 'sha256-{digest}'")
    (out / "index.html").write_text(page, encoding="utf-8")

    stamp = hashlib.sha256()
    for path in sorted(out.rglob("*")):
        if path.is_file():
            stamp.update(path.name.encode() + path.read_bytes())
    worker = (WEB / "sw.js").read_text(encoding="utf-8")
    worker = worker.replace("__BUILD__", stamp.hexdigest()[:12])
    (out / "sw.js").write_text(worker, encoding="utf-8")
    return out


if __name__ == "__main__":
    target = build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "web")
    print(f"wrote {target}")
