#!/usr/bin/env python3
"""Build the Windows installer from any system (a Mac, Linux or Windows): dist/Veuly-Setup.exe.

  python3 packaging/build_windows.py           build it
  python3 packaging/build_windows.py --zip     also make dist/Veuly-windows.zip (unzip, double-click Veuly.bat)

Why this exists next to build.py: PyInstaller can only build for the system it runs on, so build.py
needs a Windows machine for the Windows app. The app is plain Python with no dependencies, so this
script instead puts a ready-made Windows Python beside the source, adds the transcriber, and wraps
the folder with NSIS, which runs everywhere.

Needs `makensis` (Mac: `brew install makensis`; Ubuntu: `apt install nsis`). Without it only the zip is made.

What goes inside (downloaded once into build/win-cache/, checked against the hashes below):
  python/      CPython for Windows from python-build-standalone, trimmed (no tkinter, tests, pip)
  bin/         whisper-cli.exe and its libraries from the whisper.cpp release, plus the two Microsoft
               runtime libraries it needs (msvcp140.dll, vcomp140.dll), which a fresh Windows may lack;
               ffmpeg.exe (gyan.dev "essentials" build, GPLv3), so mp3, m4a and video files can be imported
  app.py, transcapp/, web/
  bundle.json  tells the app it came from this installer: on the first start it downloads the
               transcription model by itself (see App.first_run in transcapp/server.py)
The transcription model (570 MB) is not inside: the app fetches it on the first start.

To move to newer versions change URL and SHA256 together in PARTS.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / "build" / "win-cache"
STAGE = ROOT / "build" / "win" / "Veuly"
DIST = ROOT / "dist"

PARTS = {
    "python": ("cpython-3.12.15-win64.tar.gz",
               "https://github.com/astral-sh/python-build-standalone/releases/download/20261003/"
               "cpython-3.12.15%2B20261003-x86_64-pc-windows-msvc-install_only_stripped.tar.gz",
               "6fba7f2ae506facf41d457ea8293c7497910a675c69a4e954875169410a50402"),
    "whisper": ("whisper-bin-x64.zip",            # build b5130 = whisper.cpp v1.9.4
                "https://github.com/ggml-org/whisper.cpp/releases/download/b5130/whisper-bin-x64.zip",
                "f9ec6c52a2e949b62ab51fa21d0d497958f9e41c3010c157c4e42932d5316f3c"),
    "msvc": ("msvc_runtime-14.44.35112-cp312-cp312-win_amd64.whl",
             "https://files.pythonhosted.org/packages/21/3b/134d04268ab8e35853cd007582076429b45d60d6abb1036d159be9c50342/"
             "msvc_runtime-14.44.35112-cp312-cp312-win_amd64.whl",
             "32f9c706009e16ccc319d6947ce3bffe20e5192bee52b18cf48313f9e7bedfbe"),
    "ffmpeg": ("ffmpeg-9.0.2-essentials_build.zip",
               "https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip",
               "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba"),
}
# Parts of the Python download the app never uses (it has no window of its own and installs nothing).
PYTHON_DROP = ["tcl", "include", "libs", "Scripts", "Lib/test", "Lib/tkinter", "Lib/idlelib", "Lib/ensurepip",
               "Lib/turtledemo", "Lib/lib2to3", "Lib/venv", "Lib/pydoc_data", "Lib/site-packages",
               "DLLs/_tkinter.pyd", "DLLs/tcl86t.dll", "DLLs/tk86t.dll"]
WHISPER_KEEP = re.compile(r"^(whisper-cli\.exe|whisper\.dll|ggml(-base|-cpu-[\w-]+)?\.dll)$")
MSVC_KEEP = ("msvcp140.dll", "vcomp140.dll")


def fetch(name: str) -> pathlib.Path:
    file, url, digest = PARTS[name]
    dest = CACHE / file
    if not dest.exists():
        print(f"Downloading {file}", flush=True)
        CACHE.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=120) as r, open(dest, "wb") as out:
            shutil.copyfileobj(r, out)
    with open(dest, "rb") as f:
        got = hashlib.file_digest(f, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else hashlib.sha256(f.read()).hexdigest()
    if got != digest:
        sys.exit(f"{dest} is not the file this script expects (sha256 {got}). Delete it and run again.")
    return dest


def assemble() -> dict:
    version = re.search(r'VERSION = "([^"]+)"', (ROOT / "transcapp" / "server.py").read_text(encoding="utf-8")).group(1)
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)

    with tarfile.open(fetch("python")) as tar:              # one top folder: python/
        try:
            tar.extractall(STAGE, filter="data")
        except TypeError:                                   # a Python from before the filter existed
            tar.extractall(STAGE)
    py = STAGE / "python"
    for rel in PYTHON_DROP:
        target = py / rel
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    for cache in list(py.rglob("__pycache__")):
        shutil.rmtree(cache)
    # The same program under the app's name: Task Manager and the installer (which stops a running copy) see "Veuly.exe".
    shutil.copyfile(py / "pythonw.exe", py / "Veuly.exe")

    (STAGE / "bin").mkdir()
    with zipfile.ZipFile(fetch("whisper")) as z:
        for item in z.infolist():
            name = item.filename.rsplit("/", 1)[-1]
            if WHISPER_KEEP.match(name):
                (STAGE / "bin" / name).write_bytes(z.read(item))
    with zipfile.ZipFile(fetch("msvc")) as z:
        for item in z.infolist():
            if item.filename.endswith(tuple(f".data/data/{n}" for n in MSVC_KEEP)):
                (STAGE / "bin" / item.filename.rsplit("/", 1)[-1]).write_bytes(z.read(item))
    with zipfile.ZipFile(fetch("ffmpeg")) as z:               # only the converter: ffplay and ffprobe are not used
        for item in z.infolist():
            if item.filename.endswith("/bin/ffmpeg.exe"):
                with z.open(item) as src, open(STAGE / "bin" / "ffmpeg.exe", "wb") as out:
                    shutil.copyfileobj(src, out)
            elif item.filename.count("/") == 1 and item.filename.endswith("/LICENSE"):
                (STAGE / "bin" / "ffmpeg-LICENSE.txt").write_bytes(z.read(item))
    for needed in ("whisper-cli.exe", "whisper.dll", "ggml.dll", "ggml-base.dll", "ggml-cpu-x64.dll", "ffmpeg.exe", *MSVC_KEEP):
        if not (STAGE / "bin" / needed).is_file():
            sys.exit(f"{needed} did not come out of the downloads: check WHISPER_KEEP / MSVC_KEEP against the new archive.")

    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")
    shutil.copytree(ROOT / "transcapp", STAGE / "transcapp", ignore=ignore)
    shutil.copytree(ROOT / "web", STAGE / "web", ignore=ignore)
    shutil.copyfile(ROOT / "app.py", STAGE / "app.py")
    shutil.copyfile(ROOT / "packaging" / "veuly.ico", STAGE / "veuly.ico")
    info = {"auto_model": True, "version": version, "built": datetime.date.today().isoformat(),
            "python": PARTS["python"][0], "whisper": PARTS["whisper"][1].split("/")[-2],
            "ffmpeg": PARTS["ffmpeg"][1].split("/")[-2]}
    (STAGE / "bundle.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    crlf = lambda text: text.replace("\n", "\r\n")
    (STAGE / "Veuly.bat").write_text(crlf('@echo off\nstart "" "%~dp0python\\Veuly.exe" "%~dp0app.py"\n'), encoding="ascii")
    # For when it does not start: the same app with a window that shows what it says.
    (STAGE / "Veuly-debug.bat").write_text(crlf('@echo off\n"%~dp0python\\python.exe" "%~dp0app.py"\npause\n'), encoding="ascii")
    (STAGE / "THIRD-PARTY.txt").write_text(crlf(
        "Veuly for Windows includes:\n\n"
        "python\\   CPython (Python Software Foundation License), built by the python-build-standalone project. See python\\LICENSE.txt.\n"
        "bin\\      whisper.cpp and ggml (MIT License, Copyright (c) The ggml authors), github.com/ggml-org/whisper.cpp.\n"
        "bin\\ffmpeg.exe   FFmpeg, the \"essentials\" build 9.0.2 from gyan.dev, under the GNU General Public License version 3 "
        "(bin\\ffmpeg-LICENSE.txt). A separate program that Veuly starts to convert imported audio. "
        "Source: github.com/GyanD/codexffmpeg and ffmpeg.org.\n"
        "bin\\msvcp140.dll, bin\\vcomp140.dll   Microsoft Visual C++ Redistributable runtime libraries (Microsoft Software License Terms).\n"
        "web\\vendor\\   see web\\vendor\\LICENSES.txt.\n"), encoding="utf-8")
    return info


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the Windows installer of Veuly from any system.")
    ap.add_argument("--zip", action="store_true", help="also make a zip of the app folder")
    args = ap.parse_args()
    info = assemble()
    size = sum(f.stat().st_size for f in STAGE.rglob("*") if f.is_file())
    print(f"App folder: {STAGE}  ({size / 1e6:.0f} MB, {sum(1 for _ in STAGE.rglob('*'))} entries)")
    DIST.mkdir(exist_ok=True)
    makensis = shutil.which("makensis")
    if makensis:
        subprocess.run([makensis, "-V2", f"-DVERSION={info['version']}", f"-DSIZE_KB={size // 1024}",
                        str(ROOT / "packaging" / "windows.nsi")], check=True, cwd=ROOT)
        out = DIST / "Veuly-Setup.exe"
        print(f"Built: {out}  ({out.stat().st_size / 1e6:.1f} MB)")
    else:
        print("makensis was not found (Mac: brew install makensis): making the zip only.")
    if args.zip or not makensis:
        out = pathlib.Path(shutil.make_archive(str(DIST / "Veuly-windows"), "zip", STAGE.parent, "Veuly"))
        print(f"Built: {out}  ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
