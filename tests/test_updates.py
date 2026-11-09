"""The check for a newer version: only a copy built with an address asks, and what it reads is outside data."""
import http.server
import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("VEULY_SECRETS", "file")

from transcapp import home, updates  # noqa: E402


class Reading(unittest.TestCase):
    def test_only_a_higher_version_counts(self):
        for latest, current, want in (("1.0.1", "1.0.0", True), ("1.10", "1.9.9", True), ("1.0", "1.0.0", False), ("0.9", "1.0", False),
                                      ("1.0.0", "1.0.0", False), ("2", "1.9.2", True), ("v2", "1.0", False), ("2.0-beta", "1.0", False),
                                      ("", "1.0", False), (None, "1.0", False), (3, "1.0", False), ("2.0", "", False)):
            self.assertEqual(updates.newer(latest, current), want, (latest, current))

    def test_what_the_file_says_is_checked(self):
        good = {"version": "2.0", "url": "https://veuly.example/download"}
        self.assertEqual(updates.read(json.dumps(good).encode(), "1.0"), good)
        self.assertEqual(updates.read(json.dumps({**good, "extra": "<script>"}).encode(), "1.0"), good, "nothing else is kept")
        for bad in (b"not json", b"\xff\xfe", b"[]", b'"2.0"', json.dumps({"version": "2.0"}).encode(),
                    json.dumps({**good, "url": "javascript:alert(1)"}).encode(), json.dumps({**good, "url": "file:///etc/passwd"}).encode(),
                    json.dumps({**good, "url": "http://veuly.example/plain"}).encode(), json.dumps({**good, "version": "1.0"}).encode()):
            self.assertEqual(updates.read(bad, "1.0"), {}, bad)


class Asking(unittest.TestCase):
    def setUp(self):
        updates._found.clear()
        self.addCleanup(updates._found.clear)

    def test_a_copy_with_no_address_never_asks(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        with mock.patch.object(updates.paths, "resource_dir", return_value=pathlib.Path(folder.name)), \
                mock.patch.object(updates, "check", side_effect=AssertionError("asked")):
            self.assertFalse(updates.start("1.0"), "no bundle.json")
            (pathlib.Path(folder.name) / "bundle.json").write_text('{"auto_model": true}', encoding="utf-8")
            self.assertFalse(updates.start("1.0"), "no address in it")
            (pathlib.Path(folder.name) / "bundle.json").write_text('{"update_url": "http://veuly.example/x"}', encoding="utf-8")
            self.assertFalse(updates.start("1.0"), "not https")
            (pathlib.Path(folder.name) / "bundle.json").write_text("{broken", encoding="utf-8")
            self.assertFalse(updates.start("1.0"))
        self.assertEqual(updates.home_part(None, [], "course"), {})

    def test_it_asks_and_the_home_page_says_so(self):
        answers = {"body": json.dumps({"version": "9.0", "url": "https://veuly.example/get"}).encode()}
        seen = []

        class Stand(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(dict(self.headers))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(answers["body"])

            def log_message(self, *a):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stand)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_address[1]}/latest.json"
        found = updates.check(url, "1.0")
        self.assertEqual(found, {"version": "9.0", "url": "https://veuly.example/get"})
        self.assertEqual(seen[0].get("User-Agent"), "Veuly", "the request says nothing about the person")
        updates._found.update(found)
        self.assertEqual(updates.home_part(None, [], "project"), {"update": found})
        self.assertIn(updates.home_part, home.PARTS)
        answers["body"] = b"x" * (updates.MAX_BYTES + 10)
        self.assertEqual(updates.check(url, "1.0"), {}, "too long is cut and then is not JSON")
        self.assertEqual(updates.check("http://127.0.0.1:1/none", "1.0"), {}, "no answer is no tile, and no error")


if __name__ == "__main__":
    unittest.main()
