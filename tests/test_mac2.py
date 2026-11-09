"""The Mac app while it records, the parts Python can check (Wave 2b "mac2"): the server keeps where and how long the Mac
slept in a recording (class.json `gaps`), the programs the server starts are told to the Mac app as they start and end
(so the app can stop them when its server dies), app.py says nothing about a window or Ctrl+C when the app runs it, the
new texts in three languages, and, under node, what web/native.js tells the app (the classes being written and which
finished, once each), what it hands to the page of a server started again on another port, and what it does when the
Mac wakes up. The app itself is tested by tests/e2e_macapp.py.

  python3 -m unittest discover tests
"""
import json
import os
import pathlib
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_core import Client  # noqa: E402  (sets TRANSCAPP_HOME and VEULY_SECRETS)
from test_macapp import a_port, i18n_keys  # noqa: E402
from transcapp import paths, server  # noqa: E402

SECOND = struct.pack("<16000h", *([1000] * 16000))


class Gaps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd, cls.app = server.serve(ROOT / "web", 0)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.c = Client(cls.app)
        cls.c.call("GET", f"/?t={cls.app.token}")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def test_the_mac_asleep_is_kept_where_it_happened(self):
        _, course = self.c.call("POST", "/api/courses", {"name": "Sleepy"})
        _, meta = self.c.call("POST", f"/api/courses/{course['slug']}/classes", {})
        base = f"/api/classes/{course['slug']}/{meta['id']}"
        _, lease = self.c.call("POST", f"{base}/record", {})
        session = lease["session"]
        for seq in range(3):
            self.assertEqual(self.c.call("POST", f"{base}/audio?seq={seq}&track=0&session={session}", SECOND, raw=True)[0], 200)
        status, _ = self.c.call("POST", f"{base}/mute", {"on": False, "gap": 2700, "session": session})
        self.assertEqual(status, 200)
        for bad in (True, -5, "600", 10 ** 9, 0):                 # not a number of seconds a Mac can sleep: left out
            self.c.call("POST", f"{base}/mute", {"on": False, "gap": bad, "session": session})
        self.c.call("POST", f"{base}/mute", {"on": True, "session": session})
        self.c.call("POST", f"{base}/audio?seq=3&track=0&session={session}", SECOND, raw=True)
        self.c.call("POST", f"{base}/mute", {"on": True, "gap": 60.25, "session": session})     # muted when it slept: still muted
        cls = self.app.lib.meta(course["slug"], meta["id"])
        self.assertEqual(cls["gaps"], [[3.0, 2700.0], [4.0, 60.2]])
        self.assertEqual(cls["muted"], [[3.0, None]], "a gap changes nothing about Mute")
        status, refused = self.c.call("POST", f"{base}/mute", {"on": False, "gap": 5, "session": "another-tab"})
        self.assertEqual((status, refused["kind"]), (409, "recording_elsewhere"))


class Children(unittest.TestCase):
    def tearDown(self):
        paths.on_child = None

    @unittest.skipIf(os.name == "nt", "process groups")
    def test_each_program_is_told_as_it_starts_and_ends(self):
        told = []
        paths.on_child = lambda pid, running: told.append((pid, running))
        paths.run_child(["/bin/sh", "-c", "exit 3"])
        p = paths.start_child(["/bin/sleep", "5"])
        self.assertEqual(os.getpgid(p.pid), p.pid, "a process group of its own: the app stops it by that number")
        paths.end_child(p)
        p.wait(5)
        self.assertEqual([r for _, r in told], [True, False, True, False])
        self.assertEqual(told[2][0], p.pid)
        self.assertEqual(told[0][0], told[1][0])
        paths.on_child = lambda pid, running: 1 / 0            # telling never stops a program
        self.assertEqual(paths.run_child(["/bin/sh", "-c", "exit 0"]).returncode, 0)


@unittest.skipIf(os.name == "nt", "stdin pipes and signals")
class AppPyForTheMacApp(unittest.TestCase):
    def start(self, home, *extra):
        port = a_port()
        env = {k: v for k, v in os.environ.items() if k != "TRANSCAPP_HOME"}
        env.update(VEULY_HOME=str(home), VEULY_SECRETS="file", PYTHONDONTWRITEBYTECODE="1")
        p = subprocess.Popen([sys.executable, "-B", str(ROOT / "app.py"), "--no-open", "--port", str(port), *extra], env=env,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        lines = []
        for _ in range(50):
            line = p.stdout.readline()
            lines.append(line)
            if line.startswith("Open: "):
                break
        time.sleep(0.5)
        return p, port, lines

    def test_in_a_terminal_it_says_how_to_quit(self):
        home = pathlib.Path(tempfile.mkdtemp(prefix="veuly-mac2-term-"))
        p, _, _ = self.start(home)
        said = p.stdout.readline()                       # the line right after the link
        p.send_signal(signal.SIGTERM)
        rest = p.communicate(timeout=15)[0]
        self.assertIn("Keep this window open", said + rest)
        shutil.rmtree(home, ignore_errors=True)

    def test_the_mac_apps_server_names_its_programs_and_no_window(self):
        """Run as the Mac app runs it: no word of a window or Ctrl+C. A transcription's whisper-cli (a stand-in that only
        sleeps) is told as "Child: <pid> started", in a process group of its own; a server killed then leaves it running,
        which is why the app stops that group itself (Server.swift, endChildren; e2e_macapp runs it)."""
        home = pathlib.Path(tempfile.mkdtemp(prefix="veuly-mac2-app-"))
        tools = pathlib.Path(tempfile.mkdtemp(prefix="veuly-mac2-tools-"))
        whisper = tools / "whisper-cli"
        whisper.write_text("#!/bin/sh\nexec /bin/sleep 60\n")
        whisper.chmod(0o755)
        (tools / "model.bin").write_bytes(b"\0" * 1024)
        code = ("import sys; from transcapp import library, paths; lib = library.Library(); c = lib.create_course('Physics', 'en');"
                "k = lib.create_class(c['slug'], 'Waves'); d = lib.class_dir(c['slug'], k['id']);"
                "open(d / 'audio.pcm', 'wb').write(b'\\x10\\x00' * 32000); lib.update_meta(c['slug'], k['id'], status='recording');"
                "paths.save_settings({'engine': '', 'whisper_path': sys.argv[1], 'whisper_model': sys.argv[2]}); print(c['slug'], k['id'])")
        env = {k: v for k, v in os.environ.items() if k != "TRANSCAPP_HOME"}
        env.update(VEULY_HOME=str(home), VEULY_SECRETS="file", PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
        slug, cid = subprocess.run([sys.executable, "-B", "-c", code, str(whisper), str(tools / "model.bin")], env=env, cwd=ROOT,
                                   capture_output=True, text=True, check=True).stdout.split()
        p, port, lines = self.start(home, "--quit-with-stdin")
        child = None
        try:
            self.assertFalse([line for line in lines if "window" in line or "Ctrl+C" in line], lines)
            link = next(line[6:].strip() for line in lines if line.startswith("Open: "))

            class App:
                pass
            app = App()
            app.port = port
            c = Client(app)
            c.call("GET", link[len(f"http://127.0.0.1:{port}"):])
            self.assertEqual(c.call("POST", f"/api/classes/{slug}/{cid}/finish", {})[0], 200)
            deadline = time.time() + 20
            while time.time() < deadline and child is None:
                line = p.stdout.readline()
                if line.startswith("Child: ") and line.split()[2] == "started":
                    child = int(line.split()[1])
            self.assertIsNotNone(child, "the transcriber was not told")
            self.assertEqual(os.getpgid(child), child, "the transcriber leads a process group of its own")
            os.kill(p.pid, signal.SIGKILL)                     # the server crashes
            p.wait(10)
            time.sleep(0.3)
            os.kill(child, 0)                                  # still there: only the app (endChildren) can stop it now
        finally:
            if p.poll() is None:
                p.kill()
            p.stdout.close()
            p.stdin.close()
            if child:
                try:
                    os.killpg(child, signal.SIGKILL)
                except OSError:
                    pass
            shutil.rmtree(tools, ignore_errors=True)
            shutil.rmtree(home, ignore_errors=True)


class Texts(unittest.TestCase):
    def test_the_menu_bar_notifications_login_and_sleep_texts(self):
        keys = i18n_keys()
        mine = ["mac_status_recording", "mac_status_finish", "mac_status_show", "mac_status_writing", "mac_status_writing_pct",
                "mac_note_ready", "mac_note_failed", "mac_login", "mac_login_help", "mac_login_approve", "mac_login_open",
                "mac_login_failed", "mac_slept"]
        twins = ["mac_status_finish", "mac_status_writing", "mac_status_writing_pct", "mac_note_ready", "mac_note_failed", "mac_login_help"]
        for lang in ("en", "es", "ca"):
            self.assertEqual(set(mine + [k + "__work" for k in twins]) - keys[lang], set(), lang)
        native = (ROOT / "web" / "native.js").read_text(encoding="utf-8")
        self.assertIn("t(ok ? 'mac_note_ready' : 'mac_note_failed')", native)


NODE_PAGE = r"""
const vm = require('vm'), fs = require('fs');
const root = process.env.VEULY_ROOT;
const posts = [], calls = [], toasts = [], gone = [];
const clock = { now: 0 };
const ctx = { console, setTimeout, clearTimeout, setInterval, clearInterval, atob, btoa, EventTarget, Event, Date, Promise, Buffer,
              Float32Array, Int16Array, Uint8Array, Math, JSON, Map, Set, Object, Array, String, Number, Error, URL,
              performance: { now: () => clock.now }, navigator: { language: 'en' } };
ctx.window = ctx;
ctx.document = { readyState: 'complete', documentElement: {}, querySelector: () => ({ childElementCount: 1 }), addEventListener: () => {} };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(root + '/web/i18n.js', 'utf8'), ctx);
vm.runInContext(fs.readFileSync(root + '/web/recorder.js', 'utf8'), ctx);
Object.assign(ctx, { posts, calls, toasts, gone, clock, veulyShell: { post: async (m) => { posts.push(m); return true; } } });
vm.runInContext(`
  var rec = { recorder: null, slug: null, cid: null, session: null, muted: false, queue: [], lost: null, finishing: false, slept: [] };
  var S = { settings: { mode: 'course' }, courses: [], view: { name: 'home' }, cls: null };
  var details = {}, dirty = false, liveEditor = null, notes = { rev: 'r0' }, sessionToken = 'old';
  var LOST = ['recording_elsewhere', 'recording_closed', 'not_found'];
  const HELP = (kind) => 'help:' + kind;
  const isLive = () => false, recSeconds = () => 12;
  async function api(method, path, body) {
    calls.push([method, path, body]);
    const part = path.split('?')[0].split('/');          // /api/classes/<slug>/<cid>
    if (method === 'GET' && part[2] === 'classes') { const d = details[part[3] + '/' + part[4]]; if (!d) throw Object.assign(new Error('gone'), { kind: 'not_found' }); return d; }
    return { ok: true };
  }
  async function saveLive() {}
  function pump() { while (rec.queue.length && rec.session) { const item = rec.queue.shift(); calls.push(['SEND', item]); } }
  async function drain() { pump(); return true; }
  function endRecording() { Object.assign(rec, { recorder: null, session: null, finishing: false }); gone.push('end'); }
  async function recordingLost(kind) { gone.push('lost:' + kind); endRecording(); }
  async function loadState() {}
  async function afterAction() {}
  async function go(view) { S.view = view; calls.push(['GO', view]); }
  function fail(e) { gone.push('fail:' + e.message); }
  function toast(m) { toasts.push(m); }
  function recChanged() {}
  function finishClass() {}
  async function completeFinish() {}
  async function reconnectMic() { calls.push(['REOPEN']); rec.dead = ''; return true; }
`, ctx);
vm.runInContext(fs.readFileSync(root + '/web/native.js', 'utf8'), ctx);
const page = (code) => vm.runInContext(code, ctx);
"""


# The real go and startRecording, cut from web/app.js: takeOver is safe only together with them. The microphone is a stand-in.
REAL_PAGE = r"""
const appSource = fs.readFileSync(root + '/web/app.js', 'utf8');
const cut = (from, to) => {
  const a = appSource.indexOf(from), b = appSource.indexOf(to, a);
  if (a < 0 || b < 0) throw new Error('web/app.js no longer has: ' + from);
  return appSource.slice(a, b);
};
vm.runInContext(`
  var prefsDirty = false, noteDraft = null, readyHere = '', pollTimer = 0, LOADERS = {}, mic = {};
  const noteDirty = () => false, askDiscardNote = () => {}, askDropNotes = () => {}, mode = () => 'course', setKind = () => {},
        arrive = () => {}, poll = () => {}, showSection = () => {}, showMoment = () => {}, $ = () => null;
  const render = () => calls.push(['DRAW', S.view.cid || S.view.name]);
  // The microphone answers only when the test says so; the class is the one of the click, as in the real one.
  async function beginRecording() {
    const slug = S.view.slug, cid = S.view.cid;
    calls.push(['START', cid]);
    await new Promise((answer) => { mic.answer = answer; });
    Object.assign(rec, { recorder: {}, slug, cid, session: 's2' });
  }
  details['calc/c1'] = { status: 'new', live_rev: 'a' }; details['calc/c2'] = { status: 'new', live_rev: 'b' };
`, ctx);
vm.runInContext(cut('let navigation = 0;', '\nfunction render()') + cut('async function startRecording', '\nasync function beginRecording'), ctx);
const c1 = { name: 'class', slug: 'calc', cid: 'c1' }, c2 = { name: 'class', slug: 'calc', cid: 'c2' };
const state = { view: { slug: 'calc', cid: 'c1' }, kept: [], items: [] };
const hold = (cid) => page(`(() => { const plain = api; let open; const held = new Promise((r) => { open = r; });
  api = async (method, path, body) => { if (method === 'GET' && path.endsWith('/${cid}')) await held; return plain(method, path, body); };
  return open; })()`);
const settle = () => new Promise((r) => setTimeout(r, 20));
const report = (extra) => console.log(JSON.stringify({ view: page('S.view'), drawn: calls.filter(([m]) => m === 'DRAW').map(([, c]) => c),
  started: calls.filter(([m]) => m === 'START').map(([, c]) => c), recording: page('rec.recorder ? rec.cid : null'), ...extra }));
"""


@unittest.skipUnless(shutil.which("node"), "needs node")
class NativePage(unittest.TestCase):
    def node(self, script):
        r = subprocess.run(["node", "-e", NODE_PAGE + script], capture_output=True, text=True, timeout=60, env=dict(os.environ, VEULY_ROOT=str(ROOT)))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_what_is_being_written_and_what_finished_is_said_once(self):
        out = self.node(r"""
        (async () => {
          const tick = async (ms = 3000) => { clock.now += ms; page('VeulyMac.watch()'); await new Promise((r) => setTimeout(r, 5)); return page('VeulyMac.watch()'); };
          page(`S.courses = [{ slug: 'atlas', kind: 'project', classes: [{ id: 'k1', title: 'Kickoff', status: 'processing' }] },
                             { slug: 'calc', kind: 'course', classes: [{ id: 'c1', title: 'Limits', status: 'done' }] }];
                details['atlas/k1'] = { status: 'processing', step: 'transcribing', progress: 40.4, title: 'Kickoff' };`);
          const out = {};
          out.writing = (await tick()).writing;
          page(`details['atlas/k1'] = { status: 'processing', step: 'writing', progress: 100, title: 'Kickoff' };`);
          out.writingOn = (await tick()).writing.map((w) => w.progress);
          page(`details['atlas/k1'] = { status: 'done', title: 'Kickoff' };`);
          out.done = (await tick()).news;
          out.again = [(await tick()).news.length, (await tick()).news.length];            // the page's list still says "processing"
          page(`S.courses[0].classes[0].status = 'done';`);
          await tick();
          page(`S.courses[0].classes[0].status = 'processing'; details['atlas/k1'] = { status: 'error', error_kind: 'busy', title: 'Kickoff' };`);
          out.failed = (await tick()).news;                                                 // written again, and failed this time
          page(`S.view = { name: 'class', slug: 'calc', cid: 'c1' }; S.cls = { status: 'done' };`);
          await tick();
          page(`S.cls = { status: 'processing' }; details['calc/c1'] = { status: 'done', title: 'Limits' };`);
          out.open = (await tick()).news;                                                   // Write it again on the class page
          page(`rec.recorder = {}; rec.session = 's'; rec.slug = 'calc'; rec.cid = 'c1'; rec.muted = true;`);
          out.recording = page('VeulyMac.watch()').recording;
          out.gets = calls.filter(([m]) => m === 'GET').length;
          console.log(JSON.stringify(out));
        })();""")
        self.assertEqual(out["writing"], [{"slug": "atlas", "cid": "k1", "title": "Kickoff", "project": True, "step": "transcribing", "progress": 40}])
        self.assertEqual(out["writingOn"], [-1], "a number only while it is turned into text")
        self.assertEqual(out["done"], [{"slug": "atlas", "cid": "k1", "ok": True, "here": False, "title": "Your minutes are ready", "body": "Kickoff"}])
        self.assertEqual(out["again"], [0, 0])
        self.assertEqual(out["failed"][0]["title"], "The minutes could not be written")
        self.assertEqual(out["failed"][0]["body"], "Kickoff: help:busy")
        self.assertEqual([(n["title"], n["here"]) for n in out["open"]], [("Your note is ready", True)], "a class's own kind, whichever side shows")
        self.assertEqual(out["recording"], {"slug": "calc", "cid": "c1", "title": "Limits", "project": False, "seconds": 12, "muted": True, "finishing": False, "headsups": 0})
        self.assertLessEqual(out["gets"], 12, "one look per class every few seconds, not more")

    def test_a_server_on_another_port_gets_what_the_page_held(self):
        out = self.node(r"""
        (async () => {
          page(`rec.slug = 'calc'; rec.cid = 'c1'; rec.session = 's1'; rec.finishing = true;
                rec.queue = [{ seq: 7, track: 0, data: new Int16Array([1, -2, 32767, -32768]) }, { on: true }, { on: false, gap: 600 },
                             { seq: 7, track: 1, data: new Int16Array([5, 6]) }];
                S.view = { name: 'class', slug: 'calc', cid: 'c1' }; S.cls = { live: '', typed: {} }; dirty = true;
                liveEditor = { getValue: () => 'typed meanwhile', getTimes: () => ({ abc: 12 }) };`);
          const state = JSON.parse(JSON.stringify(page('VeulyMac.handOver()')));       // what the app keeps and gives back
          page(`Object.assign(rec, { slug: null, cid: null, session: null, finishing: false, queue: [] }); dirty = false; S.view = { name: 'home' };`);
          const left = await page('VeulyMac.takeOver')(state);
          const sent = calls.filter(([m]) => m === 'SEND').map(([, item]) => item.data ? [item.seq, item.track, Array.from(item.data)] : [item.on, item.gap || 0]);
          const put = calls.find(([m]) => m === 'PUT'), finish = calls.find(([m, p]) => m === 'POST' && String(p).endsWith('/finish'));
          console.log(JSON.stringify({ left, sent, put, finish, gone, view: page('S.view'), session: page('rec.session'),
                                       release: calls.some(([m, p, b]) => String(p).endsWith('/record') && b && b.release) }));
        })();""")
        self.assertEqual(out["sent"], [[7, 0, [1, -2, 32767, -32768]], [True, 0], [False, 600], [7, 1, [5, 6]]])
        self.assertEqual(out["put"], ["PUT", "/api/classes/calc/c1", {"live": "typed meanwhile", "live_base": "r0", "typed": {"abc": 12}}])
        self.assertEqual(out["finish"], ["POST", "/api/classes/calc/c1/finish", {"session": "s1"}], "the Finish pressed before")
        self.assertEqual((out["gone"], out["session"], out["release"]), (["end"], None, False))
        self.assertEqual(out["view"], {"name": "class", "slug": "calc", "cid": "c1"})
        self.assertEqual(out["left"], {"left": 0})

    def test_a_take_over_leaves_the_person_where_they_went_meanwhile(self):
        """The class that was on screen comes back after a move, but not over a recording the person started on the
        new page while the notes were still being handed over (it would hide the class being recorded)."""
        out = self.node(r"""
        (async () => {
          page(`S.view = { name: 'class', slug: 'calc', cid: 'c1' }; S.cls = { live: '', typed: {} }; dirty = true;
                liveEditor = { getValue: () => 'typed', getTimes: () => ({}) };`);
          const state = JSON.parse(JSON.stringify(page('VeulyMac.handOver()')));
          page(`dirty = false; S.view = { name: 'home' };
                const plainApi = api;
                api = async (method, path, body) => {      // Record pressed on another class while the notes go
                  if (method === 'PUT') { S.view = { name: 'class', slug: 'calc', cid: 'c2' }; Object.assign(rec, { recorder: {}, session: 's2', slug: 'calc', cid: 'c2' }); }
                  return plainApi(method, path, body);
                };`);
          await page('VeulyMac.takeOver')(state);
          const kept = { view: page('S.view'), gos: calls.filter(([m]) => m === 'GO').length, put: calls.some(([m]) => m === 'PUT') };
          page(`Object.assign(rec, { recorder: null, session: null }); S.view = { name: 'home' }; api = plainApi;`);
          await page('VeulyMac.takeOver')(state);                // nobody went anywhere: the class comes back
          console.log(JSON.stringify({ ...kept, back: page('S.view') }));
        })();""")
        self.assertEqual(out["view"], {"name": "class", "slug": "calc", "cid": "c2"}, "the class being recorded stays on screen")
        self.assertEqual((out["gos"], out["put"]), (0, True), "the notes were still saved")
        self.assertEqual(out["back"], {"name": "class", "slug": "calc", "cid": "c1"})

    def real(self, script):
        return self.node(REAL_PAGE + script)

    def test_a_recording_whose_microphone_has_not_answered_keeps_its_class_on_screen(self):
        """Record pressed on the class just opened, the microphone still to answer when takeOver goes to bring back the
        class of before: rec.recorder is unset, the page has not moved since, and only startRecording's own mark says so."""
        out = self.real(r"""
        (async () => {
          page(`S.view = { name: 'class', slug: 'calc', cid: 'c2' };`);
          page('startRecording(null)');
          const left = await page('VeulyMac.takeOver')(state);
          const during = page('S.view');
          page('mic.answer()');
          await settle();
          report({ during });
        })();""")
        self.assertEqual(out["during"], {"name": "class", "slug": "calc", "cid": "c2"})
        self.assertEqual(out["view"], {"name": "class", "slug": "calc", "cid": "c2"}, "the class being recorded stays on screen")
        self.assertEqual((out["drawn"], out["started"], out["recording"]), ([], ["c2"], "c2"))

    def test_a_recording_started_while_the_old_class_loads_stays_on_screen(self):
        """Record pressed while the class of before is still loading: that navigation gives way, whatever the microphone does."""
        out = self.real(r"""
        (async () => {
          page(`S.view = { name: 'class', slug: 'calc', cid: 'c2' };`);
          const open = hold('c1');
          const taking = page('VeulyMac.takeOver')(state);
          await settle();
          page('startRecording(null)');
          open();
          await taking;
          await settle();
          page('mic.answer()');
          await settle();
          report({});
        })();""")
        self.assertEqual(out["view"], {"name": "class", "slug": "calc", "cid": "c2"}, "the class being recorded stays on screen")
        self.assertEqual((out["drawn"], out["recording"]), ([], "c2"))

    def test_a_page_the_person_is_opening_is_not_cancelled_by_a_take_over(self):
        """A class they opened is still loading (S.view has not changed yet) when takeOver goes to navigate: theirs lands."""
        out = self.real(r"""
        (async () => {
          const open = hold('c2');
          const theirs = page(`go({ name: 'class', slug: 'calc', cid: 'c2' })`);
          await page('VeulyMac.takeOver')(state);
          open();
          await theirs;
          await settle();
          report({});
        })();""")
        self.assertEqual(out["view"], {"name": "class", "slug": "calc", "cid": "c2"})
        self.assertEqual(out["drawn"], ["c2"], "the class of before is never drawn")

    def test_the_pages_own_navigation_never_cancels_one_of_theirs(self):
        """The page's own start reaching its first page after the person opened one: theirs is the one that lands."""
        out = self.real(r"""
        (async () => {
          const open = hold('c2');
          const theirs = page(`go({ name: 'class', slug: 'calc', cid: 'c2' })`);
          await page(`go({ name: 'home' }, true)`);
          open();
          await theirs;
          await settle();
          report({});
        })();""")
        self.assertEqual(out["view"], {"name": "class", "slug": "calc", "cid": "c2"})
        self.assertEqual(out["drawn"], ["c2"])

    def test_a_page_opened_while_the_old_class_loads_wins(self):
        out = self.real(r"""
        (async () => {
          const open = hold('c1');
          const taking = page('VeulyMac.takeOver')(state);
          await settle();
          await page(`go({ name: 'class', slug: 'calc', cid: 'c2' })`);
          open();
          await taking;
          await settle();
          report({});
        })();""")
        self.assertEqual(out["view"], {"name": "class", "slug": "calc", "cid": "c2"})
        self.assertEqual(out["drawn"], ["c2"])

    def test_when_nobody_did_anything_the_class_comes_back(self):
        """The page's own navigations are not the person's: without this the fix would be never coming back at all."""
        out = self.real(r"""
        (async () => {
          await page(`go({ name: 'home' }, true)`);        // the page's own start
          await page('VeulyMac.takeOver')(state);
          report({ acted: page('!!S.acted') });
        })();""")
        self.assertEqual(out["view"], {"name": "class", "slug": "calc", "cid": "c1"})
        self.assertEqual((out["drawn"], out["acted"]), (["home", "c1"], False))

    def test_no_recording_starts_while_what_the_old_page_held_is_on_its_way(self):
        """Record pressed before the old recording's queue was taken up: its start would overwrite that queue."""
        out = self.real(r"""
        (async () => {
          const held = { slug: 'calc', cid: 'c1', session: 's1', items: [], kept: [], view: { slug: 'calc', cid: 'c1' },
                         typing: { slug: 'calc', cid: 'c1', live: 'x', live_base: 'r', typed: {} } };
          held.items = [{ seq: 3, track: 0, pcm: Buffer.from(new Int16Array([1, 2]).buffer).toString('base64') }];
          page(`S.view = { name: 'class', slug: 'calc', cid: 'c2' }; var pressed = null;
                const plainPut = api;
                api = async (method, path, body) => { if (method === 'PUT') pressed = startRecording(null); return plainPut(method, path, body); };`);
          await page('VeulyMac.takeOver')(held);
          const sent = calls.filter(([m]) => m === 'SEND').map(([, item]) => item.seq);
          report({ sent, handing: page('!!rec.handing') });
        })();""")
        self.assertEqual(out["started"], [], "the press is refused while the queue goes, as during letGo")
        self.assertEqual(out["sent"], [3], "the old recording's audio went")
        self.assertFalse(out["handing"], "and recording is possible again")
        self.assertEqual(out["view"], {"name": "class", "slug": "calc", "cid": "c2"}, "the press still counts: the page stays")

    def test_what_the_old_page_held_waits_for_a_start_already_under_way(self):
        """Record pressed before takeOver began: the queue is not taken up under it, or the start would overwrite it."""
        out = self.real(r"""
        (async () => {
          page(`S.view = { name: 'class', slug: 'calc', cid: 'c2' };`);
          page('startRecording(null)');
          const taking = page('VeulyMac.takeOver')({ slug: 'calc', cid: 'c1', session: 's1', items: [], kept: [], view: { slug: 'calc', cid: 'c1' } });
          await settle();
          const let_go = () => calls.some(([m, p, b]) => String(p).endsWith('/record') && b && b.release);
          const during = { session: page('rec.session'), let_go: let_go() };
          page('mic.answer()');
          await taking;
          report({ during, session: page('rec.session'), handing: page('!!rec.handing') });
        })();""")
        self.assertEqual(out["during"], {"session": None, "let_go": False}, "not taken up while the microphone is still to answer")
        self.assertFalse(out["handing"], "and recording is not left refused")
        self.assertEqual((out["session"], out["recording"]), ("s2", "c2"))
        self.assertEqual(out["view"], {"name": "class", "slug": "calc", "cid": "c2"})

    def test_the_mac_wakes_up_while_recording(self):
        out = self.node(r"""
        (async () => {
          page(`rec.session = 's'; rec.recorder = { kinds: ['mic', 'share'], flushed: 0, flush() { this.flushed++; },
                  ctx: { state: 'suspended', async resume() { this.state = 'running'; } },
                  streams: [{ native: false, getAudioTracks: () => [{ readyState: 'live', muted: true }] }],
                  native: { rewound: 0, rewind() { this.rewound++; } } };`);
          const sends = () => calls.filter(([m]) => m === 'SEND').map(([, item]) => item);
          const woke = await page('VeulyMac.woke')(745);
          const queue = sends(), slept = page('rec.slept'), rewound = page('rec.recorder.native.rewound'), flushed = page('rec.recorder.flushed');
          await page('VeulyMac.woke')(0.2);
          const after = sends().length;
          page('rec.recorder = null');
          const none = await page('VeulyMac.woke')(60);
          console.log(JSON.stringify({ woke, queue, slept: slept.map((n) => [n.session, n.seconds]), rewound, flushed, toasts, after, none }));
        })();""")
        self.assertEqual(out["woke"], {"recording": True, "reopened": True, "state": "running"})
        self.assertEqual(out["queue"], [{"on": False, "gap": 745}], "through the queue, after what was recorded before")
        self.assertEqual((out["rewound"], out["flushed"]), (1, 1), "this Mac's sound lined up again; what came before sent first")
        self.assertEqual(out["slept"], [["s", 745]])
        self.assertTrue(out["toasts"] and out["toasts"][0].startswith("Your Mac was asleep for 12 min ("), out["toasts"])
        self.assertEqual(out["after"], 1, "a blink of a sleep is not a gap")
        self.assertEqual(out["none"], {"recording": False})

    def test_a_wake_whose_audio_graph_does_not_resume_still_goes_on(self):
        """WebKit may leave a resume() that never settles: the gap is still queued, ahead of what a microphone opened
        again records, and woke answers."""
        out = self.node(r"""
        (async () => {
          page(`rec.session = 's'; rec.recorder = { kinds: ['mic'], flush() { calls.push(['FLUSH']); },
                  ctx: { state: 'interrupted', resume: () => new Promise(() => {}) },
                  streams: [{ native: false, getAudioTracks: () => [{ readyState: 'ended', muted: false }] }] };`);
          const t0 = Date.now(), woke = await page('VeulyMac.woke')(1800);
          console.log(JSON.stringify({ woke, ms: Date.now() - t0, order: calls.map(([m, item]) => (m === 'SEND' ? `gap ${item.gap}` : m)) }));
        })();""")
        self.assertEqual(out["woke"], {"recording": True, "reopened": True, "state": "interrupted"})
        self.assertLess(out["ms"], 4000)
        self.assertEqual(out["order"], ["FLUSH", "gap 1800", "REOPEN"])


if __name__ == "__main__":
    unittest.main()
