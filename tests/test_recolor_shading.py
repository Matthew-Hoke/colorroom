"""In-between shades: soft shading and anti-aliased edges stay smooth when recolored.

Made by Opus 5.5 (color scientist), for review by Fable. Proposed new tests: they pin the
"Blend" behavior added to designer.find_layers / suggest_targets / job_targets / recolor
and file_export.recolor_pixels.

Reference values:
- Oklab of sRGB red (1, 0, 0) is (0.627955, 0.224863, 0.125846), from Bjorn Ottosson,
  "A perceptual color space for image processing" (2020), bottosson.github.io/posts/oklab.
- deltaEOK (Euclidean Oklab distance) of about 0.02 is one just-noticeable difference
  (CSS Color Module Level 4, section 13.2).
"""

from __future__ import annotations

import numpy as np
import pytest

from colorroom.core import designer, file_export
from colorroom.core.file_export import Recipe

NAVY, WHITE, YELLOW, BLUE = "#0B1F3A", "#FFFFFF", "#FFD23F", "#2F6FDB"


def _rgb(color):
    return np.array([int(color[i : i + 2], 16) for i in (1, 3, 5)], dtype=np.float64)


def pencil(ink="#F7C82B", paper="#050505", height=120, width=256, seed=1):
    """Ink over paper: coverage runs 0..1 left to right, with pencil grain on top."""
    rng = np.random.default_rng(seed)
    coverage = np.clip(np.linspace(-0.15, 1.0, width)[None, :] + rng.normal(scale=0.06, size=(height, width)), 0, 1)
    coverage[:, : width // 6] = 0  # a band of bare paper (a flat fill)
    rgb = (1 - coverage[..., None]) * _rgb(paper) + coverage[..., None] * _rgb(ink)
    rgba = np.concatenate([np.rint(rgb), np.full((height, width, 1), 255)], axis=2).astype(np.uint8)
    return rgba, coverage


def lab_of(rgba):
    return designer._rgb_to_oklab(rgba.reshape(-1, 4)[:, :3]).reshape(rgba.shape[0], rgba.shape[1], 3)


def recolored(rgba, palette):
    layers = designer.find_layers(rgba)
    roles = designer.assign_roles(palette)
    jobs = designer.suggest_targets(layers, roles)
    targets = designer.job_targets(layers.colors, jobs, roles.as_dict(), layers.blends)
    return layers, jobs, targets, designer.recolor(layers, targets)


def test_oklab_reference_value():
    lab = designer._rgb_to_oklab(np.array([[255, 0, 0]], dtype=np.uint8))[0]
    assert lab == pytest.approx([0.627955, 0.224863, 0.125846], abs=2e-4)


def test_ink_over_paper_stays_on_a_straight_oklab_line():
    """Why RAMP_TOLERANCE is 0.03: real ink blends never bend further from the line."""
    t = np.linspace(0, 1, 101)[:, None]
    for paper in ("#FFFFFF", "#050505", "#F4EBD9"):
        for ink in ("#F7C82B", "#2F6FDB", "#D7263D", "#2A9D3F", "#333333", "#7A3EB1"):
            rgb = np.rint((1 - t) * _rgb(paper) + t * _rgb(ink)).astype(np.uint8)
            lab = designer._rgb_to_oklab(rgb)
            line = lab[-1] - lab[0]
            u = (lab - lab[0]) @ line / (line @ line)
            off = np.linalg.norm(lab - (lab[0] + u[:, None] * line), axis=1)
            assert off.max() < designer.RAMP_TOLERANCE, (paper, ink)


def test_pencil_shading_becomes_smooth_shading():
    rgba, coverage = pencil()
    layers, jobs, targets, out = recolored(rgba, [NAVY, WHITE, YELLOW, BLUE])
    assert layers.blends, "the in-between shades of a two-tone drawing are found"
    assert {jobs[b.index] for b in layers.blends} == {designer.BLEND}
    assert sum(job not in ("", designer.BLEND) for job in jobs) == 2  # only paper and ink get roles
    lab_in, lab_out = lab_of(rgba), lab_of(out)
    # Neighbours that look the same before (dE < 0.02) never jump apart after (dE > 0.08).
    d_in = np.linalg.norm(np.diff(lab_in, axis=1), axis=2)
    d_out = np.linalg.norm(np.diff(lab_out, axis=1), axis=2)
    assert not np.any(d_out[d_in < 0.02] > 0.08)
    # The tone still runs one way: average lightness per column follows the coverage.
    column = lab_out[..., 0].mean(axis=0)[rgba.shape[1] // 6 :]
    steps = np.diff(np.convolve(column, np.ones(8) / 8, mode="valid"))
    assert np.all(steps <= 0.004) or np.all(steps >= -0.004)


def test_graphite_on_white_keeps_its_light_and_dark():
    rgba, coverage = pencil(ink="#2B2B2B", paper="#FAFAF7")
    layers, jobs, targets, out = recolored(rgba, [NAVY, WHITE, YELLOW, BLUE])
    lab_out = lab_of(out)
    light = lab_out[..., 0][coverage < 0.1].mean()
    dark = lab_out[..., 0][coverage > 0.9].mean()
    assert light > dark + 0.4
    assert np.hypot(lab_out[..., 1], lab_out[..., 2])[coverage > 0.05].max() < 0.08  # no yellow or blue flecks


def test_flat_colors_have_no_in_between_shades_and_map_exactly():
    colors = ["#FFFFFF", "#102A54", "#E0452F", "#F2B705"]
    rgba = np.zeros((40, 160, 4), dtype=np.uint8)
    for position, color in enumerate(colors):
        rgba[:, position * 40 : (position + 1) * 40] = (*_rgb(color).astype(int), 255)
    layers = designer.find_layers(rgba)
    assert layers.blends == [] and all(layers.flat)
    targets = [None] * 4
    red = next(i for i, color in enumerate(layers.colors) if designer.oklab_distance(color, "#E0452F") < 0.01)
    targets[red] = "#2E8B57"
    out = designer.recolor(layers, targets)
    assert tuple(out[20, 2 * 40 + 20, :3]) == tuple(_rgb("#2E8B57").astype(int))
    for position in (0, 1, 3):
        assert tuple(out[20, position * 40 + 20]) == tuple(rgba[20, position * 40 + 20])


def test_anti_aliased_edges_follow_the_text_and_the_background():
    """Soft edges between dark text and a white page must not take a third color."""
    height, width = 60, 300
    x = np.arange(width)[None, :] + 0.5
    y = np.arange(height)[:, None]
    ink = np.zeros((height, width))
    for start in np.arange(10, 285, 13.7):  # slanted strokes at sub-pixel positions, like small type
        left = start + 0.11 * y
        ink += np.clip(np.minimum(x + 0.5, left + 2.6) - np.maximum(x - 0.5, left), 0, 1)  # pixel coverage
    ink = np.clip(ink, 0, 1)
    rgba = np.full((height, width, 4), 255, dtype=np.uint8)
    rgba[..., :3] = np.rint(255 - ink[..., None] * (255 - 17)).astype(np.uint8)
    layers, jobs, targets, out = recolored(rgba, [NAVY, WHITE, YELLOW, BLUE])
    lab_out = lab_of(out)
    edges = (ink > 0.1) & (ink < 0.9)
    assert edges.sum() > 500
    # Every edge pixel stays between the new page (white) and the new text (navy): no yellow
    # or blue fringe. (Navy itself has Oklab b of about -0.04.)
    chroma = np.hypot(lab_out[..., 1], lab_out[..., 2])
    assert chroma[edges].max() < 0.06


def test_keeping_the_ends_keeps_the_shades():
    rgba, _ = pencil()
    layers = designer.find_layers(rgba)
    jobs = [designer.BLEND if any(b.index == i for b in layers.blends) else "" for i in range(len(layers.colors))]
    targets = designer.job_targets(layers.colors, jobs, designer.assign_roles([NAVY, WHITE]).as_dict(), layers.blends)
    assert targets == [None] * len(layers.colors)
    assert np.array_equal(designer.recolor(layers, targets), rgba)


def test_a_shade_given_its_own_role_takes_it():
    rgba, _ = pencil()
    layers = designer.find_layers(rgba)
    roles = designer.assign_roles([NAVY, WHITE, YELLOW, BLUE])
    jobs = designer.suggest_targets(layers, roles)
    chosen = layers.blends[0].index
    jobs[chosen] = "Primary"
    targets = designer.job_targets(layers.colors, jobs, roles.as_dict(), layers.blends)
    assert targets[chosen] == roles.primary


def test_full_size_matches_the_preview_with_shades():
    rgba, _ = pencil()
    layers, jobs, targets, preview = recolored(rgba, [NAVY, WHITE, YELLOW, BLUE])
    recipe = Recipe(layers.colors, targets, [], layers.blends, layers.flat)
    full = file_export.recolor_pixels(rgba, recipe, chunk=5_000)
    assert np.abs(preview.astype(int) - full.astype(int)).max() <= 1
