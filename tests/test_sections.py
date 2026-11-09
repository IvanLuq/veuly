"""The course digest and the project register (transcapp/digest.py): the closing sections read back from the notes, in
English, Spanish and Catalan, in the layouts example minutes give, and what the page gets. Run with the others:
python3 -m unittest discover tests. Stand-ins only: no whisper, no Claude usage, no Keychain."""
import datetime
import json
import os
import pathlib
import re
import sys
import threading
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_core import ROOT, Client, FakeEngine  # noqa: E402  (sets TRANSCAPP_HOME and VEULY_SECRETS)
from transcapp import digest, index_md, library, paths, prefs, server  # noqa: E402

# A study note as the default quiz has Claude write it, closing sections last and in the asked order.
NOTE_EN = """# Optical amplifiers: EDFA, Raman and SOA

Optical amplifiers made WDM possible: one amplifier serves every wavelength at once.

## Gain, loss and power in decibels

dB and dBm get mixed up in the exam every year.

$$
G_{\\mathrm{dB}} = 10\\log_{10}\\frac{P_{\\mathrm{out}}}{P_{\\mathrm{in}}}
$$

where $P_{\\mathrm{in}}$ and $P_{\\mathrm{out}}$ are the powers at the input and the output. A gain is a ratio.

## EDFA: erbium-doped fibre amplifier

### How it amplifies

**Why there is no crosstalk between channels (asked in past exams):** the 10 ms lifetime is far longer than a bit.

- The pump power at 1480 nm was given as 100 mW (unclear in the recording).
- You marked ?? at the factor 2: the teacher only said it comes from the signal-spontaneous beat.

> [!warning] Check: your notes say 3 dB / the teacher said 3.5 dB (00:13:42)

## Noise: ASE and noise figure

- ASE power in an optical bandwidth $B_o$, both polarisations:

$$P_{\\mathrm{ASE}} = 2\\,n_{sp}\\,h\\nu\\,(G-1)\\,B_o$$

where $n_{sp}$ is the inversion factor and $B_o$ the optical bandwidth.

## Exam and admin

- **Partial exam: Tuesday 3 November, 10:00**, topics 1–4, with one A4 sheet of your own formulas.
- Lab 2 (OTDR) on Thursday 29 October: check your group.
- The 2025 partial is on Aula Global.

## Glossary

- **ASE**: amplified spontaneous emission, the noise of an optical amplifier.
- **EDFA**: erbium-doped fibre amplifier.
- **Noise figure (NF)**: SNR at the input over SNR at the output.

## Self-test

1. Why does an EDFA cause no crosstalk (asked in past exams)?
2. What is the lowest noise figure, and when is it reached?

### Answers

1. Its 10 ms lifetime averages the bits ?? (00:06:47)
2. 3 dB, at full inversion. $$NF_{min} = 3\\ \\mathrm{dB}$$

## Concept map

- Fibre loss -- is paid back by --> the optical amplifier

## Review plan

- **+1 day (7 Oct):** redo the worked examples.
- **+30 days (5 Nov):** after the partial, the OSNR chain again.
"""

NOTE_ES = """# Integrales dobles y cambio a polares

Una integral doble suma los valores de una función sobre una región del plano.

## Teorema de Fubini

Si $f$ es continua en el rectángulo $R$, el orden de integración no cambia el resultado:

$$
\\iint_R f\\,dA = \\int_a^b\\int_c^d f\\,dy\\,dx
$$

## Coordenadas polares

El valor de la constante se dijo deprisa (poco claro en la grabación).

## Examen y avisos

- El **parcial es el 12 de noviembre**; entra hasta el tema 3.
- Entrega de la hoja 3 el viernes 6 de noviembre.

## Glosario

- **Jacobiano**: el determinante de las derivadas del cambio de variables.
- **EDFA** — amplificador de fibra dopada con erbio.

## Autoevaluación

1. ¿Qué dice el teorema de Fubini?

### Respuestas

1. Que el orden no cambia el resultado. (00:00:37)

## Mapa conceptual

- Fubini -- permite --> integrar por partes

## Plan de repaso

- **+30 días (18 nov):** después del parcial del 12 de noviembre, el jacobiano otra vez.
"""

NOTE_CA = """# Control de congestió a TCP

TCP ajusta la finestra de congestió segons les pèrdues.

## Slow start

La finestra es dobla a cada RTT (preguntat en exàmens anteriors).

## El throughput d'una connexió llarga

Per a una probabilitat de pèrdua $p$ petita (fórmula de Mathis):

$$
\\text{throughput} \\approx \\frac{1{,}22 \\cdot \\mathrm{MSS}}{\\mathrm{RTT}\\,\\sqrt{p}}
$$

on MSS és la mida màxima del segment i RTT el temps d'anada i tornada.

El valor de l'exemple no s'entén bé (poc clar a la gravació).

## Examen i avisos

- A l'examen sempre hi ha un exercici de dibuixar l'evolució de cwnd.
- Lliurament de la pràctica 2 el 30 d'octubre.

## Glossari

- **cwnd**: la finestra de congestió.
- **edfa**: l'amplificador de fibra dopada amb erbi.

## Autoavaluació

1. Què fa TCP quan arriben tres ACK duplicats?

### Respostes

1. Redueix la finestra a la meitat.
"""

MINUTES_EN = """# Kick-off with Nokia, 7 October 2026

Kick-off of phase 2: the scope, the lab plan and the calendar were agreed.

## 1. Scope of phase 2
- 6 P routers and 14 PE routers in five sites.

## 2. Decisions under uncertainty
- A topic about how to decide: not a closing section.

## Decisions
- **D1.** Phase 2 uses SR-MPLS with TI-LFA.
- **D2.** BCN-01 goes first, on 4–5 November.

## Action items
| Task | Owner | Due date |
|---|---|---|
| Request the CAB slot | Marta Soler | 9 Oct |

## Open questions
- Who signs the customer notification: the NOC or the account teams?

## Risks and blockers
- The static labels on BCN-P2 could clash with the SRGB.
"""

MINUTES_ES = """# Seguimiento semanal, 14 de octubre de 2026

Se revisaron las acciones de la reunión de arranque.

## 1. Resultados del laboratorio
- Los fallos simples se recuperan en menos de 50 ms.

## Acuerdos
- **D1.** Se mantiene la fecha del 4 al 5 de noviembre.

## Acciones
| Tarea | Responsable | Fecha |
|---|---|---|
| Investigar el fallo doble | Jordi Puig | 20 oct |

## Preguntas abiertas
- ¿Quién está de guardia en el NOC la noche del corte?

## Riesgos y bloqueos
| Riesgo | Quién lo sigue |
|---|---|
| El fallo doble no cumple el objetivo | Jordi |
"""

# Laid out like example minutes that lead with the outcomes, and with bold labels instead of headings for the rest.
MINUTES_OUTCOMES = """# Go/no-go BCN-01

Go for the cutover on 4–5 November.

## Decisions
1. Go ahead with BCN-01 on 4–5 November.

## Action items
- Send the go/no-go paper — Marta — 30 Oct

## 1. Rehearsal results
- Every case is now under 50 ms.

**Preguntes obertes:**

- Cal una signatura formal per als 44 ms?

**Riscos i bloquejos**

- La llista de guàrdies del NOC no està tancada.
"""


def add_class(lib, slug, cid, title, note, status="done"):
    """A class folder laid out as the app writes it, with its final note."""
    d = lib.root / slug / cid
    (d / "img").mkdir(parents=True, exist_ok=True)
    meta = {"id": cid, "title": title, "created": f"{cid[:10]}T{cid[11:13]}:{cid[13:15]}:{cid[15:17]}", "status": status,
            "step": "", "error": "", "error_kind": "", "duration_s": 3600, "muted": [], "audio_seq": 0, "materials": None}
    paths.write_atomic(d / "class.json", json.dumps(meta, indent=2), keep=True)
    (d / library.TEXT_FILES["note"]).write_text(note, encoding="utf-8")
    old = d.stat().st_mtime - 60       # settled: written a minute ago, like a note that was not just saved
    os.utime(d / library.TEXT_FILES["note"], (old, old))
    return cid


class Sections(unittest.TestCase):
    """sections(): the closing sections of one note, by position and by name, in three languages and in other layouts."""

    def test_a_study_note_in_english_spanish_and_catalan(self):
        for note, extras, want in (
                (NOTE_EN, None, {"exam_list": "Exam and admin", "glossary": "Glossary", "selftest": "Self-test",
                                 "concept_map": "Concept map", "review_plan": "Review plan"}),
                (NOTE_ES, None, {"exam_list": "Examen y avisos", "glossary": "Glosario", "selftest": "Autoevaluación",
                                 "concept_map": "Mapa conceptual", "review_plan": "Plan de repaso"}),
                (NOTE_CA, ["exam_list", "glossary", "selftest"], {"exam_list": "Examen i avisos", "glossary": "Glossari",
                                                                  "selftest": "Autoavaluació"})):
            found = digest.sections(note, "course", extras)
            self.assertEqual({k: v["heading"] for k, v in found.items()}, want)
        found = digest.sections(NOTE_ES, "course", prefs.DEFAULT_EXTRAS)
        self.assertIn("### Respuestas", found["selftest"]["text"], "the answers belong to the self-test")
        self.assertTrue(found["glossary"]["text"].startswith("- **Jacobiano**"))
        self.assertNotIn("## Autoevaluación", found["glossary"]["text"])
        self.assertEqual(NOTE_ES.split("\n")[found["exam_list"]["line"]], "## Examen y avisos")

    def test_topics_that_look_like_closing_sections_stay_topics(self):
        note = "\n\n".join(["# Logic", "Gates combine bits."] + [f"## {h}\n\n- text" for h in (
            "Mapa de Karnaugh", "Review of Boolean algebra", "Key concepts", "Examples of exam-style problems", "Glossary", "Self-test")])
        found = digest.sections(note, "course", ["exam_list", "glossary", "selftest", "review_plan"])
        self.assertEqual(sorted(found), ["glossary", "selftest"],
                         "a review plan the note left out is not the topic before it, nor is the exam section")

    def test_a_name_said_differently_counts_only_where_the_section_belongs(self):
        note = ("# T\n\nIdea.\n\n## Exam, dates and deliveries in the lab\n\n- a topic\n\n## Worked example\n\nText.\n\n"
                "## Exam, dates and deliveries\n\n- Partial on 3 November.\n\n## Glossary\n\n- **A**: b.\n")
        found = digest.sections(note, "course", ["exam_list", "glossary"])
        self.assertEqual(found["exam_list"]["text"], "- Partial on 3 November.")

    def test_a_note_in_another_language_goes_by_position_and_shape(self):
        note = ("# Les amplificateurs optiques\n\nUn amplificateur optique est un dispositif qui amplifie le signal dans la fibre "
                "sans le convertir.\n\n## Le gain\n\nIl est utilisé dans les réseaux WDM pour compenser les pertes, et le gain "
                "dépend de la puissance de la pompe et de la longueur de la fibre dopée.\n\n"
                "## Examen et administration\n\n- Partiel le 3 novembre.\n\n"
                "## Glossaire\n\n- **ASE** : émission spontanée amplifiée.\n\n## Auto-évaluation\n\n1. Pourquoi ?\n\n"
                "## Carte conceptuelle\n\n- Gain -- dépend de --> pompe\n\n## Plan de révision\n\n- **+1 jour :** les exemples.\n")
        found = digest.sections(note, "course", None)
        self.assertEqual({k: v["heading"] for k, v in found.items()},
                         {"exam_list": "Examen et administration", "glossary": "Glossaire", "selftest": "Auto-évaluation",
                          "concept_map": "Carte conceptuelle", "review_plan": "Plan de révision"})
        self.assertFalse(digest._ours(note + " La fibre est le support et le gain dépend de la pompe, avec une marge."))
        for ours in (NOTE_EN, NOTE_ES, NOTE_CA, MINUTES_ES, MINUTES_OUTCOMES,
                     "Des de la capa física, pas a pas: qui envia què i quan, amb els paquets de dades."):
            self.assertTrue(digest._ours(ours), ours[:40])
        self.assertEqual(digest.sections("# Titre\n\n## Le gain\n\nTexte sans liste.\n", "course", ["glossary"]), {},
                         "a section that does not look like a glossary is not taken for one")

    def test_a_note_written_before_the_preferences_changed(self):
        found = digest.sections(NOTE_EN, "course", ["selftest"])       # only the self-test is asked now
        self.assertEqual(sorted(found), ["concept_map", "exam_list", "glossary", "review_plan", "selftest"])

    def test_minutes_in_english_and_spanish_and_with_example_minutes_layouts(self):
        found = digest.sections(MINUTES_EN, "project", prefs.WORK_DEFAULT_EXTRAS)
        self.assertEqual({k: v["heading"] for k, v in found.items()},
                         {"decisions": "Decisions", "actions": "Action items", "open": "Open questions", "risks": "Risks and blockers"})
        found = digest.sections(MINUTES_ES, "project", ["decisions", "actions", "open", "risks"])
        self.assertEqual({k: v["heading"] for k, v in found.items()},
                         {"decisions": "Acuerdos", "actions": "Acciones", "open": "Preguntas abiertas", "risks": "Riesgos y bloqueos"})
        found = digest.sections(MINUTES_OUTCOMES, "project", ["decisions", "actions", "open", "risks"])
        self.assertEqual({k: v["heading"] for k, v in found.items()},
                         {"decisions": "Decisions", "actions": "Action items", "open": "Preguntes obertes", "risks": "Riscos i bloquejos"})
        self.assertEqual(found["open"]["text"], "- Cal una signatura formal per als 44 ms?")
        self.assertEqual(found["open"]["level"], 7, "a line of bold text stands as a heading")
        self.assertNotIn("decisions", digest.sections(NOTE_EN, "course"), "a course has no minutes' sections")

    def test_code_and_formulas_are_not_headings(self):
        note = "# T\n\n## Topic\n\n```\n## Glossary\n- **x**: y\n```\n\n$$\n## x\n$$\n\nText.\n"
        self.assertEqual(digest.sections(note, "course", ["glossary"]), {})

    def test_a_line_of_bold_text_is_a_heading_only_when_it_names_a_section(self):
        note = ("# T\n\nIdea.\n\n## Topic\n\nText.\n\n## Exam and admin\n\n**The exam is open book.**\n\n- Partial on 3 November.\n\n"
                "**Glossary:**\n\n- **A**: b.\n")
        found = digest.sections(note, "course", ["exam_list", "glossary"])
        self.assertEqual(found["exam_list"]["text"], "**The exam is open book.**\n\n- Partial on 3 November.")
        self.assertEqual((found["glossary"]["heading"], found["glossary"]["level"]), ("Glossary", 7))
        got = digest._gather(note, "course", ["exam_list", "glossary"], datetime.date(2026, 10, 6))
        self.assertEqual([i["text"] for i in got["exam"]], ["**The exam is open book.**", "Partial on 3 November."])

    def test_a_section_spread_over_the_topics(self):
        note = ("# Weekly\n\nOutcome.\n\n## 1. Lab\n\nTalk.\n\n### Decisions\n\n- Keep the lab date.\n\n## 2. Budget\n\nTalk.\n\n"
                "### Decisions\n\n- Ask for more budget.\n\n## Action items\n\n- Send the paper — Marta — 30 Oct\n")
        found = digest.sections(note, "project", ["decisions", "actions"])
        self.assertEqual([p["text"] for p in found["decisions"]["parts"]], ["- Keep the lab date.", "- Ask for more budget."])
        self.assertEqual(found["decisions"]["text"], "- Keep the lab date.", "the first part, as a section on its own is")
        self.assertNotIn("parts", found["actions"])
        got = digest._gather(note, "project", ["decisions", "actions"], datetime.date(2026, 10, 14))
        self.assertEqual([(i["section"], i["text"]) for i in got["decisions"]],
                         [("Decisions", "Keep the lab date."), ("2. Budget", "Ask for more budget.")],
                         "every part counts; the second opens at its topic, as the first \"Decisions\" is the one the page finds")
        closing = note + "\n## Decisions\n\n- Both, said again at the end.\n"
        got = digest._gather(closing, "project", ["decisions", "actions"], datetime.date(2026, 10, 14))
        self.assertEqual([i["text"] for i in got["decisions"]], ["Both, said again at the end."],
                         "with a closing section at the end, that one is the decisions")


class Marks(unittest.TestCase):
    """What the notes say was asked in past exams, and what is still to check: Claude's marks, not any word near another."""

    def test_asked_in_past_exams(self):
        found = lambda text: bool(digest.ASKED.search(digest._fold1(text)))      # noqa: E731
        for text in ("Why no crosstalk (asked in past exams): the lifetime", "(asked in past exams: 2025, problem 2)",
                     "El cambio de orden (preguntado en exámenes anteriores).", "(ha salido en exámenes de otros años)",
                     "La finestra (preguntat en exàmens anteriors)", "(ha sortit en exàmens d'altres anys)", "(salió en parciales anteriores)",
                     "This came up in past exams.", "Ha salido en exámenes anteriores.", "## OSNR (asked in past exams)"):
            self.assertTrue(found(text), text)
        for text in ("In the example from the previous class, the filter was ideal.", "Someone asked whether the exam includes the lab.",
                     "The exam covers the previous chapters and this one.", "Examples of previous years' problems",
                     "(see the example from past years)", "Before the exam, redo the previous problem sets."):
            self.assertFalse(found(text), text)
        note = ("# T\n\nIdea.\n\n## Worked example\n\nIn the example from the previous class, the filter was ideal.\n\n"
                "Someone asked whether the exam includes the lab.\n\n## Sampling\n\nThe theorem (asked in past exams).\n")
        got = digest._gather(note, "course", [], datetime.date(2026, 10, 6))
        self.assertEqual([i["text"] for i in got["asked"]], ["The theorem (asked in past exams)."])
        note = "# T\n\nIdea.\n\n## A chain\n\n**In every partial (asked in past exams).** It reads:\n\n$$\nOSNR = 58 + P\n$$\n\nwhere $P$ is…\n"
        got = digest._gather(note, "course", [], datetime.date(2026, 10, 6))
        self.assertEqual([i["text"] for i in got["asked"]], ["**In every partial (asked in past exams).** It reads:\n\n$$OSNR = 58 + P$$"],
                         "a line that leads to a formula comes with it")
        note = note.replace("$$\nOSNR = 58 + P\n$$\n\nwhere $P$ is…\n", "- twice the spans: −3 dB;\n- 3 dB more power: +3 dB.\n\nThen text.\n")
        got = digest._gather(note, "course", [], datetime.date(2026, 10, 6))
        self.assertEqual(got["asked"][0]["text"].split("\n\n")[1], "- twice the spans: −3 dB;\n- 3 dB more power: +3 dB.",
                         "and so does the list it leads to")

    def test_unclear_is_the_mark_of_the_recording(self):
        found = lambda text: bool(digest.UNCLEAR.search(digest._fold1(text)))    # noqa: E731
        for text in ("(unclear in the recording)", "(poco claro en la grabación)", "(no s'entén a la gravació)", "(inaudible)",
                     "(confuso en la grabación)", "(dubtós a la gravació)"):
            self.assertTrue(found(text), text)
        for text in ("mixing up dB and dBm (a common confusion in the exam)", "(a doubtful claim)", "(confusing at first)", "(dudoso)"):
            self.assertFalse(found(text), text)

    def test_what_is_unclear_in_the_exam_section_is_still_to_check(self):
        note = ("# T\n\nIdea.\n\n## Topic\n\nText.\n\n## Exam and admin\n\n- Partial on 3 November, in room 52.S01 (unclear in the "
                "recording).\n\n## Glossary\n\n- **SNR**: signal over noise ??\n")
        got = digest._gather(note, "course", ["exam_list", "glossary"], datetime.date(2026, 10, 6))
        self.assertEqual(len(got["exam"]), 1, "the line is in the radar")
        self.assertEqual([(i["why"], i["section"]) for i in got["check"]], [("unclear", "Exam and admin"), ("marked", "Glossary")])


class Places(unittest.TestCase):
    """Where an item opens its class: a heading that showSection (web/app.js) finds as itself."""

    def test_repeated_headings_and_headings_that_start_with_a_formula(self):
        note = ("# Waves\n\nIdea.\n\n## 1. Plane waves\n\n### Example\n\n$$E = E_0 e^{-jkz}$$\n\n## 2. Reflection\n\n### Example\n\n"
                "$$\\Gamma = \\frac{Z_L - Z_0}{Z_L + Z_0}$$\n\n### $\\Gamma$ in dB\n\n$$RL = -20\\log_{10}|\\Gamma|$$\n\n"
                "## The snake_case `names`\n\n$$x_1 = 1$$\n")
        self.assertEqual([f["section"] for f in digest.formulas(note)], ["Example", "2. Reflection", "2. Reflection", "The snake_case names"])

    def test_a_heading_an_earlier_one_starts_with_keeps_its_place(self):
        note = ("# Weekly\n\nOutcome.\n\n## Decisions on the budget\n\n- A topic.\n\n## Open questions from 7 October\n\n- Answered.\n\n"
                "## Decisions\n\n- Keep the date.\n\n## Open questions\n\n- Who is on call?\n")
        got = digest._gather(note, "project", ["decisions", "open"], datetime.date(2026, 10, 14))
        self.assertEqual([(i["section"], i["text"]) for i in got["decisions"] + got["open"]],
                         [("Decisions", "Keep the date."), ("Open questions", "Who is on call?")],
                         "showSection takes the heading with exactly that text before one that starts with it")
        page = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("headings.find((el) => fold(el.textContent) === fold(section)) || headings.find(", page)

    def test_the_names_are_the_ones_index_md_leaves_out_of_the_topics(self):
        for key, names in digest.NAMES.items():
            for name in names:
                self.assertTrue(index_md.closing(name), f"index_md would list {name!r} as a topic")
                self.assertEqual(digest._name(name.title(), "project" if key in prefs.WORK_EXTRAS else "course"), key, name)
        for name in index_md._NAMES:
            self.assertTrue(digest._name(name, "course") or digest._name(name, "project"), f"digest does not know {name!r}")
        self.assertEqual(set(digest.NAMES) - {"answers"}, set(prefs.EXTRAS) | set(prefs.WORK_EXTRAS))


class Formulas(unittest.TestCase):
    def test_every_formula_on_its_own_lines_with_its_heading_lead_and_symbols(self):
        found = digest.formulas(NOTE_EN)
        self.assertEqual([f["section"] for f in found], ["Gain, loss and power in decibels", "Noise: ASE and noise figure", "Answers"])
        first, second = found[0], found[1]
        self.assertEqual(first["tex"], "G_{\\mathrm{dB}} = 10\\log_{10}\\frac{P_{\\mathrm{out}}}{P_{\\mathrm{in}}}")
        self.assertEqual(first["where"], "where $P_{\\mathrm{in}}$ and $P_{\\mathrm{out}}$ are the powers at the input and the output.")
        self.assertEqual((second["lead"], second["topic"]), ("ASE power in an optical bandwidth $B_o$, both polarisations", "Noise: ASE and noise figure"))
        self.assertEqual(found[2]["tex"], "NF_{min} = 3\\ \\mathrm{dB}", "a $$…$$ inside a line counts too")
        es = digest.formulas(NOTE_ES)[0]
        self.assertEqual((es["section"], es["lead"]), ("Teorema de Fubini", "Si $f$ es continua en el rectángulo $R$, el orden de integración no cambia el resultado"))
        ca = digest.formulas(NOTE_CA)[0]
        self.assertTrue(ca["where"].startswith("on MSS és la mida"), ca)
        self.assertEqual(digest.formulas("Inline $x^2$ only.\n\n```\n$$a$$\n```\n"), [], "inline formulas and code are not on the sheet")
        self.assertEqual(digest.formulas("## D\n\n$$\\Delta\\tau = D L \\Delta\\lambda$$\n\nOn a long link the spread adds up.\n")[0]["where"], "",
                         "a line that starts with \"On\" says what the symbols are only in Catalan (\"on $D$ és…\")")

    def test_the_heading_kept_for_opening_the_note_there(self):
        self.assertEqual(digest._anchor("**Dispersion** of $D(\\lambda)$ in ==fibre=="), "Dispersion of")
        self.assertEqual(digest._anchor("1. Scope of phase 2"), "1. Scope of phase 2")
        self.assertEqual(digest._anchor("$E = mc^2$"), "")


class Dates(unittest.TestCase):
    def test_dates_in_three_languages(self):
        base = datetime.date(2026, 10, 6)
        said = lambda text: [d.isoformat() for d, _, _ in digest.dates_in(text, base)]    # noqa: E731
        self.assertEqual(said("Partial exam: Tuesday 3 November, 10:00, topics 1–4"), ["2026-11-03"])
        self.assertEqual(said("El parcial es el 12 de noviembre; la entrega, el viernes 6 de nov."), ["2026-11-12", "2026-11-06"])
        self.assertEqual(said("Lliurament el 30 d'octubre i l'examen el 15 de gener"), ["2026-10-30", "2027-01-15"])
        self.assertEqual(said("BCN-01 on the night of 4–5 November; del 4 al 5 de noviembre"), ["2026-11-04", "2026-11-04"])
        self.assertEqual(said("Final on Nov 20, 2026 and again 2027-01-15 or 3/02/2027"), ["2026-11-20", "2027-01-15", "2027-02-03"])
        self.assertEqual(said("1 set of exercises, it may 3 times, 1/2 of the grade, 1550 nm, 10:00, problem set 2"), [],
                         "words that only look like dates")
        self.assertEqual(said("3 May and 3 de set."), ["2027-05-03", "2026-09-03"])

    def test_a_date_at_the_end_of_a_sentence(self):
        """The full stop after "30 October" ends the sentence: the delivery is not the exam of the next one (it was, and
        the home tile could give a delivery as the next exam). "3 nov. at 10" keeps its dot."""
        base = datetime.date(2026, 10, 19)
        dated = lambda line: [(d.isoformat(), exam) for d, exam, _ in digest._exam_dates(line, base, True)]   # noqa: E731
        self.assertEqual(dated("Deliver the lab report on 30 October. The exam is on 3 November."),
                         [("2026-10-30", False), ("2026-11-03", True)])
        self.assertEqual(dated("Entrega del informe el 30 de octubre. El examen es el 3 de noviembre."),
                         [("2026-10-30", False), ("2026-11-03", True)])
        self.assertEqual(dated("Lliurament el 30 d'oct. L'examen és el 3 de novembre."), [("2026-10-30", False), ("2026-11-03", True)])
        self.assertEqual(dated("Lab on 29 October; exam on 3 November. Deliver on 6 November; the resit is on 2 February."),
                         [("2026-10-29", False), ("2026-11-03", True), ("2026-11-06", False), ("2027-02-02", True)])
        self.assertEqual(dated("Exam on 3 nov. at 10, room 52."), [("2026-11-03", True)])
        self.assertEqual([(d.isoformat(), said) for d, _, said in digest._exam_dates(
            "Review on 5 Nov, after the midterm on 3 November. Then the summer.", base, False)],
            [("2026-11-03", "after the midterm on 3 November")])

    def test_which_dates_are_an_exam(self):
        base = datetime.date(2026, 10, 19)
        inside = digest._exam_dates("Lab 2 on Thursday 29 October; partial exam on 3 November.", base, True)
        self.assertEqual([(d.isoformat(), exam) for d, exam, _ in inside], [("2026-10-29", False), ("2026-11-03", True)])
        outside = digest._exam_dates("**+30 días (18 nov):** después del parcial del 12 de noviembre, el jacobiano otra vez.", base, False)
        self.assertEqual([(d.isoformat(), said) for d, _, said in outside], [("2026-11-12", "después del parcial del 12 de noviembre")])
        self.assertEqual(digest._exam_dates("**+30 days (5 Nov):** after the partial, the OSNR chain again.", base, False), [],
                         "a review session's date next to the exam it follows is not the exam's")


class Speed(unittest.TestCase):
    def test_a_long_line_takes_no_longer_than_its_length(self):
        """A transcript pasted into a note, or a damaged file, makes lines of tens of KB: none may take seconds (each of
        these took 1 to 10 s before)."""
        base = datetime.date(2026, 10, 1)
        for note in ("# T\n\n## A\n\n(" + "confusing " * 5000 + ")",
                     "# T\n\n## Exam and admin\n\n" + "3 de noviembre " * 2000,
                     "# T\n\n## A\n\n" + "exam 3 de noviembre " * 1500):
            start = time.perf_counter()
            got = digest._gather(note, "course", None, base)
            self.assertLess(time.perf_counter() - start, 0.5, note[:40])
            self.assertEqual(got["check"], [])
        self.assertEqual(len(got["dates"]), 1500)
        self.assertTrue(all(d["exam"] and d["date"] == "2026-11-03" for d in got["dates"]))


class Glossary(unittest.TestCase):
    def test_terms_and_definitions(self):
        for line, want in (("**ASE**: amplified spontaneous emission.", ("ASE", "amplified spontaneous emission.")),
                           ("**Noise figure (NF)**: SNR in over SNR out.", ("Noise figure (NF)", "SNR in over SNR out.")),
                           ("**Throughput** (tasa) — lo que llega.", ("Throughput (tasa)", "lo que llega.")),
                           ("**Metastable level.** The long-lived upper level.", ("Metastable level", "The long-lived upper level.")),
                           ("*cwnd*: la finestra", ("cwnd", "la finestra")), ("Jacobiano: el determinante", ("Jacobiano", "el determinante")),
                           ("ASE — amplified spontaneous emission", ("ASE", "amplified spontaneous emission")), ("Just a term", ("Just a term", ""))):
            self.assertEqual(digest._term(line), want, line)
        self.assertEqual(digest._term_key("Noise figure (NF)"), digest._term_key("noise figure"))
        self.assertNotEqual(digest._term_key("3R / 1R"), digest._term_key("R / 1R"))

    def test_a_term_a_line_without_list_marks(self):
        note = ("# T\n\nIdea.\n\n## Topic\n\nText.\n\n## Glossary\n\n**ASE**: amplified spontaneous emission.\n**EDFA**: erbium-doped "
                "fibre amplifier.\nGFF: gain-flattening filter.\n\nA definition written over\ntwo lines stays one.\n")
        got = digest._gather(note, "course", ["glossary"], datetime.date(2026, 10, 6))["glossary"]
        self.assertEqual([(g["term"], g["text"]) for g in got],
                         [("ASE", "amplified spontaneous emission."), ("EDFA", "erbium-doped fibre amplifier."),
                          ("GFF", "gain-flattening filter."), ("A definition written over two lines stays one", "")])


class Route(unittest.TestCase):
    """GET /api/courses/<slug>/digest, through the server: what the page gets, in order, and nothing written or sent."""

    @classmethod
    def setUpClass(cls):
        cls.fake = FakeEngine()
        cls.httpd, cls.app = server.serve(ROOT / "web", 0, engine_factory=lambda settings: cls.fake)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.c = Client(cls.app)
        cls.c.call("GET", f"/?t={cls.app.token}")
        lib = cls.app.lib
        cls.school = cls.c.call("POST", "/api/courses", {"name": "Digest optical networks", "lang": "en"})[1]["slug"]
        add_class(lib, cls.school, "2026-09-29_100105", "Fibre attenuation", NOTE_CA)
        add_class(lib, cls.school, "2026-10-06_100422", "Optical amplifiers", NOTE_EN)
        add_class(lib, cls.school, "2026-10-19_083012", "Integrales dobles", NOTE_ES)
        add_class(lib, cls.school, "2026-10-27_100150", "ROADMs", "", status="new")       # no note yet
        add_class(lib, cls.school, "2026-10-20_100317", "Transmitters", NOTE_EN.replace("EDFA", "XYZ"), status="error")
        (lib.root / cls.school / "2026-10-20_100317" / library.TEXT_FILES["note"]).write_text("", encoding="utf-8")
        older = lib.root / cls.school / "2026-10-06_100422" / "versions"
        older.mkdir()
        (older / "final-note.20261006-184005.md").write_text("## Glossary\n\n- **OLDTERM**: from an earlier version.\n")
        cls.work = cls.c.call("POST", "/api/courses", {"name": "Digest migration", "lang": "auto", "kind": "project"})[1]["slug"]
        add_class(lib, cls.work, "2026-10-07_100005", "Kick-off with Nokia", MINUTES_EN)
        add_class(lib, cls.work, "2026-10-14_163010", "Seguimiento semanal", MINUTES_ES)
        add_class(lib, cls.work, "2026-10-30_171502", "Go/no-go BCN-01", MINUTES_OUTCOMES)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def get(self, slug):
        status, data = self.c.call("GET", f"/api/courses/{slug}/digest")
        self.assertEqual(status, 200, data)
        return data

    def test_a_school_course(self):
        d = self.get(self.school)
        self.assertEqual(d["kind"], "course")
        self.assertEqual([c["cid"] for c in d["classes"]], ["2026-10-19_083012", "2026-10-06_100422", "2026-09-29_100105"],
                         "the classes with a final note, newest first")
        self.assertEqual(d["classes"][1], {"cid": "2026-10-06_100422", "date": "2026-10-06", "title": "Optical amplifiers"})
        self.assertEqual([(i["cid"][:10], i["text"][:24]) for i in d["exam"]],
                         [("2026-10-19", "El **parcial es el 12 de"), ("2026-10-19", "Entrega de la hoja 3 el "),
                          ("2026-10-06", "**Partial exam: Tuesday "), ("2026-10-06", "Lab 2 (OTDR) on Thursday"),
                          ("2026-10-06", "The 2025 partial is on A"), ("2026-09-29", "A l'examen sempre hi ha "),
                          ("2026-09-29", "Lliurament de la pràctic")])
        self.assertEqual({i["section"] for i in d["exam"]}, {"Examen y avisos", "Exam and admin", "Examen i avisos"})
        self.assertEqual([(x["date"], x["exam"], x["cid"][:10], x["also"]) for x in d["dates"]],
                         [("2026-10-29", False, "2026-10-06", []), ("2026-10-30", False, "2026-09-29", []),
                          ("2026-11-03", True, "2026-10-06", []), ("2026-11-06", False, "2026-10-19", []),
                          ("2026-11-12", True, "2026-10-19", [])],
                         "soonest first; the review plan's \"after the partial of 12 November\" is the same exam, said in the same class")
        self.assertEqual([(i["section"], i["cid"][:10]) for i in d["asked"]], [("How it amplifies", "2026-10-06"), ("Slow start", "2026-09-29")],
                         "the marks in the note's sections; not the self-test's own questions")
        self.assertEqual([(i["why"], i["section"]) for i in d["check"]],
                         [("unclear", "Coordenadas polares"), ("unclear", "How it amplifies"), ("marked", "How it amplifies"),
                          ("disagree", "How it amplifies"), ("unclear", "El throughput d'una connexió llarga")])
        self.assertEqual(d["check"][3]["text"], "Check: your notes say 3 dB / the teacher said 3.5 dB (00:13:42)")
        self.assertEqual([f["cid"][:10] for f in d["formulas"]], ["2026-09-29", "2026-10-06", "2026-10-06", "2026-10-19"],
                         "in the order of the course, without the self-test's answers")
        glossary = {g["term"]: g for g in d["glossary"]}
        self.assertEqual(list(glossary), ["ASE", "cwnd", "edfa", "Jacobiano", "Noise figure (NF)"], "each term once, by name")
        self.assertEqual((glossary["edfa"]["cid"], glossary["edfa"]["also"]), ("2026-09-29_100105", ["2026-10-06_100422", "2026-10-19_083012"]),
                         "the first class to define it keeps it; the others are listed")
        self.assertEqual(glossary["edfa"]["also_at"], {"2026-10-06_100422": "Glossary", "2026-10-19_083012": "Glosario"},
                         "each other class opens at its own glossary: the Spanish note has no \"Glossari\"")
        self.assertNotIn("OLDTERM", json.dumps(d), "earlier versions are not read")
        self.assertNotIn("XYZ", json.dumps(d), "nor a class whose note is empty")

    def test_a_work_project(self):
        d = self.get(self.work)
        self.assertEqual(d["kind"], "project")
        self.assertEqual([c["date"] for c in d["classes"]], ["2026-10-30", "2026-10-14", "2026-10-07"])
        self.assertEqual([(i["cid"][:10], i["section"], i["text"]) for i in d["decisions"]],
                         [("2026-10-30", "Decisions", "Go ahead with BCN-01 on 4–5 November."),
                          ("2026-10-14", "Acuerdos", "**D1.** Se mantiene la fecha del 4 al 5 de noviembre."),
                          ("2026-10-07", "Decisions", "**D1.** Phase 2 uses SR-MPLS with TI-LFA."),
                          ("2026-10-07", "Decisions", "**D2.** BCN-01 goes first, on 4–5 November.")])
        self.assertEqual([(i["cid"][:10], i["section"]) for i in d["open"]],
                         [("2026-10-30", "1. Rehearsal results"), ("2026-10-14", "Preguntas abiertas"), ("2026-10-07", "Open questions")],
                         "a bold label is not a heading the page can open at: the one above it is")
        self.assertEqual([i["text"] for i in d["risks"]], ["La llista de guàrdies del NOC no està tancada.",
                                                           "El fallo doble no cumple el objetivo — Jordi",
                                                           "The static labels on BCN-P2 could clash with the SRGB."])
        self.assertNotIn("exam", d)
        self.assertNotIn("Request the CAB slot", json.dumps(d), "action items are not part of the register")

    def test_deleted_classes_are_left_out_and_nothing_is_written_or_sent(self):
        slug = self.c.call("POST", "/api/courses", {"name": "Digest trash", "lang": "en"})[1]["slug"]
        add_class(self.app.lib, slug, "2026-10-06_100422", "Amplifiers", NOTE_EN)
        cid = add_class(self.app.lib, slug, "2026-10-13_100209", "Gone", NOTE_ES)
        self.assertEqual(len(self.get(slug)["classes"]), 2)
        self.assertEqual(self.c.call("DELETE", f"/api/classes/{slug}/{cid}")[0], 200)
        bin_dir = self.app.lib.root / ".trash"
        self.assertTrue(any(bin_dir.glob(f"*_{slug}_{cid}")), "the class went to the trash")
        tree = lambda: sorted((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in self.app.lib.root.rglob("*") if p.is_file())  # noqa: E731
        before, asked = tree(), len(self.fake.requests)
        d = self.get(slug)
        self.assertEqual([c["cid"] for c in d["classes"]], ["2026-10-06_100422"])
        self.assertNotIn("Jacobiano", json.dumps(d), "nothing comes from the trash")
        self.assertEqual(tree(), before, "the digest writes nothing")
        self.assertEqual(len(self.fake.requests), asked, "and asks Claude nothing")

    def test_an_edited_note_shows_at_once(self):
        slug = self.c.call("POST", "/api/courses", {"name": "Digest edits", "lang": "en"})[1]["slug"]
        cid = add_class(self.app.lib, slug, "2026-10-06_100422", "Amplifiers", NOTE_EN)
        self.assertEqual(len(self.get(slug)["glossary"]), 3)
        base = f"/api/classes/{slug}/{cid}"
        rev = self.c.call("GET", base)[1]["note_rev"]
        note = NOTE_EN.replace("- **EDFA**: erbium-doped fibre amplifier.", "- **EDFA**: erbium-doped fibre amplifier.\n- **GFF**: gain-flattening filter.")
        self.assertEqual(self.c.call("PUT", base, {"note": note, "note_base": rev})[0], 200)
        self.assertEqual([g["term"] for g in self.get(slug)["glossary"]], ["ASE", "EDFA", "GFF", "Noise figure (NF)"])

    def test_refusals(self):
        status, data = self.c.call("GET", "/api/courses/no-such-course/digest")
        self.assertEqual((status, data["kind"]), (404, "not_found"))
        self.assertEqual(Client(self.app).call("GET", f"/api/courses/{self.school}/digest")[0], 403, "the cookie and the token are needed")

    def test_what_a_home_tile_gets(self):
        tile = digest.home(self.app.lib, self.school, today=datetime.date(2026, 10, 31))
        self.assertEqual(tile["next_exam"]["date"], "2026-11-03")
        self.assertEqual([x["date"] for x in tile["dates"]], ["2026-11-03", "2026-11-06", "2026-11-12"], "from today on")
        self.assertEqual(tile["unclear"], 4, "unclear in the recording, and notes that disagree with the teacher; not the ?? marks")
        self.assertEqual(len(tile["unclear_items"]), 3)
        self.assertIsNone(digest.home(self.app.lib, self.school, today=datetime.date(2026, 11, 13))["next_exam"])
        work = digest.home(self.app.lib, self.work)
        self.assertEqual((work["kind"], work["open"], work["open_items"][0]["text"]),
                         ("project", 3, "Cal una signatura formal per als 44 ms?"))


class Page(unittest.TestCase):
    def test_the_page_texts_are_in_every_language(self):
        text = (ROOT / "web" / "i18n.js").read_text(encoding="utf-8")
        page = (ROOT / "web" / "digest.js").read_text(encoding="utf-8")
        self.assertIn("COURSE_PAGES.push({ name: 'digest', label: 'digest_page' })", page)
        for lang in ("en", "es", "ca"):
            block = text[text.index(f"    {lang}: {{"):]
            for key in ("digest_page", "digest_page__work"):
                self.assertIn(f" {key}: '", block[:block.index("\n    },")], f"{lang} lacks {key}")
        # every text the page shows, in every language: the keys it names, and the closing sections' names it builds
        keys = set(re.findall(r"\bt\('([a-z_0-9]+)'", page)) | {f"o_extras_{key}" for key in digest.NAMES if key != "answers"}
        self.assertIn("dg_print", keys)
        for lang in ("en", "es", "ca"):
            block = text[text.index(f"    {lang}: {{"):]
            have = set(re.findall(r"(?:^|[\s{,])([a-z_0-9]+): ['\"`]", block[:block.index("\n    },")]))
            self.assertEqual(sorted(keys - have), [], f"{lang} lacks these texts of the digest")


if __name__ == "__main__":
    unittest.main()
