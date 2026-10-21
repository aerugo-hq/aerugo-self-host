"""Who gets in, and what they're a member of (G4, and the org's membership).

Groups: a person's groups come from their signed token (the identity provider's groups, through Keycloak), so the
token is the truth for which groups they're in. OpenFGA has to hold it too, or a team granted to a group would refuse
the group's members: each authenticated request makes OpenFGA's `user:<u> member group:<g>` tuples match the token,
writing the groups joined and deleting the groups left, checked at most every SYNC_SECONDS for an unchanged set. A
person leaving a group in the identity provider loses it at their next token.

The org: someone the company's identity provider signs in is one of its people, so each request also records
`user:<u> member org:<org>`, once. Without it, a document a source shares with the whole company (a Confluence space
anyone may read, a public Slack channel, a Drive file shared with the domain) reached only org admins: row-level
security let everyone in, and OpenFGA's post-check, knowing no one's membership but the dev seed's, let no one. Leaving
the company is the identity provider's to decide: someone it no longer signs in makes no request.

Admission: an org admin chooses `org` (anyone signed in, as before) or `teams` (a pilot: only people on an onboarded
team, and org admins), read through kl.admission_mode (migration 0056). On an onboarded team means a role granted to
the person or to one of their groups: a team granted to the whole org (a commons anyone may join) onboards nobody,
or a pilot would let everyone in. Service accounts always pass (an org admin registers each one), and are never made
members of a group or the org: their grants are their own.

All of it fails closed: when OpenFGA or Postgres can't answer, the request gets a 503, never an admission or a skipped
removal. Nothing here grants a document; it records who someone is, and narrows who gets in."""
import logging
import time

import httpx
import psycopg
from fastapi import HTTPException

from . import config
from .auth import Caller
from .authz import fga

log = logging.getLogger("kl.admission")
SYNC_SECONDS = 300      # an unchanged set of groups is compared with OpenFGA at most this often
MODE_SECONDS = 30       # how long a process trusts the mode it read; one that changes it forgets at once
ADMITTED_SECONDS = 60   # how long an admission holds; a refusal is never kept, so onboarding takes effect at once
CACHE_LIMIT = 10_000    # ponytail: each cache is emptied whole when full; an LRU if a deployment outgrows it
TEAM_ROLES = ("admin", "curator", "member", "auditor")
_synced: dict[str, tuple[frozenset[str], float]] = {}
_admitted: dict[str, float] = {}
_mode: tuple[str, float] = ("org", 0.0)


def _remember(cache: dict, key: str, value) -> None:
    if len(cache) >= CACHE_LIMIT:
        cache.clear()
    cache[key] = value


def group_changes(user: str, held: set[str], token_groups: set[str]) -> tuple[set, set]:
    """The tuples to write and to delete so OpenFGA holds exactly the token's groups for this user (`held`: the groups
    OpenFGA has them in now)."""
    member = lambda group: (user, "member", f"group:{group}")
    return {member(g) for g in token_groups - held}, {member(g) for g in held - token_groups}


SIGN_IN_SQL = """INSERT INTO person_sign_ins (org_id, username, groups) VALUES (%s, %s, %s)
ON CONFLICT (org_id, username) DO UPDATE SET groups = EXCLUDED.groups, signed_in_at = now()"""


def record_sign_in(caller: Caller) -> None:
    """A person's verified sign-in and the groups it named (C2d): a Slack ask runs as them with these, only while recent."""
    from .db import connect, resolve_org_id
    org_id = resolve_org_id(config.ORG_SLUG)
    with connect() as conn:
        conn.execute("SELECT set_config('kl.org_id', %s, true)", (org_id,))
        conn.execute(SIGN_IN_SQL, (org_id, caller.username, sorted(caller.groups)))


def sync_memberships(caller: Caller) -> None:
    """Make OpenFGA hold the caller's groups as their token says, and their membership of the org, and record the
    sign-in (C2d)."""
    if caller.service:  # an automation account's grants are its own, never a group's or the org's
        return
    groups = frozenset(caller.groups)
    seen = _synced.get(caller.fga_user)
    if seen and seen[0] == groups and seen[1] > time.monotonic():
        return
    held = {t["object"].split(":", 1)[1] for t in fga.read({"user": caller.fga_user, "relation": "member", "object": "group:"})}
    writes, deletes = group_changes(caller.fga_user, held, set(groups))
    org = (caller.fga_user, "member", f"org:{caller.org_slug}")
    if not fga.read({"user": org[0], "relation": org[1], "object": org[2]}):
        writes.add(org)
    if writes or deletes:
        fga.write(writes=writes, deletes=deletes)
        _admitted.pop(caller.fga_user, None)  # their teams may have changed with their groups
        log.info("a person's memberships now match their token: %d added, %d left", len(writes), len(deletes))
    record_sign_in(caller)
    _remember(_synced, caller.fga_user, (groups, time.monotonic() + SYNC_SECONDS))


def mode() -> str:
    global _mode
    if _mode[1] <= time.monotonic():
        from .db import connect  # here, so the MCP server reaches the database only when it authenticates someone
        with connect() as conn:
            row = conn.execute("SELECT kl.admission_mode(%s) AS mode", (config.ORG_SLUG,)).fetchone()
        _mode = (row["mode"], time.monotonic() + MODE_SECONDS)
    return _mode[0]


def forget_mode() -> None:
    """This process just changed the mode: read it again on the next request. Other processes see it within MODE_SECONDS."""
    global _mode
    _mode = ("org", 0.0)


def own_teams(caller: Caller) -> set[str]:
    """The teams a person holds a role on, granted to them or to one of their groups. A grant to the whole org doesn't
    count: it would make everyone a member of that team (G4's pilot, F5a's gaps)."""
    out: set[str] = set()
    for who in (caller.fga_user, *(f"group:{g}#member" for g in caller.groups)):
        out |= {t["object"].split(":", 1)[1] for t in fga.read({"user": who, "object": "workspace:"}) if t["relation"] in TEAM_ROLES}
    return out


def onboarded(caller: Caller) -> bool:
    """A role on a team, granted to the caller or to one of their groups. A grant to the whole org doesn't count."""
    return bool(own_teams(caller))


def admitted(caller: Caller) -> bool:
    if caller.service or mode() == "org":
        return True
    until = _admitted.get(caller.fga_user)
    if until and until > time.monotonic():
        return True
    ok = onboarded(caller) or fga.check(caller.fga_user, "admin", f"org:{config.ORG_SLUG}")
    if ok:
        _remember(_admitted, caller.fga_user, time.monotonic() + ADMITTED_SECONDS)
    return ok


def unavailable(e: Exception) -> HTTPException:
    log.warning("who gets in couldn't be checked (%s)", type(e).__name__)
    return HTTPException(503, "sign-in can't be checked right now; try again shortly", headers={"Retry-After": "5"})


def admit(caller: Caller) -> Caller:
    """Bring the caller's memberships up to date, then refuse anyone the org's admission leaves out (403)."""
    try:
        sync_memberships(caller)
        ok = admitted(caller)
    except (httpx.HTTPError, psycopg.Error, RuntimeError) as e:
        raise unavailable(e)
    if not ok:
        raise HTTPException(403, "your team isn't onboarded to Aerugo yet: ask your Aerugo administrator")
    return caller
