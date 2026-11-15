"""Tests for the role ladder and exact-color layers (Fable 5.2's work in progress of
2026-11-13, written as tests by Opus 5.5 on 2026-11-14 before it was committed)."""

from __future__ import annotations

import numpy as np

from colorroom.core import designer

PALETTE = ["#102A54", "#E07A5F", "#F2CC8F", "#FFFFFF", "#F4F1DE", "#111111"]


def test_the_order_of_the_palette_gives_the_roles():
    ladder = designer.ladder_overrides(PALETTE)
    assert list(ladder) == list(designer.LADDER)
    assert ladder["Primary"] == "#102A54" and ladder["Text"] == "#111111"
    assert designer.ladder_overrides(["#abc", "#AABBCC", "#000"]) == {"Primary": "#AABBCC", "Secondary": "#000000"}  # repeats count once
    by_order = designer.roles_for(PALETTE, True)
    assert by_order.as_dict()["Secondary"] == "#E07A5F" and by_order.as_dict()["Background"] == "#FFFFFF"
    assert designer.roles_for([], True) is None


def test_roles_held_by_each_color():
    roles = designer.roles_for(PALETTE, True)
    held = designer.role_of_each(PALETTE, roles)
    assert held["#102A54"] == ["Primary"] and held["#111111"] == ["Text"]
    assert set(held) == set(PALETTE)
    assert designer.role_of_each(PALETTE, None) == {color: [] for color in PALETTE}


def test_giving_a_role_swaps_instead_of_leaving_a_role_empty():
    overrides = {"Primary": "#102A54", "Accent": "#F2CC8F"}
    swapped = designer.give_role(PALETTE, overrides, "Primary", "#F2CC8F")
    assert swapped["Primary"] == "#F2CC8F" and swapped["Accent"] == "#102A54"
    taken = designer.give_role(PALETTE, swapped, "", "#F2CC8F")
    assert "#F2CC8F" not in taken.values() and taken["Accent"] == "#102A54"
    assert designer.give_role(PALETTE, overrides, "Nonsense", "#F2CC8F") == overrides
    fresh = designer.give_role(PALETTE, {}, "Text", "#E07A5F")
    assert fresh == {"Text": "#E07A5F"}


def test_layers_from_exact_colors_keep_every_listed_color_and_leave_strangers_alone():
    rgba = np.zeros((20, 40, 4), dtype=np.uint8)
    rgba[..., 3] = 255
    rgba[:, :20, :3] = (16, 42, 84)  # #102A54
    rgba[:, 20:, :3] = (224, 122, 95)  # #E07A5F
    rgba[0, 0, :3] = (16, 42, 85)  # one level off
    rgba[19, 39, :3] = (0, 255, 0)  # a color nobody listed (a photo pixel)
    layers = designer.layers_from_colors(rgba, ["#102a54", "#E07A5F", "#F2CC8F"])
    assert layers.colors == ["#102A54", "#E07A5F", "#F2CC8F"]  # kept, even the one covering nothing
    assert layers.shares[2] < 0.01 and abs(layers.shares[0] - 0.5) < 0.01  # a stray pixel counts toward its nearest
    out = designer.recolor(layers, ["#FFFFFF", None, None])
    assert tuple(out[5, 5, :3]) == (255, 255, 255) and tuple(out[0, 0, :3])[0] >= 250
    assert tuple(out[5, 30, :3]) == (224, 122, 95)  # kept
    assert tuple(out[19, 39, :3]) == (0, 255, 0)  # far from every listed color: not dragged along
    empty = designer.layers_from_colors(rgba, [])
    assert empty.colors == [] and np.array_equal(designer.recolor(empty, []), rgba)
