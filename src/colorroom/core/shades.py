"""Related shades: the colors in an image that belong to the same family as a chosen one.

Two colors are in the same family when they are both near-neutral (grays), or both
colorful with hue angles within about 30 degrees of each other in Oklab. Shades are
ranked by how different they look from the chosen color and grouped into five tones.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .index import ColorIndex, Point

TONE_NAMES = ("Deep", "Dark", "Midtone", "Bright", "Light")
#: Oklab lightness boundaries between the five tones.
TONE_LIMITS = (0.32, 0.47, 0.62, 0.78)
#: Below this Oklab chroma a color counts as a neutral (a gray).
NEUTRAL_CHROMA = 0.035
#: cos(30 degrees): the widest hue difference that still counts as the same family.
FAMILY_COSINE = 0.866

_CACHE_SIZE = 6


@dataclass(frozen=True)
class ShadeEntry:
    """One related shade, ready to show."""

    index: int  # distinct index in the ColorIndex
    color: str  # HEX code
    point: Point  # where it first appears in the image
    difference: float  # estimated visual difference from the base color, in percent
    pixels: int  # how many pixels have this color


@dataclass(frozen=True)
class ToneSummary:
    """One tone group: its name, how many shades it holds, and its most common shade."""

    name: str
    tone: int
    count: int
    sample: ShadeEntry


class RelatedShades:
    """All shades related to one base color, sorted from most to least similar."""

    def __init__(self, index: ColorIndex, base: int) -> None:
        self.index = index
        self.base = int(base)
        lights, a_values, b_values = index.lab
        base_light, base_a, base_b = (float(lights[base]), float(a_values[base]), float(b_values[base]))
        base_chroma = float(np.hypot(base_a, base_b))
        chroma = np.hypot(a_values, b_values)
        if base_chroma < NEUTRAL_CHROMA:
            family = chroma < NEUTRAL_CHROMA
        else:
            family = (chroma >= NEUTRAL_CHROMA) & (
                a_values * base_a + b_values * base_b >= FAMILY_COSINE * chroma * base_chroma
            )
        family[base] = False
        members = np.flatnonzero(family)
        distance = (
            (lights[members] - base_light) ** 2
            + (a_values[members] - base_a) ** 2
            + (b_values[members] - base_b) ** 2
        ).astype(np.float64)
        # Most similar first; ties go to the more frequent color, then the lower HEX.
        # `members` is ascending, which is also ascending HEX order.
        ranking = np.lexsort((members, -index.counts[members].astype(np.int64), distance))
        self.indices = members[ranking].astype(np.int32)
        self.distances = distance[ranking]
        self.tones = np.searchsorted(TONE_LIMITS, lights[self.indices], side="right").astype(np.int8)
        self._groups: Optional[list[np.ndarray]] = None

    def __len__(self) -> int:
        return int(self.indices.size)

    def _tone_groups(self) -> list[np.ndarray]:
        """For each tone, positions into `indices`, most frequent shade first."""
        if self._groups is None:
            counts = self.index.counts
            groups = []
            for tone in range(len(TONE_NAMES)):
                positions = np.flatnonzero(self.tones == tone)
                members = self.indices[positions]
                by_frequency = np.lexsort((members, -counts[members].astype(np.int64)))
                groups.append(positions[by_frequency])
            self._groups = groups
        return self._groups

    def count_within(self, maximum: float) -> int:
        """How many shades differ from the base by at most `maximum` percent."""
        if maximum >= 100:
            return len(self)
        return int(np.searchsorted(self.distances, (maximum / 100) ** 2, side="right"))

    def select(self, tone: Optional[int] = None, maximum: float = 100.0) -> np.ndarray:
        """Positions of the shades to list.

        tone=None lists every tone, most similar first. A tone number (0 = Deep to
        4 = Light) lists that tone only, most frequent first. `maximum` keeps only the
        shades within that percentage difference of the base color.
        """
        if tone is None:
            return np.arange(self.count_within(maximum), dtype=np.int64)
        positions = self._tone_groups()[tone]
        if maximum < 100:
            positions = positions[self.distances[positions] <= (maximum / 100) ** 2]
        return positions

    def entry(self, position: int) -> ShadeEntry:
        shade = int(self.indices[position])
        difference = min(100.0, float(np.sqrt(self.distances[position])) * 100)
        return ShadeEntry(shade, self.index.hex(shade), self.index.locate(shade), difference, self.index.count(shade))

    def entries(self, positions) -> list[ShadeEntry]:
        return [self.entry(int(position)) for position in positions]

    def tone_summaries(self) -> list[ToneSummary]:
        """The non-empty tone groups, darkest first."""
        summaries = []
        for tone, positions in enumerate(self._tone_groups()):
            if positions.size:
                summaries.append(ToneSummary(TONE_NAMES[tone], tone, int(positions.size), self.entry(int(positions[0]))))
        return summaries


def related_shades(index: ColorIndex, base: int) -> RelatedShades:
    """Related shades for the color at distinct index `base` (recent results are cached)."""
    cache: OrderedDict[int, RelatedShades] = index.__dict__.setdefault("_related_cache", OrderedDict())
    base = int(base)
    found = cache.get(base)
    if found is None:
        found = RelatedShades(index, base)
        cache[base] = found
        while len(cache) > _CACHE_SIZE:
            cache.popitem(last=False)
    else:
        cache.move_to_end(base)
    return found
