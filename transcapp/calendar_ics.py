"""The calendar links: which class or meeting is on now, for the home page ("Now: Optical Networks 10:00-12:00 · Start").

The user pastes each calendar's secret address in iCal format (Google: Settings › the calendar › Integrate calendar ›
"Secret address in iCal format"; Outlook: Publish a calendar › ICS) in Settings › Calendars, and says whether it is
for school or for work (its side). Each address is a password: whoever has it reads the whole calendar. So it is kept
through `keystore` (secret_of(id): calendar-url-<id>), never in settings.json or the list (LIST, calendars.json in the
data folder: id, name, side and the site's host), never logged, never sent back to the page (only the site's name,
`where`), and an error message never carries it.

The list has a file of its own because a version that does not know a setting drops it when it saves settings.json (the
school / work switch does): 0.6.0, opened once more, would otherwise take every calendar off the list for good.

  Calendars     the list (at most MAX_CALENDARS), adding (a link is fetched once before it is kept), renaming,
                changing the side, removing; the single link of before (keystore SECRET) is moved into the list as
                its first calendar on the first start (`_migrate`), its side worked out once it is read (`settle`:
                a calendar with events for both sides shows on both, as two entries, as it did before)
  Feed          one calendar: fetches its link with urllib, at most every CACHE_S (a failure is a CalendarError with
                a kind, and is tried again after RETRY_S), keeps the last good copy, and parses it once per copy.
                Several are fetched at once, each in a thread of its own (`Calendars.read`)
  Calendar      the parsed file: VEVENT with line unfolding and escapes; TZID through zoneinfo, with the file's own
                VTIMEZONE when zoneinfo does not know the name (Windows has no tz database, Outlook writes
                "Romance Standard Time"); a floating time is this computer's; DAILY / WEEKLY (INTERVAL, COUNT, UNTIL, BYDAY,
                WKST) and simple MONTHLY / YEARLY rules, EXDATE, RDATE, a moved or cancelled occurrence (RECURRENCE-ID);
                all-day events and cancelled ones are left out. `between(start, end)` gives the occurrences that overlap
  match         which course or project an event belongs to: its "calendar name" (course.json `calendar`, names
                separated by commas; by default the course's own name) as whole words of the event's title,
                compared without case or accents; the longest name wins
  home_part     home.PARTS: what is on now and next today, from the calendars of the side shown, merged in time order
                (an event in two of them shows once: `now_next`), and a line for each one that could not be read. On
                the work side an event of no project is offered too when it has attendees (a meeting), with a choice
                of project
  find          one occurrence again by its calendar, UID and start, for POST /api/calendar/start

Nothing here is written to the library: the class made from an event keeps `calendar` (uid, start, title) in its
class.json, so the home page offers Open instead of a second Start."""
from __future__ import annotations

import dataclasses
import datetime
import http.client
import json
import re
import secrets
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from . import home, keystore, library, paths, recipients

try:
    import zoneinfo
except ImportError:         # Python 3.8: only the file's own VTIMEZONE
    zoneinfo = None

SECRET = "calendar-url"     # the keystore name of the single link of before; each calendar's own is secret_of(its id)
FIRST = "c0"                # the id of the calendar moved from it. Nothing else leaves this id's secret without an entry
LIST = "calendars.json"     # the list, in the data folder (see the docstring: not settings.json)
MAX_CALENDARS = 8
SIDES = ("course", "project")   # school, work: the side whose home page shows a calendar's events
ID = re.compile(r"[a-z0-9]{1,16}")
NAME_MAX = 60
NO_NAMES = ("calendar", "calendario", "calendari", "calendrier", "kalender")      # what Outlook calls every calendar, by language
MOVED = ("microsoft", "projects", "courses", "side", "both")    # why the calendar moved from the single link is on its side
CACHE_S = 15 * 60           # a copy this young is used as it is
RETRY_S = 2 * 60            # after a failure, the next try
TIMEOUT_S = 15
WAIT_S = 1.5                # the home page waits this long for a calendar with no copy yet; then it shows what it has and asks again
STALE_WAIT_S = 0.3          # and this long for a fresher copy of one it has (the old copy is shown meanwhile)
MAX_BYTES = 8 << 20
EARLY = datetime.timedelta(minutes=10)      # an event starting this soon counts as "now": the room is filling up
MAX_STEPS = 20000                           # occurrences looked at per event, whatever its rule says
UTC = datetime.timezone.utc
WEEKDAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


class CalendarError(ValueError):
    """kind: calendar_address (not a link it can use), calendar_network (no answer), calendar_gone (the address no longer
    works: it was reset, or the calendar is no longer shared), calendar_not_ics (what came back is not a calendar),
    calendar_event_gone (the event is no longer in the calendar). The message never names the link."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


# ---------------------------------------------------------------- the link

def clean_url(url: str) -> str:
    """The address to fetch: https (webcal:// is the same address, and an http:// one is asked over https, so the secret
    never crosses the network in the clear), or http on this computer only (the tests' server)."""
    url = (url or "").strip()
    if url.lower().startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    refused = CalendarError("calendar_address", "That is not a calendar link. Paste the secret address in iCal format.")
    try:                    # urllib's own complaints quote what they were given: never the link in a message
        parts = urllib.parse.urlsplit(url)
        local = parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost")
        if parts.scheme.lower() == "http" and parts.hostname and not local:
            url = "https://" + url[len("http://"):]
            parts = urllib.parse.urlsplit(url)
        host = parts.hostname
    except ValueError:
        raise refused from None
    if not host or not (parts.scheme == "https" or local) or len(url) > 2000 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        raise refused
    return url


def where(url: str) -> str:
    """What the page may show of the link: the site's name, never the secret part."""
    return urllib.parse.urlsplit(url or "").hostname or ""


class _Redirects(urllib.request.HTTPRedirectHandler):
    """A calendar may send the app on (Outlook's links do), but only over https: never to plain http, where the address
    asked for (perhaps the secret one again) would cross the network in the clear, nor to this computer's own servers.
    The test's calendar on 127.0.0.1 may send it on only on 127.0.0.1."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        was = urllib.parse.urlsplit(req.full_url).hostname in ("127.0.0.1", "localhost")
        to = urllib.parse.urlsplit(newurl)
        here = to.hostname in ("127.0.0.1", "localhost", "::1") or (to.hostname or "").startswith("127.")
        if not (to.scheme == "https" and not here or was and here and to.scheme in ("http", "https")):
            raise CalendarError("calendar_not_ics", "The calendar sent the app to another kind of address.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_Redirects)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": f"{paths.APP} (calendar)", "Accept": "text/calendar, */*"})
    try:
        with _OPENER.open(req, timeout=TIMEOUT_S) as resp:
            data = resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404, 410):
            raise CalendarError("calendar_gone", f"The calendar answered {e.code}: the address no longer works.") from None
        raise CalendarError("calendar_network", f"The calendar answered {e.code}.") from None
    except CalendarError:                   # a redirect it does not follow
        raise
    # no connection, a timeout, a certificate it does not trust, an answer that is not HTTP; only the kind of failure: their
    # own messages quote the address (http.client.InvalidURL) or the answer
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
        reason = getattr(e, "reason", e)
        raise CalendarError("calendar_network", f"The calendar could not be reached ({type(reason).__name__}).") from None
    if len(data) > MAX_BYTES:
        raise CalendarError("calendar_not_ics", "That calendar is too large.")
    text = data.decode("utf-8", "replace").lstrip("﻿")
    if "BEGIN:VCALENDAR" not in text[:4096].upper():
        raise CalendarError("calendar_not_ics", "What that link gives is not a calendar in iCal format.")
    return text


# ---------------------------------------------------------------- reading the file

def unfold(text: str) -> list:
    """The file's logical lines: a line that starts with a space or a tab goes on the one before it (RFC 5545 3.1)."""
    return [line for line in re.sub(r"\r?\n[ \t]", "", text.replace("\r\n", "\n").replace("\r", "\n")).split("\n") if line.strip()]


_TZID_SCHEME = re.compile(r";TZID=[A-Za-z][\w+.-]*$", re.I)


def parse_line(line: str):
    """(NAME, {PARAM: value}, value). A parameter's value may be quoted, with ':' or ';' inside. Outlook leaves one
    unquoted that holds "://" (DTSTART;TZID=tzone://Microsoft/Utc:20261102T140000): that colon is the parameter's."""
    i, n, quoted = 0, len(line), False
    while i < n and (quoted or line[i] != ":" or (line.startswith("//", i + 1) and _TZID_SCHEME.search(line, 0, i))):
        if line[i] == '"':
            quoted = not quoted
        i += 1
    head, value = line[:i], line[i + 1:]
    pieces, cur, quoted = [], "", False
    for ch in head:
        if ch == '"':
            quoted = not quoted
        if ch == ";" and not quoted:
            pieces.append(cur)
            cur = ""
        else:
            cur += ch
    pieces.append(cur)
    params = {}
    for p in pieces[1:]:
        k, _, v = p.partition("=")
        params[k.strip().upper()] = v.strip().strip('"')
    return pieces[0].strip().upper(), params, value


def unescape(value: str) -> str:
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), value)


def components(text: str) -> dict:
    """The file as a tree: {"type", "props": [(name, params, value)], "subs": [...]} from its BEGIN/END lines."""
    root = {"type": "ROOT", "props": [], "subs": []}
    stack = [root]
    for line in unfold(text):
        name, params, value = parse_line(line)
        if name == "BEGIN":
            node = {"type": value.strip().upper(), "props": [], "subs": []}
            stack[-1]["subs"].append(node)
            stack.append(node)
        elif name == "END":
            if len(stack) > 1:
                stack.pop()
        else:
            stack[-1]["props"].append((name, params, value))
    return root


def _first(node, name):
    return next(((p, v) for k, p, v in node["props"] if k == name), (None, None))


def _all(node, name):
    return [(p, v) for k, p, v in node["props"] if k == name]


def _local(value: str):
    """A DATE-TIME or DATE value as (naive datetime, is_utc, is_date)."""
    v = value.strip()
    m = re.fullmatch(r"(\d{4})(\d\d)(\d\d)(?:T(\d\d)(\d\d)(\d\d)?(Z)?)?", v)
    if not m:
        raise ValueError(f"not a date: {v[:20]}")
    y, mo, d, hh, mi, ss, z = m.groups()
    if hh is None:
        return datetime.datetime(int(y), int(mo), int(d)), False, True
    return datetime.datetime(int(y), int(mo), int(d), int(hh), int(mi), int(ss or 0)), bool(z), False


def _duration(value: str) -> datetime.timedelta:
    m = re.fullmatch(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", (value or "").strip())
    if not m:
        raise ValueError("not a duration")
    sign, w, d, h, mi, s = m.groups()
    out = datetime.timedelta(weeks=int(w or 0), days=int(d or 0), hours=int(h or 0), minutes=int(mi or 0), seconds=int(s or 0))
    return -out if sign == "-" else out


def _nth_weekday(year: int, month: int, weekday: int, nth: int):
    """The nth `weekday` (0 = Monday) of a month; a negative nth counts from its end. None when there is no such day."""
    if nth > 0:
        first = datetime.date(year, month, 1)
        day = first + datetime.timedelta(days=(weekday - first.weekday()) % 7 + 7 * (nth - 1))
    else:
        last = (datetime.date(year + month // 12, month % 12 + 1, 1) - datetime.timedelta(days=1))
        day = last - datetime.timedelta(days=(last.weekday() - weekday) % 7 + 7 * (-nth - 1))
    return day if day.month == month else None


def _rule(value: str) -> dict:
    out = {}
    for part in (value or "").split(";"):
        k, _, v = part.partition("=")
        if k.strip():
            out[k.strip().upper()] = v.strip().upper()
    return out


def _byday(text: str):
    """BYDAY=MO,WE or 1SU,-1SU: [(ordinal or 0, weekday)]."""
    out = []
    for item in (text or "").split(","):
        m = re.fullmatch(r"([+-]?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)", item.strip())
        if m:
            out.append((int(m.group(1) or 0), WEEKDAYS.index(m.group(2))))
    return out


class FileZone(datetime.tzinfo):
    """A time zone as the file's VTIMEZONE describes it: STANDARD and DAYLIGHT onsets (each a DTSTART in local time, its
    TZOFFSETTO, and a yearly rule such as BYMONTH=10;BYDAY=-1SU). Used when zoneinfo does not know the TZID."""

    def __init__(self, node: dict):
        self.onsets = []        # (local start, offset from, offset to, rule)
        for sub in node["subs"]:
            if sub["type"] not in ("STANDARD", "DAYLIGHT"):
                continue
            try:
                start = _local(_first(sub, "DTSTART")[1] or "")[0]
                off_from, off_to = (self._offset(_first(sub, k)[1]) for k in ("TZOFFSETFROM", "TZOFFSETTO"))
            except (ValueError, TypeError):
                continue
            rdates = []
            for _, v in _all(sub, "RDATE"):
                for one in v.split(","):
                    try:
                        rdates.append(_local(one)[0])
                    except ValueError:
                        pass
            self.onsets.append((start, off_from, off_to, _rule(_first(sub, "RRULE")[1] or ""), rdates))

    @staticmethod
    def _offset(value) -> datetime.timedelta:
        m = re.fullmatch(r"([+-])(\d\d)(\d\d)(\d\d)?", (value or "").strip())
        if not m:
            raise ValueError("not an offset")
        delta = datetime.timedelta(hours=int(m.group(2)), minutes=int(m.group(3)), seconds=int(m.group(4) or 0))
        return -delta if m.group(1) == "-" else delta

    def _changes(self, year: int):
        """The moments the offset changes around `year`, in local time before the change: [(when, offset after)]."""
        out = []
        for start, off_from, off_to, rule, rdates in self.onsets:
            out.append((start, off_to))
            out.extend((d, off_to) for d in rdates)
            if rule.get("FREQ") != "YEARLY":
                continue
            months = [int(m) for m in rule.get("BYMONTH", str(start.month)).split(",") if m.isdigit()]
            until = None
            if rule.get("UNTIL"):
                try:
                    until = _local(rule["UNTIL"])[0]
                except ValueError:
                    pass
            for y in (year - 1, year, year + 1):
                if y < start.year:
                    continue
                for mo in months:
                    days = []
                    for nth, wd in _byday(rule.get("BYDAY", "")):
                        # Windows' own zones say "week 5" for the last one (Exchange writes BYDAY=5SU): a month with
                        # four Sundays still changes the clock, on its last
                        d = _nth_weekday(y, mo, wd, nth or 1) or (_nth_weekday(y, mo, wd, -1) if nth == 5 else None)
                        if d:
                            days.append(d)
                    if not rule.get("BYDAY"):
                        try:
                            days.append(datetime.date(y, mo, int(rule.get("BYMONTHDAY", start.day))))
                        except ValueError:
                            pass
                    for d in days:
                        when = datetime.datetime.combine(d, start.time())
                        if when >= start and (until is None or when <= until):
                            out.append((when, off_to))
        return sorted(out)

    def utcoffset(self, dt):
        if dt is None:
            return None
        if not self.onsets:
            return datetime.timedelta(0)
        local = dt.replace(tzinfo=None)
        changes = self._changes(local.year)
        before = [off for when, off in changes if when <= local]
        if before:
            return before[-1]
        return min(self.onsets, key=lambda o: o[0])[1]      # before every onset: the offset the first one changes from

    def dst(self, dt):
        return datetime.timedelta(0)

    def tzname(self, dt):
        return None


# Windows' names for the zones a work calendar from Outlook / Microsoft 365 is likely to use, for when the file names one
# without describing it in a VTIMEZONE (the file's own description comes first). "tzone://Microsoft/Utc" is Outlook's UTC.
WINDOWS_ZONES = {
    "utc": "UTC", "coordinated universal time": "UTC", "tzone://microsoft/utc": "UTC", "greenwich standard time": "Atlantic/Reykjavik",
    "gmt standard time": "Europe/London", "romance standard time": "Europe/Paris", "w. europe standard time": "Europe/Berlin",
    "central europe standard time": "Europe/Budapest", "central european standard time": "Europe/Warsaw",
    "e. europe standard time": "Europe/Chisinau", "gtb standard time": "Europe/Bucharest", "fle standard time": "Europe/Kiev",
    "morocco standard time": "Africa/Casablanca", "eastern standard time": "America/New_York",
    "central standard time": "America/Chicago", "mountain standard time": "America/Denver",
    "pacific standard time": "America/Los_Angeles", "e. south america standard time": "America/Sao_Paulo",
    "sa pacific standard time": "America/Bogota", "central standard time (mexico)": "America/Mexico_City",
    "india standard time": "Asia/Kolkata", "china standard time": "Asia/Shanghai", "tokyo standard time": "Asia/Tokyo",
}
# Older Outlook writes the zone's display name instead: "(UTC+01:00) Brussels, Copenhagen, Madrid, Paris"
WINDOWS_CITIES = [("madrid", "Europe/Madrid"), ("paris", "Europe/Paris"), ("berlin", "Europe/Berlin"), ("london", "Europe/London"),
                  ("lisbon", "Europe/Lisbon"), ("warsaw", "Europe/Warsaw"), ("prague", "Europe/Prague"),
                  ("eastern time", "America/New_York"), ("pacific time", "America/Los_Angeles"),
                  ("coordinated universal time", "UTC")]


def _windows_zone(tzid: str):
    name = (tzid or "").strip().strip('"').lower()
    found = WINDOWS_ZONES.get(name) or next((z for city, z in WINDOWS_CITIES if name.startswith("(utc") and city in name), None)
    if not found:
        return None
    if found == "UTC":
        return UTC
    try:
        return zoneinfo.ZoneInfo(found) if zoneinfo is not None else None
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError):
        return None


def _zone_name(tzid: str) -> str:
    """"/mozilla.org/20050126_1/Europe/Madrid" and '"Europe/Madrid"' are Europe/Madrid."""
    tzid = (tzid or "").strip().strip('"')
    m = re.search(r"([A-Za-z_]+/[A-Za-z_+\-]+(?:/[A-Za-z_+\-]+)?)$", tzid)
    return m.group(1) if m and tzid.startswith("/") else tzid


@dataclasses.dataclass
class Event:
    uid: str
    title: str
    start: datetime.datetime        # aware
    end: datetime.datetime          # aware
    attendees: list
    location: str = ""
    mails: list = dataclasses.field(default_factory=list)      # [(name, address)] of those with one: never sent to the page


def _name_of(params: dict, value: str) -> str:
    """An ATTENDEE's or ORGANIZER's name: CN, or the address's own part made readable ("maria.lopez" is Maria Lopez)."""
    cn = " ".join((params.get("CN") or "").split())
    mail = re.sub(r"^mailto:", "", value.strip(), flags=re.I)
    if cn and "@" not in cn:
        return cn[:80]
    local = (cn or mail).split("@")[0]
    words = [w for w in re.split(r"[._\-+]+", local) if w and not w.isdigit()]
    return " ".join(w[:1].upper() + w[1:] for w in words)[:80]


class Calendar:
    """One copy of the file, parsed once."""

    def __init__(self, text: str):
        root = components(text)
        cals = [s for s in root["subs"] if s["type"] == "VCALENDAR"] or [root]
        self.zones, self.masters, self.moved = {}, [], {}
        # Outlook's published calendars (Microsoft 365, Outlook.com) leave every attendee out: there, on the work side,
        # an event of no project is a meeting even without them (see now_next)
        self.microsoft = "MICROSOFT" in (_first(cals[0], "PRODID")[1] or "").upper()
        self.no_attendees = self.microsoft
        self.name = _name(unescape(_first(cals[0], "X-WR-CALNAME")[1] or ""))      # the calendar's own name, if it has one
        for cal in cals:
            for sub in cal["subs"]:
                if sub["type"] == "VTIMEZONE":
                    tzid = _first(sub, "TZID")[1]
                    if tzid:
                        self.zones[tzid.strip()] = FileZone(sub)
            for sub in cal["subs"]:
                if sub["type"] != "VEVENT":
                    continue
                try:
                    ev = self._event(sub)
                except (ValueError, TypeError, OverflowError):
                    continue            # one event it cannot read never hides the others
                if ev is None:
                    continue
                if ev["attendees"]:
                    self.no_attendees = False
                if ev["recurrence_id"] is not None:
                    self.moved.setdefault(ev["uid"], []).append(ev)
                else:
                    self.masters.append(ev)

    # -- times
    def _zone(self, tzid):
        if not tzid:
            return None
        name = _zone_name(tzid)
        if zoneinfo is not None:
            try:
                return zoneinfo.ZoneInfo(name)
            except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError):
                pass
        return self.zones.get(tzid.strip()) or self.zones.get(name) or _windows_zone(tzid)

    def _aware(self, local: datetime.datetime, utc: bool, zone):
        if utc:
            return local.replace(tzinfo=UTC)
        if zone is not None:
            return local.replace(tzinfo=zone).astimezone(UTC)
        return local.astimezone(UTC)        # a floating time: this computer's own

    def _when(self, params, value):
        local, utc, is_date = _local(value)
        if params and params.get("VALUE") == "DATE":
            is_date = True
        return local, utc, is_date, self._zone((params or {}).get("TZID"))

    # -- one VEVENT
    def _event(self, node):
        p, v = _first(node, "DTSTART")
        if v is None:
            return None
        start, utc, is_date, zone = self._when(p, v)
        if is_date:
            return None                     # all day: not a class or a meeting to record
        if (_first(node, "STATUS")[1] or "").strip().upper() == "CANCELLED" and _first(node, "RECURRENCE-ID")[1] is None:
            return None
        ep, ev = _first(node, "DTEND")
        if ev is not None:
            end, end_utc, _, end_zone = self._when(ep, ev)
            length = self._aware(end, end_utc, end_zone) - self._aware(start, utc, zone)
        else:
            dur = _first(node, "DURATION")[1]
            length = _duration(dur) if dur else datetime.timedelta(0)
        if length < datetime.timedelta(0):
            length = datetime.timedelta(0)
        attendees, seen, mails = [], set(), []
        for key in ("ORGANIZER", "ATTENDEE"):
            for params, value in _all(node, key):
                if params.get("CUTYPE", "").upper() in ("ROOM", "RESOURCE") or params.get("PARTSTAT", "").upper() == "DECLINED" \
                        or params.get("ROLE", "").upper() == "NON-PARTICIPANT":
                    continue
                name = _name_of(params, value)
                mail = recipients.of_calendar(value)
                if mail and mail.casefold() not in {m.casefold() for _, m in mails}:
                    mails.append((name, mail))
                if name and name.lower() not in seen:
                    seen.add(name.lower())
                    attendees.append(name)
        rid = None
        rp, rv = _first(node, "RECURRENCE-ID")
        if rv is not None:
            r_local, r_utc, r_date, r_zone = self._when(rp, rv)
            rid = r_local.date() if r_date else self._aware(r_local, r_utc, r_zone or zone)
        exdates, exdays = set(), set()
        for params, value in _all(node, "EXDATE"):
            for one in value.split(","):
                try:
                    x_local, x_utc, x_date, x_zone = self._when(params, one)
                except ValueError:
                    continue
                if x_date:
                    exdays.add(x_local.date())
                else:
                    exdates.add(self._aware(x_local, x_utc, x_zone or zone))
        rdates = []
        for params, value in _all(node, "RDATE"):
            for one in value.split(","):
                try:
                    r_local, r_utc, r_date, r_zone = self._when(params, one.split("/")[0])
                except ValueError:
                    continue
                if not r_date:
                    rdates.append(self._aware(r_local, r_utc, r_zone or zone))
        return {"uid": (_first(node, "UID")[1] or "").strip(), "title": " ".join(unescape(_first(node, "SUMMARY")[1] or "").split()),
                "location": " ".join(unescape(_first(node, "LOCATION")[1] or "").split()),
                "start": start, "utc": utc, "zone": zone, "length": length, "attendees": attendees, "mails": mails,
                "rule": _rule(_first(node, "RRULE")[1] or ""), "exdates": exdates, "exdays": exdays, "rdates": rdates,
                "recurrence_id": rid,
                "cancelled": (_first(node, "STATUS")[1] or "").strip().upper() == "CANCELLED"}

    # -- occurrences
    def _starts(self, ev, until_local: datetime.datetime, since_local: datetime.datetime):
        """The local starts of a rule, from DTSTART on, until `until_local`. COUNT counts from DTSTART, so the first
        ones are walked even when only later ones are wanted (unless there is no COUNT: then it jumps ahead)."""
        start, rule = ev["start"], ev["rule"]
        freq = rule.get("FREQ")
        if freq not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
            yield start
            return
        try:
            interval = max(1, int(rule.get("INTERVAL", "1")))
            count = int(rule["COUNT"]) if rule.get("COUNT") else None
        except ValueError:
            yield start
            return
        until = None
        if rule.get("UNTIL"):
            u_local, u_utc, u_date = _local(rule["UNTIL"])
            until = (datetime.datetime.combine(u_local.date(), datetime.time(23, 59, 59)) if u_date
                     else self._aware(u_local, u_utc, ev["zone"]))
        days = _byday(rule.get("BYDAY", ""))
        wkst = WEEKDAYS.index(rule["WKST"]) if rule.get("WKST") in WEEKDAYS else 0
        made, steps = 0, 0

        def ok(local):
            if local < start:
                return None
            if until is not None:
                if isinstance(until, datetime.datetime) and until.tzinfo is None:
                    if local > until:
                        return False
                elif self._aware(local, ev["utc"], ev["zone"]) > until:
                    return False
            return True

        period = 0
        if count is None:       # jump to just before the window: nothing before it is needed
            gap = (since_local - start).days
            if freq == "DAILY":
                period = max(0, gap // interval - 1)
            elif freq == "WEEKLY":
                period = max(0, gap // (7 * interval) - 1)
            elif freq == "MONTHLY":
                period = max(0, (gap // 31) // interval - 1)
            else:
                period = max(0, (gap // 366) // interval - 1)
        week0 = start.date() - datetime.timedelta(days=(start.weekday() - wkst) % 7)
        while steps < MAX_STEPS:
            steps += 1
            if freq == "DAILY":
                day = start.date() + datetime.timedelta(days=period * interval)
                cands = [day] if not days or day.weekday() in [d for _, d in days] else []
                first = datetime.datetime.combine(day, start.time())
            elif freq == "WEEKLY":
                base = week0 + datetime.timedelta(weeks=period * interval)
                wds = sorted({d for _, d in days} or {start.weekday()}, key=lambda d: (d - wkst) % 7)
                cands = [base + datetime.timedelta(days=(d - wkst) % 7) for d in wds]
                first = datetime.datetime.combine(base, start.time())
            elif freq == "MONTHLY":
                m = start.month - 1 + period * interval
                y, mo = start.year + m // 12, m % 12 + 1
                first = datetime.datetime(y, mo, 1, start.hour, start.minute, start.second)
                if days:
                    cands = sorted(d for d in (_nth_weekday(y, mo, wd, nth) for nth, wd in days if nth) if d)
                else:
                    try:
                        cands = [datetime.date(y, mo, start.day)]
                    except ValueError:
                        cands = []          # the 31st of a shorter month: there is none
            else:
                y = start.year + period * interval
                first = datetime.datetime(y, 1, 1, start.hour, start.minute, start.second)
                try:
                    cands = [datetime.date(y, start.month, start.day)]
                except ValueError:
                    cands = []
            if first > until_local + datetime.timedelta(days=32):
                return
            for day in cands:
                local = datetime.datetime.combine(day, start.time())
                state = ok(local)
                if state is None:
                    continue
                if state is False or local > until_local + datetime.timedelta(days=1) and count is None:
                    return
                made += 1
                yield local
                if count is not None and made >= count:
                    return
            period += 1

    def between(self, since: datetime.datetime, until: datetime.datetime) -> list:
        """Every occurrence that overlaps [since, until) (aware datetimes), earliest first."""
        out = []
        margin = datetime.timedelta(days=2)
        for ev in self.masters:
            if "at" not in ev:
                ev["at"] = self._aware(ev["start"], ev["utc"], ev["zone"])
            if not ev["rule"] and not ev["rdates"]:         # most of a calendar: one occurrence, looked at quickly
                if not (ev["at"] < until and ev["at"] + ev["length"] > since):
                    continue
                starts = [ev["at"]]
            elif ev["at"] >= until:
                continue
            elif ev["rule"].get("COUNT") or ev["rule"].get("UNTIL"):     # a rule that ends: its starts worked out once
                if "all" not in ev:
                    far = datetime.datetime.max - datetime.timedelta(days=400)
                    ev["all"] = sorted({self._aware(s, ev["utc"], ev["zone"]) for s in self._starts(ev, far, ev["start"])} | set(ev["rdates"]))
                if not ev["all"] or ev["all"][-1] + ev["length"] <= since:
                    continue
                starts = [at for at in ev["all"] if since - ev["length"] - margin < at < until]
            else:
                since_local = since.astimezone(ev["zone"]).replace(tzinfo=None) if ev["zone"] else since.astimezone().replace(tzinfo=None)
                until_local = until.astimezone(ev["zone"]).replace(tzinfo=None) if ev["zone"] else until.astimezone().replace(tzinfo=None)
                starts = [self._aware(s, ev["utc"], ev["zone"]) for s in self._starts(ev, until_local + margin, since_local - margin - ev["length"])]
                starts += ev["rdates"]
            moved = self.moved.get(ev["uid"], []) if ev["uid"] else []
            replaced = {m["recurrence_id"] for m in moved}
            for at in sorted(set(starts)):
                local_day = at.astimezone(ev["zone"]).date() if ev["zone"] else at.astimezone().date()
                if at in ev["exdates"] or local_day in ev["exdays"] or at in replaced or local_day in replaced:
                    continue
                if at < until and at + ev["length"] > since:
                    out.append(Event(ev["uid"], ev["title"], at, at + ev["length"], list(ev["attendees"]), ev["location"], list(ev.get("mails", []))))
        for moved in self.moved.values():
            for m in moved:
                if m["cancelled"]:
                    continue
                at = self._aware(m["start"], m["utc"], m["zone"])
                if at < until and at + m["length"] > since:
                    out.append(Event(m["uid"], m["title"], at, at + m["length"], list(m["attendees"]), m["location"], list(m.get("mails", []))))
        return sorted(out, key=lambda e: (e.start, e.title))


# ---------------------------------------------------------------- courses

def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").lower()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^\w]+", " ", text).split())


def names(course: dict) -> list:
    """The names a course's events have in the calendar: its `calendar` (several, by commas or semicolons), or its name,
    and the part of its name before a dash, a colon or a bracket ("Network migration — Logística" is also "Network
    migration": the calendar says "Network migration - weekly sync")."""
    given = course.get("calendar") if isinstance(course.get("calendar"), str) else ""
    out = [fold(n) for n in re.split(r"[,;]", given) if fold(n)]
    if out:
        return out
    name = course.get("name", "") if isinstance(course.get("name"), str) else ""
    head = fold(re.split(r"\s[-–—|/]\s|[–—:(\[|]", name)[0])
    return [n for n in dict.fromkeys([fold(name), head]) if len(n) >= 3]


def match(title: str, courses: list):
    """The course whose calendar name is in the event's title as whole words; the longest name when several are."""
    said, best, size = f" {fold(title)} ", None, 0
    for course in courses:
        for n in names(course):
            if f" {n} " in said and len(n) > size:
                best, size = course, len(n)
    return best


# ---------------------------------------------------------------- the calendars, and the copy of each kept in memory

# What a calendar is called when the person gives it no name and its file has none: its site's name
SITES = (("google.com", "Google"), ("googleusercontent.com", "Google"), ("office365.com", "Outlook"), ("office.com", "Outlook"),
         ("outlook.com", "Outlook"), ("live.com", "Outlook"), ("icloud.com", "iCloud"), ("me.com", "iCloud"),
         ("yahoo.com", "Yahoo"), ("fastmail.com", "Fastmail"), ("proton.me", "Proton"))


def site_name(host: str) -> str:
    host = (host or "").lower().rstrip(".")
    for suffix, name in SITES:
        if host == suffix or host.endswith("." + suffix):
            return name
    return host[4:] if host.startswith("www.") else host


def secret_of(cid: str) -> str:
    """The keystore name of one calendar's link."""
    return f"{SECRET}-{cid}"


def _name(value) -> str:
    """A calendar's name; a link pasted in the wrong box is not one (the list must never hold one)."""
    return " ".join(value.split())[:NAME_MAX] if isinstance(value, str) and "://" not in value else ""


def _own_name(cal: "Calendar") -> str:
    """X-WR-CALNAME when it tells something: Google's is often the address, Outlook's the word "Calendar"."""
    return "" if "@" in cal.name or cal.name.lower() in NO_NAMES else cal.name


def _unique(name: str, entries: list) -> str:
    """"Google", then "Google 2": a calendar's line on the home page ("Google could not be read") says which one."""
    taken = {e["name"].lower() for e in entries}
    out, n = name, 2
    while out.lower() in taken:
        out, n = f"{name} {n}", n + 1
    return out


def _clean(raw) -> list:
    """The list, each of its own shape: {id, name, side, where} and, for the one moved from the single link, `moved` (why
    it is on its side) and `guess` (its side is still to be worked out from its events)."""
    out, ids = [], set()
    for e in raw if isinstance(raw, list) else []:
        if not isinstance(e, dict) or not isinstance(e.get("id"), str) or not ID.fullmatch(e["id"]) or e["id"] in ids:
            continue
        ids.add(e["id"])
        host = e.get("where")[:200] if isinstance(e.get("where"), str) else ""
        one = {"id": e["id"], "name": _name(e.get("name")) or site_name(host) or "?",
               "side": e.get("side") if e.get("side") in SIDES else "course", "where": host}
        if e.get("moved") in MOVED:
            one["moved"] = e["moved"]
        if e.get("guess") is True:
            one["guess"] = True
        out.append(one)
    return out[:MAX_CALENDARS]


class Feed:
    """One calendar's link and its last good copy, fetched on its own (at most every CACHE_S; RETRY_S after a failure)."""

    def __init__(self, url: str):
        self.lock = threading.Lock()
        self.url = url              # "": its link is not in the key store (looked for again after RETRY_S: `Calendars.feed`)
        self.made_at = time.monotonic()
        self.text, self.cal, self.got_at, self.tried_at = "", None, 0.0, 0.0
        self.error_kind, self.checked = ("" if url else "calendar_gone"), ""
        self.thread = None

    def took(self, text, cal):
        self.text, self.cal, self.got_at, self.tried_at, self.error_kind = text, cal, time.monotonic(), time.monotonic(), ""
        self.checked = datetime.datetime.now().isoformat(timespec="seconds")

    def _refresh(self):
        try:
            text = fetch(self.url)
            cal = Calendar(text)
            with self.lock:
                self.took(text, cal)
        except CalendarError as e:
            with self.lock:
                self.error_kind, self.tried_at = e.kind, time.monotonic()
        except Exception as e:                  # never the link in the log: only what kind of failure it was
            with self.lock:
                self.error_kind, self.tried_at = "calendar_network", time.monotonic()
            print(f"{paths.APP}: a calendar could not be read ({type(e).__name__})")

    def due(self):
        """Start a fetch when the copy is old (and the last failure not too recent); the thread fetching, or None."""
        if not self.url:
            return None
        with self.lock:
            now = time.monotonic()
            stale = not self.cal or now - self.got_at > CACHE_S
            due = stale and (not self.tried_at or now - self.tried_at > (RETRY_S if self.error_kind else 0))
            if due and not (self.thread and self.thread.is_alive()):
                self.tried_at = now
                self.thread = threading.Thread(target=self._refresh, daemon=True)
                self.thread.start()
            return self.thread if self.thread and self.thread.is_alive() else None

    def loading(self) -> bool:
        return bool(self.thread and self.thread.is_alive())


class Calendars:
    """The linked calendars: their list in calendars.json (id, name, side, the site's host; never a link), each link in keystore as calendar-url-<id>, and one Feed per calendar, made the first time it is needed (its link
    read from the key store once). Kept per data folder (tests start apps in other folders)."""

    def __init__(self):
        self.lock = threading.RLock()
        self.home, self.feeds = None, {}

    def _here(self):
        home = str(paths.data_dir())
        if self.home != home:
            self.home, self.feeds = home, {}
            try:
                self._migrate()
            except Exception as e:              # the key store refused: the single link stays where it was, for the next start
                print(f"{paths.APP}: the calendar link could not be moved yet ({type(e).__name__})")

    def entries(self) -> list:
        with self.lock:
            self._here()
            return self._read()

    def _read(self) -> list:
        path = paths.data_dir() / LIST
        if not path.exists():
            return []
        data, _ = paths.load_json(path, lambda text: {}, mark=False)     # a failure to read goes up: an empty list must not be saved over it
        return _clean(data.get("calendars"))

    def _save(self, entries: list) -> None:
        paths.write_atomic(paths.data_dir() / LIST, json.dumps({"calendars": entries}, indent=2), keep=True)

    def feed(self, cid: str) -> Feed:
        with self.lock:
            self._here()
            feed = self.feeds.get(cid)
            # a key store that did not answer (a locked Keychain) must not say "no longer works" until the app restarts
            if feed is None or (not feed.url and time.monotonic() - feed.made_at > RETRY_S):
                feed = self.feeds[cid] = Feed(keystore.get_secret(secret_of(cid)) or "")
            return feed

    def _migrate(self):
        """The single link of before (keystore SECRET) becomes the first calendar of the list, its link moved to its own
        name in the key store and the old entry removed, in an order where a stop at any step loses nothing: the new
        secret written, read back and on the disk, the list saved, and only then the old one deleted. A move cut short is
        finished at the next start, not repeated. FIRST's secret with no entry is a list that lost it (cut short, or the
        file gone): back on the list. A list that is full leaves the old link where it is, not one of the others out."""
        entries = self._read()
        if len(entries) < MAX_CALENDARS and not any(e["id"] == FIRST for e in entries):
            lost = keystore.get_secret(secret_of(FIRST))
            if lost:
                entries.insert(0, self._moved(FIRST, lost, entries))
                self._save(entries)
        old = keystore.get_secret(SECRET)
        if not old:
            return
        cid = next((e["id"] for e in entries if keystore.get_secret(secret_of(e["id"])) == old), None)
        if cid is None:
            if len(entries) >= MAX_CALENDARS:
                print(f"{paths.APP}: the calendar link of before waits: the list of calendars is full")
                return
            cid = FIRST if not any(e["id"] == FIRST for e in entries) else _new_id(entries)
            keystore.set_secret(secret_of(cid), old)
            if keystore.get_secret(secret_of(cid)) != old:
                return
            keystore.sync_secret(secret_of(cid))
            entries.insert(0, self._moved(cid, old, entries))
            self._save(entries)
        else:
            keystore.sync_secret(secret_of(cid))
        if keystore.get_secret(SECRET) == old:      # an older version running may have just been given another: that one waits for the next start
            keystore.delete_secret(SECRET)

    def _moved(self, cid: str, link: str, entries: list) -> dict:
        """The entry of a calendar moved from the single link. Its side for now: Work for an Outlook address (Microsoft
        365's published calendars are how a company's meetings come), else the side on screen, until `settle` reads it."""
        host = where(link)
        name = site_name(host) or "?"
        outlook = name == "Outlook"
        return {"id": cid, "name": _unique(name, entries), "side": "project" if outlook else paths.load_settings()["mode"],
                "where": host, "moved": "microsoft" if outlook else "side", "guess": True}

    def settle(self, lib) -> None:
        """The side of a calendar moved from the single link, once its copy is read: Work for a Microsoft calendar;
        otherwise the side whose courses or projects more of its events (60 days either side of today) are named after;
        a tie keeps the side it has. Before, one link served both sides: when the other side would have shown something
        of it too, it stays there as a second entry (`both`), so nothing leaves a home page. Done once, when first read
        (until then it stays on the side it has, and says so); the person may change it in Settings."""
        for e in self.entries():
            if not e.get("guess"):
                continue
            feed = self.feed(e["id"])
            thread = feed.due()
            if thread is not None:
                thread.join(WAIT_S)
            cal = feed.cal
            if cal is None:             # not read yet, or it cannot be read now: it is worked out when it is
                continue
            now = datetime.datetime.now(UTC)
            seen = cal.between(now - datetime.timedelta(days=60), now + datetime.timedelta(days=60))
            courses = lib.courses()
            of = {k: [c for c in courses if c.get("kind") == k] for k in SIDES}
            count = {k: sum(1 for ev in seen if match(ev.title, of[k])) for k in SIDES}
            if cal.microsoft:
                side, why = "project", "microsoft"
            elif count["project"] != count["course"]:
                side = "project" if count["project"] > count["course"] else "course"
                why = "projects" if side == "project" else "courses"
            else:
                side, why = e["side"], "side"
            other = "course" if side == "project" else "project"
            # what that side's home page would have shown of it: what now_next offers there, the meetings of no project included
            both = bool(of[other]) and (count[other] > 0 or (other == "project" and any(ev.attendees and not match(ev.title, of["course"]) for ev in seen)))
            own = _own_name(cal)
            with self.lock:
                entries = self.entries()
                one = next((x for x in entries if x["id"] == e["id"] and x.get("guess")), None)     # not changed by hand meanwhile
                if one is None:
                    continue
                one.update(side=side, moved=why)
                one.pop("guess")
                if own and one["name"] == site_name(one["where"]):       # the calendar's own name, as when it is added
                    one["name"] = _unique(own, [x for x in entries if x is not one])
                twin = one["id"] + "b"
                if both and feed.url and len(entries) < MAX_CALENDARS and not any(x["id"] == twin for x in entries):
                    try:
                        keystore.set_secret(secret_of(twin), feed.url)
                    except (OSError, ValueError):       # the key store refused: it stays on one side, and says why
                        print(f"{paths.APP}: a calendar could not be put on both sides")
                    else:
                        one["moved"] = "both"
                        entries.insert(entries.index(one) + 1, {"id": twin, "name": _unique(one["name"], entries), "side": other,
                                                                "where": one["where"], "moved": "both"})
                        self.feeds[twin] = Feed(feed.url)
                        self.feeds[twin].took(feed.text, cal)
                self._save(entries)

    def read(self, entries: list, wait: float = WAIT_S) -> list:
        """[(entry, Feed)] with each copy as fresh as it gets within `wait`: every calendar due is fetched at once, each
        in its own thread; one with no copy yet is waited for up to `wait`, an old copy only STALE_WAIT_S (it is shown
        meanwhile, and the page asks again while one is still being read)."""
        feeds = [(e, self.feed(e["id"])) for e in entries]
        started_at = time.monotonic()
        threads = [(f, f.due()) for _, f in feeds]
        for f, thread in threads:
            if thread is None:
                continue
            left = started_at + (wait if f.cal is None else min(wait, STALE_WAIT_S)) - time.monotonic()
            if left > 0:
                thread.join(left)
        return feeds

    def state(self) -> dict:
        """What Settings shows: each calendar's name, side, site, how its last reading went. Never a link."""
        out = []
        for e in self.entries():
            f = self.feed(e["id"])
            out.append({"id": e["id"], "name": e["name"], "side": e["side"], "where": e["where"], "moved": e.get("moved", ""),
                        "error_kind": f.error_kind, "checked": f.checked})
        return {"calendars": out, "max": MAX_CALENDARS}

    def _room(self, url: str, side: str) -> list:
        """One calendar that holds both classes and meetings may be linked once for each side."""
        entries = self.entries()
        if len(entries) >= MAX_CALENDARS:
            raise CalendarError("calendar_too_many", f"At most {MAX_CALENDARS} calendars can be linked.")
        if any(e["side"] == side and self.feed(e["id"]).url == url for e in entries):
            raise CalendarError("calendar_twice", "That calendar is already linked on that side.")
        return entries

    def add(self, url: str, side: str, name: str = "") -> dict:
        """Check the link by fetching it (outside the lock: the home page is not held meanwhile), then keep it. A link
        that does not work is not kept. Its name: the one given, or the calendar's own (X-WR-CALNAME, unless it is an
        address), or its site's."""
        url = clean_url(url)
        if side not in SIDES:
            raise CalendarError("bad_request", "Say whether the calendar is for school or for work.")
        self._room(url, side)
        text = fetch(url)
        cal = Calendar(text)
        with self.lock:
            entries = self._room(url, side)
            cid = _new_id(entries)
            try:
                keystore.set_secret(secret_of(cid), url)
            except ValueError:
                raise CalendarError("calendar_address", "That is not a calendar link.") from None
            own = _own_name(cal)
            entry = {"id": cid, "name": _name(name) or _unique(own or site_name(where(url)) or "?", entries), "side": side, "where": where(url)}
            try:
                self._save(entries + [entry])
            except BaseException:
                keystore.delete_secret(secret_of(cid))
                raise
            feed = Feed(url)
            feed.took(text, cal)
            self.feeds[cid] = feed
            return entry

    def update(self, cid: str, name=None, side=None) -> dict:
        with self.lock:
            entries = self.entries()
            e = next((x for x in entries if x["id"] == cid), None)
            if e is None:
                raise library.NotFound("That calendar is no longer linked.")
            if side is not None:
                if side not in SIDES:
                    raise CalendarError("bad_request", "Say whether the calendar is for school or for work.")
                e["side"] = side
            if name is not None:
                e["name"] = _name(name) or site_name(e["where"]) or e["name"]
            e.pop("moved", None)            # looked at: the note of why it was put on its side has done its work
            e.pop("guess", None)
            self._save(entries)
            return e

    def remove(self, cid: str) -> None:
        """The link out of the key store first, then the calendar off the list: an interrupted removal never leaves a
        link behind (a calendar left without one says it no longer works, and can be removed again)."""
        with self.lock:
            entries = self.entries()
            if not any(e["id"] == cid for e in entries):
                raise library.NotFound("That calendar is no longer linked.")
            keystore.delete_secret(secret_of(cid))
            self.feeds.pop(cid, None)
            self._save([e for e in entries if e["id"] != cid])

    def forget(self) -> None:
        """Every calendar removed (the tests' clean-up)."""
        for e in self.entries():
            self.remove(e["id"])


def _new_id(entries: list) -> str:
    taken = {e["id"] for e in entries}
    while True:
        cid = secrets.token_hex(4)
        if cid not in taken:
            return cid


CALENDARS = Calendars()
STARTING = threading.Lock()     # Start looks for the class made from an event, and makes it, as one step


# ---------------------------------------------------------------- the home page

def started(lib, slug: str, day: datetime.date, classes: list | None = None) -> dict:
    """{(uid, start): class id} of the classes of `slug` made from an event on that day (`classes`: the course's list,
    when the caller has it already)."""
    out = {}
    if classes is None:
        try:
            classes = lib.course(slug)["classes"]
        except Exception:
            return out
    for c in classes:
        if not c["id"].startswith(day.isoformat()):
            continue
        try:
            got = lib.meta(slug, c["id"]).get("calendar")
        except Exception:
            continue
        if isinstance(got, dict) and got.get("uid") is not None:
            out[(got.get("uid"), got.get("start"))] = c["id"]
    return out


def _item(e: Event, course, made: dict, cal_id: str = "") -> dict:
    """One event for the page. An event already started somewhere (a meeting of no project put in one) is that class.
    `cal`: the calendar it came from, which Start names back."""
    start = e.start.astimezone().replace(tzinfo=None).isoformat(timespec="minutes")
    there = made.get((e.uid, start))
    if there:
        course, cid = there
    else:
        cid = ""
    return {"uid": e.uid, "start": start, "end": e.end.astimezone().replace(tzinfo=None).isoformat(timespec="minutes"),
            "title": e.title, "attendees": e.attendees, "location": e.location,
            "slug": course["slug"] if course else "", "course": course["name"] if course else "", "cid": cid, "cal": cal_id}


def now_next(cal, courses: list, kind: str, now: datetime.datetime, lib=None, others: list = ()) -> dict:
    """What is on now (it started, or starts within EARLY) and the next thing today, for one side, from one Calendar or
    from [(calendar id, Calendar)] of that side's calendars, merged in time order. An event in two of them (the same UID
    and start, or the same title and start) shows once, from the first. On the work side an event of no project is
    offered as a meeting when it has attendees, or in a calendar that never lists them (Outlook's published ones: the
    rule of the calendar it came from), unless it is a class of a course of the other side (`others`)."""
    sources = [("", cal)] if isinstance(cal, Calendar) else list(cal)
    now = now.astimezone()
    end_of_day = datetime.datetime.combine(now.date() + datetime.timedelta(days=1), datetime.time(0)).astimezone()
    events = []
    for order, (cal_id, one) in enumerate(sources):
        for e in one.between(now.astimezone(UTC), end_of_day.astimezone(UTC)):
            events.append((e.start, order, e, cal_id, one.no_attendees))
    events.sort(key=lambda x: x[:2])            # stable: one calendar's events at the same time stay in its own order
    picked, seen = [], set()
    for _, _, e, cal_id, quiet in events:
        course = match(e.title, courses)
        if not (course or (kind == "project" and (e.attendees or quiet) and not match(e.title, others))):
            continue                # not one to show, here: another calendar's copy of it may be (Outlook's, with no attendees)
        keys = {("title", fold(e.title), e.start)} | ({("uid", e.uid, e.start)} if e.uid else set())
        if keys & seen:
            continue
        seen |= keys
        picked.append((e, course, cal_id))
    made = {}
    if lib is not None and picked:
        for course in courses:      # the classes of today only: a few class.json at most
            for key, cid in started(lib, course["slug"], now.date(), course.get("classes")).items():
                made[key] = (course, cid)
    on = [p for p in picked if p[0].start - EARLY <= now < p[0].end]
    later = [p for p in picked if p[0].start - EARLY > now]
    return {"now": [_item(e, c, made, k) for e, c, k in on[:3]], "next": [_item(e, c, made, k) for e, c, k in later[:1]]}


def home_part(lib, courses: list, kind: str) -> dict:
    """home.PARTS: `calendar` in the home page's answer, from the calendars of the side shown only; nothing when that
    side has none. A calendar that could not be read is named in `troubles`; the others' events still show."""
    entries = CALENDARS.entries()
    if not entries:
        return {}
    if any(e.get("guess") for e in entries):
        CALENDARS.settle(lib)
        entries = CALENDARS.entries()
    mine = [e for e in entries if e["side"] == kind]
    if not mine:
        return {}
    got = CALENDARS.read(mine)
    troubles = [{"id": e["id"], "name": e["name"], "kind": f.error_kind} for e, f in got if f.error_kind]
    out = {"on": True, "loading": any(f.loading() for _, f in got), "error_kind": troubles[0]["kind"] if troubles else "",
           "troubles": troubles, "calendars": [{"id": e["id"], "name": e["name"]} for e in mine], "now": [], "next": []}
    cals = [(e["id"], f.cal) for e, f in got if f.cal is not None]
    if cals:
        others = [c for c in lib.courses() if c.get("kind") != kind] if kind == "project" else []
        out.update(now_next(cals, courses, kind, datetime.datetime.now().astimezone(), lib, others))
    return {"calendar": out}


def find(uid: str, start: str, cal: str = "", side: str = ""):
    """The occurrence of `uid` that starts at `start` (local, as home_part gave it) in the copy kept of calendar `cal`
    (any of `side`'s when none is named), or None. A calendar of the other side never gives one: the event's calendar
    decides the side of the class made from it."""
    try:
        at = datetime.datetime.fromisoformat(start).astimezone()
    except (TypeError, ValueError):
        return None
    entries = [e for e in CALENDARS.entries() if (not cal or e["id"] == cal) and (not side or e["side"] == side)]
    for _, f in CALENDARS.read(entries):
        if f.cal is None:
            continue
        for e in f.cal.between((at - datetime.timedelta(minutes=1)).astimezone(UTC), (at + datetime.timedelta(minutes=1)).astimezone(UTC)):
            if e.uid == uid and e.start.astimezone().replace(tzinfo=None).isoformat(timespec="minutes") == start:
                return e
    return None


if home_part not in home.PARTS:
    home.PARTS.append(home_part)
