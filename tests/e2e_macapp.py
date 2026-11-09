"""The Mac app (packaging/mac/, built by packaging/build_mac.py), driven through its test mode on a temporary library.

  python3 tests/e2e_macapp.py           (VEULY_SEED=<path to a seed.py> fills the library with a fuller demo one)

Skips (exit 0) unless this is a Mac with swiftc. The app is built into a temporary folder with the bundle id
app.veuly.test, so the real app's settings, WebKit storage and permissions are never touched, and started from its
binary with VEULY_TEST_TOKEN: no Dock icon, no focus taken, its window off screen, a control port on 127.0.0.1
(VEULY_MACAPP_PORT, 58280 by default, and the next one; its server 10 and 11 above). Nothing here can ask for a permission: this Mac's sound is the app's stand-in tone
and the microphone is never opened (where a test needs one, the page's getUserMedia is replaced by a tone of its own,
STAND_IN_MIC); pictures that add themselves stay off. Nothing here spends Claude usage: the library's engine setting is
empty and HOME is a temporary folder, so the server finds no Claude Code either; the transcriber, where one runs, is a
stand-in that only sleeps. It never runs `open`: links to other sites and apps are written down by the app, downloads
and prints land in the temporary folder. WebKit sees the off-screen window as hidden, so every recording here is one
made with the window hidden.
"""
import array, base64, json, os, pathlib, plistlib, secrets, shutil, signal, struct, subprocess, sys, tempfile, time, urllib.request, wave, zlib

# A stand-in microphone for the page: a 220 Hz tone from an AudioContext of its own, as getUserMedia's stream. It never
# touches a capture device, so nothing asks for a permission; it plays nothing itself (as a real microphone does not).
STAND_IN_MIC = """
  navigator.mediaDevices.getUserMedia = async () => {
    const c = new AudioContext(), o = c.createOscillator(), g = c.createGain(), d = c.createMediaStreamDestination();
    o.frequency.value = 220; g.gain.value = 0.2; o.connect(g).connect(d); o.start(); await c.resume();
    window.standInMics = (window.standInMics || 0) + 1;
    return d.stream;
  };"""

ROOT = pathlib.Path(__file__).resolve().parent.parent
if sys.platform != "darwin" or not shutil.which("swiftc"):
    print("SKIP: the Mac app is built and run on a Mac with swiftc (Command Line Tools)")
    sys.exit(0)

# VEULY_MACAPP_PORT (default 58280) and the ports after it: two copies' control ports, the server's, and the one a server
# started again moves to when its own was taken (tests/test_macapp.py takes +16 to +19).
PORT_BASE = int(os.environ.get("VEULY_MACAPP_PORT") or 58280)
CONTROL_A, CONTROL_B, SERVER_PORT, MOVED_PORT = PORT_BASE, PORT_BASE + 1, PORT_BASE + 10, PORT_BASE + 11
TOKEN = secrets.token_urlsafe(32)
WORK = pathlib.Path(tempfile.mkdtemp(prefix="veuly-macapp-"))
APP = WORK / "build" / "Veuly.app"
ok = True


def check(label, value, extra=""):
    global ok
    ok = ok and bool(value)
    print(("ok   " if value else "FAIL ") + label, extra if not value else "", flush=True)


# ------------------------------------------------------------------ build
built = subprocess.run([sys.executable, "-B", str(ROOT / "packaging" / "build_mac.py"), str(WORK / "build"), "--arch", "native",
                        "--bundle-id", "app.veuly.test"], capture_output=True, text=True)
check("the app builds", built.returncode == 0, built.stdout[-2000:] + built.stderr[-2000:])
if built.returncode:
    sys.exit(1)
info = plistlib.loads((APP / "Contents" / "Info.plist").read_bytes())
check("Info.plist: identifier, version, minimum macOS, no LSUIElement", info["CFBundleIdentifier"] == "app.veuly.test"
      and info["CFBundleShortVersionString"] and info["LSMinimumSystemVersion"] == "14.2" and "LSUIElement" not in info, info)
check("Info.plist: the microphone and this Mac's sound are explained", "microphone" in info["NSMicrophoneUsageDescription"]
      and "Teams" in info["NSAudioCaptureUsageDescription"])
check("the bundle has its icon, the server and the page", (APP / "Contents/Resources/AppIcon.icns").stat().st_size > 10000
      and (APP / "Contents/Resources/app/app.py").is_file() and (APP / "Contents/Resources/app/web/native.js").is_file())
check("its signature verifies", subprocess.run(["codesign", "--verify", "--strict", str(APP)], capture_output=True).returncode == 0)

# ------------------------------------------------------------------ a library to open
HOME = WORK / "veuly"
FAKE_HOME = WORK / "home"           # the server's HOME: no Claude Code, no whisper model of the user's
SAVED = WORK / "saved"
for d in (HOME, FAKE_HOME, SAVED):
    d.mkdir()
seed = os.environ.get("VEULY_SEED")
if seed:
    subprocess.run([sys.executable, "-B", seed, str(HOME)], check=True, capture_output=True)
else:                               # a small library of its own: one course, one finished class with a formula and a picture
    png = zlib.compress(b"".join(b"\0" + b"\x1e\x3a\x34" * 40 for _ in range(30)))
    png = (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">IIBBBBB", 40, 30, 8, 2, 0, 0, 0)
           + struct.pack(">I", zlib.crc32(b"IHDR" + struct.pack(">IIBBBBB", 40, 30, 8, 2, 0, 0, 0)))
           + struct.pack(">I", len(png)) + b"IDAT" + png + struct.pack(">I", zlib.crc32(b"IDAT" + png))
           + struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND")))
    (WORK / "pic.png").write_bytes(png)
    code = ("import sys; from transcapp import library; lib = library.Library(); c = lib.create_course('Calculus', 'en');"
            "k = lib.create_class(c['slug'], 'Limits'); img = lib.add_image(c['slug'], k['id'], 'board.png', open(sys.argv[1], 'rb').read());"
            "lib.write_text(c['slug'], k['id'], 'note', '# Limits\\n\\nThe limit $$\\\\lim_{x \\\\to 0} \\\\frac{\\\\sin x}{x} = 1$$\\n\\n![](' + img + ')\\n');"
            "lib.update_meta(c['slug'], k['id'], status='done', model='stand-in', engine='api')")
    subprocess.run([sys.executable, "-B", "-c", code, str(WORK / "pic.png")], check=True, cwd=ROOT,
                   env=dict(os.environ, VEULY_HOME=str(HOME), VEULY_SECRETS="file", PYTHONPATH=str(ROOT)))
# The first finished class with a note was also sent to an Obsidian vault (as the pipeline marks it): its page has "Open in Obsidian".
subprocess.run([sys.executable, "-B", "-c",
                "from transcapp import library; lib = library.Library()\n"
                "for c in lib.courses():\n"
                "    done = [k for k in c['classes'] if k['status'] == 'done']\n"
                "    if done:\n"
                "        lib.update_meta(c['slug'], done[0]['id'], obsidian={'path': '/Users/someone/Vault/Calculus/Limits.md', 'rel': 'Calculus/Limits.md'})\n"
                "        break"], check=True, cwd=ROOT, env=dict(os.environ, VEULY_HOME=str(HOME), VEULY_SECRETS="file", PYTHONPATH=str(ROOT)))
settings = json.loads((HOME / "settings.json").read_text()) if (HOME / "settings.json").exists() else {}
settings.update(engine="", auto_images=False, setup_done=True, ui_lang="en")      # no note engine: nothing can reach Claude
(HOME / "settings.json").write_text(json.dumps(settings))
(HOME / "server.json").write_text(json.dumps({"port": SERVER_PORT}))             # the server takes a port of this test's
if not (WORK / "pic.png").exists():
    shutil.copy(next(HOME.glob("library/*/*/img/*.png")), WORK / "pic.png")


def launch(control):
    env = {k: v for k, v in os.environ.items() if k not in ("TRANSCAPP_HOME", "VEULY_PYTHON")}
    env.update(VEULY_HOME=str(HOME), VEULY_SECRETS="file", HOME=str(FAKE_HOME), PATH="/usr/bin:/bin:/usr/sbin:/sbin",
               VEULY_TEST_TOKEN=TOKEN, VEULY_TEST_PORT=str(control), VEULY_TEST_SAVE_DIR=str(SAVED), VEULY_TEST_AUDIO="tone")
    log = open(WORK / f"app-{control}.log", "w")
    return subprocess.Popen([str(APP / "Contents" / "MacOS" / "Veuly")], env=env, stdout=log, stderr=log)


DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(control, path, body=None, raw=False, timeout=90):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{control}{path}", data=data, method="POST" if data is not None else "GET",
                                 headers={"X-Test-Token": TOKEN, "Content-Type": "application/json"})
    with DIRECT.open(req, timeout=timeout) as r:
        payload = r.read()
    return payload if raw else json.loads(payload)


def js(control, code, gesture=False):
    r = call(control, "/eval", {"js": code, "gesture": gesture})
    if "error" in r:
        raise RuntimeError(f"{r['error']}\n{code[:300]}")
    return r.get("value")


def wait(what, test, timeout=30.0, every=0.25):
    end = time.time() + timeout
    while time.time() < end:
        try:
            value = test()
            if value:
                return value
        except Exception:
            pass
        time.sleep(every)
    check(f"{what} (timed out)", False)
    return None


def status(control):
    return call(control, "/status")


def server_answers(port=SERVER_PORT):
    try:
        with DIRECT.open(f"http://127.0.0.1:{port}/api/ping", timeout=2) as r:
            return json.load(r).get("app") == "transcapp"
    except Exception:
        return False


def press_menu(control, item):
    return call(control, "/menu", {"id": item})


def press_status(control, item):
    """A click on an item of the menu bar item's menu (test mode keeps that menu off the screen)."""
    return call(control, "/statusmenu", {"id": item})["pressed"]


def status_item(control):
    return status(control)["statusItem"]


KEYS = {"m": 46, "k": 40, "z": 6, "x": 7, "a": 0, " ": 49, "Escape": 53}


def keys(control, *strokes):
    out = []
    for s in strokes:
        name, mods = (s.split("+")[-1], s.split("+")[:-1]) if "+" in s and len(s) > 1 else (s, [])
        out.append({"chars": "\u001b" if name == "Escape" else name, "code": KEYS.get(name, 0), "modifiers": mods})
    return call(control, "/key", {"keys": out})


a = launch(CONTROL_A)
b = None
try:
    # ------------------------------------------------------------------ start
    s = wait("the page loads", lambda: (lambda st: st if st["loaded"] else None)(status(CONTROL_A)), 45)
    check("the app started its own server, on the port it was given", s and s["owned"] and s["port"] == SERVER_PORT and s["serverRunning"], s)
    check("test mode: no Dock icon (accessory), the window off screen", s and s["activation"] == 1 and float(s["frame"].strip("{}").split(",")[0]) < -10000, s)
    state = js(CONTROL_A, "return await api('GET', '/api/state')")
    check("the server got VEULY_HOME from the app", state["library_path"].startswith(str(HOME.resolve())) or state["library_path"].startswith(str(HOME)),
          state["library_path"])
    courses = lambda control: js(control, "return document.querySelectorAll('#side .course').length")   # noqa: E731
    check("the page shows the library", wait("the side panel", lambda: courses(CONTROL_A) >= 1, 15))
    check("the link's token left the address bar, the bridge is there", js(CONTROL_A,
          "return location.search === '' && !!window.veulyShell && !!window.VeulyMac && Recorder.canShare()"))
    check("no page uses the microphone here", js(CONTROL_A, "return typeof rec === 'object' && !rec.recorder"))
    st = status(CONTROL_A)
    check("at launch: no menu bar item, no badge on the Dock, and macOS was not asked about notifications",
          not st["statusItem"]["shown"] and not st["statusItem"]["badge"] and st["notifyAsked"] == 0 and not st["notifications"], st["statusItem"])

    # ------------------------------------------------------------------ the menu bar
    menus = {m["id"]: m for m in call(CONTROL_A, "/menus")}
    check("the menu bar: Veuly, Edit, View, Window, Help", list(menus) == ["app", "edit", "view", "window", "help"], list(menus))
    edit = {i["id"]: i for i in menus["edit"]["items"]}
    check("Edit has undo, redo, cut, copy, paste, select all with their keys", [(edit[k]["action"], edit[k]["key"]) for k in ("undo", "redo", "cut", "copy", "paste", "select-all")]
          == [("undo:", "z"), ("redo:", "z"), ("cut:", "x"), ("copy:", "c"), ("paste:", "v"), ("selectAll:", "a")], edit)
    app_items = {i["id"]: i for i in menus["app"]["items"]}
    check("the app menu: About, Settings (Cmd+,), Hide, Quit (Cmd+Q)", app_items["settings"]["key"] == "," and app_items["quit"]["key"] == "q"
          and "about" in app_items and "hide" in app_items and app_items["quit"]["title"] == "Quit Veuly", app_items)
    view = {i["id"]: i for i in menus["view"]["items"]}
    check("View: reload, actual size, zoom in, zoom out", {"reload", "zoom-actual", "zoom-in", "zoom-out"} <= set(view))

    press_menu(CONTROL_A, "settings")
    check("Settings from the menu opens the page's Settings", wait("Settings shows", lambda: js(CONTROL_A,
          "return S.view.name === 'settings' && !!document.querySelector('#setup-section')"), 15))
    login = wait("the login line", lambda: js(CONTROL_A, """const b = document.querySelector('#open-at-login');
        return b && !b.disabled ? { checked: b.checked, text: document.querySelector('#login-row').textContent, help: document.querySelector('#login-help').textContent } : null"""), 10)
    check("Settings in the app: Open Veuly at login, off, with what it does", login and not login["checked"]
          and login["text"] == "Open Veuly when you log in to this Mac" and "ready for the next class" in login["help"], login)
    check("with the other parts of Settings (the calendar link, your name): the login line once, right after the language",
          js(CONTROL_A, """return document.querySelectorAll('#open-at-login').length === 1 && document.querySelector('#set-lang').nextElementSibling.id === 'login-row'
            && document.querySelectorAll('#calendar-settings').length === 1 && document.querySelectorAll('#my-name').length === 1"""))
    js(CONTROL_A, "document.querySelector('#open-at-login').click(); return 1", gesture=True)
    on = wait("the login item on", lambda: status(CONTROL_A)["login"]["on"], 5)
    js(CONTROL_A, "await go({ name: 'home' }); await go({ name: 'settings' }); return 1")
    check("ticked: the app's login item (a stand-in in test mode) is on, and Settings shows it so when drawn again",
          on and wait("ticked again", lambda: js(CONTROL_A, "const b = document.querySelector('#open-at-login'); return !!b && !b.disabled && b.checked"), 10))
    js(CONTROL_A, "document.querySelector('#open-at-login').click(); return 1", gesture=True)
    check("and unticked, off again", wait("the login item off", lambda: not status(CONTROL_A)["login"]["on"], 5))
    press_menu(CONTROL_A, "help-item")
    check("Help from the menu opens the Info pop-up", wait("Info shows", lambda: js(CONTROL_A, "return !!document.querySelector('#info-pop')"), 10))
    js(CONTROL_A, "document.querySelector('#info-pop').dispatchEvent(new MouseEvent('mousedown', { bubbles: true })); return 1")

    js(CONTROL_A, "S.settings = await api('POST', '/api/settings', { ui_lang: 'es' }); await loadState(); render(); return 1")
    check("the menus follow the page's language (Spanish)", wait("Spanish menus", lambda: status(CONTROL_A)["menus"] == ["Veuly", "Edición", "Visualización", "Ventana", "Ayuda"], 10),
          status(CONTROL_A)["menus"])
    js(CONTROL_A, "S.settings = await api('POST', '/api/settings', { ui_lang: 'ca' }); await loadState(); render(); return 1")
    check("and Catalan", wait("Catalan menus", lambda: status(CONTROL_A)["menus"][1] == "Edita", 10))
    js(CONTROL_A, "S.settings = await api('POST', '/api/settings', { ui_lang: 'en' }); await loadState(); render(); return 1")
    wait("English menus", lambda: status(CONTROL_A)["menus"][1] == "Edit", 10)

    press_menu(CONTROL_A, "zoom-in")
    zoomed = status(CONTROL_A)["zoom"]
    press_menu(CONTROL_A, "zoom-actual")
    check("View > Zoom In and Actual Size change the page's zoom", zoomed > 1.05 and abs(status(CONTROL_A)["zoom"] - 1) < 0.01, zoomed)
    call(CONTROL_A, "/key", {"keys": [{"chars": "=", "code": 24, "modifiers": ["command"]}]})
    zoomed = wait("Cmd+=", lambda: (lambda z: z if z > 1.05 else None)(status(CONTROL_A)["zoom"]), 5)
    call(CONTROL_A, "/key", {"keys": [{"chars": "0", "code": 29, "modifiers": ["command"]}]})
    check("Cmd+= zooms in too (as in Safari and Chrome), Cmd+0 back", zoomed and wait("Cmd+0", lambda: abs(status(CONTROL_A)["zoom"] - 1) < 0.01, 5), zoomed)

    # ------------------------------------------------------------------ the window
    call(CONTROL_A, "/window", {"visible": False})
    check("closing the window hides it; the app and its server stay", not status(CONTROL_A)["visible"] and server_answers() and a.poll() is None)
    call(CONTROL_A, "/reopen", {})
    check("clicking the Dock icon (reopen) shows it again", status(CONTROL_A)["visible"])

    # ------------------------------------------------------------------ the page in WebKit
    done = js(CONTROL_A, """
      for (const c of S.courses) for (const k of c.classes) {
        if (k.status !== 'done') continue;
        const cls = await api('GET', `/api/classes/${c.slug}/${k.id}`);
        if (cls.note.includes('$') && cls.note.includes('![')) return { slug: c.slug, cid: k.id };
      }
      for (const c of S.courses) for (const k of c.classes) if (k.status === 'done') return { slug: c.slug, cid: k.id };
      return null;""")
    js(CONTROL_A, f"await go({{ name: 'class', slug: '{done['slug']}', cid: '{done['cid']}' }}); return 1")
    wait("the note shows", lambda: js(CONTROL_A, "return !!document.querySelector('#note-view')"), 15)
    seen = js(CONTROL_A, """
      const imgs = [...document.querySelectorAll('#note-view img')];
      for (const i of imgs) {                     // they load lazily: each one is brought into view first
        i.scrollIntoView();
        await new Promise((done) => { if (i.complete) done(); else { i.addEventListener('load', done, { once: true });
          i.addEventListener('error', done, { once: true }); setTimeout(done, 5000); } });
      }
      return { katex: document.querySelectorAll('#note-view .katex').length,
               pictures: imgs.length, drawn: imgs.filter((i) => i.complete && i.naturalWidth > 0).length };""")
    check("a note with formulas: KaTeX draws them", seen["katex"] >= 1, seen)
    check("its pictures load", seen["pictures"] == 0 or seen["drawn"] == seen["pictures"], seen)

    before = set(SAVED.iterdir())
    js(CONTROL_A, "document.querySelector('#save-pdf').click(); return 1", gesture=True)
    pdf = wait("Save as PDF arrives", lambda: [p for p in SAVED.iterdir() if p not in before and p.suffix == ".pdf"
                                               and p.stat().st_size > 1000 and p.read_bytes()[:5] == b"%PDF-"], 60)
    check("Save as PDF: the server's PDF is downloaded to the path the test gave (no save panel)", bool(pdf), sorted(SAVED.iterdir()))

    printed_before = len(status(CONTROL_A)["printed"])
    js(CONTROL_A, "window.print(); return 1", gesture=True)
    printed = wait("printing", lambda: (lambda st: st["printed"][printed_before:] if len(st["printed"]) > printed_before else None)(status(CONTROL_A)), 20)
    out = pathlib.Path(printed[0]) if printed else None
    check("window.print() prints the page (to a PDF in test mode)", out and wait("the printed PDF", lambda: out.exists() and out.stat().st_size > 1000, 20)
          and out.read_bytes()[:5] == b"%PDF-")
    # WebKit takes this off-screen window as hidden and runs no animation frames there: a page that go() faded in would
    # stay on its first frame (opacity 0) and print blank. go() does not fade a hidden page in (arrive, web/app.js).
    words = js(CONTROL_A, "return (document.querySelector('#note-view').innerText.match(/[A-Za-z\\u00c0-\\u00ff]{5,}/g) || []).slice(0, 3)")
    pdftotext = shutil.which("pdftotext") or next((p for p in ("/opt/homebrew/bin/pdftotext", "/usr/local/bin/pdftotext") if os.path.exists(p)), None)
    if pdftotext and out:
        printed_text = subprocess.run([pdftotext, str(out), "-"], capture_output=True, text=True).stdout
        check("and the printed page holds the note's text (a page shown while the window is hidden is not left blank)",
              words and all(w in printed_text for w in words), (words, printed_text[:300]))
    else:
        print("skip the printed page's text: no pdftotext here", flush=True)

    call(CONTROL_A, "/answers", {"buttons": [0, 1], "typed": ["typed in the prompt"]})
    answers = js(CONTROL_A, "return [alert('Hello from the page'), confirm('Sure?'), prompt('Your name?', 'Ana')]")
    asked = status(CONTROL_A)["asked"][-3:]
    check("alert, confirm and prompt get the app's panels", answers == [None, False, "typed in the prompt"]
          and [q["title"] for q in asked] == ["Hello from the page", "Sure?", "Your name?"], (answers, asked))

    js(CONTROL_A, """const a = document.createElement('a'); a.href = 'https://example.com/syllabus'; a.target = '_blank';
                     document.body.append(a); a.click(); a.remove();
                     const b = document.createElement('a'); b.href = 'https://example.org/page'; document.body.append(b); b.click(); b.remove(); return 1""", gesture=True)
    time.sleep(1)
    st = status(CONTROL_A)
    check("links to other sites go to the user's browser, the window stays on the app", "https://example.com/syllabus" in st["outside"]
          and "https://example.org/page" in st["outside"] and st["url"] == "/", st)
    js(CONTROL_A, "document.querySelector('#ob-link').click(); return 1", gesture=True)
    check("Open in Obsidian opens Obsidian (its obsidian: link goes to the system)", wait("the Obsidian link", lambda: [u for u in status(CONTROL_A)["outside"]
          if u.startswith("obsidian://open?path=%2FUsers%2Fsomeone%2FVault")], 5), status(CONTROL_A)["outside"])
    spot = js(CONTROL_A, """const box = document.createElement('div');            // an empty box: the page's background, no text
                            Object.assign(box.style, { position: 'fixed', right: '0', bottom: '0', width: '80px', height: '80px', zIndex: 99999 });
                            box.id = 'empty-box'; document.body.append(box); const r = box.getBoundingClientRect();
                            return { x: r.left + 40, y: r.top + 40 };""")
    call(CONTROL_A, "/rightclick", spot)
    menu = wait("the right-click menu", lambda: status(CONTROL_A)["contextMenu"] or None, 10) or {}
    js(CONTROL_A, "document.getElementById('empty-box').remove(); return 1")
    check("WebKit's own right-click Reload (and Back, Forward) are taken out of its menu", "WKMenuItemIdentifierReload" in menu.get("offered", [])
          and not {"WKMenuItemIdentifierReload", "WKMenuItemIdentifierGoBack", "WKMenuItemIdentifierGoForward"} & set(menu.get("kept", [])), menu)

    # a class to record in, opened
    new = js(CONTROL_A, f"""const meta = await api('POST', '/api/courses/{done['slug']}/classes', {{ title: 'Mac app test' }});
                           await loadState(); await go({{ name: 'class', slug: '{done['slug']}', cid: meta.id }}); return {{ slug: '{done['slug']}', cid: meta.id }};""")
    wait("the live editor", lambda: js(CONTROL_A, "return !!document.querySelector('#live-editor .cm-content')"), 15)
    base = f"/api/classes/{new['slug']}/{new['cid']}"
    class_dir = HOME / "library" / new["slug"] / new["cid"]

    spot = js(CONTROL_A, """const r = document.querySelector('#live-editor .cm-content').getBoundingClientRect();
                            return { x: r.left + r.width / 2, y: r.top + Math.min(r.height / 2, 60) };""")
    pictures = lambda: js(CONTROL_A, "return (liveEditor.getValue().match(/!\\[\\]\\(img\\//g) || []).length")   # noqa: E731
    dropped = call(CONTROL_A, "/drop", {"paths": [str(WORK / "pic.png")], **spot})
    check("a file dragged onto the notes is dropped there (the web view's own drag and drop)", dropped["performed"]
          and wait("the dropped picture", lambda: pictures() == 1, 15) and len(list((class_dir / "img").glob("*"))) == 1, dropped)

    call(CONTROL_A, "/files", {"paths": [str(WORK / "pic.png")]})
    js(CONTROL_A, "document.querySelector('#image-picker').click(); return 1", gesture=True)
    check("a file picker gets a file (the open panel's answer), and it lands in the notes", wait("the picture", lambda: pictures() == 2, 15)
          and len(list((class_dir / "img").glob("*"))) == 2)

    js(CONTROL_A, "liveEditor.view.focus(); liveEditor.view.dispatch({ selection: { anchor: liveEditor.view.state.doc.length } }); return 1", gesture=True)
    keys(CONTROL_A, " ", "m", "k")
    text = lambda: js(CONTROL_A, "return liveEditor.getValue()")
    check("real key presses reach the editor, and its mk shortcut opens a formula", wait("mk", lambda: text().endswith(" $$"), 5), repr(text()[-20:]))
    keys(CONTROL_A, "command+z")
    check("Cmd+Z in the editor gives the letters back (the editor's own undo)", wait("undo", lambda: text().endswith(" mk"), 5), repr(text()[-20:]))
    js(CONTROL_A, "document.querySelector('#draw').click(); return 1", gesture=True)
    check("the drawing pad opens", wait("the pad", lambda: js(CONTROL_A, "return !!document.querySelector('.cm-draw-canvas')"), 10))
    keys(CONTROL_A, "Escape")
    check("Escape closes it", wait("the pad closes", lambda: js(CONTROL_A, "return !document.querySelector('.cm-draw')"), 10))
    check("the clipboard API is there (not written to: it is the user's)", js(CONTROL_A, "return !!(navigator.clipboard && navigator.clipboard.writeText)"))

    # the notes saved first: with typing still unsaved, a reload asks (as the page's own beforeunload does in a browser)
    js(CONTROL_A, "await saveLive(); setSide(false); window.beforeReload = true; return 1")
    press_menu(CONTROL_A, "reload")
    wait("the page loads again", lambda: js(CONTROL_A, "return !window.beforeReload && document.querySelectorAll('#side .course').length > 0"), 20)
    check("View > Reload keeps the session and what the page saved (localStorage)", js(CONTROL_A,
          "return document.querySelector('#app').classList.contains('side-hidden') && localStorage.getItem('ta-side') === 'hidden'"))
    js(CONTROL_A, f"setSide(true); await go({{ name: 'class', slug: '{new['slug']}', cid: '{new['cid']}' }}); return 1")
    wait("the class again", lambda: js(CONTROL_A, "return !!document.querySelector('#record')"), 15)

    # ------------------------------------------------------------------ recording this Mac's sound, mostly hidden
    js(CONTROL_A, "const s = document.querySelector('#source'); s.value = 'system'; s.dispatchEvent(new Event('change')); return 1")
    check("in the app, 'this computer's sound' says what it records", "everything this Mac plays" in js(CONTROL_A, "return document.querySelector('#source-help').textContent"))
    time.sleep(2.5)                                           # past the last click this test made
    refused = js(CONTROL_A, "try { await veulyShell.post({ type: 'sound', op: 'start' }); return 'started'; } catch (e) { return String(e.message || e); }")
    check("the app does not start this Mac's sound without a click just before", "gesture" in str(refused) and not status(CONTROL_A)["sound"], refused)
    t0 = time.time()
    js(CONTROL_A, "const b = document.querySelector('#record'); b.click(); b.click(); return 1", gesture=True)      # a double click
    rec_on = wait("recording starts", lambda: js(CONTROL_A, "return !!rec.recorder && rec.recorder.tracks.length === 1 && rec.recorder.tracks[0].ratio === 1"), 15)
    started_at = time.time()
    check("Record with this Mac's sound starts the native source (one track, already 16 kHz)", rec_on)
    time.sleep(1)
    st = status(CONTROL_A)
    check("while recording the Mac stays awake (a power assertion) and the app is not napped; a double click started one recorder",
          st["awake"] and st["sound"] and st["recorders"] == 1, st)
    assertions = subprocess.run(["pmset", "-g", "assertions"], capture_output=True, text=True).stdout
    check("macOS lists the assertion", "Veuly is recording a class" in assertions)
    item = wait("the menu bar item", lambda: (lambda i: i if i["shown"] and ":" in i["title"] else None)(status_item(CONTROL_A)), 5) or {}
    ids = [i["id"] for i in item.get("items", [])]
    check("while recording: a menu bar item with the time, and its menu says what records, Mute, Finish class…, Show Veuly",
          ids == ["status-recording", "status-mute", "status-finish", "status-show"] and item["items"][0]["title"] == "Recording: Mac app test"
          and item["items"][2]["title"] == "Finish class…" and item["badge"] == "●", item)
    later = wait("its clock goes on", lambda: (lambda i: i if i["title"] != item.get("title") else None)(status_item(CONTROL_A)), 4)
    secs = lambda text: int(text.split(":")[0]) * 60 + int(text.split(":")[1])      # noqa: E731  (m:ss)
    check("the item's clock follows the recording's", later and 0 < secs(later["title"]) - secs(item["title"]) <= 4 and secs(later["title"]) <= 8,
          (item.get("title"), later))
    time.sleep(2.5)
    check("its menu is not made again as the time goes on (a menu open under the pointer stays as it is), its first line follows the time",
          later and status_item(CONTROL_A)["rebuilds"] == later["rebuilds"] and status_item(CONTROL_A)["items"][0]["title"] == "Recording: Mac app test",
          (later, status_item(CONTROL_A)))
    call(CONTROL_A, "/answers", {"buttons": [0, 0]})
    press_menu(CONTROL_A, "reload")
    time.sleep(1)
    check("View > Reload while recording asks first (and Cancel keeps recording)", js(CONTROL_A, "return !!rec.recorder")
          and "recording" in status(CONTROL_A)["asked"][-1]["title"].lower(), status(CONTROL_A)["asked"][-1:])
    asked = len(status(CONTROL_A)["asked"])
    call(CONTROL_A, "/answers", {"buttons": [0]})
    js(CONTROL_A, "setTimeout(() => location.reload(), 10); return 1")
    time.sleep(1.5)
    check("a reload the page itself starts asks the same (and Cancel keeps recording)", js(CONTROL_A, "return !!rec.recorder")
          and [q["title"] for q in status(CONTROL_A)["asked"][asked:]] == ["Reload while recording?"], status(CONTROL_A)["asked"][asked:])
    press_menu(CONTROL_A, "quit")
    time.sleep(1.5)
    q = status(CONTROL_A)["asked"][-1]
    check("Cmd+Q while recording asks (A class is recording) and Keep recording keeps it", q["title"] == "A class is recording"
          and q["buttons"] == ["Keep recording", "Quit anyway"] and a.poll() is None and js(CONTROL_A, "return !!rec.recorder"), q)

    call(CONTROL_A, "/window", {"visible": False})
    hidden_at = time.time()
    time.sleep(10)
    mid = js(CONTROL_A, "return { vis: document.visibilityState, seq: rec.seq, queue: rec.queue.length }")
    time.sleep(max(0.0, 50 - (time.time() - hidden_at)))
    call(CONTROL_A, "/window", {"visible": True})
    check("hidden, the page knows it is hidden, and chunks keep going", mid["vis"] == "hidden" and mid["seq"] >= 4 and mid["queue"] <= 2, mid)
    press_status(CONTROL_A, "status-mute")                    # Mute from the menu bar item, the window hidden or not
    muted = wait("muted", lambda: js(CONTROL_A, "return rec.muted") and status_item(CONTROL_A)["items"][1]["title"] == "Unmute", 3)
    time.sleep(4.5)
    press_status(CONTROL_A, "status-mute")
    check("Mute and Unmute from the menu bar item: the page's own Mute, and the item says which one comes next", muted
          and wait("unmuted", lambda: not js(CONTROL_A, "return rec.muted") and status_item(CONTROL_A)["items"][1]["title"] == "Mute", 3))
    js(CONTROL_A, "window.__huWas = window.headsUpWaiting; window.headsUpWaiting = () => 2; return true")
    counted = wait("the count", lambda: (lambda i: i if i["title"].endswith(" · 2") else None)(status_item(CONTROL_A)), 4) or {}
    line = [i for i in counted.get("items", []) if i["id"] == "status-headsups"]
    check("heads-ups waiting: the item shows only their number, and its menu line brings the window",
          [i["title"] for i in line] == ["Heads-ups waiting: 2"] and line[0]["enabled"] and press_status(CONTROL_A, "status-headsups"), counted)
    js(CONTROL_A, "window.headsUpWaiting = window.__huWas; return true")
    check("and none left: the number and the line go", wait("no count", lambda: ":" in status_item(CONTROL_A)["title"]
          and " · " not in status_item(CONTROL_A)["title"] and not any(i["id"] == "status-headsups" for i in status_item(CONTROL_A)["items"]), 4))
    time.sleep(max(0.0, 60 - (time.time() - started_at)))
    recorded_for = time.time() - started_at
    js(CONTROL_A, "document.querySelector('#finish').click(); return 1", gesture=True)
    meta = wait("Finish", lambda: (lambda m: m if m["status"] != "recording" else None)(js(CONTROL_A, f"return await api('GET', '{base}')")), 30)
    st = status(CONTROL_A)
    check("Finish ends the recording; the stand-in sound stops; the Mac may sleep again", meta and not st["sound"] and not st["awake"] and st["recorders"] == 0, (meta, st))
    check("the first Finish is when macOS is asked whether Veuly may notify (a stand-in: nothing is asked)", st["notifyAsked"] == 1, st["notifyAsked"])
    check("the badge on the Dock goes with the recording", wait("no badge", lambda: not status_item(CONTROL_A)["badge"], 5), status_item(CONTROL_A))
    js(CONTROL_A, "await go({ name: 'home' }); return 1")        # elsewhere in the app: the class's end is said by a notification
    wav_path = class_dir / "audio.wav"
    with wave.open(str(wav_path)) as w:
        seconds, frames = w.getnframes() / w.getframerate(), w.readframes(w.getnframes())
    check("the whole recording reached the server (about 60 s, 16 kHz mono)", abs(seconds - recorded_for) < 1.5 and meta["duration_s"] >= 58, (seconds, recorded_for, meta["duration_s"]))
    made = js(CONTROL_A, "return rec.seq")
    check("every chunk the page made reached the server", meta["audio_seq"] == made and 25 <= made <= 70, (meta["audio_seq"], made))
    samples = array.array("h", frames)
    runs, start = [], None
    for i in range(0, len(samples), 160):                     # 10 ms steps: where it is silent
        quiet = max(abs(v) for v in samples[i:i + 160]) == 0
        if quiet and start is None:
            start = i
        elif not quiet and start is not None:
            runs.append(((i - start) / 16000, start / 16000))
            start = None
    loud = max(abs(v) for v in samples[: 16000 * 5])
    mutes = meta.get("muted") or []
    check("Mute wrote silence for as long as it was on, where the server marked it", len(runs) == 1 and 4.3 < runs[0][0] < 5.7
          and mutes and abs(mutes[0][0] - runs[0][1]) < 0.5, (runs, mutes))
    check("the rest is the tone, at its level", 7000 < loud < 9000, loud)
    check("Finish went on to the transcript step, and stopped without any note engine (no Claude)", meta["status"] in ("processing", "error")
          and (meta["status"] == "processing" or meta["error_kind"] in ("not_configured", "no_model", "no_whisper")), meta)
    told = wait("the notification", lambda: [n for n in status(CONTROL_A)["notifications"] if n["cid"] == new["cid"]], 20) or [{}]
    check("a step that failed: a notification says the note could not be written, the class and why", told[0].get("title") == "The note could not be written"
          and told[0].get("body", "").startswith("Mac app test: ") and len(told[0]["body"]) > 30 and not told[0]["ok"], told)
    time.sleep(4)
    check("and only once", len([n for n in status(CONTROL_A)["notifications"] if n["cid"] == new["cid"]]) == 1, status(CONTROL_A)["notifications"])
    clicked = call(CONTROL_A, "/notification", {"index": len(status(CONTROL_A)["notifications"]) - 1})
    check("a click on it opens that class", clicked["clicked"] and wait("the class", lambda: js(CONTROL_A,
          f"return S.view.name === 'class' && S.view.cid === '{new['cid']}' && !!document.querySelector('#status-slot')"), 10))

    # A note being written: the menu bar item says how far, and a notification says it is ready (class.json played by the test).
    done_json = HOME / "library" / done["slug"] / done["cid"] / "class.json"
    kept_meta = json.loads(done_json.read_text())
    done_json.write_text(json.dumps({**kept_meta, "status": "processing", "step": "transcribing", "progress": 40}))
    js(CONTROL_A, "await loadState(); renderSide(); return 1")
    writing = wait("writing", lambda: (lambda i: i if i["shown"] and i["title"] == "40%" else None)(status_item(CONTROL_A)), 10) or {}
    check("a note being written: the menu bar item shows how far, and its menu says which class", [i["title"] for i in writing.get("items", [])]
          == [f"{kept_meta['title']} · Writing the note… 40%", "Show Veuly"] and not writing["badge"] and status(CONTROL_A)["writingAwake"], writing)
    done_json.write_text(json.dumps({**kept_meta, "status": "processing", "step": "writing", "progress": 100}))
    check("then without a number while Claude writes", wait("writing on", lambda: [i["title"] for i in status_item(CONTROL_A)["items"]][:1]
          == [f"{kept_meta['title']} · Writing the note…"], 8), status_item(CONTROL_A))
    done_json.write_text(json.dumps(kept_meta))
    ready = wait("ready", lambda: [n for n in status(CONTROL_A)["notifications"] if n["cid"] == done["cid"]], 10) or [{}]
    check("a note that is ready: a notification with the class's title, and the menu bar item goes away", ready[0].get("title") == "Your note is ready"
          and ready[0].get("body") == kept_meta["title"] and ready[0].get("ok") and wait("gone", lambda: not status_item(CONTROL_A)["shown"]
          and not status(CONTROL_A)["writingAwake"], 5), (ready, status_item(CONTROL_A)))
    call(CONTROL_A, "/notification", {"index": len(status(CONTROL_A)["notifications"]) - 1})
    check("a click on it opens the class at its note", wait("the note", lambda: js(CONTROL_A,
          f"return S.view.name === 'class' && S.view.cid === '{done['cid']}' && S.tab === 'note' && !!document.querySelector('#note-view')"), 10))
    time.sleep(3)
    check("a class reported once is not reported again while the page's list is older", len([n for n in status(CONTROL_A)["notifications"]
          if n["cid"] == done["cid"]]) == 1)

    # ------------------------------------------------------------------ Both: a stand-in microphone and this Mac's sound
    def new_class(title):
        meta = js(CONTROL_A, f"""const m = await api('POST', '/api/courses/{done['slug']}/classes', {{ title: '{title}' }});
                                await loadState(); await go({{ name: 'class', slug: '{done['slug']}', cid: m.id }}); return m;""")
        wait("the class page", lambda: js(CONTROL_A, "return !!document.querySelector('#record')"), 15)
        return meta["id"], HOME / "library" / done["slug"] / meta["id"]

    def source(value):
        js(CONTROL_A, f"const s = document.querySelector('#source'); s.value = '{value}'; s.dispatchEvent(new Event('change')); return 1")

    def seconds_of(path):
        with wave.open(str(path)) as w:
            return w.getnframes() / w.getframerate(), array.array("h", w.readframes(w.getnframes()))

    totals = "return rec.recorder ? rec.recorder.tracks.map((t) => t.flushed + t.length) : null"
    probe = """return { dead: rec.dead, warn: (document.querySelector('#source-lost') || {}).textContent || '',
                        toast: document.querySelector('#toast').textContent, flushed: rec.recorder ? rec.recorder.tracks.map((t) => t.flushed) : null }"""
    cid, both_dir = new_class("Both test")
    js(CONTROL_A, STAND_IN_MIC + " return 1;")
    source("both")
    js(CONTROL_A, "document.querySelector('#record').click(); return 1", gesture=True)
    check("Both: the stand-in microphone and this Mac's sound record as two tracks", wait("two tracks", lambda: js(CONTROL_A,
          "return !!rec.recorder && rec.recorder.tracks.length === 2 && window.standInMics === 1"), 15))
    time.sleep(3)
    t1, n1 = time.time(), js(CONTROL_A, totals)[0]
    time.sleep(8)
    rate = (js(CONTROL_A, totals)[0] - n1) / (time.time() - t1)
    check("with the window hidden the microphone's audio graph keeps real time (the page plays an inaudible constant)",
          15600 < rate < 16400 and js(CONTROL_A, "return !!rec.recorder.audible"), rate)
    call(CONTROL_A, "/sound", {"stall": 6, "mode": "gap"})
    lost = wait("lost", lambda: (lambda x: x if x["dead"] else None)(js(CONTROL_A, probe)), 8)
    check("a stalled Mac sound is said: the page's lost source, and that silence goes in its place", lost and lost["warn"]
          and "Silence is recorded in its place" in lost["toast"], lost)
    back = wait("back", lambda: (lambda x: x if not x["dead"] and not x["warn"] and "reaches Veuly again" in x["toast"] else None)(js(CONTROL_A, probe)), 12)
    check("when it comes back the page says so, and the lost state and its warning clear", back, js(CONTROL_A, probe))
    before = js(CONTROL_A, probe)["flushed"][0]
    shared = js(CONTROL_A, "window.extraSound = await Recorder.share(); return !rec.recorder.native.stopped && window.extraSound !== rec.recorder.native", gesture=True)
    time.sleep(9)
    after = js(CONTROL_A, probe)["flushed"][0]
    # its track is filled with silence once it is two seconds behind: the microphone goes out at most that much later
    check("this Mac's sound started again while recording (what Share again does first) leaves the recording's source attached,"
          " and the microphone goes on being sent", shared and after - before >= 5 * 16000, (shared, before, after))
    js(CONTROL_A, "window.extraSound.stop(); return 1")
    check("and stopping the new one stops the app's capture", wait("capture stops", lambda: not status(CONTROL_A)["sound"], 5))
    # The attached source gets nothing now: the page's own lost-source notice (the recording package's) offers Share again.
    # Pressed, the page's reshare runs the recording package's Recorder.reconnect('share'), which native.js wraps: the new
    # source takes the old one's track, and the old one goes without stopping the app's new capture.
    gone = wait("this Mac's sound said as lost", lambda: js(CONTROL_A, "return rec.dead === 'share' && !!document.querySelector('#source-lost #reconnect')"), 10)
    check("this Mac's sound that stops reaching the page: the page's lost-source notice, with Share again in the app's words",
          gone and "Record this Mac's sound again" in js(CONTROL_A, "return document.querySelector('#source-lost').textContent"), js(CONTROL_A, probe))
    js(CONTROL_A, "window.oldNative = rec.recorder.native; window.oldLane = rec.recorder.native.lane; return 1")
    js(CONTROL_A, "document.querySelector('#source-lost #reconnect').click(); return 1", gesture=True)
    again = wait("shared again", lambda: js(CONTROL_A, """const r = rec.recorder;
        return !!r && rec.dead === '' && !document.querySelector('#source-lost') && r.native !== window.oldNative && window.oldNative.stopped
          && r.streams[1] === r.native && r.native.lane === window.oldLane && r.tracks[1] === window.oldLane
          && document.querySelector('#toast').textContent.includes('reaches Veuly again')"""), 10)
    time.sleep(4)
    lanes = js(CONTROL_A, totals)
    check("Share again (the page's button, the recording package's reconnect, native.js's wrapper): the same track, the old source"
          " stopped without stopping the app's capture, the notice gone, and the new source keeps up with the microphone",
          again and status(CONTROL_A)["sound"] and abs(lanes[0] - lanes[1]) < 16000 * 3, (again, lanes, js(CONTROL_A, probe)))
    # A microphone that ends (unplugged): the page opens it again by itself, through the recording package's own reconnect.
    js(CONTROL_A, "rec.recorder.streams[0].getAudioTracks()[0].dispatchEvent(new Event('ended')); return 1")
    mic = wait("the microphone again", lambda: js(CONTROL_A, "return window.standInMics === 2 && rec.dead === '' && !!rec.recorder"
                                                 " && document.querySelector('#toast').textContent.startsWith('Recording again')"), 10)
    before = js(CONTROL_A, totals)
    time.sleep(3)
    after = js(CONTROL_A, totals)
    check("a microphone that ends is opened again by the page's own reconnect (native.js leaves it alone), and both tracks go on",
          mic and after[0] - before[0] >= 2 * 16000 and after[1] - before[1] >= 2 * 16000, (mic, before, after))
    time.sleep(2.7)                                           # mid-chunk: its last seconds are only in the page
    page_has = js(CONTROL_A, totals)
    call(CONTROL_A, "/answers", {"buttons": [1]})             # "Reload while recording?": Reload
    press_menu(CONTROL_A, "reload")
    wait("the page loads again", lambda: js(CONTROL_A, "return !rec.recorder && !window.standInMics && document.querySelectorAll('#side .course').length > 0"), 20)
    on_disk = [(both_dir / f).stat().st_size / 2 for f in ("audio.pcm", "audio2.pcm")]
    check("View > Reload while recording, answered Reload: what the page had recorded reached the server first, both tracks",
          on_disk[0] >= page_has[0] - 1600 and on_disk[0] == on_disk[1], (page_has, on_disk))

    # ------------------------------------------------------------------ the Mac sleeps while recording (the lid closed)
    cid, sleep_dir = new_class("Sleep test")
    js(CONTROL_A, STAND_IN_MIC + " return 1;")
    source("both")
    js(CONTROL_A, "document.querySelector('#record').click(); return 1", gesture=True)
    wait("two tracks", lambda: js(CONTROL_A, "return !!rec.recorder && rec.recorder.tracks.length === 2"), 15)
    time.sleep(4)
    # What a sleep can leave behind: the audio graph suspended, the microphone silent (a muted track), this Mac's sound gone.
    js(CONTROL_A, """await rec.recorder.ctx.suspend(); const tr = rec.recorder.streams[0].getAudioTracks()[0];
                     Object.defineProperty(tr, 'muted', { get: () => true, configurable: true }); return 1""")
    woke = call(CONTROL_A, "/sleep", {"seconds": 2700})["value"] or {}
    before = js(CONTROL_A, totals)
    time.sleep(4)
    after = js(CONTROL_A, totals)
    check("after a sleep the recording goes on: the audio graph runs again, the silent microphone is opened again, both tracks"
          " move and stay together", woke.get("recording") and woke.get("state") == "running" and woke.get("reopened")
          and js(CONTROL_A, "return window.standInMics") == 2 and after[0] - before[0] >= 3 * 16000 and after[1] - before[1] >= 3 * 16000
          and abs(after[0] - after[1]) < 16000 * 3, (woke, before, after))
    log = pathlib.Path(status(CONTROL_A)["log"]).read_text()
    check("this Mac's sound is captured afresh after the wake (its device may have changed)", "the Mac woke up, starting the stand-in again" in log)
    gaps = wait("the gap", lambda: json.loads((sleep_dir / "class.json").read_text()).get("gaps"), 10) or []
    note = js(CONTROL_A, "return (document.querySelector('#slept-notice') || {}).textContent || ''")
    check("the class keeps where and how long the Mac slept (class.json gaps), and the page says it", len(gaps) == 1 and gaps[0][1] == 2700
          and 3 < gaps[0][0] < 12 and "asleep for 45 min" in note and "Nothing was recorded then" in note, (gaps, note))
    js(CONTROL_A, "document.querySelector('#slept-ok').click(); return 1", gesture=True)
    check("Close takes the notice away", wait("closed", lambda: js(CONTROL_A, "return !document.querySelector('#slept-notice')"), 5))
    js(CONTROL_A, "document.querySelector('#finish').click(); return 1", gesture=True)
    wait("finished", lambda: json.loads((sleep_dir / "class.json").read_text())["status"] != "recording", 30)
    lengths = [seconds_of(sleep_dir / f)[0] for f in ("audio.wav", "audio2.wav")]
    check("and the recording holds both tracks, as long as each other (the sleep is not in it)", abs(lengths[0] - lengths[1]) < 0.5 and lengths[0] > 7, lengths)

    # ------------------------------------------------------------------ this Mac's device changes its rate
    js(CONTROL_A, f"await go({{ name: 'class', slug: '{done['slug']}', cid: '{done['cid']}' }}); return 1")
    cid, rate_dir = new_class("Rate test")
    source("system")
    js(CONTROL_A, "document.querySelector('#record').click(); return 1", gesture=True)
    wait("recording", lambda: js(CONTROL_A, "return !!rec.recorder"), 15)
    t1 = time.time()
    time.sleep(5)
    call(CONTROL_A, "/sound", {"deviceRate": 16000})          # as AirPods do when a microphone opens; no notification
    time.sleep(11)
    recorded_rate = time.time() - t1
    js(CONTROL_A, "await go({ name: 'home' }); return 1")
    press_status(CONTROL_A, "status-finish")
    asked = wait("the question", lambda: js(CONTROL_A, "return S.view.name === 'class' && !!document.querySelector('#finish-pop')"), 10)
    check("Finish class… from the menu bar item shows the class and asks, as the page does", asked
          and js(CONTROL_A, "return document.querySelector('#finish-pop h2').textContent") == "Finish the class?" and js(CONTROL_A, "return !!rec.recorder && !rec.finishing"))
    recorded_rate = time.time() - t1                          # it records until the answer
    js(CONTROL_A, "document.querySelector('#finish-pop-yes').click(); return 1", gesture=True)
    wait("finished", lambda: json.loads((rate_dir / "class.json").read_text())["status"] != "recording", 30)
    length, tone = seconds_of(rate_dir / "audio.wav")
    tail = tone[-16000 * 6:]
    hz = sum(1 for i in range(1, len(tail)) if tail[i - 1] < 0 <= tail[i]) / 6
    log = pathlib.Path(status(CONTROL_A)["log"]).read_text()
    check("a device whose rate changes under the tap: found from the frames themselves, the tap built again at the new rate,"
          " the tone 440 Hz again and the recording its length", "frames come at" in log and 430 < hz < 450 and abs(length - recorded_rate) < 1.5,
          (hz, length, recorded_rate))

    # ------------------------------------------------------------------ a start that a stop overtakes
    call(CONTROL_A, "/sound", {"startDelay": 1.0})             # the tap's first start waits while macOS asks the user
    raced = js(CONTROL_A, """const started = veulyShell.post({ type: 'sound', op: 'start' }).then(() => 'started', (e) => String(e.message || e));
                             await veulyShell.post({ type: 'sound', op: 'stop' }); return await started;""", gesture=True)
    time.sleep(1.5)
    check("a stop that arrives while this Mac's sound is still starting is not lost: what started is stopped at once",
          raced == "stopped" and not status(CONTROL_A)["sound"], raced)
    call(CONTROL_A, "/sound", {"startDelay": 0.0})

    # ------------------------------------------------------------------ two clocks, two hours, in the real WebKit
    sim = js(CONTROL_A, """
      // Two hours of a class with the microphone and this Mac's sound, through the page's own Recorder and NativeSound,
      // in simulated time: the Mac's clock runs 200 ppm fast; at 30 min its device goes away for 6 s (those samples never
      // exist), at 60 min its blocks stall for 6 s and then arrive all at once. Every second of real time is a click on
      // both tracks; the chunks tell how far apart the two clicks of each second end up.
      const N = Recorder.native, R = 16000, SECONDS = 7200, DRIFT = 200e-6, T0 = 1.7e12, F = R * (1 + DRIFT);
      const sim = { wall: T0, now: 0 }, kept = { wall: N.clock.wall, now: N.clock.now };
      N.clock.wall = () => sim.wall; N.clock.now = () => sim.now;
      try {
        const marks = [[], []], seen = [0, 0], high = [false, false];
        const scan = (chunk, k) => {
          if (!chunk) return;
          for (let i = 0; i < chunk.length; i++) {
            const on = chunk[i] > 8000;
            if (on && !high[k]) marks[k].push(seen[k] + i);
            high[k] = on;
          }
          seen[k] += chunk.length;
        };
        const rec = new Recorder({ onChunk: (a, b) => { scan(a, 0); scan(b, 1); }, onLevel: () => {} });
        rec.ratio = 1; rec.sources = [{}]; rec.streams = []; rec.tracks = [rec.newTrack(1)];
        const sound = new N.Sound();
        sound.stop = () => {};                     // a simulation: the app is never told
        rec.native = sound;
        sound.attach(rec);
        let ended = 0;
        sound.track.addEventListener('ended', () => { ended++; });
        const b64 = (a) => { const u = new Uint8Array(a.buffer); let s = ''; for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000)); return btoa(s); };
        const zeros = {};
        const mic = new Float32Array(1600), pending = [];
        let micSent = 0, made = 0, micAtStallStart = 0, micAtStallEnd = 0;
        for (let step = 1; step <= SECONDS * 10; step++) {
          sim.now = step * 100; sim.wall = T0 + sim.now;
          const sec = sim.now / 1000;
          mic.fill(0);
          const j = Math.ceil(micSent / R) * R - micSent;
          if (j < 1600) { mic[j] = mic[j + 1] = mic[j + 2] = 0.61; }
          rec.take(mic, 0); micSent += 1600;
          const upTo = Math.floor(sec * F);
          if (sec > 1800 && sec <= 1806) made = upTo;          // the device went away: these samples never exist
          else if (upTo > made) {
            const n = upTo - made;
            let block = null;
            for (let c = Math.floor(made / F); ; c++) {          // the clicks that fall in this block
              const at = Math.round(c * F) - made;
              if (at >= n) break;
              if (at < 0) continue;
              if (!block) block = new Int16Array(n);
              for (let q = 0; q < 3 && at + q < n; q++) block[at + q] = 20000;
            }
            const pcm = block ? b64(block) : (zeros[n] || (zeros[n] = b64(new Int16Array(n))));
            const arrival = sec > 3600 && sec <= 3606 ? 3606100 : sim.now + 50;
            pending.push([arrival, made, T0 + made / F * 1000, pcm]);
            made = upTo;
          }
          while (pending.length && pending[0][0] <= sim.now) { const [, at, t, pcm] = pending.shift(); sound.receive({ at, t, pcm }); }
          if (step % 20 === 0) rec.flush();                    // the page's two-second timer
          if (step === 18000) micAtStallStart = seen[0];
          if (step === 18060) micAtStallEnd = seen[0];
        }
        rec.flush();
        const errors = [];
        let k = 0;
        for (const m of marks[0]) {                           // the click of this Mac's sound nearest each microphone click
          while (k + 1 < marks[1].length && Math.abs(marks[1][k + 1] - m) <= Math.abs(marks[1][k] - m)) k++;
          if (marks[1].length) errors.push([m / R, (marks[1][k] - m) / R * 1000]);
        }
        const settled = errors.filter(([t]) => t > 30 && !(t > 1799 && t < 1808) && !(t > 3599 && t < 3608));
        const worst = settled.reduce((w, [, e]) => Math.max(w, Math.abs(e)), 0);
        return { worstMs: Math.round(worst), lastMs: Math.round(settled[settled.length - 1][1]), pairs: settled.length,
                 micClicks: marks[0].length, macClicks: marks[1].length, ended, micTotal: seen[0], macTotal: seen[1], micSent,
                 heldDuringStallS: (micAtStallEnd - micAtStallStart) / R, uncorrectedMs: Math.round(SECONDS * DRIFT * 1000) };
      } finally { N.clock.wall = kept.wall; N.clock.now = kept.now; }""")
    check("two hours with this Mac's clock 200 ppm fast: the two tracks stay within 60 ms (uncorrected it would be 1440 ms)",
          sim["worstMs"] <= 60 and sim["pairs"] > 7000, sim)
    check("a stalled or vanished Mac sound never holds back the microphone's track (it kept being sent through the stall)",
          sim["heldDuringStallS"] >= 5.0 and sim["micTotal"] >= sim["micSent"] - 2 * 16000, sim)
    check("and that is reported the way a lost source is ('ended')", sim["ended"] >= 1, sim)

    # ------------------------------------------------------------------ a second copy, a server that dies
    b = launch(CONTROL_B)
    sb = wait("the second copy loads", lambda: (lambda st: st if st["loaded"] else None)(status(CONTROL_B)), 45)
    check("a second start uses the running server (does not own it)", sb and not sb["owned"] and sb["port"] == SERVER_PORT, sb)
    check("and shows the same library", wait("the second copy's side panel", lambda: courses(CONTROL_B) >= 1, 15))
    press_menu(CONTROL_B, "quit")
    b.wait(timeout=30)
    check("quitting the second copy leaves the first one's server running", b.returncode == 0 and server_answers(), b.returncode)
    b = None

    # the server dies in the middle of a recording (the stand-in microphone), with notes just typed
    js(CONTROL_A, f"await go({{ name: 'class', slug: '{done['slug']}', cid: '{done['cid']}' }}); return 1")
    cid, crash_dir = new_class("Crash test")
    js(CONTROL_A, STAND_IN_MIC + " window.samePage = true; return 1;")
    source("mic")
    js(CONTROL_A, "document.querySelector('#record').click(); return 1", gesture=True)
    wait("recording", lambda: js(CONTROL_A, "return !!rec.recorder"), 15)
    js(CONTROL_A, "liveEditor.view.focus(); liveEditor.view.dispatch({ changes: { from: 0, insert: 'typed just before' } }); return 1")
    time.sleep(6.5)
    old_pid = status(CONTROL_A)["serverPid"]
    page_had = js(CONTROL_A, totals)[0]
    js(CONTROL_A, "window.oldToken = sessionToken; return 1")
    os.kill(old_pid, signal.SIGKILL)
    st = wait("the window says the server stopped", lambda: (lambda x: x if x["status"] and "recording stopped" in x["statusBody"] else None)(status(CONTROL_A)), 15)
    check("a server that dies while recording: the window says so, that the recording stopped, and offers to start it again",
          st and st["status"] == "Veuly stopped working" and st["statusButtons"] == ["Start again", "Quit Veuly"] and not st["awake"], st)
    held = js(CONTROL_A, "return { recording: !!rec.recorder, queue: rec.queue.length }")
    check("the page stopped recording at once (nothing records out of sight), and kept what the server never got", held == {"recording": False, "queue": held["queue"]}
          and held["queue"] >= 1, held)
    call(CONTROL_A, "/answers", {"buttons": [0]})             # Cancel
    press_menu(CONTROL_A, "quit")
    time.sleep(1.5)
    q = status(CONTROL_A)["asked"][-1]
    check("quitting now asks first (Part of the recording is not saved), and Cancel stays", q["title"] == "Part of the recording is not saved"
          and q["buttons"] == ["Cancel", "Quit anyway"] and a.poll() is None, q)
    call(CONTROL_A, "/press", {"index": 0})
    st = wait("started again", lambda: (lambda x: x if x["loaded"] and x["serverRunning"] and x["serverPid"] != old_pid and not x["status"] else None)(status(CONTROL_A)), 45)
    check("Start again brings the server and the page back, on the same address", st and st["owned"] and st["port"] == SERVER_PORT
          and wait("the page again", lambda: courses(CONTROL_A) >= 1, 15), st)
    sent = wait("what the page kept is sent", lambda: js(CONTROL_A, "return window.samePage === true && !rec.queue.length"), 20)
    on_disk = (crash_dir / "audio.pcm").stat().st_size / 2
    notes = [p.read_text() for p in crash_dir.glob("*my-notes.md")]
    check("the same page goes on (not reloaded): all it had recorded reached the new server, and the notes typed just before",
          sent and on_disk >= page_had and notes == ["typed just before"], (page_had, on_disk, notes))
    check("and the class offers to finish it or go on recording", "Finish class" in js(CONTROL_A, "return document.querySelector('#status-slot').textContent"))
    took = js(CONTROL_A, """const ask = (token, credentials) => fetch('/api/state', { headers: { 'X-TA': token }, credentials }).then((r) => r.status);
      return { mine: await ask(sessionToken, 'same-origin'), old: await ask(window.oldToken, 'same-origin'), noCookie: await ask(sessionToken, 'omit'),
               stored: sessionStorage.getItem('ta-token') === sessionToken, changed: sessionToken !== window.oldToken, session: rec.session }""")
    check("Start again: the page took the new server's session itself (its cookie for this data folder, the new token in X-TA),"
          " and let the class go (no session held)", took == {"mine": 200, "old": 403, "noCookie": 403, "stored": True, "changed": True, "session": None}, took)

    # Finish pressed just before the server died (it never answered): done once the server is back.
    _, finish_dir = new_class("Finish test")
    source("mic")
    js(CONTROL_A, "document.querySelector('#record').click(); return 1", gesture=True)
    wait("recording", lambda: js(CONTROL_A, "return !!rec.recorder"), 15)
    time.sleep(5)
    old_pid = status(CONTROL_A)["serverPid"]
    os.kill(old_pid, signal.SIGSTOP)                          # it hears nothing more: the Finish below gets no answer
    js(CONTROL_A, "window.finishSamples = rec.recorder.tracks[0].flushed + rec.recorder.tracks[0].length; document.querySelector('#finish').click(); return 1", gesture=True)
    time.sleep(2)
    os.kill(old_pid, signal.SIGKILL)
    wait("the window says the server stopped", lambda: status(CONTROL_A)["status"] == "Veuly stopped working", 15)
    call(CONTROL_A, "/press", {"index": 0})
    st = wait("started again", lambda: (lambda x: x if x["loaded"] and x["serverRunning"] and x["serverPid"] != old_pid and not x["status"] else None)(status(CONTROL_A)), 45)
    fin = wait("finished", lambda: (lambda m: m if m["status"] != "recording" else None)(json.loads((finish_dir / "class.json").read_text())), 30) or {}
    check("a Finish pressed just before the server died is done after Start again: the class is finished with all it recorded",
          st and fin.get("status") in ("processing", "error") and (finish_dir / "audio.wav").exists()
          and seconds_of(finish_dir / "audio.wav")[0] * 16000 >= js(CONTROL_A, "return window.finishSamples") - 1600
          and js(CONTROL_A, "return window.samePage === true && !rec.session && !rec.recorder && !rec.finishing"), (fin.get("status"), fin.get("error_kind")))

    # Start again, but the server's address was taken meanwhile: it serves another port, which this page cannot reach.
    # The page hands the app what only it holds (the queue, the typed notes), and the new page sends it.
    _, moved_dir = new_class("Move test")
    source("mic")
    js(CONTROL_A, "document.querySelector('#record').click(); return 1", gesture=True)
    wait("recording", lambda: js(CONTROL_A, "return !!rec.recorder"), 15)
    time.sleep(5.5)
    old_pid = status(CONTROL_A)["serverPid"]
    page_had = js(CONTROL_A, totals)[0]
    os.kill(old_pid, signal.SIGKILL)
    wait("the window says the server stopped", lambda: (lambda x: x if x["status"] and "recording stopped" in x["statusBody"] else None)(status(CONTROL_A)), 15)
    js(CONTROL_A, "liveEditor.view.focus(); liveEditor.view.dispatch({ changes: { from: 0, insert: 'typed before the move' } }); return 1")
    time.sleep(1)
    check("(the page holds what the server never got: the end of the recording, and notes typed since)", js(CONTROL_A, "return rec.queue.length > 0 && dirty"))
    (HOME / "server.json").write_text(json.dumps({"port": MOVED_PORT}))     # as if another program had taken the old one
    call(CONTROL_A, "/press", {"index": 0})
    st = wait("started again elsewhere", lambda: (lambda x: x if x["loaded"] and x["serverRunning"] and x["port"] == MOVED_PORT and not x["status"] else None)(status(CONTROL_A)), 45)
    sent = wait("sent", lambda: js(CONTROL_A, f"return S.view.name === 'class' && S.view.cid === '{moved_dir.name}' && !rec.session && !rec.queue.length"), 30)
    on_disk = (moved_dir / "audio.pcm").stat().st_size / 2 if (moved_dir / "audio.pcm").exists() else 0
    notes = [p.read_text() for p in moved_dir.glob("*my-notes.md")]
    check("Start again on another port: the new page took the recording's queue and the typed notes from the old one, sent them,"
          " shows that class and lets it go", st and sent and on_disk >= page_had and notes == ["typed before the move"]
          and "Finish class" in js(CONTROL_A, "return document.querySelector('#status-slot').textContent"), (page_had, on_disk, notes))
    old_pid = status(CONTROL_A)["serverPid"]                  # and back to the usual port, for what follows
    os.kill(old_pid, signal.SIGKILL)
    wait("the window says the server stopped", lambda: status(CONTROL_A)["status"] == "Veuly stopped working", 15)
    (HOME / "server.json").write_text(json.dumps({"port": SERVER_PORT}))
    call(CONTROL_A, "/press", {"index": 0})
    wait("back", lambda: (lambda x: x if x["loaded"] and x["port"] == SERVER_PORT and not x["status"] else None)(status(CONTROL_A)), 45)
    wait("the page", lambda: courses(CONTROL_A) >= 1, 15)
    js(CONTROL_A, "window.samePage = true; return 1")
    js(CONTROL_A, STAND_IN_MIC + " return 1;")

    # The same, but the new server refuses the recording's session (the class was taken over meanwhile): what the page
    # held is lost, the page says so plainly, and there is no Save as file.
    _, refused_dir = new_class("Refused test")
    source("mic")
    js(CONTROL_A, "document.querySelector('#record').click(); return 1", gesture=True)
    wait("recording", lambda: js(CONTROL_A, "return !!rec.recorder"), 15)
    time.sleep(6.5)
    old_pid = status(CONTROL_A)["serverPid"]
    js(CONTROL_A, "window.oldToken = sessionToken; return 1")
    os.kill(old_pid, signal.SIGKILL)
    wait("the window says the server stopped", lambda: (lambda x: x if x["status"] and "recording stopped" in x["statusBody"] else None)(status(CONTROL_A)), 15)
    queued = js(CONTROL_A, "return rec.queue.reduce((n, item) => n + (item.data && item.track === 0 ? item.data.length : 0), 0)")
    meta = json.loads((refused_dir / "class.json").read_text())
    meta.update(rec_session="another-tab", rec_seen=time.time())       # another tab holds the class now
    (refused_dir / "class.json").write_text(json.dumps(meta))
    call(CONTROL_A, "/press", {"index": 0})
    wait("started again", lambda: (lambda x: x if x["loaded"] and x["serverRunning"] and x["serverPid"] != old_pid else None)(status(CONTROL_A)), 45)
    lost = wait("the refusal", lambda: js(CONTROL_A, """const n = document.querySelector('#rec-lost');
        return rec.lost && rec.lost.kind === 'recording_elsewhere' && !rec.recorder && !rec.session && !rec.queue.length && n && n.querySelector('#lost-ok')
          ? { text: n.textContent, seconds: rec.lost.seconds, rescue: !!document.querySelector('#save-unsent') } : null"""), 25)
    check("a session the restarted server refuses: the page stops, says plainly how much is lost and offers no file",
          lost and abs(lost["seconds"] - queued / 16000) < 0.01 and queued > 0 and not lost["rescue"] and "never reached Veuly and are lost" in lost["text"], (queued, lost) if lost else
          (queued, js(CONTROL_A, """return { lost: rec.lost, recorder: !!rec.recorder, session: rec.session, queue: rec.queue.length,
             handing: rec.handing, failing: rec.failing, notice: !!document.querySelector('#rec-lost'), view: S.view, toast: document.querySelector('#toast').textContent }"""),
           json.loads((refused_dir / "class.json").read_text()).get("rec_session")))
    js(CONTROL_A, "document.querySelector('#lost-ok').click(); return 1", gesture=True)

    # And one the new server takes but cannot write (its recording is read-only now): not taken within 20 s, it is said
    # to be lost the same way, never left in a queue that a new recording would drop.
    _, slow_dir = new_class("Slow test")
    source("mic")
    js(CONTROL_A, "document.querySelector('#record').click(); return 1", gesture=True)
    wait("recording", lambda: js(CONTROL_A, "return !!rec.recorder"), 15)
    time.sleep(4.5)
    old_pid = status(CONTROL_A)["serverPid"]
    os.kill(old_pid, signal.SIGKILL)
    wait("the window says the server stopped", lambda: (lambda x: x if x["status"] and "recording stopped" in x["statusBody"] else None)(status(CONTROL_A)), 15)
    queued = js(CONTROL_A, "return rec.queue.reduce((n, item) => n + (item.data && item.track === 0 ? item.data.length : 0), 0)")
    (slow_dir / "audio.pcm").chmod(0o400)
    try:
        call(CONTROL_A, "/press", {"index": 0})
        wait("started again", lambda: (lambda x: x if x["loaded"] and x["serverRunning"] and x["serverPid"] != old_pid else None)(status(CONTROL_A)), 45)
        late = wait("said after 20 s", lambda: js(CONTROL_A, """const n = document.querySelector('#rec-lost');
            return rec.lost && rec.lost.kind === 'not_sent' && !rec.session && !rec.queue.length && n && n.querySelector('#lost-ok')
              ? { text: n.textContent, seconds: rec.lost.seconds } : null"""), 40)
    finally:
        (slow_dir / "audio.pcm").chmod(0o600)
    check("a recording the new server does not take in time: the page says so, and how much is lost",
          late and abs(late["seconds"] - queued / 16000) < 0.01 and queued > 0 and "could not save the end of this recording" in late["text"], (queued, late))
    js(CONTROL_A, "document.querySelector('#lost-ok').click(); return 1", gesture=True)

    # ------------------------------------------------------------------ the transcript's pane and Catch me up in WebKit (stand-in whisper-cli and `claude`)
    tools, live_tools = FAKE_HOME / ".local" / "bin", WORK / "live-tools"
    tools.mkdir(parents=True, exist_ok=True)
    live_tools.mkdir()
    (tools / "claude").write_text(f"""#!{sys.executable}
import json, sys
if "--version" in sys.argv:
    print("9.9.9 (Claude Code)")
    sys.exit(0)
sys.stdin.read()
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "result": "## Main ideas so far\\n\\n- Limits, said in **WebKit**.\\n\\n## Being explained now\\n\\n- Continuity.", "stop_reason": "end_turn"}}))
""")
    (tools / "claude").chmod(0o755)
    (live_tools / "whisper-cli").write_text(f"""#!{sys.executable}
import sys
a = sys.argv
if "--help" in a:
    print("usage: whisper-cli")
    sys.exit(0)
for o in [a[i + 1] for i, x in enumerate(a) if x == "-of"]:
    open(o + ".srt", "w").write("1\\n00:00:00,500 --> 00:00:03,000\\nSomething said about limits.\\n\\n")
""")
    (live_tools / "whisper-cli").chmod(0o755)
    (live_tools / "ggml-live.bin").write_bytes(b"\0" * 1024)
    plain_settings = (HOME / "settings.json").read_text()
    (HOME / "settings.json").write_text(json.dumps({**json.loads(plain_settings), "engine": "claude-code", "whisper_path": str(live_tools / "whisper-cli"),
                                                    "whisper_model": str(live_tools / "ggml-live.bin"), "live_transcript": True, "live_short": True}))
    js(CONTROL_A, "S.settings.live_transcript = true; S.settings.engine = 'claude-code'; return 1")
    _, catch_dir = new_class("Catch test")
    js(CONTROL_A, STAND_IN_MIC + " return 1;")
    source("mic")
    js(CONTROL_A, "document.querySelector('#record').click(); return 1", gesture=True)
    check("the transcript's pane fills itself in WebKit, in short pieces", wait("a line", lambda: js(CONTROL_A, "return !!document.querySelector('#live-pane .live-line')"), 40))
    check("Catch me up is on top of it, and says it uses the Claude plan", "Claude plan" in js(CONTROL_A, "return document.querySelector('#live-pane #catch-cost').textContent"))
    js(CONTROL_A, "document.querySelector('#catch-go').click(); return 1", gesture=True)
    drawn = wait("the answer", lambda: js(CONTROL_A, "const x = document.querySelector('#catch-text'); return x ? x.textContent : ''"), 40)
    check("its answer is drawn in the pane by md.js (a stand-in claude)", drawn and "Limits" in drawn and js(CONTROL_A, "return document.querySelectorAll('#catch-text h2').length") == 2,
          js(CONTROL_A, "return document.querySelector('#catch-out').textContent"))
    check("it says how far it reaches", js(CONTROL_A, "return document.querySelector('#catch-reach').textContent").startswith("Up to "))
    js(CONTROL_A, "document.querySelector('#catch-put').click(); return 1", gesture=True)
    put = js(CONTROL_A, "return liveEditor.view.state.doc.toString()")
    check("Put it in my notes: at the end of My notes, without stars", "Catch-up, up to" in put and "- Limits, said in WebKit." in put, put)
    check("with no typed time on its lines", js(CONTROL_A, "return Object.keys(liveEditor.getTimes()).length") == 0)
    check("the recording went on meanwhile", js(CONTROL_A, "return !!rec.recorder && !rec.dead"))
    js(CONTROL_A, "document.querySelector('#finish').click(); return 1", gesture=True)
    check("Finish still writes the note", wait("done", lambda: json.loads((catch_dir / "class.json").read_text()).get("status") == "done", 40), (catch_dir / "class.json").read_text()[:300])
    (HOME / "settings.json").write_text(plain_settings)
    js(CONTROL_A, "S.settings.engine = ''; await loadState(); return 1")      # the page's lists no longer say that class is being written
    (tools / "claude").unlink()

    # ------------------------------------------------------------------ a folder kept in step: the app's folder panel (a test panel here)
    kept_in_step = WORK / "Slides folder"
    kept_in_step.mkdir()
    (kept_in_step / "Deck.txt").write_text("Limits and continuity")
    os.utime(kept_in_step / "Deck.txt", (time.time() - 60, time.time() - 60))
    sync_slug = js(CONTROL_A, "return S.courses[0].slug")
    js(CONTROL_A, f"await go({{ name: 'course-settings', slug: '{sync_slug}' }}); return 1")
    check("a folder is chosen in a panel of the app, with no path to type", js(CONTROL_A, "return !!document.querySelector('#sync-choose') && !document.querySelector('#sync-path')"))
    synced = lambda: js(CONTROL_A, f"const c = await api('GET', '/api/courses/{sync_slug}/synced'); return c.folders")   # noqa: E731
    js(CONTROL_A, "document.querySelector('#sync-choose').click(); return 1", gesture=True)
    time.sleep(1)
    check("Cancel in the panel links nothing", synced() == [])
    call(CONTROL_A, "/folder", {"path": str(kept_in_step)})
    js(CONTROL_A, "document.querySelector('#sync-choose').click(); return 1", gesture=True)
    came = wait("the folder's file", lambda: js(CONTROL_A, "return document.querySelector('#files-slides').textContent.includes('Deck.txt') && document.querySelector('#sync-list').textContent"), 20)
    check("the panel's folder is linked, and its file comes in with its mark", came and "Slides folder" in came and "from the folder" in js(CONTROL_A, "return document.querySelector('#files-slides').textContent"), came)
    check("the person's folder is as it was", [f.name for f in kept_in_step.iterdir()] == ["Deck.txt"])
    js(CONTROL_A, f"await api('DELETE', `/api/courses/{sync_slug}/synced/${{(await api('GET', '/api/courses/{sync_slug}/synced')).folders[0].id}}`); await loadState(); return 1")

    # ------------------------------------------------------------------ quitting
    server_pid = status(CONTROL_A)["serverPid"]
    press_menu(CONTROL_A, "quit")
    a.wait(timeout=40)
    check("Quit (Cmd+Q) with nothing at stake quits without asking", a.returncode == 0, a.returncode)
    alive = subprocess.run(["ps", "-o", "pid=", "-g", str(server_pid)], capture_output=True, text=True).stdout.split()
    check("and stops the server it started, and anything that server started", not server_answers() and not alive, alive)
    kept = json.loads((HOME / "server.json").read_text())
    check("the server kept its port for next time (and no token)", kept == {"port": SERVER_PORT}, kept)

    # ------------------------------------------------------------------ the app killed during a transcription
    whisper, pids = WORK / "whisper-cli", WORK / "whisper.pids"            # a stand-in transcriber: it only sleeps
    whisper.write_text(f"#!/bin/sh\necho $$ >> '{pids}'\nexec /bin/sleep 60\n")
    whisper.chmod(0o755)
    (WORK / "model.bin").write_bytes(b"\0" * 1024)
    settings = json.loads((HOME / "settings.json").read_text())
    settings.update(whisper_path=str(whisper), whisper_model=str(WORK / "model.bin"))
    (HOME / "settings.json").write_text(json.dumps(settings))
    a = launch(CONTROL_A)
    wait("the page loads", lambda: status(CONTROL_A)["loaded"], 45)
    wait("the page's own start", lambda: js(CONTROL_A, "return S.view.name === 'home'") and courses(CONTROL_A) >= 1, 15)
    # the server alone dies (a crash) during a transcription: the app stops the transcriber it had started
    js(CONTROL_A, f"await api('POST', '/api/classes/{done['slug']}/{slow_dir.name}/finish', {{}}); return 1")
    child = wait("the transcriber starts", lambda: pids.exists() and int(pids.read_text().split()[0]), 30)
    listed = wait("the server tells the app", lambda: child in status(CONTROL_A)["serverChildren"], 5)
    old_pid = status(CONTROL_A)["serverPid"]
    os.kill(old_pid, signal.SIGKILL)
    gone = wait("the transcriber stops", lambda: subprocess.run(["ps", "-p", str(child)], capture_output=True).returncode != 0, 10)
    check("the server killed during a transcription: the app stops the transcriber that server started (its own process group)",
          listed and gone and wait("stopped", lambda: status(CONTROL_A)["status"] == "Veuly stopped working", 10), (child, listed))
    if not gone:
        os.kill(child, signal.SIGKILL)
    pids.unlink()
    call(CONTROL_A, "/press", {"index": 0})
    wait("started again", lambda: (lambda x: x if x["loaded"] and x["serverRunning"] and x["serverPid"] != old_pid and not x["status"] else None)(status(CONTROL_A)), 45)
    meta = wait("interrupted", lambda: (lambda m: m if m["status"] == "error" else None)(json.loads((slow_dir / "class.json").read_text())), 10) or {}
    check("and once started again, that class says it was interrupted (Try again)", meta.get("error_kind") == "interrupted", meta.get("error_kind"))
    js(CONTROL_A, f"await go({{ name: 'class', slug: '{done['slug']}', cid: '{cid}' }}); return 1")
    wait("the unfinished class", lambda: js(CONTROL_A, "return !!document.querySelector('#status-slot button.main')"), 15)
    js(CONTROL_A, "document.querySelector('#status-slot button.main').click(); return 1", gesture=True)      # Finish class
    child = wait("the transcriber starts", lambda: pids.exists() and int(pids.read_text().split()[0]), 30)
    os.kill(a.pid, signal.SIGKILL)
    a.wait(10)
    gone = wait("the transcriber stops", lambda: subprocess.run(["ps", "-p", str(child)], capture_output=True).returncode != 0, 10)
    meta = json.loads((crash_dir / "class.json").read_text())
    check("the app killed (crash, Force Quit) during a transcription: its server quits and stops the transcriber too;"
          " the class says it was interrupted", gone and not server_answers() and meta["status"] == "error" and meta["error_kind"] == "interrupted",
          (child, meta.get("status"), meta.get("error_kind")))
    if not gone:
        os.kill(child, signal.SIGKILL)
finally:
    for proc in (a, b):
        if proc and proc.poll() is None:
            proc.kill()
    if not ok:
        for f in sorted(WORK.glob("app-*.log")):
            print(f"--- {f.name}\n{f.read_text()[-3000:]}")
    shutil.rmtree(WORK, ignore_errors=True)
print("ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
