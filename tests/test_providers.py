"""Writing with more than Claude: OpenAIEngine against a stand-in server on this computer, CodexEngine against a stand-in `codex`,
the accounts of those kinds, Setup asking nothing of a program nobody chose, what only Claude Code can do, the size of a
request, the language line and the bytes of what Claude is sent.
Run with the others: python3 -m unittest discover tests
No real AI call of any kind: no network but 127.0.0.1, no real `codex` or `claude`, no ~/.codex or ~/.claude, no Keychain."""
import hashlib
import http.server
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_core import ROOT, Client, FakeEngine  # noqa: E402  (sets TRANSCAPP_HOME and VEULY_SECRETS)
from transcapp import accounts, confluence, engine, headsup, keystore, obsidian, paths, pipeline, platform_setup, prefs, server  # noqa: E402
from transcapp.engine import CODEX_SAFE, CodexEngine, EngineError, NoteRequest, OpenAIEngine  # noqa: E402

KEY = "sk-proj-" + "k" * 30
SRT = "1\n00:00:00,000 --> 00:00:04,000\nToday: attenuation of the fibre.\n"
REQ = dict(course="Fibre", transcript=SRT, language="en")


# A refusal written as text, in real wordings, and what must not be taken for one (engine._declined, a heuristic).
DECLINED = ("I can't help with that.", "I cannot write this note, because the transcript is about something I may not discuss.",
            "I'm sorry, but I can't assist with this request.", "I\u2019m sorry, but I can\u2019t assist with this request.", "Sorry, I can't do that.",
            "I am unable to create this note.", "**I won't write this.**",
            "Lo siento, pero no puedo ayudar con esa solicitud.", "No puedo escribir este apunte.", "Lamentablemente, no puedo hacerlo.",
            "Ho sento, per\u00f2 no puc ajudar-te amb aix\u00f2.", "No puc escriure aquest apunt.", "Em sap greu, per\u00f2 no ho puc fer.")
HONEST = ("Short meeting: Ana cannot attend on Friday. Nothing else was decided.",
          "Reuni\u00f3n breve. Ana no puede venir el viernes y no se decidi\u00f3 nada m\u00e1s.",
          "Reuni\u00f3 breu. L'Anna no pot venir divendres i no es va decidir res m\u00e9s.",
          "# Lo siento\n\nA song the teacher played; the class talked about its chords.",
          "I'm sorry to say the fibre attenuates. " * 20)


def chunk(text=None, finish=None, **more):
    delta = {} if text is None else {"content": text}
    return {"choices": [{"delta": {**delta, **more}, "finish_reason": finish}], "model": "served-model"}


WHOLE = [chunk("# Fibre\n\n"), chunk("It attenuates."), chunk(finish="stop"), {"usage": {"prompt_tokens": 90000, "completion_tokens": 3}, "choices": []}]


class Calls(Client):
    def get(self, path):
        return self.call("GET", path)

    def post(self, path, body=None):
        return self.call("POST", path, body if body is not None else {})

    def put(self, path, body):
        return self.call("PUT", path, body)

    def delete(self, path):
        return self.call("DELETE", path)


class Service(http.server.BaseHTTPRequestHandler):
    """A stand-in for OpenAI's chat completions. `script` is what the next requests get: a list of events to stream, an int (that status,
    with `detail`), or a tuple (status, detail, headers)."""
    script: list = []
    seen: list = []
    elsewhere: list = []      # what reached the place a redirect points to

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        if self.path.startswith("/elsewhere"):
            type(self).elsewhere.append((self.headers.get("authorization"), body))
            self.send_response(200)
            self.end_headers()
            return
        type(self).seen.append({"path": self.path, "auth": self.headers.get("authorization"), "body": body})
        step = type(self).script.pop(0) if type(self).script else WHOLE
        if isinstance(step, tuple):
            code, detail, headers = step
            self.send_response(code)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("content-length", str(len(detail)))
            self.end_headers()
            self.wfile.write(detail.encode())
            return
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for event in step:
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode() if isinstance(event, dict) else event.encode())
        if step and isinstance(step[-1], dict):
            self.wfile.write(b"data: [DONE]\n\n")

    def do_GET(self):       # urllib turns a POST redirected by 301, 302 or 303 into a GET, with the same headers
        type(self).elsewhere.append((self.headers.get("x-api-key") or self.headers.get("authorization"), None))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


class OpenAI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Service)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        Service.script, Service.seen, Service.elsewhere = [], [], []

    def eng(self, **kw):
        e = OpenAIEngine(api_key=kw.pop("key", KEY), model=kw.pop("model", "m1"), base_url=kw.pop("base", self.base), service=kw.pop("service", "Gateway"), **kw)
        e.sleep = lambda s: None
        return e

    def test_a_whole_answer_comes_from_the_stream(self):
        result = self.eng().final_note(NoteRequest(**REQ))
        self.assertEqual(result.text, "# Fibre\n\nIt attenuates.")
        self.assertEqual((result.engine, result.model), ("openai", "served-model"))
        self.assertEqual(result.usage["completion_tokens"], 3)
        seen = Service.seen[0]
        self.assertEqual(seen["path"], "/v1/chat/completions")
        self.assertEqual(seen["auth"], f"Bearer {KEY}")
        body = seen["body"]
        self.assertIs(body["stream"], True)
        self.assertEqual(body["messages"][0], {"role": "system", "content": engine.SYSTEM})
        self.assertIn("<transcript>", body["messages"][1]["content"], "plain text when there are no pictures")
        self.assertEqual(body["max_tokens"], engine.MAX_OUTPUT_TOKENS, "max_tokens away from api.openai.com")
        self.assertNotIn("max_completion_tokens", body)

    def test_the_field_of_the_limit_on_api_openai_com(self):
        e = self.eng()
        e.base_url = engine.OPENAI_URL          # the first request would go out: catch it before it does
        sent = []

        def stream(data):
            sent.append(json.loads(data))
            return "ok", {}, ""
        e._stream = stream
        e.complete("s", "p")
        self.assertIn("max_completion_tokens", sent[0])
        self.assertNotIn("max_tokens", sent[0])

    def test_pictures_go_as_data_urls_each_after_its_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            pic = pathlib.Path(tmp) / "a.png"
            pic.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 30)
            self.eng().final_note(NoteRequest(**REQ, images=[("img/a.png", pic)]))
        content = Service.seen[0]["body"]["messages"][1]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "img/a.png:"})
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertIn("The images above are from the notes", content[2]["text"])

    def test_429_then_success_shares_the_waits(self):
        waited = []
        Service.script = [(429, '{"error": {"message": "slow down"}}', {"retry-after": "3"}), WHOLE]
        e = self.eng()
        e.sleep = waited.append
        self.assertIn("It attenuates", e.final_note(NoteRequest(**REQ)).text)
        self.assertEqual(waited, [3.0])
        self.assertEqual(e.waits, engine.WAITS)

    def test_429_for_ever_is_limit_and_a_used_up_quota_is_not_waited_for(self):
        Service.script = [(429, "busy", {})] * 4
        with self.assertRaises(EngineError) as e:
            self.eng().complete("s", "p")
        self.assertEqual(e.exception.kind, "limit")
        self.assertEqual(len(Service.seen), 4)
        Service.seen.clear()
        Service.script = [(429, '{"error": {"code": "insufficient_quota"}}', {})]
        with self.assertRaises(EngineError) as e:
            self.eng().complete("s", "p")
        self.assertEqual((e.exception.kind, len(Service.seen)), ("limit", 1))

    def test_what_is_not_a_whole_answer_is_never_a_note(self):
        for script, kind in (([chunk("half"), chunk(finish="length")], "cut_off"),
                             ([chunk("nope"), chunk(finish="content_filter")], "refused"),
                             ([chunk(refusal="I cannot"), chunk(finish="stop")], "refused"),
                             ([chunk(finish="stop")], "failed"),
                             ([chunk("   "), chunk(finish="stop")], "failed")):
            Service.script = [script]
            with self.assertRaises(EngineError) as e:
                self.eng().complete("s", "p")
            self.assertEqual(e.exception.kind, kind, script)

    def test_a_connection_that_closes_half_way_is_tried_again(self):
        Service.script = [[chunk("half"), "\n"], WHOLE]
        self.assertIn("It attenuates", self.eng().complete("s", "p").text)
        self.assertEqual(len(Service.seen), 2)

    def test_a_400_naming_the_token_field_gets_one_try_with_the_other(self):
        Service.script = [(400, "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead.", {}), WHOLE]
        self.eng().complete("s", "p")
        self.assertIn("max_tokens", Service.seen[0]["body"])
        self.assertIn("max_completion_tokens", Service.seen[1]["body"])
        self.assertNotIn("max_tokens", Service.seen[1]["body"])

    def test_then_one_with_no_limit_and_then_it_stops(self):
        said = "max_tokens must be at most 4096"
        Service.script = [(400, said, {}), (400, said, {}), (400, said, {}), WHOLE]
        with self.assertRaises(EngineError) as e:
            self.eng().complete("s", "p")
        self.assertEqual(e.exception.kind, "failed")
        self.assertEqual(len(Service.seen), 3)
        self.assertFalse({"max_tokens", "max_completion_tokens"} & set(Service.seen[2]["body"]))

    def test_a_400_about_stream_options_gets_one_try_without(self):
        Service.script = [(400, "Unknown field stream_options", {}), WHOLE]
        self.eng().complete("s", "p")
        self.assertIn("stream_options", Service.seen[0]["body"])
        self.assertNotIn("stream_options", Service.seen[1]["body"])

    def test_a_redirect_gets_neither_the_key_nor_the_text(self):
        Service.script = [(307, "", {"location": f"{self.base}/elsewhere/v1/chat/completions"})]
        with self.assertRaises(EngineError) as e:
            self.eng().complete("secret system", "secret text")
        self.assertEqual(e.exception.kind, "service_redirect")
        self.assertEqual(Service.elsewhere, [], "nothing reached the other address")
        self.assertEqual(len(Service.seen), 1)
        self.assertNotIn(KEY, str(e.exception))
        Service.script = [(302, "", {"location": f"{self.base}/elsewhere"})]
        with self.assertRaises(EngineError):
            self.eng().complete("s", "p")
        self.assertEqual(Service.elsewhere, [])

    def test_kinds_of_failure(self):
        for step, kind, tries in (((401, "bad key", {}), "bad_key", 1), ((403, "no", {}), "bad_key", 1),
                                  ((400, '{"error": {"code": "context_length_exceeded"}}', {}), "too_long", 1),
                                  ((413, "too big", {}), "too_long", 1),
                                  ((404, '{"error": {"message": "model \'x\' not found"}}', {}), "service_model", 1),
                                  ((404, "no such page", {}), "service_address", 1),
                                  ((400, "something else", {}), "failed", 1)):
            Service.seen.clear()
            Service.script = [step]
            with self.assertRaises(EngineError) as e:
                self.eng().complete("s", "p")
            self.assertEqual((e.exception.kind, len(Service.seen)), (kind, tries), step)
        Service.script = [[{"error": {"message": "the model behind it failed", "code": "weird"}}]]
        with self.assertRaises(EngineError) as e:
            self.eng().complete("s", "p")
        self.assertEqual(e.exception.kind, "failed")

    def test_a_key_said_back_in_an_error_is_masked(self):
        Service.script = [(401, f"Incorrect API key provided: {KEY}", {})]
        with self.assertRaises(EngineError) as e:
            self.eng().complete("s", "p")
        self.assertNotIn(KEY, str(e.exception))

    def test_a_service_elsewhere_needs_a_key_this_computer_does_not(self):
        with self.assertRaises(EngineError) as e:
            OpenAIEngine(api_key=None, base_url="https://gateway.example", model="m").complete("s", "p")
        self.assertEqual(e.exception.kind, "bad_key")
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-from-the-environment"}):
            self.assertIsNone(OpenAIEngine(base_url="https://gateway.example").api_key, "never the environment's key")
            self.eng(key=None).complete("s", "p")
        self.assertIsNone(Service.seen[-1]["auth"], "no key, no header")

    def test_addresses(self):
        for good, clean in (("https://api.openai.com/", "https://api.openai.com"),
                            ("https://api.openai.com/v1", "https://api.openai.com"),
                            ("https://openrouter.ai/api/v1", "https://openrouter.ai/api/v1"),
                            ("https://openrouter.ai/api/v1/chat/completions", "https://openrouter.ai/api/v1"),
                            ("https://generativelanguage.googleapis.com/v1beta/openai", "https://generativelanguage.googleapis.com/v1beta/openai"),
                            ("http://localhost:11434/v1", "http://localhost:11434"), ("http://127.0.0.1:1234", "http://127.0.0.1:1234"),
                            ("  https://GATEWAY.example.com/ai/ ", "https://gateway.example.com/ai")):
            self.assertEqual(engine.clean_base_url(good), clean)
        for bad in ("", "gateway.example", "ftp://a.example", "http://gateway.example", "http://192.168.1.5:1234", "http://127.0.0.1.evil.example",
                    "http://localhost@evil.example", "https://user:pw@gateway.example", "https://gateway.example?key=1", "https://a.example#x",
                    "https://a.example:notaport", "file:///etc/passwd"):
            with self.assertRaises(ValueError, msg=bad):
                engine.clean_base_url(bad)
        with self.assertRaises(EngineError) as e:
            OpenAIEngine(base_url="http://gateway.example")
        self.assertEqual(e.exception.kind, "api_address")

    def test_a_server_that_ignores_stream_and_a_model_that_thinks_out_loud(self):
        class Plain(Service):
            def do_POST(self):
                self.rfile.read(int(self.headers["content-length"]))
                data = json.dumps({"choices": [{"message": {"content": "<think>hm</think>\n# Note\n"}, "finish_reason": "stop"}]}).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        httpd = http.server.HTTPServer(("127.0.0.1", 0), Plain)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            text = self.eng(base=f"http://127.0.0.1:{httpd.server_port}").complete("s", "p").text
        finally:
            httpd.shutdown()
            httpd.server_close()
        self.assertEqual(text, "# Note\n")

    def test_where_the_request_goes(self):
        for path, posted in (("", "/v1/chat/completions"), ("/v1", "/v1/chat/completions"), ("/v1/", "/v1/chat/completions"),
                             ("/v1beta/openai", "/v1beta/openai/chat/completions"), ("/api/v1", "/api/v1/chat/completions"),
                             ("/api/v1/chat/completions", "/api/v1/chat/completions")):
            Service.seen.clear()
            self.eng(base=self.base + path).complete("s", "p")
            self.assertEqual(Service.seen[0]["path"], posted, path)

    def test_an_address_with_a_path_of_its_own_stays_strict(self):
        for bad in ("http://generativelanguage.googleapis.com/v1beta/openai", "https://a.example/v1?key=1", "https://u:p@a.example/v1"):
            with self.assertRaises(ValueError, msg=bad):
                engine.clean_base_url(bad)

    def finishing(self, reason, text="# Note\n"):
        Service.script = [[chunk(text), chunk(finish=reason)]]
        return self.eng().complete("s", "p")

    def test_only_a_finish_on_the_list_is_a_whole_answer(self):
        for reason in ("stop", "STOP", "End_Turn", "stop_sequence", "eos", "eos_token", "complete", "Completed"):
            self.assertEqual(self.finishing(reason).text, "# Note\n", reason)

    def test_a_finish_at_a_limit_is_cut_off_and_a_filter_is_a_refusal(self):
        for reason, kind in (("length", "cut_off"), ("LENGTH", "cut_off"), ("max_tokens", "cut_off"), ("model_length", "cut_off"), ("max_output_tokens", "cut_off"),
                             ("content_filter", "refused"), ("Content_Filter", "refused")):
            Service.seen.clear()
            with self.assertRaises(EngineError) as e:
                self.finishing(reason)
            self.assertEqual((e.exception.kind, len(Service.seen)), (kind, 1), reason)

    def test_any_other_finish_or_none_is_tried_again_and_never_a_note(self):
        for name, step in (("unknown", [chunk("half"), chunk(finish="tool_calls")]), ("another", [chunk("half"), chunk(finish="weird")]),
                           ("[DONE] with no reason", [chunk("half"), chunk()]), ("a stream that just ends", [chunk("half"), "\n"])):
            Service.seen.clear()
            Service.script = [step] * 4
            with self.assertRaises(EngineError, msg=name):
                self.eng().complete("s", "p")
            self.assertEqual(len(Service.seen), 4, name)
            Service.seen.clear()
            Service.script = [step, WHOLE]
            self.assertIn("It attenuates", self.eng().complete("s", "p").text, name)
            self.assertEqual(len(Service.seen), 2, name)

    def test_the_answer_that_ignores_stream_is_read_the_same_way(self):
        answers = []

        class Plain(Service):
            def do_POST(self):
                self.rfile.read(int(self.headers["content-length"]))
                data = json.dumps({"choices": [{"message": {"content": "# Note\n"}, **answers[0]}]}).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Plain)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{httpd.server_port}"
            answers[:] = [{"finish_reason": "stop"}]
            self.assertEqual(self.eng(base=base).complete("s", "p").text, "# Note\n")
            for said, kind in (({"finish_reason": "length"}, "cut_off"), ({"finish_reason": "content_filter"}, "refused"),
                               ({"finish_reason": "odd"}, "failed"), ({}, "network")):
                answers[:] = [said]
                with self.assertRaises(EngineError) as e:
                    self.eng(base=base).complete("s", "p")
                self.assertEqual(e.exception.kind, kind, said)
        finally:
            httpd.shutdown()
            httpd.server_close()

    def noting(self, text):
        Service.script = [[chunk(text), chunk(finish="stop")]]
        return self.eng().final_note(NoteRequest(**REQ))

    def test_a_refusal_written_as_text_is_no_note(self):
        for said in DECLINED:
            with self.assertRaises(EngineError, msg=said) as e:
                self.noting(said)
            self.assertEqual(e.exception.kind, "refused", said)

    def test_a_short_honest_note_and_what_only_looks_like_a_refusal_pass(self):
        for said in HONEST:
            self.assertEqual(self.noting(said).text, said)

    def test_ask_and_the_others_may_say_they_cannot(self):
        for said in DECLINED:
            Service.script = [[chunk(said), chunk(finish="stop")]]
            self.assertEqual(self.eng().complete("s", "p").text, said)

    def trimmed(self, usage, call="note"):
        Service.script = [[chunk("# Note\n"), chunk(finish="stop"), *([{"usage": usage, "choices": []}] if usage is not None else [])]]
        req = NoteRequest(course="Fibre", transcript=SRT * 1000, language="en")
        return self.eng().final_note(req) if call == "note" else self.eng().complete("s", "word " * 8000)

    def test_a_request_the_service_trimmed_silently_is_too_long(self):
        with self.assertRaises(EngineError) as e:
            self.trimmed({"prompt_tokens": 2048, "completion_tokens": 3})
        self.assertEqual(e.exception.kind, "too_long")
        self.assertIn("cut", str(e.exception))
        self.assertIn("Gateway", str(e.exception))

    def test_a_service_that_read_it_all_or_says_nothing_passes(self):
        self.assertEqual(self.trimmed(None).text, "# Note\n", "no usage sent")
        self.assertEqual(self.trimmed({"completion_tokens": 3}).text, "# Note\n", "none about the request")
        self.assertEqual(self.trimmed({"prompt_tokens": 0}).text, "# Note\n", "a service that fills in nothing")
        self.assertEqual(self.trimmed({"prompt_tokens": 9000}).text, "# Note\n", "about what was sent")

    def test_only_a_note_is_held_to_that(self):
        self.assertEqual(self.trimmed({"prompt_tokens": 5}, "complete").text, "# Note\n")

    def test_the_anthropic_engine_follows_no_redirect_either(self):
        for code in (301, 302, 303, 307, 308):
            Service.seen.clear()
            Service.script = [(code, "", {"location": f"{self.base}/elsewhere/v1/messages"})]
            eng = engine.ApiEngine(api_key=KEY, model="m", base_url=self.base)
            eng.sleep = lambda s: None
            with self.assertRaises(EngineError) as e:
                eng.complete("secret system", "secret text")
            self.assertEqual(e.exception.kind, "service_redirect", code)
            self.assertEqual(Service.elsewhere, [], f"nothing reached the other address on a {code}")
            self.assertNotIn(KEY, str(e.exception))
            self.assertEqual(len(Service.seen), 1)

    def test_the_anthropic_engine_is_otherwise_as_it_was(self):
        events = [{"type": "message_start", "message": {"model": "served", "usage": {"input_tokens": 4}}},
                  {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "# Whole\n"}},
                  {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}}]
        Service.script = [(500, "busy", {}), events + ["\n"]]
        eng = engine.ApiEngine(api_key=KEY, model="m", base_url=self.base)
        eng.sleep = lambda s: None
        result = eng.complete("s", "p")
        self.assertEqual((result.text, result.model, Service.seen[-1]["path"]), ("# Whole\n", "served", "/v1/messages"))
        self.assertEqual(len(Service.seen), 2, "what may be cured is tried again")

    def test_a_provider_saying_too_long_keeps_the_class(self):
        Service.script = [(400, "This model's maximum context length is 8192 tokens", {})]
        with self.assertRaises(EngineError) as e:
            self.eng().final_note(NoteRequest(**REQ))
        self.assertEqual(e.exception.kind, "too_long")


# A stand-in `codex`: writes down every run, then does what the file `mode` beside it says.
CODEX = """#!{python}
import json, os, pathlib, sys, time
here = pathlib.Path(sys.argv[0]).parent
argv = sys.argv[1:]
stdin = sys.stdin.read() if argv[-1:] == ["-"] else ""
system = ""
for a in argv:
    if a.startswith("model_instructions_file="):
        system = open(json.loads(a.split("=", 1)[1]), encoding="utf-8").read()
env = {{k: os.environ.get(k) for k in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_HOME", "OPENAI_BASE_URL")}}
with open(here / "runs.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps({{"argv": argv, "env": env, "stdin": stdin, "system": system, "cwd": os.getcwd(), "cwd_files": os.listdir(".")}}) + "\\n")
mode = (here / "mode").read_text().strip() if (here / "mode").exists() else "ok"
home = os.environ.get("CODEX_HOME")
signed = home is None or os.path.exists(os.path.join(home, "signed"))
if argv[:1] == ["--version"]:
    print((here / "version").read_text().strip() if (here / "version").exists() else "codex-cli 0.165.2")
elif argv[:2] == ["login", "status"]:
    print("Logged in using ChatGPT" if signed else "Not logged in")
elif argv[:1] == ["login"]:
    open(os.path.join(home, "signed"), "w").write("x") if home else None
elif argv[:1] == ["logout"]:
    os.path.exists(os.path.join(home or "", "signed")) and os.unlink(os.path.join(home, "signed"))
elif argv[:2] == ["debug", "models"]:
    print(json.dumps({{"models": [{{"slug": "gpt-big", "description": "The deepest model"}}, {{"slug": "gpt-tiny", "description": "A small, fast model"}}]}}) if mode != "no-small" else "gpt-big The deepest model")
else:
    def say(*events):
        for e in events:
            print(json.dumps(e), flush=True)
    start = ({{"type": "thread.started", "thread_id": "t"}}, {{"type": "turn.started"}})
    # As the real 0.165.0 starts with code_mode_host off: a warning, and the turn goes on.
    start += ({{"type": "item.completed", "item": {{"type": "error", "message": "Code Mode is unavailable because code-mode host is disabled. Code mode will fail closed; ..."}}}},)
    message = {{"type": "item.completed", "item": {{"id": "i", "type": "agent_message", "text": "# From the events\\n"}}}}
    done = {{"type": "turn.completed", "usage": {{"input_tokens": 7, "cached_input_tokens": 0, "output_tokens": 2, "reasoning_output_tokens": 1}}}}
    print("Reading prompt from stdin...", file=sys.stderr)
    out = argv[argv.index("-o") + 1]
    if mode == "ok":
        open(out, "w", encoding="utf-8").write("# From the file\\n\\nIt attenuates.\\n")
        say(*start, message, done)
    elif mode == "events-only":
        say(*start, message, done)
    elif mode == "shell":
        open(out, "w", encoding="utf-8").write("# With a stolen file\\n")
        say(*start, {{"type": "item.completed", "item": {{"type": "command_execution", "command": "cat /etc/passwd"}}}}, message, done)
    elif mode.startswith("item:"):
        open(out, "w", encoding="utf-8").write("# Looks fine\\n")
        say(*start, {{"type": "item.completed", "item": {{"type": mode[5:], "text": "x"}}}}, message, done)
    elif mode == "reasoning":
        open(out, "w", encoding="utf-8").write("# Thought about\\n")
        say(*start, {{"type": "item.completed", "item": {{"type": "reasoning", "text": "hm"}}}}, message, done)
    elif mode == "text":
        open(out, "w", encoding="utf-8").write((here / "answer").read_text(encoding="utf-8"))
        say(*start, message, done)
    elif mode == "shell-started":
        say(*start, {{"type": "item.started", "item": {{"type": "command_execution"}}}}, message, done)
    elif mode == "reconnect":
        open(out, "w", encoding="utf-8").write("# Fine\\n")
        say(*start, {{"type": "error", "message": "Reconnecting... 1/5"}}, message, done)
    elif mode == "failed":
        say(*start, {{"type": "turn.failed", "error": {{"message": "You've hit your usage limit. Try again later."}}}})
        sys.exit(1)
    elif mode == "signedout":
        say({{"type": "error", "message": "Not logged in. Please run codex login"}})
        sys.exit(1)
    elif mode == "toolong":
        say(*start, {{"type": "turn.failed", "error": {{"message": "Your input exceeds the context window of this model."}}}})
        sys.exit(1)
    elif mode == "old":
        print("error: unexpected argument '--ignore-rules' found", file=sys.stderr)
        sys.exit(2)
    elif mode == "empty":
        say(*start, done)
    elif mode == "no-turn":
        say(*start, message)
    elif mode == "slow":
        time.sleep(30)
"""


class Codex(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp(prefix="veuly-codex-"))
        cls.codex = cls.tmp / "codex"
        cls.codex.write_text(CODEX.format(python=sys.executable))
        cls.codex.chmod(0o755)
        cls.guard = mock.patch.object(platform_setup, "find_codex", lambda: str(cls.codex))
        cls.guard.start()

    @classmethod
    def tearDownClass(cls):
        cls.guard.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        for name in ("mode", "runs.jsonl"):
            (self.tmp / name).unlink(missing_ok=True)
        platform_setup._small_models.clear()

    def mode(self, name):
        (self.tmp / "mode").write_text(name)

    def runs(self):
        return [json.loads(x) for x in (self.tmp / "runs.jsonl").read_text().splitlines()]

    def test_the_exact_safety_flags_and_what_it_is_given(self):
        result = CodexEngine(model="gpt-big").final_note(NoteRequest(**REQ))
        self.assertEqual(result.text, "# From the file\n\nIt attenuates.\n", "the -o file is the answer")
        self.assertEqual((result.engine, result.model, result.cost_usd), ("codex", "gpt-big", None))
        self.assertEqual(result.usage["output_tokens"], 2)
        run = self.runs()[0]
        argv = run["argv"]
        self.assertEqual(tuple(argv[:len(CODEX_SAFE)]), CODEX_SAFE)
        self.assertEqual(" ".join(CODEX_SAFE),
                         'exec --ephemeral --skip-git-repo-check --ignore-user-config --ignore-rules --sandbox read-only --json '
                         '--disable shell_tool --disable unified_exec --disable multi_agent --disable apps --disable plugins '
                         '--disable browser_use --disable computer_use --disable skill_search '
                         '--disable view_image --disable image_generation --disable code_mode_host --disable multi_agent_v2 '
                         '--disable sleep_tool --disable tool_suggest --disable goals '
                         '-c web_search="disabled" -c project_doc_max_bytes=0')
        self.assertEqual(argv[-1], "-", "the prompt goes on stdin")
        self.assertEqual(argv[argv.index("-m") + 1], "gpt-big")
        self.assertTrue(any(a.startswith("model_instructions_file=") for a in argv))
        self.assertEqual(run["system"], engine.SYSTEM, "the instructions file holds the system prompt")
        self.assertIn("<transcript>", run["stdin"])
        self.assertEqual(run["cwd_files"], [], "it runs in an empty folder")
        self.assertEqual(os.path.realpath(argv[argv.index("-C") + 1]), os.path.realpath(run["cwd"]))

    def test_no_model_named_means_none_on_the_command_line(self):
        CodexEngine().complete("s", "p")
        self.assertNotIn("-m", self.runs()[0]["argv"])

    def test_the_environment_has_no_keys_and_only_its_own_folder(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-a", "CODEX_API_KEY": "sk-b", "CODEX_HOME": "/somebody/elses", "OPENAI_BASE_URL": "https://evil.example"}):
            CodexEngine().complete("s", "p")
            CodexEngine(home=str(self.tmp)).complete("s", "p")
        first, second = self.runs()
        self.assertEqual(first["env"], {"OPENAI_API_KEY": None, "CODEX_API_KEY": None, "CODEX_HOME": None, "OPENAI_BASE_URL": None})
        self.assertEqual(second["env"]["CODEX_HOME"], str(self.tmp))
        self.assertIsNone(second["env"]["OPENAI_API_KEY"])

    def test_pictures_each_with_its_own_flag_copied_to_its_own_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = pathlib.Path(tmp) / "a b.png", pathlib.Path(tmp) / "c.jpg"
            a.write_bytes(b"1")
            b.write_bytes(b"2")
            CodexEngine().final_note(NoteRequest(**REQ, images=[("img/a b.png", a), ("img/c.jpg", b)]))
        argv = self.runs()[0]["argv"]
        given = [argv[i + 1] for i, x in enumerate(argv) if x == "-i"]
        self.assertEqual([pathlib.Path(g).suffix for g in given], [".png", ".jpg"])
        self.assertTrue(all("a b" not in g for g in given))
        self.assertIn("img/a b.png, img/c.jpg", self.runs()[0]["stdin"])
        self.assertEqual(argv[-1], "-")

    def test_jsonl_alone_gives_the_answer_and_a_passing_error_is_no_failure(self):
        self.mode("events-only")
        self.assertEqual(CodexEngine().complete("s", "p").text, "# From the events\n")
        self.mode("reconnect")
        self.assertEqual(CodexEngine().complete("s", "p").text, "# Fine\n")

    def test_a_tool_that_ran_fails_the_answer(self):
        for mode in ("shell", "shell-started"):
            self.mode(mode)
            with self.assertRaises(EngineError) as e:
                CodexEngine().complete("s", "p")
            self.assertEqual(e.exception.kind, "failed", mode)
            self.assertIn("command_execution", str(e.exception))

    def test_the_warning_of_code_mode_being_off_is_no_failure(self):
        self.assertEqual(CodexEngine().complete("s", "p").text, "# From the file\n\nIt attenuates.\n")
        self.assertIn("error", [json.loads(line).get("item", {}).get("type") for line in self.codex_events()], "the stand-in does say it")
        self.mode("events-only")
        self.assertEqual(CodexEngine().final_note(NoteRequest(**REQ)).text, "# From the events\n")

    def codex_events(self):
        out = subprocess.run([str(self.codex), "exec", "-o", str(self.tmp / "o.txt"), "-"], input="p", capture_output=True, text=True).stdout
        return out.splitlines()

    def test_reasoning_is_fine_and_any_other_item_fails_the_answer(self):
        self.mode("reasoning")
        self.assertEqual(CodexEngine().complete("s", "p").text, "# Thought about\n")
        for item in ("collab_tool_call", "view_image", "image_view", "a_tool_of_a_later_codex", "web_search", "mcp_tool_call", "file_change"):
            self.mode("item:" + item)
            with self.assertRaises(EngineError) as e:
                CodexEngine().complete("s", "p")
            self.assertEqual(e.exception.kind, "failed", item)
            self.assertIn(item, str(e.exception))

    def test_a_refusal_written_as_text_is_no_note_but_ask_may_say_so(self):
        self.mode("text")
        for said in DECLINED:
            (self.tmp / "answer").write_text(said, encoding="utf-8")
            with self.assertRaises(EngineError, msg=said) as e:
                CodexEngine().final_note(NoteRequest(**REQ))
            self.assertEqual(e.exception.kind, "refused", said)
            self.assertEqual(CodexEngine().complete("s", "p").text, said)
        for said in HONEST:
            (self.tmp / "answer").write_text(said, encoding="utf-8")
            self.assertEqual(CodexEngine().final_note(NoteRequest(**REQ)).text, said)

    def test_setup_says_when_this_codex_is_not_the_version_checked(self):
        self.assertEqual(engine.CODEX_TESTED, "0.165")
        how = {"engine": "codex"}
        with mock.patch.object(platform_setup, "find_claude", lambda: None):
            self.assertEqual(platform_setup.status(how, {}).codex_unchecked, "", "0.165.2 is that version")
            for said, unchecked in (("codex-cli 0.166.0", "0.166.0"), ("codex-cli 0.16.5", "0.16.5"), ("codex-cli 0.1650.0", "0.1650.0"), ("something odd", "")):
                (self.tmp / "version").write_text(said)
                self.addCleanup((self.tmp / "version").unlink, missing_ok=True)
                self.assertEqual(platform_setup.status(how, {}).codex_unchecked, unchecked, said)
            before = sum(r["argv"][:1] == ["--version"] for r in self.runs())
            seen = {}
            platform_setup.status(how, seen)
            platform_setup.status(how, seen)
            self.assertEqual(sum(r["argv"][:1] == ["--version"] for r in self.runs()), before + 1, "asked once per program file")
            (self.tmp / "runs.jsonl").unlink()
            self.assertEqual(platform_setup.status({"engine": "api"}, {}).codex_unchecked, "", "nothing is asked of a Codex nobody chose")
            self.assertFalse((self.tmp / "runs.jsonl").exists())

    def test_failures_have_kinds(self):
        for mode, kind in (("failed", "limit"), ("signedout", "codex_signed_out"), ("toolong", "too_long"), ("old", "failed"), ("empty", "failed"), ("no-turn", "failed")):
            self.mode(mode)
            with self.assertRaises(EngineError) as e:
                CodexEngine().complete("s", "p")
            self.assertEqual(e.exception.kind, kind, mode)
        self.mode("old")
        with self.assertRaises(EngineError) as e:
            CodexEngine().complete("s", "p")
        self.assertIn("update Codex", str(e.exception))

    def test_a_timeout_stops_it_and_not_installed_says_so(self):
        self.mode("slow")
        started = time.monotonic()
        with self.assertRaises(EngineError) as e:
            CodexEngine(timeout_s=1).complete("s", "p")
        self.assertEqual(e.exception.kind, "failed")
        self.assertLess(time.monotonic() - started, 15)
        self.assertIn("no answer", str(e.exception))
        with mock.patch.object(platform_setup, "find_codex", lambda: None):
            with self.assertRaises(EngineError) as e:
                CodexEngine().complete("s", "p")
        self.assertEqual(e.exception.kind, "codex_not_installed")

    def test_it_starts_through_run_child_so_quit_stops_it(self):
        with mock.patch.object(paths, "run_child", wraps=paths.run_child) as run:
            CodexEngine().complete("s", "p")
        self.assertEqual(run.call_count, 1)

    def test_its_login_state_and_small_model(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertIs(platform_setup.codex_signed(str(self.codex)), True, "this Mac's own")
            self.assertIs(platform_setup.codex_signed(str(self.codex), home), False, "a folder with no sign-in")
            self.assertEqual(platform_setup.codex_small_model(str(self.codex), home), "gpt-tiny")
            platform_setup.codex_small_model(str(self.codex), home)
            self.assertEqual(sum(r["argv"][:2] == ["debug", "models"] for r in self.runs()), 1, "asked once")
        platform_setup._small_models.clear()
        self.mode("no-small")
        self.assertEqual(platform_setup.codex_small_model(str(self.codex)), "", "none that says it is small")
        self.assertEqual(platform_setup._listed_models('[{"slug": "a", "description": "b"}, "c"]'), [("a", "b"), ("c", "")])
        hidden = '{"models": [{"slug": "gpt-reserve", "visibility": "hide", "description": "Fast and affordable"}, {"slug": "gpt-6-luna", "visibility": "list", "description": "Fast and affordable"}]}'
        self.assertEqual(platform_setup._listed_models(hidden), [("gpt-6-luna", "Fast and affordable")], "the real 0.165 picked a hidden one")
        self.assertEqual(platform_setup._listed_models("gpt-a The first\n  gpt-b\n\n--- table\n"), [("gpt-a", "The first"), ("gpt-b", "")])


class Stand(unittest.TestCase):
    """Accounts of the new kinds through the server's routes, in a data folder of their own."""

    @classmethod
    def setUpClass(cls):
        cls.home = pathlib.Path(tempfile.mkdtemp(prefix="veuly-providers-"))
        cls.saved = {k: os.environ.get(k) for k in ("VEULY_HOME", "CLAUDE_CONFIG_DIR", "OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_HOME")}
        os.environ["VEULY_HOME"] = str(cls.home)
        for k in ("CLAUDE_CONFIG_DIR", "OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_HOME"):
            os.environ.pop(k, None)
        cls.bin = cls.home / "bin"
        cls.bin.mkdir()
        cls.codex = cls.bin / "codex"
        cls.codex.write_text(CODEX.format(python=sys.executable))
        cls.codex.chmod(0o755)
        cls.claude = cls.bin / "claude"
        cls.claude.write_text(f"#!/bin/sh\necho \"$@\" >> \"{cls.bin}/claude.log\"\necho '{{\"loggedIn\": true}}'\n")
        cls.claude.chmod(0o755)
        cls.guards = [mock.patch.object(platform_setup, "find_codex", lambda: str(cls.codex)),
                      mock.patch.object(platform_setup, "find_claude", lambda: str(cls.claude))]
        for g in cls.guards:
            g.start()
        cls.service = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Service)
        threading.Thread(target=cls.service.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.service.server_port}"
        paths.save_settings({"engine": "api", "auto_images": False, "setup_done": True})
        cls.httpd, cls.app = server.serve(ROOT / "web", 0)
        cls.app.pipeline.find_claude = platform_setup.find_claude
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.c = Calls(cls.app)
        cls.c.call("GET", f"/?t={cls.app.token}")
        cls.lib = cls.app.lib
        cls.school = cls.lib.create_course("Fibre", "en")["slug"]
        cls.work = cls.lib.create_course("Migration", "en", "project")["slug"]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.service.shutdown()
        cls.service.server_close()
        for g in cls.guards:
            g.stop()
        for k, v in cls.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(cls.home, ignore_errors=True)

    def setUp(self):
        Service.script, Service.seen, Service.elsewhere = [], [], []
        (self.bin / "mode").unlink(missing_ok=True)
        (self.bin / "runs.jsonl").unlink(missing_ok=True)
        (self.bin / "claude.log").unlink(missing_ok=True)
        self.app.seen.clear()
        self.app.account_seen.clear()
        platform_setup._small_models.clear()
        paths.save_settings({"engine": "api", "claude_accounts": [], "school_account": "default", "work_account": "default",
                             "openai_base_url": "", "openai_name": "", "openai_model": "gpt-5", "openai_small_model": "", "codex_model": "", "context_tokens": 0})

    def started(self, name):
        return (self.bin / "runs.jsonl").read_text() if name == "codex" and (self.bin / "runs.jsonl").exists() else \
            (self.bin / "claude.log").read_text() if name == "claude" and (self.bin / "claude.log").exists() else ""

    def add(self, **kw):
        code, data = self.c.post("/api/accounts", kw)
        self.assertEqual(code, 200, data)
        return next(a for a in data["accounts"] if a["id"] == data["added"])

    def test_a_key_with_a_newline_never_reaches_a_header(self):
        bad = "sk-good-key-" + "k" * 20 + "\nX-Evil: yes"
        gw = self.add(name="GW", kind="openai", key=KEY, base_url="https://g.example", model="m")
        api = self.add(name="Direct", kind="api", key="sk-ant-" + "a" * 30)
        for aid in (gw["id"], api["id"]):
            with self.assertRaises(accounts.AccountError) as e:
                accounts.set_key(aid, bad)
            self.assertEqual(e.exception.kind, "key_shape")
            code, data = self.c.put(f"/api/accounts/{aid}", {"key": bad})
            self.assertEqual((code, data.get("kind")), (400, "key_shape"), data)
        self.assertEqual(keystore.get_secret(accounts.secret_name(gw["id"])), KEY, "the good one stays")
        for body in ({"kind": "api", "key": bad}, {"kind": "openai", "key": bad, "base_url": "https://g.example", "model": "m"}):
            with self.assertRaises(accounts.AccountError) as e:
                accounts.add("Bad", body.pop("kind"), **body)
            self.assertEqual(e.exception.kind, "key_shape")
        with self.assertRaises(ValueError):
            keystore.set_key(bad)
        accounts.set_key(gw["id"], KEY + "\n")          # pasted with the end of the line: it is the key all the same
        self.assertEqual(keystore.get_secret(accounts.secret_name(gw["id"])), KEY)

    def write(self, slug):
        cid = self.lib.create_class(slug, "Weekly")["id"]
        self.lib.write_text(slug, cid, "transcript", SRT)
        self.app.pipeline.start(slug, cid, "write")
        for _ in range(300):
            if self.lib.meta(slug, cid)["status"] in ("done", "error"):
                break
            time.sleep(0.02)
        return self.lib.meta(slug, cid)

    # ---- what kinds exist
    def test_an_openai_account_writes_with_its_key_and_keeps_it_out_of_the_settings(self):
        acct = self.add(name="Work AI", kind="openai", key=KEY, base_url=self.base + "/v1", model="llama3", small_model="tiny")
        self.assertEqual(acct["base_url"], self.base, "kept clean")
        self.assertEqual(acct["key_hint"], keystore.hint(KEY))
        raw = (paths.data_dir() / "settings.json").read_text()
        self.assertNotIn(KEY, raw)
        self.assertEqual(keystore.get_secret(accounts.secret_name(acct["id"])), KEY)
        paths.save_settings({"school_account": acct["id"]})
        meta = self.write(self.school)
        self.assertEqual(meta["status"], "done", meta.get("error"))
        self.assertEqual((meta["engine"], meta["model"], meta["service"], meta["account"]), ("openai", "served-model", "Work AI", acct["id"]))
        self.assertEqual(Service.seen[0]["auth"], f"Bearer {KEY}")
        self.assertEqual(Service.seen[0]["body"]["model"], "llama3")

    def test_refusals_when_adding(self):
        for body, kind in (({"name": "a", "kind": "openai", "key": KEY, "base_url": "http://gateway.example", "model": "m"}, "api_address"),
                           ({"name": "a", "kind": "openai", "key": KEY, "base_url": "https://gateway.example"}, "model_name"),
                           ({"name": "a", "kind": "openai", "key": KEY, "base_url": "https://gateway.example", "model": "bad model"}, "model_name"),
                           ({"name": "a", "kind": "openai", "base_url": "https://gateway.example", "model": "m"}, "key_shape"),
                           ({"name": "a", "kind": "openai"}, "key_shape"),
                           ({"name": "a", "kind": "openai", "key": "with space", "base_url": "https://gateway.example", "model": "m"}, "key_shape"),
                           ({"name": "a", "kind": "codex", "model": "bad model"}, "model_name"),
                           ({"name": "a", "kind": "openai", "key": KEY, "context_tokens": 5}, "bad_request")):
            code, data = self.c.post("/api/accounts", body)
            self.assertEqual((code, data.get("kind")), (400, kind), body)

    def test_no_key_only_for_this_computer(self):
        acct = self.add(name="Ollama", kind="openai", base_url="http://localhost:11434/v1", model="llama3")
        self.assertEqual(acct["base_url"], "http://localhost:11434")
        self.assertIsNone(keystore.get_secret(accounts.secret_name(acct["id"])))

    def test_a_new_address_deletes_the_key(self):
        acct = self.add(name="GW", kind="openai", key=KEY, base_url="https://one.example", model="m")
        code, data = self.c.put(f"/api/accounts/{acct['id']}", {"base_url": "https://two.example"})
        self.assertEqual(code, 200, data)
        self.assertIsNone(keystore.get_secret(accounts.secret_name(acct["id"])), "the key was for the other address")
        self.assertEqual(next(a for a in data["accounts"] if a["id"] == acct["id"])["key_hint"], "")
        keystore.set_secret(accounts.secret_name(acct["id"]), KEY)
        self.c.put(f"/api/accounts/{acct['id']}", {"base_url": "https://two.example/v1/"})
        self.assertEqual(keystore.get_secret(accounts.secret_name(acct["id"])), KEY, "the same address, as written otherwise")

    def test_removing_takes_the_key_and_the_folder(self):
        acct = self.add(name="GW", kind="openai", key=KEY)
        self.c.delete(f"/api/accounts/{acct['id']}")
        self.assertIsNone(keystore.get_secret(accounts.secret_name(acct["id"])))
        cod = self.add(name="Cod", kind="codex")
        folder = accounts.codex_folder(cod["id"])
        self.assertTrue(folder.is_dir())
        self.assertEqual(list(folder.iterdir()), [], "made empty")
        self.c.delete(f"/api/accounts/{cod['id']}?signout=1")
        self.assertFalse(folder.exists())

    def test_a_codex_account_uses_its_own_folder(self):
        acct = self.add(name="Cod", kind="codex", model="gpt-big")
        self.assertIs(acct["signed"], False)
        (accounts.codex_folder(acct["id"]) / "signed").write_text("x")
        paths.save_settings({"school_account": acct["id"]})
        meta = self.write(self.school)
        self.assertEqual(meta["status"], "done", meta.get("error"))
        self.assertEqual((meta["engine"], meta["model"], meta["service"]), ("codex", "gpt-big", "Codex"))
        run = json.loads(self.started("codex").splitlines()[-1])
        self.assertEqual(run["env"]["CODEX_HOME"], str(accounts.codex_folder(acct["id"])))
        (self.bin / "mode").write_text("signedout")
        (accounts.codex_folder(acct["id"]) / "signed").unlink()
        meta = self.write(self.school)
        self.assertEqual(meta["error_kind"], "account_signed_out")
        self.assertIn("Cod account", meta["error"])

    def test_its_sign_in_opens_a_terminal_on_codex_login_with_its_folder(self):
        acct = self.add(name="Cod", kind="codex")
        with mock.patch.object(platform_setup, "detect_os", lambda: {"os": "linux", "arch": "x64", "version": ""}):
            code, data = self.c.post(f"/api/accounts/{acct['id']}/login", {})
        self.assertEqual(code, 200)
        self.assertFalse(data["ok"])
        self.assertIn(f"CODEX_HOME={accounts.codex_folder(acct['id'])}", data["command"])
        self.assertTrue(data["command"].endswith("login"))

    def test_setup_may_choose_each_new_way(self):
        code, data = self.c.post("/api/settings", {"engine": "openai", "openai_base_url": self.base + "/v1", "openai_model": "local/m", "openai_name": "Mine"})
        self.assertEqual(code, 200, data)
        self.assertEqual(data["openai_base_url"], self.base)
        code, data = self.c.post("/api/key", {"slot": "openai", "key": "short-key"})
        self.assertEqual(code, 200, data)
        self.assertEqual(data["openai_key_hint"], keystore.hint("short-key"))
        self.assertEqual(keystore.get_secret(accounts.setup_secret(self.base)), "short-key")
        code, data = self.c.post("/api/setup/test-engine", {})
        self.assertEqual(code, 200, data)
        self.assertEqual((data["engine"], data["service"]), ("openai", "Mine"))
        self.assertEqual(Service.seen[-1]["auth"], "Bearer short-key")
        # a new address: the key does not go there, and it is gone
        code, data = self.c.post("/api/settings", {"openai_base_url": "https://other.example"})
        self.assertEqual(code, 200)
        self.assertIsNone(keystore.get_secret(accounts.setup_secret(self.base)))
        self.assertIsNone(accounts.openai_key(accounts.settings_for(paths.load_settings())))
        code, data = self.c.post("/api/settings", {"engine": "codex"})
        self.assertEqual(code, 200)
        code, data = self.c.post("/api/setup/test-engine", {})
        self.assertEqual((code, data.get("engine")), (200, "codex"), data)
        self.assertIsNone(json.loads(self.started("codex").splitlines()[-1])["env"]["CODEX_HOME"], "this Mac's own login")

    def test_a_key_of_setup_is_named_after_its_address(self):
        self.assertEqual(accounts.setup_secret(""), accounts.setup_secret(engine.OPENAI_URL))
        self.assertNotEqual(accounts.setup_secret("https://a.example"), accounts.setup_secret("https://b.example"))

    def test_check_settings_refuses(self):
        for body, kind in (({"openai_base_url": "http://gateway.example"}, "api_address"), ({"openai_model": "two words"}, "model_name"),
                           ({"codex_model": "bad;model"}, "model_name"), ({"openai_small_model": "x y"}, "model_name"),
                           ({"engine": "something-else"}, "bad_request"), ({"context_tokens": 3}, "bad_request")):
            code, data = self.c.post("/api/settings", body)
            self.assertEqual((code, data.get("kind")), (400, kind), body)
        self.assertEqual(self.c.post("/api/settings", {"openai_model": "meta-llama/llama-3:free"})[0], 200)

    # ---- nothing is started for someone who did not choose it
    def test_setup_starts_neither_program_for_someone_with_a_key(self):
        paths.save_settings({"engine": "openai"})
        for path in ("/api/setup", "/api/setup?refresh=1", "/api/accounts", "/api/accounts?refresh=1"):
            self.assertEqual(self.c.get(path)[0], 200)
        self.assertEqual(self.started("claude"), "")
        self.assertEqual(self.started("codex"), "")
        code, data = self.c.get("/api/setup")
        self.assertEqual((data["claude_path"], data["codex_path"]), (str(self.claude), str(self.codex)), "found, not started")
        self.assertIsNone(data["codex_signed"])

    def test_setup_asks_each_only_when_chosen(self):
        paths.save_settings({"engine": "codex"})
        code, data = self.c.get("/api/setup")
        self.assertIs(data["codex_signed"], True)
        self.assertIn("login", self.started("codex"))
        self.assertEqual(self.started("claude"), "", "Codex chosen: not Claude Code")
        (self.bin / "runs.jsonl").unlink()
        self.c.get("/api/setup")
        self.assertEqual(self.started("codex"), "", "once it said yes, opening Settings asks nothing")
        paths.save_settings({"engine": "claude-code"})
        self.c.get("/api/setup")
        self.assertIn("--version", self.started("claude"))

    def test_setup_needs_neither_program(self):
        paths.save_settings({"engine": ""})
        with mock.patch.object(platform_setup, "find_codex", lambda: None), mock.patch.object(platform_setup, "find_claude", lambda: None):
            code, data = self.c.get("/api/setup")
        self.assertEqual(code, 200)
        self.assertEqual((data["claude_path"], data["codex_path"]), (None, None))

    # ---- heads-up: a small model per way
    def small(self, acct_id=None):
        paths.save_settings({"work_account": acct_id or "default"})
        return headsup.engine_settings(paths.load_settings(), {"kind": "project"})

    def test_the_small_model_of_each_way(self):
        s = self.small()
        self.assertEqual((s["api_model"], s["claude_model"]), ("claude-haiku-5-5", "haiku"))
        oa = self.add(name="OA", kind="openai", key=KEY, model="gpt-5.5")
        self.assertEqual(self.small(oa["id"])["openai_model"], "gpt-5-mini")
        oa2 = self.add(name="OA2", kind="openai", key=KEY, small_model="gpt-own-mini")
        self.assertEqual(self.small(oa2["id"])["openai_model"], "gpt-own-mini")
        gw = self.add(name="GW", kind="openai", key=KEY, base_url="https://g.example", model="their-big")
        self.assertEqual(self.small(gw["id"])["openai_model"], "their-big", "nothing else is known to exist there")
        gw2 = self.add(name="GW2", kind="openai", key=KEY, base_url="https://g.example", model="their-big", small_model="their-small")
        self.assertEqual(self.small(gw2["id"])["openai_model"], "their-small")
        self.assertEqual(self.started("codex"), "", "no Codex asked for any of those")
        cod = self.add(name="Cod", kind="codex", model="gpt-big")
        self.assertEqual(self.small(cod["id"])["codex_model"], "gpt-tiny")
        platform_setup._small_models.clear()
        (self.bin / "mode").write_text("no-small")
        self.assertEqual(self.small(cod["id"])["codex_model"], "gpt-big", "the slug is gone: the account's model")

    def test_a_check_through_openai_is_small_and_tries_once(self):
        gw = self.add(name="GW", kind="openai", key=KEY, base_url=self.base, model="their-big")
        eng = headsup._short(pipeline.default_engine(self.small(gw["id"])))
        Service.script = [(429, "busy", {})]
        with self.assertRaises(EngineError):
            eng.complete("s", "p")
        self.assertEqual(len(Service.seen), 1, "one try")
        self.assertEqual(Service.seen[0]["body"]["max_tokens"], headsup.MAX_TOKENS)
        self.assertEqual(eng.inner.timeout_s, headsup.CALL_LIMIT_S)
        self.assertEqual(headsup.STOPS["codex_signed_out"], "signed_out")

    # ---- what only Claude Code can do
    def test_the_connector_and_the_agent_say_they_need_claude_code(self):
        oa = self.add(name="OA", kind="openai", key=KEY)
        cod = self.add(name="Cod", kind="codex")
        pipe = self.app.pipeline
        for acct in (oa, cod):
            self.lib.update_course(self.work, {"claude_account": acct["id"]})
            course = self.lib.course(self.work)
            with self.assertRaises(confluence.ConfluenceError) as e:
                pipe.claude_env(course, confluence.ConfluenceError)
            self.assertEqual(e.exception.kind, "account_needs_login")
            with self.assertRaises(obsidian.ObsidianError) as e:
                pipe.claude_env(course, obsidian.ObsidianError)
            self.assertEqual(e.exception.kind, "account_needs_login")
            code, data = self.c.post("/api/confluence/connector", {"account": acct["id"]})
            self.assertEqual(data.get("kind"), "account_needs_login", data)
            self.assertNotEqual(code, 200)
        self.lib.update_course(self.work, {"claude_account": ""})
        # Setup's own way of writing, when it is another company's
        for how in ("openai", "codex"):
            paths.save_settings({"engine": how})
            self.assertFalse(accounts.claude_code_ok(paths.load_settings(), accounts.default_account()))
            with self.assertRaises(confluence.ConfluenceError) as e:
                pipe.claude_env(self.lib.course(self.work), confluence.ConfluenceError)
            self.assertEqual(e.exception.kind, "account_needs_login")
        paths.save_settings({"engine": "api"})
        self.assertEqual(pipe.claude_env(self.lib.course(self.work), confluence.ConfluenceError), {}, "Setup's Anthropic key, as before")
        self.assertEqual(self.started("claude"), "", "and none of that started it")

    def test_the_api_token_way_keeps_working_for_everyone(self):
        oa = self.add(name="OA", kind="openai", key=KEY)
        self.lib.update_course(self.work, {"claude_account": oa["id"]})
        self.lib.update_course(self.work, {"confluence": {"parent_id": "777"}})
        cid = self.lib.create_class(self.work, "Weekly")["id"]
        self.lib.write_text(self.work, cid, "note", "# Weekly\n\nDone.\n")
        paths.save_settings({"confluence_method": "token", "confluence_url": "https://a.example/wiki"})
        published = []

        class Fake:
            def publish(self, dest, title, storage, previous=""):
                published.append(title)
                return {"id": "1"}

            def attach(self, *a):
                pass

            def page_url(self, page):
                return "https://a.example/wiki/1"
        keystore.set_secret(confluence.TOKEN_NAME, "tok" * 10)
        with mock.patch.object(confluence, "Client", lambda *a, **k: Fake()):
            self.app.pipeline.publish(self.work, cid)
        self.assertEqual(len(published), 1)
        self.assertEqual(self.started("claude"), "")
        self.lib.update_course(self.work, {"claude_account": "", "confluence": {}})
        paths.save_settings({"confluence_method": "", "confluence_url": ""})
        keystore.delete_secret(confluence.TOKEN_NAME)

    # ---- the size of a request
    def test_the_limit_comes_from_the_way_of_writing(self):
        self.assertIsNone(accounts.context_tokens({"engine": "api"}), "Anthropic's ways: the engine's 900k")
        self.assertIsNone(accounts.context_tokens({"engine": "claude-code"}))
        self.assertEqual(engine.CONTEXT_TOKENS, 900_000)
        for how in ("openai", "codex"):
            self.assertEqual(accounts.context_tokens({"engine": how}), 200_000)
            self.assertEqual(accounts.context_tokens({"engine": how, "context_tokens": 32_000}), 32_000)
        acct = self.add(name="Small", kind="openai", base_url="http://localhost:1/", model="m", context_tokens=2000)
        self.assertEqual(accounts.context_tokens(accounts.settings_for(paths.load_settings(), account=acct["id"])), 2000)

    def test_a_class_too_long_for_a_small_account_stops_before_anything_is_sent(self):
        acct = self.add(name="Small", kind="openai", base_url=self.base, model="m", context_tokens=20000)
        paths.save_settings({"school_account": acct["id"]})
        cid = self.lib.create_class(self.school, "Long")["id"]
        self.lib.write_text(self.school, cid, "transcript", "1\n00:00:00,000 --> 00:00:04,000\n" + "word " * 30000 + "\n")
        self.app.pipeline.start(self.school, cid, "write")
        for _ in range(300):
            if self.lib.meta(self.school, cid)["status"] in ("done", "error"):
                break
            time.sleep(0.02)
        meta = self.lib.meta(self.school, cid)
        self.assertEqual(meta["error_kind"], "too_long", meta.get("error"))
        self.assertEqual(Service.seen, [], "nothing was sent")

    # ---- the scan that keeps every engine on its account
    def test_only_default_engine_makes_an_engine(self):
        src = (ROOT / "transcapp").glob("*.py")
        for f in src:
            if f.name == "engine.py":
                continue
            text = f.read_text(encoding="utf-8")
            for name in ("OpenAIEngine(", "CodexEngine("):
                if name in text:
                    self.assertEqual(f.name, "pipeline.py", f"{name} made outside default_engine")


class FindingCodex(unittest.TestCase):
    """Installed with a plain `npm install -g @openai/codex` under nvm: a copy opened from Finder has no nvm on its PATH."""

    def setUp(self):
        self.home = pathlib.Path(tempfile.mkdtemp(prefix="veuly-nvm-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        patch = mock.patch.dict(os.environ, {"HOME": str(self.home), "USERPROFILE": str(self.home), "PATH": "/usr/bin:/bin"})
        patch.start()
        self.addCleanup(patch.stop)

    def node(self, version, codex=True):
        folder = self.home / ".nvm" / "versions" / "node" / version / "bin"
        folder.mkdir(parents=True)
        if codex:
            (folder / "codex").write_text("#!/bin/sh\n")
            (folder / "codex").chmod(0o755)
        (folder / "node").write_text("#!/bin/sh\n")
        return folder

    def test_the_newest_node_first_by_number_not_by_spelling(self):
        self.node("v9.11.2")
        newest = self.node("v22.3.0")
        self.node("v18.20.4")
        self.node("v24.0.0", codex=False)
        self.node("not-a-version")
        with mock.patch.object(paths, "find_program", lambda *names: None):
            self.assertEqual(platform_setup.find_codex(), str(newest / "codex"))

    def test_what_path_finds_comes_first_and_nothing_is_nothing(self):
        self.node("v22.3.0")
        with mock.patch.object(paths, "find_program", lambda *names: "/somewhere/codex"):
            self.assertEqual(platform_setup.find_codex(), "/somewhere/codex")
        shutil.rmtree(self.home / ".nvm")
        with mock.patch.object(paths, "find_program", lambda *names: None):
            self.assertIsNone(platform_setup.find_codex())

    @unittest.skipUnless(sys.platform == "darwin", "PATH is only extended on a Mac")
    def test_its_node_is_on_path_too_or_it_would_not_start(self):
        self.node("v9.11.2")
        newest = self.node("v22.3.0")
        paths.extend_path()
        have = os.environ["PATH"].split(os.pathsep)
        self.assertIn(str(newest), have, "a script that starts with `env node`")
        self.assertLess(have.index(str(newest)), have.index(str(self.home / ".nvm" / "versions" / "node" / "v9.11.2" / "bin")))

    def test_the_hint_is_the_plain_command_and_says_no_more(self):
        for hints in platform_setup.HINTS.values():
            self.assertEqual(hints["codex"], "npm install -g @openai/codex")


class Language(unittest.TestCase):
    """The request's language line, on stand-ins: a real run is still pending (README)."""
    NO_NOTES = dict(course="Fibre", transcript=SRT)

    def test_with_no_language_known_it_says_the_main_language_of_the_transcript(self):
        for kind, end in (("course", "Write the final note now."), ("project", "Write the minutes now.")):
            for lang in ("", "auto"):
                prompt = engine.build_prompt(NoteRequest(**self.NO_NOTES, kind=kind, language=lang, instructions=prefs.instructions({}, kind=kind)))
                self.assertIn(engine.LANGUAGE_OF_TRANSCRIPT, prompt)
                self.assertIn("main language of the transcript", prompt)
                self.assertNotIn(engine.TRANSLATE_HEADINGS, prompt)
                self.assertNotIn("translate", prompt.split("</transcript>")[1].replace("<note_style>", ""), "nothing there says translate: that asked for another language")
                self.assertTrue(prompt.endswith(f"{engine.LANGUAGE_OF_TRANSCRIPT}\n\n{end}"))

    def test_typed_notes_decide_as_the_system_prompt_says(self):
        prompt = engine.build_prompt(NoteRequest(**self.NO_NOTES, live_notes="Atenuación ??"))
        self.assertIn(engine.LANGUAGE_OF_NOTES, prompt)
        self.assertNotIn(engine.LANGUAGE_OF_TRANSCRIPT, prompt)

    def test_with_a_language_known_nothing_changes(self):
        for lang in ("en", "es", "ca"):
            prompt = engine.build_prompt(NoteRequest(**self.NO_NOTES, language=lang))
            self.assertIn(engine.TRANSLATE_HEADINGS, prompt)
            self.assertNotIn("main language", prompt)

    def test_the_rule_in_the_system_prompts_is_as_it_was(self):
        self.assertIn("with no notes, in the language of the class given in the request, or else of the transcript", engine.SYSTEM)
        self.assertIn("with no notes, in the language of the meeting given in the request, or else of the transcript", engine.SYSTEM_WORK)


class SameBytes(unittest.TestCase):
    """What Claude is sent, byte for byte what it was before other providers came. The numbers are of the code before this work (commit 16ce458)."""
    # each: sha256 of the text as commit 16ce458 made it
    SYSTEM = "abe207a6b48a90e594a7826c90b71cb62c10981716218ccacf5dcb15899203dc"
    SYSTEM_WORK = "29a5fcb5edf9f4454d558bc9123adfd8a367edd7a58e2fe12d289aa6bb88e9cb"
    REQUEST_SCHOOL = "4d5e766176288cbf98401c0dab38306cd5032fbdd687be4f2fe18d9566972877"
    REQUEST_WORK = "f285c8d8e8c5ffeda04b0e00384a497058a458b31dd22a874e1f4e99563ab022"

    @staticmethod
    def sha(text):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def test_the_system_prompts(self):
        self.assertEqual(self.sha(engine.SYSTEM), self.SYSTEM)
        self.assertEqual(self.sha(engine.SYSTEM_WORK), self.SYSTEM_WORK)

    def school(self, **kw):
        return NoteRequest(course="Fibre", transcript=SRT, live_notes="Attenuation ??", materials=[("Slides.pdf", "Slide 1")], exams=[("Exam.pdf", "Q1")],
                           style_samples=[("Mine.md", "## Topic")], extra="Short", instructions=prefs.instructions({}), title="T", date="2026-10-30",
                           language="en", me="Ana", **kw)

    def work(self, **kw):
        return NoteRequest(course="Migration", transcript=SRT, live_notes="Budget ??", kind="project", attendees="Marta", materials=[("Plan.md", "Phase")],
                           instructions=prefs.instructions({}, kind="project"), title="W", date="2026-10-30", language="es", me="Ana", **kw)

    def test_the_requests_with_a_language_known(self):
        self.assertEqual(self.sha(engine.build_prompt(self.school())), self.REQUEST_SCHOOL)
        self.assertEqual(self.sha(engine.build_prompt(self.work())), self.REQUEST_WORK)

    def test_with_no_language_only_its_line_differs(self):
        known, unknown = self.school(), self.school()
        unknown.language = ""
        plain = engine.build_prompt(known).replace(' language="English"', "")
        self.assertEqual(engine.build_prompt(unknown).replace(engine.LANGUAGE_OF_NOTES, engine.TRANSLATE_HEADINGS), plain)


class NothingNamesAVendorLogo(unittest.TestCase):
    def test_the_claude_logo_is_gone(self):
        self.assertFalse((ROOT / "web" / "logos" / "claude.svg").exists())
        readme = (ROOT / "web" / "logos" / "README.txt").read_text(encoding="utf-8")
        self.assertNotIn("claude.svg", readme)
        self.assertIn("guidelines", readme)


class FitRequest(unittest.TestCase):
    def test_it_takes_the_limit_it_is_given(self):
        req = NoteRequest(course="C", transcript="x" * 30_000, materials=[("a", "y" * 30_000)])
        self.assertEqual(engine.fit_request(req, 15_000), ["a"])
        with self.assertRaises(EngineError) as e:
            engine.fit_request(NoteRequest(course="C", transcript="x" * 90_000), 15_000)
        self.assertEqual(e.exception.kind, "too_long")
        self.assertEqual(engine.fit_request(NoteRequest(course="C", transcript="x" * 90_000)), [], "900k without a limit given")

    def test_the_pipeline_still_uses_fake_engines(self):
        self.assertTrue(callable(FakeEngine))


if __name__ == "__main__":
    unittest.main()
