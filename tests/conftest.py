"""Shared helpers for the tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from colorroom.core.index import ColorIndex

FIXTURES = Path(__file__).parent / "fixtures"
IMAGE_NAMES = sorted(path.stem for path in (FIXTURES / "images").glob("*.png"))


def load_rgba(name: str) -> np.ndarray:
    return np.array(Image.open(FIXTURES / "images" / f"{name}.png").convert("RGBA"), dtype=np.uint8)


def load_expected(name: str) -> dict:
    return json.loads((FIXTURES / "expected" / f"{name}.json").read_text(encoding="utf-8"))


def sha1_u32(values) -> str:
    """SHA-1 of the values as little-endian 32-bit integers (matches tools/make_fixtures.mjs)."""
    return hashlib.sha1(np.asarray(values, dtype="<u4").tobytes()).hexdigest()


_cache: dict[str, ColorIndex] = {}


def build_index(name: str) -> ColorIndex:
    if name not in _cache:
        _cache[name] = ColorIndex.build(load_rgba(name))
    return _cache[name]


@pytest.fixture(params=IMAGE_NAMES)
def case(request):
    """(image name, its ColorIndex, what Colorroom 1.7 produced for it)."""
    name = request.param
    return name, build_index(name), load_expected(name)


@pytest.fixture
def sample_index() -> ColorIndex:
    return build_index("sample")


@pytest.fixture
def blobs_index() -> ColorIndex:
    return build_index("blobs")
