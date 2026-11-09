"""What the home page shows for one side, school or work (GET /api/home?kind=course|project).

  needs     what needs the user now: classes recording (in a tab that is heard from), being transcribed or written
            (with the transcriber's progress), failed (Try again) and recordings left unfinished (Finish)
  recent    the finished classes of the last RECENT_DAYS days, at least the RECENT_MIN latest ones, newest first,
            with their opening idea and section titles
  courses   how many courses (projects) that side has, `classes` and `done` how many classes and finished ones

Computed on every call from Library.courses(), whose class list is kept against each class.json's stat (and says
which classes a tab records now, `rec_live`), plus the class.json of the few classes being written: nothing is stored. A later feature adds its part
to PARTS: a function (library, the courses of that side, kind) returning keys to add to the answer. Its tile on the
page goes to HOME_TILES in web/app.js."""
from __future__ import annotations

import datetime
import traceback

from . import kinds, library

RECENT_DAYS = 7
RECENT_MIN = 3          # a quiet week still shows the latest classes
RECENT_MAX = 30
PARTS: list = []        # fn(lib, courses, kind) -> dict, merged into the answer; one that fails is left out


def _day(cid: str):
    try:
        return datetime.date.fromisoformat(cid[:10])
    except ValueError:          # a class folder named by hand: not dated, so not "recent"
        return None


def build(lib: library.Library, kind: str, today: datetime.date | None = None) -> dict:
    kind = kinds.clean(kind)
    courses = [c for c in lib.courses() if c["kind"] == kind]
    since = (today or datetime.date.today()) - datetime.timedelta(days=RECENT_DAYS)
    needs = {"recording": [], "writing": [], "failed": [], "unfinished": []}
    done, total = [], 0
    for course in courses:
        for c in course["classes"]:
            total += 1
            item = {"slug": course["slug"], "course": course["name"], "id": c["id"], "title": c["title"],
                    "duration_s": c.get("duration_s") or 0}
            try:
                if c.get("rec_live"):                # a tab records it now (Library.course works that out)
                    needs["recording"].append(item)
                elif c["status"] == "recording":     # recorded, and no tab records it any more: it waits for Finish
                    needs["unfinished"].append(item)
                elif c["status"] == "processing":
                    meta = lib.meta(course["slug"], c["id"])
                    needs["writing"].append({**item, **{k: meta.get(k) for k in ("step", "progress", "eta_s")}})
                elif c["status"] == "error":
                    needs["failed"].append({**item, "error_kind": c.get("error_kind") or "", "has_note": bool(c.get("has_note"))})
                elif c["status"] == "done":
                    done.append({**item, "summary": c.get("summary", ""), "topics": c.get("topics", []), "day": _day(c["id"])})
            except library.NotFound:                # deleted while this was being worked out
                continue
    for group in needs.values():
        group.sort(key=lambda x: x["id"], reverse=True)
    done.sort(key=lambda x: x["id"], reverse=True)
    recent = [c for i, c in enumerate(done) if i < RECENT_MIN or (c["day"] and c["day"] >= since)][:RECENT_MAX]
    out = {"kind": kind, "needs": needs, "recent": [{k: v for k, v in c.items() if k != "day"} for c in recent],
           "courses": len(courses), "classes": total, "done": len(done)}
    for part in PARTS:
        try:
            out.update(part(lib, courses, kind) or {})
        except Exception:                           # one part never takes the home page down
            traceback.print_exc()
    return out
