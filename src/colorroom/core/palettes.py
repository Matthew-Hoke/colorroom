"""Palette styles: colors taken from the image, color-wheel harmonies and presets.

Ported from Colorroom 1.7, with two fixes from the plan: Monochromatic and Seasonal now
respect the requested number of colors instead of always returning five.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from .colorspace import (
    from_hsl,
    hex_to_key,
    mix_oklab,
    normalize_hex,
    oklab_distance,
    shift_on_wheel,
    to_hsl,
)
from .index import ColorIndex, Point

IMAGE_MODES = ("balanced", "dominant")

#: Style menu, in display order: (group title, [(mode key, label), ...]).
MODE_GROUPS = (
    ("From the image", (("balanced", "Balanced"), ("dominant", "Dominant"))),
    (
        "Color wheel",
        (
            ("monochromatic", "Monochromatic"),
            ("analogous", "Analogous"),
            ("complementary", "Complementary"),
            ("split", "Split-Complementary"),
            ("triadic", "Triadic"),
            ("tetradic", "Tetradic (Double-Complementary)"),
        ),
    ),
    (
        "Functional and aesthetic",
        (
            ("neutral", "Neutral"),
            ("pastel", "Pastel"),
            ("vibrant", "Vibrant / Bright"),
            ("seasonal", "Seasonal / Thematic"),
            ("custom", "Custom / Brand Identity"),
        ),
    ),
)
MODES = tuple(key for _, modes in MODE_GROUPS for key, _ in modes)
MODE_LABELS = {key: label for _, modes in MODE_GROUPS for key, label in modes}

#: Steps around the 12-part artist's wheel for each harmony, with its description.
WHEEL_STYLES = {
    "monochromatic": (None, "Shades, tints, and muted tones of one hue."),
    "analogous": ((-1, 1), "The two hues beside your base on the artist’s color wheel."),
    "complementary": ((6,), "Your base and the hue directly opposite it."),
    "split": ((5, 7), "The two hues on either side of your base’s opposite."),
    "triadic": ((4, 8), "Three hues equally spaced around the wheel."),
    "tetradic": ((2, 6, 8), "Two complementary pairs, including your base."),
}
AESTHETIC_STYLES = {
    "neutral": "Low-chroma lights, midtones, and darks.",
    "pastel": "Light, softly saturated hues.",
    "vibrant": "Bright, highly saturated hues.",
}
THEMES = {
    "Spring": ("#F6E8C8", "#F4AAA7", "#F6C46B", "#83A867", "#877AB3"),
    "Summer": ("#FCE8B5", "#FF9876", "#37B4AE", "#3675AB", "#133E62"),
    "Autumn": ("#F1DAB6", "#D88738", "#A9432F", "#6D7040", "#51302A"),
    "Winter": ("#EDF4FA", "#B7CDD9", "#6481A3", "#32456B", "#593D67"),
    "Holiday": ("#F6EFE1", "#CB9F49", "#A92D40", "#306451", "#24332C"),
    "Nature": ("#E8DBBA", "#9CA667", "#5A794E", "#3F6D76", "#754B3A"),
}
BRAND_ROLES = ("Dominant · 60%", "Secondary · 30%", "Accent · 10%")

MAX_PICKS = 12
SIZES = tuple(range(3, 13))
DEFAULT_SIZE = 6

#: (lightness, share of the base saturation) for monochromatic variants. The first five
#: are the original app's; the rest extend the ramp so up to 12 colors can be requested.
_MONOCHROME_TONES = (
    (0.18, 1.0), (0.35, 0.65), (0.67, 0.72), (0.86, 0.9), (0.51, 0.8),
    (0.93, 0.6), (0.10, 0.9), (0.27, 0.85), (0.76, 0.8), (0.43, 0.7),
    (0.59, 0.6), (0.05, 0.8), (0.97, 0.5),
)  # fmt: skip
_NEUTRAL_LIGHTS = (0.96, 0.81, 0.65, 0.43, 0.23, 0.12, 0.90, 0.73, 0.53, 0.34, 0.17, 0.06)
_GOLDEN_ANGLE = 137.508


def uses_size(mode: str) -> bool:
    """True if the "Colors" count affects this style."""
    return mode in IMAGE_MODES or mode in AESTHETIC_STYLES or mode in ("monochromatic", "seasonal")


def _unique(colors: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(colors))


def theme_colors(theme: str, size: int = 5) -> list[str]:
    """A seasonal preset stretched or trimmed to `size` colors.

    Five gives the preset as designed. Fewer keeps an evenly spaced selection. More
    adds in-between blends where neighbouring colors are furthest apart.
    """
    colors = list(THEMES[theme])
    size = max(1, int(size))
    if size < len(colors):
        if size == 1:
            return [colors[len(colors) // 2]]
        last = len(colors) - 1
        return [colors[round(position * last / (size - 1))] for position in range(size)]
    while len(colors) < size:
        gaps = [oklab_distance(colors[i], colors[i + 1]) for i in range(len(colors) - 1)]
        widest = max(range(len(gaps)), key=gaps.__getitem__)
        colors.insert(widest + 1, mix_oklab(colors[widest], colors[widest + 1]))
    return colors


def suggest_colors(mode: str, base: str, size: int = DEFAULT_SIZE, theme: str = "Spring") -> list[str]:
    """Colors for every style that is computed from a base color or a preset."""
    base = normalize_hex(base)
    hue, saturation, lightness = to_hsl(base)
    if mode == "monochromatic":
        variants = [from_hsl(hue, saturation * intensity, light) for light, intensity in _MONOCHROME_TONES]
        return _unique([base, *variants])[: max(1, size)]
    if mode in WHEEL_STYLES:
        steps = WHEEL_STYLES[mode][0]
        intensity = max(0.5, saturation)
        light = max(0.34, min(0.68, lightness))
        return [base, *(from_hsl(shift_on_wheel(hue, step), intensity, light) for step in steps)]
    if mode == "seasonal":
        return theme_colors(theme, size)
    if mode == "neutral":
        return [
            from_hsl(hue, (0.08, 0.14, 0.18)[position % 3], light)
            for position, light in enumerate(_NEUTRAL_LIGHTS[:size])
        ]
    if mode == "pastel":
        return [
            from_hsl(hue + position * _GOLDEN_ANGLE, 0.43, (0.83, 0.88, 0.79)[position % 3])
            for position in range(size)
        ]
    if mode == "vibrant":
        return [
            from_hsl(hue + position * _GOLDEN_ANGLE, 0.91, (0.53, 0.48, 0.59)[position % 3])
            for position in range(size)
        ]
    raise ValueError(f"No suggested colors for style {mode!r}")


# --------------------------------------------------------------------------------------
# Palettes tied to an image
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PaletteEntry:
    """One swatch: the palette color and the image pixel that represents it."""

    color: str  # the palette color
    source_color: str  # the image color shown for it (same as `color` if the image has it)
    source_index: int  # distinct index of `source_color`
    point: Point  # where `source_color` appears in the image
    role: str = ""  # e.g. "Dominant · 60%" for brand palettes

    @property
    def exact(self) -> bool:
        """True if the palette color itself occurs in the image."""
        return self.color == self.source_color


@dataclass(frozen=True)
class Palette:
    mode: str
    entries: tuple[PaletteEntry, ...]
    note: str  # one-line explanation of how the palette was made

    @property
    def colors(self) -> list[str]:
        return [entry.color for entry in self.entries]


def image_palette(index: ColorIndex, size: int, mode: str = "balanced") -> list[str]:
    """Representative colors taken straight from the image ("balanced" or "dominant")."""
    limit = min(int(size), index.distinct)
    chosen = index.dominant(limit) if mode == "dominant" else index.balanced(limit)
    chosen = list(dict.fromkeys(chosen))
    if len(chosen) < limit:  # few color groups: top up with the most frequent colors
        taken = set(chosen)
        for candidate in index.frequency_order:
            candidate = int(candidate)
            if candidate not in taken:
                taken.add(candidate)
                chosen.append(candidate)
                if len(chosen) == limit:
                    break
    return [index.hex(position) for position in chosen]


def palette_entry(index: ColorIndex, color: str, role: str = "") -> PaletteEntry:
    """Attach a palette color to the image: itself if present, else its closest shade."""
    color = normalize_hex(color)
    source = index.nearest(hex_to_key(color))
    return PaletteEntry(color, index.hex(source), source, index.locate(source), role)


def build_palette(
    index: ColorIndex,
    mode: str = "balanced",
    size: int = DEFAULT_SIZE,
    theme: str = "Spring",
    picks: Sequence[str] = (),
    base: Optional[str] = None,
) -> Palette:
    """Build the palette for the chosen style.

    `picks` are the colors the user selected; the first one is the base for the
    color-wheel styles. Without picks the image's most frequent color is the base.
    """
    if mode not in MODES:
        raise ValueError(f"Unknown palette style: {mode!r}")
    picks = _unique([normalize_hex(color) for color in picks])
    roles: Sequence[str] = ()

    if mode in IMAGE_MODES:
        note = "Extracts representative colors from the image."
        colors = image_palette(index, size, mode)
    elif mode == "custom":
        if len(picks) >= 3:
            note = "Suggested usage for your first three picks: dominant, secondary, and accent."
            roles = BRAND_ROLES
        else:
            note = "Pick at least three colors to assign 60/30/10 design roles."
        colors = picks
    else:
        base = normalize_hex(picks[0] if picks else (base or index.base_color))
        if mode in WHEEL_STYLES:
            steps, description = WHEEL_STYLES[mode]
            origin = "" if picks else " (most frequent shade)"
            gray = " Blue guides the suggested hues for this gray base." if steps and not to_hsl(base)[1] else ""
            note = f"{description} Base {base}{origin}.{gray}"
        elif mode in AESTHETIC_STYLES:
            note = AESTHETIC_STYLES[mode]
        else:
            note = f"A preset inspired by {theme.lower()}."
        colors = _unique([*picks, *suggest_colors(mode, base, size, theme)])

    entries = tuple(
        palette_entry(index, color, roles[position] if position < len(roles) else "")
        for position, color in enumerate(colors)
    )
    return Palette(mode, entries, note)
