"""Color math: HEX keys, sRGB to linear, Oklab, HSL and the artist's color wheel.

Ported from Colorroom 1.7 (legacy/index.html). A color "key" is the 24-bit integer
0xRRGGBB. The order of every arithmetic operation matches the JavaScript original so
results agree with it as closely as the platform's math library allows.
"""

from __future__ import annotations

import math

import numpy as np

# --------------------------------------------------------------------------------------
# HEX <-> key
# --------------------------------------------------------------------------------------


def hex_to_key(color: str) -> int:
    """'#B47A3C', 'b47a3c' or the short form '#B73' -> 0xB47A3C."""
    text = color.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        raise ValueError(f"Not a HEX color: {color!r}")
    return int(text, 16)


def key_to_hex(key: int) -> str:
    """0xB47A3C -> '#B47A3C' (always upper case, like the original app)."""
    return f"#{int(key):06X}"


def normalize_hex(color: str) -> str:
    return key_to_hex(hex_to_key(color))


def key_to_rgb(key: int) -> tuple[int, int, int]:
    key = int(key)
    return (key >> 16) & 255, (key >> 8) & 255, key & 255


def rgb_to_key(red: int, green: int, blue: int) -> int:
    return (int(red) << 16) | (int(green) << 8) | int(blue)


# --------------------------------------------------------------------------------------
# sRGB <-> Oklab
# --------------------------------------------------------------------------------------


def _channel_to_linear(value: int) -> float:
    channel = value / 255
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


#: sRGB 8-bit channel -> linear light, one entry per channel value (the JS `linear` table).
SRGB_TO_LINEAR = np.array([_channel_to_linear(value) for value in range(256)], dtype=np.float64)


def keys_to_oklab(keys, dtype=np.float64, chunk: int = 1 << 20) -> np.ndarray:
    """Convert color keys to Oklab.

    Returns an array of shape (3, n): row 0 is lightness L, rows 1 and 2 are a and b.
    Work is done in chunks so converting millions of colors needs little extra memory.
    """
    keys = np.ascontiguousarray(keys, dtype=np.uint32).ravel()
    out = np.empty((3, keys.size), dtype=dtype)
    for start in range(0, keys.size, chunk):
        part = keys[start : start + chunk]
        stop = start + part.size
        r = SRGB_TO_LINEAR[part >> 16]
        g = SRGB_TO_LINEAR[(part >> 8) & 255]
        b = SRGB_TO_LINEAR[part & 255]
        l = np.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
        m = np.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
        s = np.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
        out[0, start:stop] = 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s
        out[1, start:stop] = 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s
        out[2, start:stop] = 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s
    return out


def key_to_oklab(key: int) -> tuple[float, float, float]:
    """Oklab (L, a, b) of one color, computed exactly like the vector version."""
    lab = keys_to_oklab(np.array([int(key)], dtype=np.uint32))
    return float(lab[0, 0]), float(lab[1, 0]), float(lab[2, 0])


def _linear_to_channel(value: float) -> int:
    value = min(1.0, max(0.0, value))
    encoded = 12.92 * value if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055
    return min(255, max(0, int(round(encoded * 255))))


def oklab_to_key(lightness: float, a: float, b: float) -> int:
    """Oklab -> nearest displayable sRGB color key (out-of-range channels are clipped)."""
    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_**3, m_**3, s_**3
    red = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    green = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    blue = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
    return rgb_to_key(_linear_to_channel(red), _linear_to_channel(green), _linear_to_channel(blue))


def mix_oklab(first: str, second: str, amount: float = 0.5) -> str:
    """Blend two HEX colors in Oklab. amount=0 gives `first`, amount=1 gives `second`."""
    l1, a1, b1 = key_to_oklab(hex_to_key(first))
    l2, a2, b2 = key_to_oklab(hex_to_key(second))
    return key_to_hex(
        oklab_to_key(l1 + (l2 - l1) * amount, a1 + (a2 - a1) * amount, b1 + (b2 - b1) * amount)
    )


def oklab_distance(first: str, second: str) -> float:
    """Perceptual distance between two HEX colors (0 = identical, about 1 = black to white)."""
    l1, a1, b1 = key_to_oklab(hex_to_key(first))
    l2, a2, b2 = key_to_oklab(hex_to_key(second))
    return math.sqrt((l1 - l2) ** 2 + (a1 - a2) ** 2 + (b1 - b2) ** 2)


# --------------------------------------------------------------------------------------
# HSL and the artist's color wheel
# --------------------------------------------------------------------------------------

#: Display hues of 12 equal steps around a painter's (red-yellow-blue) wheel, plus the wrap.
WHEEL_HUES = (0, 15, 30, 45, 60, 90, 120, 180, 240, 265, 285, 330, 360)


def js_round(value: float) -> int:
    """JavaScript's Math.round: halves round up (toward positive infinity)."""
    floor = math.floor(value)
    return floor + 1 if value - floor >= 0.5 else floor


def wrap_hue(hue: float) -> float:
    """Bring any angle into 0 <= hue < 360, the same way the JS `wrapHue` does."""
    return math.fmod(math.fmod(hue, 360) + 360, 360)


def to_hsl(color: str) -> tuple[float, float, float]:
    """HEX -> (hue in degrees, saturation 0..1, lightness 0..1).

    A gray has no hue of its own; like the original, it reports hue 220 (blue) with zero
    saturation so the color-wheel styles still have a hue to build from.
    """
    rgb = hex_to_key(color)
    r, g, b = (rgb >> 16) / 255, ((rgb >> 8) & 255) / 255, (rgb & 255) / 255
    high, low = max(r, g, b), min(r, g, b)
    lightness = (high + low) / 2
    delta = high - low
    if not delta:
        return 220.0, 0.0, lightness
    if high == r:
        sector = (g - b) / delta
    elif high == g:
        sector = 2 + (b - r) / delta
    else:
        sector = 4 + (r - g) / delta
    return wrap_hue(sector * 60), delta / (1 - abs(2 * lightness - 1)), lightness


def from_hsl(hue: float, saturation: float, lightness: float) -> str:
    """(hue, saturation, lightness) -> HEX."""
    factor = saturation * min(lightness, 1 - lightness)
    turn = wrap_hue(hue) / 30

    def channel(offset: int) -> str:
        position = math.fmod(offset + turn, 12)
        value = lightness - factor * max(-1, min(position - 3, 9 - position, 1))
        return f"{min(255, max(0, js_round(value * 255))):02X}"

    return "#" + channel(0) + channel(8) + channel(4)


def shift_on_wheel(hue: float, steps: int) -> float:
    """Move `steps` twelfths around the artist's wheel.

    Equal artist-wheel steps are mapped to display hues so that red pairs with green and
    blue with orange, the way painters expect.
    """
    segment = next(index for index, stop in enumerate(WHEEL_HUES) if stop > hue) - 1
    fraction = (hue - WHEEL_HUES[segment]) / (WHEEL_HUES[segment + 1] - WHEEL_HUES[segment])
    target = (segment + steps + 12) % 12
    return WHEEL_HUES[target] + fraction * (WHEEL_HUES[target + 1] - WHEEL_HUES[target])


# --------------------------------------------------------------------------------------
# Contrast helpers (used by the interface to keep text readable on any swatch)
# --------------------------------------------------------------------------------------


def relative_luminance(color: str) -> float:
    """WCAG relative luminance of a HEX color (0 = black, 1 = white)."""
    red, green, blue = key_to_rgb(hex_to_key(color))
    return float(
        0.2126 * SRGB_TO_LINEAR[red] + 0.7152 * SRGB_TO_LINEAR[green] + 0.0722 * SRGB_TO_LINEAR[blue]
    )


def contrast_ratio(first: str, second: str) -> float:
    """WCAG contrast ratio between two HEX colors (1 to 21)."""
    one, two = relative_luminance(first), relative_luminance(second)
    lighter, darker = max(one, two), min(one, two)
    return (lighter + 0.05) / (darker + 0.05)


def readable_text_color(background: str) -> str:
    """Black or white, whichever reads better on `background`."""
    return "#000000" if contrast_ratio(background, "#000000") >= contrast_ratio(background, "#FFFFFF") else "#FFFFFF"
