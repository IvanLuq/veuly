"""Jump to the source: a moment of the transcript, and a course PDF opened at a page.
Run with the rest:  python3 -m unittest discover tests      (no microphone, whisper or Claude needed)

  GET /files/<course>/materials/<name>: only a PDF that course.json lists
  web/source.js, run with node when it is there: the transcript read into lines, each section of a note matched to the
  moment it starts, a time Claude wrote checked against the words said then, "slide 14" found in a note; its words and
  closing sections the same as materials._words and index_md.closing
"""
import http.client
import json
import pathlib
import shutil
import subprocess
import sys
import threading
import unittest
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_core import Client, FakeEngine  # noqa: E402  (sets TRANSCAPP_HOME and VEULY_SECRETS first)
from transcapp import index_md, materials, paths, server, source  # noqa: E402


def pdf(pages):
    """A small real PDF with one line of text per page."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for n, text in enumerate(pages):
        stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET"
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {len(objs)} 0 R /Resources << /Font << /F1 3 0 R >> >> >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    return out + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


class Served(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd, cls.app = server.serve(ROOT / "web", 0, engine_factory=lambda settings: FakeEngine())
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.c = Client(cls.app)
        cls.c.call("GET", f"/?t={cls.app.token}")
        _, course = cls.c.call("POST", "/api/courses", {"name": "Xarxes Òptiques", "lang": "ca"})
        cls.slug = course["slug"]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def get(self, path, headers=None, cookie=True):
        """A GET as a frame makes it: the cookie, and no X-TA header."""
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=30)
        conn.request("GET", path, headers={**({"Cookie": self.c.cookie} if cookie else {}), **(headers or {})})
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r.status, dict(r.getheaders()), body

    def new_class(self, title="Amplificadors"):
        _, meta = self.c.call("POST", f"/api/courses/{self.slug}/classes", {"title": title})
        return meta["id"], self.app.lib.class_dir(self.slug, meta["id"])


class CoursePdf(Served):
    """"slide 14" in a note opens the deck at #page=14: only a PDF that course.json lists, never another file."""

    def test_a_listed_pdf_and_nothing_else(self):
        name = "Tema 3 – Amplificació.pdf"
        data = pdf(["EDFA", "Raman", "SOA"])
        self.assertEqual(self.c.call("POST", f"/api/courses/{self.slug}/materials?kind=slides", data,
                                     {"X-Filename": urllib.parse.quote(name)}, raw=True)[0], 200)
        self.c.call("POST", f"/api/courses/{self.slug}/materials?kind=syllabus", b"# Syllabus\n", {"X-Filename": "Guia.md"}, raw=True)
        path = f"/files/{self.slug}/materials/{urllib.parse.quote(name)}"
        status, head, body = self.get(path)
        self.assertEqual((status, head["Content-Type"], body), (200, "application/pdf", data))
        self.assertEqual(head["X-Frame-Options"], "SAMEORIGIN", "framed by the app's own pages only")
        self.assertEqual(head["Content-Disposition"], f"inline; filename*=UTF-8''{urllib.parse.quote(name)}")
        self.assertEqual(head["Cross-Origin-Resource-Policy"], "same-origin")
        self.assertEqual(self.get(path, {"Range": "bytes=0-7"})[2], b"%PDF-1.4")
        decomposed = __import__("unicodedata").normalize("NFD", name)      # as a Mac app may send it
        self.assertEqual(self.get(f"/files/{self.slug}/materials/{urllib.parse.quote(decomposed)}")[0], 200)
        self.assertEqual(self.get(path, cookie=False)[0], 403)
        folder = self.app.lib.course_dir(self.slug) / "materials"
        shutil.copy(folder / name, folder / "Not listed.pdf")
        refused = ["Guia.md", "Not%20listed.pdf", f"..%2F{self.slug}%2Fcourse.json", "..%2F..%2Fsettings.json",
                   urllib.parse.quote(f".text/{name}.txt", safe=""), "nothing.pdf"]
        for bad in refused:
            status, _, body = self.get(f"/files/{self.slug}/materials/{bad}")
            self.assertEqual((status, json.loads(body)["kind"]), (404, "not_found"), bad)
        self.assertEqual(self.get(f"/files/nope/materials/{urllib.parse.quote(name)}")[0], 404)


def node():
    return shutil.which("node")


def srt(lines):
    """An SRT transcript from (start, end, text) lines, as whisper and merge_speakers write it."""
    clock = lambda s: f"{int(s) // 3600:02d}:{int(s) % 3600 // 60:02d}:{int(s) % 60:02d},{int(round((s - int(s)) * 1000)):03d}"  # noqa: E731
    return "\n\n".join(f"{i}\n{clock(a)} --> {clock(b)}\n{text}" for i, (a, b, text) in enumerate(lines, 1)) + "\n"


# A class of five topics in a row, said in lines of 8 s, each topic in its own words with the class's common ones mixed in.
TOPICS = [
    ("Attenuation", "attenuation decibels kilometre rayleigh scattering absorption window silica loss budget"),
    ("Chromatic dispersion", "dispersion chromatic pulse broadening wavelength group velocity picoseconds spread"),
    ("Erbium amplifiers", "erbium amplifier pump metastable inversion gain photon stimulated emission"),
    ("Noise figure", "noise figure spontaneous emission osnr chain quantum limit factor"),
    ("Raman amplification", "raman scattering distributed counterpumped terahertz backwards instantaneous"),
]
COMMON = "so now we look at the signal here and this one is important for the exam okay".split()


def lecture():
    lines, at = [], 1.0
    for n, (_, vocab) in enumerate(TOPICS):
        own = vocab.split()
        for i in range(30):
            said = [own[(i + k) % len(own)] for k in range(4)] + [COMMON[(i * 3 + k + n) % len(COMMON)] for k in range(5)]
            lines.append((at, at + 7.5, " ".join(said).capitalize() + "."))
            at += 8
    return lines


# The first four minutes of a lecture of the demo library, and its note's first sections as the page reads them (formulas left out).
FIBRE = [(1.0, 9.5, "Good morning. Today we go into the fibre itself. Attenuation and dispersion, the two things that limit a link."),
         (10.0, 16.5, "Last week we said that the loss is low. Today we see how low, and why."),
         (17.0, 32.5, "The power decays exponentially with the distance. In dB this is a straight line."),
         (33.0, 44.5, "And this is why we always work in dB. In linear the loss is an exponential, in dB it's just alpha times L."),
         (45.0, 53.5, "The typical values, write them down. At 850 nanometres, around two point five dB per kilometre. That's multimode, short distance."),
         (54.0, 63.5, "At 1310, zero point three five. And at 1550, zero point two. This is the minimum, the famous third window."),
         (64.0, 69.5, "Let's look at slide seven. This is the attenuation of silica against the wavelength."),
         (70.0, 80.5, "On the left, the loss falls very fast. That is Rayleigh scattering. It goes like one over lambda to the fourth."),
         (81.0, 91.5, "It comes from tiny fluctuations of the density of the glass, frozen when the fibre was drawn. You cannot remove it. It's the floor."),
         (92.0, 108.5, "Then this peak at 1383. That's the water, the O H ions. Old fibres had a big peak there. The modern ones, the G 652 D, almost don't have it."),
         (109.0, 118.5, "And on the right, above 1600, the infrared absorption of the silica itself starts. So the window closes on both sides."),
         (119.0, 130.5, "And this gives us the bands. O band from 1260 to 1360. E, 1360 to 1460. S, up to 1530. C, 1530 to 1565. And L, up to 1625."),
         (131.0, 144.5, "The C band is the important one, because it has the lowest loss and because it's where the erbium amplifiers work."),
         (145.0, 166.5, "There are also bending losses. If you bend the fibre too much, the light escapes from the core. Macrobends and microbends."),
         (167.0, 168.5, "Okay, the second problem. Dispersion."),
         (169.0, 177.5, "Chromatic dispersion. The refractive index depends on the wavelength, so the different wavelengths of your pulse travel at different speeds."),
         (178.0, 186.5, "And a real source is not one wavelength. It has a spectral width, delta lambda. So the pulse spreads."),
         (187.0, 195.5, "The broadening is D times L times delta lambda. D is the dispersion parameter, in picoseconds per nanometre and kilometre."),
         (196.0, 201.5, "For standard fibre, the G 652, D is zero around 1310 and around seventeen at 1550."),
         (202.0, 214.5, "So you see the problem. At 1550 we have the minimum loss but a lot of dispersion. At 1310 we have zero dispersion but more loss."),
         (215.0, 223.5, "Let's compute one. Seventeen picoseconds per nanometre kilometre, eighty kilometres, and a source of zero point one nanometres."),
         (224.0, 228.5, "Seventeen times eighty times zero point one. One hundred and thirty-six picoseconds."),
         (229.0, 240.5, "At ten gigabit per second, the bit lasts one hundred picoseconds. So the pulse is spread over more than one bit."),
         (241.0, 253.5, "A rule of thumb that you can use: the broadening should be less than about a quarter of the bit period. Here we are far above.")]
FIBRE_NOTE = [
    "Attenuation\nwhere is the launch power, the length (km) and the attenuation (dB/km). In dB the loss is simply , which is why links "
    "are always computed in dB.\nTypical values: 2.5 dB/km at 850 nm (multimode, short reach), 0.35 dB/km at 1310 nm, 0.2 dB/km at 1550 nm "
    "(the minimum).\nSlide 7: attenuation of silica against wavelength. Rayleigh scattering falls as on the left, the OH peak sits at 1383 nm, "
    "and infrared absorption rises beyond 1600 nm; the dots mark 1310 and 1550 nm.\nRayleigh scattering : tiny density fluctuations frozen "
    "into the glass when the fibre is drawn. It cannot be removed: it is the floor of the curve.\nOH absorption peak at 1383 nm: water in "
    "the glass. Low-water-peak fibre (G.652.D) has almost none.\nInfrared absorption of silica above about 1600 nm: the window closes on "
    "both sides.\nBending losses: macro- and microbends let light escape from the core (seen in lab 1 with the power meter).\nThe bands\n"
    "Band Range (nm) Note O 1260–1360 zero dispersion of standard fibre E 1360–1460 contains the OH peak S 1460–1530 C 1530–1565 "
    "lowest loss, the EDFA band L 1565–1625",
    "Chromatic dispersion\nThe refractive index depends on wavelength, and a real source has a spectral width, so the pulse spreads:\n"
    "where is the dispersion parameter (ps/(nm·km)), the length (km) and the spectral width of the source (nm).\nStandard fibre "
    "(G.652): near 1310 nm and ps/(nm·km) at 1550 nm.\nThe trade-off: 1550 nm has the lowest loss but a lot of dispersion; 1310 nm has "
    "no dispersion but more loss.\nRule of thumb (the ?? in your notes): keep the broadening below about a quarter of the bit period, .",
    "Worked example: pulse broadening at 10 Gb/s\nps/(nm·km), km, source width nm.\nps.\nAt 10 Gb/s the bit period is ps: the pulse "
    "spreads over more than one bit, far above ps.\nSo this link needs dispersion compensation or a narrower source.\nWhy: the broadening "
    "grows linearly with , so at 1550 nm and 10 Gb/s it is dispersion, not loss, that limits the link."]


@unittest.skipUnless(node(), "needs node to run web/source.js")
class Moments(unittest.TestCase):
    """What web/source.js works out without a page: run in node, as the page would."""

    def run_js(self, code, *args):
        script = ("global.window = globalThis; eval(require('fs').readFileSync(process.argv[1], 'utf8'));"
                  "let s = ''; process.stdin.on('data', (d) => { s += d; }).on('end', () => { const a = JSON.parse(s);"
                  "process.stdout.write(JSON.stringify(new Function('M', 'args', a.code)(window.Moments, a.args))); });")
        out = subprocess.run([node(), "-e", script, str(ROOT / "web" / "source.js")], input=json.dumps({"code": code, "args": args}),
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_transcripts_are_read_into_lines(self):
        lines = self.run_js("return M.parse(args[0]);", srt([(1.2, 4.0, "[others] Good morning."), (4.5, 9.0, "[me] Morning.\nSo, the rehearsal."),
                                                            (9.0, 30.0, "[muted 00:00:09–00:00:30]"), (31.5, 33.0, "[others] 42")]))
        self.assertEqual([(round(x["s"], 1), x["who"], x["text"]) for x in lines],
                         [(1.2, "others", "Good morning."), (4.5, "me", "Morning. So, the rehearsal."), (9.0, "", "[muted 00:00:09–00:00:30]"),
                          (31.5, "others", "42")], "a line's number is not what was said, a line that is a number is")
        self.assertEqual(lines[2]["muted"], [9, 30])
        vtt = ("WEBVTT\n\n00:01.000 --> 00:04.000\n<v Marta Pérez>We ship on <i>Friday</i> &amp; not before.</v>\n\n"
               "1:02:03.500 --> 1:02:05.000 align:start\n<v Joan>Agreed: <00:01:02.900>latency < 5 ms and jitter > 1 ms.</v>\n")
        self.assertEqual([(x["s"], x["who"], x["text"]) for x in self.run_js("return M.parse(args[0]);", vtt)],
                         [(1, "Marta Pérez", "We ship on Friday & not before."), (3723.5, "Joan", "Agreed: latency < 5 ms and jitter > 1 ms.")])
        teams_vtt = ("WEBVTT\n\n1f0c9a2e-6b1d-4c55-9a1e-2d8f7c3b5e10/12-0\n00:00:02.640 --> 00:00:05.120\n<v Marta Pérez>Good morning.</v>\n\n"
                     "NOTE the next line was cut\n\n1f0c9a2e-6b1d-4c55-9a1e-2d8f7c3b5e10/13-0\n00:00:05.500 --> 00:00:08.000\n"
                     "<v Joan Puig>Let's start with the rollback.</v>\n\nintro\n00:00:09.000 --> 00:00:10.000\n<v Marta Pérez>Fine.</v>\n")
        self.assertEqual([(x["s"], x["who"], x["text"]) for x in self.run_js("return M.parse(args[0]);", teams_vtt)],
                         [(2.64, "Marta Pérez", "Good morning."), (5.5, "Joan Puig", "Let's start with the rollback."), (9, "Marta Pérez", "Fine.")],
                         "a line ends at the blank line: the next line's name (Teams gives each one) and a NOTE are not what was said")
        teams = "Marta Pérez  0:03\nWe ship on Friday.\nAnd not before.\nJoan Puig  1:15\nAgreed, Friday."
        self.assertEqual([(x["s"], x["who"], x["text"]) for x in self.run_js("return M.parse(args[0]);", teams)],
                         [(3, "Marta Pérez", "We ship on Friday. And not before."), (75, "Joan Puig", "Agreed, Friday.")])
        self.assertEqual(self.run_js("return M.parse(args[0]);", "Just what I typed, with no times.\n\nA second paragraph."), [])
        self.assertEqual(self.run_js("return M.parse(args[0]);", "We start at 9:15\nThe intro.\nBreak at 11:30\nLunch."), [],
                         "lines that end in a time are not a Teams transcript unless they name someone")

    def test_each_section_starts_where_its_topic_does(self):
        lines = lecture()
        first = [lines[30 * n][0] for n in range(len(TOPICS))]
        sections = [f"{title}\n- " + ", ".join(vocab.split()[:6]) + "." for title, vocab in TOPICS]
        found = self.run_js("return M.starts(M.model(M.parse(args[0])), args[1]);", srt(lines), sections)
        for title, want, got in zip([t for t, _ in TOPICS], first, found):
            self.assertIsNotNone(got, title)
            self.assertEqual(got, want, f"{title}: {got} for {want}")
        # a section that was never said gets no time, and the others keep theirs
        found = self.run_js("return M.starts(M.model(M.parse(args[0])), args[1]);", srt(lines),
                            sections[:2] + ["Kubernetes\n- pods, deployments, helm charts, ingress controllers."] + sections[2:])
        self.assertIsNone(found[2])
        self.assertEqual(found[3], first[2])
        self.assertEqual(self.run_js("return M.starts(M.model(M.parse(args[0])), args[1]);", "No times here.", sections), [None] * 5)
        # a class that opens with a greeting and a notice: the first section starts where its topic does
        opening = [(1.0, 6.0, "Good morning, let's start, it's already five past."), (7.0, 13.0, "The lab groups are on the board, please check them.")]
        later = [(a + 14, b + 14, text) for a, b, text in lines]
        found = self.run_js("return M.starts(M.model(M.parse(args[0])), args[1]);", srt(opening + later), sections)
        self.assertEqual(found, [s + 14 for s in first], "not at the greeting")

    def test_a_section_starts_where_its_words_take_over(self):
        """From a class of the demo library: the stretch ahead of "The C band is the important one" already holds the
        start of dispersion, and that line shares as much with either section; the start is where dispersion is said."""
        found = self.run_js("return M.starts(M.model(M.parse(args[0])), args[1]);", srt(FIBRE), FIBRE_NOTE)
        self.assertEqual(found, [1.0, 167.0, 215.0], "Attenuation, Chromatic dispersion, the worked example")

    def test_a_time_in_the_note_is_checked_against_what_was_said_then(self):
        lines = lecture()
        cases = [("Pump, metastable level and population inversion. (00:08:10)", 490),      # the erbium topic: said then
                 ("Raman gain is distributed and counterpumped backwards (00:16:41)", 1001),
                 ("Pump, metastable level and population inversion. (00:01:10)", 70),       # the right words, the wrong time
                 ("The partial exam is on Tuesday at 10:00 in room 3.", 600),              # a clock time
                 ("Pump, metastable level and population inversion. (02:10:00)", 7800),     # after the end of the class
                 ("Pump and inversion (00:00)", 0)]
        self.assertEqual(self.run_js("const m = M.model(M.parse(args[0])); return args[1].map(([c, at]) => M.check(m, c, at));", srt(lines), cases),
                         [True, True, False, False, False, False])

    def test_times_and_slides_are_found_in_the_note(self):
        text = ("(00:41:10) and [12:40], at 10:30h, 9:30 am, a ratio of 3:2, 16:9 screens, 12:75, 1:02:03; "
                "slide 14, see p. 3, pp. 12-14, la diapositiva 7, la pàgina 21, página 2, Folie 5, the slides 3 and 4, page14")
        found = self.run_js("return [[...args[0].matchAll(M.STAMP)].map((m) => [m[0], M.toSeconds(m[0])]),"
                            " [...args[0].matchAll(M.SLIDE)].map((m) => [m[1], +m[2]])];", text)
        self.assertEqual(found[0], [["00:41:10", 2470], ["12:40", 760], ["12:75", None], ["1:02:03", 3723]])
        self.assertEqual(found[1], [["slide", 14], ["p.", 3], ["pp.", 12], ["diapositiva", 7], ["pàgina", 21], ["página", 2], ["Folie", 5],
                                    ["slides", 3], ["page", 14]])

    def test_words_are_read_as_materials_reads_them(self):
        texts = ["La dispersión cromática ensancha el pulso: 17 ps/(nm·km) a 1550 nm, y la PMD crece con √L.",
                 "L'atenuació de la fibra és de 0,2 dB/km; l'EDFA amplifica tota la banda C amb un bombeig a 980 nm.",
                 "The ﬁbre's Rayleigh scattering falls as λ⁻⁴: café, naïve, İstanbul, x² + y², WDM_grid and the ITU-T G.694.1 grid."]
        got = self.run_js("return args[0].map((text) => Object.fromEntries(M.words(text)));", texts)
        self.assertEqual(got, [dict(materials._words(text)) for text in texts])

    def test_closing_sections_are_the_ones_index_md_knows(self):
        self.assertEqual(sorted(self.run_js("return [...M.CLOSING];")), sorted({n.lower() for n in index_md._NAMES}),
                         "CLOSING in web/source.js lists the names of index_md._NAMES, no more and no fewer")
        names = sorted(set(index_md._NAMES))
        headings = names + [n.title() for n in names] + ["2. Self-test", "Glossary:", "Action items (from today)", "## Respostes", "Pla de repàs",
                                                         "Preguntes d’autoavaluació"]
        topics = ["Worked example", "Examples of hazards", "Key concepts", "Review of TCP", "Mapa de Karnaugh", "Exam strategy for problem 2",
                  "1. Acciones de la reunión de arranque (7 de octubre)", "Raman amplification"]
        got = self.run_js("return args[0].map(M.closing);", headings + topics)
        self.assertEqual(got, [index_md.closing(h) for h in headings + topics])
        self.assertTrue(all(got[:len(headings)]) and not any(got[len(headings):]))

    def test_a_long_class_is_quick(self):
        lines, at = [], 1.0
        for n in range(900):            # two hours in lines of 8 s, twenty topics
            vocab = TOPICS[n // 45 % len(TOPICS)][1].split()
            lines.append((at, at + 7.5, f"{vocab[n % len(vocab)]} {vocab[(n + 3) % len(vocab)]} word{n // 45} " + " ".join(COMMON[n % 7:n % 7 + 6])))
            at += 8
        sections = [f"Topic {k}\n" + " ".join(TOPICS[k % len(TOPICS)][1].split()[:5]) + f" word{k}" for k in range(20)]
        ms = self.run_js("const t0 = Date.now(); const m = M.model(M.parse(args[0])); M.starts(m, args[1]);"
                         "for (let i = 0; i < 40; i++) M.check(m, 'erbium pump inversion gain', 600 + i * 60); return Date.now() - t0;", srt(lines), sections)
        self.assertLess(ms, 1500, f"{ms} ms for a two-hour class")


if __name__ == "__main__":
    unittest.main()
