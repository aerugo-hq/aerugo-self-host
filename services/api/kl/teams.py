"""Microsoft Teams (C1a; docs/ENTERPRISE_PLAN.md section 6): the standard channels of the teams whose owners consented
to the company's Teams app, their posts and replies, readable by exactly each team's own members.

The app is the company's own (an Entra app registration signing in with a certificate, as the directory's does, and a
Teams app package the company uploads; never distributed). A team's owner installs it with resource-specific consent
(ChannelMessage.Read.Group, TeamMember.Read.Group, ChannelSettings.Read.Group, TeamSettings.Read.Group), which lets the
app read that team and no other: Graph answers 403 for a team whose owner hasn't consented, and that team is skipped.
No tenant-wide chat permission is ever asked for. Readers mirror Teams and fail closed:

  - a standard channel's readers are its team's members and owners, as Graph lists them, each `ext:microsoft.<email>`
    (the identity Entra and Microsoft 365 record, so sign-in matching links them);
  - a guest reaches no one, nor does a member Graph gives no email for; the whole org never does, even for an org-wide
    team;
  - private and shared channels, group chats, and 1:1 chats are never read.

A post and its replies are one document. Each sync re-reads the connection's history window in full, so an edit, a
deletion, or a removed member carries over on that sync. A message the sensitive gate flags (C2a), or one someone marked
"don't capture", is left out of every section. The owner's consent is capture's first key (decided 2026-10-21): a
consented team's channels arrive as requested, for the team's admin in Aerugo to confirm. Teams is indexed only."""
import hashlib
import html
import logging
import re
import time
from datetime import datetime, timezone

import httpx

from . import privacy
from .connectors import Container, Health
from .directory import EMAIL, GUID, GraphClient
from .principals import is_valid
from .sync_items import Item, record_identities, sync_container
from .vault import vault

log = logging.getLogger("kl.teams")

CHANNEL = re.compile(r"^19:[A-Za-z0-9_-]{1,100}@thread\.(tacv2|skype)$")
MESSAGE = re.compile(r"^[0-9]{10,20}$")
HISTORY_DAYS = 365


class TeamsClient(GraphClient):
    """The teams whose owners consented to the app, their standard channels, members, posts, and replies."""

    def __init__(self, api_base: str, tenant_id: str, client_id: str, certificate: dict, teams: list[str]):
        super().__init__(api_base, tenant_id, client_id, certificate)
        self.teams = [t.lower() for t in teams if GUID.fullmatch(t)]

    def test(self) -> Health:
        try:
            self._access_token()
            return Health(True, "connected")
        except httpx.HTTPStatusError as e:
            return Health(False, f"Microsoft returned {e.response.status_code}: check the tenant, the app, and its certificate")
        except httpx.HTTPError as e:
            return Health(False, f"couldn't reach Microsoft: {type(e).__name__}")

    def discover(self) -> list[Container]:
        """Each consented team's standard channels, keeping the team's id (Graph reaches a channel through its team).
        A team whose owner hasn't consented answers 403 and is skipped: there's nothing of it the app may read."""
        found = []
        for team in self.teams:
            try:
                name = self._get(f"{self.base}/v1.0/teams/{team}", {"$select": "id,displayName"}).get("displayName") or team
                channels = self._pages(f"teams/{team}/channels", {"$select": "id,displayName,membershipType"})
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (403, 404):
                    continue
                raise
            found += [Container(external_id=c["id"], kind="channel", visibility="private", default_ref=team,
                                name=f"{name} / {c.get('displayName') or 'Channel'}")
                      for c in channels if CHANNEL.fullmatch(c.get("id") or "") and c.get("membershipType") == "standard"]
        return found

    def members(self, team: str) -> list[dict]:
        return self._pages(f"teams/{team}/members", {})

    def posts(self, team: str, channel: str) -> list[dict]:
        return self._pages(f"teams/{team}/channels/{channel}/messages", {"$top": 50})

    def replies(self, team: str, channel: str, post: str) -> list[dict]:
        return self._pages(f"teams/{team}/channels/{channel}/messages/{post}/replies", {"$top": 50})

    def message(self, team: str, channel: str, message: str, root: str | None = None) -> dict:
        """One message as Teams holds it now: a post, or (with its post's id) a reply. {} when it isn't there."""
        if not GUID.fullmatch(team) or not CHANNEL.fullmatch(channel) or not MESSAGE.fullmatch(message) \
                or (root is not None and not MESSAGE.fullmatch(root)):
            raise ValueError("unexpected team, channel, or message")
        path = f"messages/{root}/replies/{message}" if root and root != message else f"messages/{message}"
        try:
            return self._get(f"{self.base}/v1.0/teams/{team}/channels/{channel}/{path}", None)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return {}
            raise


def client_for(connection: dict) -> TeamsClient:
    cfg = connection["config"] or {}
    return TeamsClient(cfg["api_base"], cfg["tenant_id"], cfg["client_id"], vault.get(connection["credential_ref"])["certificate"],
                       cfg.get("team_ids") or [])  # never `teams`: that's GitHub's routing map


def member_email(member: dict) -> str | None:
    """A team member's company email, or None for a guest or anyone Graph gives no email for: they reach no one."""
    email = str(member.get("email") or "").strip().lower()
    if "guest" in {str(r).lower() for r in member.get("roles") or []} or not EMAIL.fullmatch(email):
        return None
    return email


def readers(members: list[dict]) -> list[str]:
    """A standard channel's readers: its team's members and owners, never a guest, never the whole org."""
    return sorted(p for m in members if (email := member_email(m)) and is_valid(p := f"ext:microsoft.{email}"))


def plain(body: dict | None) -> str:
    """A message's body as plain text: Teams sends HTML, mentions as <at> tags."""
    content, kind = str((body or {}).get("content") or ""), (body or {}).get("contentType")
    if kind == "html":
        content = re.sub(r"<at\b[^>]*>(.*?)</at>", r"@\1", content, flags=re.I | re.S)
        content = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", content, flags=re.I)
        content = re.sub(r"<[^>]+>", "", content)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(content)).strip()


def kept(m: dict) -> bool:
    """A person's message, not deleted, saying something (a system event, such as someone joining, isn't one)."""
    return m.get("messageType") == "message" and not m.get("deletedDateTime") and MESSAGE.fullmatch(str(m.get("id") or "")) is not None \
        and bool(plain(m.get("body")) or m.get("attachments"))


def created(m: dict) -> float:
    try:
        return datetime.fromisoformat(str(m.get("createdDateTime") or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def dropped(m: dict, excluded: frozenset[str] = frozenset()) -> bool:
    """A message Aerugo never keeps: one speaking of one person's sensitive matters (C2a), or one marked "don't capture"."""
    return str(m.get("id")) in excluded or privacy.sensitive(plain(m.get("body")))


def version(messages: list[dict], excluded: frozenset[str] = frozenset()) -> str:
    """Changes whenever a message is added, edited, or removed, and when one is dropped since (a copy kept before is
    read again without it)."""
    marks = "|".join(f"{m['id']}:{m.get('lastEditedDateTime') or ''}:{hashlib.sha256(plain(m.get('body')).encode()).hexdigest()[:12]}"
                     for m in sorted(messages, key=lambda m: m["id"]))
    gone = sorted(m["id"] for m in messages if dropped(m, excluded))
    return hashlib.sha256(marks.encode()).hexdigest() + (":d" + hashlib.sha256("|".join(gone).encode()).hexdigest()[:12] if gone else "")


def sections_of(messages: list[dict], excluded: frozenset[str] = frozenset()) -> list[tuple[str | None, str]]:
    """A section per message, leaving out each that's dropped: never kept, searched, or sent to a model, though the
    team's members can read it in Teams."""
    out = []
    for m in sorted(messages, key=created):
        if dropped(m, excluded):
            continue
        who = (((m.get("from") or {}).get("user") or {}).get("displayName")) or "someone"
        when = datetime.fromtimestamp(created(m), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        files = [a.get("name") for a in m.get("attachments") or [] if a.get("name")]
        text = plain(m.get("body"))
        body = "\n".join(([text] if text else []) + ([f"Files: {', '.join(files)}"] if files else []))
        out.append((f"{who}, {when}{' (edited)' if m.get('lastEditedDateTime') else ''}", body))
    return out


def key_of(channel: str, post: str) -> str:
    """A post's document, its replies included: the channel by a hash (its id holds characters a key can't)."""
    return f"teams-{hashlib.sha256(channel.encode()).hexdigest()[:10]}-{post}"


def holding(channel: str, message: dict) -> list[str]:
    """The document a message is in: its post's, whether it's the post or a reply."""
    post = str(message.get("replyToId") or message.get("id") or "")
    return [key_of(channel, post)] if MESSAGE.fullmatch(post) else []


def items_for(client: TeamsClient, team: str, channel: str, name: str, acl: list[str], oldest: float,
              excluded: frozenset[str] = frozenset(), readers_only: bool = False) -> list[Item]:
    """A channel's posts in the window, one document each with its replies. A readers refresh (S8) reads no replies:
    it gives each document its readers, and its version isn't compared."""
    items = []
    for post in client.posts(team, channel):
        if not kept(post) or created(post) < oldest:
            continue
        thread = [post] if readers_only else [post] + [r for r in client.replies(team, channel, post["id"]) if kept(r)]
        first = plain(post.get("body")).split("\n", 1)[0][:120] or "Post"
        if dropped(post, excluded):  # its title never says what it does
            first = f"post on {datetime.fromtimestamp(created(post), timezone.utc).strftime('%Y-%m-%d')}"
        url = str(post.get("webUrl") or "")
        items.append(Item(key=key_of(channel, post["id"]), uri=url if url.startswith("https://") else f"teams:{channel}/{post['id']}",
                          version="" if readers_only else version(thread, excluded), title=f"{name}: {first}", path=name, acl=acl,
                          sections=lambda thread=thread, first=first: (f"{name}: {first}", sections_of(thread, excluded))))
    return items


def sync_channel(conn, connection: dict, client: TeamsClient, channel: str, team: str | None) -> dict:
    """A channel: its team's members as its readers, and its posts in the history window. A team whose owner withdrew
    consent (or removed the app) answers 403, and the channel then keeps nothing."""
    cfg = connection["config"] or {}
    name = conn.execute("SELECT display_name FROM connection_containers WHERE connection_id = %s AND external_id = %s",
                        (connection["id"], channel)).fetchone()
    name = str((name or {}).get("display_name") or channel).split(" / ", 1)[-1]
    excluded = frozenset(r["message"] for r in conn.execute(  # marked "don't capture"
        "SELECT message FROM capture_exclusions WHERE connection_id = %s AND container = %s", (connection["id"], channel)))
    oldest = time.time() - 86400 * int(cfg.get("history_days") or HISTORY_DAYS)
    try:
        if not team or not GUID.fullmatch(team) or not CHANNEL.fullmatch(channel):
            raise ValueError("unexpected team or channel")
        members = client.members(team)
        items = items_for(client, team, channel, name, readers(members), oldest, excluded, bool(connection.get("readers_only")))
    except httpx.HTTPStatusError as e:
        if e.response.status_code not in (403, 404):
            raise
        log.info("Teams refused channel %s (%s): it keeps nothing", channel, e.response.status_code)
        return sync_container(conn, connection, channel, "teams", [])
    people = {email: {"email": email, "name": str(m.get("displayName") or "")[:200]} for m in members if (email := member_email(m))}
    if people:
        record_identities(conn, connection["org_id"], "microsoft", people)
    return sync_container(conn, connection, channel, "teams", items)


def may_mark(members: list[dict], user: str) -> bool:
    """Who may mark a message "don't capture": anyone in its team (not a guest), as Graph lists the team now."""
    return GUID.fullmatch(user or "") is not None and any(
        str(m.get("userId") or "").lower() == user.lower() and member_email(m) for m in members)
