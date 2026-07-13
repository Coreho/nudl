"""Render the README demo GIF.

Composed, not screen-recorded: a real capture would show this machine's desktop,
wallpaper and open windows -- noise, and a privacy leak.

Two things keep it honest:
  * The URLs and the tracker count come from calling the REAL clean(), so the GIF can
    never drift from what the product does.
  * Each query parameter is coloured individually. Only the params nudl actually strips
    are struck through -- `psc=1` survives on screen exactly as it survives in reality.
    Striking the whole URL would advertise a product that destroys the link.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from src.clean import clean_result

OUT = Path(__file__).resolve().parent / "demo.gif"
OUT.parent.mkdir(parents=True, exist_ok=True)

W, H = 900, 360
BG = (13, 15, 18)
PANEL = (22, 24, 29)
FG = (232, 234, 237)
MUTED = (154, 160, 166)
ACCENT = (76, 183, 130)
RED = (237, 106, 94)

UGLY = "https://www.amazon.com/dp/B08X7QK2P?tag=affiliate-20&ref_=nb_sb&psc=1&utm_source=newsletter"
result = clean_result(UGLY)
CLEAN = result.result
REMOVED = result.params_removed
# `raise`, not `assert`: python -O strips asserts, and these two lines are the only thing
# standing between the README's GIF and a picture of behaviour nudl no longer has.
if not result.changed:
    raise SystemExit("the demo URL no longer cleans -- fix the demo, not this check")
if "psc=1" not in CLEAN:
    raise SystemExit("the demo shows psc=1 surviving, and it no longer does")


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype("segoeuib.ttf" if bold else "segoeui.ttf", size)
    except OSError:
        return ImageFont.load_default(size)


F_TITLE, F_URL = font(26, bold=True), font(15)
F_LABEL, F_TOAST, F_TOAST_B, F_KEY = font(13), font(15), font(15, bold=True), font(14, bold=True)


def tokenize(url: str) -> list[tuple[str, bool]]:
    """Split a URL into drawable tokens, flagging the ones nudl strips."""
    base, _, query = url.partition("?")
    tokens: list[tuple[str, bool]] = [(base, False)]
    if not query:
        return tokens
    tokens.append(("?", False))
    for i, pair in enumerate(query.split("&")):
        if i:
            tokens.append(("&", False))
        key = pair.split("=", 1)[0]
        tokens.append((pair, key in REMOVED))
    return tokens


def draw_url(d: ImageDraw.ImageDraw, url: str, x0: int, y0: int, max_w: int, *, mark: bool):
    """Draw a URL token by token, so trackers can be struck without harming the rest."""
    x, y = x0, y0
    for text, is_tracker in tokenize(url):
        w = F_URL.getlength(text)
        if x + w > x0 + max_w and x > x0:
            x, y = x0, y + 24
        colour = RED if (mark and is_tracker) else (ACCENT if not mark else FG)
        d.text((x, y), text, font=F_URL, fill=colour)
        if mark and is_tracker:
            d.line((x, y + 11, x + w, y + 11), fill=RED, width=1)
        x += w
    return y


def frame(*, url, caption, caption_colour, mark=False, show_key=False, show_toast=False):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    d.text((48, 34), "nudl", font=F_TITLE, fill=ACCENT)
    d.text((112, 42), "strips the tracking junk off any link you copy", font=F_LABEL, fill=MUTED)

    d.rounded_rectangle((48, 92, W - 48, 208), radius=12, fill=PANEL)
    d.text((70, 108), "CLIPBOARD", font=F_LABEL, fill=MUTED)
    draw_url(d, url, 70, 136, W - 140, mark=mark)

    d.text((48, 228), caption, font=F_LABEL, fill=caption_colour)

    if show_key:
        x = 48
        for key in ("Ctrl", "Alt", "V"):
            w = F_KEY.getlength(key) + 22
            d.rounded_rectangle((x, 262, x + w, 296), radius=6, fill=PANEL, outline=ACCENT)
            d.text((x + 11, 270), key, font=F_KEY, fill=ACCENT)
            x += w + 8
            if key != "V":
                d.text((x - 4, 270), "+", font=F_KEY, fill=MUTED)
                x += 12

    if show_toast:
        label = f"nudl — removed {len(REMOVED)} trackers"
        tw = F_TOAST.getlength(label) + F_TOAST_B.getlength("Undo") + 74
        x0, y0 = W - 48 - tw, 262
        d.rounded_rectangle((x0, y0, W - 48, y0 + 52), radius=8, fill=PANEL, outline=(44, 48, 56))
        d.text((x0 + 20, y0 + 16), label, font=F_TOAST, fill=FG)
        d.text((W - 72 - F_TOAST_B.getlength("Undo"), y0 + 16), "Undo", font=F_TOAST_B, fill=ACCENT)

    return img


scenes = [
    (frame(url=UGLY, caption="You copied a link.", caption_colour=MUTED, mark=True), 18),
    (
        frame(
            url=UGLY,
            caption=f"{len(REMOVED)} trackers riding along: " + ", ".join(REMOVED),
            caption_colour=RED,
            mark=True,
        ),
        16,
    ),
    (
        frame(
            url=UGLY,
            caption="Press the hotkey.",
            caption_colour=MUTED,
            mark=True,
            show_key=True,
        ),
        14,
    ),
    (
        frame(
            url=CLEAN,
            caption="Same link. Same destination. No trackers.",
            caption_colour=ACCENT,
            show_toast=True,
        ),
        34,
    ),
    (
        frame(
            url=CLEAN,
            caption="Works in every app. 100% local — your links never leave your machine.",
            caption_colour=MUTED,
        ),
        26,
    ),
]

frames = [img for img, hold in scenes for _ in range(hold)]
frames[0].save(OUT, save_all=True, append_images=frames[1:], duration=60, loop=0, optimize=True)

print(f"in  : {UGLY}")
print(f"out : {CLEAN}")
print(f"struck through ({len(REMOVED)}): {', '.join(REMOVED)}")
print("KEPT on screen, as in reality: psc=1")
print(f"\n{OUT}  ({OUT.stat().st_size / 1024:.0f} KB)")
