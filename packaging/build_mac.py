#!/usr/bin/env python3
"""Build Veuly.app for a Mac: a small native window (packaging/mac/*.swift, built with swiftc) around the app's own
server, which the system's Python runs from inside the bundle.

  python3 packaging/build_mac.py                  build dist/Veuly.app
  python3 packaging/build_mac.py OUT              build OUT/Veuly.app
  python3 packaging/build_mac.py --install        build, then copy it to /Applications (only over a Veuly this script built)
  python3 packaging/build_mac.py --dmg            also make OUT/Veuly-mac.dmg, with a link to Applications
  python3 packaging/build_mac.py --standalone --whisper FILE [--python FOLDER]
                                                  a copy for other people: its own Python and whisper-cli inside, no Terminal needed
  ... --sign "Developer ID Application: NAME (TEAM)" [--notarize PROFILE]
                                                  signed with the hardened runtime; with --dmg, notarized through notarytool's saved profile

A Veuly.app already in OUT (or in /Applications) is replaced only when this script built it and it is not running:
never an older installer's copy, another app, or a Veuly in use.

Needs macOS 14.2 or later and Apple's Command Line Tools (swiftc, iconutil, codesign); no Xcode project. The app runs
app.py with /usr/bin/python3 when the Command Line Tools are there (otherwise Homebrew's or python.org's python3).
The bundle:

  Contents/MacOS/Veuly                    the window, the menus, this Mac's sound (packaging/mac/)
  Contents/Resources/app/                 app.py, transcapp/, web/ and, if packaging/bin has them, bin/ (whisper-cli...)
  Contents/Resources/AppIcon.icns         made from web/logo.svg with qlmanage, sips and iconutil
  Contents/Resources/<lang>.lproj/        the permission texts in English, Spanish and Catalan, from web/i18n.js
  Contents/Info.plist                     made by info_plist() below

Without --sign it is signed ad hoc (codesign -s -): it runs on this Mac; giving it to someone else needs a Developer ID (SIGNING.md).
--standalone adds Contents/Resources/python/ (a relocatable CPython: `uv python install 3.12` gives one), whisper-cli in
app/bin, bundle.json (the transcription model downloads by itself on the first start) and THIRD-PARTY.txt. It is built for this
Mac's own architecture only, because that Python and whisper-cli are.
Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCES = ROOT / "packaging" / "mac"
BUNDLE_ID = "app.veuly"
MIN_MACOS = "14.2"               # Core Audio process taps, the way the app records this Mac's sound
LANGS = ("en", "es", "ca")
BUILT_BY = "packaging/build_mac.py"   # in Info.plist: --install replaces only a copy that says this
# Info.plist key -> the text in web/i18n.js that explains it (macOS shows it when it asks the user)
PURPOSES = {
    "NSMicrophoneUsageDescription": "mac_purpose_mic",
    "NSAudioCaptureUsageDescription": "mac_purpose_audio",
    "NSDesktopFolderUsageDescription": "mac_purpose_desktop",
    "NSDownloadsFolderUsageDescription": "mac_purpose_downloads",
    "NSDocumentsFolderUsageDescription": "mac_purpose_documents",
    "NSRemovableVolumesUsageDescription": "mac_purpose_place",     # a linked folder on a drive, a network or in cloud storage
    "NSNetworkVolumesUsageDescription": "mac_purpose_place",
    "NSFileProviderDomainUsageDescription": "mac_purpose_place",
}
ICON_SIZES = (16, 32, 128, 256, 512)
ENTITLEMENTS = SOURCES / "veuly.entitlements"
# What a server with no window or Tk never imports, and the tests of the language itself: about a third of a CPython.
PYTHON_LEAVE_OUT = ("test", "tests", "idlelib", "tkinter", "turtledemo", "turtle.py", "ensurepip", "lib2to3", "venv", "pydoc_data",
                    "tcl[0-9]*", "tk[0-9]*", "itcl[0-9]*", "thread[0-9]*", "include", "share", "Tk.*", "libtcl*", "libtk*",
                    "libpython*",     # bin/python3.12 is linked statically, and the library names the folder it was built in
                    "_tkinter*", "2to3*", "idle3*", "pip*", "pydoc3*", "python3-config", "python3.*-config", "pkgconfig", "site-packages/*")
THIRD_PARTY = """Veuly for Mac includes:

Contents/Resources/python/   CPython (Python Software Foundation License), built by the python-build-standalone project. Its licence is python/lib/python3.*/LICENSE.txt.
Contents/Resources/app/bin/whisper-cli   whisper.cpp and ggml (MIT License, Copyright (c) The ggml authors), github.com/ggml-org/whisper.cpp. Its licence is app/bin/whisper-LICENSE.txt.
Contents/Resources/app/transcapp/_vendor/pypdf   pypdf (BSD 3-Clause License), github.com/py-pdf/pypdf.
Contents/Resources/app/web/vendor/   see web/vendor/LICENSES.txt (CodeMirror, Lezer, KaTeX).

The transcription model (whisper.cpp's large-v3-turbo, MIT License, made by OpenAI) is downloaded on the first start from huggingface.co/ggerganov/whisper.cpp, not included.
Claude Code and Codex are not included: they are other companies' programs, used only if you installed them yourself.
"""


def version() -> str:
    return re.search(r'VERSION = "([^"]+)"', (ROOT / "transcapp" / "server.py").read_text(encoding="utf-8")).group(1)


def js_string(body: str) -> str:
    """The value of a JavaScript string literal's body (what is between the quotes)."""
    out, i = [], 0
    simple = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
    while i < len(body):
        c = body[i]
        if c != "\\" or i + 1 == len(body):
            out.append(c)
            i += 1
            continue
        n = body[i + 1]
        if n == "u" and re.fullmatch(r"[0-9a-fA-F]{4}", body[i + 2:i + 6]):
            out.append(chr(int(body[i + 2:i + 6], 16)))
            i += 6
        elif n == "x" and re.fullmatch(r"[0-9a-fA-F]{2}", body[i + 2:i + 4]):
            out.append(chr(int(body[i + 2:i + 4], 16)))
            i += 4
        else:
            out.append(simple.get(n, n))
            i += 2
    return "".join(out)


def i18n_texts(keys, path: pathlib.Path = ROOT / "web" / "i18n.js") -> dict:
    """{lang: {key: text}} for `keys`, read from the language blocks of web/i18n.js."""
    text = path.read_text(encoding="utf-8")
    cut = [text.index(f"    {lang}: {{") for lang in LANGS] + [len(text)]
    found = {}
    for i, lang in enumerate(LANGS):
        block = text[cut[i]:cut[i + 1]]
        found[lang] = {}
        for key in keys:
            m = re.search(r"(?:^|[\s,{])" + re.escape(key) + r"""\s*:\s*(?:'((?:[^'\\\n]|\\.)*)'|"((?:[^"\\\n]|\\.)*)")""", block)
            if m:
                found[lang][key] = js_string(m.group(1) if m.group(1) is not None else m.group(2))
    return found


def info_plist(ver: str, bundle_id: str = BUNDLE_ID, texts: dict | None = None) -> dict:
    """What macOS reads about the app. No LSUIElement: it has a Dock icon and a menu bar of its own."""
    texts = texts or i18n_texts(PURPOSES.values())
    plist = {
        "CFBundleIdentifier": bundle_id,
        "CFBundleName": "Veuly",
        "CFBundleDisplayName": "Veuly",
        "CFBundleExecutable": "Veuly",
        "CFBundleIconFile": "AppIcon",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": ver,
        "CFBundleVersion": ver,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleDevelopmentRegion": "en",
        "CFBundleLocalizations": list(LANGS),
        "LSMinimumSystemVersion": MIN_MACOS,
        "LSApplicationCategoryType": "public.app-category.productivity",
        "NSPrincipalClass": "NSApplication",
        "NSHighResolutionCapable": True,
        "NSSupportsAutomaticTermination": False,
        "NSSupportsSuddenTermination": False,
        # The page comes from the app's own server on 127.0.0.1, over plain http.
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        "VeulyBuiltBy": BUILT_BY,
    }
    for key, text_key in PURPOSES.items():
        plist[key] = texts["en"][text_key]
    return plist


def strings_file(entries: dict) -> str:
    """A .strings file (InfoPlist.strings): "key" = "value";"""
    quote = lambda s: s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return "".join(f'"{k}" = "{quote(v)}";\n' for k, v in entries.items())


def run(*cmd, **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def compile_shell(target: pathlib.Path, arches: list[str], work: pathlib.Path) -> None:
    """swiftc for each architecture, joined with lipo when there is more than one."""
    sources = sorted(SOURCES.glob("*.swift"))
    parts = []
    for arch in arches:
        out = work / f"Veuly-{arch}"
        run("swiftc", "-O", "-swift-version", "5", "-target", f"{arch}-apple-macos{MIN_MACOS}", "-module-name", "Veuly",
            *sources, "-o", out)
        parts.append(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    if len(parts) == 1:
        shutil.copy2(parts[0], target)
    else:
        run("lipo", "-create", *parts, "-output", target)


def icon_svg(logo: str) -> str:
    """web/logo.svg on the macOS icon grid: its square drawn 824 px wide in a 1024 canvas, with a soft shadow."""
    m = re.search(r"<svg\b[^>]*viewBox=\"0 0 ([\d.]+) ([\d.]+)\"[^>]*>(.*)</svg>", logo, re.S)
    if not m:
        raise SystemExit("web/logo.svg has no viewBox to scale")
    scale = 824 / float(m.group(1))
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">'
            '<defs><filter id="shadow" x="-10%" y="-10%" width="120%" height="125%">'
            '<feDropShadow dx="0" dy="10" stdDeviation="12" flood-color="#000" flood-opacity="0.28"/></filter></defs>'
            f'<g filter="url(#shadow)"><g transform="translate(100 100) scale({scale:.4f})">{m.group(3)}</g></g></svg>')


def make_icon(target: pathlib.Path, work: pathlib.Path) -> None:
    """AppIcon.icns from web/logo.svg: qlmanage draws it at 1024 px (with transparency), sips scales, iconutil packs."""
    svg = work / "icon.svg"
    svg.write_text(icon_svg((ROOT / "web" / "logo.svg").read_text(encoding="utf-8")), encoding="utf-8")
    drawn = work / "drawn"
    drawn.mkdir(exist_ok=True)
    run("qlmanage", "-t", "-s", "1024", "-o", drawn, svg, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    big = drawn / "icon.svg.png"
    if not big.is_file():
        raise SystemExit("qlmanage did not draw the icon")
    iconset = work / "AppIcon.iconset"
    iconset.mkdir(exist_ok=True)
    for size in ICON_SIZES:
        for factor in (1, 2):
            px = size * factor
            name = f"icon_{size}x{size}{'@2x' if factor == 2 else ''}.png"
            run("sips", "-z", px, px, big, "--out", iconset / name, stdout=subprocess.DEVNULL)
    run("iconutil", "-c", "icns", iconset, "-o", target)


def copy_app_files(app_dir: pathlib.Path) -> None:
    """app.py, transcapp/ and web/ (and packaging/bin, when it has programs) go into Resources/app as they are."""
    skip = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")
    app_dir.mkdir(parents=True)
    shutil.copy2(ROOT / "app.py", app_dir / "app.py")
    shutil.copytree(ROOT / "transcapp", app_dir / "transcapp", ignore=skip)
    shutil.copytree(ROOT / "web", app_dir / "web", ignore=skip)
    programs = [f for f in (ROOT / "packaging" / "bin").glob("*") if f.is_file() and f.name != ".gitkeep" and f.suffix != ".html"]
    if programs:
        (app_dir / "bin").mkdir()
        for f in programs:
            shutil.copy2(f, app_dir / "bin" / f.name)


def layout(app: pathlib.Path, binary: pathlib.Path, icon: pathlib.Path | None, plist: dict, texts: dict) -> None:
    """The bundle's folders and files, with the shell already compiled into `binary`."""
    if app.exists():
        shutil.rmtree(app)
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    shutil.copy2(binary, contents / "MacOS" / "Veuly")
    os.chmod(contents / "MacOS" / "Veuly", 0o755)
    resources = contents / "Resources"
    copy_app_files(resources / "app")
    if icon:
        shutil.copy2(icon, resources / "AppIcon.icns")
    for lang in LANGS:
        folder = resources / f"{lang}.lproj"
        folder.mkdir()
        entries = {key: texts[lang].get(text_key) or texts["en"][text_key] for key, text_key in PURPOSES.items()}
        (folder / "InfoPlist.strings").write_text(strings_file(entries), encoding="utf-8")
    with open(contents / "Info.plist", "wb") as f:
        plistlib.dump(plist, f)
    (contents / "PkgInfo").write_text("APPL????", encoding="ascii")


def copy_python(source: pathlib.Path, target: pathlib.Path) -> None:
    """A relocatable CPython into the bundle, without what a server never imports. Its links stay links."""
    if not (source / "bin" / "python3").exists():
        raise SystemExit(f"{source} has no bin/python3: give the folder of a relocatable CPython (`uv python install 3.12`).")
    shutil.copytree(source, target, symlinks=True, ignore=shutil.ignore_patterns(*PYTHON_LEAVE_OUT, ".DS_Store"))


def add_standalone(app: pathlib.Path, python: pathlib.Path, whisper: pathlib.Path, seller: dict | None = None) -> None:
    """What makes a copy work on a Mac with nothing installed: its own Python, whisper-cli and the first start's download."""
    if not whisper.is_file() or not os.access(whisper, os.X_OK):
        raise SystemExit(f"{whisper} is not a program: give a whisper-cli built with no outside libraries.")
    linked = subprocess.run(["otool", "-L", str(whisper)], capture_output=True, text=True).stdout
    outside = [line.strip() for line in linked.splitlines()[1:] if not line.strip().startswith(("/usr/lib/", "/System/"))]
    if outside:
        raise SystemExit(f"{whisper} needs libraries other Macs do not have: {outside}. Build it with -DBUILD_SHARED_LIBS=OFF.")
    resources = app / "Contents" / "Resources"
    copy_python(python, resources / "python")
    programs = resources / "app" / "bin"
    programs.mkdir(exist_ok=True)
    shutil.copy2(whisper, programs / "whisper-cli")
    licence = whisper.with_name("whisper-LICENSE.txt")
    if not licence.is_file():
        raise SystemExit(f"{licence} is missing: the MIT licence has to go with the program.")
    shutil.copy2(licence, programs / "whisper-LICENSE.txt")
    (resources / "app" / "bundle.json").write_text(json.dumps({"auto_model": True, "version": version(), **(seller or {})}, indent=2), encoding="utf-8")
    (resources / "app" / "THIRD-PARTY.txt").write_text(THIRD_PARTY, encoding="utf-8")


def is_macho(path: pathlib.Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca")
    except OSError:
        return False


def sign(app: pathlib.Path, identity: str = "-") -> None:
    """Every program and library inside first, the app last, as codesign needs. A real identity gets the hardened runtime,
    which notarization requires, and the microphone's entitlement; ad hoc stays as it was, for this Mac only."""
    real = identity != "-"
    options = ["--force", "--sign", identity] + (["--options", "runtime", "--timestamp"] if real else ["--timestamp=none"])
    inside = [f for f in (app / "Contents" / "Resources").rglob("*") if f.is_file() and not f.is_symlink() and is_macho(f)]
    for f in sorted(inside, key=lambda f: len(f.parts), reverse=True):
        run("codesign", *options, f, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    run("codesign", *options, *(["--entitlements", ENTITLEMENTS] if real else []), app)
    run("codesign", "--verify", "--strict", "--deep", app)


def notarize(dmg: pathlib.Path, profile: str) -> None:
    """Apple checks the image and the ticket is stapled to it, so a new Mac opens it with no warning, even offline."""
    run("xcrun", "notarytool", "submit", dmg, "--keychain-profile", profile, "--wait")
    run("xcrun", "stapler", "staple", dmg)


def built_by_us(app: pathlib.Path, ids=(BUNDLE_ID,)) -> bool:
    """Whether `app` is a Veuly this script built (its Info.plist says so, with one of `ids`): the only kind a build
    or --install replaces."""
    try:
        with open(app / "Contents" / "Info.plist", "rb") as f:
            info = plistlib.load(f)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return False
    return info.get("CFBundleIdentifier") in ids and info.get("VeulyBuiltBy") == BUILT_BY


def is_running(app: pathlib.Path) -> bool:
    binary = str(app / "Contents" / "MacOS" / "Veuly")
    # The program itself, not `pgrep -f`: that also finds any process with this path among its arguments, such as a shell or an
    # agent's command line, and then refuses to install while nothing is running.
    programs = subprocess.run(["ps", "-axo", "comm="], capture_output=True, text=True).stdout.splitlines()
    return binary in (line.strip() for line in programs)


def check_replaceable(app: pathlib.Path, ids=(BUNDLE_ID,), again: str = "run this again") -> None:
    """Refuses (SystemExit) to replace `app` unless this script built it and it is not running."""
    if not (app.exists() or app.is_symlink()):
        return
    if not built_by_us(app, ids):
        raise SystemExit(f"{app} is not a Veuly this script built (an older installer's copy, or another app). "
                         f"Move it to the Trash yourself, then {again}.")
    if is_running(app):
        raise SystemExit(f"Veuly is running from {app}: quit it first (Veuly menu > Quit Veuly), then {again}.")


def install(app: pathlib.Path, target: pathlib.Path = pathlib.Path("/Applications/Veuly.app")) -> None:
    """Copies the new build over an older one in /Applications, never over anything else."""
    check_replaceable(target, again="run --install again")
    staged = target.with_name(".Veuly.app.new")
    if staged.exists():
        shutil.rmtree(staged)
    run("ditto", app, staged)
    if target.exists():
        old = target.with_name(".Veuly.app.old")
        if old.exists():
            shutil.rmtree(old)
        target.rename(old)
        staged.rename(target)
        shutil.rmtree(old)
    else:
        staged.rename(target)
    lsregister = pathlib.Path("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister")
    if target.parent == pathlib.Path("/Applications") and lsregister.exists():     # Finder and the Dock see the new icon now
        subprocess.run([str(lsregister), "-f", str(target)], capture_output=True)
    print(f"Installed {target}")


def make_dmg(app: pathlib.Path, out: pathlib.Path, work: pathlib.Path) -> pathlib.Path:
    """A disk image with Veuly.app and a link to Applications, so it can be dragged across."""
    staging = work / "dmg"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    run("ditto", app, staging / "Veuly.app")
    os.symlink("/Applications", staging / "Applications")
    dmg = out / "Veuly-mac.dmg"
    run("hdiutil", "create", "-volname", "Veuly", "-srcfolder", staging, "-ov", "-format", "UDZO", dmg, stdout=subprocess.DEVNULL)
    return dmg


def build(out: pathlib.Path, arches: list[str], bundle_id: str = BUNDLE_ID, python: pathlib.Path | None = None,
          whisper: pathlib.Path | None = None, identity: str = "-", seller: dict | None = None) -> pathlib.Path:
    if platform.system() != "Darwin":
        raise SystemExit("The Mac app is built on a Mac.")
    if not shutil.which("swiftc"):
        raise SystemExit("swiftc is missing: install Apple's Command Line Tools (xcode-select --install).")
    app = out / "Veuly.app"
    check_replaceable(app, {BUNDLE_ID, bundle_id}, "build again (or into another folder)")      # before anything is built
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="veuly-build-") as tmp:
        work = pathlib.Path(tmp)
        binary = work / "Veuly"
        compile_shell(binary, arches, work)
        icon = work / "AppIcon.icns"
        make_icon(icon, work)
        texts = i18n_texts(PURPOSES.values())
        missing = [f"{lang}:{key}" for lang in LANGS for key in PURPOSES.values() if not texts[lang].get(key)]
        if missing:
            raise SystemExit(f"web/i18n.js lacks the permission texts {missing}")
        layout(app, binary, icon, info_plist(version(), bundle_id, texts), texts)
    if python:
        add_standalone(app, python, whisper, seller)
    sign(app, identity)
    print(f"Built {app} ({version()}, {'+'.join(arches)}, {bundle_id})")
    return app


def default_python() -> pathlib.Path:
    found = sorted(pathlib.Path.home().glob(".local/share/uv/python/cpython-3.12.*-macos-*-none"))
    found = [f for f in found if not f.is_symlink()]
    if not found:
        raise SystemExit("No relocatable CPython: run `uv python install 3.12`, or give --python.")
    return found[-1]


def main() -> None:
    ap = argparse.ArgumentParser(description="Build Veuly.app for a Mac.")
    ap.add_argument("out", nargs="?", default=str(ROOT / "dist"), help="folder for Veuly.app (default dist/)")
    ap.add_argument("--install", action="store_true", help="then copy it to /Applications (only over a Veuly this script built)")
    ap.add_argument("--dmg", action="store_true", help="also make Veuly-mac.dmg with a link to Applications")
    ap.add_argument("--arch", choices=("universal", "native"), default="universal",
                    help="universal: Apple silicon and Intel (default); native: this Mac's only, faster")
    ap.add_argument("--standalone", action="store_true", help="a copy for other people: its own Python and whisper-cli inside")
    ap.add_argument("--python", help="with --standalone: the folder of a relocatable CPython (default: uv's 3.12)")
    ap.add_argument("--whisper", help="with --standalone: a whisper-cli with no outside libraries, whisper-LICENSE.txt beside it")
    ap.add_argument("--update-url", help="with --standalone: https address of the JSON file that names the latest version")
    ap.add_argument("--sign", default="-", help="a Developer ID Application identity (default: ad hoc, this Mac only)")
    ap.add_argument("--notarize", metavar="PROFILE", help="with --dmg and --sign: notarytool's profile saved with store-credentials")
    ap.add_argument("--bundle-id", default=BUNDLE_ID, help=argparse.SUPPRESS)      # tests build a copy apart from the real one
    args = ap.parse_args()
    arches = ["arm64", "x86_64"] if args.arch == "universal" else [platform.machine()]
    if args.install and args.bundle_id != BUNDLE_ID:
        raise SystemExit("--install takes the real app only.")
    if args.notarize and (args.sign == "-" or not args.dmg):
        raise SystemExit("--notarize needs --dmg and a real --sign identity.")
    python, whisper = None, None
    if args.standalone:
        if not args.whisper:
            raise SystemExit("--standalone needs --whisper: a whisper-cli with no outside libraries.")
        arches = [platform.machine()]
        whisper = pathlib.Path(args.whisper).resolve()
        python = pathlib.Path(args.python).expanduser() if args.python else default_python()
    out = pathlib.Path(args.out).resolve()
    seller = {"update_url": args.update_url} if args.update_url else {}
    app = build(out, arches, args.bundle_id, python, whisper, args.sign, seller)
    if args.dmg:
        with tempfile.TemporaryDirectory(prefix="veuly-dmg-") as tmp:
            dmg = make_dmg(app, out, pathlib.Path(tmp))
        if args.sign != "-":
            run("codesign", "--force", "--sign", args.sign, "--timestamp", dmg)
        print(f"Made {dmg}")
        if args.notarize:
            notarize(dmg, args.notarize)
    if args.install:
        install(app)


if __name__ == "__main__":
    main()
