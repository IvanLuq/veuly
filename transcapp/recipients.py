"""Who a meeting's minutes go to: the addresses the E-mail button puts in the new message's To.

The app sends nothing. The page opens a message in the person's own mail program with these in To, and they read it and send
it. A class keeps them as `mail_to` in class.json (from its calendar event, or as typed there), a project as `mail_to` in
course.json (its usual people, for a meeting with none of its own: an Outlook calendar lists no attendees). They are the
person's data like the attendees' names, but they go nowhere else: never to Claude, the maps, a vault or Confluence, so a new
request or export takes them only by asking for `mail_to`, which nothing else does.
"""
from __future__ import annotations

import re

MAX = 40                    # addresses of one message: a mailto: link has room for few more
TEXT_MAX = 2000
# No character that means something in a mailto: link or a mail header (?, &, =, %, #, spaces, line ends) gets through.
ADDRESS = re.compile(r"[A-Za-z0-9.!$'*+/^_`{|}~\- -￿]{1,64}@[A-Za-z0-9 -￿](?:[A-Za-z0-9.\- -￿]{0,250}[A-Za-z0-9 -￿])?\.[A-Za-z -￿]{2,}")
_IN_ANGLES = re.compile(r"<([^<>]*)>")


class BadAddress(ValueError):
    """Something typed that is not an address (the text says which)."""


def parse(text, strict: bool = True) -> list:
    """The addresses in what was typed or pasted: separated by commas, semicolons, spaces or lines; `Ana Ruiz <ana@x.com>` as
    a mail program copies it counts as its address. Each once, whatever its case. `strict`: anything else is refused
    (BadAddress); otherwise it is left out (what comes from a calendar or a file the person can edit)."""
    text = str(text or "")[:TEXT_MAX]
    if "<" in text:
        # the name before <…> is not an address: only what is in the angles counts, on each part that has them
        text = ",".join(_IN_ANGLES.search(part).group(1) if _IN_ANGLES.search(part) else part for part in re.split(r"[,;\n]+", text))
    out, seen = [], set()
    for word in re.split(r"[\s,;]+", text):
        word = word.strip()
        if not word:
            continue
        if not ADDRESS.fullmatch(word) or ".." in word or len(word) > 254:
            if strict:
                raise BadAddress(word[:80])
            continue
        if word.casefold() not in seen and len(out) < MAX:
            seen.add(word.casefold())
            out.append(word)
    return out


def of_calendar(value) -> str:
    """The address of an ATTENDEE or ORGANIZER line (`mailto:ana@x.com`), or "" when it holds none."""
    mail = re.sub(r"^mailto:", "", str(value or "").strip(), flags=re.I)
    return mail if ADDRESS.fullmatch(mail) and ".." not in mail and len(mail) <= 254 else ""


def join(addresses) -> str:
    return ", ".join(addresses)
