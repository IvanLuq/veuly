"""The app finds its tools and behaves as a Mac app: one finder per program, also under the short PATH an app opened
from Finder gets; the Mac's own converter when ffmpeg is missing; Setup asking Claude Code and the Keychain only what
it must; a Sign in that says whether Terminal opened; the port of the last run; veuly.log; the PDF browser kept away
from the login Keychain. Stand-ins only: no Claude usage, no `open`, no Keychain.

  python3 -m unittest discover tests
"""
import http.client
import io
import json
import os
import pathlib
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_core import FAKE_WHISPER, HOME, Client, FakeEngine  # noqa: E402  (sets TRANSCAPP_HOME and VEULY_SECRETS)
from transcapp import engine, keystore, library, paths, pdf, pipeline, platform_setup, server, transcribe  # noqa: E402
import app  # noqa: E402  (app.py, next to transcapp/)

FINDER_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"      # all an app opened from Finder or the Dock has on PATH
POSIX = os.name != "nt"

FAKE_CLAUDE = '''#!{python}
import json, sys
here = {here!r}
with open(here + "/calls.log", "a") as f:
    f.write(json.dumps(sys.argv) + "\\n")
args = sys.argv[1:]
if args == ["--version"]:
    print("9.9.9 (Claude Code)")
elif args[:2] == ["auth", "status"]:
    print(json.dumps({{"loggedIn": open(here + "/signed").read().strip() == "yes"}}))
elif "-p" in args:
    sys.stdin.read()
    print(json.dumps({{"result": "# Note\\n\\nWritten by the stand-in.", "is_error": False, "total_cost_usd": 0, "usage": {{}}}}))
'''


def fake_claude(folder: pathlib.Path) -> pathlib.Path:
    """A stand-in for Claude Code that writes down how it was started; `signed` says whether it is signed in."""
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / "claude"
    exe.write_text(FAKE_CLAUDE.format(python=sys.executable, here=str(folder)))
    exe.chmod(0o755)
    (folder / "signed").write_text("yes")
    return exe


def calls(exe: pathlib.Path) -> list:
    log = exe.parent / "calls.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def a_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve():
    httpd, a = server.serve(ROOT / "web", 0, engine_factory=lambda settings: FakeEngine())
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    c = Client(a)
    c.call("GET", f"/?t={a.token}")
    return httpd, a, c


@unittest.skipUnless(POSIX, "shell-style stand-ins")
class Finders(unittest.TestCase):
    def test_setup_and_every_engine_find_the_same_claude_with_finders_path(self):
        home = pathlib.Path(tempfile.mkdtemp(prefix="mactools-home-"))
        claude = fake_claude(home / ".local" / "bin")          # where Claude Code's own installer puts it
        with mock.patch.dict(os.environ, {"HOME": str(home), "PATH": FINDER_PATH}):
            self.assertIsNone(shutil.which("claude"), "the stand-in is not on this PATH")
            self.assertEqual(platform_setup.find_claude(), str(claude))
            note_engine = pipeline.default_engine({"engine": "claude-code", "claude_model": "sonnet"})
            self.assertEqual(note_engine.claude, str(claude), "the note engine, not only Setup")
            self.assertEqual(pipeline.Pipeline(library.Library()).find_claude(), str(claude), "the Obsidian agent")
            result = note_engine.final_note(engine.NoteRequest(course="Test", transcript="1\n00:00:00,000 --> 00:00:02,000\nHello.\n"))
        self.assertIn("Written by the stand-in", result.text)
        started = [argv for argv in calls(claude) if "-p" in argv]
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0][0], str(claude), "started by its absolute path")

    def test_ffmpeg_and_whisper_are_found_outside_finders_path(self):
        brew = pathlib.Path(tempfile.mkdtemp(prefix="mactools-brew-"))
        for name in ("ffmpeg", "whisper-cli"):
            (brew / name).write_text("#!/bin/sh\n")
            (brew / name).chmod(0o755)
        bundle = pathlib.Path(tempfile.mkdtemp(prefix="mactools-bundle-"))     # an installed copy with no bin/ of its own
        with mock.patch.dict(os.environ, {"PATH": FINDER_PATH}), mock.patch.object(paths, "resource_dir", lambda: bundle), \
                mock.patch.object(paths, "program_dirs", lambda: [bundle / "bin", brew]):
            self.assertEqual(transcribe.find_ffmpeg(), str(brew / "ffmpeg"))
            self.assertEqual(transcribe.find_whisper({}), str(brew / "whisper-cli"))
        home = pathlib.Path.home()
        for d in (home / ".local" / "bin", home / ".claude" / "local", pathlib.Path("/opt/homebrew/bin"),
                  pathlib.Path("/usr/local/bin"), pathlib.Path("/opt/local/bin"), paths.resource_dir() / "bin"):
            self.assertIn(d, paths.program_dirs())

    def test_path_is_extended_at_startup_on_a_mac_only(self):
        there = pathlib.Path(tempfile.mkdtemp(prefix="mactools-dir-"))
        dirs = [pathlib.Path("/usr/bin"), there, there / "missing"]
        with mock.patch.object(paths, "program_dirs", lambda: dirs):
            for system, want in (("Darwin", f"/usr/bin:/bin:{there}"), ("Linux", "/usr/bin:/bin")):
                with mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}), mock.patch.object(paths.platform, "system", lambda s=system: s):
                    paths.extend_path()
                    self.assertEqual(os.environ["PATH"], want, system)


class Converter(unittest.TestCase):
    def test_an_extensible_wav_is_copied_into_a_plain_one(self):
        """afconvert writes the "extensible" kind of WAV, often with a filler chunk; Python 3.9's wave refuses it."""
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="mactools-wav-"))
        samples = struct.pack("<16000h", *[(i * 37) % 2000 - 1000 for i in range(16000)])
        pcm = bytes.fromhex("0100000000001000800000aa00389b71")

        def extensible(channels):
            fmt = struct.pack("<HHIIHH", 0xFFFE, channels, 16000, 32000 * channels, 2 * channels, 16) + struct.pack("<HHI", 22, 16, 4) + pcm
            body = (b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"FLLR" + struct.pack("<I", 3) + b"\0\0\0\0"
                    + b"data" + struct.pack("<I", len(samples)) + samples)
            return b"RIFF" + struct.pack("<I", len(body)) + body

        (tmp / "af.wav").write_bytes(extensible(1))
        transcribe._plain_wav(tmp / "af.wav", tmp / "plain.wav")
        self.assertTrue(transcribe.is_whisper_ready_wav(tmp / "plain.wav"))
        with wave.open(str(tmp / "plain.wav")) as w:
            self.assertEqual(w.readframes(w.getnframes()), samples)
        (tmp / "stereo.wav").write_bytes(extensible(2))
        with self.assertRaises(transcribe.TranscribeError):
            transcribe._plain_wav(tmp / "stereo.wav", tmp / "nothing.wav")
        self.assertFalse((tmp / "nothing.wav").exists(), "nothing half-written is left")


@unittest.skipUnless(transcribe.find_afconvert(), "macOS's afconvert")
class MacConverter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        whisper = pathlib.Path(HOME) / "fake-whisper-mactools"
        whisper.write_text(FAKE_WHISPER)
        whisper.chmod(0o755)
        model = pathlib.Path(HOME) / "ggml-fake-mactools.bin"
        model.write_bytes(b"x")
        paths.save_settings({"engine": "api", "whisper_path": str(whisper), "whisper_model": str(model)})
        cls.httpd, cls.app, cls.c = serve()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_a_voice_memo_is_imported_without_ffmpeg(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="mactools-m4a-"))
        with wave.open(str(tmp / "cd.wav"), "wb") as w:          # 1.5 s at 44.1 kHz in stereo: not what whisper takes
            w.setnchannels(2), w.setsampwidth(2), w.setframerate(44100)
            w.writeframes(struct.pack(f"<{2 * 66150}h", *[((i // 2) * 50) % 3000 - 1500 for i in range(2 * 66150)]))
        memo = tmp / "Voice memo.m4a"
        subprocess.run([transcribe.AFCONVERT, "-f", "m4af", "-d", "aac", str(tmp / "cd.wav"), str(memo)], check=True, capture_output=True)
        with mock.patch.object(transcribe, "find_ffmpeg", lambda: None):
            transcribe.to_wav(memo, tmp / "out.wav")
            self.assertTrue(transcribe.is_whisper_ready_wav(tmp / "out.wav"))
            self.assertAlmostEqual(transcribe.wav_seconds(tmp / "out.wav"), 1.5, delta=0.1)
            _, course = self.c.call("POST", "/api/courses", {"name": "Mac import", "lang": "en"})
            slug = course["slug"]
            _, meta = self.c.call("POST", f"/api/courses/{slug}/classes", {})
            base = f"/api/classes/{slug}/{meta['id']}"
            status, data = self.c.call("POST", f"{base}/import", memo.read_bytes(), {"X-Filename": "Voice memo.m4a"}, raw=True)
            self.assertEqual(status, 200, data)
            self.assertAlmostEqual(data["duration_s"], 1.5, delta=0.1)
            for _ in range(100):
                done = self.c.call("GET", base)[1]
                if done["status"] in ("done", "error"):
                    break
                time.sleep(0.1)
            self.assertEqual(done["status"], "done", done.get("error"))
            self.assertIn("TCP slow start", done["transcript"])
            _, meta = self.c.call("POST", f"/api/courses/{slug}/classes", {})
            base = f"/api/classes/{slug}/{meta['id']}"
            status, err = self.c.call("POST", f"{base}/import", b"\x1aE\xdf\xa3 not a real webm", {"X-Filename": "call.webm"}, raw=True)
            self.assertEqual((status, err["kind"]), (409, "no_ffmpeg"), "what the Mac cannot read still needs ffmpeg")
            left = [f.name for f in self.app.lib.class_dir(slug, meta["id"]).iterdir() if f.name not in ("1-my-notes.md", "class.json", ".class.json.bak", "img")]
            self.assertEqual(left, [], "no recording or temporary file is left behind")
        with mock.patch.object(platform_setup, "find_claude", lambda: None):     # not this computer's Claude Code: its `auth status` reads the Keychain
            self.assertTrue(platform_setup.status({}, {"key": ""}).afconvert, "Setup knows the Mac converts by itself")


class SetupAsksOnlyWhatItMust(unittest.TestCase):
    @unittest.skipUnless(POSIX, "a shell-style stand-in for Claude Code")
    def test_opening_settings_does_not_start_claude_or_read_the_key_each_time(self):
        claude = fake_claude(pathlib.Path(tempfile.mkdtemp(prefix="mactools-claude-")))
        reads, real_get = [], keystore.get_key

        def counted():
            reads.append(1)
            return real_get()

        asked = lambda what: sum(1 for argv in calls(claude) if argv[1:3] == what)   # noqa: E731
        chose = paths.load_settings()["engine"]
        self.addCleanup(paths.save_settings, {"engine": chose})
        paths.save_settings({"engine": "claude-code"})        # Setup asks Claude Code only of someone who chose it
        httpd, a, c = serve()
        try:
            with mock.patch.object(platform_setup, "find_claude", lambda: str(claude)), mock.patch.object(keystore, "get_key", counted):
                first = c.call("GET", "/api/setup")[1]
                self.assertEqual((first["claude_path"], first["claude_version"], first["logged_in"]), (str(claude), "9.9.9 (Claude Code)", True))
                self.assertEqual((asked(["--version"]), asked(["auth", "status"]), len(reads)), (1, 1, 1))
                for _ in range(3):                                  # Settings opened again and again
                    c.call("GET", "/api/setup")
                self.assertEqual((asked(["--version"]), asked(["auth", "status"]), len(reads)), (1, 1, 1))
                c.call("GET", "/api/setup?refresh=1")             # Check again
                self.assertEqual((asked(["--version"]), asked(["auth", "status"]), len(reads)), (2, 2, 2))
                saved = c.call("POST", "/api/key", {"key": "sk-ant-" + "x" * 30 + "abcd"})[1]
                self.assertEqual((saved["api_key_hint"], asked(["auth", "status"]), len(reads)), ("…abcd", 2, 3))
                gone = c.call("DELETE", "/api/key")[1]
                self.assertEqual((gone["api_key_hint"], asked(["auth", "status"]), len(reads)), ("", 2, 4))

                (claude.parent / "signed").write_text("no")       # signed out in a terminal, then Check again
                self.assertIs(c.call("GET", "/api/setup?refresh=1")[1]["logged_in"], False)
                (claude.parent / "signed").write_text("yes")      # signed in: the next look sees it, without Check again
                self.assertIs(c.call("GET", "/api/setup")[1]["logged_in"], True)
                before = asked(["auth", "status"])
                c.call("GET", "/api/setup")
                self.assertEqual(asked(["auth", "status"]), before, "once signed in, it is not asked again")
                versions = asked(["--version"])
                claude.write_text(claude.read_text() + "\n# a new version\n")      # an update replaces the program
                c.call("GET", "/api/setup")
                self.assertEqual(asked(["--version"]), versions + 1)
        finally:
            httpd.shutdown()
            httpd.server_close()

    @unittest.skipUnless(POSIX, "a shell-style stand-in for Claude Code")
    def test_someone_who_did_not_choose_claude_code_starts_nothing(self):
        claude = fake_claude(pathlib.Path(tempfile.mkdtemp(prefix="mactools-claude-")))
        chose = paths.load_settings()["engine"]
        self.addCleanup(paths.save_settings, {"engine": chose})
        paths.save_settings({"engine": "api"})
        httpd, a, c = serve()
        try:
            with mock.patch.object(platform_setup, "find_claude", lambda: str(claude)):
                seen = c.call("GET", "/api/setup?refresh=1")[1]
            self.assertEqual((seen["claude_path"], seen["claude_version"], seen["logged_in"]), (str(claude), None, None))
            self.assertEqual(calls(claude), [], "found on disk, and never started")
        finally:
            httpd.shutdown()
            httpd.server_close()


@unittest.skipUnless(POSIX, "shell-style stand-ins")
class MacSignIn(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="mactools-login-"))
        self.claude = fake_claude(self.tmp / "My Apps")           # a space in the path: it must be quoted
        self.open = self.tmp / "open"

    def stand_in_open(self, fail: bool) -> None:
        """Plays /usr/bin/open: writes down what it was asked and, as Terminal does with a .command file, runs it."""
        refuse = 'echo "LSOpenURLsWithRole() failed with error -10810" >&2; exit 1' if fail else ""
        self.open.write_text(f'#!/bin/sh\necho "$@" >> "{self.tmp}/open.log"\n{refuse}\n[ "$1" = "-a" ] && [ "$2" = "Terminal" ] || exit 2\n/bin/sh "$3"\n')
        self.open.chmod(0o755)

    def run_login(self):
        started, real = [], subprocess.Popen

        def spy(cmd, *a, **kw):
            started.append(cmd)
            return real(cmd, *a, **kw)

        with mock.patch.object(platform_setup, "OPEN", str(self.open)), mock.patch.object(platform_setup, "find_claude", lambda: str(self.claude)), \
                mock.patch.object(platform_setup, "detect_os", lambda: {"os": "mac", "arch": "arm64", "version": ""}), \
                mock.patch.object(subprocess, "Popen", spy):
            result = platform_setup.open_login()
        self.assertFalse([c for c in started if "osascript" in str(c[0])], "no Apple Events: no osascript")
        return result

    def test_terminal_opens_the_sign_in_without_apple_events(self):
        self.stand_in_open(fail=False)
        ok, message = self.run_login()
        self.assertTrue(ok, message)
        asked = (self.tmp / "open.log").read_text().split()
        self.assertEqual(asked[:2], ["-a", "Terminal"])
        script = pathlib.Path(asked[2])
        self.assertTrue(script.name.endswith(".command"))
        self.assertFalse(script.exists(), "the file deletes itself once Terminal runs it")
        self.assertIn(["auth", "login"], [argv[1:] for argv in calls(self.claude)])

    def test_when_terminal_does_not_open_it_says_so_and_gives_the_command(self):
        self.stand_in_open(fail=True)
        ok, message = self.run_login()
        quoted = f"'{self.claude}' auth login"
        self.assertFalse(ok)
        self.assertIn("-10810", message)
        self.assertIn(quoted, message)
        self.assertFalse(pathlib.Path((self.tmp / "open.log").read_text().split()[2]).exists(), "nothing left behind")
        self.assertEqual(calls(self.claude), [])
        httpd, a, c = serve()
        try:
            with mock.patch.object(platform_setup, "OPEN", str(self.open)), mock.patch.object(platform_setup, "find_claude", lambda: str(self.claude)), \
                    mock.patch.object(platform_setup, "detect_os", lambda: {"os": "mac", "arch": "arm64", "version": ""}):
                status, answer = c.call("POST", "/api/setup/login")
        finally:
            httpd.shutdown()
            httpd.server_close()
        self.assertEqual((status, answer["ok"], answer["command"]), (200, False, quoted), "the page shows the line to type")


class PdfBrowser(unittest.TestCase):
    @unittest.skipUnless(POSIX, "a script stands in for the browser")
    def test_the_windowless_browser_stays_away_from_the_login_keychain(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="mactools-pdf-"))
        browser = tmp / "browser"
        browser.write_text("#!" + sys.executable + "\nimport sys\nopen(sys.argv[0] + '.args', 'w').write('\\n'.join(sys.argv[1:]))\n"
                           "out = [a.split('=', 1)[1] for a in sys.argv if a.startswith('--print-to-pdf=')][0]\n"
                           "open(out, 'wb').write(b'%PDF-1.4 stand-in' + b' ' * 200)\n")
        browser.chmod(0o755)
        with mock.patch.dict(os.environ, {"VEULY_BROWSER": str(browser)}):
            self.assertTrue(pdf.render("http://127.0.0.1:9/print/x").startswith(b"%PDF"))
        args = (tmp / "browser.args").read_text().splitlines()
        for flag in ("--headless", "--use-mock-keychain", "--password-store=basic"):
            self.assertIn(flag, args)


class Launcher(unittest.TestCase):
    def test_the_ping_goes_straight_to_this_computer(self):
        """With a proxy set (http_proxy, or a company's), asking "is it running?" through it failed: a second copy started."""
        httpd, a, c = serve()
        info = pathlib.Path(HOME) / "server.json"
        info.write_text(json.dumps({"port": a.port, "token": a.token}))
        dead = "http://127.0.0.1:9"                 # nothing listens there
        try:
            with mock.patch.dict(os.environ, {"http_proxy": dead, "HTTP_PROXY": dead, "no_proxy": "", "NO_PROXY": ""}):
                self.assertEqual(app.already_running(), f"http://127.0.0.1:{a.port}/?t={a.token}")
        finally:
            httpd.shutdown()
            httpd.server_close()
            info.unlink()

    def test_the_last_port_is_used_again_unless_something_has_it(self):
        port = a_free_port()
        make = lambda **kw: server.serve(ROOT / "web", port, engine_factory=lambda s: FakeEngine(), **kw)   # noqa: E731
        httpd, a = make(fallback=True)
        self.assertEqual(a.port, port)
        httpd.server_close()
        other = socket.socket()
        other.bind(("127.0.0.1", port))
        other.listen()
        try:
            self.assertTrue(server._in_use(port))
            httpd, a = make(fallback=True)
            self.assertNotIn(a.port, (0, port), "another program has it: any free port")
            httpd.server_close()
            with self.assertRaises(OSError):
                make()                                  # an explicit --port stays explicit
        finally:
            other.close()
        self.assertFalse(server._in_use(port))
        # Something listening on every address does not stop a 127.0.0.1 bind on a Mac: the check before binding does.
        with mock.patch.object(server, "_in_use", lambda p: True):
            httpd, a = make(fallback=True)
            self.assertNotEqual(a.port, port)
            httpd.server_close()

    def test_server_json_keeps_the_port_and_drops_the_token_at_quit(self):
        info = pathlib.Path(tempfile.mkdtemp(prefix="mactools-info-")) / "server.json"
        mine, other = mock.Mock(port=50123, token="mine"), mock.Mock(port=50124, token="other")
        info.write_text(json.dumps({"port": 50123, "token": "mine"}))
        app.keep_port(info, other)                      # a second copy quitting leaves the first one's file alone
        self.assertEqual(json.loads(info.read_text()), {"port": 50123, "token": "mine"})
        app.keep_port(info, mine)
        self.assertEqual(json.loads(info.read_text()), {"port": 50123})
        self.assertEqual(app.last_port(info), 50123)
        for bad in ('{"port": "x"}', '{"port": 80}', "[]", "not json"):
            info.write_text(bad)
            self.assertEqual(app.last_port(info), 0, bad)

    @unittest.skipUnless(POSIX, "file modes")
    def test_the_log_is_bounded_and_private(self):
        folder = pathlib.Path(tempfile.mkdtemp(prefix="mactools-log-"))
        with mock.patch.object(app, "LOG_LIMIT", 1000):
            log = app.LogFile(folder / "veuly.log")
            console = io.StringIO()
            out, quiet = app.Tee(log, console), app.Tee(log, None)
            for i in range(60):
                print(f"line {i:02d} " + "x" * 90, file=out if i % 2 else quiet)
        self.assertLess((folder / "veuly.log").stat().st_size, 1200)
        self.assertLess((folder / "veuly.log.1").stat().st_size, 1200)
        self.assertEqual(sorted(f.name for f in folder.iterdir()), ["veuly.log", "veuly.log.1"])
        self.assertIn("line 59", (folder / "veuly.log").read_text())
        self.assertEqual(console.getvalue().count("line"), 30, "a console still gets what it got before")
        self.assertEqual(oct((folder / "veuly.log").stat().st_mode & 0o777), oct(0o600))
        self.assertTrue(app.unread(None))
        with open(os.devnull, "w") as null, open(folder / "plain.txt", "w") as plain:
            self.assertTrue(app.unread(null), "a Mac app opened from Finder writes to /dev/null")
            self.assertFalse(app.unread(plain))

    @unittest.skipUnless(POSIX, "signals and file modes")
    def test_a_restart_keeps_the_address_and_a_second_launch_reuses_the_first(self):
        home = pathlib.Path(tempfile.mkdtemp(prefix="mactools-app-"))
        port = a_free_port()
        (home / "server.json").write_text(json.dumps({"port": port}))        # what the last run left
        env = {k: v for k, v in os.environ.items() if k not in ("TRANSCAPP_HOME",)}
        env.update(VEULY_HOME=str(home), VEULY_SECRETS="file", PYTHONDONTWRITEBYTECODE="1")
        start = lambda out, **kw: subprocess.Popen([sys.executable, "-B", str(ROOT / "app.py"), "--no-open"], env={**env, **kw},   # noqa: E731
                                                   stdout=out, stderr=subprocess.STDOUT, text=True)

        def quit_app(link):
            port_, token = link.split(":")[2].split("/")[0], link.split("?t=")[1]
            conn = http.client.HTTPConnection("127.0.0.1", int(port_), timeout=10)
            conn.request("GET", f"/?t={token}")
            cookie = conn.getresponse().getheader("Set-Cookie").split(";")[0]
            conn.close()
            conn = http.client.HTTPConnection("127.0.0.1", int(port_), timeout=10)
            conn.request("POST", "/api/quit", body=b"{}", headers={"Cookie": cookie, "X-TA": token})   # every route needs the session token
            conn.getresponse().read()
            conn.close()

        first = start(subprocess.PIPE)
        link = first.stdout.readline().strip().split("Open: ", 1)[1]
        self.assertTrue(link.startswith(f"http://127.0.0.1:{port}/?t="), link)
        quit_app(link)
        first.wait(timeout=15)
        first.stdout.close()
        self.assertEqual(json.loads((home / "server.json").read_text()), {"port": port}, "the port stays, the token goes")

        second = start(subprocess.DEVNULL)                   # as from Finder: what it prints reaches nobody
        for _ in range(100):
            info = json.loads((home / "server.json").read_text())
            if "token" in info:
                break
            time.sleep(0.1)
        self.assertEqual(info["port"], port, "the same address after a restart")
        dead = "http://127.0.0.1:9"
        third = start(subprocess.PIPE, http_proxy=dead, HTTP_PROXY=dead, no_proxy="", NO_PROXY="")
        said = third.communicate(timeout=15)[0]
        self.assertIn("Veuly is already running.", said)
        self.assertIn(f"Open: http://127.0.0.1:{port}/?t={info['token']}", said)
        quit_app(f"http://127.0.0.1:{port}/?t={info['token']}")
        second.wait(timeout=15)

        log = (home / "veuly.log").read_text()
        self.assertIn(f"Veuly {server.VERSION}", log)
        self.assertIn(f"Open: http://127.0.0.1:{port}/", log)
        for token in (link.split("?t=")[1], info["token"]):
            self.assertNotIn(token, log, "a session's token never goes into the log")
        self.assertEqual(oct((home / "veuly.log").stat().st_mode & 0o777), oct(0o600))
        self.assertEqual(log.count("--- "), 3 if sys.platform == "darwin" else 1, "on a Mac every start keeps the log")


if __name__ == "__main__":
    unittest.main()
