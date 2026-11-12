"""Build the standalone Windows app with PyInstaller.

Run from the project folder:   .venv\\Scripts\\python tools\\build_app.py

Result:  dist\\Colorroom FABLE\\Colorroom FABLE.exe   (a folder you can copy or zip;
the .exe needs the files next to it). No Python installation is needed to run it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"
APP_NAME = "Colorroom FABLE"
MARK_COLORS = ("#eec586", "#a58bd8", "#6448ae", "#33244d")


def brand_mark(size: int) -> Image.Image:
    """The Colorroom mark (rounded square in four colored quarters), drawn with Pillow."""
    scale = 4
    big = size * scale
    center = big / 2
    quarters = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(quarters)
    reach = big  # large enough for the quarter circles to cover the whole square
    for position, color in enumerate(MARK_COLORS):
        start = -55 + 90 * position  # 35 degrees past 12 o'clock, going clockwise
        draw.pieslice([center - reach, center - reach, center + reach, center + reach], start, start + 90, fill=color)
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, big - 1, big - 1], radius=big * 0.3, fill=255)
    quarters.putalpha(mask)
    return quarters.resize((size, size), Image.Resampling.LANCZOS)


def make_icon() -> Path:
    PACKAGING.mkdir(exist_ok=True)
    target = PACKAGING / "colorroom.ico"
    sizes = [16, 24, 32, 48, 64, 128, 256]
    brand_mark(256).save(target, format="ICO", sizes=[(size, size) for size in sizes])
    brand_mark(512).save(PACKAGING / "colorroom.png")
    return target


def main() -> int:
    icon = make_icon()
    entry = PACKAGING / "launch.py"
    entry.write_text(
        '"""Entry point used by PyInstaller."""\n\n'
        "import sys\n\n"
        "from colorroom.__main__ import main\n\n"
        'if __name__ == "__main__":\n'
        "    sys.exit(main())\n",
        encoding="utf-8",
    )
    for folder in (ROOT / "build", ROOT / "dist" / APP_NAME):
        shutil.rmtree(folder, ignore_errors=True)
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--windowed",
        "--name", APP_NAME,
        "--icon", str(icon),
        "--paths", str(ROOT / "src"),
        "--hidden-import", "PySide6.QtSvg",
        "--exclude-module", "tkinter",
        "--exclude-module", "pytest",
        "--specpath", str(PACKAGING),
        "--workpath", str(ROOT / "build"),
        "--distpath", str(ROOT / "dist"),
        str(entry),
    ]  # fmt: skip
    print("Running PyInstaller. This takes a few minutes…")
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        print("The build failed. See the messages above.")
        return result.returncode
    app = ROOT / "dist" / APP_NAME / f"{APP_NAME}.exe"
    removed = prune(app.parent)
    print(f"Removed {removed:.0f} MB of Qt parts the app does not use.")

    # Prove the packaged app starts: it draws its window into an image and exits.
    proof = ROOT / "build" / "packaged-app.png"
    proof.unlink(missing_ok=True)
    check = subprocess.run([str(app), "--screenshot", str(proof)], cwd=ROOT, timeout=180)
    if check.returncode != 0 or not proof.exists():
        print("The packaged app did not pass its start-up check.")
        return 1
    megabytes = sum(path.stat().st_size for path in app.parent.rglob("*") if path.is_file()) / 1e6
    print(f"\nBuilt and checked {app}\nFolder size: {megabytes:.0f} MB")
    return 0


#: Files PyInstaller bundles because some Qt plugin links to them, although a plain
#: widgets app never loads them: 3D software rendering, QML/Quick, PDF, the on-screen
#: keyboard, networking and the translations of Qt's own dialogs.
UNUSED = (
    "PySide6/opengl32sw.dll",
    "PySide6/Qt6Quick*.dll",
    "PySide6/Qt6Qml*.dll",
    "PySide6/Qt6Pdf*.dll",
    "PySide6/Qt6VirtualKeyboard*.dll",
    "PySide6/Qt6Network.dll",
    "PySide6/QtNetwork.pyd",
    "PySide6/Qt6OpenGL.dll",
    "PySide6/translations",
    "PySide6/plugins/imageformats/qpdf.dll",
    "PySide6/plugins/platforminputcontexts",
    "PySide6/plugins/tls",
    "PySide6/plugins/networkinformation",
    "PySide6/plugins/generic",
)


def prune(folder: Path) -> float:
    """Delete the unused files from the built app. Returns the megabytes saved."""
    saved = 0
    internal = folder / "_internal"
    for pattern in UNUSED:
        for path in internal.glob(pattern):
            if path.is_dir():
                saved += sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
                shutil.rmtree(path)
            else:
                saved += path.stat().st_size
                path.unlink()
    return saved / 1e6


if __name__ == "__main__":
    sys.exit(main())
