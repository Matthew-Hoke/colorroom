"""The color index: every distinct color in an image, how often it occurs, and where.

This replaces the Web Worker of Colorroom 1.7. All per-color data lives in NumPy arrays
that share one numbering, the "distinct index": color number i has key `keys[i]`,
appears `counts[i]` times, and is first seen at flat pixel `locations[i]`. Keys are
stored in ascending order, so the distinct index is also the HEX sort order.
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from .colorspace import key_to_hex, key_to_oklab, keys_to_oklab

#: Largest image the app accepts (width x height), same as the original.
MAX_PIXELS = 24_000_000
COLOR_SPACE_SIZE = 1 << 24
#: The three ways the inventory can be ordered.
SORTS = ("variety", "frequency", "hex")

_NO_PIXEL = 0xFFFFFFFF
_CHUNK_PIXELS = 2_000_000
_FLOAT64_LIMIT = 4_000_000  # above this many distinct colors, Oklab is stored as float32

ProgressFn = Callable[[float, str], None]
CancelFn = Callable[[], bool]


class ImageError(ValueError):
    """A problem with the image, worded so it can be shown to the user as-is."""


class AnalysisCancelled(Exception):
    """Raised inside `ColorIndex.build` when the caller asks to stop."""


@dataclass(frozen=True)
class Point:
    """The center of a pixel as fractions of the image width and height (0..1)."""

    x: float
    y: float


@dataclass(frozen=True)
class PixelPick:
    """The result of clicking a pixel in the image."""

    index: int  # distinct index of the clicked color
    color: str  # its HEX code
    point: Point  # center of the clicked pixel
    allowed: bool  # False if the color is outside the current "shades to show" limit


class ColorIndex:
    """Everything the app knows about the colors of one image."""

    def __init__(self) -> None:
        self.width = 0
        self.height = 0
        self.visible_pixels = 0
        self.rgba: np.ndarray = np.empty((0, 0, 4), dtype=np.uint8)
        self.keys: np.ndarray = np.empty(0, dtype=np.uint32)
        self.counts: np.ndarray = np.empty(0, dtype=np.uint32)
        self.locations: np.ndarray = np.empty(0, dtype=np.uint32)
        self.frequency_order: np.ndarray = np.empty(0, dtype=np.int32)
        self.variety_order: np.ndarray = np.empty(0, dtype=np.int32)
        self.variety_rank: np.ndarray = np.empty(0, dtype=np.int32)
        self.candidate_idx: np.ndarray = np.empty(0, dtype=np.int32)
        self.candidate_weight: np.ndarray = np.empty(0, dtype=np.float64)
        self.candidate_lab: np.ndarray = np.empty((3, 0), dtype=np.float64)
        self.lab: np.ndarray = np.empty((3, 0), dtype=np.float64)
        self._nearest_cache: dict[int, int] = {}

    # ----------------------------------------------------------------------------------
    # Building
    # ----------------------------------------------------------------------------------

    @classmethod
    def build(
        cls,
        rgba: np.ndarray,
        progress: Optional[ProgressFn] = None,
        cancelled: Optional[CancelFn] = None,
    ) -> "ColorIndex":
        """Analyze an image given as a (height, width, 4) array of 8-bit sRGB RGBA.

        Fully transparent pixels are skipped. `progress(fraction, message)` is called
        as work proceeds, and `cancelled()` is polled so a long analysis can be stopped.
        """

        def report(fraction: float, message: str) -> None:
            if cancelled is not None and cancelled():
                raise AnalysisCancelled()
            if progress is not None:
                progress(fraction, message)

        rgba = np.ascontiguousarray(rgba)
        if rgba.ndim != 3 or rgba.shape[2] != 4 or rgba.dtype != np.uint8:
            raise ValueError("Expected an 8-bit RGBA array of shape (height, width, 4).")
        height, width = rgba.shape[:2]
        if width * height == 0:
            raise ImageError("This image has no pixels.")
        if width * height > MAX_PIXELS:
            raise ImageError("Choose an image with 24 megapixels or fewer.")

        self = cls()
        self.width, self.height, self.rgba = width, height, rgba

        # 1. Count every color and remember the first pixel where each one appears.
        report(0.0, "Counting colors…")
        totals = np.zeros(COLOR_SPACE_SIZE, dtype=np.uint32)
        firsts = np.full(COLOR_SPACE_SIZE, _NO_PIXEL, dtype=np.uint32)
        rows_per_chunk = max(1, _CHUNK_PIXELS // width)
        visible = 0
        for top in range(0, height, rows_per_chunk):
            block = rgba[top : top + rows_per_chunk]
            chunk_keys = (
                (block[..., 0].astype(np.uint32) << 16)
                | (block[..., 1].astype(np.uint32) << 8)
                | block[..., 2]
            ).ravel()
            alpha = block[..., 3].ravel()
            offset = top * width
            if alpha.all():
                positions = np.arange(offset, offset + chunk_keys.size, dtype=np.uint32)
            else:
                shown = np.flatnonzero(alpha)
                chunk_keys = chunk_keys[shown]
                positions = (shown + offset).astype(np.uint32)
            np.add.at(totals, chunk_keys, 1)
            np.minimum.at(firsts, chunk_keys, positions)
            visible += int(chunk_keys.size)
            report(0.6 * min(1.0, (top + rows_per_chunk) / height), "Counting colors…")
        if not visible:
            raise ImageError("This image is fully transparent. Choose an image with visible pixels.")
        self.visible_pixels = visible

        # 2. Keep only the colors that occur. They come out in ascending HEX order.
        report(0.62, "Sorting colors…")
        self.keys = np.flatnonzero(totals).astype(np.uint32)
        self.counts = totals[self.keys]
        self.locations = firsts[self.keys]
        del totals, firsts

        # 3. Most frequent first; ties go to the lower HEX value. A stable sort on the
        #    inverted counts does this because the keys are already ascending.
        top_count = int(self.counts.max())
        inverted = top_count - self.counts
        if top_count < 1 << 16:
            inverted = inverted.astype(np.uint16)  # NumPy radix-sorts 16-bit values
        self.frequency_order = np.argsort(inverted, kind="stable").astype(np.int32)
        del inverted

        # 4. Candidates: group colors into 32 x 32 x 32 bins. Each occupied bin is
        #    represented by its most frequent color and weighted by all its pixels.
        report(0.7, "Grouping similar colors…")
        keys = self.keys
        bins = ((keys >> 19) << 10) | (((keys >> 11) & 31) << 5) | ((keys >> 3) & 31)
        _, first_seen = np.unique(bins[self.frequency_order], return_index=True)
        representatives = self.frequency_order[first_seen]
        weights = np.bincount(bins, weights=self.counts, minlength=1 << 15)[bins[representatives]]
        by_weight = np.lexsort((keys[representatives], -weights))
        self.candidate_idx = representatives[by_weight].astype(np.int32)
        self.candidate_weight = weights[by_weight]
        self.candidate_lab = keys_to_oklab(keys[self.candidate_idx])
        del bins

        # 5. The "variety" order used by the inventory and the shade-detail slider.
        report(0.78, "Ranking color variety…")
        self._index_variety()

        # 6. Oklab for every color, computed once so later searches are instant.
        report(0.86, "Mapping colors for comparison…")
        dtype = np.float64 if keys.size <= _FLOAT64_LIMIT else np.float32
        self.lab = keys_to_oklab(keys, dtype=dtype)
        report(1.0, "Done")
        return self

    def _index_variety(self) -> None:
        """Order colors so the first N are as varied as possible.

        Leaders are 12 well-spread picks, then the dominant shade of each color group
        from coarse groups (2 x 2 x 2) to fine ones (32 x 32 x 32). Every other color
        follows by frequency.
        """
        leaders = list(dict.fromkeys(self.balanced(12, 0.1)))
        seen = set(leaders)
        candidate_keys = self.keys[self.candidate_idx]
        for bits in range(1, 6):
            shift = 8 - bits
            red = (candidate_keys >> 16) >> shift
            green = ((candidate_keys >> 8) & 255) >> shift
            blue = (candidate_keys & 255) >> shift
            groups = (red << (bits * 2)) | (green << bits) | blue
            _, first_seen = np.unique(groups, return_index=True)
            for position in np.sort(first_seen):
                leader = int(self.candidate_idx[position])
                if leader not in seen:
                    seen.add(leader)
                    leaders.append(leader)
        is_leader = np.zeros(self.distinct, dtype=bool)
        is_leader[leaders] = True
        rest = self.frequency_order[~is_leader[self.frequency_order]]
        self.variety_order = np.concatenate([np.asarray(leaders, dtype=np.int32), rest]).astype(np.int32)
        self.variety_rank = np.empty(self.distinct, dtype=np.int32)
        self.variety_rank[self.variety_order] = np.arange(self.distinct, dtype=np.int32)

    # ----------------------------------------------------------------------------------
    # Simple lookups
    # ----------------------------------------------------------------------------------

    @property
    def distinct(self) -> int:
        """How many different colors the image contains."""
        return int(self.keys.size)

    @property
    def base_color(self) -> str:
        """The most frequent color, used as the default base for color-wheel palettes."""
        return self.hex(int(self.frequency_order[0]))

    def hex(self, index: int) -> str:
        return key_to_hex(int(self.keys[index]))

    def count(self, index: int) -> int:
        return int(self.counts[index])

    def share(self, index: int) -> float:
        """Fraction of the visible pixels that have this color."""
        return int(self.counts[index]) / self.visible_pixels

    def index_of(self, key: int) -> Optional[int]:
        """Distinct index of a color key, or None if the image does not contain it."""
        key = int(key)
        position = int(np.searchsorted(self.keys, key))
        if position < self.keys.size and int(self.keys[position]) == key:
            return position
        return None

    def locate(self, index: int) -> Point:
        """Where the color first appears, as the center of that pixel."""
        position = int(self.locations[index])
        return self._pixel_center(position % self.width, position // self.width)

    def _pixel_center(self, column: int, row: int) -> Point:
        return Point((column + 0.5) / self.width, (row + 0.5) / self.height)

    def pixel_at(self, x: float, y: float) -> tuple[int, int]:
        """Column and row of the pixel under the fractional position (x, y)."""
        column = min(self.width - 1, max(0, int(x * self.width)))
        row = min(self.height - 1, max(0, int(y * self.height)))
        return column, row

    def in_subset(self, index: int, limit: Optional[int]) -> bool:
        """True if the color is among the first `limit` colors of the variety order."""
        return limit is None or limit >= self.distinct or int(self.variety_rank[index]) < limit

    def pick_pixel(self, x: float, y: float, limit: Optional[int] = None) -> Optional[PixelPick]:
        """Read the color under a click. Returns None on a fully transparent pixel."""
        column, row = self.pixel_at(x, y)
        red, green, blue, alpha = (int(value) for value in self.rgba[row, column])
        if not alpha:
            return None
        index = self.index_of((red << 16) | (green << 8) | blue)
        assert index is not None
        return PixelPick(index, self.hex(index), self._pixel_center(column, row), self.in_subset(index, limit))

    # ----------------------------------------------------------------------------------
    # Orders for the inventory table
    # ----------------------------------------------------------------------------------

    def order(self, sort: str = "variety", limit: Optional[int] = None, prefix: str = "") -> np.ndarray:
        """Distinct indices to list, in display order.

        sort    "variety", "frequency" (most frequent first) or "hex" (ascending).
        limit   only the first `limit` colors of the variety order are eligible.
        prefix  only HEX codes starting with these characters, e.g. "B4" or "#b4".
        """
        if sort not in SORTS:
            raise ValueError(f"Unknown sort: {sort!r}")
        total = self.distinct
        limit = total if limit is None else max(0, min(int(limit), total))
        prefix = prefix.strip().lstrip("#")
        lower = upper = 0
        if prefix:
            if len(prefix) > 6 or any(ch not in string.hexdigits for ch in prefix):
                raise ValueError("Enter up to six HEX characters (0-9 or A-F).")
            unit = 16 ** (6 - len(prefix))
            lower = int(prefix, 16) * unit
            upper = lower + unit

        if sort == "hex":
            start, stop = 0, total
            if prefix:
                start = int(np.searchsorted(self.keys, lower))
                stop = int(np.searchsorted(self.keys, upper))
            if limit >= total:
                return np.arange(start, stop, dtype=np.int32)
            return (np.flatnonzero(self.variety_rank[start:stop] < limit) + start).astype(np.int32)

        if sort == "frequency":
            order = self.frequency_order
            if limit < total:
                order = order[self.variety_rank[order] < limit]
        else:
            order = self.variety_order[:limit]
        if prefix:
            listed = self.keys[order]
            order = order[(listed >= lower) & (listed < upper)]
        return order

    # ----------------------------------------------------------------------------------
    # Representative colors
    # ----------------------------------------------------------------------------------

    def balanced(self, limit: int, weight_exponent: float = 0.25) -> list[int]:
        """Pick up to `limit` candidates that are both common and far apart.

        Starts with the heaviest candidate, then repeatedly takes the color with the
        best score = (squared Oklab distance to the nearest color already chosen)
        x (pixel weight ** weight_exponent). Returns distinct indices.
        """
        total = int(self.candidate_idx.size)
        wanted = min(int(limit), total)
        if wanted <= 0:
            return []
        weight = np.power(self.candidate_weight, weight_exponent)
        light, a, b = self.candidate_lab
        nearest = np.full(total, np.inf)
        chosen: list[int] = []
        pick = 0
        while len(chosen) < wanted:
            chosen.append(pick)
            distance = (light - light[pick]) ** 2 + (a - a[pick]) ** 2 + (b - b[pick]) ** 2
            np.minimum(nearest, distance, out=nearest)
            pick = int(np.argmax(nearest * weight))
        return [int(self.candidate_idx[position]) for position in chosen]

    def dominant(self, limit: int) -> list[int]:
        """The `limit` heaviest candidates, as distinct indices."""
        return [int(value) for value in self.candidate_idx[: max(0, int(limit))]]

    def nearest(self, key: int) -> int:
        """Distinct index of the image color that looks closest to `key` (Oklab distance).

        Every color in the image is considered, however many there are.
        """
        key = int(key)
        exact = self.index_of(key)
        if exact is not None:
            return exact
        cached = self._nearest_cache.get(key)
        if cached is not None:
            return cached
        light, a, b = key_to_oklab(key)
        lights, a_values, b_values = self.lab
        best, best_distance = 0, float("inf")
        step = 1 << 20
        for start in range(0, self.distinct, step):
            part = slice(start, start + step)
            distance = (lights[part] - light) ** 2 + (a_values[part] - a) ** 2 + (b_values[part] - b) ** 2
            position = int(np.argmin(distance))
            if float(distance[position]) < best_distance:
                best, best_distance = start + position, float(distance[position])
        self._nearest_cache[key] = best
        return best
