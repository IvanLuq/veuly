"""Imported by every browser test before it starts the app, so that the test, run as a plain command, never reaches this
computer's own things:
  - the picture watcher's default folders (the screenshot folder or Desktop, and Downloads) are empty, so a recording looks
    only at the folders the test adds itself (looking inside the real ones can make macOS ask the user for permission);
  - Claude Code is found at /usr/bin/true, so Setup runs no real `claude --version` / `claude auth status` (which reads
    Claude Code's login from the Keychain). A test that sets its own find_claude stand-in afterwards replaces this one. Codex likewise.
  - the transcript while recording (transcapp/live.py) runs only with a whisper-cli the test names in the settings
    (`whisper_path`, a stand-in), never this computer's own whisper-cli and model found by themselves.
An app a test starts in another process does not get these: give that one a scratch HOME and PATH instead.
"""
from transcapp import incoming, live, paths, platform_setup

incoming.default_folders = lambda: []
platform_setup.find_claude = lambda: "/usr/bin/true"
platform_setup.find_codex = lambda: "/usr/bin/true"
_ensure = live.Live.ensure
live.Live.ensure = lambda self, slug, cid: _ensure(self, slug, cid) if paths.load_settings()["whisper_path"] else None
