"""Knowledge health (E2, K10): what about each of a team's curated pages needs someone, said plainly.

- Stale: never verified, or last reviewed longer ago than the page's review interval (x-kl `review_days`, else 90).
- Conflicting: it shares a name (an alias) with another page, so the name finds neither, or a newer page replaces it
  and it's still live.
- Orphaned: nothing links to it, no page relates to it and no document points at it.
- No owner: none is named, its owning team doesn't exist, or every person named has left.

A team's admins work the queue: confirm a page (it counts as reviewed now), ask for an update, or retire it (search
and Ask leave it out until someone confirms it again). Every action is recorded. Pure, so the API decides what may be
seen, from what the caller may read only, and this only weighs it."""
from datetime import datetime

REVIEW_DAYS = 90
ACTIONS = ("confirm", "update", "retire")


def _when(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def issues(page: dict, now: datetime) -> list[dict]:
    """page: {"meta", "last_review": {"action", "by", "at"} | None, "conflicts": [(alias, other title)], "replaced_by": [titles],
    "inbound": int, "left": [owners who have left], "teams": set of team slugs}. Returns [{"kind", "text"}], worst first."""
    meta, review = page.get("meta") or {}, page.get("last_review")
    xkl = meta.get("x-kl") or {}
    out = []
    verified = [_when(v.get("at")) for v in (meta.get("verified") if isinstance(meta.get("verified"), list) else [meta.get("verified")])
                if isinstance(v, dict)]
    reviewed = max([v for v in verified if v] + ([_when(review["at"])] if review and review["action"] == "confirm" else []), default=None)
    every = xkl.get("review_days") if isinstance(xkl.get("review_days"), int) and 1 <= xkl.get("review_days") <= 3650 else REVIEW_DAYS
    for alias, other in page.get("conflicts") or []:
        out.append({"kind": "conflicting", "text": f"Shares the name “{alias}” with {other}, so that name finds neither page."})
    for newer in page.get("replaced_by") or []:
        out.append({"kind": "conflicting", "text": f"{newer} replaces it, but it's still live."})
    if reviewed is None:
        out.append({"kind": "stale", "text": "Never verified or confirmed."})
    elif (now - reviewed).days > every:
        out.append({"kind": "stale", "text": f"Last reviewed {(now - reviewed).days} days ago; it's due every {every}."})
    owners = [str(o) for o in xkl.get("owners") or []]
    groups = [o.partition(":")[2] for o in owners if o.startswith("group:")]
    people = [o.partition(":")[2] for o in owners if o.startswith(("human:", "user:"))]
    missing = [g for g in groups if g not in (page.get("teams") or set())]
    gone = [p for p in people if p in (page.get("left") or [])]
    if not owners:
        out.append({"kind": "ownerless", "text": "No owner is named."})
    elif missing and len(missing) == len(groups) and not people:
        out.append({"kind": "ownerless", "text": f"Its owning team, {', '.join(missing)}, doesn't exist."})
    elif gone and len(gone) == len(people) and not groups:
        out.append({"kind": "ownerless", "text": f"Everyone it names as owner has left: {', '.join(gone)}."})
    if not page.get("inbound"):
        out.append({"kind": "orphaned", "text": "Nothing links to it: no page relates to it, and no document points at it."})
    return out


if __name__ == "__main__":
    now = datetime.fromisoformat("2026-10-16T00:00:00+00:00")
    fresh = {"meta": {"verified": {"by": "human:alice", "at": "2026-10-01T00:00:00Z"}, "x-kl": {"owners": ["group:payments"]}},
             "inbound": 2, "teams": {"payments"}}
    assert issues(fresh, now) == [], issues(fresh, now)
    worn = {"meta": {"x-kl": {"owners": ["human:zed"]}}, "left": ["zed"], "conflicts": [("ledger", "Ledger service")], "inbound": 0}
    assert [i["kind"] for i in issues(worn, now)] == ["conflicting", "stale", "ownerless", "orphaned"], issues(worn, now)
    confirmed = {**worn, "last_review": {"action": "confirm", "by": "alice", "at": "2026-10-15T00:00:00+00:00"}}
    assert "stale" not in [i["kind"] for i in issues(confirmed, now)]
    print("health: ok")
