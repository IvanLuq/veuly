"""AI accounts: which account writes each course's notes and each project's minutes. (The settings keep their old names,
`claude_accounts`, `claude_account`, `school_account`: renaming them would lose people's choices.)

  default   what Setup sets up (`settings["engine"]`): an Anthropic API key, an OpenAI key, another service, this Mac's Claude
            Code or this Mac's Codex. Its Claude Code runs with no CLAUDE_CONFIG_DIR and its Codex with no CODEX_HOME, as before
            accounts existed: nothing changes for anyone who adds none.
  api       an Anthropic API key of its own, kept only through keystore (`claude-account-<id>`), never in settings.json.
  openai    a key of its own (the same keystore name; none only for a service on this computer) for OpenAI, or for any service that
            speaks the same language at its own `base_url`, with its `model` typed. The key goes only to that address: changing the
            address deletes the key (`set_service`), and the engine follows no redirect.
  login     a Claude Code login of its own. Claude Code keeps a login per config folder (CLAUDE_CONFIG_DIR; on a Mac its
            Keychain item is named after the folder), so the app makes one for it, `<data>/claude-accounts/<id>/`,
            outside the library, and runs every `claude` of that account with CLAUDE_CONFIG_DIR set to it. The app never
            reads or copies what Claude Code keeps there; `--no-session-persistence` keeps any transcript out of it.
  codex     a Codex login of its own, the same way: CODEX_HOME is `<data>/codex-accounts/<id>/`, made empty and never read.

The app never handles a Claude.ai or ChatGPT login: signing in happens in the vendor's own tool, in a Terminal.

settings.json lists the added ones as `claude_accounts` [{id, name, kind, model, base_url, small_model, context_tokens}] (each only
when the account has one; with no `model`, the one chosen in Setup) and says which one each side uses
(`school_account`, `work_account`: "default" or an id). A course's own choice is `claude_account` in course.json (an
id; none, or one no longer listed, means its side's choice: never an error at Finish).

`resolve` is the one place that decides; `settings_for` gives the settings an engine is made from for that account
(`engine` is the account's way of writing, `_account` says which), so the note, Write it again, Ask, practice exams,
the Confluence connector, the Obsidian agent and Test all go through it. `child_env` is the environment of every
`claude` the app starts, `codex_env` of every `codex`. `service_of` names where the text goes; `context_tokens` how much a request may hold.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import re
import secrets
import shutil
import urllib.parse

from . import keystore, paths

DEFAULT = "default"
KINDS = ("login", "api", "openai", "codex")
FOLDER = "claude-accounts"
CODEX_FOLDER = "codex-accounts"
OPENAI_URL = "https://api.openai.com"       # as engine.OPENAI_URL (engine.py imports this module)
OTHERS_CONTEXT = 200_000                    # what one request may hold on any way but Anthropic's, unless the account says (context_tokens)
CONTEXT_RANGE = (1_000, 2_000_000)
ID = re.compile(r"^[a-z0-9]{8}$")
NAME_MAX = 40
MODEL = re.compile(r"[A-Za-z0-9][\w.:\-\[\]]{0,79}")      # a model's name or alias, as server.MODEL_NAME: it reaches a command line
SERVICE_MODEL = re.compile(r"[A-Za-z0-9][\w.:\-\[\]/+@]{0,159}")     # what other services call a model (OpenRouter's have a /); a list argument for Codex
SERVICE_KEY = re.compile(r"[\x21-\x7e]{1,300}")     # a gateway's key has any shape: printable, no spaces
SIDES = {"course": "school_account", "project": "work_account"}
# What a `claude` of the app never inherits: a key would be billed instead of the login, and the two folders would pick
# another login than the account's (CLAUDE_SECURESTORAGE_CONFIG_DIR overrides which Keychain item is read).
UNSET = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CONFIG_DIR", "CLAUDE_SECURESTORAGE_CONFIG_DIR")
# The same for a `codex`: a key would be billed instead of the login, another CODEX_HOME is another login.
CODEX_UNSET = ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_HOME", "OPENAI_BASE_URL", "OPENAI_API_BASE")


class AccountError(ValueError):
    """A refusal about an account: answered 400 with its kind (h_<kind> in web/i18n.js)."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def secret_name(aid: str) -> str:
    return f"claude-account-{aid}"


def root() -> pathlib.Path:
    return paths.data_dir() / FOLDER


def folder(aid: str) -> pathlib.Path:
    """The config folder of a login account (made when the account is added)."""
    if not ID.match(aid or ""):
        raise AccountError("account_unknown", "There is no such account.")
    return root() / aid


def codex_folder(aid: str) -> pathlib.Path:
    """The CODEX_HOME of a Codex account (made when the account is added)."""
    if not ID.match(aid or ""):
        raise AccountError("account_unknown", "There is no such account.")
    return paths.data_dir() / CODEX_FOLDER / aid


def clean_base_url(url: str) -> str:
    """What engine.clean_base_url says of an address, as a refusal the page can show (engine.py imports this module)."""
    from . import engine
    try:
        return engine.clean_base_url(url)
    except ValueError as e:
        raise AccountError("api_address", str(e))


def _context(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and CONTEXT_RANGE[0] <= value <= CONTEXT_RANGE[1] else None


def _base_ok(url) -> bool:
    try:
        return isinstance(url, str) and clean_base_url(url) == url
    except AccountError:
        return False


def listed(settings: dict) -> list[dict]:
    """The added accounts, cleaned: settings.json is the user's file, and a damaged entry is left out, not trusted."""
    out, seen = [], set()
    for a in settings.get("claude_accounts") or []:
        if not isinstance(a, dict):
            continue
        aid, name, kind = a.get("id"), a.get("name"), a.get("kind")
        if isinstance(aid, str) and ID.match(aid) and aid not in seen and isinstance(name, str) and name.strip() and kind in KINDS:
            if kind == "openai" and a.get("base_url") and not _base_ok(a["base_url"]):
                continue            # an address not to send a key to: the account is left out, like any damaged entry
            seen.add(aid)
            model, shape = a.get("model"), SERVICE_MODEL if kind in ("openai", "codex") else MODEL
            row = {"id": aid, "name": " ".join(name.split())[:NAME_MAX], "kind": kind,
                   **({"model": model} if isinstance(model, str) and shape.fullmatch(model) else {})}
            if kind == "openai" and a.get("base_url"):
                row["base_url"] = a["base_url"]
            small = a.get("small_model")
            if kind in ("openai", "codex"):
                if isinstance(small, str) and SERVICE_MODEL.fullmatch(small):
                    row["small_model"] = small
                if _context(a.get("context_tokens")):
                    row["context_tokens"] = a["context_tokens"]
            out.append(row)
    return out


def default_account() -> dict:
    return {"id": DEFAULT, "name": "", "kind": DEFAULT}


def get(settings: dict, aid) -> dict | None:
    if aid == DEFAULT:
        return default_account()
    return next((a for a in listed(settings) if a["id"] == aid), None)


def side_account(settings: dict, kind: str) -> dict:
    """The account a side uses ("course" school, "project" work): its choice in Settings, the default when none or gone."""
    return get(settings, settings.get(SIDES.get(kind, ""), DEFAULT)) or default_account()


def resolve(settings: dict, course: dict | None = None, side: str | None = None, account: str | None = None) -> dict:
    """The account that writes. `account`: one named (Test); `course`: its own choice when it names a listed account, else
    its side's; `side` alone (Ask over a whole side): that side's choice; nothing (Ask over everything): the default."""
    if account is not None:
        found = get(settings, account)
        if not found:
            raise AccountError("account_unknown", "There is no such account.")
        return found
    if course is not None:
        return get(settings, course.get("claude_account")) or side_account(settings, course.get("kind", "course"))
    if side in SIDES:
        return side_account(settings, side)
    return default_account()


def settings_for(settings: dict, course: dict | None = None, side: str | None = None, account: str | None = None) -> dict:
    """The settings an engine is made from (pipeline.default_engine), for the account that writes: `engine` is how that
    account writes ("claude-code" for a login, "api" for an Anthropic key, "openai", "codex", Setup's choice for the default),
    `_account` says which account it is, and the account's own model, when it chose one, takes the place of Setup's
    (`claude_model` for a login, `api_model` for a key, `openai_model`, `codex_model`)."""
    acct = resolve(settings, course, side, account)
    out = dict(settings, _account=acct)
    if acct["kind"] == "login":
        out["engine"] = "claude-code"
        out["claude_model"] = acct.get("model") or settings.get("claude_model")
    elif acct["kind"] == "api":
        out["engine"] = "api"
        out["api_model"] = acct.get("model") or settings.get("api_model")
    elif acct["kind"] == "openai":
        base = acct.get("base_url") or ""
        out.update(engine="openai", openai_base_url=base, openai_name=acct["name"], openai_small_model=acct.get("small_model", ""),
                   # a typed model on another service is its own; on OpenAI, Setup's when it chose none
                   openai_model=acct.get("model") or (settings.get("openai_model") if not base else ""))
    elif acct["kind"] == "codex":
        out.update(engine="codex", codex_model=acct.get("model") or settings.get("codex_model", ""), openai_small_model=acct.get("small_model", ""))
    if acct["kind"] != DEFAULT:
        out["context_tokens"] = acct.get("context_tokens", 0)
    return out


def host_of(base_url: str) -> str:
    return (urllib.parse.urlsplit(base_url).hostname or "").lower()


def service_of(settings: dict) -> str:
    """The service that gets a class's text, from settings made by settings_for: what a failure and the note's line call it."""
    how = settings.get("engine")
    if how == "openai":
        base = settings.get("openai_base_url") or OPENAI_URL
        return "OpenAI" if base == OPENAI_URL else (settings.get("openai_name") or host_of(base))
    return "Codex" if how == "codex" else "Claude" if how in ("api", "claude-code") else ""


def context_tokens(settings: dict) -> int | None:
    """What one request may hold on this way of writing: the account's own when it says; Anthropic's ways keep the engine's own limit (None)."""
    if settings.get("engine") not in ("openai", "codex"):
        return None
    return _context(settings.get("context_tokens")) or OTHERS_CONTEXT


def claude_code_ok(settings: dict, acct: dict) -> bool:
    """Whether what only Claude Code can do (the Confluence connector, the Obsidian agent) can run under this account: a login of its own,
    or this Mac's Claude Code unless Setup chose another company's way of writing (Setup's Anthropic key has always used this Mac's)."""
    if acct["kind"] == DEFAULT:
        return settings.get("engine") not in ("openai", "codex")
    return acct["kind"] == "login"


def of(settings: dict) -> dict:
    """The account in settings made by settings_for; the default for any other settings."""
    acct = settings.get("_account")
    return acct if isinstance(acct, dict) and acct.get("kind") in KINDS + (DEFAULT,) else default_account()


def codex_home(acct: dict) -> str | None:
    """CODEX_HOME of the account's `codex` runs: its folder for a Codex account, none for the others (this Mac's own login)."""
    return str(codex_folder(acct["id"])) if acct.get("kind") == "codex" else None


def config_dir(acct: dict) -> str | None:
    """CLAUDE_CONFIG_DIR of the account's `claude` runs: its folder for a login, none for the others."""
    return str(folder(acct["id"])) if acct.get("kind") == "login" else None


def child_env(config: str | None = None, base: dict | None = None) -> dict:
    """The environment of a `claude` the app starts: the app's own, without an API key or another login's folder, and
    with CLAUDE_CONFIG_DIR set to `config` (a login account's folder) when one is given."""
    env = {k: v for k, v in (os.environ if base is None else base).items() if k not in UNSET}
    if config:
        env["CLAUDE_CONFIG_DIR"] = config
    return env


def env_for(acct: dict) -> dict:
    return child_env(config_dir(acct))


def codex_env(home: str | None = None, base: dict | None = None) -> dict:
    """The environment of a `codex` the app starts: the app's own, without a key or another login's folder, and with CODEX_HOME
    set to `home` (a Codex account's folder) when one is given. As Claude's, it never gets the API keys of the app's own environment."""
    env = {k: v for k, v in (os.environ if base is None else base).items() if k not in CODEX_UNSET}
    if home:
        env["CODEX_HOME"] = home
    return env


def setup_secret(base_url: str) -> str:
    """The key store's name of Setup's own key for OpenAI or for another service. A name per address: a key cannot reach another
    address by a change of settings, even one made by hand in settings.json."""
    if not base_url or base_url == OPENAI_URL:
        return "setup-openai-key"
    return "setup-service-" + hashlib.sha256(base_url.encode("utf-8")).hexdigest()[:16]


def openai_key(settings: dict) -> str | None:
    """The key an "openai" engine sends: the account's own, or Setup's for that address. Never one from the environment."""
    acct = of(settings)
    if acct.get("kind") == "openai":
        return keystore.get_secret(secret_name(acct["id"]))
    return keystore.get_secret(setup_secret(settings.get("openai_base_url") or ""))


def api_key(acct: dict) -> str | None:
    """The key an API account writes with (keystore), or Setup's key for the default."""
    if acct.get("kind") == "api":
        return keystore.get_secret(secret_name(acct["id"]))
    return keystore.get_key()


def label(acct: dict) -> str:
    return acct.get("name") or "This Mac's Claude Code"


def _service_fields(key: str, base_url, model, small_model, context) -> dict:
    """The checked fields of an "openai" account: `base_url` left out for OpenAI itself, a model needed on any other service (there
    is no name to assume), a key needed unless the service is on this computer."""
    from . import engine
    base = clean_base_url(base_url) if str(base_url or "").strip() else ""
    base = "" if base == OPENAI_URL else base
    model, small = str(model or "").strip(), str(small_model or "").strip()
    if (base and not model) or (model and not SERVICE_MODEL.fullmatch(model)) or (small and not SERVICE_MODEL.fullmatch(small)):
        raise AccountError("model_name", "That is not the name of a model.")
    key = (key or "").strip()
    if key and not SERVICE_KEY.fullmatch(key):
        raise AccountError("key_shape", "That does not look like an API key.")
    if not key and not (base and engine.is_local(base)):
        raise AccountError("key_shape", "This service needs an API key.")
    out = {**({"base_url": base} if base else {}), **({"model": model} if model else {}), **({"small_model": small} if small else {})}
    if context not in (None, "", 0):
        if not _context(context):
            raise AccountError("bad_request", "That is not a size of request.")
        out["context_tokens"] = context
    return out


# ---------------------------------------------------------------- adding, renaming, removing (settings.json)
def _name(settings: dict, name, but: str = "") -> str:
    name = " ".join(str(name or "").split())
    if not name:
        raise AccountError("account_name", "Give the account a name.")
    if len(name) > NAME_MAX:
        raise AccountError("account_name", f"A name of at most {NAME_MAX} characters.")
    if any(a["name"].casefold() == name.casefold() and a["id"] != but for a in listed(settings)):
        raise AccountError("account_name", "Another account has that name.")
    return name


def add(name, kind, key: str = "", base_url="", model="", small_model="", context=None) -> dict:
    """A new account: a login or a Codex login (its folder is made now, empty, for that program to fill when it signs in), an
    Anthropic API key, or a key for OpenAI or another service (kept through keystore only). Returns it."""
    settings = paths.load_settings()
    name = _name(settings, name)
    if kind not in KINDS:
        raise AccountError("bad_request", "Choose how the account writes.")
    if kind == "api" and not keystore.KEY_SHAPE.fullmatch((key or "").strip()):
        raise AccountError("key_shape", "That does not look like an API key.")
    extra = _service_fields(key, base_url, model, small_model, context) if kind == "openai" else {}
    if kind == "codex":
        model = str(model or "").strip()
        if model and not SERVICE_MODEL.fullmatch(model):
            raise AccountError("model_name", "That is not the name of a model.")
        extra = {"model": model} if model else {}
    have = {a["id"] for a in listed(settings)}
    aid = next(x for x in iter(lambda: secrets.token_hex(4), None) if x not in have and not (root() / x).exists() and not codex_folder(x).exists())
    if kind == "login":
        folder(aid).mkdir(parents=True, mode=0o700)
    elif kind == "codex":
        codex_folder(aid).mkdir(parents=True, mode=0o700)
    elif (key or "").strip():
        keystore.set_secret(secret_name(aid), key.strip())
    acct = {"id": aid, "name": name, "kind": kind, **extra}
    paths.save_settings({"claude_accounts": listed(settings) + [acct]})
    return acct


def rename(aid: str, name) -> dict:
    settings = paths.load_settings()
    if not get(settings, aid) or aid == DEFAULT:
        raise AccountError("account_unknown", "There is no such account.")
    name = _name(settings, name, but=aid)
    accts = [dict(a, name=name) if a["id"] == aid else a for a in listed(settings)]
    paths.save_settings({"claude_accounts": accts})
    return get(paths.load_settings(), aid)


def set_model(aid: str, model) -> dict:
    """The model this account writes with; "" goes back to the one chosen in Setup."""
    settings = paths.load_settings()
    if not get(settings, aid) or aid == DEFAULT:
        raise AccountError("account_unknown", "There is no such account.")
    model = model if isinstance(model, str) else None
    shape = SERVICE_MODEL if get(settings, aid)["kind"] in ("openai", "codex") else MODEL
    if model is None or (model and not shape.fullmatch(model)):
        raise AccountError("model_name", "That is not the name of a model.")
    if not model and get(settings, aid).get("base_url"):
        raise AccountError("model_name", "This service needs the name of a model.")
    accts = []
    for a in listed(settings):
        if a["id"] == aid:
            a = {k: v for k, v in a.items() if k != "model"}
            if model:
                a["model"] = model
        accts.append(a)
    paths.save_settings({"claude_accounts": accts})
    return get(paths.load_settings(), aid)


def set_key(aid: str, key: str) -> None:
    acct = get(paths.load_settings(), aid)
    if not acct or acct["kind"] not in ("api", "openai"):
        raise AccountError("account_unknown", "There is no such account.")
    if not (keystore.KEY_SHAPE if acct["kind"] == "api" else SERVICE_KEY).fullmatch((key or "").strip()):
        raise AccountError("key_shape", "That does not look like an API key.")
    keystore.set_secret(secret_name(aid), key.strip())


def set_service(aid: str, base_url=None, small_model=None, context=None) -> dict:
    """An "openai" account's address, the small model of its checks, what a request may hold; each only when given. The key belongs to
    the address it was given for: a new address deletes it, before the account is saved, so it can never go to the new place."""
    settings = paths.load_settings()
    acct = get(settings, aid)
    if not acct or acct["kind"] not in ("openai", "codex"):
        raise AccountError("account_unknown", "There is no such account.")
    changes = {}
    if base_url is not None and acct["kind"] == "openai":
        base = clean_base_url(base_url) if str(base_url).strip() else ""
        base = "" if base == OPENAI_URL else base
        if base != acct.get("base_url", ""):
            if base and not acct.get("model"):
                raise AccountError("model_name", "This service needs the name of a model.")
            keystore.delete_secret(secret_name(aid))
            changes["base_url"] = base
    if small_model is not None:
        small = str(small_model).strip()
        if small and not SERVICE_MODEL.fullmatch(small):
            raise AccountError("model_name", "That is not the name of a model.")
        changes["small_model"] = small
    if context is not None:
        if context not in ("", 0) and not _context(context):
            raise AccountError("bad_request", "That is not a size of request.")
        changes["context_tokens"] = context or 0
    accts = []
    for a in listed(settings):
        if a["id"] == aid:
            a = {k: v for k, v in {**a, **changes}.items() if v not in ("", 0)}
        accts.append(a)
    paths.save_settings({"claude_accounts": accts})
    return get(paths.load_settings(), aid)


def remove(aid: str, lib=None, signed_out: bool = False) -> list[str]:
    """Take the account off the list. A side that used it goes back to the default; a course that chose it goes back to
    its side's choice (its `claude_account` is dropped). A key leaves the key store. A login account's or a Codex account's
    folder goes only once that program signed it out (`signed_out`): otherwise its login would stay behind with no folder.
    Returns the slugs of the courses changed."""
    settings = paths.load_settings()
    acct = get(settings, aid)
    if not acct or aid == DEFAULT:
        raise AccountError("account_unknown", "There is no such account.")
    changes = {"claude_accounts": [a for a in listed(settings) if a["id"] != aid]}
    for key in SIDES.values():
        if settings.get(key) == aid:
            changes[key] = DEFAULT
    paths.save_settings(changes)
    if acct["kind"] in ("api", "openai"):
        keystore.delete_secret(secret_name(aid))
    elif signed_out:
        shutil.rmtree(folder(aid) if acct["kind"] == "login" else codex_folder(aid), ignore_errors=True)
    moved = []
    if lib is not None:
        for course in lib.courses():
            if course.get("claude_account") == aid:
                lib.update_course(course["slug"], {"claude_account": ""})
                moved.append(course["slug"])
    return moved


def clean_choice(value, settings: dict, allow_same: bool) -> str:
    """A choice of account from the page: "default", a listed id, or (for a course) "" for its side's choice."""
    if value == "" and allow_same:
        return ""
    if value == DEFAULT or get(settings, value):
        return value
    raise AccountError("account_unknown", "There is no such account.")
