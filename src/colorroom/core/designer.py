"""Tools for working on a palette as a designer: a palette you collect yourself, contrast
checks, color-blindness simulation, tint and shade ramps, print values, and the formats
that carry colors into design apps. No window code in here.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

import numpy as np

from .colorspace import (
    SRGB_TO_LINEAR,
    contrast_ratio,
    hex_to_key,
    key_to_hex,
    key_to_oklab,
    key_to_rgb,
    mix_oklab,
    normalize_hex,
    oklab_distance,
    oklab_to_key,
    readable_text_color,
    to_hsl,
)

MAX_COLORS = 24  # colors in one palette
PROJECT_EXTENSION = ".colorroom"
PROJECT_VERSION = 1

# --------------------------------------------------------------------------------------
# The palette you collect
# --------------------------------------------------------------------------------------


@dataclass
class PaletteColor:
    hex: str
    name: str = ""
    locked: bool = False

    def label(self) -> str:
        return self.name or self.hex


@dataclass
class NamedPalette:
    name: str = "My palette"
    colors: list[PaletteColor] = field(default_factory=list)

    def hexes(self) -> list[str]:
        return [color.hex for color in self.colors]

    def names(self) -> list[str]:
        return [color.name for color in self.colors]

    def has(self, color: str) -> bool:
        return normalize_hex(color) in self.hexes()

    def add(self, colors: Iterable[str]) -> tuple[int, int, int]:
        """Add HEX colors, skipping ones already there. Returns (added, duplicates, no room)."""
        added = duplicates = dropped = 0
        present = set(self.hexes())
        for raw in colors:
            color = normalize_hex(raw)
            if color in present:
                duplicates += 1
            elif len(self.colors) >= MAX_COLORS:
                dropped += 1
            else:
                self.colors.append(PaletteColor(color))
                present.add(color)
                added += 1
        return added, duplicates, dropped

    def remove(self, positions: Iterable[int]) -> int:
        """Remove the colors at these positions; locked colors stay. Returns how many went."""
        doomed = {position for position in positions if 0 <= position < len(self.colors) and not self.colors[position].locked}
        self.colors = [color for position, color in enumerate(self.colors) if position not in doomed]
        return len(doomed)

    def clear_unlocked(self) -> int:
        before = len(self.colors)
        self.colors = [color for color in self.colors if color.locked]
        return before - len(self.colors)

    def move(self, position: int, target: int) -> int:
        """Move one color to a new position. Returns where it ended up."""
        if not 0 <= position < len(self.colors):
            return position
        target = max(0, min(len(self.colors) - 1, target))
        self.colors.insert(target, self.colors.pop(position))
        return target

    def sort(self, by: str) -> None:
        """Reorder by 'hue', 'lightness' (light to dark) or 'saturation' (vivid first)."""

        def hue(color: PaletteColor):
            angle, saturation, lightness = to_hsl(color.hex)
            return (1, lightness, 0) if saturation < 0.08 else (0, angle, lightness)  # grays last

        keys = {
            "hue": hue,
            "lightness": lambda color: -key_to_oklab(hex_to_key(color.hex))[0],
            "saturation": lambda color: -math.hypot(*key_to_oklab(hex_to_key(color.hex))[1:]),
        }
        self.colors.sort(key=keys[by])


@dataclass
class Project:
    """Everything the "My palette" tab holds: one or more named palettes."""

    palettes: list[NamedPalette] = field(default_factory=lambda: [NamedPalette()])
    active: int = 0

    @property
    def current(self) -> NamedPalette:
        self.active = max(0, min(len(self.palettes) - 1, self.active))
        return self.palettes[self.active]

    def unique_name(self, wanted: str) -> str:
        wanted = wanted.strip() or "Palette"
        taken = {palette.name for palette in self.palettes}
        if wanted not in taken:
            return wanted
        number = 2
        while f"{wanted} {number}" in taken:
            number += 1
        return f"{wanted} {number}"

    def new_palette(self, name: str = "Palette", colors: Sequence[PaletteColor] = ()) -> NamedPalette:
        palette = NamedPalette(self.unique_name(name), [PaletteColor(c.hex, c.name, c.locked) for c in colors])
        self.palettes.append(palette)
        self.active = len(self.palettes) - 1
        return palette

    def delete_current(self) -> None:
        del self.palettes[self.active]
        if not self.palettes:
            self.palettes.append(NamedPalette())
        self.active = min(self.active, len(self.palettes) - 1)

    def is_empty(self) -> bool:
        return all(not palette.colors for palette in self.palettes)

    # --- saving ------------------------------------------------------------------------

    def to_json(self) -> str:
        data = {
            "format": "colorroom-palettes",
            "version": PROJECT_VERSION,
            "active": self.active,
            "palettes": [
                {
                    "name": palette.name,
                    "colors": [
                        {"hex": color.hex, **({"name": color.name} if color.name else {}), **({"locked": True} if color.locked else {})}
                        for color in palette.colors
                    ],
                }
                for palette in self.palettes
            ],
        }
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "Project":
        """Read a saved project. Raises ValueError with a plain message if it is not one."""
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise ValueError("This is not a Colorroom palette file.") from None
        if not isinstance(data, dict) or data.get("format") != "colorroom-palettes" or not isinstance(data.get("palettes"), list):
            raise ValueError("This is not a Colorroom palette file.")
        palettes: list[NamedPalette] = []
        for entry in data["palettes"][:200]:
            if not isinstance(entry, dict):
                continue
            palette = NamedPalette(str(entry.get("name") or "Palette")[:80])
            for item in (entry.get("colors") or [])[:MAX_COLORS]:
                try:
                    color = normalize_hex(str(item.get("hex", "")) if isinstance(item, dict) else str(item))
                except ValueError:
                    continue  # skip anything that is not a HEX color
                if isinstance(item, dict):
                    palette.colors.append(PaletteColor(color, str(item.get("name") or "")[:60], bool(item.get("locked"))))
                else:
                    palette.colors.append(PaletteColor(color))
            palettes.append(palette)
        if not palettes:
            palettes = [NamedPalette()]
        active = data.get("active", 0)
        return cls(palettes, active if isinstance(active, int) and 0 <= active < len(palettes) else 0)

    def save(self, path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8", newline="\n")

    @classmethod
    def load(cls, path) -> "Project":
        target = Path(path)
        if target.stat().st_size > 2_000_000:
            raise ValueError("This file is too large to be a Colorroom palette file.")
        return cls.from_json(target.read_text(encoding="utf-8", errors="replace"))


_HEX_IN_TEXT = re.compile(r"(?<![0-9A-Za-z])#?([0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})(?![0-9A-Za-z])")


def parse_hex_list(text: str) -> list[str]:
    """Find HEX colors in free text: '#B47A3C, 1E2A3B  #fff' -> three colors, in order."""
    found: list[str] = []
    for match in _HEX_IN_TEXT.finditer(text):
        token = match.group(0)
        if not token.startswith("#") and token.isdigit() and len(token) == 3:
            continue  # a bare number such as "100" is not meant as a color
        color = normalize_hex(match.group(1))
        if color not in found:
            found.append(color)
    return found


# --------------------------------------------------------------------------------------
# Color values
# --------------------------------------------------------------------------------------


def rgb_text(color: str) -> str:
    return "{}, {}, {}".format(*key_to_rgb(hex_to_key(color)))


def hsl_text(color: str) -> str:
    hue, saturation, lightness = to_hsl(color)
    if not saturation:
        hue = 0
    return f"{round(hue) % 360}°, {round(saturation * 100)}%, {round(lightness * 100)}%"


def to_cmyk(color: str) -> tuple[int, int, int, int]:
    """Approximate CMYK percentages (cyan, magenta, yellow, black).

    This is the plain arithmetic conversion. Real print values depend on the press and
    paper profile, so treat these as a starting point and check a proof.
    """
    red, green, blue = (channel / 255 for channel in key_to_rgb(hex_to_key(color)))
    black = 1 - max(red, green, blue)
    if black >= 1:
        return 0, 0, 0, 100
    scale = 1 - black
    return (
        round((1 - red - black) / scale * 100),
        round((1 - green - black) / scale * 100),
        round((1 - blue - black) / scale * 100),
        round(black * 100),
    )


def cmyk_text(color: str) -> str:
    return "{}, {}, {}, {}".format(*to_cmyk(color))


# --------------------------------------------------------------------------------------
# Contrast (WCAG 2)
# --------------------------------------------------------------------------------------

AAA, AA, AA_LARGE, FAIL = "AAA", "AA", "AA Large", "Fail"


def contrast_grade(ratio: float) -> str:
    """WCAG 2 level for text: AAA from 7, AA from 4.5, large text only from 3."""
    if ratio >= 7:
        return AAA
    if ratio >= 4.5:
        return AA
    if ratio >= 3:
        return AA_LARGE
    return FAIL


def contrast_table(colors: Sequence[str]) -> list[list[float]]:
    """Contrast ratio of every pair: table[row][column], text color `column` on `row`."""
    return [[contrast_ratio(background, text) for text in colors] for background in colors]


def readable_pairs(colors: Sequence[str], minimum: float = 4.5) -> list[tuple[str, str, float]]:
    """Each pair of palette colors that passes `minimum`, best first, once per pair."""
    pairs = [
        (colors[first], colors[second], contrast_ratio(colors[first], colors[second]))
        for first in range(len(colors))
        for second in range(first + 1, len(colors))
    ]
    return sorted((pair for pair in pairs if pair[2] >= minimum), key=lambda pair: -pair[2])


# --------------------------------------------------------------------------------------
# Color-blindness simulation
# --------------------------------------------------------------------------------------

#: Simulation matrices for complete dichromacy (Machado, Oliveira and Fernandes, 2009),
#: applied to linear RGB.
_CVD = {
    "protanopia": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216), (-0.003882, -0.048116, 1.051998)),
    "deuteranopia": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413), (-0.011820, 0.042940, 0.968881)),
    "tritanopia": ((1.255528, -0.076749, -0.178779), (-0.078411, 0.930809, 0.147602), (0.004733, 0.691367, 0.303900)),
}
#: (key, name, plain description)
VISION_TYPES = (
    ("normal", "Typical vision", "How most people see it"),
    ("protanopia", "Protanopia", "No red cones: reds look dark and muddy"),
    ("deuteranopia", "Deuteranopia", "No green cones: the most common type"),
    ("tritanopia", "Tritanopia", "No blue cones: rare; blues and yellows shift"),
    ("achromatopsia", "Grayscale", "No color at all: lightness only"),
)
_LINEAR_TO_SRGB = np.array(
    [
        round((12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055) * 255)
        for v in (step / 4095 for step in range(4096))
    ],
    dtype=np.uint8,
)


def simulate_rgb(rgb: np.ndarray, kind: str) -> np.ndarray:
    """Simulate a type of color vision on an array of 8-bit sRGB values (..., 3)."""
    values = np.asarray(rgb, dtype=np.uint8)
    if kind == "normal":
        return values.copy()
    linear = SRGB_TO_LINEAR[values]
    if kind == "achromatopsia":
        gray = linear[..., 0] * 0.2126 + linear[..., 1] * 0.7152 + linear[..., 2] * 0.0722
        out = np.repeat(gray[..., None], 3, axis=-1)
    else:
        out = linear @ np.array(_CVD[kind], dtype=np.float64).T
    steps = np.clip(np.rint(out * 4095), 0, 4095).astype(np.int32)
    return _LINEAR_TO_SRGB[steps]


def simulate(color: str, kind: str) -> str:
    """One HEX color as seen with a type of color vision."""
    red, green, blue = simulate_rgb(np.array(key_to_rgb(hex_to_key(color)), dtype=np.uint8), kind)
    return f"#{int(red):02X}{int(green):02X}{int(blue):02X}"


def confusable_pairs(colors: Sequence[str], kind: str, close: float = 0.055, apart: float = 0.09) -> list[tuple[str, str]]:
    """Pairs that are clearly different normally but nearly the same with this vision."""
    seen = [simulate(color, kind) for color in colors]
    pairs = []
    for first in range(len(colors)):
        for second in range(first + 1, len(colors)):
            if oklab_distance(colors[first], colors[second]) >= apart and oklab_distance(seen[first], seen[second]) < close:
                pairs.append((colors[first], colors[second]))
    return pairs


# --------------------------------------------------------------------------------------
# Tints and shades
# --------------------------------------------------------------------------------------

RAMP_STEPS = (50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950)
_RAMP_LIGHTNESS = (0.975, 0.94, 0.885, 0.815, 0.735, 0.65, 0.56, 0.47, 0.385, 0.305, 0.235)


def tint_ramp(color: str) -> list[tuple[int, str]]:
    """An 11-step scale (50 lightest to 950 darkest) in the hue of `color`.

    Lightness is spaced evenly for the eye (Oklab). The step nearest the color's own
    lightness is the color itself, so the scale always contains it exactly.
    """
    color = normalize_hex(color)
    lightness, a, b = key_to_oklab(hex_to_key(color))
    own = min(range(len(RAMP_STEPS)), key=lambda step: abs(_RAMP_LIGHTNESS[step] - lightness))
    chroma = math.hypot(a, b)
    ramp = []
    for position, (step, target) in enumerate(zip(RAMP_STEPS, _RAMP_LIGHTNESS)):
        if position == own:
            ramp.append((step, color))
            continue
        # Very light and very dark steps cannot hold much color: ease it out toward the ends.
        room = min(1.0, (1 - target) / 0.2, target / 0.25)
        scale = min(1.0, room) if chroma else 0.0
        ramp.append((step, key_to_hex(oklab_to_key(target, a * scale, b * scale))))
    return ramp


def ramp_css(name: str, ramp: Sequence[tuple[int, str]]) -> str:
    lines = "\n".join(f"  --{name}-{step}: {color};" for step, color in ramp)
    return f":root {{\n{lines}\n}}\n"


def ramp_tailwind(name: str, ramp: Sequence[tuple[int, str]]) -> str:
    lines = "\n".join(f"  {step}: '{color}'," for step, color in ramp)
    return f"{name}: {{\n{lines}\n}},\n"


def slug(text: str, fallback: str = "color") -> str:
    """A name safe for CSS variables and tokens: 'Deep Plum!' -> 'deep-plum'."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned or fallback


# --------------------------------------------------------------------------------------
# Getting colors into design apps
# --------------------------------------------------------------------------------------


def _labels(colors: Sequence[str], names: Sequence[str]) -> list[str]:
    return [(names[position] if position < len(names) and names[position] else "") for position in range(len(colors))]


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def swatches_svg(colors: Sequence[str], names: Sequence[str] = (), title: str = "Colorroom palette") -> str:
    """A row of labelled color squares as SVG.

    Pasted into Figma or Illustrator this becomes real vector shapes, each filled with one
    palette color and named after its HEX code, ready to eyedrop or turn into styles.
    """
    colors = [normalize_hex(color) for color in colors]
    labels = _labels(colors, names)
    side, gap, margin, text_height = 120, 16, 24, 54 if any(labels) else 34
    width = margin * 2 + len(colors) * side + max(0, len(colors) - 1) * gap
    height = margin * 2 + side + text_height
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f"<title>{_xml(title)}</title>",
    ]
    for position, color in enumerate(colors):
        x = margin + position * (side + gap)
        identity = _xml(labels[position] or color)
        parts.append(f'<g id="{_xml(slug(labels[position] or color.lstrip("#"), "color"))}-{position + 1}">')
        parts.append(f'<rect id="{identity}" x="{x}" y="{margin}" width="{side}" height="{side}" rx="12" fill="{color}"/>')
        parts.append(
            f'<text x="{x}" y="{margin + side + 22}" font-family="Consolas, Menlo, monospace" font-size="15" fill="#211D26">{color}</text>'
        )
        if labels[position]:
            parts.append(
                f'<text x="{x}" y="{margin + side + 42}" font-family="Segoe UI, Helvetica, Arial, sans-serif" '
                f'font-size="13" fill="#635C69">{_xml(labels[position])}</text>'
            )
        parts.append("</g>")
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def design_tokens(colors: Sequence[str], names: Sequence[str] = (), group: str = "palette") -> str:
    """The palette as a design-tokens JSON file (the W3C community-group format).

    Token plugins for Figma read this and create color variables or styles from it.
    """
    colors = [normalize_hex(color) for color in colors]
    labels = _labels(colors, names)
    tokens: dict[str, dict] = {}
    for position, color in enumerate(colors):
        key = slug(labels[position], f"color-{position + 1}") if labels[position] else f"color-{position + 1}"
        while key in tokens:
            key += "-2"
        tokens[key] = {"$type": "color", "$value": color}
    return json.dumps({slug(group, "palette"): tokens}, indent=2, ensure_ascii=False) + "\n"


def values_table(colors: Sequence[str], names: Sequence[str] = ()) -> str:
    """Tab-separated values that paste into a spreadsheet or a document table."""
    labels = _labels(colors, names)
    rows = ["Name\tHEX\tRGB\tHSL\tCMYK (approx.)"]
    for position, color in enumerate(colors):
        color = normalize_hex(color)
        rows.append(f"{labels[position]}\t{color}\t{rgb_text(color)}\t{hsl_text(color)}\t{cmyk_text(color)}")
    return "\n".join(rows) + "\n"


# --------------------------------------------------------------------------------------
# Roles for the mockup preview
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Roles:
    background: str
    surface: str
    text: str
    primary: str
    secondary: str
    accent: str
    on_primary: str  # readable text on the primary color

    def as_dict(self) -> dict[str, str]:
        return {
            "Background": self.background,
            "Surface": self.surface,
            "Text": self.text,
            "Primary": self.primary,
            "Secondary": self.secondary,
            "Accent": self.accent,
        }


def assign_roles(colors: Sequence[str], variant: int = 0) -> Optional[Roles]:
    """Give palette colors jobs in a layout: a background, readable text and accents.

    `variant` cycles through alternatives: even numbers use the lightest color as the
    background, odd numbers the darkest; higher numbers rotate which colors are accents.
    """
    colors = list(dict.fromkeys(normalize_hex(color) for color in colors))
    if not colors:
        return None
    by_light = sorted(colors, key=lambda color: -key_to_oklab(hex_to_key(color))[0])
    background = by_light[0] if variant % 2 == 0 else by_light[-1]
    others = [color for color in colors if color != background]
    text = max(others, key=lambda color: contrast_ratio(color, background), default=readable_text_color(background))
    if contrast_ratio(text, background) < 4.5:
        text = readable_text_color(background)  # nothing in the palette is readable enough
    vivid = sorted(
        (color for color in others if color != text),
        key=lambda color: -math.hypot(*key_to_oklab(hex_to_key(color))[1:]),
    )
    if not vivid:
        vivid = [text]
    shift = (variant // 2) % len(vivid)
    vivid = vivid[shift:] + vivid[:shift]
    primary = vivid[0]
    secondary = vivid[1] if len(vivid) > 1 else primary
    accent = vivid[2] if len(vivid) > 2 else secondary
    surface = by_light[1] if variant % 2 == 0 and len(by_light) > 1 else by_light[-2] if len(by_light) > 1 else background
    if surface in (text, primary) or contrast_ratio(surface, text) < 3:
        surface = background
    return Roles(background, surface, text, primary, secondary, accent, readable_text_color(primary))


# --------------------------------------------------------------------------------------
# Gradient map
# --------------------------------------------------------------------------------------


def gradient_stops(colors: Sequence[str]) -> list[str]:
    """The palette ordered from darkest to lightest: the stops of its gradient map."""
    unique = list(dict.fromkeys(normalize_hex(color) for color in colors))
    if len(unique) == 1:
        unique = ["#000000", unique[0], "#FFFFFF"]  # one color: run it from black to white
    return sorted(unique, key=lambda color: key_to_oklab(hex_to_key(color))[0])


def gradient_lut(colors: Sequence[str]) -> np.ndarray:
    """256 colors running smoothly through the palette, dark to light. Shape (256, 3)."""
    stops = gradient_stops(colors)
    table = np.zeros((256, 3), dtype=np.uint8)
    if not stops:
        return table
    for level in range(256):
        position = level / 255 * (len(stops) - 1)
        lower = min(len(stops) - 2, int(position)) if len(stops) > 1 else 0
        color = mix_oklab(stops[lower], stops[lower + 1], position - lower) if len(stops) > 1 else stops[0]
        table[level] = key_to_rgb(hex_to_key(color))
    return table


def gradient_map(rgb: np.ndarray, colors: Sequence[str]) -> np.ndarray:
    """Recolor an image with the palette: each pixel's brightness picks a gradient color.

    This is what a "gradient map" adjustment does in Photoshop: shadows take the darkest
    palette color, highlights the lightest, and everything between runs through the rest.
    """
    values = np.asarray(rgb, dtype=np.uint16)
    brightness = (values[..., 0] * 54 + values[..., 1] * 183 + values[..., 2] * 19) >> 8
    return gradient_lut(colors)[brightness]


# --------------------------------------------------------------------------------------
# Roles chosen by hand
# --------------------------------------------------------------------------------------

ROLE_NAMES = ("Background", "Surface", "Text", "Primary", "Secondary", "Accent")
_ROLE_FIELDS = {name: name.lower() for name in ROLE_NAMES}


#: Roles by position when "roles follow the order": the first color is the Primary, and so on.
LADDER = ("Primary", "Secondary", "Accent", "Background", "Surface", "Text")


def ladder_overrides(colors: Sequence[str]) -> dict[str, str]:
    """The role each color gets from its place in the list: {"Primary": first color, ...}."""
    unique = list(dict.fromkeys(normalize_hex(color) for color in colors))
    return {role: color for role, color in zip(LADDER, unique)}


def roles_for(colors: Sequence[str], by_order: bool, overrides: Optional[dict] = None, variant: int = 0) -> Optional[Roles]:
    """The roles of a palette: from its order, or chosen automatically with hand-set changes."""
    auto = assign_roles(colors, variant)
    return with_overrides(auto, ladder_overrides(colors) if by_order else (overrides or {}))


def role_of_each(colors: Sequence[str], roles: Optional[Roles]) -> dict[str, list[str]]:
    """For every palette color, the roles it holds right now: {"#102A54": ["Primary"], ...}."""
    held: dict[str, list[str]] = {}
    if roles is not None:
        for role, color in roles.as_dict().items():
            held.setdefault(color, []).append(role)
    return {color: held.get(color, []) for color in colors}


def give_role(colors: Sequence[str], overrides: dict, role: str, color: str, variant: int = 0) -> dict:
    """Hand a role to a color and return the new hand-set roles.

    If the color already had a hand-set role, the previous holder of `role` takes it over,
    so the two swap and nothing is left without a color. `role` may be "" to take every
    hand-set role away from the color.
    """
    color = normalize_hex(color)
    result = dict(overrides)
    if role == "":
        return {name: value for name, value in result.items() if value != color}
    if role not in ROLE_NAMES:
        return result
    current = roles_for(colors, False, result, variant)
    previous = current.as_dict()[role] if current is not None else None
    for name, value in list(result.items()):
        if value == color and name != role:
            if previous and previous != color:
                result[name] = previous  # swap
            else:
                del result[name]
    result[role] = color
    return result


def with_overrides(roles: Optional[Roles], overrides: dict) -> Optional[Roles]:
    """Roles with some jobs set by hand: {"Primary": "#B47A3C", ...}. Bad entries are ignored."""
    if roles is None:
        return None
    values = {field: getattr(roles, field) for field in _ROLE_FIELDS.values()}
    for name, color in (overrides or {}).items():
        if name in _ROLE_FIELDS and color:
            try:
                values[_ROLE_FIELDS[name]] = normalize_hex(color)
            except ValueError:
                pass
    return Roles(on_primary=readable_text_color(values["primary"]), **values)


# --------------------------------------------------------------------------------------
# Recoloring an uploaded image with the palette
# --------------------------------------------------------------------------------------


def _rgb_to_oklab(rgb: np.ndarray) -> np.ndarray:
    """8-bit sRGB (n, 3) -> Oklab (n, 3)."""
    linear = SRGB_TO_LINEAR[rgb]
    r, g, b = linear[:, 0], linear[:, 1], linear[:, 2]
    l = np.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m = np.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s = np.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return np.stack(
        [
            0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
        ],
        axis=1,
    )


def _oklab_to_rgb(lab: np.ndarray) -> np.ndarray:
    """Oklab (n, 3) -> 8-bit sRGB (n, 3); colors outside the screen's range are clipped."""
    lightness, a, b = lab[:, 0], lab[:, 1], lab[:, 2]
    l = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    linear = np.stack(
        [
            4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
        ],
        axis=1,
    )
    return _LINEAR_TO_SRGB[np.clip(np.rint(linear * 4095), 0, 4095).astype(np.int32)]


def _hex_lab(color: str) -> np.ndarray:
    return np.array(key_to_oklab(hex_to_key(color)), dtype=np.float64)


@dataclass
class Layers:
    """An uploaded image split into its main colors, ready to be recolored."""

    rgba: np.ndarray  # (height, width, 4) the picture as loaded
    colors: list[str]  # its main colors, biggest area first
    shares: list[float]  # share of the visible picture each one covers (0..1)
    lab: np.ndarray  # (n, 3) Oklab of every pixel
    weights: np.ndarray  # (n, k) how strongly each pixel belongs to each main color
    excluded: Optional[np.ndarray] = None  # (n,) True for pixels that are left alone (photos)
    blends: list = field(default_factory=list)  # main colors that are only in-between shades (Blend)
    flat: list = field(default_factory=list)  # (k,) True for main colors that are flat fills


# --------------------------------------------------------------------------------------
# In-between shades (made by Opus 5.5)
# --------------------------------------------------------------------------------------
#
# A pencil stroke fading into the paper, a soft shadow or anti-aliased text makes colors
# that lie on the straight Oklab line between two other main colors. Giving each of them a
# role of its own turns smooth shading into flecks of unrelated colors, so they follow
# the two colors at their ends instead, and pixels on such a ramp move like a gradient map.
#
# Sources and measurements:
# - Oklab: Bjorn Ottosson, "A perceptual color space for image processing" (2020),
#   https://bottosson.github.io/posts/oklab/. Distances are Euclidean in Oklab, the
#   "deltaEOK" of CSS Color Module Level 4 (section 13.2), where 0.02 is about one
#   just-noticeable difference.
# - RAMP_TOLERANCE: an ink blended over paper (gamma-encoded or linear sRGB; white, black
#   and cream paper; yellow, blue, red, green, gray and purple ink) never leaves the
#   straight Oklab line between its two ends by more than 0.028.
# - FLAT_RADIUS is below one JND: a flat fill has most of its pixels this close to its
#   center, while a shading step spreads them over its whole slice of the ramp.

BLEND = "Blend"  # the job of an in-between shade: follow the colors at its two ends
FLAT_RADIUS = 0.012
FLAT_SHARE = 0.5
RAMP_TOLERANCE = 0.03
MIN_RAMP = 0.06  # two colors closer than this are not the ends of a ramp


@dataclass(frozen=True)
class Blend:
    """Main color `index` is an in-between shade of `low` and `high`, at `t` (0 = low, 1 = high)."""

    index: int
    low: int
    high: int
    t: float


def _between(i: int, pool: Sequence[int], centers: np.ndarray) -> Optional[tuple[int, int, float]]:
    """The two colors of `pool` that color `i` lies between on a straight Oklab line, or None."""
    best = None
    pool = [p for p in pool if p != i]
    for first in range(len(pool)):
        for second in range(first + 1, len(pool)):
            low, high = pool[first], pool[second]
            line = centers[high] - centers[low]
            length2 = float(line @ line)
            if length2 < MIN_RAMP**2:
                continue
            t = float((centers[i] - centers[low]) @ line) / length2
            if not 0.03 < t < 0.97:
                continue
            off = float(np.linalg.norm(centers[i] - (centers[low] + t * line)))
            if off > RAMP_TOLERANCE:
                continue
            score = (round(off / RAMP_TOLERANCE, 1), -length2)  # the closest fit, then the longest ramp
            if best is None or score < best[0]:
                best = (score, low, high, t)
    return None if best is None else best[1:]


def find_blends(sample: np.ndarray, centers: np.ndarray) -> tuple[list[Blend], list[bool]]:
    """Which main colors are only in-between shades of two others, and which are flat fills.

    `sample` is Oklab pixels of the picture (visible, not left out). A main color is an
    in-between shade when its pixels are spread out (not a flat fill) and it lies on the
    straight line between two "end" colors. Ends are the flat fills and every color that
    lies between no two others; a shade's two ends are always ends, never other shades.
    """
    count = len(centers)
    if count == 0 or not len(sample):
        return [], [True] * count
    distance = np.linalg.norm(sample[:, None, :] - centers[None, :, :], axis=2)
    nearest = distance.argmin(axis=1)
    flat = []
    for position in range(count):
        own = distance[nearest == position, position]
        flat.append(True if not len(own) else bool((own < FLAT_RADIUS).mean() >= FLAT_SHARE))
    everyone = list(range(count))
    ends = {i for i in everyone if flat[i] or _between(i, everyone, centers) is None}
    changed = True
    while changed:
        changed = False
        for i in everyone:
            if i not in ends and _between(i, sorted(ends), centers) is None:
                ends.add(i)
                changed = True
    blends = []
    for i in everyone:
        if i not in ends:
            low, high, t = _between(i, sorted(ends), centers)
            blends.append(Blend(i, low, high, t))
    return blends, flat


def ramp_segments(blends: Sequence[Blend]) -> list[tuple[int, int]]:
    """Each ramp as consecutive pairs of main colors: low end, its shades by t, high end."""
    chains: dict[tuple[int, int], list[Blend]] = {}
    for blend in blends:
        chains.setdefault((blend.low, blend.high), []).append(blend)
    segments = []
    for (low, high), members in chains.items():
        order = [low] + [blend.index for blend in sorted(members, key=lambda b: b.t)] + [high]
        segments += list(zip(order[:-1], order[1:]))
    return segments


def membership(
    lab: np.ndarray,
    centers: np.ndarray,
    blends: Sequence[Blend] = (),
    flat: Sequence[bool] = (),
    distance: Optional[np.ndarray] = None,
) -> np.ndarray:
    """How strongly each pixel belongs to each main color, (n, k), rows summing to 1.

    Away from ramps: soft weights 1/(d^2 + 1e-5)^2, so anti-aliased edges stay smooth and
    a flat fill maps exactly. On a ramp: a pixel at fraction u between two neighbouring
    ramp colors belongs (1 - u) to one and u to the other, like a gradient map, so
    shading moves smoothly and keeps its order. The switch fades in between 0.02 and 0.06
    of distance from the ramp, and pixels within 0.008 of a flat fill keep the soft
    weights.
    """
    if distance is None:
        distance = np.linalg.norm(lab[:, None, :] - centers[None, :, :], axis=2)
    weights = 1.0 / (distance**2 + 1e-5) ** 2
    weights /= weights.sum(axis=1, keepdims=True)
    segments = ramp_segments(blends)
    if not segments or not len(lab):
        return weights
    best_r = np.full(len(lab), np.inf)
    best_u = np.zeros(len(lab))
    best_n = np.zeros(len(lab), dtype=np.int32)
    for number, (p, q) in enumerate(segments):  # one segment at a time: little memory
        line = centers[q] - centers[p]
        u = np.clip((lab - centers[p]) @ line / float(line @ line), 0.0, 1.0)
        r = np.linalg.norm(lab - centers[p] - u[:, None] * line, axis=1)
        better = r < best_r
        best_r[better], best_u[better], best_n[better] = r[better], u[better], number
    near = np.clip(1.0 - (best_r - 0.02) / 0.04, 0.0, 1.0)
    flat_columns = [i for i, is_flat in enumerate(flat) if is_flat]
    if flat_columns:
        near *= np.clip((distance[:, flat_columns].min(axis=1) - 0.008) / 0.012, 0.0, 1.0)
    rows = np.nonzero(near > 0)[0]
    if len(rows):
        starts = np.array([p for p, _ in segments])[best_n[rows]]
        ends = np.array([q for _, q in segments])[best_n[rows]]
        ramp = np.zeros((len(rows), len(centers)))
        index = np.arange(len(rows))
        ramp[index, starts] = 1.0 - best_u[rows]
        ramp[index, ends] = best_u[rows]
        share = near[rows, None]
        weights[rows] = share * ramp + (1.0 - share) * weights[rows]
    return weights


def job_targets(colors: Sequence[str], jobs: Sequence[str], table: dict, blends: Sequence[Blend] = ()) -> list[Optional[str]]:
    """What each main color becomes: its role's color, None to keep it, and for an
    in-between shade (job BLEND) the same mix of its two ends' moves.

    move(shade) = (1 - t) * move(low end) + t * move(high end), in Oklab, so a shade keeps
    its place on the ramp. If neither end changes, the shade is kept as well.
    """
    targets = [table.get(job) if job and job != BLEND else None for job in jobs]
    result = list(targets)
    for blend in blends:
        if blend.index >= len(jobs) or jobs[blend.index] != BLEND or max(blend.low, blend.high) >= len(colors):
            continue
        low, high = targets[blend.low], targets[blend.high]
        if not low and not high:
            continue
        move_low = _hex_lab(low) - _hex_lab(colors[blend.low]) if low else np.zeros(3)
        move_high = _hex_lab(high) - _hex_lab(colors[blend.high]) if high else np.zeros(3)
        new = _hex_lab(colors[blend.index]) + (1.0 - blend.t) * move_low + blend.t * move_high
        result[blend.index] = key_to_hex(oklab_to_key(*new))
    return result


def find_layers(rgba: np.ndarray, count: int = 6, exclude: Optional[np.ndarray] = None) -> Layers:
    """Find the main colors of an image and how much each pixel belongs to each of them.

    Soft membership (rather than "this pixel is color 3") keeps anti-aliased edges and
    soft shadows smooth when the colors are swapped.

    `exclude` is a (height, width) mask of pixels to leave out, such as photos: they do not
    count toward the main colors and `recolor` never changes them.
    """
    rgba = np.ascontiguousarray(rgba, dtype=np.uint8)
    pixels = rgba.reshape(-1, 4)
    visible = pixels[:, 3] > 0
    left_out = None
    if exclude is not None and exclude.any() and not exclude.all():
        left_out = np.asarray(exclude, dtype=bool).reshape(-1)
        visible = visible & ~left_out
    lab = _rgb_to_oklab(pixels[:, :3])
    seen = lab[visible] if visible.any() else lab
    # Start from the most common coarse colors that are clearly different from each other...
    sample = seen[:: max(1, len(seen) // 40_000)]
    coarse = np.round(sample / 0.04).astype(np.int32)
    bins, inverse, counts = np.unique(coarse, axis=0, return_inverse=True, return_counts=True)
    inverse = inverse.ravel()
    centers_all = np.stack(
        [np.bincount(inverse, weights=sample[:, channel], minlength=len(bins)) for channel in range(3)], axis=1
    ) / counts[:, None]
    chosen = [int(np.argmax(counts))]
    while len(chosen) < min(count, len(bins)):
        distance = np.min(np.linalg.norm(centers_all[:, None, :] - centers_all[chosen][None, :, :], axis=2), axis=1)
        score = distance * np.sqrt(counts)
        score[distance < 0.07] = 0  # too close to a color already chosen
        if score.max() <= 0:
            break
        chosen.append(int(np.argmax(score)))
    centers = centers_all[chosen].copy()
    # ...then let each center settle on the average of the pixels nearest to it.
    for _ in range(8):
        nearest = np.argmin(np.linalg.norm(sample[:, None, :] - centers[None, :, :], axis=2), axis=1)
        for position in range(len(centers)):
            members = sample[nearest == position]
            if len(members):
                centers[position] = members.mean(axis=0)
    # Measure from the colors as saved (8-bit HEX), exactly as file_export.recolor_pixels
    # does, so the exported file matches the preview pixel for pixel.
    centers = np.array([_hex_lab(key_to_hex(oklab_to_key(*center))) for center in centers])
    distance = np.linalg.norm(lab[:, None, :] - centers[None, :, :], axis=2)
    nearest = np.argmin(distance, axis=1)
    area = np.bincount(nearest[visible] if visible.any() else nearest, minlength=len(centers)).astype(np.float64)
    order = np.argsort(-area)
    centers, distance, area = centers[order], distance[:, order], area[order]
    keep = area > 0
    centers, distance, area = centers[keep], distance[:, keep], area[keep]
    colors = [key_to_hex(oklab_to_key(*center)) for center in centers]
    blends, flat = find_blends(sample, centers)
    weights = membership(lab, centers, blends, flat, distance)
    shares = [float(value) for value in area / max(1.0, area.sum())]
    return Layers(rgba, colors, shares, lab, weights.astype(np.float32), left_out, blends, flat)


def layers_from_colors(rgba: np.ndarray, colors: Sequence[str], exclude: Optional[np.ndarray] = None) -> Layers:
    """Split an image by colors that are already known exactly.

    This is for documents sent by a plugin: the manifest lists the true fill, stroke and
    text colors, so there is nothing to guess. Every listed color is kept, even one that
    covers almost nothing, because each is a real color of a real layer.
    """
    rgba = np.ascontiguousarray(rgba, dtype=np.uint8)
    pixels = rgba.reshape(-1, 4)
    visible = pixels[:, 3] > 0
    left_out = None
    if exclude is not None and exclude.any() and not exclude.all():
        left_out = np.asarray(exclude, dtype=bool).reshape(-1)
        visible = visible & ~left_out
    colors = list(dict.fromkeys(normalize_hex(color) for color in colors))
    lab = _rgb_to_oklab(pixels[:, :3])
    if not colors:
        return Layers(rgba, [], [], lab, np.zeros((len(lab), 0), dtype=np.float32), left_out)
    centers = np.array([_hex_lab(color) for color in colors])
    distance = np.linalg.norm(lab[:, None, :] - centers[None, :, :], axis=2)
    nearest = np.argmin(distance, axis=1)
    area = np.bincount(nearest[visible] if visible.any() else nearest, minlength=len(centers)).astype(np.float64)
    weights = 1.0 / (distance**2 + 1e-5) ** 2
    # A pixel far from every listed color (a photo, a gradient, an effect) belongs to none
    # of them: fade its membership out so it is not dragged along.
    weights *= np.clip(1.0 - (distance.min(axis=1, keepdims=True) - 0.06) / 0.10, 0.0, 1.0) / weights.sum(axis=1, keepdims=True)
    shares = [float(value) for value in area / max(1.0, area.sum())]
    return Layers(rgba, colors, shares, lab, weights.astype(np.float32), left_out)


def recolor(layers: Layers, targets: Sequence[Optional[str]]) -> np.ndarray:
    """Swap the image's main colors for `targets` (None or "" keeps a color). Returns RGBA.

    Each pixel moves by the same step its main color moves, so shading, texture and
    smooth edges survive: a darker patch of the old blue becomes a darker patch of the
    new color.
    """
    steps = np.zeros((len(layers.colors), 3), dtype=np.float64)
    for position, target in enumerate(list(targets)[: len(layers.colors)]):
        if target:
            steps[position] = _hex_lab(target) - _hex_lab(layers.colors[position])
    out = layers.rgba.copy()
    if steps.any():
        moved = layers.lab + layers.weights.astype(np.float64) @ steps
        changed = _oklab_to_rgb(moved)
        if layers.excluded is None:
            out.reshape(-1, 4)[:, :3] = changed
        else:
            keep = ~layers.excluded
            out.reshape(-1, 4)[keep, :3] = changed[keep]
    return out


def suggest_targets(layers: Layers, roles: Optional[Roles]) -> list[str]:
    """A first guess at which role each main color of the image should take.

    The biggest area becomes the background, the color that contrasts most with it the
    text, the most colorful ones the primary, secondary and accent, the rest surface.
    In-between shades (a pencil stroke fading into the paper) get BLEND: they follow the
    two colors at their ends, and only the ends are given roles.
    """
    count = len(layers.colors)
    if roles is None or not count:
        return [""] * count
    blended = {blend.index for blend in getattr(layers, "blends", ()) if blend.index < count}
    ends = [i for i in range(count) if i not in blended]
    jobs = [BLEND if i in blended else "" for i in range(count)]
    jobs[ends[0]] = "Background"
    rest = ends[1:]
    if rest:
        text = max(rest, key=lambda i: contrast_ratio(layers.colors[i], layers.colors[ends[0]]))
        jobs[text] = "Text"
        rest.remove(text)
    vivid = sorted(rest, key=lambda i: -math.hypot(*key_to_oklab(hex_to_key(layers.colors[i]))[1:]))
    for position, job in zip(vivid, ("Primary", "Secondary", "Accent")):
        jobs[position] = job
    return [job or "Surface" for job in jobs]


# --------------------------------------------------------------------------------------
# Leaving photos out
# --------------------------------------------------------------------------------------

Box = tuple  # (left, top, right, bottom) as fractions 0..1 of the picture


def busy_grid(rgba: np.ndarray, block: int = 12) -> np.ndarray:
    """Which small squares of a picture look like a photograph.

    Flat artwork, text and smooth gradients use one or two colors in a small square. A
    photograph uses many. A square counts as "busy" when its two most common colors cover
    less than 60% of it. Returns a grid of booleans, one per `block` x `block` square.
    """
    height, width = rgba.shape[:2]
    rows, columns = max(1, height // block), max(1, width // block)
    if height < block or width < block:
        return np.zeros((rows, columns), dtype=bool)  # too small to judge: treat as not a photo
    coarse = (
        (rgba[: rows * block, : columns * block, 0].astype(np.uint16) >> 4 << 8)
        | (rgba[: rows * block, : columns * block, 1].astype(np.uint16) >> 4 << 4)
        | (rgba[: rows * block, : columns * block, 2].astype(np.uint16) >> 4)
    )
    if coarse.size == 0:
        return np.zeros((rows, columns), dtype=bool)
    squares = coarse.reshape(rows, block, columns, block).transpose(0, 2, 1, 3).reshape(rows, columns, block * block)
    ordered = np.sort(squares, axis=2)
    # Length of each run of equal values = how often each color appears in the square.
    change = np.ones(ordered.shape, dtype=bool)
    change[:, :, 1:] = ordered[:, :, 1:] != ordered[:, :, :-1]
    run = np.cumsum(change, axis=2) - 1  # index of the color each pixel belongs to
    grid = np.zeros((rows, columns), dtype=bool)
    total = block * block
    for row in range(rows):
        counts = np.zeros((columns, total), dtype=np.int32)
        np.add.at(counts, (np.repeat(np.arange(columns), total), run[row].reshape(-1)), 1)
        ranked = np.sort(counts, axis=1)
        top_two = ranked[:, -2:].sum(axis=1)
        # In text the paper color still fills a third or more of a square; in a photo no
        # single color fills much (about 8% on a real screenshot).
        grid[row] = (top_two < total * 0.6) & (ranked[:, -1] < total * 0.25)
    if not grid.any():
        return grid
    # Small text is busy too (its soft edges make many in-between shades), but all of its
    # pixels sit on the line between two colors: the ink and the paper. A photograph's
    # pixels do not. Keep only the squares whose pixels stray from such a line.
    pixels = (
        rgba[: rows * block, : columns * block, :3]
        .reshape(rows, block, columns, block, 3)
        .transpose(0, 2, 1, 3, 4)
        .reshape(rows, columns, total, 3)
        .astype(np.float32)
    )
    brightness = pixels @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    darkest = np.take_along_axis(pixels, brightness.argmin(axis=2)[:, :, None, None], axis=2)
    lightest = np.take_along_axis(pixels, brightness.argmax(axis=2)[:, :, None, None], axis=2)
    line = lightest - darkest
    length = np.maximum((line * line).sum(axis=3, keepdims=True), 1e-6)
    offset = pixels - darkest
    along = np.clip((offset * line).sum(axis=3, keepdims=True) / length, 0.0, 1.0)
    stray = np.linalg.norm(offset - along * line, axis=3).mean(axis=2)
    # Measured on a real website screenshot: photo squares stray about 16 on average, small
    # screen text (whose sub-pixel smoothing adds faint color fringes) about 8.
    return grid & (stray > 11.0)


def _components(grid: np.ndarray) -> list[list[tuple[int, int]]]:
    """Groups of touching True squares (touching sides or corners)."""
    rows, columns = grid.shape
    seen = np.zeros_like(grid, dtype=bool)
    groups = []
    for row in range(rows):
        for column in range(columns):
            if not grid[row, column] or seen[row, column]:
                continue
            stack, group = [(row, column)], []
            seen[row, column] = True
            while stack:
                r, c = stack.pop()
                group.append((r, c))
                for nr in range(max(0, r - 1), min(rows, r + 2)):
                    for nc in range(max(0, c - 1), min(columns, c + 2)):
                        if grid[nr, nc] and not seen[nr, nc]:
                            seen[nr, nc] = True
                            stack.append((nr, nc))
            groups.append(group)
    return groups


def boxes_mask(shape: tuple[int, int], boxes: Sequence[Box]) -> np.ndarray:
    """A (height, width) mask that is True inside any of the boxes."""
    height, width = shape
    mask = np.zeros((height, width), dtype=bool)
    for left, top, right, bottom in boxes:
        x0, x1 = sorted((int(round(left * width)), int(round(right * width))))
        y0, y1 = sorted((int(round(top * height)), int(round(bottom * height))))
        mask[max(0, y0) : min(height, y1), max(0, x0) : min(width, x1)] = True
    return mask


def photo_boxes(rgba: np.ndarray, known: Optional[Sequence[Box]] = None, block: int = 12) -> list[Box]:
    """The rectangles of a picture that hold photographs.

    With `known` boxes (the file's own images or raster layers) the answer is exact about
    *where* pictures are; each one is left out only if it actually looks like a photograph,
    so a flat raster shape is still recolored.

    Without them this is a guess from the pixels: groups of busy squares are boxed, and a
    box counts as a photo when it is big enough and mostly busy. It finds typical website
    hero pictures and thumbnails, misses flat areas inside a photo such as an even sky, and
    can mistake dense texture for a photo. It is a starting point, never a promise.
    """
    grid = busy_grid(rgba, block)
    rows, columns = grid.shape
    if known is not None:
        found = []
        for left, top, right, bottom in known:
            r0, r1 = int(top * rows), max(int(top * rows) + 1, int(np.ceil(bottom * rows)))
            c0, c1 = int(left * columns), max(int(left * columns) + 1, int(np.ceil(right * columns)))
            part = grid[r0:r1, c0:c1]
            if part.size and part.mean() >= 0.25:
                found.append((left, top, right, bottom))
        return found
    found = []
    smallest = max(6, rows * columns // 150)  # ignore specks: icons, small textured buttons
    for group in _components(grid):
        if len(group) < smallest:
            continue
        r0, r1 = min(r for r, _ in group), max(r for r, _ in group) + 1
        c0, c1 = min(c for _, c in group), max(c for _, c in group) + 1
        if len(group) / ((r1 - r0) * (c1 - c0)) < 0.35:
            continue  # scattered busy squares, not one picture
        found.append((c0 / columns, r0 / rows, c1 / columns, r1 / rows))
    return found


# --------------------------------------------------------------------------------------
# Contrast results as text and files
# --------------------------------------------------------------------------------------

#: Formats the contrast table can be saved in: key -> (label, extension).
CONTRAST_FORMATS = {
    "csv": ("Spreadsheet (CSV)", ".csv"),
    "txt": ("Plain text", ".txt"),
    "md": ("Markdown table", ".md"),
    "json": ("JSON", ".json"),
    "css": ("CSS variables with readable pairs", ".css"),
    "svg": ("Picture of the grid (SVG)", ".svg"),
    "png": ("Picture of the grid (PNG)", ".png"),
}


def contrast_pairs(colors: Sequence[str], names: Sequence[str] = ()) -> list[dict]:
    """Every pair once: the two colors, their names, the ratio and the WCAG level."""
    colors = [normalize_hex(color) for color in colors]
    labels = _labels(colors, names)
    pairs = []
    for first in range(len(colors)):
        for second in range(first + 1, len(colors)):
            ratio = contrast_ratio(colors[first], colors[second])
            pairs.append(
                {
                    "color": colors[first],
                    "name": labels[first],
                    "with": colors[second],
                    "withName": labels[second],
                    "ratio": round(ratio, 2),
                    "level": contrast_grade(ratio),
                }
            )
    return sorted(pairs, key=lambda pair: -pair["ratio"])


def _csv_cell(value: str) -> str:
    return '"' + value.replace('"', '""') + '"' if any(ch in value for ch in ',"\n') else value


def contrast_text(colors: Sequence[str], names: Sequence[str] = (), kind: str = "tsv") -> str:
    """The contrast results as text.

    'tsv' pastes into spreadsheets and document tables; the others are 'csv', 'txt',
    'md', 'json' and 'css'.
    """
    pairs = contrast_pairs(colors, names)
    header = ["Color", "Name", "With", "Name", "Contrast", "WCAG level"]
    rows = [[p["color"], p["name"], p["with"], p["withName"], f"{p['ratio']:.2f}", p["level"]] for p in pairs]
    if kind == "json":
        return json.dumps({"pairs": pairs}, indent=2, ensure_ascii=False) + "\n"
    if kind == "csv":
        return "\r\n".join(",".join(_csv_cell(value) for value in row) for row in [header, *rows]) + "\r\n"
    if kind == "md":
        lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
        lines += ["| " + " | ".join(value.replace("|", "/") for value in row) + " |" for row in rows]
        return "\n".join(lines) + "\n"
    if kind == "txt":
        lines = [
            "Contrast between palette colors (WCAG 2).",
            "4.5 or more is readable as body text; 3 or more works for large text.",
            "",
        ]
        for p in pairs:
            first = f"{p['name']} {p['color']}".strip()
            second = f"{p['withName']} {p['with']}".strip()
            lines.append(f"{p['ratio']:5.2f}  {p['level']:<8}  {first}  with  {second}")
        return "\n".join(lines) + "\n"
    if kind == "css":
        clean = [normalize_hex(color) for color in colors]
        lines = [":root {"] + [f"  --palette-{number}: {color};" for number, color in enumerate(clean, 1)] + ["}", ""]
        lines.append("/* Pairs readable as body text (WCAG AA, 4.5 or more):")
        number_of = {color: number for number, color in enumerate(clean, 1)}
        good = [p for p in pairs if p["ratio"] >= 4.5]
        lines += [
            f"   --palette-{number_of[p['color']]} with --palette-{number_of[p['with']]}: {p['ratio']:.2f} ({p['level']})"
            for p in good
        ]
        if not good:
            lines.append("   none")
        lines.append("*/")
        return "\n".join(lines) + "\n"
    return "\n".join("\t".join(row) for row in [header, *rows]) + "\n"


def contrast_svg(colors: Sequence[str]) -> str:
    """The contrast grid as a vector picture: rows are backgrounds, columns are text colors."""
    colors = [normalize_hex(color) for color in colors]
    cell, head = 92, 40
    side = head + cell * len(colors)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{side}" height="{side}" viewBox="0 0 {side} {side}" '
        'font-family="Segoe UI, Helvetica, Arial, sans-serif">',
        f'<rect width="{side}" height="{side}" fill="#FFFEFA"/>',
    ]
    for position, color in enumerate(colors):
        parts.append(f'<rect x="{head + position * cell + 4}" y="4" width="{cell - 8}" height="{head - 10}" rx="5" fill="{color}"/>')
        parts.append(f'<rect x="4" y="{head + position * cell + 4}" width="{head - 10}" height="{cell - 8}" rx="5" fill="{color}"/>')
    for row, background in enumerate(colors):
        for column, text in enumerate(colors):
            if row == column:
                continue
            x, y = head + column * cell, head + row * cell
            ratio = contrast_ratio(background, text)
            note = readable_text_color(background)
            parts.append(f'<rect x="{x + 3}" y="{y + 3}" width="{cell - 6}" height="{cell - 6}" rx="7" fill="{background}"/>')
            parts.append(
                f'<text x="{x + cell / 2}" y="{y + 44}" font-size="22" font-weight="600" text-anchor="middle" fill="{text}">Aa</text>'
            )
            parts.append(
                f'<text x="{x + cell / 2}" y="{y + 70}" font-size="11" text-anchor="middle" fill="{note}">'
                f"{ratio:.1f}  {contrast_grade(ratio)}</text>"
            )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


# --------------------------------------------------------------------------------------
# The library: palettes kept for later, in folders
# --------------------------------------------------------------------------------------


@dataclass(eq=False)  # two entries are the same only if they are the same object
class LibraryEntry:
    palette: NamedPalette
    folder: str = ""  # "" = not in a folder
    favorite: bool = False


def _unique_name(wanted: str, taken, fallback: str) -> str:
    wanted = wanted.strip()[:80] or fallback
    final, number = wanted, 2
    while final in taken:
        final, number = f"{wanted} {number}", number + 1
    return final


@dataclass
class Library:
    """Palettes saved to come back to, organised in folders, with favorites."""

    folders: list[str] = field(default_factory=list)
    entries: list[LibraryEntry] = field(default_factory=list)

    # --- palettes ----------------------------------------------------------------------

    def add(self, palette: NamedPalette, folder: str = "", name: str = "", favorite: bool = False) -> LibraryEntry:
        """Save a copy of a palette. Its name is made unique within the library."""
        final = _unique_name(name or palette.name, {entry.palette.name for entry in self.entries}, "Palette")
        copy = NamedPalette(final, [PaletteColor(c.hex, c.name, c.locked) for c in palette.colors])
        entry = LibraryEntry(copy, folder if folder in self.folders else "", favorite)
        self.entries.append(entry)
        return entry

    def remove(self, entry: LibraryEntry) -> None:
        self.entries = [known for known in self.entries if known is not entry]

    def rename(self, entry: LibraryEntry, name: str) -> str:
        others = {known.palette.name for known in self.entries if known is not entry}
        entry.palette.name = _unique_name(name, others, entry.palette.name or "Palette")
        return entry.palette.name

    def move(self, entry: LibraryEntry, folder: str) -> None:
        entry.folder = folder if folder in self.folders else ""

    def in_folder(self, folder: str) -> list[LibraryEntry]:
        return [entry for entry in self.entries if entry.folder == folder]

    def favorites(self) -> list[LibraryEntry]:
        return [entry for entry in self.entries if entry.favorite]

    def search(self, text: str) -> list[LibraryEntry]:
        """Entries whose name, folder, color names or HEX codes contain `text`."""
        wanted = text.strip().lower().lstrip("#")
        if not wanted:
            return list(self.entries)
        found = []
        for entry in self.entries:
            haystack = [
                entry.palette.name,
                entry.folder,
                *entry.palette.names(),
                *(color.lstrip("#") for color in entry.palette.hexes()),
            ]
            if any(wanted in value.lower() for value in haystack):
                found.append(entry)
        return found

    # --- folders -----------------------------------------------------------------------

    def add_folder(self, name: str) -> str:
        final = _unique_name(name, set(self.folders), "New folder")
        self.folders.append(final)
        return final

    def rename_folder(self, old: str, new: str) -> str:
        if old not in self.folders:
            return old
        final = _unique_name(new, set(self.folders) - {old}, old)
        self.folders[self.folders.index(old)] = final
        for entry in self.entries:
            if entry.folder == old:
                entry.folder = final
        return final

    def delete_folder(self, name: str, with_palettes: bool = False) -> int:
        """Delete a folder. Its palettes move out of it unless `with_palettes`.

        Returns how many palettes it held.
        """
        held = self.in_folder(name)
        if with_palettes:
            self.entries = [entry for entry in self.entries if entry.folder != name]
        else:
            for entry in held:
                entry.folder = ""
        self.folders = [folder for folder in self.folders if folder != name]
        return len(held)

    # --- saving ------------------------------------------------------------------------

    def to_json(self) -> str:
        data = {
            "format": "colorroom-library",
            "version": 1,
            "folders": list(self.folders),
            "palettes": [
                {
                    "name": entry.palette.name,
                    "folder": entry.folder,
                    "favorite": entry.favorite,
                    "colors": [{"hex": c.hex, **({"name": c.name} if c.name else {})} for c in entry.palette.colors],
                }
                for entry in self.entries
            ],
        }
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "Library":
        """Read a saved library. Raises ValueError with a plain message if it is not one."""
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise ValueError("This is not a Colorroom library file.") from None
        if not isinstance(data, dict) or data.get("format") != "colorroom-library":
            raise ValueError("This is not a Colorroom library file.")
        library = cls()
        for name in (data.get("folders") or [])[:500]:
            if isinstance(name, str) and name.strip() and name.strip()[:80] not in library.folders:
                library.folders.append(name.strip()[:80])
        for item in (data.get("palettes") or [])[:5000]:
            if not isinstance(item, dict):
                continue
            palette = NamedPalette(str(item.get("name") or "Palette")[:80])
            for color in (item.get("colors") or [])[:MAX_COLORS]:
                try:
                    value = normalize_hex(str(color.get("hex", "")) if isinstance(color, dict) else str(color))
                except ValueError:
                    continue
                label = str(color.get("name") or "")[:60] if isinstance(color, dict) else ""
                palette.colors.append(PaletteColor(value, label))
            if palette.colors:
                folder = item.get("folder") if isinstance(item.get("folder"), str) else ""
                library.add(palette, folder, favorite=bool(item.get("favorite")))
        return library
