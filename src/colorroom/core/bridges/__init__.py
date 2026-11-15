"""Links between Colorroom and the person's own design programs (route A and friends).

Made by Opus 5.5 (2026-11-14). `base` holds what every bridge shares; each program has its
own module (`photoshop_win`). Nothing here imports pywin32 or any window code until a bridge
call needs it, so importing this package is always safe.
"""

from .base import AppBusy, ApplyReport, Bridge, BridgeError, NotAvailable, normalize_mapping, output_path


def photoshop(**options):
    """The Photoshop bridge for this computer (Windows: COM)."""
    from .photoshop_win import PhotoshopBridge

    return PhotoshopBridge(**options)


def illustrator(**options):
    """The Illustrator bridge for this computer (Windows: COM)."""
    from .illustrator_win import IllustratorBridge

    return IllustratorBridge(**options)


def indesign(**options):
    """The InDesign bridge for this computer (Windows: COM)."""
    from .indesign_win import IndesignBridge

    return IndesignBridge(**options)


#: Which bridge writes each file format back (file extension -> factory).
FOR_FORMAT = {".psd": photoshop, ".psb": photoshop, ".ai": illustrator, ".indd": indesign, ".idml": indesign}


__all__ = [
    "AppBusy", "ApplyReport", "Bridge", "BridgeError", "FOR_FORMAT", "NotAvailable",
    "illustrator", "indesign", "normalize_mapping", "output_path", "photoshop",
]  # fmt: skip
