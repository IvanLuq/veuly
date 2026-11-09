"""The Mac app, the parts Python can check: what packaging/build_mac.py puts in the bundle and its Info.plist, every
text the app and its page use in three languages, the page's half of the bridge (web/native.js) wired into the page,
a build or an install that only replaces a Veuly the script built, app.py stopping when the app's pipe closes or on
SIGTERM (and what it started stopping with it), and how web/native.js keeps this Mac's sound in step with the
microphone and never lets it hold the microphone back (under node, when it is installed).
The app itself, built and running, is tested by tests/e2e_macapp.py.

  python3 -m unittest discover tests
"""
import contextlib
import http.client
import io
import json
import os
import pathlib
import plistlib
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_core import HOME  # noqa: E402,F401  (sets TRANSCAPP_HOME and VEULY_SECRETS)
sys.path.insert(0, str(ROOT / "packaging"))
import build_mac  # noqa: E402
from transcapp import server  # noqa: E402

MAC = sys.platform == "darwin"
LANGS = ("en", "es", "ca")


def i18n_keys() -> dict:
    """The keys of each language block of web/i18n.js (as test_core reads them)."""
    text = (ROOT / "web" / "i18n.js").read_text(encoding="utf-8")
    cut = [text.index(f"    {lang}: {{") for lang in LANGS] + [len(text)]
    return {lang: set(re.findall(r"(?:^|[\s,{])([a-z][a-z_0-9]+): ['\"]", text[cut[i]:cut[i + 1]])) for i, lang in enumerate(LANGS)}


# The Mac app's tests take their ports from VEULY_MACAPP_PORT (default 58280): e2e_macapp.py uses it and the next ones
# (+1 a second copy, +10 and +11 its server), this file +16 to +19. Two runs with bases 20 apart cannot collide.
PORT_BASE = int(os.environ.get("VEULY_MACAPP_PORT") or 58280)


def a_port() -> int:
    """A free port of this test's own (VEULY_MACAPP_PORT + 16 to + 19 first: 58296-58299 by default)."""
    for port in range(PORT_BASE + 16, PORT_BASE + 20):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Texts(unittest.TestCase):
    def test_the_app_and_its_page_say_everything_in_three_languages(self):
        keys = i18n_keys()
        swift = "".join(f.read_text(encoding="utf-8") for f in (ROOT / "packaging" / "mac").glob("*.swift"))
        used = set(re.findall(r'\btexts\("([a-z_0-9]+)"', swift)) | set(re.findall(r'\b(?:item|menu)\("([a-z_0-9]+)"', swift))
        used |= set(re.findall(r'"(mac_[a-z_0-9]+)"', swift)) | set(build_mac.PURPOSES.values())
        native = (ROOT / "web" / "native.js").read_text(encoding="utf-8")
        used |= set(re.findall(r"\bt\(\s*'([a-z_0-9]+)'", native))
        app_text = dict(re.findall(r"([a-z_]+): '([a-z_]+_app|native_[a-z]+)'", native))
        self.assertIn("src_help", app_text)
        used |= set(app_text.values())
        self.assertGreater(len(used), 60)
        for lang in LANGS:
            self.assertEqual(used - keys[lang], set(), f"{lang} is missing texts")
        self.assertIn("mac_menu_quit", used)

    def test_the_permission_texts_are_translated(self):
        texts = build_mac.i18n_texts(build_mac.PURPOSES.values())
        for lang in LANGS:
            self.assertEqual(set(texts[lang]), set(build_mac.PURPOSES.values()), lang)
        self.assertNotEqual(texts["en"]["mac_purpose_mic"], texts["es"]["mac_purpose_mic"])
        self.assertIn("micròfon", texts["ca"]["mac_purpose_mic"])
        self.assertIn("d'aquest ordinador", texts["ca"]["mac_purpose_audio"], "\\' in i18n.js is read as a quote")

    def test_javascript_strings_are_read_as_javascript_reads_them(self):
        self.assertEqual(build_mac.js_string(r"l\'app és \\ fi\n\x41"), "l'app és \\ fi\nA")


class Bundle(unittest.TestCase):
    def test_info_plist(self):
        plist = build_mac.info_plist("1.2.3")
        self.assertEqual(plist["CFBundleIdentifier"], "app.veuly")
        self.assertEqual((plist["CFBundleShortVersionString"], plist["CFBundleExecutable"], plist["CFBundlePackageType"]), ("1.2.3", "Veuly", "APPL"))
        self.assertEqual(plist["LSMinimumSystemVersion"], "14.2", "Core Audio process taps need macOS 14.2")
        self.assertNotIn("LSUIElement", plist, "a Dock icon and a menu bar of its own")
        for key in ("NSMicrophoneUsageDescription", "NSAudioCaptureUsageDescription", "NSDesktopFolderUsageDescription",
                    "NSDownloadsFolderUsageDescription"):
            self.assertGreater(len(plist[key]), 30, key)
        self.assertNotIn("NSAppleEventsUsageDescription", plist, "nothing drives another app any more")
        self.assertTrue(plist["NSAppTransportSecurity"]["NSAllowsLocalNetworking"])
        self.assertEqual(plist["CFBundleLocalizations"], ["en", "es", "ca"])
        self.assertEqual(plist["VeulyBuiltBy"], build_mac.BUILT_BY)
        self.assertEqual(plistlib.loads(plistlib.dumps(plist)), plist)
        self.assertEqual(build_mac.version(), server.VERSION)

    def test_no_apple_events_are_sent(self):
        code = "".join(f.read_text(encoding="utf-8") for f in (ROOT / "transcapp").glob("*.py"))
        self.assertNotIn('"osascript"', code, "an Apple Events purpose string would be needed again")

    def test_layout(self):
        work = pathlib.Path(tempfile.mkdtemp(prefix="veuly-bundle-"))
        binary = work / "Veuly-binary"
        binary.write_bytes(b"\xcf\xfa\xed\xfe")
        texts = build_mac.i18n_texts(build_mac.PURPOSES.values())
        app = work / "Veuly.app"
        build_mac.layout(app, binary, None, build_mac.info_plist("9.9.9", texts=texts), texts)
        contents = app / "Contents"
        self.assertTrue(os.access(contents / "MacOS" / "Veuly", os.X_OK))
        for part in ("app.py", "transcapp/__init__.py", "transcapp/_vendor", "web/index.html", "web/native.js", "web/vendor/editor.js"):
            self.assertTrue((contents / "Resources" / "app" / part).exists(), part)
        self.assertEqual(list(app.rglob("__pycache__")), [], "no compiled files inside the signed bundle")
        self.assertFalse((contents / "Resources" / "app" / "tests").exists())
        self.assertEqual(plistlib.loads((contents / "Info.plist").read_bytes())["CFBundleShortVersionString"], "9.9.9")
        self.assertEqual((contents / "PkgInfo").read_text(), "APPL????")
        es = (contents / "Resources" / "es.lproj" / "InfoPlist.strings").read_text(encoding="utf-8")
        self.assertIn(f'"NSMicrophoneUsageDescription" = "{texts["es"]["mac_purpose_mic"]}";', es)
        if MAC:
            for lang in LANGS:
                lint = subprocess.run(["plutil", "-lint", str(contents / "Resources" / f"{lang}.lproj" / "InfoPlist.strings")], capture_output=True)
                self.assertEqual(lint.returncode, 0, lint.stdout)
        shutil.rmtree(work)

    def test_python_for_other_people_leaves_out_what_a_server_never_imports(self):
        work = pathlib.Path(tempfile.mkdtemp(prefix="veuly-python-"))
        source = work / "cpython"
        lib = source / "lib" / "python3.12"
        for part in ("json", "sqlite3", "test", "tkinter", "idlelib", "ensurepip", "site-packages/pip"):
            (lib / part).mkdir(parents=True)
            (lib / part / "__init__.py").write_text("")
        (source / "bin").mkdir()
        (source / "bin" / "python3.12").write_text("")
        os.symlink("python3.12", source / "bin" / "python3")
        (source / "lib" / "libtcl8.6.dylib").write_text("")
        (lib / "LICENSE.txt").write_text("psf")
        target = work / "out"
        build_mac.copy_python(source, target)
        kept = target / "lib" / "python3.12"
        self.assertTrue((kept / "json" / "__init__.py").exists())
        self.assertTrue((kept / "sqlite3" / "__init__.py").exists())
        self.assertTrue((kept / "LICENSE.txt").exists(), "its licence goes with it")
        self.assertTrue((target / "bin" / "python3").is_symlink(), "links stay links, or the bundle holds it twice")
        for gone in ("test", "tkinter", "idlelib", "ensurepip", "site-packages/pip"):
            self.assertFalse((kept / gone).exists(), gone)
        self.assertFalse((target / "lib" / "libtcl8.6.dylib").exists())
        with self.assertRaises(SystemExit):
            build_mac.copy_python(work / "nothing", work / "out2")
        shutil.rmtree(work)

    def test_the_app_prefers_the_python_it_carries(self):
        code = (ROOT / "packaging" / "mac" / "Support.swift").read_text(encoding="utf-8")
        self.assertLess(code.index('"/python/bin/python3"'), code.index('"/usr/bin/python3"'))

    def test_the_build_refuses_before_it_makes_anything(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(build_mac, "build") as built, mock.patch.object(build_mac, "ROOT", pathlib.Path(tmp)), \
                mock.patch.object(sys, "argv", ["build_mac.py", tmp, "--notarize", "veuly-notary"]):     # notarizing needs --dmg and a real --sign
            with self.assertRaises(SystemExit):
                build_mac.main()
            self.assertFalse(built.called)

    def test_signing_for_other_people_keeps_the_microphone(self):
        ent = plistlib.loads(build_mac.ENTITLEMENTS.read_bytes())
        self.assertIs(ent.get("com.apple.security.device.audio-input"), True)
        self.assertEqual(set(ent), {"com.apple.security.device.audio-input"}, "the hardened runtime keeps nothing else open")
        self.assertTrue(build_mac.is_macho(pathlib.Path("/bin/ls")) if MAC else True)
        self.assertFalse(build_mac.is_macho(ROOT / "app.py"))

    def test_strings_file_quotes(self):
        line = build_mac.strings_file({"Key": 'Say "hi" \\ there\nnow'})
        self.assertEqual(line, '"Key" = "Say \\"hi\\" \\\\ there\\nnow";\n')

    def test_the_icon_is_the_logo_on_the_macos_grid(self):
        svg = build_mac.icon_svg((ROOT / "web" / "logo.svg").read_text(encoding="utf-8"))
        self.assertIn('viewBox="0 0 1024 1024"', svg)
        self.assertIn("translate(100 100) scale(34.3333)", svg, "the 24-unit logo drawn 824 px wide")
        self.assertIn('fill="#1e3a34"', svg)


@unittest.skipUnless(MAC, "ditto and the bundle layout are macOS's")
class Install(unittest.TestCase):
    def test_only_a_veuly_this_script_built_is_replaced(self):
        work = pathlib.Path(tempfile.mkdtemp(prefix="veuly-install-"))
        texts = build_mac.i18n_texts(build_mac.PURPOSES.values())
        binary = work / "bin"
        binary.write_bytes(b"\xcf\xfa\xed\xfe")
        new = work / "build" / "Veuly.app"
        build_mac.layout(new, binary, None, build_mac.info_plist("2.0.0", texts=texts), texts)
        target = work / "Applications" / "Veuly.app"
        (target / "Contents").mkdir(parents=True)          # an older installer's copy: the same identifier, not ours
        (target / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "app.veuly", "LSUIElement": True}))
        quiet = contextlib.redirect_stdout(io.StringIO())
        with quiet, self.assertRaises(SystemExit):
            build_mac.install(new, target)
        self.assertNotIn("VeulyBuiltBy", plistlib.loads((target / "Contents" / "Info.plist").read_bytes()), "left as it was")
        shutil.rmtree(target)
        with contextlib.redirect_stdout(io.StringIO()):
            build_mac.install(new, target)
        self.assertTrue(build_mac.built_by_us(target))
        build_mac.layout(new, binary, None, build_mac.info_plist("2.0.1", texts=texts), texts)
        with contextlib.redirect_stdout(io.StringIO()):
            build_mac.install(new, target)                 # over our own: replaced
        self.assertEqual(plistlib.loads((target / "Contents" / "Info.plist").read_bytes())["CFBundleShortVersionString"], "2.0.1")
        self.assertEqual(sorted(p.name for p in target.parent.iterdir()), ["Veuly.app"], "nothing left beside it")
        shutil.rmtree(work)

    def test_a_build_never_replaces_a_veuly_it_did_not_make(self):
        work = pathlib.Path(tempfile.mkdtemp(prefix="veuly-build-out-"))
        old = work / "Veuly.app"
        (old / "Contents").mkdir(parents=True)                 # an older installer's copy: the same identifier, not ours
        (old / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "app.veuly", "LSUIElement": True}))
        (old / "marker.txt").write_text("the user's")
        with self.assertRaises(SystemExit):
            build_mac.check_replaceable(old, {"app.veuly", "app.veuly.test"})
        if shutil.which("swiftc"):
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                build_mac.build(work, [os.uname().machine], "app.veuly.test")      # refused before anything is compiled
        self.assertEqual((old / "marker.txt").read_text(), "the user's", "left as it was")
        shutil.rmtree(old)
        texts = build_mac.i18n_texts(build_mac.PURPOSES.values())
        binary = work / "bin"
        binary.write_bytes(b"\xcf\xfa\xed\xfe")
        build_mac.layout(old, binary, None, build_mac.info_plist("2.0.0", "app.veuly.test", texts), texts)
        build_mac.check_replaceable(old, {"app.veuly", "app.veuly.test"})       # one of ours, not running: replaced
        with self.assertRaises(SystemExit):
            build_mac.check_replaceable(old)                                    # but /Applications takes only the real one
        shutil.rmtree(work)


class NativePage(unittest.TestCase):
    def test_native_js_loads_between_the_recorder_and_the_app(self):
        page = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        order = [page.index(f'src="/{name}"') for name in ("recorder.js", "native.js", "app.js")]
        self.assertEqual(order, sorted(order))
        native = (ROOT / "web" / "native.js").read_text(encoding="utf-8")
        self.assertRegex(native, r"const shell = window\.veulyShell;\s*if \(!shell\) return;", "nothing runs in a browser")

    def test_the_recorder_takes_the_native_source(self):
        recorder = (ROOT / "web" / "recorder.js").read_text(encoding="utf-8")
        for hook in ("if (Recorder.native) return Recorder.native.open();", "static canShare()", "this.native.attach(this)",
                     "if (this.native) this.native.keepUp();", "track.flushed += size;", "track.ratio !== 1", "filter((s) => !s.native)"):
            self.assertIn(hook, recorder)
        self.assertIn("if (!Recorder.canShare()) return null;", (ROOT / "web" / "app.js").read_text(encoding="utf-8"))

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_a_browser_recording_is_still_brought_down_to_16_khz(self):
        """The browser's own path through recorder.js (no native source): two tracks from a 48 kHz audio graph give
        16 kHz chunks of the same length, the second one's level kept apart."""
        script = r"""
        const vm = require('vm'), fs = require('fs');
        const ctx = { console, setTimeout, clearTimeout, setInterval, clearInterval, Float32Array, Int16Array, Math, navigator: {} };
        ctx.window = ctx;
        vm.createContext(ctx);
        vm.runInContext(fs.readFileSync(process.env.VEULY_ROOT + '/web/recorder.js', 'utf8'), ctx);
        const out = vm.runInContext(`(() => {
          const chunks = [];
          const rec = new Recorder({ onChunk: (a, b) => chunks.push([a, b]), onLevel: () => {} });
          rec.ratio = 3; rec.tracks = [rec.newTrack(), rec.newTrack()];
          for (let block = 0; block < 375; block++) {               // one second at 48 kHz, in the graph's 128-frame blocks
            const a = new Float32Array(128), b = new Float32Array(128);
            for (let i = 0; i < 128; i++) { const n = block * 128 + i; a[i] = 0.5 * Math.sin(n / 48000 * 2 * Math.PI * 440); b[i] = 0.1; }
            rec.take(a, 0); rec.take(b, 1);
          }
          rec.flush();
          const [a, b] = chunks[0];
          return { chunks: chunks.length, a: a.length, b: b.length, peak: Math.max(...a), level: b[100], flushed: rec.tracks[0].flushed, canShare: Recorder.canShare() };
        })()`, ctx);
        console.log(JSON.stringify(out));
        """
        r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60, env=dict(os.environ, VEULY_ROOT=str(ROOT)))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual((out["chunks"], out["a"], out["b"], out["flushed"]), (1, 16000, 16000, 16000), out)
        self.assertTrue(15000 < out["peak"] < 16500, out)            # 0.5 of full scale survives the box filter at 440 Hz
        self.assertEqual(out["level"], round(0.1 * 32767), out)
        self.assertFalse(out["canShare"], "no getDisplayMedia and no app: only the microphone")

    def test_a_double_click_on_record_starts_one_recorder(self):
        app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("if (rec.recorder || rec.starting) return;", app)

    def test_what_a_stopped_recording_had_queued_is_sent_or_said_to_be_lost(self):
        """When its server dies the app stops the recording and keeps its chunks queued under the session (native.js,
        serverGone). A restarted server that refuses them, or does not take them in time, goes through the page's own
        recordingLost, which says that part is lost and must take a recorder that stopped already (e2e_macapp runs both)."""
        app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("if (!recorder && !rec.session) return;", app)
        self.assertIn("not_sent: 'rec_not_sent'", app)
        native = (ROOT / "web" / "native.js").read_text(encoding="utf-8")
        self.assertIn("if (unsent()) await recordingLost('not_sent');\n      else if (rec.finishing && await finishNow(slug, cid, session)) return;\n      else endRecording();", native)
        self.assertIn("} finally { rec.handing = false; }", native)
        self.assertIn("if (rec.handing) return;", app)
        self.assertIn("{ release: session }", native)
        self.assertNotIn("rec.unsent", native + app)

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_a_replaced_or_stopped_source_never_holds_the_microphone_back(self):
        """Both, in simulated time under node, through recorder.js and native.js, with a page that has the recording
        package's reconnect (a stand-in of it). This Mac's sound starts again halfway (what Share again does first): the
        old source gets nothing more and is filled with silence, the microphone goes on being sent. Share again's
        reconnect gives the new source the old one's track; the old one is stopped without a word to the app. A stall
        is said as lost and cleared on the page when the sound comes back. The recorder's last chunk holds all of the
        microphone, and the app is told to stop its sound once, by the source that has it."""
        script = r"""
        const vm = require('vm'), fs = require('fs');
        const posts = [], toasts = [];
        const ctx = { console, setTimeout, clearTimeout, setInterval, clearInterval, atob, btoa, EventTarget, Event, Date, performance, Promise, Buffer };
        ctx.window = ctx;
        Object.assign(ctx, { t: (k) => k, getLang: () => 'en', setLang: () => {}, setKind: () => {}, toast: (m) => toasts.push(m), posts, toasts,
                             veulyShell: { post: async (m) => { posts.push(m); return true; } } });
        vm.createContext(ctx);
        vm.runInContext(fs.readFileSync(process.env.VEULY_ROOT + '/web/recorder.js', 'utf8'), ctx);
        vm.runInContext(`Recorder.prototype.reconnect = async function (kind) { (this.asked || (this.asked = [])).push(kind); return 'mic'; };
                         Recorder.prototype.watch = function (stream, kind) { stream.getAudioTracks()[0].addEventListener('ended', () => { rec.dead = kind; }); };`, ctx);
        vm.runInContext(fs.readFileSync(process.env.VEULY_ROOT + '/web/native.js', 'utf8'), ctx);
        vm.runInContext(`var rec = { recorder: null, dead: '' };`, ctx);
        const run = vm.runInContext(`(async () => {
          const N = Recorder.native, R = 16000, T0 = 1.7e12, sim = { now: 0 };
          N.clock.wall = () => T0 + sim.now; N.clock.now = () => sim.now;
          let micOut = 0, macOut = 0;
          const r = new Recorder({ onChunk: (a, b) => { micOut += a.length; macOut += b ? b.length : 0; }, onLevel: () => {} });
          r.ratio = 1; r.sources = [{}]; r.tracks = [r.newTrack(1)]; r.ctx = { state: 'running', close: async () => {} };
          const mic = { native: false, getAudioTracks: () => [], getTracks: () => [] };
          const A = await N.open();
          r.streams = [mic, A]; r.native = A; A.attach(r); r.kinds = ['mic', 'share']; r.watch(A, 'share');
          rec.recorder = r;
          const b64 = (a) => Buffer.from(a.buffer).toString('base64');
          let made = 0, micSent = 0, sharing = null, B = null;
          const out = {};
          for (let step = 1; step <= 600; step++) {          // one minute, in 100 ms steps
            sim.now = step * 100;
            r.take(new Float32Array(1600).fill(0.1), 0); micSent += 1600;
            const upTo = Math.floor(sim.now / 1000 * R);
            const stalled = step > 400 && step <= 440;        // 40 s - 44 s: no blocks at all
            if (!stalled && upTo > made) { VeulyMac.sound({ at: made, t: T0 + made / 16, pcm: b64(new Int16Array(upTo - made).fill(3000)) }); }
            made = upTo;
            if (step === 150) { sharing = N.open(); sharing.catch(() => {}); }        // Share again, first half
            if (step === 152) { out.oldStopped = A.stopped; out.micAtShare = micOut; }
            if (step === 250) { out.micBeforeReconnect = micOut; out.fromLane = r.tracks[1].flushed; B = await r.reconnect('share', sharing) && r.native; }
            if (step === 252) Object.assign(out, { sameLane: B.lane === r.tracks[1], oldStoppedNow: A.stopped, isB: r.native === B && r.streams[1] === B,
                                                   stopsSoFar: posts.filter((m) => m.op === 'stop').length });
            if (step === 300) await r.reconnect('mic');
            if (step % 20 === 0) r.flush();
            if (step === 440) Object.assign(out, { deadDuringStall: rec.dead, toastsDuringStall: toasts.slice() });
            if (step === 460) out.deadAfter = rec.dead;
          }
          await r.stop();
          return Object.assign(out, { micOut, micSent, macOut, asked: r.asked, starts: posts.filter((m) => m.op === 'start').length,
                                      stops: posts.filter((m) => m.op === 'stop').length, toasts });
        })()`, ctx);
        run.then((out) => console.log(JSON.stringify(out)), (e) => { console.error(e); process.exit(1); });
        """
        r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=120, env=dict(os.environ, VEULY_ROOT=str(ROOT)))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertFalse(out["oldStopped"], out)                                # a new start leaves the attached source alone
        self.assertGreaterEqual(out["micBeforeReconnect"] - out["micAtShare"], 9 * 16000, out)    # 10 s later: sent all along
        self.assertTrue(out["sameLane"] and out["isB"] and out["oldStoppedNow"], out)
        self.assertEqual(out["stopsSoFar"], 0, "the old source does not stop the new one's capture")
        self.assertEqual(out["asked"], ["mic"], "a microphone is reconnected by the page's own reconnect")
        self.assertEqual(out["deadDuringStall"], "share", out)
        self.assertEqual(out["deadAfter"], "", "cleared when the sound came back")
        self.assertIn("native_back", out["toasts"])
        self.assertEqual(out["micOut"], out["micSent"], "the last chunk holds all of the microphone")
        self.assertEqual(out["macOut"], out["micOut"], out)
        self.assertEqual((out["starts"], out["stops"]), (2, 1), out)

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_this_macs_sound_stays_in_step_with_the_microphone(self):
        """Twenty minutes with the Mac's clock 300 ppm fast and a 5 s stall, in simulated time, through recorder.js and
        native.js under node: the clicks each second of both tracks stay within 50 ms, and the microphone never waits."""
        script = r"""
        const vm = require('vm'), fs = require('fs');
        const ctx = { console, setTimeout, clearTimeout, setInterval, clearInterval, atob, btoa, EventTarget, Event, Date, performance, Promise, Buffer };
        ctx.window = ctx;
        Object.assign(ctx, { t: (k) => k, getLang: () => 'en', setLang: () => {}, setKind: () => {}, veulyShell: { post: async () => true } });
        vm.createContext(ctx);
        vm.runInContext(fs.readFileSync(process.env.VEULY_ROOT + '/web/recorder.js', 'utf8'), ctx);
        vm.runInContext(fs.readFileSync(process.env.VEULY_ROOT + '/web/native.js', 'utf8'), ctx);
        const out = vm.runInContext(`(() => {
          const N = Recorder.native, R = 16000, SECONDS = 1200, F = R * (1 + 300e-6), T0 = 1.7e12;
          const sim = { now: 0 };
          N.clock.wall = () => T0 + sim.now; N.clock.now = () => sim.now;
          const marks = [[], []], seen = [0, 0], high = [false, false];
          const scan = (chunk, k) => { if (!chunk) return; for (let i = 0; i < chunk.length; i++) { const on = chunk[i] > 8000;
            if (on && !high[k]) marks[k].push(seen[k] + i); high[k] = on; } seen[k] += chunk.length; };
          const rec = new Recorder({ onChunk: (a, b) => { scan(a, 0); scan(b, 1); }, onLevel: () => {} });
          rec.ratio = 1; rec.sources = [{}]; rec.streams = []; rec.tracks = [rec.newTrack(1)];
          const sound = new N.Sound(); sound.stop = () => {}; rec.native = sound; sound.attach(rec);
          let ended = 0; sound.track.addEventListener('ended', () => { ended++; });
          const b64 = (a) => Buffer.from(a.buffer).toString('base64');
          const mic = new Float32Array(1600);
          let micSent = 0, made = 0, during = 0;
          for (let step = 1; step <= SECONDS * 10; step++) {
            sim.now = step * 100;
            const sec = sim.now / 1000;
            mic.fill(0);
            const j = Math.ceil(micSent / R) * R - micSent;
            if (j < 1600) mic[j] = mic[j + 1] = mic[j + 2] = 0.61;
            rec.take(mic, 0); micSent += 1600;
            const upTo = Math.floor(sec * F);
            if (sec > 600 && sec <= 605) made = upTo;
            else if (upTo > made) {
              const n = upTo - made, block = new Int16Array(n);
              for (let c = Math.floor(made / F); ; c++) { const at = Math.round(c * F) - made; if (at >= n) break; if (at < 0) continue;
                for (let q = 0; q < 3 && at + q < n; q++) block[at + q] = 20000; }
              sound.receive({ at: made, t: T0 + made / F * 1000, pcm: b64(block) });
              made = upTo;
            }
            if (step % 20 === 0) rec.flush();
            if (step === 6000) during = seen[0];
            if (step === 6060) during = seen[0] - during;
          }
          rec.flush();
          let worst = 0, k = 0;
          for (const m of marks[0]) {
            while (k + 1 < marks[1].length && Math.abs(marks[1][k + 1] - m) <= Math.abs(marks[1][k] - m)) k++;
            const t = m / R, e = Math.abs(marks[1][k] - m) / R * 1000;
            if (t > 30 && !(t > 599 && t < 607)) worst = Math.max(worst, e);
          }
          return { worst, ended, during: during / R, micClicks: marks[0].length, macClicks: marks[1].length };
        })()`, ctx);
        console.log(JSON.stringify(out));
        """
        r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=120, env=dict(os.environ, VEULY_ROOT=str(ROOT)))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertLess(out["worst"], 50, out)                       # uncorrected: 300 ppm of 20 minutes is 360 ms
        self.assertGreaterEqual(out["during"], 5.0, out)              # the microphone went on being sent through the 5 s stall
        self.assertEqual(out["ended"], 1, out)
        self.assertGreaterEqual(out["macClicks"], 1190, out)


@unittest.skipUnless(os.name != "nt", "stdin pipes and signals")
class AppPy(unittest.TestCase):
    def start(self, *extra, home=None, own_group=False):
        home = home or pathlib.Path(tempfile.mkdtemp(prefix="veuly-macapp-py-"))
        port = a_port()
        env = {k: v for k, v in os.environ.items() if k != "TRANSCAPP_HOME"}
        env.update(VEULY_HOME=str(home), VEULY_SECRETS="file", PYTHONDONTWRITEBYTECODE="1")
        # own_group: a process group of its own, as the Mac app starts it (otherwise it shares this test's)
        p = subprocess.Popen([sys.executable, "-B", str(ROOT / "app.py"), "--no-open", "--port", str(port), *extra], env=env,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             start_new_session=own_group)
        for _ in range(50):
            line = p.stdout.readline()
            if line.startswith("Open: "):
                self.link = line[6:].strip()
                break
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/ping")
        self.assertEqual(json.loads(conn.getresponse().read())["app"], "transcapp")
        conn.close()
        return p, home, port

    def test_it_quits_when_the_mac_apps_pipe_closes(self):
        p, home, port = self.start("--quit-with-stdin")
        p.stdin.close()                                   # what happens when the app ends, even when it is killed
        p.wait(timeout=15)
        rest = p.stdout.read()
        p.stdout.close()
        self.assertEqual(p.returncode, 0)
        self.assertIn("Closed.", rest)
        self.assertEqual(json.loads((home / "server.json").read_text()), {"port": port}, "it quit like Quit: the port stays")

    def test_what_the_server_started_stops_with_it_when_the_pipe_closes(self):
        """The Mac app was killed during a transcription: the server quits as its pipe closes, the transcriber it
        started (a stand-in that only sleeps) stops with it, and the class says it was interrupted, not that it failed."""
        home = pathlib.Path(tempfile.mkdtemp(prefix="veuly-macapp-group-"))
        tools = pathlib.Path(tempfile.mkdtemp(prefix="veuly-macapp-tools-"))
        pids = tools / "pids"
        whisper = tools / "whisper-cli"
        whisper.write_text(f"#!/bin/sh\necho $$ >> '{pids}'\nexec /bin/sleep 60\n")
        whisper.chmod(0o755)
        (tools / "model.bin").write_bytes(b"\0" * 1024)
        code = ("import json, sys; from transcapp import library, paths; lib = library.Library(); c = lib.create_course('Physics', 'en');"
                "k = lib.create_class(c['slug'], 'Interrupted'); d = lib.class_dir(c['slug'], k['id']);"
                "open(d / 'audio.pcm', 'wb').write(b'\\x10\\x00' * 32000); lib.update_meta(c['slug'], k['id'], status='recording');"
                "paths.save_settings({'engine': '', 'whisper_path': sys.argv[1], 'whisper_model': sys.argv[2]}); print(c['slug'], k['id'])")
        env = {k: v for k, v in os.environ.items() if k != "TRANSCAPP_HOME"}
        env.update(VEULY_HOME=str(home), VEULY_SECRETS="file", PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
        slug, cid = subprocess.run([sys.executable, "-B", "-c", code, str(whisper), str(tools / "model.bin")], env=env, cwd=ROOT,
                                   capture_output=True, text=True, check=True).stdout.split()
        p, home, port = self.start("--quit-with-stdin", home=home, own_group=True)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", self.link[len(f"http://127.0.0.1:{port}"):])
        page = conn.getresponse()
        page.read()
        cookie = page.getheader("Set-Cookie").split(";")[0]
        token = self.link.split("?t=", 1)[1]                 # every route wants the session's token itself (server.py)
        conn.request("POST", f"/api/classes/{slug}/{cid}/finish", headers={"Cookie": cookie, "X-TA": token})
        self.assertEqual(conn.getresponse().status, 200)
        conn.close()
        for _ in range(100):
            if pids.exists() and pids.read_text().strip():
                break
            time.sleep(0.1)
        child = int(pids.read_text().split()[0])
        p.stdin.close()                                   # the app is gone
        p.wait(timeout=20)
        rest = p.stdout.read()
        p.stdout.close()
        gone = False
        for _ in range(50):
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                gone = True
                break
            time.sleep(0.1)
        if not gone:
            os.kill(child, signal.SIGKILL)
        self.assertTrue(gone, "the transcriber outlived the server")
        self.assertEqual(p.returncode, 0, rest)
        self.assertIn("Closed.", rest)
        meta = json.loads((home / "library" / slug / cid / "class.json").read_text())
        self.assertEqual((meta["status"], meta["error_kind"]), ("error", "interrupted"), meta)
        shutil.rmtree(tools)
        shutil.rmtree(home, ignore_errors=True)

    def test_sigterm_quits_like_the_quit_button(self):
        p, home, port = self.start()
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=15)
        rest = p.stdout.read()
        p.stdout.close()
        p.stdin.close()
        self.assertEqual(p.returncode, 0)
        self.assertIn("Closed.", rest)
        self.assertEqual(json.loads((home / "server.json").read_text()), {"port": port})


if __name__ == "__main__":
    unittest.main()
