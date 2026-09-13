#!/usr/bin/env python3
"""Render assets/example_report.png for the README.

Every number and device name below is fabricated. It illustrates the shape
and magnitude of what `knowledgec_audit.py report` surfaces without
publishing any maintainer's real activity, timing, or paired-device data.
Regenerate after editing EXAMPLE_SECTIONS: python3 assets/generate_example_report.py
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUTPUT_PATH = Path(__file__).parent / "example_report.png"

WIDTH = 1100
PADDING_X = 56
PADDING_TOP = 48
PADDING_BOTTOM = 48
LINE_HEIGHT = 34
SECTION_GAP = 26
BULLET_GAP = 14
BULLET_INDENT = 30

BACKGROUND = "#16171b"
CAPTION_COLOR = "#8a8d94"
HEADER_COLOR = "#f2f2f3"
BODY_COLOR = "#d7d8db"
BOLD_COLOR = "#ffffff"
BULLET_COLOR = "#6b6f78"

FONT_DIR = Path("/System/Library/Fonts/Supplemental")
FONT_REGULAR = ImageFont.truetype(str(FONT_DIR / "Arial.ttf"), 19)
FONT_BOLD = ImageFont.truetype(str(FONT_DIR / "Arial Bold.ttf"), 19)
FONT_HEADER = ImageFont.truetype(str(FONT_DIR / "Arial Bold.ttf"), 24)
FONT_CAPTION = ImageFont.truetype(str(FONT_DIR / "Arial.ttf"), 16)

EXAMPLE_SECTIONS = [
    (
        "Screen time",
        [
            "162.4 hours of tracked foreground app time over 21 active days "
            "(of 30 calendar days in the window) -- avg **7.7 hrs/day** on "
            "days the machine was used.",
            "**Browser dominates: 58.4%** of all foreground time (94.6 hrs, "
            "2,910 sessions). Average session is only **117 seconds** -- "
            "heavy tab-hopping, not long reading sessions.",
            "Code editor is #2: 41.2 hrs across 2,588 sessions, averaging "
            "just **57.3 seconds/session** -- very choppy, consistent with "
            "bouncing constantly between editor and terminal (terminal "
            "itself: 6.1 hrs, 640 sessions).",
            "Longest unbroken sessions: browser 52.4 min, editor 48.9 min.",
        ],
    ),
    (
        "Daily rhythm",
        [
            "**8-9pm** is the peak hour of the entire month -- 13.6 hours "
            "logged in that single hour-slot across all days combined.",
            "Clean sleep window: essentially **zero activity 1am-5am** all "
            "month.",
            "Odd dip at **5-6pm** sandwiched between an afternoon peak and "
            "a second climb into the evening -- looks like a consistent "
            "dinner/commute break.",
            "**Wednesday is the heaviest day** (2,015 min total across the "
            "month); weekends are a cliff -- Saturday + Sunday combined "
            "barely beats one weekday.",
            "Busiest single calendar day: **11.5 hours** of active app "
            "time across 610 sessions.",
        ],
    ),
    (
        "Bluetooth",
        [
            "Paired overwhelmingly with one named device: connected "
            "**190+ times** in the month -- averaging several times a day.",
            "A second device connected a handful of times, and a third "
            "only once all month.",
        ],
    ),
]

_BOLD_SPLIT = re.compile(r"(\*\*.*?\*\*)")


def parse_bold_segments(text: str) -> list[tuple[str, bool]]:
    segments = []
    for part in _BOLD_SPLIT.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            segments.append((part[2:-2], True))
        else:
            segments.append((part, False))
    return segments


def word_tokens(text: str) -> list[tuple[str, bool]]:
    tokens = []
    for content, bold in parse_bold_segments(text):
        for word in content.split(" "):
            if word:
                tokens.append((word, bold))
    return tokens


def wrap_tokens(
    tokens: list[tuple[str, bool]], draw: ImageDraw.ImageDraw, max_width: int
) -> list[list[tuple[str, bool]]]:
    lines: list[list[tuple[str, bool]]] = [[]]
    x = 0
    space_width = draw.textlength(" ", font=FONT_REGULAR)
    for word, bold in tokens:
        font = FONT_BOLD if bold else FONT_REGULAR
        word_width = draw.textlength(word, font=font)
        if lines[-1] and x + word_width > max_width:
            lines.append([])
            x = 0
        lines[-1].append((word, bold))
        x += word_width + space_width
    return lines


def draw_bullet(
    draw: ImageDraw.ImageDraw, x: int, y: int, text: str, max_width: int
) -> int:
    draw.text((x, y), "•", font=FONT_REGULAR, fill=BULLET_COLOR)
    text_x = x + BULLET_INDENT
    tokens = word_tokens(text)
    lines = wrap_tokens(tokens, draw, max_width - BULLET_INDENT)
    space_width = draw.textlength(" ", font=FONT_REGULAR)
    for line in lines:
        cursor_x = text_x
        for word, bold in line:
            font = FONT_BOLD if bold else FONT_REGULAR
            color = BOLD_COLOR if bold else BODY_COLOR
            draw.text((cursor_x, y), word, font=font, fill=color)
            cursor_x += draw.textlength(word, font=font) + space_width
        y += LINE_HEIGHT
    return y


def render() -> None:
    scratch = Image.new("RGB", (10, 10))
    scratch_draw = ImageDraw.Draw(scratch)
    max_text_width = WIDTH - PADDING_X * 2

    y = PADDING_TOP + 26
    for title, bullets in EXAMPLE_SECTIONS:
        y += 22 + SECTION_GAP
        for bullet in bullets:
            lines = wrap_tokens(
                word_tokens(bullet), scratch_draw, max_text_width - BULLET_INDENT
            )
            y += LINE_HEIGHT * len(lines) + BULLET_GAP
    height = y + PADDING_BOTTOM

    image = Image.new("RGB", (WIDTH, height), BACKGROUND)
    draw = ImageDraw.Draw(image)

    y = PADDING_TOP
    draw.text(
        (PADDING_X, y),
        "EXAMPLE FINDINGS -- SYNTHETIC DATA, NOT FROM ANY REAL DEVICE",
        font=FONT_CAPTION,
        fill=CAPTION_COLOR,
    )
    y += 26 + SECTION_GAP

    for title, bullets in EXAMPLE_SECTIONS:
        draw.text((PADDING_X, y), title, font=FONT_HEADER, fill=HEADER_COLOR)
        y += 22 + SECTION_GAP
        for bullet in bullets:
            y = draw_bullet(draw, PADDING_X, y, bullet, max_text_width)
            y += BULLET_GAP

    image.save(OUTPUT_PATH)
    print(f"wrote {OUTPUT_PATH} ({WIDTH}x{height})")


if __name__ == "__main__":
    render()
