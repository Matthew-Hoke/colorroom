"""Parity of the page-side color math (HSL, artist's wheel, palette suggestions)."""

from __future__ import annotations

import json

import pytest

from colorroom.core import palettes
from colorroom.core.colorspace import from_hsl, shift_on_wheel, to_hsl

from conftest import FIXTURES

EXPECTED = json.loads((FIXTURES / "expected" / "_suggest.json").read_text(encoding="utf-8"))


def test_to_hsl():
    for item in EXPECTED["hsl"]:
        hue, saturation, lightness = to_hsl(item["color"])
        assert hue == pytest.approx(item["hue"], abs=1e-9)
        assert saturation == pytest.approx(item["saturation"], abs=1e-12)
        assert lightness == pytest.approx(item["lightness"], abs=1e-12)


def test_from_hsl():
    for item in EXPECTED["fromHsl"]:
        assert from_hsl(item["hue"], item["saturation"], item["lightness"]) == item["color"], item


def test_shift_on_wheel():
    for item in EXPECTED["wheel"]:
        assert shift_on_wheel(item["hue"], item["steps"]) == pytest.approx(item["result"], abs=1e-9)


def test_constants_match_the_original():
    assert {name: list(colors) for name, colors in palettes.THEMES.items()} == EXPECTED["themes"]
    assert palettes.AESTHETIC_STYLES == EXPECTED["aestheticStyles"]
    assert list(palettes.BRAND_ROLES) == EXPECTED["brandRoles"]
    for mode, (steps, description) in palettes.WHEEL_STYLES.items():
        original = EXPECTED["wheelStyles"][mode]
        assert description == original["description"]
        assert (list(steps) if steps else None) == original.get("steps")


def test_suggested_colors():
    for item in EXPECTED["suggest"]:
        mode, base, size, theme = item["mode"], item["base"], item["size"], item["theme"]
        if mode == "monochromatic":
            # The original always gave up to five colors. Ours honours the count, and
            # agrees with the original for every count up to five.
            assert palettes.suggest_colors(mode, base, 5, theme) == item["colors"], item
            assert palettes.suggest_colors(mode, base, 3, theme) == item["colors"][:3], item
        elif mode == "seasonal":
            assert palettes.suggest_colors(mode, base, 5, theme) == item["colors"], item
        else:
            assert palettes.suggest_colors(mode, base, size, theme) == item["colors"], item
