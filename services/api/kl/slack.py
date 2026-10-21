"""Slack (S4, ADR 0011): the channels a bot is in, their messages and threads, readable by exactly who Slack lets read
them. A Slack app the company creates in its own workspace (internal, never distributed, so Slack's history calls keep
their usual rate) installs a bot with read-only scopes; the bot only sets what's indexed. Readers mirror Slack and fail
closed:

  - A private channel's readers are its members; so are a public channel's, unless the connection's admin confirmed
    this workspace is the company's own (`company_workspace`), when a public channel reaches the whole org.
  - A channel shared with another organization (Slack Connect) reaches only its members from this workspace, never
    the other organization's, whatever the workspace; a member who has left the workspace, and a bot, reach no one.
  - Members are external identities (ext:slack.<user id>) that reach a person only once identity matching or the
    person links them.

A thread is one document; messages outside threads are grouped per channel per day, so a busy channel isn't a million
documents. Each sync re-reads the connection's history window (`history_days`, a year by default) in full, so an edit,
a deletion, a message Slack's retention removed, or a day that fell out of the window leaves the index on that sync.
Direct messages are never read."""
import hashlib
import html
import re
import os
import time
from datetime import datetime, timezone

import httpx

from . import config, privacy
from .connectors import Container, Health
from .principals import is_valid
from .sync_items import Item, record_identities, storage_of, sync_container
from .vault import vault
from . import source_http

SLACK_API = "https://slack.com/api"
CHANNEL = re.compile(r"^[CG][A-Z0-9]{2,20}$")
USER = re.compile(r"^[UW][A-Z0-9]{2,20}$")
TS = re.compile(r"^[0-9]{9,11}\.[0-9]{1,6}$")
SKIPPED = {"channel_join", "channel_leave", "channel_topic", "channel_purpose", "channel_name", "channel_archive",
           "channel_unarchive", "pinned_item", "unpinned_item", "group_join", "group_leave", "bot_add", "bot_remove"}
MAX_PAGES = 1000
MAX_MESSAGES = 50000  # per channel per sync: past this the sync fails, rather than reading the rest as deleted
HISTORY_DAYS = 365
# An incremental sync (H6) re-reads the channel from the UTC midnight before the earlier of its last sync and this many
# days ago, so recent edits, deletions, and thread replies carry over every time; older days wait for the daily
# reconcile, which reads the whole history window.
RECENT_DAYS = int(os.environ.get("KL_SLACK_RECENT_DAYS", "7"))


class SlackError(RuntimeError):
    """Slack answered `ok: false`: its error code names the problem (a missing scope, a revoked token)."""


def allowed_base(api_base: str) -> bool:
    return api_base.rstrip("/") in config.SLACK_API_BASES


def plain(text: str, names: dict[str, str]) -> str:
    """Slack's mrkdwn as plain text: people and channels by name, links as their label and address."""
    text = re.sub(r"<@([UW][A-Z0-9]+)(?:\|[^>]*)?>", lambda m: f"@{names.get(m.group(1), 'someone')}", text or "")
    text = re.sub(r"<#[CG][A-Z0-9]+\|([^>]*)>", r"#\1", text)
    text = re.sub(r"<!(here|channel|everyone)(?:\|[^>]*)?>", r"@\1", text)
    text = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"\2 (\1)", text)
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)
    return html.unescape(text).strip()


class SlackClient:
    """Slack's Web API with a bot token, read only. Pages follow Slack's cursor, never a URL."""

    def __init__(self, api_base: str, token: str):
        if not allowed_base(api_base):
            raise ValueError("that Slack API address isn't allowed here")
        self.base, self.headers = api_base.rstrip("/"), {"Authorization": f"Bearer {token}"}
        self._users: dict | None = None

    def _call(self, method: str, **params) -> dict:
        r = source_http.get(f"{self.base}/{method}", params=params, headers=self.headers, timeout=30)  # its limits, waited out (H2)
        r.raise_for_status()
        body = r.json()
        if not body.get("ok"):
            raise SlackError(str(body.get("error") or "not ok")[:100])
        return body

    def _pages(self, method: str, key: str, **params) -> list:
        out, cursor = [], None
        for _ in range(MAX_PAGES):
            body = self._call(method, **params, **({"cursor": cursor} if cursor else {}))
            out += body.get(key) or []
            if len(out) > MAX_MESSAGES:
                raise SlackError(f"more than {MAX_MESSAGES} {key} in one listing")
            cursor = (body.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                return out
        raise SlackError(f"Slack's {method} listing ran past {MAX_PAGES} pages")

    def test(self) -> Health:
        try:
            me = self._call("auth.test")
            return Health(True, f"signed in to {me.get('team') or 'Slack'} as {me.get('user') or 'its bot'}")
        except SlackError as e:
            return Health(False, f"Slack refused the bot token ({e})")
        except httpx.HTTPError as e:
            return Health(False, f"couldn't reach Slack: {type(e).__name__}")

    def team(self) -> dict:
        """This workspace: its id (who's a member of it, not a guest from a shared channel's other side) and address."""
        me = self._call("auth.test")
        return {"id": me.get("team_id"), "url": str(me.get("url") or "").rstrip("/")}

    def discover(self) -> list[Container]:
        """The channels the bot is in: those are the ones it may read, so those are what's indexed."""
        channels = self._pages("conversations.list", "channels", types="public_channel,private_channel",
                               exclude_archived="true", limit=200)
        return [Container(external_id=c["id"], kind="channel", default_ref=None, name=f"#{c.get('name') or c['id']}",
                          visibility="private" if c.get("is_private") else "shared" if c.get("is_ext_shared") else "public")
                for c in channels if c.get("is_member") and CHANNEL.fullmatch(c.get("id") or "")]

    def channel(self, channel: str) -> dict:
        return self._call("conversations.info", channel=channel).get("channel") or {}

    def members(self, channel: str) -> list[str]:
        return [m for m in self._pages("conversations.members", "members", channel=channel, limit=200) if USER.fullmatch(m)]

    def users(self) -> dict[str, dict]:
        """This workspace's people, once per sync: whether each has left, is a bot, and is this workspace's own."""
        if self._users is None:
            self._users = {u["id"]: {"team": u.get("team_id"), "gone": bool(u.get("deleted")), "bot": bool(u.get("is_bot")),
                                     "email": (u.get("profile") or {}).get("email"),
                                     "name": (u.get("profile") or {}).get("real_name") or u.get("name") or u["id"]}
                           for u in self._pages("users.list", "members", limit=200) if USER.fullmatch(u.get("id") or "")}
        return self._users

    def history(self, channel: str, oldest: float, latest: float | None = None) -> list[dict]:
        return self._pages("conversations.history", "messages", channel=channel, oldest=f"{oldest:.6f}", limit=200,
                           **({"latest": f"{latest:.6f}"} if latest is not None else {}))

    def user(self, user_id: str) -> dict:
        """One person, as this workspace knows them: whether an admin or owner, a bot, gone, and whose team."""
        if not USER.fullmatch(user_id):
            raise ValueError("unexpected user")
        return self._call("users.info", user=user_id).get("user") or {}

    def message(self, channel: str, ts: str) -> dict:
        """One message as Slack shows it now, its reactions (each with who added it; C2b: "don't capture" is one) and,
        for a reply, its thread's ts."""
        if not CHANNEL.fullmatch(channel) or not TS.fullmatch(ts):
            raise ValueError("unexpected message")
        return self._call("reactions.get", channel=channel, timestamp=ts, full="true").get("message") or {}

    def post(self, channel: str, text: str) -> str:
        """A message from the bot in a channel: only capture's notice ever (C2b, decided 2026-10-20). Its ts."""
        if not CHANNEL.fullmatch(channel):
            raise ValueError("unexpected channel")
        r = source_http.post(f"{self.base}/chat.postMessage", data={"channel": channel, "text": text}, headers=self.headers, timeout=30)
        r.raise_for_status()
        body = r.json()
        if not body.get("ok"):
            raise SlackError(str(body.get("error") or "not ok")[:100])
        return str(body.get("ts") or "")

    def replies(self, channel: str, ts: str) -> list[dict]:
        return self._pages("conversations.replies", "messages", channel=channel, ts=ts, limit=200)


CAPTURE_COMMAND = re.compile(r"^\s*capture\s+(on|off)\s*$", re.I)
DONT_CAPTURE = "no_entry_sign"  # the reaction that marks a message "don't capture" (C2b)
NOTICE_ON = ("Aerugo now proposes knowledge from this channel's threads, for {team}'s curators to confirm; only this "
             "channel's members can read it. React :no_entry_sign: to any message to keep it out. "
             "Run /aerugo capture off to stop.")
NOTICE_OFF = "Aerugo no longer proposes knowledge from this channel."


def holding(channel: str, message: dict) -> list[str]:
    """The keys of the documents a message can be in: its own thread, the thread it replies to, or its day (`items_for`)."""
    ts = str(message.get("ts") or "")
    if not TS.fullmatch(ts):
        return []
    day = datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%d")
    threads = {t for t in (ts, str(message.get("thread_ts") or "")) if TS.fullmatch(t)}
    return sorted({f"slack-{channel}-{t}" for t in threads} | {f"slack-{channel}-{day}"})


def capture_action(text: str) -> str | None:
    """`on` or `off` from `/aerugo capture on|off`, or None for anything else."""
    m = CAPTURE_COMMAND.fullmatch(text or "")
    return m[1].lower() if m else None


def may_turn(user: dict, channel: dict, team: str) -> bool:
    """The first key (C2b): the channel's creator, or an admin or owner of this workspace; never a guest from a shared
    channel's other side, someone who has left, or a bot."""
    if not user or user.get("deleted") or user.get("is_bot") or user.get("team_id") != team:
        return False
    return user.get("id") == channel.get("creator") or bool(user.get("is_admin") or user.get("is_owner") or user.get("is_primary_owner"))


def client_for(connection: dict) -> SlackClient:
    return SlackClient(connection["config"]["api_base"], vault.get(connection["credential_ref"])["bot_token"])


def readers_of(channel: dict, members: list[str], users: dict, team: str, company_workspace: bool, org_slug: str) -> list[str]:
    """Who may read a channel, failing closed: its members from this workspace (never a shared channel's other side,
    someone who has left, or a bot), or the whole org for a public, unshared channel on a confirmed company workspace."""
    if company_workspace and not channel.get("is_private") and not channel.get("is_ext_shared") and not channel.get("is_shared"):
        return [f"org:{org_slug}"]
    return sorted(p for m in members if (u := users.get(m)) and u["team"] == team and not u["gone"] and not u["bot"]
                  and is_valid(p := f"ext:slack.{m}"))


def kept(message: dict) -> bool:
    return TS.fullmatch(str(message.get("ts") or "")) is not None and message.get("subtype") not in SKIPPED \
        and bool((message.get("text") or "").strip() or message.get("files"))


def version(messages: list[dict]) -> str:
    """Changes whenever a message is added, edited, or removed."""
    marks = "|".join(f"{m['ts']}:{(m.get('edited') or {}).get('ts', '')}:{hashlib.sha256((m.get('text') or '').encode()).hexdigest()[:12]}"
                     for m in sorted(messages, key=lambda m: m["ts"]))
    return hashlib.sha256(marks.encode()).hexdigest()


def said(m: dict, names: dict[str, str]) -> str:
    return plain(m.get("text") or "", names)


def dropped(m: dict, names: dict[str, str], excluded: frozenset[str] = frozenset()) -> bool:
    """A message Aerugo never keeps: one speaking of one person's sensitive matters (C2a), or one someone marked "don't
    capture" (C2b)."""
    return m.get("ts") in excluded or privacy.sensitive(said(m, names))


def kept_version(messages: list[dict], names: dict[str, str], excluded: frozenset[str] = frozenset()) -> str:
    """A document's version, marked by which of its messages are dropped, so a copy kept before one was dropped (a
    sensitive message before C2a, a message marked "don't capture" since) is read again without it."""
    gone = sorted(m["ts"] for m in messages if dropped(m, names, excluded))
    return version(messages) + (":d" + hashlib.sha256("|".join(gone).encode()).hexdigest()[:12] if gone else "")


def sections_of(messages: list[dict], names: dict[str, str], excluded: frozenset[str] = frozenset()) -> list[tuple[str | None, str]]:
    """A section per message, leaving out each that's dropped (sensitive, C2a; marked "don't capture", C2b): never kept,
    never searched, never sent to a model, though the channel's members can read it in Slack."""
    out = []
    for m in sorted(messages, key=lambda m: float(m["ts"])):
        when = datetime.fromtimestamp(float(m["ts"]), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        who = names.get(m.get("user") or "", m.get("username") or "someone")
        text = said(m, names)
        if dropped(m, names, excluded):
            continue
        files = [f.get("name") for f in m.get("files") or [] if f.get("name")]
        body = "\n".join(([text] if text else []) + ([f"Files: {', '.join(files)}"] if files else []))
        out.append((f"{who}, {when}{' (edited)' if m.get('edited') else ''}", body))
    return out


def permalink(team_url: str, channel: str, ts: str) -> str:
    return f"{team_url}/archives/{channel}/p{ts.replace('.', '')}"


def items_for(client: SlackClient, channel_id: str, name: str, acl: list[str], names: dict[str, str], team_url: str,
              oldest: float, pointer: bool = False, excluded: frozenset[str] = frozenset()) -> list[Item]:
    """A channel's threads (one document each) and its other messages, one document per day. A thread is titled by its
    opener's first line, except for a pointer (F11a), which keeps no message text: by its channel and day."""
    items, days = [], {}
    for m in client.history(channel_id, oldest):
        if not kept(m):
            continue
        if threaded(m):
            thread = [r for r in client.replies(channel_id, m["ts"]) if kept(r)]
            first = said(m, names).split("\n", 1)[0][:120] or "Thread"
            if pointer or dropped(m, names, excluded):  # no message text kept (F11a), or none that's dropped (C2a, C2b)
                first = f"thread on {datetime.fromtimestamp(float(m['ts']), timezone.utc).strftime('%Y-%m-%d')}"
            items.append(Item(key=f"slack-{channel_id}-{m['ts']}", uri=permalink(team_url, channel_id, m["ts"]), version=kept_version(thread, names, excluded),
                              title=f"{name}: {first}", path=f"{name} › Threads", acl=acl,
                              sections=lambda thread=thread, first=first: (f"{name}: {first}", sections_of(thread, names, excluded))))
        else:
            day = datetime.fromtimestamp(float(m["ts"]), timezone.utc).strftime("%Y-%m-%d")
            days.setdefault(day, []).append(m)
    for day, messages in days.items():
        start = min(messages, key=lambda m: float(m["ts"]))
        items.append(Item(key=f"slack-{channel_id}-{day}", uri=permalink(team_url, channel_id, start["ts"]), version=kept_version(messages, names, excluded),
                          title=f"{name}, {day}", path=name, acl=acl,
                          sections=lambda messages=messages, day=day: (f"{name}, {day}", sections_of(messages, names, excluded))))
    return items


def threaded(m: dict) -> bool:
    """A message that starts a thread: the thread is its own document."""
    return bool(m.get("reply_count")) and m.get("thread_ts") == m["ts"]


def live(client: SlackClient, cfg: dict, doc: dict) -> tuple[list[str], list[tuple[str | None, str]]]:
    """A pointer thread or day as Slack holds it now (F11a): the channel's readers, worked out as a sync works them out
    (nobody once the bot has left it), and its messages' sections."""
    channel, _, rest = doc["external_key"][len("slack-"):].partition("-")
    if not CHANNEL.fullmatch(channel):
        raise ValueError("unexpected channel")
    info = client.channel(channel)
    if not info.get("is_member"):
        return [], []
    team, users = client.team(), client.users()
    readers = readers_of(info, client.members(channel), users, team["id"], bool(cfg.get("company_workspace")), config.ORG_SLUG)
    names = {uid: u["name"] for uid, u in users.items()}
    if TS.fullmatch(rest):  # a thread
        messages = [r for r in client.replies(channel, rest) if kept(r)]
    else:  # a day's other messages
        start = datetime.strptime(rest, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
        messages = [m for m in client.history(channel, start, start + 86400)
                    if kept(m) and not threaded(m) and start <= float(m["ts"]) < start + 86400]
    return readers, sections_of(messages, names, frozenset(doc.get("excluded") or ()))


def since_time(cursor: str | None) -> float | None:
    """A cursor's time as a timestamp, or None for no cursor, or one that isn't an ISO time with its zone."""
    try:
        then = datetime.fromisoformat(cursor) if cursor else None
    except ValueError:
        return None
    return then.timestamp() if then is not None and then.tzinfo is not None else None


def sync_channel(conn, connection: dict, client: SlackClient, channel_id: str, since: str | None = None) -> dict:
    cfg = connection["config"] or {}
    started = datetime.now(timezone.utc).isoformat()
    team, users, info = client.team(), client.users(), client.channel(channel_id)
    if not info.get("is_member"):  # the bot was removed: the channel isn't Aerugo's to read any more
        return sync_container(conn, connection, channel_id, "slack", [])
    acl = readers_of(info, client.members(channel_id), users, team["id"], bool(cfg.get("company_workspace")), config.ORG_SLUG)
    names = {uid: u["name"] for uid, u in users.items()}
    people = {uid: {"email": u["email"], "name": u["name"]} for uid, u in users.items()
              if u["team"] == team["id"] and not u["gone"] and not u["bot"] and u["email"]}
    if people:
        record_identities(conn, connection["org_id"], "slack", people)
    window = time.time() - 86400 * int(cfg.get("history_days") or HISTORY_DAYS)
    last = since_time(since)
    recent = None if last is None else min(last - 600, time.time() - 86400 * RECENT_DAYS) // 86400 * 86400  # a whole day
    oldest = window if recent is None or recent <= window else recent
    excluded = frozenset(r["message"] for r in conn.execute(  # marked "don't capture" (C2b)
        "SELECT message FROM capture_exclusions WHERE connection_id = %s AND container = %s", (connection["id"], channel_id)))
    items = items_for(client, channel_id, f"#{info.get('name') or channel_id}", acl, names, team["url"], oldest,
                      pointer=storage_of(connection) == "pointer", excluded=excluded)
    return sync_container(conn, connection, channel_id, "slack", items, complete=oldest == window, cursor=started)
