"""Claude accounts: which Claude account writes each course's notes and each project's minutes (transcapp/accounts.py).
Run with the others: python3 -m unittest discover tests
A data folder of its own (VEULY_HOME), keys in files (VEULY_SECRETS=file, set by test_core), and a stand-in `claude` that
writes down the argv and the login variables of every run: no real `claude auth login`, no ~/.claude, no Keychain, no
Claude usage. The stand-in Messages API is a server on this computer."""
import http.server
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_core import ROOT, Client  # noqa: E402  (sets TRANSCAPP_HOME and VEULY_SECRETS)
from transcapp import accounts, confluence, engine, keystore, obsidian, paths, pipeline, platform_setup, server  # noqa: E402

# The stand-in Claude Code. Signed in means a file `.signed` in its config folder (which only its own `auth login` makes),
# or no folder at all (this Mac's Claude Code). What it answers depends on what it was asked.
STAND_IN = """#!{python}
import json, os, sys
argv = sys.argv[1:]
stdin = "" if sys.stdin.isatty() else sys.stdin.read() if "-p" in argv else ""
folder = os.environ.get("CLAUDE_CONFIG_DIR")
with open(os.environ["ACCT_TEST_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({{"argv": argv, "config": folder, "secure": os.environ.get("CLAUDE_SECURESTORAGE_CONFIG_DIR"),
                          "key": os.environ.get("ANTHROPIC_API_KEY"), "token": os.environ.get("ANTHROPIC_AUTH_TOKEN"),
                          "stdin": stdin[:4000]}}) + "\\n")
signed = folder is None or os.path.exists(os.path.join(folder, ".signed"))
if argv[:2] == ["auth", "login"]:
    open(os.path.join(folder, ".signed"), "w").write("x") if folder else None
elif argv[:2] == ["auth", "status"]:
    print(json.dumps({{"loggedIn": True, "email": "ivan@company.example", "orgName": "Company"}} if folder and signed
                     else {{"loggedIn": True, "email": "ivan@home.example"}} if signed else {{"loggedIn": False}}))
elif argv[:2] == ["auth", "logout"]:
    if folder and os.path.exists(os.path.join(folder, ".signed")):
        os.unlink(os.path.join(folder, ".signed"))
elif argv[:2] == ["mcp", "list"]:
    print("claude.ai Atlassian: https://mcp.atlassian.com/v1/sse - \\u2713 Connected" if folder else "No MCP servers configured.")
elif "-p" in argv:
    if not signed:
        print(json.dumps({{"is_error": True, "result": "Not logged in \\u00b7 Please run /login"}}))
        sys.exit(1)
    if "--permission-mode" in argv:
        print(json.dumps({{"result": "Filed it under Topics.", "total_cost_usd": 0}}))
    elif any("mcp__" in a for a in argv):
        print(json.dumps({{"result": '{{"id": "77", "url": "https://acme.atlassian.net/wiki/pages/77"}}'}}))
    else:
        print(json.dumps({{"result": "# Attenuation and the migration\\n\\nIt is in decibels per kilometre [S1].", "stop_reason": "end_turn",
                          "total_cost_usd": 0.0, "usage": {{}}}}))
"""
NOTE_SCHOOL = "# Fibre\n\n## Attenuation\n\nAttenuation is measured in decibels per kilometre.\n"
SRT = "1\n00:00:00,000 --> 00:00:04,000\nToday: attenuation of the fibre and the migration plan.\n"
WORK_KEY = "sk-ant-api03-" + "w" * 30
SETUP_KEY = "sk-ant-api03-" + "s" * 30


class StandInApi(http.server.BaseHTTPRequestHandler):
    """The Messages API on this computer: writes down the key each request came with, answers a whole note."""
    keys: list = []
    models: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        type(self).keys.append(self.headers.get("x-api-key"))
        type(self).models.append(body.get("model"))
        events = [{"type": "message_start", "message": {"usage": {"input_tokens": 10}}},
                  {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "# Attenuation\n\nWritten through the API [S1]."}},
                  {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 5}}]
        self.send_response(200)
        self.end_headers()
        for e in events:
            self.wfile.write(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode())

    def log_message(self, *a):
        pass


class Accounts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = pathlib.Path(tempfile.mkdtemp(prefix="veuly-accounts-"))
        cls.saved = {k: os.environ.get(k) for k in ("VEULY_HOME", "ACCT_TEST_LOG", "CLAUDE_CONFIG_DIR", "CLAUDE_SECURESTORAGE_CONFIG_DIR",
                                                     "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
        os.environ["VEULY_HOME"] = str(cls.home)
        # What the app's own environment may hold: none of it may reach a `claude` it starts (accounts.UNSET)
        os.environ.update(CLAUDE_CONFIG_DIR=str(cls.home / "someone-elses"), CLAUDE_SECURESTORAGE_CONFIG_DIR=str(cls.home / "other-store"),
                          ANTHROPIC_API_KEY="sk-ant-from-the-environment", ANTHROPIC_AUTH_TOKEN="tok-from-the-environment")
        cls.log = cls.home / "claude-runs.jsonl"
        os.environ["ACCT_TEST_LOG"] = str(cls.log)
        cls.claude = cls.home / "bin" / "claude"
        cls.claude.parent.mkdir()
        cls.claude.write_text(STAND_IN.format(python=sys.executable))
        cls.claude.chmod(0o755)
        cls.real_find = platform_setup.find_claude
        platform_setup.find_claude = lambda: str(cls.claude)
        cls.api = http.server.HTTPServer(("127.0.0.1", 0), StandInApi)
        threading.Thread(target=cls.api.serve_forever, daemon=True).start()
        paths.save_settings({"engine": "claude-code", "auto_images": False, "setup_done": True,
                             "api_base_url": f"http://127.0.0.1:{cls.api.server_port}"})
        keystore.set_key(SETUP_KEY)
        cls.httpd, cls.app = server.serve(ROOT / "web", 0)        # the real engines: pipeline.default_engine
        cls.app.pipeline.find_claude = platform_setup.find_claude
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.c = Client(cls.app)
        cls.c.call("GET", f"/?t={cls.app.token}")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.api.shutdown()
        platform_setup.find_claude = cls.real_find
        for k, v in cls.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(cls.home, ignore_errors=True)

    # ---- helpers
    def runs(self) -> list:
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []

    def since(self, mark: int, what=None) -> list:
        got = self.runs()[mark:]
        return [r for r in got if what is None or what(r)]

    def mark(self) -> int:
        return len(self.runs())

    def course(self, name, kind="course"):
        status, data = self.c.call("POST", "/api/courses", {"name": name, "kind": kind})
        self.assertEqual(status, 200, data)
        return data["slug"]

    def write(self, slug, note=None):
        """A class with its transcript, written by the pipeline (the note through the course's account)."""
        _, meta = self.c.call("POST", f"/api/courses/{slug}/classes", {})
        cid = meta["id"]
        self.app.lib.write_text(slug, cid, "transcript", SRT)
        self.app.pipeline.start(slug, cid, "write")
        for _ in range(200):
            meta = self.app.lib.meta(slug, cid)
            if meta["status"] in ("done", "error"):
                return cid, meta
            time.sleep(0.05)
        self.fail("the class was never written")

    def add(self, name, kind="login", key=""):
        status, data = self.c.call("POST", "/api/accounts", {"name": name, "kind": kind, "key": key})
        self.assertEqual(status, 200, data)
        return data["added"]

    def sign_in(self, aid):
        """What the Sign in button does, through a stand-in for /usr/bin/open that runs the .command as Terminal would."""
        opener = self.home / "open"
        opener.write_text(f'#!/bin/sh\necho "$@" >> "{self.home}/open.log"\n/bin/sh "$3"\n')
        opener.chmod(0o755)
        with mock.patch.object(platform_setup, "OPEN", str(opener)), \
                mock.patch.object(platform_setup, "detect_os", lambda: {"os": "mac", "arch": "arm64", "version": ""}):
            return self.c.call("POST", f"/api/accounts/{aid}/login")

    def note_runs(self, mark):
        return self.since(mark, lambda r: "-p" in r["argv"] and "--system-prompt-file" in r["argv"])

    def assert_alone(self, run, folder):
        """A `claude` run with exactly this login folder (None: this Mac's own), and nothing else that picks a login or a key."""
        self.assertEqual(run["config"], folder, run["argv"])
        self.assertIsNone(run["secure"], "CLAUDE_SECURESTORAGE_CONFIG_DIR would pick another login's Keychain item")
        self.assertIsNone(run["key"], "an API key in the environment would be billed instead of the login")
        self.assertIsNone(run["token"])

    # ---- the tests
    def test_1_a_work_account_writes_the_minutes_and_school_stays_as_it_was(self):
        school, work = self.course("Optical networks"), self.course("Migration", "project")
        mark = self.mark()
        _, meta = self.write(school)
        self.assertEqual(meta["status"], "done", meta.get("error"))
        self.assertEqual(meta["account"], "default")
        [run] = self.note_runs(mark)
        self.assert_alone(run, None)
        self.assertIn("--no-session-persistence", run["argv"])

        aid = self.add("Work")
        folder = self.home / "claude-accounts" / aid
        self.assertTrue(folder.is_dir(), "a login account gets a config folder of its own, in the data folder")
        self.assertNotIn(str(self.app.lib.root), str(folder), "outside the library")
        self.assertEqual(list(folder.iterdir()), [], "the app puts nothing there: only Claude Code writes in it")
        saved = json.loads((self.home / "settings.json").read_text())
        self.assertEqual(saved["claude_accounts"], [{"id": aid, "name": "Work", "kind": "login"}], "id and name (and how it writes) only")
        self.assertEqual(self.c.call("POST", "/api/settings", {"work_account": aid})[1]["work_account"], aid)

        # not signed in yet: Settings says so, and the minutes fail naming the account (the recording stays)
        state = self.c.call("GET", "/api/accounts")[1]
        self.assertEqual([(a["name"], a["signed"]) for a in state["accounts"]], [("Work", False)])
        self.assertEqual((state["school"], state["work"]), ("default", aid))
        mark = self.mark()
        _, meta = self.write(work)
        self.assertEqual((meta["status"], meta["error_kind"], meta["account"]), ("error", "account_signed_out", aid))
        self.assertIn("Work account", meta["error"])
        self.assert_alone(self.note_runs(mark)[0], str(folder))

        # Sign in: Terminal runs `claude auth login` with the account's folder, in an environment that names no other login
        mark = self.mark()
        status, answer = self.sign_in(aid)
        self.assertEqual((status, answer["ok"]), (200, True), answer)
        [login] = self.since(mark, lambda r: r["argv"][:2] == ["auth", "login"])
        self.assert_alone(login, str(folder))
        state = self.c.call("GET", "/api/accounts")[1]
        self.assertEqual([(a["signed"], a["email"], a["org"]) for a in state["accounts"]], [(True, "ivan@company.example", "Company")])

        # the minutes, written again: the work folder; a school note still none
        mark = self.mark()
        _, meta = self.write(work)
        self.assertEqual(meta["status"], "done", meta.get("error"))
        _, meta = self.write(school)
        self.assertEqual(meta["status"], "done", meta.get("error"))
        minutes, note = self.note_runs(mark)
        self.assert_alone(minutes, str(folder))
        self.assert_alone(note, None)
        self.assertIn("--no-session-persistence", minutes["argv"], "no transcript kept in the account's folder")

        # Write it again goes the same way
        cid = self.app.lib.courses()[[c["slug"] for c in self.app.lib.courses()].index(work)]["classes"][0]["id"]
        mark = self.mark()
        self.assertEqual(self.c.call("POST", f"/api/classes/{work}/{cid}/retry", {"feedback": "Shorter."})[0], 200)
        for _ in range(200):
            if self.app.lib.meta(work, cid)["status"] == "done" and self.note_runs(mark):
                break
            time.sleep(0.05)
        self.assert_alone(self.note_runs(mark)[0], str(folder))
        self.__class__.work_id, self.__class__.school, self.__class__.work = aid, school, work

    def test_2_ask_practice_and_test_use_the_account_of_their_scope(self):
        aid, school, work = self.work_id, self.school, self.work
        folder = str(self.home / "claude-accounts" / aid)
        cases = [({"q": "When is the migration?", "course": work}, folder),
                 ({"q": "What is the attenuation?", "course": school}, None),
                 ({"q": "When is the migration?", "scope": "project"}, folder),
                 ({"q": "What is the attenuation?", "scope": "course"}, None),
                 ({"q": "attenuation migration", "scope": "all"}, None)]
        for body, want in cases:
            mark = self.mark()
            status, answer = self.c.call("POST", "/api/ask", body)
            self.assertEqual(status, 200, answer)
            for _ in range(100):
                if answer.get("answer") is not None or "error" in answer:
                    break
                time.sleep(0.05)
                answer = self.c.call("GET", f"/api/ask/{answer['id']}")[1]
            self.assertTrue(answer.get("answer"), (body, answer))
            [run] = self.note_runs(mark)
            self.assert_alone(run, want)

        mark = self.mark()
        status, job = self.c.call("POST", f"/api/courses/{school}/practice", {})
        self.assertEqual(status, 200, job)
        for _ in range(200):
            if (self.c.call("GET", f"/api/courses/{school}/practice")[1]["job"] or {}).get("state") != "writing":
                break
            time.sleep(0.05)
        [run] = self.note_runs(mark)
        self.assert_alone(run, None)
        self.c.call("PUT", f"/api/courses/{school}", {"claude_account": aid})       # this one course writes with Work
        mark = self.mark()
        self.c.call("POST", f"/api/courses/{school}/practice", {})
        for _ in range(200):
            if (self.c.call("GET", f"/api/courses/{school}/practice")[1]["job"] or {}).get("state") != "writing":
                break
            time.sleep(0.05)
        self.assert_alone(self.note_runs(mark)[0], folder)
        self.c.call("PUT", f"/api/courses/{school}", {"claude_account": ""})

        for which, want in ((aid, folder), ("default", None), (None, None)):
            mark = self.mark()
            status, answer = self.c.call("POST", "/api/setup/test-engine", {"account": which} if which else None)
            self.assertEqual(status, 200, answer)
            self.assert_alone(self.note_runs(mark)[0], want)
        self.assertEqual(self.c.call("POST", "/api/setup/test-engine", {"account": "zzzzzzzz"})[1]["kind"], "account_unknown")

    def test_3_the_connector_and_the_agent_run_in_the_work_account(self):
        aid, school, work = self.work_id, self.school, self.work
        folder = str(self.home / "claude-accounts" / aid)
        # "Use my Claude account" looks for the connector in the work side's account: there it is
        mark = self.mark()
        status, data = self.c.call("POST", "/api/confluence/connector")
        self.assertEqual(status, 200, data)
        self.assertEqual(paths.load_settings()["confluence_connector"], "claude.ai Atlassian")
        [listing] = self.since(mark, lambda r: r["argv"][:2] == ["mcp", "list"])
        self.assert_alone(listing, folder)
        self.assertEqual(self.c.call("POST", "/api/confluence/connector", {"account": "default"})[1]["kind"], "confluence_no_connector",
                         "this Mac's own Claude Code has none")
        self.assertEqual(self.c.call("POST", f"/api/courses/{work}/confluence", {"url": "https://acme.atlassian.net/wiki/spaces/ENG/folder/777"})[0], 200)
        cid = self.app.lib.course(work)["classes"][0]["id"]
        mark = self.mark()
        status, meta = self.c.call("POST", f"/api/classes/{work}/{cid}/publish")
        self.assertEqual((status, meta["confluence"]["id"]), (200, "77"), meta)
        [publish] = self.since(mark, lambda r: any("mcp__" in a for a in r["argv"]))
        self.assert_alone(publish, folder)
        self.assertIn("--no-session-persistence", publish["argv"])

        # the Obsidian agent of a project: the work account's Claude Code, in the vault
        vault = self.home / "Vault"
        (vault / "Work").mkdir(parents=True)
        (vault / "Work" / "CLAUDE.md").write_text("File the minutes.")
        self.assertEqual(self.c.call("POST", "/api/obsidian", {"path": str(vault)})[0], 200)
        for slug in (work, school):
            self.assertEqual(self.c.call("POST", f"/api/courses/{slug}/obsidian", {"folder": "Work/Veuly", "agent": True, "agent_dir": "Work"})[0], 200)
        for slug, want in ((work, folder), (school, None)):
            cid = self.app.lib.course(slug)["classes"][0]["id"]
            mark = self.mark()
            self.assertEqual(self.c.call("POST", f"/api/classes/{slug}/{cid}/obsidian")[0], 200)
            for _ in range(200):
                if (self.app.lib.meta(slug, cid).get("obsidian_agent") or {}).get("status") in ("done", "error"):
                    break
                time.sleep(0.05)
            self.assertEqual(self.app.lib.meta(slug, cid)["obsidian_agent"]["status"], "done", self.app.lib.meta(slug, cid)["obsidian_agent"])
            [agent] = self.since(mark, lambda r: "--permission-mode" in r["argv"])
            self.assert_alone(agent, want)

    def test_4_an_api_account_s_key_reaches_only_its_own_requests(self):
        aid, school, work = self.work_id, self.school, self.work
        self.assertEqual(self.c.call("POST", "/api/accounts", {"name": "Mine", "kind": "api", "key": "nope"})[1]["kind"], "key_shape")
        own = self.add("Mine", "api", WORK_KEY)
        self.assertNotIn(WORK_KEY, (self.home / "settings.json").read_text(), "the key lives in the key store only")
        self.assertEqual(keystore.get_secret(accounts.secret_name(own)), WORK_KEY)
        state = self.c.call("GET", "/api/accounts")[1]
        self.assertNotIn(WORK_KEY, json.dumps(state))
        self.assertEqual(next(a for a in state["accounts"] if a["id"] == own)["key_hint"], "…wwww")
        self.c.call("PUT", f"/api/courses/{school}", {"claude_account": own})
        StandInApi.keys.clear()
        mark = self.mark()
        _, meta = self.write(school)
        self.assertEqual((meta["status"], meta["account"]), ("done", own), meta.get("error"))
        self.assertEqual(StandInApi.keys, [WORK_KEY], "its own key, not Setup's")
        self.assertEqual(self.note_runs(mark), [], "no Claude Code run for it")
        _, meta = self.write(work)              # the work account's minutes: Claude Code with its folder, no key anywhere
        self.assertEqual(StandInApi.keys, [WORK_KEY])
        self.assert_alone(self.note_runs(mark)[0], str(self.home / "claude-accounts" / aid))
        # the default, when Setup chose the API: Setup's key
        eng = pipeline.default_engine(accounts.settings_for({**paths.load_settings(), "engine": "api"}))
        self.assertEqual(eng.api_key, SETUP_KEY)
        self.assertNotIsInstance(eng, pipeline.Named)
        # an API account has no Claude Code login: no connector, no agent
        cid = self.app.lib.course(school)["classes"][0]["id"]
        self.c.call("POST", f"/api/courses/{school}/confluence", {"url": "https://acme.atlassian.net/wiki/spaces/ENG/folder/777"})
        self.assertEqual(self.c.call("POST", f"/api/classes/{school}/{cid}/publish")[1]["kind"], "account_needs_login")
        self.c.call("POST", f"/api/classes/{school}/{cid}/obsidian")
        self.assertEqual(self.app.lib.meta(school, cid)["obsidian_agent"]["kind"], "account_needs_login")
        # renamed, its key replaced
        self.assertEqual(self.c.call("PUT", f"/api/accounts/{own}", {"name": "work"})[1]["kind"], "account_name", "two accounts, one name")
        self.assertEqual(self.c.call("PUT", f"/api/accounts/{own}", {"name": "  Personal  key "})[0], 200)
        self.assertEqual(accounts.get(paths.load_settings(), own)["name"], "Personal key")
        removed = self.c.call("DELETE", f"/api/accounts/{own}")[1]
        self.assertEqual(removed["moved"], [school], "the course that chose it goes back to its side's choice")
        self.assertIsNone(keystore.get_secret(accounts.secret_name(own)))
        self.assertNotIn("claude_account", json.loads((self.app.lib.root / school / "course.json").read_text()))

    def test_5_a_removed_or_unknown_account_falls_back(self):
        aid, work = self.work_id, self.work
        folder = self.home / "claude-accounts" / aid
        # an id no longer listed in a course.json (written by hand, or by an older copy): the side's choice, no error
        info = json.loads((self.app.lib.root / work / "course.json").read_text())
        (self.app.lib.root / work / "course.json").write_text(json.dumps({**info, "claude_account": "0000aaaa"}))
        mark = self.mark()
        _, meta = self.write(work)
        self.assertEqual((meta["status"], meta["account"]), ("done", aid))
        self.assert_alone(self.note_runs(mark)[0], str(folder))
        self.assertEqual(self.c.call("PUT", f"/api/courses/{work}", {"claude_account": "0000aaaa"})[1]["kind"], "account_unknown")
        self.assertEqual(self.c.call("POST", "/api/settings", {"school_account": "0000aaaa"})[1]["kind"], "account_unknown")
        self.c.call("POST", "/api/settings", {"claude_accounts": [{"id": "0000bbbb", "name": "Sneaky", "kind": "login"}]})
        self.assertEqual([a["id"] for a in accounts.listed(paths.load_settings())], [aid], "accounts come only through /api/accounts")
        self.assertEqual(self.c.call("PUT", f"/api/courses/{work}", {"claude_account": aid})[0], 200)
        self.assertEqual(self.c.call("DELETE", "/api/accounts/zzzzzzzz")[1]["kind"], "account_unknown")

        # removed, signed out first: its own `claude auth logout`, its folder gone, the side and the course back to the default
        mark = self.mark()
        removed = self.c.call("DELETE", f"/api/accounts/{aid}?signout=1")[1]
        self.assertEqual((removed["moved"], removed["signed_out"]), ([work], True))
        [logout] = self.since(mark, lambda r: r["argv"][:2] == ["auth", "logout"])
        self.assert_alone(logout, str(folder))
        self.assertFalse(folder.exists())
        settings = paths.load_settings()
        self.assertEqual((settings["work_account"], settings["claude_accounts"]), ("default", []))
        mark = self.mark()
        _, meta = self.write(work)
        self.assertEqual((meta["status"], meta["account"]), ("done", "default"))
        self.assert_alone(self.note_runs(mark)[0], None)


    def test_6_a_signed_out_account_or_one_with_a_key_and_confluence(self):
        """Review: a signed-out work account says so for "Use my Claude account" and for a publish through the connector
        (not "Confluence did not accept the token"); a work side with an API key looks for the connector in this Mac's
        Claude Code; an API account whose key is gone never falls back on an ANTHROPIC_API_KEY of the environment."""
        work = self.work
        office = self.add("Office")                       # a sign-in of its own, never signed in
        self.c.call("POST", "/api/settings", {"work_account": office})
        mark = self.mark()
        status, data = self.c.call("POST", "/api/confluence/connector")
        self.assertEqual((status, data["kind"]), (409, "account_signed_out"), data)
        self.assertEqual(self.since(mark, lambda r: r["argv"][:2] == ["mcp", "list"]), [], "nothing to list before it signs in")
        self.assertEqual(paths.load_settings()["confluence_method"], "connector", "the connection made before stays")
        cid = self.app.lib.course(work)["classes"][0]["id"]
        mark = self.mark()
        status, data = self.c.call("POST", f"/api/classes/{work}/{cid}/publish")
        self.assertEqual((status, data["kind"]), (409, "account_signed_out"), data)
        self.assertIn("Office account", data["error"])
        [publish] = self.since(mark, lambda r: any("mcp__" in a for a in r["argv"]))
        self.assert_alone(publish, str(self.home / "claude-accounts" / office))

        key_acct = self.add("Keyed", "api", WORK_KEY)
        self.c.call("POST", "/api/settings", {"work_account": key_acct})
        mark = self.mark()
        status, data = self.c.call("POST", "/api/confluence/connector")
        self.assertEqual(data["kind"], "confluence_no_connector", "looked for in this Mac's Claude Code, which has none")
        [listing] = self.since(mark, lambda r: r["argv"][:2] == ["mcp", "list"])
        self.assert_alone(listing, None)
        self.assertEqual(self.c.call("POST", "/api/confluence/connector", {"account": key_acct})[1]["kind"], "account_needs_login")

        keystore.delete_secret(accounts.secret_name(key_acct))     # its key gone (the store was emptied, say)
        self.assertEqual(os.environ.get("ANTHROPIC_API_KEY"), "sk-ant-from-the-environment")
        StandInApi.keys.clear()
        _, meta = self.write(work)
        self.assertEqual((meta["status"], meta["error_kind"]), ("error", "bad_key"), meta.get("error"))
        self.assertIn("Keyed account", meta["error"])
        self.assertEqual(StandInApi.keys, [], "no request with the environment's key")
        for aid in (office, key_acct):
            self.c.call("DELETE", f"/api/accounts/{aid}")
        self.c.call("POST", "/api/confluence/connector", {"account": "default"})

    def test_8_each_account_writes_with_its_own_model(self):
        """The account's model reaches the real engines, its choice is kept and can be undone, and nobody else's changes."""
        login, other, key = self.add("Own model"), self.add("Setup's model"), self.add("Own key", "api", WORK_KEY)
        saved = paths.load_settings()
        self.assertEqual(saved["claude_model"], "sonnet")
        answer = self.c.call("PUT", f"/api/accounts/{login}", {"model": "opus"})[1]
        self.assertEqual(next(a for a in answer["accounts"] if a["id"] == login)["model"], "opus")
        self.assertEqual(self.c.call("PUT", f"/api/accounts/{key}", {"model": "claude-haiku-5-5"})[0], 200)
        by_id = {a["id"]: a for a in paths.load_settings()["claude_accounts"]}
        self.assertEqual((by_id[login]["model"], by_id[key]["model"]), ("opus", "claude-haiku-5-5"))
        self.assertNotIn("model", by_id[other], "an account that chose none says nothing")
        self.assertEqual((by_id[login]["name"], by_id[login]["kind"]), ("Own model", "login"), "the rest of the entry stays")
        self.c.call("PUT", f"/api/accounts/{login}", {"name": "Renamed"})
        self.assertEqual(accounts.get(paths.load_settings(), login)["model"], "opus", "renaming keeps it")
        self.assertEqual(paths.load_settings()["claude_model"], "sonnet", "Setup's choice does not change")

        for bad in ("--dangerously-skip-permissions", "two words", 5, None):
            status, data = self.c.call("PUT", f"/api/accounts/{login}", {"model": bad})
            self.assertEqual((status, data["kind"]), (400, "model_name"), bad)
        self.assertEqual(accounts.get(paths.load_settings(), login)["model"], "opus", "a refusal changes nothing")

        def models(course):
            mark = self.mark()
            self.assertEqual(self.write(course)[1]["status"], "done")
            argv = self.note_runs(mark)[0]["argv"]
            return argv[argv.index("--model") + 1]
        chosen, plain = self.course("Own model course", "project"), self.course("Plain course", "project")
        for aid in (login, other):
            self.assertTrue(self.sign_in(aid)[1]["ok"])
        self.c.call("PUT", f"/api/courses/{chosen}", {"claude_account": login})
        self.c.call("PUT", f"/api/courses/{plain}", {"claude_account": other})
        self.assertEqual((models(chosen), models(plain)), ("opus", "sonnet"), "its own, and Setup's for the one that chose none")

        self.c.call("PUT", f"/api/courses/{chosen}", {"claude_account": key})
        StandInApi.models.clear()
        self.assertEqual(self.write(chosen)[1]["status"], "done")
        self.assertEqual(StandInApi.models, ["claude-haiku-5-5"], "an API account's model goes in its request")

        self.c.call("PUT", f"/api/courses/{chosen}", {"claude_account": login})
        self.assertEqual(self.c.call("PUT", f"/api/accounts/{login}", {"model": ""})[0], 200)
        self.assertNotIn("model", accounts.get(paths.load_settings(), login), "back to Setup's")
        self.assertEqual(models(chosen), "sonnet")
        self.assertEqual(self.c.call("PUT", "/api/accounts/default", {"model": "opus"})[0], 404,
                         "this Mac's Claude Code uses Setup's choice, on Setup's page")

    def test_7_auth_status_as_claude_code_prints_it(self):
        """The keys of `claude auth status` as Claude Code 2.1.316 prints them (read in its own code; the signed-out answer
        captured with CLAUDE_CONFIG_DIR an empty folder): email and orgName, null when it has none."""
        signed_in = {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "analyticsDisabled": False,
                     "projectsDirectory": "/x/projects", "configDirectory": "/x", "email": "ivan@company.example",
                     "orgId": "0f0e", "orgName": "Company", "subscriptionType": "team"}
        signed_out = {"loggedIn": False, "authMethod": "none", "apiProvider": "firstParty", "analyticsDisabled": False,
                      "projectsDirectory": "/x/projects", "configDirectory": "/x"}
        console = {**signed_in, "authMethod": "api_key", "email": None, "orgName": None}
        for answer, code, want in ((signed_in, 0, {"signed": True, "email": "ivan@company.example", "org": "Company"}),
                                   (signed_out, 1, {"signed": False, "email": "", "org": ""}),
                                   (console, 0, {"signed": True, "email": "", "org": ""})):
            with mock.patch.object(platform_setup, "_run", lambda cmd, timeout=20, env=None: (code, json.dumps(answer, indent=2))):
                self.assertEqual(platform_setup.auth_state("/bin/claude", "/x"), want)


class Rules(unittest.TestCase):
    """What decides, in one place, with no server."""

    def test_a_model_in_settings_json_is_checked_too(self):
        """settings.json is the user's file, and the model reaches a command line."""
        s = {"claude_model": "sonnet", "api_model": "claude-sonnet-5-5", "engine": "claude-code",
             "claude_accounts": [{"id": "aaaa1111", "name": "A", "kind": "login", "model": "--evil"},
                                 {"id": "bbbb2222", "name": "B", "kind": "login", "model": "opus"},
                                 {"id": "cccc3333", "name": "C", "kind": "api", "model": "claude-opus-5-5"}]}
        self.assertEqual(accounts.settings_for(s, account="aaaa1111")["claude_model"], "sonnet")
        self.assertEqual(accounts.settings_for(s, account="bbbb2222")["claude_model"], "opus")
        got = accounts.settings_for(s, account="cccc3333")
        self.assertEqual((got["api_model"], got["claude_model"]), ("claude-opus-5-5", "sonnet"))
        self.assertEqual(accounts.settings_for(s)["claude_model"], "sonnet", "this Mac's: Setup's")

    def test_resolve(self):
        s = {"claude_accounts": [{"id": "aaaa1111", "name": "Work", "kind": "login"}, {"id": "bbbb2222", "name": "Key", "kind": "api"},
                                 {"id": "../x", "name": "Bad", "kind": "login"}, {"id": "cccc3333", "name": "", "kind": "login"}, "junk"],
             "school_account": "default", "work_account": "aaaa1111", "engine": "", "claude_model": "sonnet"}
        self.assertEqual([a["id"] for a in accounts.listed(s)], ["aaaa1111", "bbbb2222"], "a damaged entry is left out")
        r = accounts.resolve
        self.assertEqual(r(s)["id"], "default")
        self.assertEqual(r(s, side="project")["id"], "aaaa1111")
        self.assertEqual(r(s, side="course")["id"], "default")
        self.assertEqual(r(s, {"kind": "project"})["id"], "aaaa1111")
        self.assertEqual(r(s, {"kind": "project", "claude_account": "default"})["id"], "default")
        self.assertEqual(r(s, {"kind": "course", "claude_account": "bbbb2222"})["id"], "bbbb2222")
        self.assertEqual(r(s, {"kind": "project", "claude_account": "dddd4444"})["id"], "aaaa1111", "unknown: its side's")
        self.assertEqual(r({**s, "work_account": "dddd4444"}, {"kind": "project"})["id"], "default", "a side's gone: the default")
        with self.assertRaises(accounts.AccountError):
            r(s, account="dddd4444")
        # how each one writes: the licence mode and the engine follow it; the default keeps Setup's choice
        self.assertEqual(accounts.settings_for(s, {"kind": "project"})["engine"], "claude-code")
        self.assertEqual(accounts.settings_for(s, account="bbbb2222")["engine"], "api")
        self.assertEqual(accounts.settings_for(s)["engine"], "")
        eng = pipeline.default_engine(accounts.settings_for(s, {"kind": "project"}))
        self.assertIsInstance(eng, pipeline.Named)
        self.assertEqual(eng.inner.config_dir, str(paths.data_dir() / "claude-accounts" / "aaaa1111"))
        with self.assertRaises(engine.EngineError) as ctx:
            pipeline.default_engine(accounts.settings_for(s))
        self.assertEqual(ctx.exception.kind, "not_configured", "nothing changes for the default")

    def test_child_env(self):
        base = {"PATH": "/usr/bin", "HOME": "/h", "ANTHROPIC_API_KEY": "k", "ANTHROPIC_AUTH_TOKEN": "t",
                "CLAUDE_CONFIG_DIR": "/other", "CLAUDE_SECURESTORAGE_CONFIG_DIR": "/store"}
        self.assertEqual(accounts.child_env(None, base), {"PATH": "/usr/bin", "HOME": "/h"})
        self.assertEqual(accounts.child_env("/mine", base), {"PATH": "/usr/bin", "HOME": "/h", "CLAUDE_CONFIG_DIR": "/mine"})
        self.assertEqual(platform_setup.login_command("/a b/claude", "/x y/acct"), "CLAUDE_CONFIG_DIR='/x y/acct' '/a b/claude' auth login")
        self.assertEqual(platform_setup.login_command("/bin/claude"), "/bin/claude auth login", "the default's line, as before")

    def test_every_claude_goes_through_an_account(self):
        """Every place that starts Claude takes its account from accounts.py: no engine made from the bare settings, no
        `claude` run with an environment of its own making."""
        src = {f.name: f.read_text(encoding="utf-8") for f in (ROOT / "transcapp").glob("*.py")}
        for name, text in src.items():
            for line in text.splitlines():
                if "engine_factory(" in line and "def " not in line and "lambda settings" not in line:
                    self.assertNotIn("load_settings()", line, f"{name}: an engine made without its account: {line.strip()}")
                self.assertNotRegex(line, r"os\.environ\.items\(\) if k not in", f"{name}: a child environment not from accounts.child_env")
        for name in ("engine.py", "confluence.py", "obsidian.py", "platform_setup.py"):
            self.assertIn("accounts.child_env(", src[name], name)
        self.assertEqual(src["pipeline.py"].count("self.engine_factory("), 1)
        self.assertIn("accounts.settings_for(", src["pipeline.py"])
        for route in ("h_ask", "h_practice_start", "h_test_engine", "h_catchup"):
            body = re.search(rf"def {route}\(.*?(?=\n    def )", src["server.py"], re.S).group(0)
            self.assertIn("accounts.settings_for(", body, route)
        # A heads-up during a meeting: its routes make no engine, and its one engine comes from the project's account
        for route in ("h_headsup", "h_headsup_change"):
            body = re.search(rf"def {route}\(.*?(?=\n    def |\n    @)", src["server.py"], re.S).group(0)
            self.assertNotIn("engine_factory(", body, route)
        self.assertEqual(src["headsup.py"].count("engine_factory("), 1)
        self.assertIn("self.pipeline.engine_factory(use)", src["headsup.py"])
        self.assertIn("use = engine_settings(settings, info)", src["headsup.py"])
        self.assertIn("accounts.settings_for(settings, course=course)", src["headsup.py"])

    def test_kinds_have_words(self):
        kinds = set(re.findall(r'AccountError\("([a-z_]+)"', (ROOT / "transcapp" / "accounts.py").read_text())) | {"account_needs_login", "account_signed_out"}
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        listed = set(re.findall(r"'([a-z_]+)'", re.search(r"const HELP_KINDS = \[(.*?)\];", app_js, re.S).group(1)))
        self.assertEqual(kinds - listed, set())
        text = (ROOT / "web" / "i18n.js").read_text(encoding="utf-8")
        cut = [text.index(f"    {lang}: {{") for lang in ("en", "es", "ca")] + [len(text)]
        for i, lang in enumerate(("en", "es", "ca")):
            block = text[cut[i]:cut[i + 1]]
            for kind in kinds:
                self.assertIn(f"h_{kind}:", block, lang)
            self.assertIn("h_account_needs_login__work:", block, lang)
        page = (ROOT / "web" / "accounts.js").read_text(encoding="utf-8")
        self.assertIn('<script src="/accounts.js"></script>', (ROOT / "web" / "index.html").read_text())
        for hook in ("SETTINGS_PARTS.push(", "COURSE_SETTINGS_PARTS.push(", "CLASS_NOTICES.push("):
            self.assertIn(hook, page)


if __name__ == "__main__":
    unittest.main()
