"""Render docs/social-preview.png — the card GitHub shows when the repo link is shared.

GitHub has no API for the social preview, so the image is uploaded by hand under
Settings > General > Social preview. It is generated rather than drawn so that, like the
README gif, it cannot show behaviour nudl no longer has: the "after" line is whatever
the real engine returns for the "before" line.

    python docs/make_social_preview.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from src.clean import clean_result  # noqa: E402

OUT = Path(__file__).resolve().parent / "social-preview.png"

# GitHub's recommended size: 1280x640, shown cropped to 2:1 everywhere it is used.
W, H = 1280, 640
MARGIN = 96
BG = (13, 15, 18)
PANEL = (22, 24, 29)
FG = (232, 234, 237)
MUTED = (154, 160, 166)
ACCENT = (76, 183, 130)
RED = (237, 106, 94)

BEFORE = "https://youtu.be/dQw4w9WgXcQ?si=Ab12Cd34Ef56&t=42"
result = clean_result(BEFORE)
AFTER = result.result
if not result.changed or "t=42" not in AFTER:
    raise SystemExit("the preview URL no longer cleans the way the card shows -- fix the card")


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        return ImageFont.load_default(size)


F_TITLE = font("segoeuib.ttf", 112)
F_TAG = font("segoeui.ttf", 44)
F_LABEL = font("segoeui.ttf", 24)
F_URL = font("consola.ttf", 31)
F_FOOT = font("segoeui.ttf", 27)
F_CMD = font("consolab.ttf", 29)


def url_tokens(url: str, removed: list[str]) -> list[tuple[str, tuple[int, int, int], bool]]:
    """The URL as (text, colour, struck-through) runs: removed pairs in red, struck."""
    base, _, query = url.partition("?")
    runs = [(base, FG, False)]
    for index, pair in enumerate(query.split("&") if query else []):
        sep = "?" if index == 0 else "&"
        gone = pair.partition("=")[0] in removed
        runs.append((sep + pair, RED if gone else FG, gone))
    return runs


def draw_runs(d: ImageDraw.ImageDraw, runs, x: int, y: int) -> None:
    for text, colour, struck in runs:
        width = d.textlength(text, font=F_URL)
        d.text((x, y), text, font=F_URL, fill=colour)
        if struck:
            mid = y + F_URL.size * 0.62
            d.line([(x, mid), (x + width, mid)], fill=colour, width=3)
        x += width


img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)

d.text((MARGIN - 6, 58), "nudl", font=F_TITLE, fill=FG)
title_w = d.textlength("nudl", font=F_TITLE)
d.ellipse(
    [MARGIN + title_w + 8, 156, MARGIN + title_w + 30, 178], fill=ACCENT
)  # the tray icon's green dot
d.text((MARGIN, 214), "Strips the tracking junk off any link you copy.", font=F_TAG, fill=FG)

panel_top, panel_bottom = 316, 512
d.rounded_rectangle([MARGIN - 28, panel_top, W - MARGIN + 28, panel_bottom], 18, fill=PANEL)
d.text((MARGIN, panel_top + 26), "you copy", font=F_LABEL, fill=MUTED)
draw_runs(d, url_tokens(BEFORE, result.params_removed), MARGIN, panel_top + 58)
d.text((MARGIN, panel_top + 108), "you paste", font=F_LABEL, fill=MUTED)
d.text((MARGIN, panel_top + 140), AFTER, font=F_URL, fill=ACCENT)

d.text(
    (MARGIN, 556),
    "Windows tray app + command line  ·  offline  ·  open source",
    font=F_FOOT,
    fill=MUTED,
)
cmd = "winget install nudl"
d.text((W - MARGIN - d.textlength(cmd, font=F_CMD), 560), cmd, font=F_CMD, fill=ACCENT)

# A longer example URL would run off the panel with no error at all, so say so instead.
if d.textlength(BEFORE, font=F_URL) > W - 2 * MARGIN:
    raise SystemExit(f"{BEFORE!r} is too wide for the card -- pick a shorter example")
img.save(OUT, optimize=True)
print(f"wrote {OUT}  ({OUT.stat().st_size // 1024} KB)")
