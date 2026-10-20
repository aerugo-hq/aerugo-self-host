"""Who is asking (F8, docs/ENTERPRISE_PLAN.md section 4, principles 5 and 6): the asker's teams, and from their own
listing in the company's directory (by the email their identity provider verified, in a directory that isn't paused,
only while it says they're still there) their role (title and department), their place (office, city, state, country),
and their manager.

Every answer uses it: Ask tells the model who's asking (their role and place, nothing else, and only to a model their
team chose, under the egress rules), and F4's `scope_fit` matches a unit's roles and places to the asker's, so a
Colorado associate asking about their sick leave gets Colorado's rule and never Oregon's, while a unit for another role
or place still answers someone asking about it. A context entry that isn't known stays None, and a unit scoped to it is
used with its scope said.

Contacts resolve to a role's current holder, never a name kept in text: up the asker's manager chain first (their
district manager is their manager's manager), else the one person still at the company with that title in the unit's
places (or the asker's); two such people, or none, is no holder. A holder is shown by name and title only.

A person's own context is shown only to them (`/v1/whoami`)."""
from __future__ import annotations

from .policy import memberships

CHAIN = 6  # managers walked up, at most
LISTING_COLUMNS = "p.email, p.display_name, p.title, p.department, p.office, p.city, p.state, p.country, p.manager_email"
LISTED = "JOIN connections c ON c.id = p.connection_id AND c.kind = 'directory' AND c.status <> 'disabled'"
LISTING_SQL = f"SELECT {LISTING_COLUMNS} FROM directory_people p {LISTED} WHERE p.email = %s AND p.active ORDER BY p.seen_at DESC LIMIT 1"
TITLED_SQL = f"""SELECT {LISTING_COLUMNS} FROM directory_people p {LISTED}
WHERE p.active AND (lower(p.title) = lower(%(role)s) OR lower(p.title) LIKE lower(%(prefix)s) ESCAPE '\\') LIMIT 50"""
PLACE_FIELDS = ("office", "city", "state", "country")


def role_matches(role: str, title: str | None) -> bool:
    """A role names a title when it's the title, or the title's head before a comma ("District manager" names
    "District manager, Mountain")."""
    if not title:
        return False
    role, title = role.strip().lower(), title.strip().lower()
    return bool(role) and (title == role or title.startswith(role + ","))


def roles_of(me: dict) -> set[str]:
    return {v.strip() for v in (me.get("title"), me.get("department")) if v and v.strip()}


def places_of(me: dict) -> set[str]:
    return {me[f].strip() for f in PLACE_FIELDS if me.get(f) and me[f].strip()}


def asked_by(me: dict | None) -> str:
    """The line that tells a model who's asking, or nothing when the directory doesn't list them."""
    if not me:
        return ""
    role = ", ".join(v for v in (me.get("title"), me.get("department")) if v)
    place = ", ".join(v for v in (me.get("office"), me.get("city"), me.get("state"), me.get("country")) if v)
    said = " ".join(x for x in (role, f"at {place}" if place else "") if x)
    return f"Asked by: {said}." if said else ""


def holder_in(role: str, me: dict | None, by_email: dict[str, dict], titled: list[dict], places: set[str]) -> dict | None:
    """The current holder of a role for this asker, from active listings: up their manager chain, else the one person
    with that title in these places. Never the asker themselves, never two candidates."""
    seen, email = set(), (me or {}).get("manager_email")
    for _ in range(CHAIN):
        if not email or email in seen or email not in by_email:
            break
        seen.add(email)
        boss = by_email[email]
        if role_matches(role, boss.get("title")):
            return boss
        email = boss.get("manager_email")
    wanted = {p.lower() for p in places}
    here = [p for p in titled if role_matches(role, p.get("title")) and p["email"] != (me or {}).get("email")
            and wanted & {v.lower() for v in places_of(p)}]
    return here[0] if len(here) == 1 else None


def listing(conn, email: str | None) -> dict | None:
    return conn.execute(LISTING_SQL, (email.lower(),)).fetchone() if email else None


def of(conn, caller) -> dict:
    """The asker's context: their teams, and their role, place, and listing, or None for each the directory doesn't say."""
    me = None if caller.service else listing(conn, caller.email)
    return {"teams": memberships(caller), "roles": roles_of(me) if me else None, "places": places_of(me) if me else None, "me": me}


def holder(conn, context: dict, role: str, places: list[str]) -> dict | None:
    """A role's current holder for this asker (name and title), read from the directory as `holder_in` decides."""
    me = context.get("me")
    by_email: dict[str, dict] = {}
    email = (me or {}).get("manager_email")
    for _ in range(CHAIN):  # the chain, one listing at a time
        if not email or email in by_email:
            break
        boss = listing(conn, email)
        if not boss:
            break
        by_email[email] = boss
        email = boss.get("manager_email")
    prefix = role.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + ",%"
    titled = conn.execute(TITLED_SQL, {"role": role, "prefix": prefix}).fetchall()
    found = holder_in(role, me, by_email, titled, set(places) or (context.get("places") or set()))
    return {"name": found["display_name"], "title": found["title"]} if found else None


def mine(context: dict) -> dict | None:
    """What the directory says about the asker, for them alone."""
    me = context.get("me")
    return {k: me.get(k) for k in ("title", "department", *PLACE_FIELDS)} if me else None
