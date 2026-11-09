"""The mk (formula) and mp (drawing) shortcuts and the drawing pad, in a real browser.
Needs `pip install playwright pillow` and a Chromium for it. Uses no Claude usage.

  python3 tests/e2e_shortcuts.py        two screenshots land in the current folder
"""
import json, os, pathlib, re, subprocess, sys, tempfile, time
from playwright.sync_api import sync_playwright
os.environ["VEULY_SECRETS"] = "file"      # the app started below inherits it: never the user's own Keychain (see keystore)
ROOT = pathlib.Path(__file__).resolve().parent.parent
HOME = pathlib.Path(tempfile.mkdtemp(prefix="ta-draw-"))
(HOME / "settings.json").write_text(json.dumps({"setup_done": True, "engine": "claude-code", "ui_lang": "es"}))
srv = subprocess.Popen([sys.executable, str(ROOT / "app.py"), "--no-open"], env=dict(os.environ, TRANSCAPP_HOME=str(HOME)), stdout=subprocess.PIPE, text=True)
link = re.search(r"Open: (\S+)", srv.stdout.readline()).group(1)
ok = True
def check(label, value, extra=""):
    global ok; ok = ok and bool(value); print(("ok   " if value else "FAIL ") + label, extra if not value else "")
doc = lambda pg: pg.evaluate("liveEditor.getValue()")
try:
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
        errors = []; pg.on("pageerror", lambda e: errors.append(str(e))); pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.goto(link); pg.wait_for_selector("#course-name")
        pg.fill("#course-name", "Fisica"); pg.locator("#main .btn.main").click(); pg.wait_for_selector("#new-class"); pg.click("#new-class")
        pg.wait_for_selector("#live-editor .cm-content"); pg.click("#live-editor .cm-content")
        pg.keyboard.type("La energia mk"); check("mk opens a formula", doc(pg) == "La energia $$", repr(doc(pg)))
        pg.keyboard.type("E=mc^2"); pg.keyboard.type("$"); pg.keyboard.type(" fin")
        check("typing inside and stepping over the closing $", doc(pg) == "La energia $E=mc^2$ fin", repr(doc(pg)))
        check("the formula is drawn once the cursor leaves it", pg.locator("#live-editor .katex").count() == 1)
        pg.keyboard.press("Enter"); pg.keyboard.type("lampara y amk no cambian. "); check("mk and mp inside a word are left alone", doc(pg).endswith("lampara y amk no cambian. "), repr(doc(pg)))
        pg.keyboard.type("mk"); pg.keyboard.press("ControlOrMeta+z"); check("undo gives the letters back", doc(pg).endswith("cambian. mk"), repr(doc(pg)))   # Cmd on a Mac
        pg.keyboard.press("Enter"); pg.keyboard.type("`mk` "); check("no shortcut inside code", doc(pg).endswith("`mk` "), repr(doc(pg)))
        pg.keyboard.press("Enter"); pg.keyboard.type("Circuito: mp")
        pg.wait_for_selector(".cm-draw-canvas"); check("mp opens the drawing pad and removes the letters", doc(pg).endswith("Circuito: "), repr(doc(pg)))
        box = pg.locator(".cm-draw-canvas").bounding_box()
        def stroke(points):
            pg.mouse.move(box["x"] + points[0][0], box["y"] + points[0][1]); pg.mouse.down()
            for x, y in points[1:]: pg.mouse.move(box["x"] + x, box["y"] + y, steps=6)
            pg.mouse.up()
        stroke([(60, 60), (260, 60), (260, 200), (60, 200), (60, 60)])
        pg.locator(".cm-draw-color").nth(1).click(); pg.locator(".cm-draw-size").nth(2).click(); stroke([(320, 130), (420, 130), (400, 110)])
        pg.locator(".cm-draw-color").nth(2).click(); pg.locator(".cm-draw-size").nth(0).click(); stroke([(100, 260), (160, 230), (220, 290), (280, 240)])
        pg.keyboard.type("x"); check("keys do not leak into the notes while drawing", doc(pg).endswith("Circuito: "), repr(doc(pg)))
        pg.screenshot(path="6-dibujo.png")
        pg.locator(".cm-draw-btn.main").click(); pg.wait_for_selector("#live-editor img.cm-drawing")
        check("the drawing lands in the notes as an image", doc(pg).endswith("Circuito: \n![](img/drawing.png)\n"), repr(doc(pg)))
        check("the pad closes", pg.locator(".cm-draw").count() == 0)
        files = sorted(f.name for f in HOME.glob("library/*/*/img/*")); check("the image file is saved", files == ["drawing.png"], files)
        from PIL import Image
        im = Image.open(next(HOME.glob("library/*/*/img/drawing.png"))).convert("RGB"); colors = {im.getpixel((int(x * im.width / box["width"]), int(y * im.height / box["height"]))) for x, y in ((160, 60), (370, 130), (10, 10))}
        check("black and red strokes on a white background", {(23, 33, 30), (200, 55, 45), (255, 255, 255)} <= colors, colors)
        pg.keyboard.type("despues"); time.sleep(1.2)
        pg.locator("#live-editor img.cm-drawing").dblclick(); pg.wait_for_selector(".cm-draw-canvas"); time.sleep(0.4)
        pg.locator(".cm-draw-canvas").scroll_into_view_if_needed()      # the pad opens under the picture: a mouse off the window draws nothing
        box = pg.locator(".cm-draw-canvas").bounding_box(); stroke([(500, 60), (560, 120)])
        pg.locator(".cm-draw-btn.main").click(); pg.wait_for_function("() => liveEditor.getValue().includes('drawing-2.png')")
        check("double-click reopens a drawing and saves a new version in place", doc(pg).endswith("Circuito: \n![](img/drawing-2.png)\ndespues"), repr(doc(pg)))
        im2 = Image.open(next(HOME.glob("library/*/*/img/drawing-2.png"))).convert("RGB")
        check("the old strokes are kept under the new one", im2.getpixel((int(160 * im2.width / box["width"]), int(60 * im2.height / box["height"])))[0] < 80)
        pg.click("#draw"); pg.wait_for_selector(".cm-draw-canvas"); pg.keyboard.press("Escape"); check("the Draw button opens it too, Escape closes it", pg.locator(".cm-draw").count() == 0)
        time.sleep(1.2); pg.reload(); pg.wait_for_selector("#side .class"); pg.locator("#side .class").first.click(); pg.wait_for_selector("#live-editor img.cm-drawing")
        check("everything is still there after a reload", "drawing-2.png" in doc(pg) and "$E=mc^2$" in doc(pg))
        pg.screenshot(path="7-notas-con-dibujo.png")
        check("no errors in the browser console", not errors, errors)
        b.close()
finally:
    srv.terminate()
sys.exit(0 if ok else 1)
