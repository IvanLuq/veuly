"""A course or project kept in step with a folder (transcapp/foldersync.py): what comes in, what is left alone, what never
happens to the person's folder, when it gives way. Temporary folders only; stand-ins only: no whisper, no Claude, no Keychain.
Run with the others: python3 -m unittest discover tests."""
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unicodedata
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_core import HOME, Client, FakeEngine, make_pdf  # noqa: E402  (importing it sets TRANSCAPP_HOME and VEULY_SECRETS)
from transcapp import engine, foldersync, kinds, library, materials, pages, paths, pipeline, search, server  # noqa: E402


def snapshot(folder):
    """Everything about a folder a program could have changed."""
    out = {}
    for d, subs, names in os.walk(folder, followlinks=False):
        for n in subs + names:
            p = os.path.join(d, n)
            st = os.lstat(p)
            body = hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() if os.path.isfile(p) and not os.path.islink(p) and st.st_mode & 0o400 else ""
            out[os.path.relpath(p, folder)] = (st.st_mode, st.st_size, st.st_mtime_ns, st.st_ino, body)
    out["."] = os.stat(folder).st_mtime_ns
    return out


class Base(unittest.TestCase):
    side = "course"

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="veuly-foldersync-"))
        self.addCleanup(self.clean)
        self.lib = library.Library(self.tmp / "library")
        self.pipe = types.SimpleNamespace(stopping=False, whisper=threading.Lock())
        self.sync = foldersync.Sync(self.lib, self.pipe)
        self.addCleanup(self.sync.close)
        self.slug = self.lib.create_course("Optics", "en", self.side)["slug"]
        self.folder = self.tmp / "My slides"
        self.folder.mkdir()
        patcher = mock.patch.object(foldersync, "QUIET_S", 0)      # most files here are written a moment before the look
        patcher.start()
        self.addCleanup(patcher.stop)

    def clean(self):
        for d, subs, names in os.walk(self.tmp):
            os.chmod(d, 0o755)
            for n in subs:
                if not os.path.islink(os.path.join(d, n)):
                    os.chmod(os.path.join(d, n), 0o755)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, rel, text="words", folder=None):
        f = (folder or self.folder) / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(text if isinstance(text, bytes) else text.encode())
        old = time.time() - 100 - (hash(rel) % 50)
        os.utime(f, (old, old))
        return f

    def link(self, folder=None, kind=""):
        self.sync.add(self.slug, str(folder or self.folder), kind)
        self.settle()
        return foldersync.folder_id(str(folder or self.folder))

    def settle(self):
        for _ in range(200):
            with self.sync._guard:
                running = list(self.sync._looks.values())
            if not running:
                return
            for t in running:
                t.join(5)
        self.fail("a look never ended")

    def info(self):
        return json.loads((self.lib.root / self.slug / "course.json").read_text(encoding="utf-8"))

    def course(self):
        return self.lib.course(self.slug)

    def names(self):
        return sorted(m["name"] for m in self.course()["materials"])

    def mat(self, name):
        return next(m for m in self.course()["materials"] if m["name"] == name)

    def entry(self, n=0):
        return self.course()["synced"][n]

    def trash(self):
        d = self.lib.root / ".trash"
        return sorted(p.name for p in d.iterdir()) if d.is_dir() else []

    def on_disk(self, name):
        return (self.lib.root / self.slug / "materials" / name).read_text(encoding="utf-8")


class WhatComesIn(Base):
    def test_a_new_file_comes_in_with_its_text_its_mark_and_the_search_follows(self):
        self.put("week1.txt", "Attenuation in decibels per kilometre")
        fid = self.link(kind="slides")
        self.assertEqual(self.names(), ["week1.txt"])
        m = self.mat("week1.txt")
        self.assertEqual((m["kind"], m["synced"], m["synced_from"]), ("slides", fid, "week1.txt"))
        self.assertEqual(self.lib.material_text(self.slug, "week1.txt"), "Attenuation in decibels per kilometre")
        e = self.entry()
        self.assertEqual((e["count"], e["kind"], e["name"], e["trouble"]), (1, "slides", "My slides", ""))
        self.assertTrue(e["last_look"])
        self.assertEqual([c["name"] for c in e["came"]], ["week1.txt"])
        record = self.info()["synced"][0]["files"]["week1.txt"]
        self.assertEqual((record["size"], record["state"], len(record["sum"])), (37, "in", 64))
        self.assertTrue(record["mtime"])
        found = search.Search(self.lib).find("decibels", course=self.slug)
        self.assertEqual([h["file"] if "file" in h else h.get("name") for h in found][:1], ["week1.txt"], found)

    def test_every_change_ends_in_refresh_maps(self):
        self.put("a.txt")
        with mock.patch.object(self.lib, "refresh_maps") as maps:
            self.link()
            n = maps.call_count
            self.assertGreaterEqual(n, 2, "the link, and the look that brought a file")
            self.sync.look(self.slug, self.entry()["id"])
            self.assertEqual(maps.call_count, n, "a look that changed nothing has nothing to refresh")
            (self.folder / "a.txt").unlink()
            self.sync.look(self.slug, self.entry()["id"])
            self.assertEqual(maps.call_count, n + 1, "a file marked gone")

    def test_a_changed_file_replaces_the_copy_and_the_old_copy_goes_to_the_trash(self):
        self.put("a.txt", "first version")
        fid = self.link()
        added = self.mat("a.txt")["added"]
        self.put("a.txt", "the second version")
        self.sync.look(self.slug, fid)
        self.assertEqual(self.names(), ["a.txt"], "one file, not two")
        self.assertEqual(self.on_disk("a.txt"), "the second version")
        self.assertEqual(self.lib.material_text(self.slug, "a.txt"), "the second version")
        kept = [n for n in self.trash() if n.endswith(f"{self.slug}_a.txt")]
        self.assertEqual(len(kept), 1, self.trash())
        self.assertEqual((self.lib.root / ".trash" / kept[0]).read_text(), "first version")
        m = self.mat("a.txt")
        self.assertEqual(m["added"], added)
        self.assertTrue(m["changed"])
        self.assertTrue(self.entry()["came"][-1]["changed"])

    def test_a_new_date_with_the_same_bytes_changes_nothing_but_the_record(self):
        f = self.put("a.txt", "same")
        fid = self.link()
        os.utime(f, (time.time() - 5, time.time() - 5))
        with mock.patch.object(materials, "read_file", side_effect=AssertionError("no second reading")):
            self.sync.look(self.slug, fid)
            self.assertEqual(self.trash(), [])
            self.assertEqual(self.info()["synced"][0]["files"]["a.txt"]["mtime"], f.stat().st_mtime_ns)
            self.sync.look(self.slug, fid)

    def test_an_unchanged_file_is_not_opened_again(self):
        self.put("a.txt")
        fid = self.link()
        with mock.patch.object(foldersync, "read_whole", side_effect=AssertionError("it did not change")):
            self.sync.look(self.slug, fid)
        self.assertEqual(self.entry()["failed"], [])

    def test_a_subfolder_comes_in_and_two_names_alike_are_told_apart(self):
        self.put("top.txt", "t")
        self.put("Week 1/notes.txt", "one")
        self.put("Week 2/notes.txt", "two")
        self.put("Week 3/deep/notes.txt", "three")
        self.link()
        self.assertEqual(self.names(), ["Week 2 - notes.txt", "deep - notes.txt", "notes.txt", "top.txt"])
        self.assertEqual(self.on_disk("notes.txt"), "one")
        self.assertEqual(self.mat("Week 2 - notes.txt")["synced_from"], "Week 2/notes.txt")

    def test_what_is_left_out(self):
        for rel in (".hidden.txt", "~$slides.docx", ".DS_Store", "photo.png", "archive.zip", ".git/config.md", "node_modules/x/readme.md"):
            self.put(rel)
        self.put("good.md", "kept")
        self.link()
        self.assertEqual(self.names(), ["good.md"])

    def test_depth_and_count_are_bounded_and_a_cut_listing_marks_nothing_gone(self):
        self.put("/".join(["d"] * foldersync.MAX_DEPTH + ["ok.txt"]), "in reach")
        self.put("/".join(["d"] * (foldersync.MAX_DEPTH + 1) + ["far.txt"]), "too deep")
        fid = self.link()
        self.assertEqual(self.names(), ["ok.txt"])
        self.assertTrue(self.entry()["partial"])
        self.put("a.txt")
        self.put("b.txt")
        self.sync.look(self.slug, fid)
        self.assertEqual(sorted(self.names()), ["a.txt", "b.txt", "ok.txt"])
        with mock.patch.object(foldersync, "MAX_FILES", 1):
            self.sync.look(self.slug, fid)
        self.assertFalse(any(m.get("gone") for m in self.course()["materials"]), "what a limit hid is not gone")

    def test_a_big_first_sync_goes_one_file_at_a_time_and_shows_how_far_it_is(self):
        for n in range(6):
            self.put(f"f{n}.txt", str(n))
        seen, at_once, running = [], [], [0]
        plain = materials.read_file

        def slow(path, *a):
            running[0] += 1
            at_once.append(running[0])
            state = self.sync.state(self.slug)["folders"][0]
            seen.append((state["looking"], state["now"]["done"], state["now"]["total"], state["now"]["name"]))
            try:
                return plain(path, *a)
            finally:
                running[0] -= 1
        with mock.patch.object(materials, "read_file", slow):
            self.link()
        self.assertEqual(len(self.names()), 6)
        self.assertEqual(max(at_once), 1)
        self.assertEqual([s[1:] for s in seen], [(n, 6, f"f{n}.txt") for n in range(6)])
        self.assertTrue(all(s[0] for s in seen))
        self.assertFalse(self.sync.state(self.slug)["looking"])
        self.assertEqual(self.lib.root.joinpath(".adding").is_dir() and list(self.lib.root.joinpath(".adding").iterdir()), [])

    def test_a_file_being_written_waits_for_the_next_look(self):
        f = self.put("a.txt")
        now = time.time()
        os.utime(f, (now, now))
        with mock.patch.object(foldersync, "QUIET_S", 60):
            self.link()
            self.assertEqual(self.names(), [])
            self.assertTrue(self.sync._again, "the next round comes soon")
        self.sync.look(self.slug, self.entry()["id"])
        self.assertEqual(self.names(), ["a.txt"])

    def test_a_file_that_changes_while_it_is_read_is_left_for_the_next_look(self):
        self.put("a.txt", "x" * 100)
        fid = self.link()
        self.assertEqual(self.names(), ["a.txt"])
        self.put("a.txt", "y" * 200)
        with mock.patch.object(foldersync, "read_whole", return_value=(None, 200, 1)):
            self.sync.look(self.slug, fid)
        self.assertEqual(self.on_disk("a.txt"), "x" * 100)
        self.sync.look(self.slug, fid)
        self.assertEqual(self.on_disk("a.txt"), "y" * 200)


class NeverLost(Base):
    def test_gone_from_the_folder_the_copy_stays_marked_and_comes_back(self):
        f = self.put("a.txt", "kept")
        self.put("b.txt", "other")
        fid = self.link()
        f.unlink()
        self.sync.look(self.slug, fid)
        self.assertEqual(self.names(), ["a.txt", "b.txt"])
        self.assertEqual(self.on_disk("a.txt"), "kept")
        self.assertTrue(self.mat("a.txt")["gone"])
        self.assertNotIn("gone", self.mat("b.txt"))
        self.put("a.txt", "kept")
        self.sync.look(self.slug, fid)
        self.assertNotIn("gone", self.mat("a.txt"))

    def test_a_folder_emptied_or_deleted_never_empties_the_course(self):
        self.put("a.txt", "kept")
        self.put("sub/b.txt", "kept too")
        fid = self.link()
        shutil.rmtree(self.folder / "sub")
        (self.folder / "a.txt").unlink()
        self.sync.look(self.slug, fid)
        self.assertEqual(self.names(), ["a.txt", "b.txt"])
        self.assertTrue(all(m["gone"] for m in self.course()["materials"]), "an emptied folder: both are marked")
        self.put("a.txt", "kept")
        self.put("sub/b.txt", "kept too")
        self.sync.look(self.slug, fid)
        self.assertFalse(any(m.get("gone") for m in self.course()["materials"]))
        before = self.info()["materials"]
        shutil.rmtree(self.folder)
        self.sync.look(self.slug, fid)
        self.assertEqual(self.info()["materials"], before, "a folder that is not there changes nothing, not even a mark")
        self.assertEqual(self.entry()["trouble"], "sync_gone")
        self.assertEqual(self.trash(), [])

    def test_a_folder_that_cannot_be_read_says_so_once_and_changes_nothing(self):
        if os.geteuid() == 0:
            self.skipTest("root reads everything")
        self.put("a.txt", "kept")
        fid = self.link()
        before = self.info()["materials"]
        self.folder.chmod(0)
        self.sync.look(self.slug, fid)
        first = self.entry()
        self.assertEqual(first["trouble"], "sync_denied")
        self.assertEqual(self.info()["materials"], before)
        with mock.patch.object(foldersync, "now_iso", return_value="2099-01-01T00:00:00"):
            self.sync.look(self.slug, fid)
        self.assertEqual(self.entry()["trouble_since"], first["trouble_since"], "said once: not a new trouble at every look")
        self.folder.chmod(0o755)
        self.sync.look(self.slug, fid)
        self.assertEqual((self.entry()["trouble"], self.entry()["trouble_since"]), ("", ""))

    def test_a_subfolder_that_cannot_be_read_marks_nothing_gone(self):
        if os.geteuid() == 0:
            self.skipTest("root reads everything")
        self.put("locked/a.txt", "kept")
        fid = self.link()
        (self.folder / "locked").chmod(0)
        self.sync.look(self.slug, fid)
        self.assertNotIn("gone", self.mat("a.txt"))
        self.assertEqual(self.entry()["trouble"], "")

    def test_remove_on_a_gone_file_forgets_it_and_on_a_present_one_keeps_it_out(self):
        f = self.put("gone.txt", "g")
        self.put("here.txt", "first")
        fid = self.link()
        f.unlink()
        self.sync.look(self.slug, fid)
        self.lib.remove_material(self.slug, "gone.txt")
        self.lib.remove_material(self.slug, "here.txt")
        self.assertEqual(self.names(), [])
        self.assertEqual(sorted(self.info()["synced"][0]["files"]), ["here.txt"])
        self.sync.look(self.slug, fid)
        self.assertEqual(self.names(), [], "pressing Remove is not undone by the next look")
        self.assertEqual(self.entry()["out"], ["here.txt"])
        now = time.time() - 3
        os.utime(self.folder / "here.txt", (now, now))
        self.sync.look(self.slug, fid)
        self.assertEqual(self.names(), [], "a new date on the same bytes")
        self.put("here.txt", "a new version")
        self.sync.look(self.slug, fid)
        self.assertEqual(self.names(), ["here.txt"], "a new version comes back")
        self.assertEqual(self.entry()["out"], [])

    def test_a_file_moved_inside_the_folder_stays_one_file(self):
        f = self.put("a.txt", "the same bytes")
        fid = self.link()
        (self.folder / "sub").mkdir()
        f.rename(self.folder / "sub" / "renamed.txt")
        self.sync.look(self.slug, fid)
        self.assertEqual(self.names(), ["a.txt"], "no second copy")
        m = self.mat("a.txt")
        self.assertEqual(m["synced_from"], "sub/renamed.txt")
        self.assertNotIn("gone", m)

    def test_a_copy_lost_from_the_course_comes_in_again(self):
        self.put("a.txt", "x")
        fid = self.link()
        (self.lib.root / self.slug / "materials" / "a.txt").unlink()
        self.sync.look(self.slug, fid)
        self.assertEqual(self.on_disk("a.txt"), "x")
        self.assertEqual(self.names(), ["a.txt"])


    def test_a_file_taken_off_the_list_by_hand_comes_back_once(self):
        self.put("a.txt", "x")
        fid = self.link()
        info = self.info()
        info["materials"] = []
        paths.write_atomic(self.lib.root / self.slug / "course.json", json.dumps(info))
        self.sync.look(self.slug, fid)
        self.assertEqual(self.names(), ["a.txt"])
        self.assertEqual(self.mat("a.txt")["synced"], fid)
        with mock.patch.object(foldersync, "read_whole", side_effect=AssertionError("it is back: nothing more to read")):
            self.sync.look(self.slug, fid)


class NeverTouched(Base):
    def test_nothing_in_the_folder_ever_changes(self):
        self.put("a.txt", "one")
        gone = self.put("sub/b.md", "two")
        self.put("~$lock.docx", "lock")
        self.put("broken.pdf", b"not a pdf at all")
        fid = self.link()
        self.put("a.txt", "one, changed")
        before = snapshot(self.folder)
        self.sync.look(self.slug, fid)
        self.assertEqual(snapshot(self.folder), before)
        gone.unlink()
        before = snapshot(self.folder)
        self.sync.look(self.slug, fid)
        self.lib.remove_material(self.slug, "a.txt")
        self.lib.remove_material(self.slug, "b.md")
        self.sync.look(self.slug, fid)
        self.sync.stop(self.slug, fid)
        self.assertEqual(snapshot(self.folder), before)

    def test_a_folder_it_may_not_write_to_works_all_the_same(self):
        self.put("a.txt")
        self.folder.chmod(0o555)
        self.link()
        self.assertEqual(self.names(), ["a.txt"])

    def test_the_code_opens_nothing_for_writing_in_the_folder(self):
        """The one place it opens a file of the folder is read_whole, read only and never through a link."""
        src = (ROOT / "transcapp" / "foldersync.py").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"\bos\.open\(", src)), 1)
        self.assertIn("os.O_RDONLY | getattr(os, \"O_NOFOLLOW\", 0)", src)
        for bad in ("shutil.", "os.remove", "os.unlink", ".unlink(", "write_text", "write_bytes", "open(seen"):
            self.assertNotIn(bad, src, bad)
        renames = re.findall(r"os\.rename\((.*?)\)$", src, re.M)
        self.assertEqual(renames, ['d / name, lib._trash_path(f"{slug}_{name}")'] * 2, "only a course's own copy moves")

    def test_a_symlink_is_never_followed_out_of_the_folder(self):
        outside = self.tmp / "outside"
        self.put("secret.txt", "private", folder=outside)
        self.put("inner/secret2.txt", "private too", folder=outside)
        (self.folder / "file-link.txt").symlink_to(outside / "secret.txt")
        (self.folder / "dir-link").symlink_to(outside / "inner")
        (self.folder / "loop").symlink_to(self.folder)
        self.put("real.txt", "mine")
        self.link()
        self.assertEqual(self.names(), ["real.txt"])
        self.assertEqual(self.entry()["failed"], [])

    def test_a_file_swapped_for_a_link_after_the_listing_is_not_read(self):
        f = self.put("a.txt", "mine")
        outside = self.put("secret.txt", "private", folder=self.tmp / "outside")
        f.unlink()
        f.symlink_to(outside)
        with self.assertRaises(OSError):
            foldersync.read_whole(str(f))

    def test_a_pipe_in_the_folder_does_not_hang_it(self):
        os.mkfifo(self.folder / "pipe.txt")
        self.put("a.txt")
        self.link()
        self.assertEqual(self.names(), ["a.txt"])

    def test_a_name_clash_with_a_hand_added_file_is_left_alone_and_listed(self):
        self.lib.add_material(self.slug, "Slides.pdf.txt", b"mine, added by hand", "slides")
        self.put("slides.PDF.TXT", "the folder's")     # another case: the same file on a Mac's disk
        fid = self.link()
        self.assertEqual(self.names(), ["Slides.pdf.txt"])
        self.assertEqual(self.on_disk("Slides.pdf.txt"), "mine, added by hand")
        self.assertNotIn("synced", self.mat("Slides.pdf.txt"))
        self.assertEqual(self.entry()["clashes"], ["slides.PDF.TXT"])
        self.assertEqual(self.trash(), [])
        with mock.patch.object(materials, "read_file", side_effect=AssertionError("a clash reads nothing")):
            self.sync.look(self.slug, fid)
        self.lib.remove_material(self.slug, "Slides.pdf.txt")
        self.sync.look(self.slug, fid)
        self.assertEqual(self.mat("slides.PDF.txt")["synced"], fid, "the hand-added one is gone: now it comes in")
        self.assertEqual(self.entry()["clashes"], [])

    def test_a_hand_added_file_arriving_during_the_reading_still_wins(self):
        self.put("a.txt", "the folder's")
        plain, once = materials.read_file, []

        def meanwhile(path, *a):
            if not once:
                once.append(1)
                self.lib.add_material(self.slug, "a.txt", b"by hand", "slides")
            return plain(path, *a)
        with mock.patch.object(materials, "read_file", meanwhile):
            self.link()
        self.assertEqual(self.on_disk("a.txt"), "by hand")
        self.assertEqual(self.entry()["clashes"], ["a.txt"])

    def test_a_file_over_the_limit_is_listed_never_read(self):
        self.put("big.txt", "x" * 100)
        self.put("small.txt", "y" * 10)
        with mock.patch.object(foldersync, "MAX_BYTES", 50), mock.patch.object(foldersync, "read_whole", wraps=foldersync.read_whole) as reads:
            fid = self.link()
            self.assertEqual(self.names(), ["small.txt"])
            self.assertEqual(self.entry()["big"], ["big.txt"])
            self.assertEqual([c.args[0].rsplit("/", 1)[-1] for c in reads.call_args_list], ["small.txt"])
            self.put("small.txt", "z" * 100)
            self.sync.look(self.slug, fid)
        self.assertEqual(self.on_disk("small.txt"), "y" * 10, "a version over the limit: the copy stays as it is")
        self.assertEqual(self.entry()["big"], ["big.txt", "small.txt"])

    def test_the_limit_is_that_of_a_file_added_by_hand(self):
        src = (ROOT / "transcapp" / "server.py").read_text(encoding="utf-8")
        self.assertIn("self.body(foldersync.MAX_BYTES)", src)
        self.assertEqual(foldersync.MAX_BYTES, 60 * server.MB)

    def test_a_cloud_placeholder_is_not_downloaded(self):
        self.put("cloud.txt", "x")
        plain = foldersync.listing

        def placeholder(root, *a):
            files, blind, cut, deep = plain(root)
            return {k: v._replace(online=True) for k, v in files.items()}, blind, cut, deep
        with mock.patch.object(foldersync, "listing", placeholder), mock.patch.object(foldersync, "read_whole", side_effect=AssertionError("it would download it")):
            fid = self.link()
        self.assertEqual(self.names(), [])
        self.assertEqual(self.entry()["online"], ["cloud.txt"])
        self.sync.look(self.slug, fid)
        self.assertEqual(self.names(), ["cloud.txt"], "on this computer now")
        self.assertEqual(self.entry()["online"], [])

    def test_a_broken_file_is_listed_and_read_again_only_when_it_changes(self):
        self.put("broken.pdf", b"not a pdf")
        fid = self.link()
        self.assertEqual(self.names(), [])
        self.assertEqual(self.entry()["failed"], [{"rel": "broken.pdf", "why": "material_failed"}])
        with mock.patch.object(foldersync, "read_whole", side_effect=AssertionError("it did not change")):
            self.sync.look(self.slug, fid)
        self.put("broken.pdf", b"still not a pdf")
        self.sync.look(self.slug, fid)
        self.assertEqual(self.entry()["failed"], [{"rel": "broken.pdf", "why": "material_failed"}])

    def test_a_file_that_cannot_be_opened_is_listed_and_tried_again(self):
        if os.geteuid() == 0:
            self.skipTest("root reads everything")
        f = self.put("a.txt", "x")
        f.chmod(0)
        fid = self.link()
        self.assertEqual(self.entry()["failed"], [{"rel": "a.txt", "why": "sync_file"}])
        f.chmod(0o644)
        self.sync.look(self.slug, fid)
        self.assertEqual(self.names(), ["a.txt"])
        self.assertEqual(self.entry()["failed"], [])


class Linking(Base):
    def refused(self, kind, path, what="", slug=None):
        with self.assertRaises(library.Refused) as e:
            self.sync.add(slug or self.slug, path, what)
        self.assertEqual(e.exception.kind, kind)

    def test_nothing_is_read_before_the_person_chose_it(self):
        """A course with a stand-in folder never looked at: no listing, no read, on a library with no link at all."""
        with mock.patch.object(foldersync, "listing", side_effect=AssertionError("it read a folder")), mock.patch("os.scandir", side_effect=AssertionError("it read a folder")):
            self.sync.start()
            self.assertIsNone(self.sync._loop)
            self.sync.before_note(self.slug)
            self.assertEqual(self.sync.page_opened(self.slug), {"folders": [], "looking": False})
        self.assertFalse([t for t in threading.enumerate() if t.name.startswith("foldersync") and t is self.sync._loop])

    def test_refusals(self):
        self.refused("sync_path", "")
        self.refused("sync_path", None)
        self.refused("sync_path", "relative/folder")
        self.refused("sync_path", str(self.put("a-file.txt")))
        self.refused("sync_gone", str(self.tmp / "nowhere"))
        # Through add only on folders of this test: the home folder is a stand-in, the data folder a temporary one, so a broken
        # check could never start a look on a real folder of this computer. The real ones go to `_too_wide` itself.
        self.assertTrue(self.sync._too_wide("/"))
        home = self.tmp / "home"
        home.mkdir()
        with mock.patch.dict(os.environ, {"HOME": str(home)}), mock.patch.object(pathlib.Path, "home", return_value=home):
            self.refused("sync_wide", str(home))
            self.refused("sync_wide", "~")
        self.refused("sync_wide", str(paths.data_dir()))
        self.refused("sync_wide", str(paths.data_dir() / "library"))
        self.refused("bad_request", str(self.folder), "unheard-of")
        self.refused("bad_request", str(self.folder), "docs")
        self.assertEqual(self.course()["synced"], [])

    def test_folders_are_limited_and_neither_twice_nor_inside_each_other(self):
        self.put("sub/a.txt")
        self.link()
        self.refused("sync_twice", str(self.folder))
        self.refused("sync_twice", str(self.folder) + "/")
        self.refused("sync_twice", str(self.folder / "sub"))
        link = self.tmp / "another name for it"
        link.symlink_to(self.folder)
        self.refused("sync_twice", str(link))
        for n in range(foldersync.MAX_FOLDERS - 1):
            (self.tmp / f"f{n}").mkdir()
            self.link(self.tmp / f"f{n}", "exams")
        (self.tmp / "one-more").mkdir()
        self.refused("sync_limit", str(self.tmp / "one-more"))
        self.assertEqual(len(self.course()["synced"]), foldersync.MAX_FOLDERS)

    def test_the_kind_is_asked_with_a_default_by_side(self):
        self.assertEqual(foldersync.default_kind("course"), "slides")
        self.assertEqual(foldersync.default_kind("project"), "docs")
        self.put("past.txt")
        self.link(kind="exams")
        self.assertEqual(self.mat("past.txt")["kind"], "exams")
        project = self.lib.create_course("Migration", "en", "project")["slug"]
        self.sync.add(project, str(self.tmp / "My slides"), "")
        self.settle()
        self.assertEqual(self.lib.course(project)["materials"][0]["kind"], "docs")
        self.refused("bad_request", str(self.folder), "slides", project)

    def test_a_tilde_is_the_home_folder(self):
        with mock.patch.dict(os.environ, {"HOME": str(self.tmp)}):
            self.put("a.txt")
            self.sync.add(self.slug, "~/My slides", "")
            self.settle()
        self.assertEqual(self.entry()["path"], str(self.folder))

    def test_two_folders_with_the_same_name_inside(self):
        other = self.tmp / "Other"
        self.put("x.txt", "one")
        self.put("x.txt", "two", folder=other)
        self.link()
        self.link(other)
        self.assertEqual(self.names(), ["x (2).txt", "x.txt"])
        self.assertEqual(self.lib.material_text(self.slug, "x (2).txt"), "two")

    def test_stop_leaves_the_files_and_reads_no_more_and_linking_again_takes_them_back(self):
        self.put("a.txt", "x")
        fid = self.link()
        self.sync.stop(self.slug, fid)
        self.assertEqual(self.course()["synced"], [])
        m = self.mat("a.txt")
        self.assertEqual((m.get("synced"), m["synced_was"], m["synced_from"]), (None, fid, "a.txt"))
        self.put("new.txt")
        self.assertEqual(self.sync._links(), [])
        with mock.patch.object(foldersync, "listing", side_effect=AssertionError("it read the folder")):
            self.sync.before_note(self.slug)
            self.sync.page_opened(self.slug, now=True)
        self.assertEqual(self.names(), ["a.txt"])
        self.assertTrue(self.on_disk("a.txt"))
        with self.assertRaises(library.NotFound):
            self.sync.stop(self.slug, fid)
        self.link()
        self.assertEqual(self.names(), ["a.txt", "new.txt"], "no clash, no second copy")
        self.assertEqual(self.mat("a.txt")["synced"], fid)
        self.assertEqual(self.entry()["clashes"], [])
        self.assertEqual(self.trash(), [], "the same bytes: nothing was replaced")

    def test_stop_while_a_file_is_being_read_adds_nothing(self):
        self.put("a.txt", "x")
        plain = materials.read_file

        def stop_first(path, *a):
            self.sync.stop(self.slug, foldersync.folder_id(str(self.folder)))
            return plain(path, *a)
        with mock.patch.object(materials, "read_file", stop_first):
            self.link()
        self.assertEqual(self.names(), [])
        self.assertEqual(self.course()["synced"], [])
        self.assertEqual(list((self.lib.root / ".adding").iterdir()), [])


class Damaged(Base):
    def test_a_damaged_course_json_comes_back_from_its_copy_with_its_links(self):
        self.put("a.txt", "x")
        fid = self.link()
        f = self.lib.root / self.slug / "course.json"
        f.write_text('{"name": "Optics", "synced": [{"pa', encoding="utf-8")
        self.lib.course(self.slug)
        self.assertEqual(self.entry()["id"], fid)
        with mock.patch.object(materials, "read_file", side_effect=AssertionError("nothing to read")):
            self.sync.look(self.slug, fid)
        self.assertEqual(self.names(), ["a.txt"])

    def test_a_course_json_edited_by_hand_is_cleaned_and_never_stops_anything(self):
        self.put("a.txt", "x")
        fid = self.link()
        for rubbish in ("a string", 3, {"a": 1}, [1, "x", None, {"path": 3}, {"path": "relative/one"}, {"path": "/nul\0here"}]):
            info = self.info()
            info["synced"] = rubbish
            paths.write_atomic(self.lib.root / self.slug / "course.json", json.dumps(info))
            self.assertEqual(self.course()["synced"], [], rubbish)
            self.assertEqual(self.sync.page_opened(self.slug)["folders"], [])
            self.assertEqual(self.names(), ["a.txt"])
        self.assertEqual(foldersync.clean([{"path": "/x", "kind": "exams", "id": "not-hex", "files": "no", "came": 4, "partial": "yes"}])[0],
                         {"id": foldersync.folder_id("/x"), "path": "/x", "kind": "exams", "added": "", "last_look": "", "trouble": "",
                          "trouble_since": "", "partial": False, "left": [], "left_more": 0, "blind": [], "blind_more": 0, "came": [], "files": {}})
        self.assertEqual(foldersync.clean([{"path": "/x", "kind": "docs"}])[0]["kind"], "slides", "not a kind of this side")
        self.assertEqual(len(foldersync.clean([{"path": f"/x{n}"} for n in range(9)])), foldersync.MAX_FOLDERS)
        self.assertEqual(len(foldersync.clean([{"path": "/x"}, {"path": "/x"}])), 1)
        files = {"ok.txt": {"state": "in", "name": "ok.txt", "size": 1, "mtime": 2}, "../../out.txt": {"state": "in", "name": "a"},
                 "/abs.txt": {"state": "in", "name": "a"}, "bad.txt": {"state": "?"}, "x.txt": {"state": "in", "name": "../../evil"}, "y.txt": "no"}
        self.assertEqual(sorted(foldersync.clean([{"path": "/x", "files": files}])[0]["files"]), ["ok.txt"])
        info = self.info()
        info["synced"] = [{"path": str(self.folder), "id": fid, "files": {"a.txt": {"state": "in", "name": "a.txt", "size": True, "mtime": "soon"}}}]
        paths.write_atomic(self.lib.root / self.slug / "course.json", json.dumps(info))
        self.sync.look(self.slug, fid)
        self.assertEqual(self.names(), ["a.txt"], "a record that says nothing: worked out again")
        self.assertEqual(self.on_disk("a.txt"), "x")

    def test_a_course_rebuilt_with_no_copy_keeps_what_it_could_read(self):
        self.put("a.txt", "x")
        fid = self.link()
        d = self.lib.root / self.slug
        (d / ".course.json.bak").unlink()
        good = (d / "course.json").read_text(encoding="utf-8")
        (d / "course.json").write_text(good[:-3], encoding="utf-8")
        course = self.lib.course(self.slug)
        self.assertEqual(course["repaired"]["how"], "rebuilt")
        self.assertEqual(self.names(), ["a.txt"])
        self.sync.page_opened(self.slug)
        self.settle()
        for e in self.course()["synced"]:
            self.assertEqual(e["id"], fid)
        self.assertEqual(self.on_disk("a.txt"), "x")

    def test_the_course_goes_while_it_looks(self):
        self.put("a.txt", "x")
        self.put("b.txt", "y")
        plain = materials.read_file

        def goes(path, *a):
            if (self.lib.root / self.slug).is_dir():
                self.lib.trash(self.slug)
            return plain(path, *a)
        with mock.patch.object(materials, "read_file", goes), mock.patch("traceback.print_exc", side_effect=AssertionError("it is no failure")):
            self.sync.add(self.slug, str(self.folder), "")
            self.settle()
        self.assertFalse((self.lib.root / self.slug).exists())


class HalfWay(Base):
    """A real process stopped dead half way through a first sync, at the two worst moments."""
    SCRIPT = """
import os, pathlib, sys
from transcapp import foldersync, library, materials, paths
foldersync.QUIET_S = 0
root, slug, fid, at, how = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5]
lib = library.Library(root)
n = [0]
if how == "reading":        # while a file's text is read: a copy in .adding/
    plain = materials.read_file
    def read(path, *a):
        n[0] += 1
        if n[0] == at:
            os._exit(9)
        return plain(path, *a)
    materials.read_file = read
else:                       # the file moved into materials/, its text and the list not written yet
    plain = paths.write_atomic
    def write(path, text, **k):
        if str(path).endswith(".txt.txt"):
            n[0] += 1
            if n[0] == at:
                os._exit(9)
        return plain(path, text, **k)
    paths.write_atomic = write
foldersync.Sync(lib).look(slug, fid)
os._exit(0)
"""

    def stop_half_way(self, how):
        for n in range(8):
            self.put(f"f{n}.txt", f"file {n}")
        with mock.patch.object(foldersync, "listing", return_value=({}, [], False, False)):
            fid = self.link()
        env = {**os.environ, "PYTHONPATH": str(ROOT), "TRANSCAPP_HOME": HOME}
        done = subprocess.run([sys.executable, "-B", "-c", self.SCRIPT, str(self.lib.root), self.slug, fid, "4", how], env=env, cwd=str(ROOT), timeout=60)
        self.assertEqual(done.returncode, 9, "it stopped dead half way")
        return fid

    def check_carries_on(self, fid):
        before = set(self.names())
        self.assertEqual(before, {f"f{n}.txt" for n in range(3)}, "the first three are whole, the rest not there")
        for name in before:
            self.assertEqual(self.lib.material_text(self.slug, name), self.on_disk(name))
        lib = library.Library(self.lib.root)       # the app started again
        sync = foldersync.Sync(lib, self.pipe)
        self.addCleanup(sync.close)
        reads = []
        plain = materials.read_file
        with mock.patch.object(materials, "read_file", lambda p, *a: (reads.append(p), plain(p, *a))[1]):
            sync.look(self.slug, fid)
        self.assertEqual(len(reads), 5, "only what was missing")
        listed = self.names()
        self.assertEqual(listed, [f"f{n}.txt" for n in range(8)], "each once, with no (2)")
        files = {p.name for p in (self.lib.root / self.slug / "materials").iterdir() if p.is_file()}
        self.assertEqual(files, set(listed), "nothing on disk the course does not list")
        for n in range(8):
            self.assertEqual(self.lib.material_text(self.slug, f"f{n}.txt"), f"file {n}")

    def test_stopped_while_reading_a_file(self):
        self.check_carries_on(self.stop_half_way("reading"))

    def test_stopped_between_moving_a_file_in_and_saving_the_list(self):
        fid = self.stop_half_way("moving")
        (self.lib.root / self.slug / "materials" / "f3.txt").is_file() or self.fail("this stop leaves the file on disk")
        self.check_carries_on(fid)


class Together(Base):
    def test_two_looks_at_once_are_one_look(self):
        for n in range(4):
            self.put(f"f{n}.txt", str(n))
        with mock.patch.object(foldersync, "listing", return_value=({}, [], False, False)):
            fid = self.link()
        hold, entered = threading.Event(), threading.Event()
        plain = materials.read_file
        reads = []

        def held(path, *a):
            entered.set()
            hold.wait(10)
            reads.append(path)
            return plain(path, *a)
        with mock.patch.object(materials, "read_file", held):
            first = self.sync.kick(self.slug, fid)
            self.assertTrue(entered.wait(10))
            self.assertIs(self.sync.kick(self.slug, fid), first, "the second gets the look going on")
            self.assertIs(self.sync.kick(self.slug, fid, soon=True), first)
            self.assertTrue(self.sync.page_opened(self.slug, now=True)["looking"])
            hold.set()
            first.join(20)
        self.assertEqual(self.names(), [f"f{n}.txt" for n in range(4)])
        self.assertEqual(len(reads), 4)

    def test_opening_the_page_again_soon_does_not_look_again(self):
        self.put("a.txt")
        fid = self.link()
        with mock.patch.object(self.sync, "kick") as kick:
            self.sync.page_opened(self.slug)
            kick.assert_not_called()
            self.sync.page_opened(self.slug, now=True)
            kick.assert_called_once_with(self.slug, fid, again=True)
            with mock.patch.object(foldersync, "LOOK_AGAIN_S", 0):
                self.sync.page_opened(self.slug)
            self.assertEqual(kick.call_count, 2)

    def test_rounds_look_by_themselves_and_end_when_nothing_is_linked(self):
        self.put("a.txt")
        with mock.patch.object(foldersync, "EVERY_S", 0.05), mock.patch.object(foldersync, "START_DELAY_S", 0.01):
            fid = self.link()
            self.put("later.txt")
            for _ in range(200):
                if "later.txt" in self.names():
                    break
                time.sleep(0.05)
            self.assertEqual(self.names(), ["a.txt", "later.txt"])
            self.settle()
            self.sync.stop(self.slug, fid)
            loop = self.sync._loop          # None when the loop has already ended, which is what this test wants
            if loop:
                loop.join(10)
                self.assertFalse(loop.is_alive())
            self.assertIsNone(self.sync._loop)

    def test_it_starts_with_the_server_and_looks_at_a_folder_linked_before(self):
        self.put("a.txt")
        with mock.patch.object(foldersync, "listing", return_value=({}, [], False, False)):
            self.link()
        sync = foldersync.Sync(self.lib, self.pipe)
        self.addCleanup(sync.close)
        with mock.patch.object(foldersync, "START_DELAY_S", 0.01):
            sync.start()
            for _ in range(200):
                if self.names():
                    break
                time.sleep(0.05)
        self.assertEqual(self.names(), ["a.txt"])


class Recording(Base):
    def recording(self):
        cid = self.lib.create_class(self.slug, "Now")["id"]
        return cid, self.lib.start_recording(self.slug, cid)["session"]

    def test_no_look_while_a_class_records_and_the_class_is_not_disturbed(self):
        self.put("a.txt", "x")
        with mock.patch.object(foldersync, "listing", return_value=({}, [], False, False)):
            fid = self.link()
        cid, session = self.recording()
        self.assertTrue(self.lib.recording_now())
        meta = self.lib.meta(self.slug, cid)
        course = (self.lib.root / self.slug / "course.json").read_bytes()
        with mock.patch.object(foldersync, "listing", side_effect=AssertionError("not even a listing")), mock.patch.object(foldersync, "read_whole", side_effect=AssertionError("no reading")):
            self.sync.look(self.slug, fid)
            self.sync.before_note(self.slug, 0.5)
            self.settle()
        self.assertEqual((self.lib.root / self.slug / "course.json").read_bytes(), course, "not a byte written")
        self.assertEqual(self.lib.meta(self.slug, cid), meta)
        state = self.sync.state(self.slug)["folders"][0]
        self.assertEqual((state["paused"]["why"], state["looking"]), ("recording", False))
        self.assertTrue(self.sync._again, "it tries again soon")
        self.lib.release_recording(self.slug, cid, session)
        self.assertFalse(self.lib.recording_now())
        self.sync.look(self.slug, fid)
        self.assertEqual(self.names(), ["a.txt"])
        self.assertIsNone(self.sync.state(self.slug)["folders"][0]["paused"])

    def test_a_recording_that_starts_during_a_big_sync_stops_it_at_the_next_file(self):
        for n in range(5):
            self.put(f"f{n}.txt", str(n))
        with mock.patch.object(foldersync, "listing", return_value=({}, [], False, False)):
            fid = self.link()
        plain, reads, held = materials.read_file, [], {}

        def read(path, *a):
            reads.append(path)
            if len(reads) == 2:
                held.update(zip(("cid", "session"), self.recording()))
            return plain(path, *a)
        with mock.patch.object(materials, "read_file", read):
            self.sync.look(self.slug, fid)
        self.assertEqual(len(reads), 2)
        self.assertEqual(self.names(), ["f0.txt", "f1.txt"])
        self.assertEqual(self.sync.state(self.slug)["folders"][0]["paused"], {"why": "recording", "left": 3})
        self.lib.release_recording(self.slug, held["cid"], held["session"])
        self.sync.look(self.slug, fid)
        self.assertEqual(self.names(), [f"f{n}.txt" for n in range(5)])

    def test_a_recording_a_page_stopped_being_heard_from_holds_nothing_back(self):
        cid, session = self.recording()
        self.lib.update_meta(self.slug, cid, rec_seen=time.time() - library.LEASE_S - 1)
        self.assertFalse(self.lib.recording_now())

    def test_it_gives_way_to_whisper_but_not_when_a_note_waits(self):
        self.put("a.txt", "x")
        with mock.patch.object(foldersync, "listing", return_value=({}, [], False, False)):
            fid = self.link()
        with self.pipe.whisper:
            self.sync.look(self.slug, fid)
            self.assertEqual(self.names(), [])
            self.assertEqual(self.sync.state(self.slug)["folders"][0]["paused"]["why"], "transcribing")
            self.sync.before_note(self.slug)
            self.assertEqual(self.names(), ["a.txt"])

    def test_it_stops_at_quit(self):
        self.put("a.txt", "x")
        with mock.patch.object(foldersync, "listing", return_value=({}, [], False, False)):
            fid = self.link()
        self.pipe.stopping = True
        self.sync.look(self.slug, fid)
        self.assertEqual(self.names(), [])


class Finish(unittest.TestCase):
    """The whole way, through the server and a stand-in engine."""

    @classmethod
    def setUpClass(cls):
        paths.save_settings({"engine": "api", "setup_done": True})
        cls.fake = FakeEngine()
        cls.httpd, cls.app = server.serve(ROOT / "web", 0, engine_factory=lambda settings: cls.fake)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.c = Client(cls.app)
        cls.c.call("GET", f"/?t={cls.app.token}")

    @classmethod
    def tearDownClass(cls):
        cls.app.sync.close()
        cls.httpd.shutdown()

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="veuly-foldersync-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.folder = self.tmp / "slides"
        self.folder.mkdir()
        self.slug = self.c.call("POST", "/api/courses", {"name": f"Course {self.tmp.name}"})[1]["slug"]
        self.addCleanup(lambda: self.c.call("DELETE", f"/api/courses/{self.slug}"))
        patcher = mock.patch.object(foldersync, "QUIET_S", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def link(self):
        status, course = self.c.call("POST", f"/api/courses/{self.slug}/synced", {"path": str(self.folder), "kind": "syllabus"})     # a syllabus always goes with a note
        self.assertEqual(status, 200, course)
        for _ in range(100):
            if not self.c.call("GET", f"/api/courses/{self.slug}/synced")[1]["looking"]:
                break
            time.sleep(0.05)
        return course["synced"][0]["id"]

    def note(self):
        cid = self.c.call("POST", f"/api/courses/{self.slug}/classes", {"title": "One"})[1]["id"]
        lib = self.app.lib
        lib.write_text(self.slug, cid, "transcript", "1\n00:00:00,000 --> 00:00:02,000\nattenuation in the fibre\n")
        lib.update_meta(self.slug, cid, status="error")
        self.fake.requests.clear()
        started = time.monotonic()
        self.assertEqual(self.c.call("POST", f"/api/classes/{self.slug}/{cid}/retry", {})[0], 200)
        for _ in range(300):
            data = self.c.call("GET", f"/api/classes/{self.slug}/{cid}")[1]
            if data["status"] in ("done", "error"):
                return data, time.monotonic() - started
            time.sleep(0.05)
        self.fail("the note was never written")

    def test_a_new_file_reaches_the_note_written_right_after(self):
        fid = self.link()
        (self.folder / "deck.txt").write_text("attenuation attenuation in the fibre, the new slides")
        old = time.time() - 50
        os.utime(self.folder / "deck.txt", (old, old))
        data, _ = self.note()
        self.assertEqual(data["status"], "done")
        self.assertIn("deck.txt", [n for n, _ in self.fake.requests[0].materials], "looked at before the files were read")
        self.assertTrue(fid)

    def test_a_slow_folder_does_not_delay_the_note(self):
        (self.folder / "deck.txt").write_text("attenuation in the fibre")
        old = time.time() - 50
        os.utime(self.folder / "deck.txt", (old, old))
        fid = self.link()
        plain, slow = foldersync.listing, threading.Event()

        def hung(root, *a):
            slow.wait(30)
            return plain(root)
        (self.folder / "later.txt").write_text("arrives after")
        os.utime(self.folder / "later.txt", (old, old))
        with mock.patch.object(foldersync, "listing", hung), mock.patch.object(foldersync, "FINISH_WAIT_S", 0.3):
            data, took = self.note()
            self.assertEqual(data["status"], "done")
            self.assertLess(took, 10, "the look goes on by itself")
            self.assertEqual([n for n, _ in self.fake.requests[0].materials], ["deck.txt"], "it goes on with what is there")
            self.assertTrue(self.c.call("GET", f"/api/courses/{self.slug}/synced")[1]["looking"], "still looking, in its own thread")
            slow.set()
            for _ in range(200):
                if not self.c.call("GET", f"/api/courses/{self.slug}/synced")[1]["looking"]:
                    break
                time.sleep(0.05)
        self.assertIn("later.txt", [m["name"] for m in self.app.lib.course(self.slug)["materials"]])
        self.assertTrue(fid)

    def test_a_failure_of_the_look_is_never_the_notes(self):
        self.link()
        with mock.patch.object(self.app.sync, "before_note", side_effect=RuntimeError("what a folder may do")):
            self.app.pipeline.before_files = self.app.sync.before_note
            try:
                data, _ = self.note()
            finally:
                self.app.pipeline.before_files = self.app.sync.__class__.before_note.__get__(self.app.sync)
        self.assertEqual(data["status"], "done")

    def test_with_no_folder_a_note_starts_no_thread(self):
        with mock.patch.object(foldersync.Sync, "kick", side_effect=AssertionError("no folder, no look")):
            data, _ = self.note()
        self.assertEqual(data["status"], "done")

    def test_the_routes(self):
        stranger = Client(self.app)
        for verb, path in (("GET", "synced"), ("POST", "synced"), ("POST", "synced/look"), ("DELETE", "synced/abcdefabcdef")):
            self.assertEqual(stranger.call(verb, f"/api/courses/{self.slug}/{path}", {})[0], 403, path)
        for body, kind in (({"path": ""}, "sync_path"), ({"path": str(self.tmp / "none")}, "sync_gone"), ({"path": str(paths.data_dir())}, "sync_wide"),
                           ({"path": str(self.folder), "kind": "x"}, "bad_request")):
            status, answer = self.c.call("POST", f"/api/courses/{self.slug}/synced", body)
            self.assertEqual((status, answer["kind"]), (400, kind), answer)
        fid = self.link()
        status, answer = self.c.call("POST", f"/api/courses/{self.slug}/synced", {"path": str(self.folder)})
        self.assertEqual((status, answer["kind"]), (400, "sync_twice"))
        self.assertEqual(self.c.call("POST", "/api/courses/nope/synced/look", {})[0], 404)
        state = self.c.call("GET", "/api/state")[1]
        listed = next(c for c in state["courses"] if c["slug"] == self.slug)["synced"][0]
        self.assertNotIn("files", listed, "no table of every file in the course list")
        self.assertEqual(self.c.call("POST", f"/api/courses/{self.slug}/synced/look", {"now": True})[0], 200)
        status, course = self.c.call("DELETE", f"/api/courses/{self.slug}/synced/{fid}")
        self.assertEqual((status, course["synced"]), (200, []))
        self.assertEqual(self.c.call("DELETE", f"/api/courses/{self.slug}/synced/{fid}")[0], 404)

    def test_a_course_with_no_link_has_no_rounds(self):
        httpd, app = server.serve(ROOT / "web", 0, engine_factory=lambda settings: FakeEngine())
        try:
            app.sync._links = mock.Mock(side_effect=AssertionError("started once only"))
            time.sleep(0.05)
            self.assertIsNone(app.sync._loop)
        finally:
            httpd.server_close()


class WithAPageInTheNote(Base):
    """A look that replaces a course's PDF while Claude writes (slideimg, transcapp/pages.py): the note Claude wrote from the old
    text is written whatever happens, and a page number it read there never draws a page of the new version."""

    DECK = "deck.pdf"
    ANSWER = f'# Attenuation\n\nText.\n\n<<page "{DECK}" 2 | The curve>>\n\nMore.\n'

    def setUp(self):
        super().setUp()
        if not (paths.find_program("pdftoppm") or sys.platform == "darwin"):
            self.skipTest("nothing here draws a PDF")
        paths.save_settings({"engine": "api"})
        self.put(self.DECK, make_pdf(["Agenda of the class", "Attenuation of the fibre", "Summary of the class"]))
        self.fid = self.link()
        self.cid = self.lib.create_class(self.slug)["id"]
        self.lib.write_text(self.slug, self.cid, "transcript", "1\n00:00:00,000 --> 00:00:04,000\nAttenuation of the fibre, agenda and summary\n")
        self.lib.update_meta(self.slug, self.cid, materials=[self.DECK])

    def new_version(self, pages):
        self.put(self.DECK, make_pdf(pages))
        self.sync.look(self.slug, self.fid)
        self.settle()

    def write(self, during=lambda: None):
        test = self

        class Writes(FakeEngine):
            def final_note(self, req):
                self.requests.append(req)
                during()
                return engine.NoteResult(test.ANSWER, "fake", "fake-1", 0.0, {})
        eng = Writes()
        pipe = pipeline.Pipeline(self.lib, lambda settings: eng)
        pipe.before_files = self.sync.before_note
        pipe._run(self.slug, self.cid, "write")
        return eng.requests[0], self.lib.read_text(self.slug, self.cid, "note"), self.lib.meta(self.slug, self.cid)

    def test_a_file_left_as_it_was_shows_its_page(self):
        req, note, meta = self.write()
        self.assertIn("Attenuation of the fibre", dict(req.materials)[self.DECK])
        self.assertEqual(note.count("!["), 1, note)
        self.assertEqual(meta["status"], "done")

    def test_a_look_that_finds_nothing_new_changes_nothing(self):
        req, note, meta = self.write(lambda: self.sync.look(self.slug, self.fid))
        self.assertEqual(note.count("!["), 1, note)

    def test_a_new_version_with_other_pages_draws_no_page_claude_never_read(self):
        before = self.lib.material_text(self.slug, self.DECK)
        req, note, meta = self.write(lambda: self.new_version(["Quite another deck", "A page Claude never read", "And another"]))
        self.assertNotEqual(self.lib.material_text(self.slug, self.DECK), before, "the new version came in")
        self.assertIn("Attenuation of the fibre", dict(req.materials)[self.DECK], "Claude read the old one")
        self.assertEqual(note, "# Attenuation\n\nText.\n\nMore.\n")
        self.assertEqual(meta["status"], "done")
        self.assertFalse(meta.get("page_pictures"))

    def test_a_shorter_new_version_loses_the_mark_not_the_note(self):
        req, note, meta = self.write(lambda: self.new_version(["Only one page now"]))
        self.assertEqual(note, "# Attenuation\n\nText.\n\nMore.\n")
        self.assertEqual(meta["status"], "done")

    def test_replaced_after_its_text_was_read_and_before_it_is_surveyed(self):
        plain = pages.survey

        def swapped(path, *a):
            if not getattr(swapped, "done", False):
                swapped.done = True
                self.new_version(["Quite another deck", "A page Claude never read", "And another"])
            return plain(path, *a)
        with mock.patch.object(pages, "survey", swapped):
            req, note, meta = self.write()
        self.assertTrue(swapped.done)
        self.assertIn("Attenuation of the fibre", dict(req.materials)[self.DECK], "it read the old text")
        self.assertEqual(note, "# Attenuation\n\nText.\n\nMore.\n")

    def test_the_moment_the_file_is_away_between_its_rename_and_the_new_one(self):
        """Inside lib.lock, but a note holds no lock: the survey or the drawing can find nothing there."""
        away = self.lib.course_dir(self.slug) / "materials" / self.DECK
        aside = self.tmp / "aside.pdf"

        def gone():
            os.rename(away, aside)
        try:
            req, note, meta = self.write(gone)
        finally:
            os.rename(aside, away)
        self.assertEqual(note, "# Attenuation\n\nText.\n\nMore.\n")
        self.assertEqual(meta["status"], "done")

    def test_a_file_replaced_by_hand_is_treated_the_same(self):
        def by_hand():
            self.lib.remove_material(self.slug, self.DECK)
            self.lib.add_material(self.slug, self.DECK, make_pdf(["Quite another deck", "A page Claude never read"]), "slides")
        req, note, meta = self.write(by_hand)
        self.assertEqual(note, "# Attenuation\n\nText.\n\nMore.\n")
        self.assertEqual(meta["status"], "done")


class Texts(unittest.TestCase):
    def test_every_refusal_has_its_text_in_three_languages_and_the_work_twins(self):
        app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        i18n = (ROOT / "web" / "i18n.js").read_text(encoding="utf-8")
        help_kinds = re.search(r"const HELP_KINDS = \[(.*?)\];", app, re.S).group(1)
        src = (ROOT / "transcapp" / "foldersync.py").read_text(encoding="utf-8")
        for kind in set(re.findall(r'"(sync_[a-z]+)"', src)):
            self.assertIn(f"'{kind}'", help_kinds)
            self.assertEqual(len(re.findall(rf"^\s*h_{kind}:", i18n, re.M)), 3, kind)
        for key in ("sync_blurb__work", "sync_stop_body__work", "info_sync_text__work", "h_sync_wide__work", "mac_sync_panel", "mac_sync_choose", "mac_purpose_documents"):
            self.assertEqual(len(re.findall(rf"^\s*{key}:", i18n, re.M)), 3, key)

    def test_what_the_page_assumes_is_what_the_server_does(self):
        js = (ROOT / "web" / "foldersync.js").read_text(encoding="utf-8")
        project, course = re.search(r"course\.kind === 'project' \? (\[[^\]]+\]) : (\[[^\]]+\])", js).groups()
        self.assertEqual(sorted(re.findall(r"'([a-z]+)'", project)), sorted(kinds.MATERIALS["project"]))
        self.assertEqual(sorted(re.findall(r"'([a-z]+)'", course)), sorted(kinds.MATERIALS["course"]))
        self.assertIn(f"folders.length >= {foldersync.MAX_FOLDERS}", js)
        for lang_text in re.findall(r"^\s*h_sync_limit: '(.*)',$", (ROOT / "web" / "i18n.js").read_text(encoding="utf-8"), re.M):
            self.assertIn(str(foldersync.MAX_FOLDERS), lang_text)

    def test_the_page_uses_only_texts_that_exist(self):
        js = (ROOT / "web" / "foldersync.js").read_text(encoding="utf-8")
        i18n = (ROOT / "web" / "i18n.js").read_text(encoding="utf-8")
        for key in set(re.findall(r"\bt\('([a-z_]+)'", js)):
            self.assertEqual(len(re.findall(rf"^\s*{key}:|[ ,]{key}: '", i18n, re.M)), 3, key)


class ReviewFixes(Base):
    """What an independent review found. Spellings of too-wide folders are checked with `_too_wide` itself and with the look
    held off: a test must never list a real folder of this computer, even when the check it tests is broken."""

    def recording(self):
        cid = self.lib.create_class(self.slug, "Now")["id"]
        return cid, self.lib.start_recording(self.slug, cid)["session"]

    def write(self, rel, data, age=100):
        f = self.folder / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
        os.utime(f, (time.time() - age, time.time() - age))

    def look(self, fid, **k):
        self.sync.look(self.slug, fid, **k)
        self.settle()

    def record(self, fid, rel):
        return next(e for e in self.info()["synced"] if e["id"] == fid)["files"][rel]

    # ---- a good copy is never replaced by nothing
    def test_a_zero_byte_or_unfilled_version_never_replaces_a_good_copy(self):
        self.write("a.txt", b"the good copy " * 40)
        fid = self.link()
        for data, age in ((b"", 90), (b"\0" * 9000, 80), (b"head of the file " + b"\0" * 9000, 70)):
            self.write("a.txt", data, age)
            with mock.patch.object(materials, "read_file", side_effect=AssertionError("nothing to read")):
                self.look(fid)
            self.assertTrue(self.on_disk("a.txt").startswith("the good copy"), data[:8])
            self.assertTrue(self.lib.material_text(self.slug, "a.txt").startswith("the good copy"))
            self.assertEqual(self.trash(), [], "nothing was replaced")
            self.assertEqual(self.entry()["held"], [{"rel": "a.txt", "why": "sync_empty"}])
            self.assertEqual(self.entry()["count"], 1)
        self.write("a.txt", b"the whole new version", 60)
        self.look(fid)
        self.assertEqual(self.on_disk("a.txt"), "the whole new version")
        self.assertEqual(self.entry()["held"], [])
        self.assertEqual([(self.lib.root / ".trash" / t).read_text()[:13] for t in self.trash()], ["the good copy"], "the old copy can be got back")

    def test_a_new_empty_file_does_not_come_in_and_is_listed(self):
        self.write("empty.txt", b"")
        self.link()
        self.assertEqual(self.names(), [])
        self.assertEqual(self.entry()["failed"], [{"rel": "empty.txt", "why": "sync_empty"}])

    def test_a_version_with_no_text_does_not_replace_a_copy_with_text(self):
        self.write("a.txt", b"the good copy")
        fid = self.link()
        self.write("a.txt", b"another version", 50)
        with mock.patch.object(materials, "read_file", return_value=("  \n", 3)):
            self.look(fid)
        self.assertEqual(self.on_disk("a.txt"), "the good copy")
        self.assertEqual(self.entry()["held"], [{"rel": "a.txt", "why": "sync_no_text"}])

    def test_a_broken_version_keeps_the_record_of_the_good_copy(self):
        good = make_pdf(["optical networks words words"])
        self.write("a.pdf", good)
        fid = self.link()
        self.write("a.pdf", good[:len(good) // 2], 50)
        self.look(fid)
        self.assertEqual((self.lib.root / self.slug / "materials" / "a.pdf").read_bytes(), good)
        self.assertEqual(self.record(fid, "a.pdf")["state"], "in", "the record of what the course has is not lost")
        self.assertEqual(self.entry()["count"], 1)
        self.assertEqual(self.entry()["held"], [{"rel": "a.pdf", "why": "material_failed"}])
        with mock.patch.object(materials, "read_file", side_effect=AssertionError("tried once, until it changes")):
            self.look(fid)
        (self.folder / "a.pdf").unlink()
        self.look(fid)
        self.assertTrue(self.mat("a.pdf")["gone"], "gone from the folder: said, as for any file")

    def test_going_back_to_the_good_version_clears_what_was_said(self):
        self.write("a.txt", b"the good copy")
        fid = self.link()
        stat = os.stat(self.folder / "a.txt")
        self.write("a.txt", b"", 50)
        self.look(fid)
        self.write("a.txt", b"the good copy")
        os.utime(self.folder / "a.txt", ns=(stat.st_mtime_ns, stat.st_mtime_ns))
        self.look(fid)
        self.assertEqual(self.entry()["held"], [])
        self.assertEqual(self.trash(), [])

    def test_a_file_a_sync_client_preallocated_is_not_mistaken_for_a_finished_one(self):
        self.assertTrue(foldersync._unfinished(b""))
        self.assertTrue(foldersync._unfinished(b"\0"))
        self.assertTrue(foldersync._unfinished(b"x" * 10 + b"\0" * foldersync.TAIL))
        self.assertFalse(foldersync._unfinished(b"x"))
        self.assertFalse(foldersync._unfinished(b"\0" * 9000 + b"end"))

    # ---- what is said about what did not come in
    def test_quit_stopping_a_reader_says_nothing_about_the_file(self):
        self.write("a.txt", b"words")
        with mock.patch.object(foldersync, "listing", return_value=({}, [], False, False)):
            fid = self.link()

        def stopped(*a):
            self.pipe.stopping = True
            raise materials.MaterialError("This file could not be read.")
        with mock.patch.object(materials, "read_file", stopped):
            self.sync.look(self.slug, fid)
        self.pipe.stopping = False
        self.assertNotIn("a.txt", next(e for e in self.info()["synced"] if e["id"] == fid)["files"])
        self.look(fid)
        self.assertEqual(self.names(), ["a.txt"], "the next start brings it")

    def test_look_now_tries_again_what_could_not_be_read(self):
        self.write("a.pptx", b"PK not yet")
        fid = self.link()
        self.assertEqual(self.entry()["failed"][0]["rel"], "a.pptx")
        with mock.patch.object(materials, "read_file", return_value=("deck text", 2)) as read:
            self.look(fid)
            self.assertEqual(read.call_count, 0, "a look by itself does not")
            self.sync.page_opened(self.slug, True)
            self.settle()
            self.assertEqual(read.call_count, 1)
        self.assertEqual(self.names(), ["a.pptx"])

    def test_limits_say_what_they_left_out(self):
        for n in range(6):
            self.write(f"f{n}.txt", b"words")
        self.write("a-sub/later.txt", b"words")
        with mock.patch.object(foldersync, "MAX_FILES", 3):
            fid = self.link()
        e = self.entry()
        self.assertTrue(e["partial"])
        self.assertEqual(e["count"], 3)
        self.assertEqual(sorted(e["left"]), [".", "a-sub"], "the top's other files, and a subfolder not reached")
        self.look(fid)        # the limit lifted: nothing was lost, and it no longer says so
        self.assertEqual((self.entry()["partial"], self.entry()["left"], self.entry()["count"]), (False, [], 7))

    def test_exactly_the_limit_is_not_called_partial(self):
        for n in range(3):
            self.write(f"f{n}.txt", b"words")
        self.write("other.bin", b"not one of ours")
        with mock.patch.object(foldersync, "MAX_FILES", 3):
            self.link()
        self.assertFalse(self.entry()["partial"])

    def test_a_level_too_deep_is_named(self):
        self.write("/".join(f"d{n}" for n in range(8)) + "/deep.txt", b"words")
        self.link()
        self.assertEqual(self.entry()["left"], ["/".join(f"d{n}" for n in range(7))])

    def test_long_lists_say_how_many_they_leave_out(self):
        for n in range(foldersync.LISTED + 7):
            self.lib.add_material(self.slug, f"f{n:03}.txt", b"by hand", "slides")
            self.write(f"f{n:03}.txt", b"from the folder")
        self.link()
        e = self.entry()
        self.assertEqual((len(e["clashes"]), e["more"]["clashes"]), (foldersync.LISTED, 7))

    @unittest.skipIf(os.geteuid() == 0, "root reads everything")
    def test_a_subfolder_that_cannot_be_read_is_named(self):
        self.write("a.txt", b"words")
        self.write("closed/b.txt", b"words")
        os.chmod(self.folder / "closed", 0)
        self.link()
        self.assertEqual((self.entry()["blind"], self.entry()["trouble"], self.names()), (["closed"], "", ["a.txt"]))

    # ---- the same folder, however it is spelled
    def test_the_same_folder_under_another_spelling_is_not_linked_twice(self):
        spelled = unicodedata.normalize("NFD", "Introducción")
        d = self.tmp / spelled
        d.mkdir()
        self.write("x.txt", b"x")
        (d / "x.txt").write_text("words")
        os.utime(d / "x.txt", (time.time() - 100, time.time() - 100))
        self.sync.add(self.slug, str(d), "")
        self.settle()
        self.assertEqual(foldersync.folder_id(str(d)), foldersync.folder_id(unicodedata.normalize("NFC", str(d))))
        same_disk = os.path.exists(str(d.parent / d.name.upper()))
        for other in [unicodedata.normalize("NFC", str(d)), str(d) + "/"] + ([str(d.parent / d.name.upper())] if same_disk else []):
            if os.path.exists(other):
                with self.assertRaises(library.Refused) as e:
                    self.sync.add(self.slug, other, "")
                self.assertEqual(e.exception.kind, "sync_twice", ascii(other))
        self.assertEqual(self.names(), ["x.txt"])

    def test_too_wide_goes_by_the_disk_not_by_the_spelling(self):
        """_too_wide only: the look must never start on these, so add() is not called with them."""
        home = pathlib.Path(tempfile.mkdtemp(prefix="home-", dir=self.tmp)).resolve()
        with mock.patch.object(pathlib.Path, "home", return_value=home):
            self.assertTrue(self.sync._too_wide(str(home)))
            self.assertTrue(self.sync._too_wide(str(home.parent)))
            self.assertTrue(self.sync._too_wide("/"))
            for system in ("/usr", "/usr/share", "/etc", "/bin", "/var", "/opt", "/System/Library"):
                if os.path.exists(system):
                    self.assertTrue(self.sync._too_wide(os.path.realpath(system)), system)
            inside = home / "University"
            inside.mkdir()
            self.assertFalse(self.sync._too_wide(str(inside)))
            if os.path.exists(str(home).upper()):       # a Mac's disk: /users is /Users
                self.assertTrue(self.sync._too_wide(str(home).upper()))
                self.assertTrue(self.sync._too_wide(str(home.parent).upper()))
                self.assertTrue(self.sync._too_wide(str(paths.data_dir()).upper()))
        if os.path.exists("/USERS"):
            self.assertTrue(self.sync._too_wide("/USERS"))
            self.assertTrue(self.sync._too_wide("/users"))

    def test_a_path_edited_by_hand_to_something_too_wide_is_never_listed(self):
        self.write("a.txt", b"words")
        fid = self.link()
        info = self.info()
        info["synced"][0]["path"] = str(paths.data_dir())       # too wide, and only a temporary folder: never a real one in a look
        (self.lib.root / self.slug / "course.json").write_text(json.dumps(info))
        with mock.patch.object(foldersync, "listing", side_effect=AssertionError("it read what was too wide")):
            self.look(fid)
        self.assertEqual((self.entry()["trouble"], self.names(), self.mat("a.txt").get("gone")), ("sync_wide", ["a.txt"], None))

    def test_damaged_values_of_a_look_reach_the_page_clean(self):
        cleaned = foldersync.clean([{"path": "/x", "trouble": "nonsense", "left": [1, "a"], "left_more": "many", "blind": "no", "came": [{"name": "a", "at": 5}],
                                     "files": {"a.txt": {"state": "in", "name": "a.txt", "held": {"size": "x"}}, "b.txt": {"state": "out", "away": "yes"}}}])[0]
        self.assertEqual((cleaned["trouble"], cleaned["left"], cleaned["left_more"], cleaned["blind"]), ("", ["a"], 0, []))
        self.assertEqual(cleaned["came"], [{"name": "a", "at": "", "changed": False}])
        self.assertNotIn("held", cleaned["files"]["a.txt"])
        self.assertNotIn("away", cleaned["files"]["b.txt"])

    # ---- moved, renamed, emptied
    def test_a_renamed_folder_linked_again_takes_its_files_back(self):
        self.write("a.txt", b"words a")
        self.write("sub/b.txt", b"words b")
        fid = self.link()
        self.sync.stop(self.slug, fid)
        moved = self.tmp / "Renamed"
        self.folder.rename(moved)
        self.sync.add(self.slug, str(moved), "")
        self.settle()
        e = self.entry()
        self.assertEqual((sorted(self.names()), e["clashes"], e["count"]), (["a.txt", "b.txt"], [], 2))
        self.assertEqual(self.trash(), [], "nothing was replaced")
        self.assertTrue(self.mat("a.txt")["synced"] == e["id"] and not self.mat("a.txt").get("synced_was"))

    def test_after_a_stop_a_changed_file_in_another_folder_is_left_alone(self):
        self.write("a.txt", b"words a")
        fid = self.link()
        self.sync.stop(self.slug, fid)
        other = self.tmp / "Another"
        other.mkdir()
        (other / "a.txt").write_text("another file with that name")
        os.utime(other / "a.txt", (time.time() - 100, time.time() - 100))
        self.sync.add(self.slug, str(other), "")
        self.settle()
        self.assertEqual(self.on_disk("a.txt"), "words a")
        self.assertEqual(self.entry()["clashes"], ["a.txt"])
        with mock.patch.object(foldersync, "read_whole", side_effect=AssertionError("a clash is not read at every look")):
            self.look(self.entry()["id"])

    def test_a_removed_file_stays_out_when_renamed_or_when_the_folder_empties_and_fills(self):
        self.write("a.txt", b"words a")
        self.write("b.txt", b"words b")
        fid = self.link()
        self.lib.remove_material(self.slug, "a.txt")
        self.look(fid)
        (self.folder / "a.txt").rename(self.folder / "renamed.txt")
        self.look(fid)
        self.assertEqual(self.names(), ["b.txt"], "a rename is no change of content")
        self.assertEqual(self.entry()["out"], ["renamed.txt"])
        away = self.tmp / "away"
        self.folder.rename(away)
        self.folder.mkdir()
        self.look(fid)
        self.assertEqual(self.entry()["out"], [], "not listed while it is not there")
        self.folder.rmdir()
        away.rename(self.folder)
        self.look(fid)
        self.assertEqual(self.names(), ["b.txt"])
        self.assertEqual(self.entry()["out"], ["renamed.txt"])
        self.write("renamed.txt", b"really a new version", 10)
        self.look(fid)
        self.assertEqual(self.names(), ["b.txt", "renamed.txt"])

    def test_a_folder_swapped_for_another_at_the_same_path_loses_nothing(self):
        self.write("a.txt", b"original a")
        self.write("b.txt", b"original b")
        fid = self.link()
        self.folder.rename(self.tmp / "away")
        self.folder.mkdir()
        self.write("a.txt", b"another folder's a")
        self.look(fid)
        self.assertEqual(self.on_disk("b.txt"), "original b")
        self.assertTrue(self.mat("b.txt")["gone"])
        self.assertEqual([(self.lib.root / ".trash" / t).read_text() for t in self.trash()], ["original a"])

    # ---- nothing changes in the folder, through a link either
    def test_a_folder_swapped_for_a_link_after_the_listing_is_not_followed(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("outside the folder")
        self.write("sub/secret.txt", b"inside")
        with mock.patch.object(foldersync, "listing", return_value=({}, [], False, False)):
            fid = self.link()
        found = foldersync.listing(str(self.folder))

        def swapped(*a):
            shutil.rmtree(self.folder / "sub")
            (self.folder / "sub").symlink_to(outside)
            return found
        with mock.patch.object(foldersync, "listing", swapped):
            self.sync.look(self.slug, fid)
        self.assertEqual(self.names(), [])
        self.assertEqual(self.entry()["failed"], [{"rel": "sub/secret.txt", "why": "sync_file"}])

    def test_odd_names_and_a_hard_link_leave_the_folder_as_it_was(self):
        names = ["x" * 240 + ".txt", "tab\tname.txt", "new\nline.txt", "quo\"te'.txt", "a:b.txt", "back\\slash.txt", "$(touch hacked).txt", "-rf.txt", "%2e%2e.txt",
                 unicodedata.normalize("NFD", "Introducción.txt"), "emoji😀.txt", " lead.txt", "CON.txt"]
        for n in names:
            self.write(n, f"words of {n}".encode())
        os.link(self.folder / "-rf.txt", self.folder / "hard.txt")
        outside = self.tmp / "keep.txt"
        outside.write_text("outside")
        os.link(outside, self.folder / "hard-out.txt")
        before = snapshot(self.folder)
        fid = self.link()
        self.look(fid)
        self.assertEqual(self.entry()["count"], len(names) + 2)
        self.assertEqual(snapshot(self.folder), before)
        copy = self.lib.root / self.slug / "materials" / "hard-out.txt"
        self.assertNotEqual(copy.stat().st_ino, outside.stat().st_ino, "the course's copy is its own file")
        self.assertEqual(outside.stat().st_nlink, 2, "no further link was made")
        before = snapshot(self.folder)
        self.lib.remove_material(self.slug, "hard-out.txt")
        self.assertEqual(snapshot(self.folder), before)
        self.assertEqual(outside.read_text(), "outside")

    # ---- notes and recording
    def test_a_note_reading_while_a_file_is_replaced_reads_a_whole_version(self):
        one, two = "version one " * 4000, "version two " * 4000
        self.write("a.txt", one.encode())
        fid = self.link()
        seen, stop = set(), threading.Event()

        def reader():
            while not stop.is_set():
                seen.add(self.lib.material_text(self.slug, "a.txt"))
        t = threading.Thread(target=reader)
        t.start()
        try:
            for n in range(12):
                self.write("a.txt", (two if n % 2 == 0 else one).encode(), 90 - n)
                self.look(fid)
        finally:
            stop.set()
            t.join()
        self.assertLessEqual(seen, {one, two}, "only ever one whole version")

    def test_add_material_by_hand_is_as_before(self):
        with mock.patch.object(materials, "read_file", wraps=materials.read_file) as read:
            answer = self.lib.add_material(self.slug, "By hand.txt", b"one", "slides")
            self.lib.add_material(self.slug, "By hand.txt", b"two", "slides")
        self.assertEqual([len(c.args) + len(c.kwargs) for c in read.call_args_list], [1, 1], "at its usual priority")
        self.assertEqual(answer["slug"], self.slug)
        self.assertEqual(self.names(), ["By hand (2).txt", "By hand.txt"])
        self.assertFalse(any(k in self.mat("By hand.txt") for k in ("synced", "synced_from", "gone")))
        self.assertEqual(self.trash(), [])

    def test_a_folders_file_is_read_at_a_low_priority_and_a_hand_added_one_is_not(self):
        cmds = []
        plain = subprocess.Popen

        def seen(cmd, **k):
            cmds.append(cmd)
            return plain(cmd, **k)
        self.write("a.docx", b"PK broken")
        with mock.patch.object(materials.subprocess, "Popen", seen):
            self.link()
            with self.assertRaises(materials.MaterialError):
                self.lib.add_material(self.slug, "b.docx", b"PK broken", "slides")
        self.assertEqual(cmds[0][:3], ["/usr/bin/nice", "-n", "10"])
        self.assertNotEqual(cmds[1][0], "/usr/bin/nice")

    def test_a_recording_that_starts_during_a_listing_stops_it(self):
        for n in range(6):
            self.write(f"d{n}/a.txt", b"words")
        with mock.patch.object(foldersync, "listing", return_value=({}, [], False, False)):
            fid = self.link()
        plain, held = os.scandir, {}

        def scan(path):
            if len(held) == 0:
                held.update(zip(("cid", "session"), self.recording()))
            return plain(path)
        with mock.patch.object(foldersync, "CHECK_EVERY_S", 0), mock.patch.object(foldersync.os, "scandir", scan), \
                mock.patch.object(materials, "read_file", side_effect=AssertionError("nothing is read")):
            self.sync.look(self.slug, fid)
        self.assertEqual(self.sync.state(self.slug)["folders"][0]["paused"]["why"], "recording")
        self.assertEqual(self.names(), [])
        self.lib.release_recording(self.slug, held["cid"], held["session"])

    def test_a_recording_that_starts_while_waiting_for_the_turn_stops_the_look(self):
        self.write("a.txt", b"words")
        with mock.patch.object(foldersync, "listing", return_value=({}, [], False, False)):
            fid = self.link()
        held = {}
        real = self.sync._one

        class Turn:
            def acquire(s, timeout=None):
                held.update(zip(("cid", "session"), self.recording()))
                return real.acquire(timeout=timeout)

            def release(s):
                real.release()
        self.sync._one = Turn()
        with mock.patch.object(materials, "read_file", side_effect=AssertionError("nothing is read")):
            self.sync.look(self.slug, fid)
        self.sync._one = real
        self.assertEqual(self.names(), [])
        self.lib.release_recording(self.slug, held["cid"], held["session"])

    def test_a_file_on_a_hung_drive_does_not_stop_the_other_folders(self):
        self.write("a.txt", b"words")
        with mock.patch.object(foldersync, "ONE_WAIT_S", 0.05):
            self.sync._one.acquire()      # a reading that never ends
            try:
                self.link()
            finally:
                self.sync._one.release()
        self.assertEqual(self.names(), ["a.txt"])

    def test_adding_a_folder_does_not_touch_another_one_under_the_lock(self):
        with mock.patch.object(foldersync, "listing", return_value=({}, [], False, False)):
            self.link()
            other = self.tmp / "Another"
            other.mkdir()
            plain, under_lock = os.path.realpath, []

            def real(path, *a, **k):
                if self.lib.lock._is_owned():
                    under_lock.append(path)
                return plain(path, *a, **k)
            with mock.patch.object(foldersync.os.path, "realpath", real):
                self.sync.add(self.slug, str(other), "")
        self.assertEqual([p for p in under_lock if "My slides" in str(p) or "Another" in str(p)], [])

    def test_one_file_that_breaks_something_does_not_stop_the_others(self):
        for n in range(3):
            self.write(f"f{n}.txt", b"words")
        with mock.patch.object(foldersync, "listing", return_value=({}, [], False, False)):
            fid = self.link()
        plain, calls = materials.read_file, []

        def read(path, *a):
            calls.append(path)
            if len(calls) == 1:
                raise RuntimeError("what one file may do")
            return plain(path, *a)
        with mock.patch.object(materials, "read_file", read), mock.patch("traceback.print_exc"):
            self.sync.look(self.slug, fid)
        self.assertEqual(self.names(), ["f1.txt", "f2.txt"])
        self.assertEqual(self.entry()["failed"], [{"rel": "f0.txt", "why": "material_failed"}])

    def test_a_name_in_no_known_encoding_is_named_and_stops_nothing(self):
        self.write("good.txt", b"words")
        fid = self.link()
        plain = os.scandir

        class Odd:
            name = "bad\udc80.txt"
            path = "/nowhere/bad.txt"

        class Listed:
            def __init__(self, inner):
                self.inner = list(inner) + [Odd()]

            def __enter__(self):
                return iter(self.inner)

            def __exit__(self, *a):
                return False
        with mock.patch.object(foldersync.os, "scandir", lambda path: Listed(plain(path))):
            self.look(fid)
        self.assertEqual(self.names(), ["good.txt"])
        self.assertEqual(self.entry()["blind"], ["bad?.txt"])
        json.dumps(self.course()["synced"]).encode("utf-8")
        with self.assertRaises(library.Refused) as e:
            self.sync.add(self.slug, str(self.tmp) + "/bad\udc80", "")
        self.assertEqual(e.exception.kind, "sync_path")

    def test_what_macos_asks_says_it_only_reads_in_three_languages(self):
        sys.path.insert(0, str(ROOT / "packaging"))
        import build_mac
        for key in ("NSDocumentsFolderUsageDescription", "NSDesktopFolderUsageDescription", "NSDownloadsFolderUsageDescription",
                    "NSRemovableVolumesUsageDescription", "NSNetworkVolumesUsageDescription", "NSFileProviderDomainUsageDescription"):
            self.assertIn(key, build_mac.PURPOSES)
        texts = build_mac.i18n_texts(build_mac.PURPOSES.values())
        for lang, reads, nothing in (("en", "reads", "changes nothing"), ("es", "lee", "no cambia nada"), ("ca", "llegeix", "no hi canvia res")):
            for key in ("mac_purpose_documents", "mac_purpose_place", "mac_purpose_desktop", "mac_purpose_downloads"):
                self.assertIn(reads, texts[lang][key], (lang, key))
                self.assertIn(nothing, texts[lang][key].lower(), (lang, key))
                self.assertNotRegex(texts[lang][key], "al día|al dia|in step", "the folder is not what is kept up to date")

    def test_a_course_put_back_from_the_trash_has_its_rounds_again(self):
        self.write("a.txt", b"words")
        fid = self.link()
        self.sync.close()
        self.sync._closed = False
        self.sync._loop = None
        trashed = self.lib.root / ".trash"
        self.lib.trash(self.slug)
        self.assertEqual(self.sync._links(), [])
        kept = next(p for p in trashed.iterdir() if p.is_dir())
        kept.rename(self.lib.root / self.slug)
        self.assertEqual(self.sync._links(), [(self.slug, fid)])
        self.write("b.txt", b"new words")
        self.sync.page_opened(self.slug)
        self.settle()
        self.assertTrue(self.sync._loop is not None)
        self.sync.close()


if __name__ == "__main__":
    unittest.main()
