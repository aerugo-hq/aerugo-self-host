"""Who to ask about a page (slice K6): the people its own content names (who verified it, who owns it), the members
of a team that owns it, and the people mentioned in it, each with the reasons. Pure, so the API decides what may be
seen and this only weighs it: callers pass members only for owning teams the person asking is on, so a private team's
membership never leaks, and a team they aren't on is named without its people."""

# The strongest signal first: someone who vouched for the page knows it; a named owner, someone who wrote it (Q7), then
# its team, then a mention.
VERIFIED, OWNER, AUTHOR, TEAM, MENTIONED = 1.0, 0.9, 0.7, 0.6, 0.5


def _verified(meta: dict) -> list[dict]:
    v = meta.get("verified")
    return [v] if isinstance(v, dict) else [e for e in (v or []) if isinstance(e, dict)]


def who_to_ask(meta: dict, members: dict[str, list[str]], mentioned: list[str],
               asset_owners: dict[str, list[str]] | None = None, left: dict[str, list[str]] | None = None,
               authors: dict[str, int] | None = None) -> dict:
    """meta: the page's OKF front matter. members: {"group:<team>": [usernames]} for owning teams the asker is on.
    mentioned: usernames named in the page. asset_owners: {username: [asset names]} for assets the page describes that
    a catalog says they own (S7), already limited to what the asker may see. left: {username: [their teams the asker
    is on]} for the people a directory says have left (Q6). authors: {username: commits} for who wrote it in Git (Q7),
    as far as they linked their accounts. Returns {"people": [...], "teams": [...], "left": [...]},
    strongest first: someone who has left is never suggested, and their team is, instead."""
    found: dict[str, dict] = {}

    def add(user: str, strength: float, reason: str) -> None:
        user = user.strip()
        if not user:
            return
        p = found.setdefault(user, {"user": user, "strength": 0.0, "reasons": []})
        p["strength"] = max(p["strength"], strength)
        if reason not in p["reasons"]:
            p["reasons"].append(reason)

    for entry in _verified(meta):
        kind, _, name = str(entry.get("by", "")).partition(":")
        if kind in ("human", "user"):
            add(name, VERIFIED, f"verified it on {str(entry.get('at', ''))[:10]}".strip())
    teams = []
    for owner in (meta.get("x-kl") or {}).get("owners") or []:
        kind, _, name = str(owner).partition(":")
        if kind in ("human", "user"):
            add(name, OWNER, "owns it")
        elif kind == "group" and name:
            teams.append({"team": name, "reason": "owns it", "members_shown": owner in members})
            for user in members.get(owner, []):
                add(user, TEAM, f"on the owning team, {name}")
    for user, names in (asset_owners or {}).items():
        for name in names:
            add(user, OWNER, f"owns {name} in its catalog")
    for user, n in (authors or {}).items():
        add(user, AUTHOR, f"wrote it ({n} commit{'' if n == 1 else 's'})")
    for user in mentioned:
        add(user, MENTIONED, "named in it")
    gone = [{"user": u, "reasons": found.pop(u)["reasons"], "instead": sorted(set(left[u]) | {t["team"] for t in teams})}
            for u in sorted(left or {}) if u in found]
    named = {t["team"] for t in teams}
    for g in gone:
        for team in g["instead"]:
            if team not in named:
                named.add(team)
                teams.append({"team": team, "reason": f"{g['user']} has left; ask their team", "members_shown": False})
    # Each extra reason adds a little: two signals about the same person beat one.
    people = [{**p, "strength": round(min(1.0, p["strength"] + 0.05 * (len(p["reasons"]) - 1)), 2)} for p in found.values()]
    return {"people": sorted(people, key=lambda p: (-p["strength"], p["user"])), "teams": teams, "left": gone}
