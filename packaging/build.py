#!/usr/bin/env python3
"""Build the installable app for the system this runs on.

  python3 packaging/build.py            build, then start the result once to check it answers
  python3 packaging/build.py --no-test  build only

Needs `pip install pyinstaller`. Output in dist/:
  Mac      Veuly.app and Veuly-mac-<arm64|x64>.dmg
  Windows  Veuly-Setup.exe if Inno Setup (iscc) is installed, otherwise Veuly-windows.zip
  Linux    Veuly-linux.tar.gz (for testing the recipe; not a supported system)

To ship the transcriber inside the app, put whisper-cli (and any library it needs) in packaging/bin
before building. The GitHub workflow does that. A build cannot be made for another system:
build the Mac app on a Mac and the Windows one on Windows, or let the workflow do both.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import platform
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"


def run(*cmd: str) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)


def smoke_test(exe: pathlib.Path) -> None:
    """Start the built app without a browser and check the server and the interface files answer."""
    import os
    import tempfile
    env = dict(os.environ, TRANSCAPP_HOME=tempfile.mkdtemp(prefix="transcapp-smoke-"))
    port = "48731"
    subprocess.run([str(exe), "--check"], env=env, check=True, timeout=120)     # PDF reader, editor and fonts are inside
    proc = subprocess.Popen([str(exe), "--no-open", "--port", port], env=env)
    try:
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=2) as r:
                    info = json.load(r)
                break
            except OSError:
                time.sleep(0.5)
        else:
            sys.exit("The built app did not start.")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/app.js", timeout=5) as r:
            assert b"renderMarkdown" in r.read(), "the interface files are missing from the build"
        print(f"The built app answers: {info}")
    finally:
        proc.terminate()


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the installable Veuly.")
    ap.add_argument("--no-test", action="store_true")
    args = ap.parse_args()
    system = platform.system()
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    run(sys.executable, "-m", "PyInstaller", "packaging/transcapp.spec", "--noconfirm",
        "--distpath", "dist", "--workpath", "build")

    if system == "Darwin":
        exe = DIST / "Veuly.app" / "Contents" / "MacOS" / "Veuly"
        out = DIST / f"Veuly-mac-{arch}.dmg"
        run("hdiutil", "create", "-volname", "Veuly", "-srcfolder", "dist/Veuly.app", "-ov", "-format", "UDZO", str(out))
    elif system == "Windows":
        exe = DIST / "Veuly" / "Veuly.exe"
        iscc = shutil.which("iscc") or shutil.which("ISCC")
        default = pathlib.Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe")
        if not iscc and default.exists():
            iscc = str(default)
        if iscc:
            run(iscc, "packaging/installer.iss")
            out = DIST / "Veuly-Setup.exe"
        else:
            out = pathlib.Path(shutil.make_archive(str(DIST / "Veuly-windows"), "zip", DIST, "Veuly"))
            print("Inno Setup was not found: made a zip instead of an installer.")
    else:
        exe = DIST / "Veuly" / "Veuly"
        out = pathlib.Path(shutil.make_archive(str(DIST / "Veuly-linux"), "gztar", DIST, "Veuly"))
    if not args.no_test:
        smoke_test(exe)
    print(f"Built: {out}  ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
