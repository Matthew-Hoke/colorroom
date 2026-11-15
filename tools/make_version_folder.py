"""Save one version of Colorroom FABLE as its own folder, with a launcher on top.

Run from the project folder, once per update:

    .venv\\Scripts\\python tools\\make_version_folder.py "2.1 - My palette and designer tools"
    .venv\\Scripts\\python tools\\make_version_folder.py "2.0 - first desktop app" --ref v2.0.0-alpha.1

It creates, next to the project folder:

    Colorroom FABLE versions\\
        <name>\\
            Launch Colorroom FABLE <number>.bat   start this version (the only file, so it is easy to find)
            app\\                                  the standalone app of this version
            source code\\                          the code exactly as it was for this version

The code comes from git (`--ref`, default: the current commit), so it is the committed
state, never half-finished edits. The app is built from that same code.
"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSIONS = ROOT.parent / "Colorroom FABLE versions"
APP_NAME = "Colorroom FABLE"

LAUNCHER = """@echo off
rem Starts {app} {number}. Keep this file next to the "app" folder.
cd /d "%~dp0"
if not exist "app\\{app}.exe" (
    echo The "app" folder of this version is missing or incomplete.
    pause
    exit /b 1
)
start "" "app\\{app}.exe" %*
"""


def git(*arguments: str) -> bytes:
    return subprocess.run(["git", "-C", str(ROOT), *arguments], check=True, capture_output=True).stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name", help='Folder name, starting with the version number, e.g. "2.1 - My palette"')
    parser.add_argument("--ref", default="HEAD", help="Git commit or tag to save (default: the current commit)")
    parser.add_argument("--replace", action="store_true", help="Overwrite the folder if it already exists")
    arguments = parser.parse_args()

    number = arguments.name.split()[0]
    target = VERSIONS / arguments.name
    if target.exists():
        if not arguments.replace:
            print(f"{target} already exists. Use --replace to rebuild it.")
            return 1
        shutil.rmtree(target)
    source = target / "source code"
    source.mkdir(parents=True)

    # 1. The code, exactly as committed.
    commit = git("rev-parse", "--short", arguments.ref).decode().strip()
    with tarfile.open(fileobj=io.BytesIO(git("archive", "--format=tar", arguments.ref))) as archive:
        archive.extractall(source, filter="data")
    print(f"Saved the code of {arguments.ref} ({commit}) to {source}")

    # 2. The standalone app, built from that code (not from whatever is being edited now).
    environment = dict(os.environ, PYTHONPATH=str(source / "src"), PYTHONIOENCODING="utf-8")
    build = subprocess.run([sys.executable, str(source / "tools" / "build_app.py")], cwd=source, env=environment)
    built = source / "dist" / APP_NAME
    if build.returncode != 0 or not (built / f"{APP_NAME}.exe").exists():
        print("The app of this version could not be built. The code was saved; the launcher was not created.")
        return 1
    shutil.move(str(built), str(target / "app"))
    for leftover in ("dist", "build", "build.log"):
        path = source / leftover
        shutil.rmtree(path, ignore_errors=True) if path.is_dir() else path.unlink(missing_ok=True)
    for spec in (source / "packaging").glob("*.spec"):
        spec.unlink()  # holds full paths from this computer
    for cache in source.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)

    # 3. The launcher, the only file at the top of the folder.
    launcher = target / f"Launch {APP_NAME} {number}.bat"
    launcher.write_text(LAUNCHER.format(app=APP_NAME, number=number), encoding="ascii", newline="\r\n")
    print(f"Done: {launcher}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
