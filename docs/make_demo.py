"""Render the README demo GIF.

Composed, not screen-recorded: a real capture would show this machine's desktop,
wallpaper and open windows -- noise, and a privacy leak.

It shows what 0.2.0 does: not one bare link, but a whole copied message with two links
in it. Three things keep it honest:
  * The text, the cleaned links and the tracker count come from calling the REAL
    clean_text(), so the GIF can never drift from what the product does.
  * Each query parameter is coloured individually. Only the params nudl actually strips
    are struck through -- `psc=1` and `t=42` survive on screen exactly as they survive
    in reality. Striking the whole URL would advertise a product that destroys the link.
  * The prose around the links is drawn from the engine's output, character for
    character, so "everything else comes back exactly as it was" is shown, not claimed.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from src.clean import clean_text, find_links  # noqa: E402

OUT = Path(__file__).resolve().parent / "demo.gif"
OUT.parent.mkdir(parents=True, exist_ok=True)

W, H = 900, 400
BG = (13, 15, 18)
PANEL = (22, 24, 29)
FG = (232, 234, 237)
MUTED = (154, 160, 166)
ACCENT = (76, 183, 130)
RED = (237, 106, 94)

MESSAGE = (
    "Loved this one: "
    "https://www.amazon.com/dp/B08X7QK2P?tag=affiliate-20&ref_=nb_sb&psc=1&utm_source=newsletter\n"
    "and the video: https://youtu.be/dQw4w9WgXcQ?si=Ab12Cd34Ef56&t=42"
)
result = clean_text(MESSAGE)
CLEAN = result.result
REMOVED = result.params_removed
# `raise`, not `assert`: python -O strips asserts, and these lines are the only thing
# standing between the README's GIF and a picture of behaviour nudl no longer has.
if len(result.links) != 2:
    raise SystemExit("the demo message should clean exactly two links -- fix the demo, not this")
if "psc=1" not in CLEAN or "t=42" not in CLEAN:
    raise SystemExit("the demo shows psc=1 and t=42 surviving, and they no longer do")
if not CLEAN.startswith("Loved this one: ") or "\nand the video: " not in CLEAN:
    raise SystemExit("the prose around the links changed, and the demo promises it never does")


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Segoe UI where it exists (Windows); a metric-compatible sans elsewhere."""
    candidates = (
        ("segoeuib.ttf", "LiberationSans-Bold.ttf", "DejaVuSans-Bold.ttf")
        if bold
        else ("segoeui.ttf", "LiberationSans-Regular.ttf", "DejaVuSans.ttf")
    )
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


F_TITLE, F_URL = font(26, bold=True), font(15)
F_LABEL, F_TOAST, F_TOAST_B, F_KEY = font(13), font(15), font(15, bold=True), font(14, bold=True)
LINE = 24


def tokenize(text: str) -> list[list[tuple[str, str]]]:
    """Split a message into lines of drawable tokens: ('text' | 'link' | 'tracker', str).

    Prose is split on spaces so it can wrap; links are split per query parameter so a
    tracker can be struck without harming the rest of the URL.
    """
    lines: list[list[tuple[str, str]]] = []
    for line in text.split("\n"):
        tokens: list[tuple[str, str]] = []
        cursor = 0
        for start, end in list(find_links(line)) + [(len(line), len(line))]:
            for word in line[cursor:start].split(" "):
                if word:
                    tokens.append(("text", word))
                tokens.append(("text", " "))
            tokens.pop()  # the trailing space the split invented
            if start == end:
                break
            url = line[start:end]
            base, _, query = url.partition("?")
            tokens.append(("link", base))
            if query:
                tokens.append(("link", "?"))
                for i, pair in enumerate(query.split("&")):
                    if i:
                        tokens.append(("link", "&"))
                    key = pair.split("=", 1)[0]
                    tokens.append(("tracker" if key in REMOVED else "link", pair))
            cursor = end
        lines.append(tokens)
    return lines


def draw_message(d: ImageDraw.ImageDraw, text: str, x0: int, y0: int, max_w: int, *, mark: bool):
    """Draw the message token by token: prose plain, trackers struck, clean links green."""
    y = y0
    for tokens in tokenize(text):
        x = x0
        for kind, token in tokens:
            w = F_URL.getlength(token)
            if x + w > x0 + max_w and x > x0:
                x, y = x0, y + LINE
                if token == " ":
                    continue
            if kind == "tracker" and mark:
                colour = RED
            elif kind == "text":
                colour = FG
            else:
                colour = FG if mark else ACCENT
            d.text((x, y), token, font=F_URL, fill=colour)
            if kind == "tracker" and mark:
                d.line((x, y + 11, x + w, y + 11), fill=RED, width=1)
            x += w
        y += LINE
    return y


def frame(*, text, caption, caption_colour, mark=False, show_key=False, show_toast=False):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    d.text((48, 34), "nudl", font=F_TITLE, fill=ACCENT)
    d.text((112, 42), "strips the tracking junk off any link you copy", font=F_LABEL, fill=MUTED)

    d.rounded_rectangle((48, 92, W - 48, 248), radius=12, fill=PANEL)
    d.text((70, 108), "CLIPBOARD", font=F_LABEL, fill=MUTED)
    draw_message(d, text, 70, 136, W - 140, mark=mark)

    d.text((48, 268), caption, font=F_LABEL, fill=caption_colour)

    if show_key:
        x = 48
        for key in ("Ctrl", "Alt", "V"):
            w = F_KEY.getlength(key) + 22
            d.rounded_rectangle((x, 302, x + w, 336), radius=6, fill=PANEL, outline=ACCENT)
            d.text((x + 11, 310), key, font=F_KEY, fill=ACCENT)
            x += w + 8
            if key != "V":
                d.text((x - 4, 310), "+", font=F_KEY, fill=MUTED)
                x += 12

    if show_toast:
        label = f"nudl — removed {len(REMOVED)} trackers from {len(result.links)} links"
        tw = F_TOAST.getlength(label) + F_TOAST_B.getlength("Undo") + 74
        x0, y0 = W - 48 - tw, 302
        d.rounded_rectangle((x0, y0, W - 48, y0 + 52), radius=8, fill=PANEL, outline=(44, 48, 56))
        d.text((x0 + 20, y0 + 16), label, font=F_TOAST, fill=FG)
        d.text((W - 72 - F_TOAST_B.getlength("Undo"), y0 + 16), "Undo", font=F_TOAST_B, fill=ACCENT)

    return img


scenes = [
    (
        frame(
            text=MESSAGE,
            caption="You copied a message with links in it.",
            caption_colour=MUTED,
            mark=True,
        ),
        18,
    ),
    (
        frame(
            text=MESSAGE,
            caption=f"{len(REMOVED)} trackers riding along: " + ", ".join(REMOVED),
            caption_colour=RED,
            mark=True,
        ),
        16,
    ),
    (
        frame(
            text=MESSAGE,
            caption="Press the hotkey.",
            caption_colour=MUTED,
            mark=True,
            show_key=True,
        ),
        14,
    ),
    (
        frame(
            text=CLEAN,
            caption="Same message. Same links. Same destinations. No trackers.",
            caption_colour=ACCENT,
            show_toast=True,
        ),
        34,
    ),
    (
        frame(
            text=CLEAN,
            caption=(
                "Every other character comes back exactly as it was. "
                "100% local — nothing leaves your machine."
            ),
            caption_colour=MUTED,
        ),
        26,
    ),
]

frames = [img for img, hold in scenes for _ in range(hold)]
frames[0].save(OUT, save_all=True, append_images=frames[1:], duration=60, loop=0, optimize=True)

print(f"in  : {MESSAGE!r}")
print(f"out : {CLEAN!r}")
print(f"struck through ({len(REMOVED)}): {', '.join(REMOVED)}")
print("KEPT on screen, as in reality: psc=1, t=42, and every character of the prose")
print(f"\n{OUT}  ({OUT.stat().st_size / 1024:.0f} KB)")
