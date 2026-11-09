# PyInstaller recipe for Veuly. Use packaging/build.py, which calls this.
# The result opens no window of its own: it starts the local server and the browser.
import pathlib
import re
import sys

ROOT = pathlib.Path(SPECPATH).parent
VERSION = re.search(r'VERSION = "([^"]+)"', (ROOT / "transcapp" / "server.py").read_text()).group(1)

datas = [(str(ROOT / "web"), "web")]
# Whatever is in packaging/bin (whisper-cli and its libraries) ships inside the app.
bin_dir = ROOT / "packaging" / "bin"
binaries = [(str(f), "bin") for f in bin_dir.glob("*") if f.is_file() and f.name != ".gitkeep"] if bin_dir.is_dir() else []

# pypdf lives in transcapp/_vendor; naming it here makes PyInstaller pack it and what it needs.
a = Analysis([str(ROOT / "app.py")], pathex=[str(ROOT), str(ROOT / "transcapp" / "_vendor")], datas=datas, binaries=binaries,
             hiddenimports=["pypdf", "typing_extensions"],
             # optional extras of pypdf that the app never uses; without this they bloat the build
             excludes=["PIL", "cryptography", "Crypto", "numpy", "fontTools", "tkinter"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Veuly", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="Veuly")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="Veuly.app", bundle_identifier="app.veuly", version=VERSION,
                 info_plist={"LSUIElement": True,          # no Dock icon: the interface is the browser tab
                             "CFBundleShortVersionString": VERSION,
                             "NSHighResolutionCapable": True})
