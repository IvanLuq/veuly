"""The course digest and the project register in a real browser, with a stand-in engine (no Claude usage): the course's
"Digest" page next to its classes, the exam radar with a date to come (older classes in a fold), what is still to check, the
formula sheet and what the print window gets of it (and the title that names its PDF; a digest with no formula prints as it
shows), the glossary and its search, every kind of item opening its class at its heading (not at a topic that starts with the
same words), Show me, a digest that answers late not drawing over the page the user went to, the work wording ("Register")
and Spanish, and a phone's width.
Needs `pip install playwright` and a Chromium for it.

  python3 tests/e2e_sections.py [port]        screenshots land in the current folder
"""
import datetime, json, os, pathlib, sys, tempfile, threading, time
os.environ["TRANSCAPP_HOME"] = tempfile.mkdtemp(prefix="transcapp-sections-")
os.environ["VEULY_SECRETS"] = "file"      # never the user's own Keychain (see keystore)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from playwright.sync_api import sync_playwright
from transcapp import digest, engine, library, paths, platform_setup, server
import guards  # noqa: E402,F401  (no real Claude Code, Desktop or Downloads: see tests/guards.py)

platform_setup.find_claude = lambda: None
paths.save_settings({"setup_done": True, "engine": "api", "ui_lang": "en", "auto_images": False, "mode": "course"})


class Engine:
    def final_note(self, req):
        raise engine.EngineError("refused", "The stand-in writes no notes in this test.")


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
today = datetime.date.today()
first, second = today - datetime.timedelta(days=20), today - datetime.timedelta(days=6)
exam = today + datetime.timedelta(days=9)
said = f"{exam.strftime('%A')} {exam.day} {MONTHS[exam.month - 1]}"
FILLER = "\n\n".join(f"Paragraph {k} of a long explanation, so that the closing sections are well below the first screen." for k in range(40))

NOTE_1 = f"""# Fibre attenuation and dispersion

Two properties of the fibre limit a link.

## Attenuation

$$
P(L) = P(0)\\,10^{{-\\alpha L / 10}}
$$

where $P(0)$ is the launch power and $\\alpha$ the attenuation (dB/km).

## Chromatic dispersion

- The value of $D$ at 1550 nm was said quickly (unclear in the recording).

## Exam and admin

- Lab 1 on Thursday: bring the guide printed.

## Glossary of terms

- **EDFA**: erbium-doped fibre amplifier.
- **PMD**: polarisation-mode dispersion.
"""
NOTE_2 = f"""# Optical amplifiers

Amplifiers made WDM possible.

## Gain in decibels

$$
G_{{\\mathrm{{dB}}}} = 10\\log_{{10}}\\frac{{P_{{\\mathrm{{out}}}}}}{{P_{{\\mathrm{{in}}}}}}
$$

{FILLER}

## How it amplifies

**Why there is no crosstalk between channels (asked in past exams):** the lifetime is far longer than a bit.

> [!warning] Check: your notes say 3 dB / the teacher said 3.5 dB (00:13:42)

- You marked ?? at the factor 2: it comes from the signal-spontaneous beat.

## Exam and admin

- **Partial exam: {said}**, topics 1–4, with one A4 sheet of your own formulas.

## Glossary

- **ASE**: amplified spontaneous emission.
- **EDFA**: erbium-doped fibre amplifier, the C-band amplifier.
"""
MINUTES_1 = """# Kick-off

Phase 2 was agreed.

## 1. Scope

- Five sites.

## Decisions

- **D1.** Phase 2 uses SR-MPLS.

## Action items

| Task | Owner | Due |
|---|---|---|
| Request the CAB slot | Marta | not said |

## Open questions

- Who signs the customer notification?
"""
MINUTES_2 = f"""# Weekly follow-up

The lab results were reviewed.

## Open questions from last week

- The CAB slot is booked.

## 1. Lab results

- Single failures recover in under 50 ms.

{FILLER}

## Decisions

- **D1.** The cutover date stays.

## Open questions

- Who is on call in the NOC that night?
"""


def add_class(lib, slug, day, title, note):
    cid = f"{day.isoformat()}_100000"
    d = lib.root / slug / cid
    (d / "img").mkdir(parents=True, exist_ok=True)
    meta = {"id": cid, "title": title, "created": f"{day.isoformat()}T10:00:00", "status": "done", "step": "", "error": "",
            "error_kind": "", "duration_s": 3600, "muted": [], "audio_seq": 0, "materials": None}
    paths.write_atomic(d / "class.json", json.dumps(meta), keep=True)
    (d / library.TEXT_FILES["note"]).write_text(note, encoding="utf-8")
    return cid


httpd, app = server.serve(ROOT / "web", int(sys.argv[1]) if len(sys.argv) > 1 else 0, engine_factory=lambda s: Engine())
threading.Thread(target=httpd.serve_forever, daemon=True).start()
lib = app.lib
school = lib.create_course("Optical Networks", "en", "course")["slug"]
lib.update_course(school, {"prefs": {"extras": ["exam_list", "glossary", "selftest"]}})
c1 = add_class(lib, school, first, "Fibre attenuation", NOTE_1)
c2 = add_class(lib, school, second, "Optical amplifiers", NOTE_2)
work = lib.create_course("Network migration", "auto", "project")["slug"]
m1 = add_class(lib, work, first, "Kick-off", MINUTES_1)
m2 = add_class(lib, work, second, "Weekly follow-up", MINUTES_2)
slow = [0.0]
real_digest = digest.course_digest


def late_digest(lib_, slug):
    time.sleep(slow[0])
    return real_digest(lib_, slug)


digest.course_digest = late_digest

ok = True
def check(label, value, extra=""):
    global ok; ok = ok and bool(value); print(("ok   " if value else "FAIL ") + label, extra if not value else "")
def heading_in_view(pg, text):
    """Whether the note's heading with this text was scrolled into view (go({ section }) brings it to the top, or as far
    as the note goes: a closing section near its end stays lower)."""
    return pg.evaluate("""(text) => {
      const main = document.querySelector('#main');
      const el = [...document.querySelectorAll('#note-view h2, #note-view h3')].find((h) => h.textContent.trim() === text);
      if (!el) return false;
      const top = el.getBoundingClientRect().top - main.getBoundingClientRect().top;
      const atEnd = main.scrollTop + main.clientHeight >= main.scrollHeight - 2;
      return main.scrollTop > 200 && top > -5 && (top < 80 || atEnd) && top < main.clientHeight - 40;
    }""", text)


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
    errors = []; pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"http://127.0.0.1:{app.port}/?t={app.token}"); pg.wait_for_selector("#side")

    # --- the course page has its Digest next to its classes
    pg.click("#side .course-name:has-text('Optical Networks')"); pg.wait_for_selector("#course-title")
    nav = pg.locator("#main nav.course-nav button")
    check("the course page shows its pages: Classes, then Digest", nav.all_inner_texts()[:2] == ["Classes", "Digest"], nav.all_inner_texts())
    nav.nth(1).click(); pg.wait_for_selector(".digest-page #dg-radar")
    check("the Digest tab is the current one", pg.locator("nav.course-nav button[aria-current='page']").inner_text() == "Digest")
    check("it says how many classes it read", "From the final notes of 2 classes." in pg.inner_text(".digest-page .sheet >> nth=1"))

    # --- the exam radar
    coming = pg.locator("#dg-coming li")
    check("the exam date to come is listed, with its weekday", coming.count() == 1 and "Partial exam" in coming.first.inner_text()
          and pg.evaluate(f"() => document.querySelector('#dg-coming .dg-date').textContent") ==
          pg.evaluate(f"() => new Date('{exam.isoformat()}T12:00:00').toLocaleDateString('en', {{ weekday: 'short', day: 'numeric', month: 'short' }})"),
          coming.all_inner_texts())
    short = lambda day: pg.evaluate("(iso) => new Date(iso + 'T12:00:00').toLocaleDateString('en', { day: 'numeric', month: 'short' })", day.isoformat())  # noqa: E731
    groups = pg.locator("#dg-radar .dg-group h4")
    check("what was said in class, newest class first", groups.all_inner_texts()[:2] == [
        f"{short(second)} · Optical amplifiers", f"{short(first)} · Fibre attenuation"], groups.all_inner_texts())
    check("and what the notes say was asked in past exams", "Why there is no crosstalk between channels" in pg.inner_text("#dg-radar"))
    check("the radar's jump says the day of the next exam", pg.locator(".dg-jump button", has_text="Exam radar").locator("b").inner_text() == short(exam))
    pg.screenshot(path="sections-1-digest.png")

    # an item opens its class, on the final note, at its heading
    pg.locator("#dg-radar .dg-list li", has_text="Partial exam").last.click()
    pg.wait_for_selector("#note-view h2"); time.sleep(0.4)
    view = pg.evaluate("() => [S.view.name, S.view.cid, S.tab, S.view.section]")
    check("an exam item opens its class at its exam section", view == ["class", c2, "note", "Exam and admin"] and heading_in_view(pg, "Exam and admin"), view)
    pg.screenshot(path="sections-2-opened.png")

    # --- what is still to check
    pg.evaluate(f"() => go({{ name: 'digest', slug: '{school}' }})"); pg.wait_for_selector("#dg-check")
    tags = pg.locator("#dg-check .dg-tag").all_inner_texts()
    check("what is still to check, each saying why", sorted(tags) == ["Unclear in the recording", "Your notes and the teacher disagree"], tags)
    check("the ?? marks wait in their own fold, closed", pg.locator("#dg-marked").count() == 1 and not pg.evaluate("() => document.querySelector('#dg-marked').open")
          and "(1)" in pg.inner_text("#dg-marked summary"))
    check("the jump to To check counts what is unclear", pg.locator(".dg-jump button", has_text="To check").locator("b").inner_text() == "2")
    pg.locator("#dg-check .dg-src", has_text="Chromatic dispersion").click()
    pg.wait_for_selector("#note-view h2"); time.sleep(0.3)
    check("a section's own button opens the class there", pg.evaluate("() => [S.view.cid, S.view.section]") == [c1, "Chromatic dispersion"])

    # --- the formula sheet, and what the print window gets
    pg.evaluate(f"() => go({{ name: 'digest', slug: '{school}' }})"); pg.wait_for_selector("#dg-formulas")
    formulas = pg.locator("#dg-formulas .dg-formula")
    check("every formula, in the order of the course, drawn", formulas.count() == 2 and pg.locator("#dg-formulas .katex").count() >= 2
          and "Attenuation" in formulas.first.inner_text() and "where" in formulas.first.inner_text())
    pg.evaluate("() => { window.printed = []; window.print = () => { window.printed.push(document.title); }; }")
    pg.click("#dg-print")
    check("Print the formula sheet opens the print window, named after the course for Save as PDF",
          pg.evaluate("() => window.printed") == ["Optical Networks: formula sheet"], pg.evaluate("() => window.printed"))
    pg.evaluate("() => window.dispatchEvent(new Event('afterprint'))")
    check("and the page's own title comes back", pg.evaluate("() => document.title") == "Veuly")
    pg.emulate_media(media="print")
    shown = pg.evaluate("""() => [...document.querySelectorAll('.digest-page > *')].filter((el) => getComputedStyle(el).display !== 'none').map((el) => el.className)""")
    sheet = pg.inner_text(".dg-print")
    check("on paper the page is only the formula sheet", shown == ["dg-print"] and sheet.startswith("Optical Networks: formula sheet")
          and pg.locator(".dg-print .katex").count() >= 2 and not pg.is_visible("#side"), (shown, sheet[:80]))
    check("each formula under its heading, at the left", pg.locator(".dg-print .dg-ph").all_inner_texts() == ["Attenuation", "Gain in decibels"]
          and pg.evaluate("() => getComputedStyle(document.querySelector('.dg-print .katex-display > .katex')).textAlign") == "left",
          pg.locator(".dg-print .dg-ph").all_inner_texts())
    pg.screenshot(path="sections-3-print.png", full_page=True)
    pg.emulate_media(media="screen")
    pg.locator("#dg-formulas .dg-formula").nth(1).click(); pg.wait_for_selector("#note-view h2"); time.sleep(0.3)
    check("a formula opens its class at its heading", pg.evaluate("() => [S.view.cid, S.view.section]") == [c2, "Gain in decibels"])

    # --- the glossary: each term once, and its search
    pg.evaluate(f"() => go({{ name: 'digest', slug: '{school}' }})"); pg.wait_for_selector("#dg-terms")
    terms = pg.locator("#dg-terms dt").all_inner_texts()
    check("each term once, in alphabetical order", terms == ["ASE", "EDFA", "PMD"], terms)
    edfa = pg.locator(".dg-term", has_text="EDFA")
    check("a term defined twice keeps its first class and names the other", edfa.locator(".dg-src").count() == 2
          and "erbium-doped fibre amplifier." in edfa.inner_text())
    edfa.locator(".dg-src").nth(1).click(); pg.wait_for_selector("#note-view h2"); time.sleep(0.3)
    check("the other class opens at its own glossary, whatever it is called there",
          pg.evaluate("() => [S.view.cid, S.view.section]") == [c2, "Glossary"] and heading_in_view(pg, "Glossary"),
          pg.evaluate("() => [S.view.cid, S.view.section]"))
    pg.evaluate(f"() => go({{ name: 'digest', slug: '{school}' }})"); pg.wait_for_selector("#dg-terms")
    many = "\n".join(f"- **Term {k}**: a definition." for k in range(9))
    add_class(lib, school, second - datetime.timedelta(days=2), "Many terms", "# Many\n\nIdea.\n\n## Topic\n\nText.\n\n## Glossary\n\n" + many + "\n")
    pg.evaluate(f"async () => {{ await loadState(); await go({{ name: 'digest', slug: '{school}' }}); }}"); pg.wait_for_selector("#dg-filter")
    pg.fill("#dg-filter", "erbium")
    check("the search keeps the terms whose name or definition match", pg.locator(".dg-term:visible").count() == 1
          and "EDFA" in pg.locator(".dg-term:visible").inner_text())
    pg.fill("#dg-filter", "zzz")
    check("and says when none does", pg.locator(".dg-term:visible").count() == 0 and pg.locator("text=No term matches.").is_visible())

    # --- a digest that answers late does not draw over the page the user went to meanwhile
    slow[0] = 1.5
    pg.click("#side .course-name:has-text('Optical Networks')"); pg.wait_for_selector("#course-title")
    pg.locator("#main nav.course-nav button").nth(1).click()
    time.sleep(0.2)
    pg.evaluate(f"() => {{ go({{ name: 'class', slug: '{school}', cid: '{c1}' }}).catch(fail); }}")     # the user opens a class meanwhile
    time.sleep(2.2)
    check("a late digest does not replace the class opened meanwhile", pg.evaluate("() => [S.view.name, S.view.cid]") == ["class", c1]
          and pg.locator(".digest-page").count() == 0)
    slow[0] = 0.0

    # --- work: the register, in its own words
    pg.click("#mode-project"); pg.wait_for_selector("#side .course-name")
    pg.click("#side .course-name:has-text('Network migration')"); pg.wait_for_selector("#course-title")
    nav = pg.locator("#main nav.course-nav button")
    check("a project's pages are Meetings, then Register", nav.all_inner_texts()[:2] == ["Meetings", "Register"], nav.all_inner_texts())
    nav.nth(1).click(); pg.wait_for_selector("#dg-decisions")
    check("decisions, newest meeting first", pg.locator("#dg-decisions .dg-list li").all_inner_texts() == ["D1. The cutover date stays.", "D1. Phase 2 uses SR-MPLS."],
          pg.locator("#dg-decisions .dg-list li").all_inner_texts())
    check("risks not asked for say how to get them", "do not end with “Risks and blockers”" in pg.inner_text("#dg-risks")
          and "What goes at the end of the minutes" in pg.inner_text("#dg-risks"))
    check("action items are not part of the register", "Request the CAB slot" not in pg.inner_text("#main"))
    pg.screenshot(path="sections-4-register.png")
    pg.locator("#dg-open .dg-list li", has_text="on call").click(); pg.wait_for_selector("#note-view h2"); time.sleep(0.4)
    check("an open question opens its meeting at its heading, not at a topic that starts with the same words",
          pg.evaluate("() => [S.view.cid, S.view.section, S.tab]") == [m2, "Open questions", "note"] and heading_in_view(pg, "Open questions"),
          pg.evaluate("() => [S.view.cid, S.view.section, S.tab]"))
    pg.evaluate(f"() => go({{ name: 'digest', slug: '{work}' }})"); pg.wait_for_selector("#dg-risks .dg-show")
    pg.click("#dg-risks .dg-show"); pg.wait_for_selector("#extra-risks"); time.sleep(0.3)
    box = pg.evaluate("""() => { const el = document.activeElement, r = el.getBoundingClientRect();
      return [S.view.name === (VIEWS['course-settings'] ? 'course-settings' : 'course'), el.id, r.top > 0 && r.bottom < innerHeight, el.checked]; }""")
    check("Show me opens the project's quiz at the tick box that asks for it", box == [True, "extra-risks", True, False], box)

    # --- minutes with a "Decisions" under each topic: the keyboard reaches each one, and it opens where it is
    m3 = add_class(lib, work, today, "Steering", "# Steering\n\nTwo topics.\n\n## 1. Budget\n\nText.\n\n### Decisions\n\n"
                   "- **D1.** The budget is approved.\n\n## 2. Calendar\n\n" + FILLER + "\n\n### Decisions\n\n- **D2.** The cutover moves a week.\n")
    pg.evaluate(f"async () => {{ await loadState(); await go({{ name: 'digest', slug: '{work}' }}); }}"); pg.wait_for_selector("#dg-decisions")
    group = pg.locator("#dg-decisions .dg-group").first
    check("each part says the heading it is under", group.locator(".dg-src").all_inner_texts() == ["Decisions", "2. Calendar"],
          group.locator(".dg-src").all_inner_texts())
    group.locator(".dg-class").focus(); pg.keyboard.press("Tab"); pg.keyboard.press("Tab"); pg.keyboard.press("Enter")
    pg.wait_for_selector("#note-view h2"); time.sleep(0.3)
    check("and the keyboard opens the second where it is", pg.evaluate("() => [S.view.cid, S.view.section]") == [m3, "2. Calendar"]
          and heading_in_view(pg, "2. Calendar"), pg.evaluate("() => [S.view.cid, S.view.section]"))

    # --- a course whose notes have no formula prints its digest as it shows, not an empty formula sheet
    plain = lib.create_course("History of science", "en", "course")["slug"]
    add_class(lib, plain, second, "Newton", "# Newton\n\nIdea.\n\n## Optics\n\nText.\n\n## Exam and admin\n\n- Essay on 3 December.\n")
    pg.evaluate(f"async () => {{ await loadState(); await go({{ name: 'digest', slug: '{plain}' }}); }}"); pg.wait_for_selector("#dg-radar")
    pg.emulate_media(media="print")
    check("a digest with no formula prints as it shows", pg.is_visible("#dg-radar") and not pg.is_visible(".dg-print")
          and pg.locator("#dg-print[disabled]").count() == 1)
    pg.emulate_media(media="screen")

    # --- what the six newest classes said about the exam shows; what older ones said waits in a fold
    for k in range(8):
        add_class(lib, plain, second - datetime.timedelta(days=30 + 7 * k), f"Week {k}", f"# Week {k}\n\nIdea.\n\n## Topic\n\nText.\n\n"
                  f"## Exam and admin\n\n- Reading {k} for next week.\n")
    pg.evaluate(f"async () => {{ await loadState(); await go({{ name: 'digest', slug: '{plain}' }}); }}"); pg.wait_for_selector("#dg-earlier")
    check("older classes' exam lines wait in a closed fold", pg.locator("#dg-radar > .dg-group").count() == 6
          and pg.locator("#dg-earlier .dg-group").count() == 3 and not pg.evaluate("() => document.querySelector('#dg-earlier').open")
          and "Earlier classes (3)" in pg.inner_text("#dg-earlier summary"), pg.locator("#dg-radar > .dg-group").count())

    # --- in Spanish
    pg.evaluate("async () => { await api('POST', '/api/settings', { ui_lang: 'es' }); await loadState(); }")
    pg.evaluate(f"() => go({{ name: 'digest', slug: '{work}' }})"); pg.wait_for_selector("#dg-decisions")
    check("in Spanish the register is Registro", pg.locator("nav.course-nav button[aria-current='page']").inner_text() == "Registro"
          and "Las actas de este proyecto no terminan con «Riesgos y bloqueos»" in pg.inner_text("#dg-risks"))
    pg.click("#mode-course"); pg.wait_for_function("() => S.view.name === 'home' && document.querySelector('#home-tiles')")  # the switch leaves a project's page for home
    pg.evaluate(f"() => go({{ name: 'digest', slug: '{school}' }})"); pg.wait_for_selector("#dg-radar")
    check("and the digest is Resumen", pg.locator("nav.course-nav button[aria-current='page']").inner_text() == "Resumen"
          and "Para el examen" in pg.inner_text("#dg-radar h3"))
    pg.screenshot(path="sections-5-es.png")
    check("no page errors", not errors, errors)

    # --- a phone's width: nothing wider than the screen
    phone = b.new_context(viewport={"width": 390, "height": 844}).new_page()
    phone.goto(f"http://127.0.0.1:{app.port}/?t={app.token}"); phone.wait_for_selector("#side")
    phone.evaluate(f"() => go({{ name: 'digest', slug: '{school}' }})"); phone.wait_for_selector("#dg-glossary")
    width = phone.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
    check("at 390 px the page is no wider than the screen", width[0] <= width[1], width)
    phone.screenshot(path="sections-6-phone.png", full_page=True)
    b.close()
httpd.shutdown()
print("ALL OK" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
