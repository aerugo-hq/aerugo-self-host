"""Identity matching (ADR 0011): a person who signs in is linked to the source accounts that report their verified
company email, so they see what those accounts may see without linking anything by hand.

It fails closed. Only an email the company's SSO verified counts, and only in a domain the company declared as its own
(KL_ORG_EMAIL_DOMAINS). It's matched only against what a connector's own account reported for a source account, for
every source alike, so no account a connector never saw is linked. A match never takes over an account someone else
holds. Matches made this way are re-checked at every sign-in and removed once they no longer hold (an email changed, a
source dropped the account); links a person proved by signing in to the source are never touched."""
import logging
import re

from . import config
from .authz import fga
from .principals import is_valid

log = logging.getLogger("kl.identity")
VERIFIED_BY = "sso-email"
EMAIL = re.compile(r"^[a-z0-9._%+-]{1,64}@[a-z0-9-]{1,63}(\.[a-z0-9-]{1,63})*\.[a-z]{2,24}$")


def company_email(email: str | None) -> str | None:
    """The email, if it's well formed and in a domain the company declared as its own; otherwise nothing."""
    email = (email or "").strip().lower()
    if not EMAIL.fullmatch(email):
        return None
    return email if email.rpartition("@")[2] in config.ORG_EMAIL_DOMAINS else None


def match(conn, org_id, caller) -> dict:
    """Link the caller to the source accounts that report their verified company email, and drop matches that no
    longer hold. Never raises: a failure links nothing new and leaves sign-in working."""
    try:
        return _match(conn, org_id, caller)
    except Exception:  # noqa: BLE001 - matching must never stop someone signing in
        log.exception("identity matching failed for %s", caller.username)
        return {"linked": [], "unlinked": []}


def _match(conn, org_id, caller) -> dict:
    me = f"user:{caller.username}"
    email = company_email(getattr(caller, "email", None))
    with conn.transaction():  # a savepoint: a failure here rolls back only this, never the request around it
        add, drop, mine = _aliases(conn, org_id, me, email)
    # Grants for every match held, not only new ones, so a grant that failed to write last time is written now.
    owner = lambda source: (me, "owner", f"identity:{source[4:]}")
    exists = lambda t: bool(fga.read({"user": t[0], "relation": t[1], "object": t[2]}))
    writes = {owner(s) for s in mine if not exists(owner(s))}
    deletes = {owner(s) for s in drop if exists(owner(s))}
    if writes or deletes:
        fga.write(writes=writes, deletes=deletes)
    return {"linked": add, "unlinked": drop}


def _aliases(conn, org_id, me: str, email: str | None) -> tuple[list[str], list[str], set[str]]:
    """The aliases to add, the ones to drop, and every email match the person holds once this runs."""
    conn.execute("""INSERT INTO people (org_id, username, email) VALUES (%s, %s, %s)
                    ON CONFLICT (org_id, username) DO UPDATE SET email = EXCLUDED.email, seen_at = now()""",
                 (org_id, me[len("user:"):], email))
    if email and conn.execute("SELECT 1 FROM directory_people WHERE email = %s HAVING bool_or(NOT active) AND NOT bool_or(active)",
                              (email,)).fetchone():
        email = None  # a directory says they've left: no new matches, and the ones held are dropped below
    want = set()
    if email:
        want |= {f"ext:{r['source']}.{r['external_id']}" for r in conn.execute(
            "SELECT source, external_id FROM source_identities WHERE email = %s", (email,)).fetchall()}
    want = {p for p in want if is_valid(p)}
    held = {r["source"]: r for r in conn.execute(
        "SELECT source, canonical, verified_by FROM principal_aliases WHERE source = ANY(%s) OR canonical = %s",
        (sorted(want), me)).fetchall()}
    add = sorted(p for p in want if p not in held)  # free accounts only: one someone else holds stays theirs
    drop = sorted(s for s, r in held.items() if r["canonical"] == me and r["verified_by"] == VERIFIED_BY and s not in want)
    for source in add:
        conn.execute("INSERT INTO principal_aliases (org_id, source, canonical, verified_by) VALUES (%s, %s, %s, %s) "
                     "ON CONFLICT DO NOTHING", (org_id, source, me, VERIFIED_BY))
    if drop:
        conn.execute("DELETE FROM principal_aliases WHERE canonical = %s AND verified_by = %s AND source = ANY(%s)",
                     (me, VERIFIED_BY, drop))
    mine = set(add) | {s for s, r in held.items() if r["canonical"] == me and s in want}
    return add, drop, mine
