"""Who to ask about a page (slice K6): the people its own content names (who verified it, who owns it), the members
of a team that owns it, and the people mentioned in it, each with the reasons. Pure, so the API decides what may be
seen and this only weighs it: callers pass members only for owning teams the person asking is on, so a private team's
membership never leaks, and a team they aren't on is named without its people."""

# The strongest signal first: someone who vouched for the page knows it; a named owner, then its team, then a mention.
VERIFIED, OWNER, TEAM, MENTIONED = 1.0, 0.9, 0.6, 0.5


def _verified(meta: dict) -> list[dict]:
    v = meta.get("verified")
    return [v] if isinstance(v, dict) else [e for e in (v or []) if isinstance(e, dict)]


def who_to_ask(meta: dict, members: dict[str, list[str]], mentioned: list[str]) -> dict:
    """meta: the page's OKF front matter. members: {"group:<team>": [usernames]} for owning teams the asker is on.
    mentioned: usernames named in the page. Returns {"people": [...], "teams": [...]}, strongest first."""
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
    for user in mentioned:
        add(user, MENTIONED, "named in it")
    # Each extra reason adds a little: two signals about the same person beat one.
    people = [{**p, "strength": round(min(1.0, p["strength"] + 0.05 * (len(p["reasons"]) - 1)), 2)} for p in found.values()]
    return {"people": sorted(people, key=lambda p: (-p["strength"], p["user"])), "teams": teams}
