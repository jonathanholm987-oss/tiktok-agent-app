"""Rendert Slides als 1080x1920-JPEGs."""
from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .core import Product

WIDTH, HEIGHT = 1080, 1920
# TikTok überlagert oben, rechts und unten Bedienelemente.
LEFT, RIGHT, TOP, BOTTOM = 84, 960, 190, 1490

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
HEAVY = FONT_DIR / "Inter-ExtraBold.otf"
BOLD = FONT_DIR / "Inter-Bold.otf"
MEDIUM = FONT_DIR / "Inter-Medium.otf"

# Hintergrund, Text, Akzent, Text auf Akzent
PALETTES = [
    ("#14161A", "#F6F1E7", "#FF6A3D", "#14161A"),
    ("#FFD23F", "#14161A", "#14161A", "#FFD23F"),
    ("#0F4C3A", "#F2F7EE", "#B8F04A", "#0F3327"),
    ("#F6F1E7", "#14161A", "#2B4BFF", "#FFFFFF"),
    ("#2B1E5C", "#F7F3FF", "#FF8FC7", "#2B1E5C"),
]


def palette_for(key: str) -> tuple[str, str, str, str]:
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return PALETTES[digest[0] % len(PALETTES)]


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def _break_long_word(draw, word, font, max_width) -> list[str]:
    parts: list[str] = []
    current = ""
    for char in word:
        if draw.textlength(current + char + "-", font=font) > max_width and current:
            parts.append(current + "-")
            current = char
        else:
            current += char
    parts.append(current)
    return parts


def wrap(draw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        pieces = (
            _break_long_word(draw, word, font, max_width)
            if draw.textlength(word, font=font) > max_width
            else [word]
        )
        for piece in pieces:
            trial = f"{current} {piece}".strip()
            if draw.textlength(trial, font=font) <= max_width:
                current = trial
            else:
                if current:
                    lines.append(current)
                current = piece
    if current:
        lines.append(current)
    return lines


def fit(draw, text, path, max_width, max_height, largest, smallest, ratio=1.14):
    """Größte Schrift, bei der der Text passt, möglichst ohne Worttrennung."""
    for size in range(largest, smallest - 1, -4):
        font = _font(path, size)
        if any(draw.textlength(word, font=font) > max_width for word in text.split()):
            continue
        lines = wrap(draw, text, font, max_width)
        line_height = int(size * ratio)
        if len(lines) * line_height <= max_height:
            return font, lines, line_height
    font = _font(path, smallest)
    return font, wrap(draw, text, font, max_width), int(smallest * ratio)


def _text(draw, x, y, lines, font, line_height, fill) -> int:
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height
    return y


def _pill(draw, x, y, text, font, background, foreground) -> int:
    width = int(draw.textlength(text, font=font))
    height = int(font.size * 2.1)
    draw.rounded_rectangle(
        (x, y, x + width + 52, y + height), radius=height // 2, fill=background
    )
    draw.text((x + 26, y + height / 2), text, font=font, fill=foreground, anchor="lm")
    return y + height


def _base(palette):
    image = Image.new("RGB", (WIDTH, HEIGHT), palette[0])
    draw = ImageDraw.Draw(image)
    _pill(draw, LEFT, TOP - 60, "WERBUNG", _font(BOLD, 30), palette[2], palette[3])
    return image, draw


def hook_slide(hook: str, product: Product, palette, photo: Image.Image | None):
    image, draw = _base(palette)
    top = TOP + 50
    box_bottom = BOTTOM - 120
    if photo is not None:
        frame = ImageOps.fit(photo.convert("RGB"), (RIGHT - LEFT, 640))
        image.paste(frame, (LEFT, BOTTOM - 120 - 640))
        box_bottom = BOTTOM - 120 - 640 - 50
    font, lines, line_height = fit(
        draw, hook, HEAVY, RIGHT - LEFT, box_bottom - top, 148, 64, 1.08
    )
    height = len(lines) * line_height
    y = top + max(0, (box_bottom - top - height) // 2)
    _text(draw, LEFT, y, lines, font, line_height, palette[1])
    arrow = _font(BOLD, 48)
    draw.text((LEFT, BOTTOM - 50), "Wischen →", font=arrow, fill=palette[2], anchor="lm")
    return image


def content_slide(index: int, total: int, slide: dict, palette):
    image, draw = _base(palette)
    counter = _font(MEDIUM, 36)
    draw.text((RIGHT, TOP - 28), f"{index}/{total}", font=counter, fill=palette[1], anchor="rm")

    number_height = 300
    area_top = TOP + 50
    body = str(slide.get("body") or "").strip()
    head_space = BOTTOM - area_top - number_height - (300 if body else 0)
    head_font, head_lines, head_line = fit(
        draw, str(slide["headline"]), HEAVY, RIGHT - LEFT, head_space, 124, 56
    )
    head_height = len(head_lines) * head_line
    body_font, body_lines, body_line, body_height = None, [], 0, 0
    if body:
        body_font, body_lines, body_line = fit(
            draw, body, MEDIUM, RIGHT - LEFT,
            BOTTOM - area_top - number_height - head_height - 40, 56, 36, 1.3,
        )
        body_height = 40 + len(body_lines) * body_line

    block = number_height + head_height + body_height
    y = area_top + max(0, (BOTTOM - area_top - block) // 2)
    draw.text((LEFT - 8, y - 40), str(index), font=_font(HEAVY, 280), fill=palette[2])
    y = _text(draw, LEFT, y + number_height, head_lines, head_font, head_line, palette[1])
    if body:
        _text(draw, LEFT, y + 40, body_lines, body_font, body_line, palette[1])
    return image


def cta_slide(product: Product, palette):
    image, draw = _base(palette)
    draw.text((LEFT, TOP + 170), "Link in Bio", font=_font(HEAVY, 124), fill=palette[1])
    draw.text((LEFT - 10, TOP + 330), f"Nr. {product.nr}", font=_font(HEAVY, 300), fill=palette[2])
    font, lines, line_height = fit(
        draw, product.short, BOLD, RIGHT - LEFT, 240, 58, 38, 1.2
    )
    y = _text(draw, LEFT, TOP + 720, lines, font, line_height, palette[1])
    disclosure, lines, line_height = fit(
        draw,
        "Werbung. Als Amazon-Partner verdiene ich an qualifizierten Verkäufen.",
        MEDIUM,
        RIGHT - LEFT,
        200,
        40,
        30,
        1.3,
    )
    _text(draw, LEFT, max(y + 60, BOTTOM - 180), lines, disclosure, line_height, palette[1])
    return image


def render(draft: dict, product: Product, output: Path, root: Path, key: str) -> list[str]:
    """Schreibt alle Slides und gibt die Dateinamen in Reihenfolge zurück."""
    output.mkdir(parents=True, exist_ok=True)
    palette = palette_for(key)
    photo = None
    if product.image:
        path = (root / product.image).resolve()
        if path.is_relative_to(root.resolve()) and path.exists():
            photo = Image.open(path)
        else:
            print(f"Bild nicht gefunden oder außerhalb des Projekts: {product.image}")

    total = len(draft["slides"])
    images = [hook_slide(draft["hook"], product, palette, photo)]
    images += [
        content_slide(i, total, slide, palette)
        for i, slide in enumerate(draft["slides"], 1)
    ]
    images.append(cta_slide(product, palette))

    names: list[str] = []
    for number, image in enumerate(images, 1):
        name = f"{number:02d}.jpg"
        image.save(output / name, "JPEG", quality=92, optimize=True)
        names.append(name)
    return names
