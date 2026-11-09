"""Pages of the course's files in a note, in a real browser with a stand-in engine (no Claude usage): the picture drawn
like any other with its file and page under it, "p. 2" opening the PDF at that page, a wrong mark leaving nothing, the real
PDF, Copy as Markdown, Copy for e-mail, the digest, the
setting on a course's and a project's settings page (and what it changes in the next request), Info, Spanish and Catalan,
1280 and 390 px. It draws the pages with the computer's real pdftoppm or sips.

  python3 tests/e2e_slideimg.py [port]       screenshots land in the current folder
"""
import json, os, pathlib, sys, threading
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from test_slideimg import DIAGONAL, make_pdf       # sets TRANSCAPP_HOME and VEULY_SECRETS through test_core: a temporary folder, never the Keychain
from playwright.sync_api import sync_playwright
from transcapp import engine, paths, server
import guards  # noqa: E402,F401  (no real Claude Code, Desktop or Downloads: see tests/guards.py)

assert "TRANSCAPP_HOME" in os.environ and os.environ.get("VEULY_SECRETS") == "file"
paths.save_settings({"setup_done": True, "engine": "api", "ui_lang": "en", "auto_images": False, "mode": "course"})
FILE = "T1.1 Optical Systems.pdf"
requests = []
answer = {"text": ""}


class Engine:
    name = "stand-in"

    def final_note(self, req):
        requests.append(req)
        return engine.NoteResult(answer["text"], "fake", "fake-1", 0.0, {})


port = int(sys.argv[1]) if len(sys.argv) > 1 else 58770
httpd, app = server.serve(ROOT / "web", port, engine_factory=lambda s: Engine())
threading.Thread(target=httpd.serve_forever, daemon=True).start()
lib = app.lib
ok = True
def check(label, value, extra=""):
    global ok; ok = ok and bool(value); print(("ok   " if value else "FAIL ") + label, extra if not value else "")


DECK = make_pdf([{"text": "Agenda of the class"},
                 {"text": "Attenuation against wavelength", "images": [("chart", 120, 90, 700, 340)], "draw": DIAGONAL},
                 {"text": "What to remember"}])
SPOKEN = "1\n00:00:00,000 --> 00:00:04,000\n" + "Attenuation against wavelength, agenda of the class, what to remember. " * 6 + "\n"
GOOD = f"""# Attenuation in fibre

The loss of silica has three windows, and the class went through the curve.

## The curve

Rayleigh scattering falls with the wavelength, and the OH peak splits the windows.

<<page "{FILE}" 2 | Attenuation against wavelength, with the three windows>>

The lowest loss is at 1550 nm.
"""


def seed(name, kind, material):
    slug = lib.create_course(name, kind=kind)["slug"]
    lib.add_material(slug, FILE, DECK, material)
    return slug


def written(slug, text):
    answer["text"] = text
    cid = lib.create_class(slug)["id"]
    lib.write_text(slug, cid, "transcript", SPOKEN)
    if lib.course(slug)["kind"] == "course":
        lib.update_meta(slug, cid, materials=[FILE])
    app.pipeline._run(slug, cid, "write")
    return cid


school = seed("Optical networks", "course", "slides")
work = seed("Network migration", "project", "docs")
good = written(school, GOOD)
check("the request says page 2 holds a figure and only that", "[page 2: figure]" in dict(requests[-1].materials)[FILE]
      and "[page 1]" in dict(requests[-1].materials)[FILE] and requests[-1].show_pages)
wrong = written(school, f'# Wrong\n\nBefore.\n\n<<page "{FILE}" 99 | nothing here>>\n\n<<page "Another course.pdf" 1>>\n\nAfter.\n')
minutes = written(work, GOOD.replace("the class went", "the meeting went"))

DIGESTED = written(school, f"""# Windows

Text.

## Exam and admin

- The three windows are in the exam on 12 December.

<<page "{FILE}" 2 | The windows to learn>>
""")


def pdf_browser(full):
    """Playwright's headless shell, as tests/e2e_rewrite_pdf.py: its full Chrome loads a page given with --print-to-pdf and never prints it."""
    if not full:
        return ""
    d = pathlib.Path(full)
    while d.parent != d and not d.name.startswith("chromium-"):
        d = d.parent
    shell = d.parent / d.name.replace("chromium-", "chromium_headless_shell-", 1)
    found = sorted(shell.glob("*/chrome-headless-shell")) + sorted(shell.glob("*/*/chrome-headless-shell"))
    return str(found[-1]) if found else full


with sync_playwright() as p:
    os.environ.setdefault("VEULY_BROWSER", pdf_browser(p.chromium.executable_path))
    b = p.chromium.launch()
    pg = b.new_context(viewport={"width": 1280, "height": 900}, permissions=["clipboard-read", "clipboard-write"], accept_downloads=True).new_page()
    errors = []; pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"http://127.0.0.1:{app.port}/?t={app.token}"); pg.wait_for_selector("#side")
    go = lambda view: pg.evaluate(f"() => go({json.dumps(view)})")

    def language(lang, mode):
        paths.save_settings({"ui_lang": lang, "mode": mode})
        pg.reload(); pg.wait_for_selector("#side")

    def sees_picture():
        return pg.evaluate("""() => { const i = document.querySelector('#note-view img[src*="/img/page-"]');
            const r = i && i.getBoundingClientRect();
            return i && { width: i.naturalWidth, shown: r.width, right: r.right, fits: document.documentElement.scrollWidth <= innerWidth, alt: i.alt,
                          caption: i.parentElement.textContent.trim() }; }""")

    # ---- the note
    go({"name": "class", "slug": school, "cid": good}); pg.wait_for_selector("#note-view")
    pg.wait_for_function("""() => { const i = document.querySelector('#note-view img[src*="/img/page-"]'); return i && i.complete && i.naturalWidth > 0; }""")
    seen = sees_picture()
    check("the page is drawn like any picture, as wide as the note", seen and seen["width"] == 1400 and seen["fits"] and seen["shown"] > 300, seen)
    check("its caption says what to see, the file and the page", seen and seen["caption"] == f"Attenuation against wavelength, with the three windows ({FILE}, p. 2)", seen)
    check("no mark on the page", "<<" not in pg.inner_text("#note-view"))
    pg.wait_for_timeout(350)        # the page's fade-in
    pg.screenshot(path="slideimg-1-note-1280.png", full_page=True)
    link = pg.locator("#note-view em .slide-ref")
    check("the page in the caption is a link to the file", link.count() == 1 and link.inner_text() == "p. 2", link.count())
    link.click(); pg.wait_for_selector("#deck-pop")
    check("and opens that page", pg.get_attribute("#deck-frame", "src").endswith("#page=2") and FILE in pg.inner_text("#deck-h"), pg.get_attribute("#deck-frame", "src"))
    pg.keyboard.press("Escape"); pg.wait_for_selector("#deck-pop", state="detached")
    note = lib.read_text(school, good, "note")
    check("the note itself keeps a plain picture line and no link", "\n![Attenuation against wavelength, with the three windows](img/page-" in note
          and f"\n*Attenuation against wavelength, with the three windows ({FILE}, p. 2)*\n" in note and "<<" not in note, note)
    printed = pg.evaluate("(note) => window.renderMarkdown(note, { imgBase: '/x/img/' })", note)
    check("the PDF's page draws it too, with no button", 'src="/x/img/page-' in printed and "slide-ref" not in printed and f"({FILE}, p. 2)" in printed, printed[:300])

    pg.set_viewport_size({"width": 390, "height": 844})
    pg.wait_for_timeout(200)
    seen = sees_picture()
    check("at 390 px it stays inside the page", seen and seen["fits"] and seen["right"] <= 390, seen)
    pg.screenshot(path="slideimg-2-note-390.png", full_page=True)
    pg.set_viewport_size({"width": 1280, "height": 900})

    # ---- where the note goes from the page
    go({"name": "class", "slug": school, "cid": good}); pg.wait_for_selector("#note-view")
    pg.get_by_role("button", name="Copy as Markdown").click()
    pg.wait_for_function("() => document.querySelector('#toast') && document.querySelector('#toast').textContent.includes('Copied')")
    copied = pg.evaluate("() => navigator.clipboard.readText()")
    check("Copy as Markdown is the note as written: a picture line, the file and page, no mark and no link", copied == note, copied[:200])
    pg.get_by_role("button", name="Copy for e-mail").click()
    pg.wait_for_function("() => document.querySelector('#toast') && document.querySelector('#toast').textContent.includes('formatting')")
    mail = pg.evaluate("""async () => { const out = {}; for (const item of await navigator.clipboard.read())
        for (const type of item.types) out[type] = await (await item.getType(type)).text(); return out; }""")
    check("Copy for e-mail names the picture, with its file and page, and loads nothing",
          "[image: Attenuation against wavelength, with the three windows]" in mail.get("text/plain", "") and f"({FILE}, p. 2)" in mail.get("text/plain", "")
          and "<img" not in mail.get("text/html", ""), mail.get("text/plain", "")[:300])
    pg.wait_for_selector("#save-pdf")
    with pg.expect_download(timeout=90000) as d:
        pg.click("#save-pdf")
    d.value.save_as("slideimg-note.pdf")
    from transcapp import pages as _pages       # its pypdf
    import pypdf
    printed_pages = pypdf.PdfReader("slideimg-note.pdf").pages
    pictures = sum(len(page.images) for page in printed_pages)
    words = " ".join(page.extract_text() for page in printed_pages)
    check("the real PDF holds the page's picture and its caption with no link", pictures >= 1 and f"{FILE}, p. 2" in " ".join(words.split()), (pictures, words[:300]))
    go({"name": "digest", "slug": school}); pg.wait_for_selector(".dg-md")
    pg.wait_for_function("""() => { const i = document.querySelector('.dg-md img[src*="/img/page-"]'); return !i || i.complete; }""")
    drawn = pg.evaluate("""() => { const i = document.querySelector('.dg-md img[src*="/img/page-"]'); return i ? i.naturalWidth : -1; }""")
    check("a page in a closing section: the digest does not show broken syntax", "![" not in pg.inner_text("#main") and "<<" not in pg.inner_text("#main") and drawn != 0, drawn)

    # ---- a wrong mark
    go({"name": "class", "slug": school, "cid": wrong}); pg.wait_for_selector("#note-view")
    text = pg.inner_text("#note-view")
    check("a wrong mark leaves nothing", "<<" not in text and "nothing here" not in text and "Before." in text and "After." in text and
          pg.locator('#note-view img').count() == 0, text)
    check("and the class is done", lib.meta(school, wrong)["status"] == "done")

    # ---- the setting
    go({"name": "course-settings", "slug": school}); pg.wait_for_selector("#show-pages")
    check("on by default, with its words", pg.is_checked("#show-pages") and pg.locator("label", has_text="Show pages of your files in the notes").count() == 1)
    check("and it says what happens to a PowerPoint", "PowerPoint" in pg.inner_text("#show-pages-hint"), pg.inner_text("#show-pages-hint"))
    pg.locator("#show-pages").scroll_into_view_if_needed()
    pg.screenshot(path="slideimg-3-setting-1280.png")
    pg.uncheck("#show-pages")
    check("a change waits for Save preferences", pg.is_enabled("#save-prefs"))
    pg.click("#save-prefs"); pg.wait_for_function("() => document.querySelector('#prefs-status').textContent !== '' && document.querySelector('#save-prefs').disabled")
    check("saved in the course", lib.course(school)["prefs"]["pages"] is False)
    off = written(school, f'# Off\n\nText.\n')
    check("off: the next request has no marks and no instruction", not requests[-1].show_pages and ": figure]" not in engine.build_prompt(requests[-1])
          and engine.system_prompt(requests[-1].kind, requests[-1].show_pages) is engine.SYSTEM)
    pg.reload(); pg.wait_for_selector("#side")
    go({"name": "course-settings", "slug": school}); pg.wait_for_selector("#show-pages")
    check("still off when the page comes back", not pg.is_checked("#show-pages"))
    pg.check("#show-pages"); pg.click("#save-prefs")
    pg.wait_for_function("() => document.querySelector('#save-prefs').disabled")
    check("on again", lib.course(school)["prefs"].get("pages") is not False)

    # ---- Info
    pg.click("#open-info"); pg.wait_for_selector("#info-pop")
    info = pg.inner_text("#info-page")
    check("Info says what this does", "Pages of your files in the notes" in info and "makes the picture from your own file" in info and "PowerPoint" in info, info[-400:])
    pg.keyboard.press("Escape"); pg.wait_for_selector("#info-pop", state="detached")

    # ---- work wording
    language("en", "project")
    go({"name": "course-settings", "slug": work}); pg.wait_for_selector("#show-pages")
    check("a project says minutes", pg.locator("label", has_text="Show pages of your files in the minutes").count() == 1 and "meeting" in pg.inner_text("#show-pages-hint"))
    go({"name": "class", "slug": work, "cid": minutes}); pg.wait_for_selector("#note-view")
    check("the minutes show the page", (sees_picture() or {}).get("caption", "").endswith(f"({FILE}, p. 2)"), sees_picture())
    pg.wait_for_timeout(200)
    pg.screenshot(path="slideimg-4-minutes-1280.png", full_page=True)
    pg.click("#open-info"); pg.wait_for_selector("#info-pop")
    check("Info too", "Pages of your files in the minutes" in pg.inner_text("#info-page"))
    pg.keyboard.press("Escape"); pg.wait_for_selector("#info-pop", state="detached")

    # ---- other languages
    language("es", "course")
    go({"name": "course-settings", "slug": school}); pg.wait_for_selector("#show-pages")
    check("in Spanish", pg.locator("label", has_text="Mostrar páginas de tus archivos en los apuntes").count() == 1 and "el apunte muestra esa página" in pg.inner_text("#show-pages-hint"))
    pg.locator("#show-pages").scroll_into_view_if_needed()
    pg.screenshot(path="slideimg-5-setting-es-1280.png")
    pg.set_viewport_size({"width": 390, "height": 844})
    pg.wait_for_timeout(200)
    check("at 390 px nothing overflows", pg.evaluate("() => document.documentElement.scrollWidth <= innerWidth"))
    pg.locator("#show-pages").scroll_into_view_if_needed()
    pg.screenshot(path="slideimg-6-setting-es-390.png")
    pg.click("#open-info"); pg.wait_for_selector("#info-pop")
    check("Info in Spanish", "Páginas de tus archivos en los apuntes" in pg.inner_text("#info-page"))
    pg.keyboard.press("Escape"); pg.wait_for_selector("#info-pop", state="detached")
    language("ca", "project")
    go({"name": "course-settings", "slug": work}); pg.wait_for_selector("#show-pages")
    check("in Catalan, a project", pg.locator("label", has_text="Mostra pàgines dels teus fitxers a les actes").count() == 1 and "les actes mostren aquesta pàgina" in pg.inner_text("#show-pages-hint"))
    pg.locator("#show-pages").scroll_into_view_if_needed()
    pg.screenshot(path="slideimg-7-setting-ca-390.png")
    pg.set_viewport_size({"width": 1280, "height": 900})

    check("no page errors", not errors, errors)
    b.close()
httpd.shutdown()
print("ALL OK" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
