"""The company's Teams bot (C1a, C1b; docs/ENTERPRISE_PLAN.md section 6): its deliveries checked, capture's notice
posted, "Don't capture" and "Save to Aerugo" taken, and its private replies sent.

Invariant 13 for Teams (decided 2026-10-21): Bot Framework signs no HMAC over a delivery's body. It sends a token
Microsoft signs, which stands in for one, checked here before anything else is acted on: RS256 with a key Bot
Framework publishes and endorses for Teams, issued by Bot Framework, for the connection's own app, unexpired (five
minutes' skew), and naming the service address the activity came from, which must be on the allowlist. A delivery is
taken once, by its activity's id. Because the body itself isn't signed, a delivery only queues a job, and that job reads
back from Graph or the Bot Connector everything it acts on (the message, that it's in a channel the connection reads,
that whoever marked or saved it is in its team, who's asking); a question asked of the bot in its 1:1 chat is the one
text taken from a delivery (invariant 13's recorded exception, C1b), never kept.

The bot writes to Teams only capture's notice, posted in a channel when capture starts and stops (decided 2026-10-20),
and private replies, each in the 1:1 chat of the person who asked or saved, which it opens by their id: through the Bot
Connector, as the app, to the service address the connection names (on the allowlist), never one a delivery names."""
import hashlib
import logging
import threading
import time
from urllib.parse import quote, urlparse

import httpx
import jwt
from psycopg.types.json import Jsonb

from . import config, source_http, teams
from .directory import GRAPH, GUID, GraphClient
from .vault import vault

log = logging.getLogger("kl.teams_bot")

ISSUER = "https://api.botframework.com"
BOT_SCOPE = "https://api.botframework.com/.default"
SERVICE = "https://smba.trafficmanager.net/teams"  # Teams' global Bot Connector address, for messages the bot starts
SKEW = 300  # seconds a token's times may be off
KEYS_MAX_AGE, KEYS_MIN_GAP = 86400.0, 60.0  # keys are read again daily, or for an unknown key at most once a minute
DONT_CAPTURE, SAVE = "dontCapture", "aerugoSave"  # the message actions' command ids, in the Teams app's manifest
NOTICE_ON = ("Aerugo now proposes knowledge from this channel's posts, for {team}'s curators to confirm; only this team's "
             "members can read it. To keep a message out, choose Don't capture from its More actions menu. To stop, ask "
             "{team}'s admins in Aerugo, or remove the Aerugo app from this team.")
NOTICE_OFF = "Aerugo no longer proposes knowledge from this channel."
MARK_REPLY = "Asked. If Aerugo reads this channel and you're in its team, this message won't be kept or proposed from again."
SAVE_REPLY = "Saving. Aerugo will tell you here, privately, whether it was saved."
SAVED = "Saved for {team}'s curators to review. Only this team's members can read it."
NOT_SAVED = {  # what the person who saved is told, privately, in their 1:1 chat with the bot (C1b)
    "saver": "Only someone in this team can save its messages.",
    "gone": "That message isn't there any more.",
    "someone else's": "You can save your own messages here. Someone else's can be saved only in a channel whose capture is on.",
    "marked": "That message is marked don't capture, so it can't be saved.",
    "sensitive": "That message speaks of someone's personal matters, so Aerugo doesn't keep it.",
    "not found": "Aerugo doesn't hold that message (it may be older than the channel's history window).",
    "already": "That message is already saved.",
}
SAVE_TRIES = 5  # a minute apart: a message newer than the last sync waits for the one this queues

_keys: dict = {"at": 0.0, "keys": {}}
_keys_lock = threading.Lock()


class Refused(Exception):
    """A delivery that isn't Microsoft's, for this app, from an allowed service address: refused, whatever's wrong."""


def allowed_service(url: str) -> bool:
    """A Bot Framework service address this deployment allows (a prefix of KL_TEAMS_BOT_SERVICES)."""
    url = (url or "").rstrip("/") + "/"
    return any(url.startswith(prefix) for prefix in config.TEAMS_BOT_SERVICES)


def _fetch_keys() -> dict:
    """Bot Framework's signing keys, from its OpenID metadata, the keys only on that same host: {kid: (key, endorsements)}.
    Read while a delivery waits, so each call is one short try, never a throttle's wait."""
    with source_http.answering():
        meta = source_http.get(config.TEAMS_BOT_OPENID, timeout=10)
        meta.raise_for_status()
        where = str(meta.json().get("jwks_uri") or "")
        home = urlparse(config.TEAMS_BOT_OPENID)
        if (urlparse(where).scheme, urlparse(where).netloc) != (home.scheme, home.netloc):
            raise Refused("Bot Framework's keys aren't on its own host")
        found = source_http.get(where, timeout=10)
        found.raise_for_status()
    keys = {}
    for k in found.json().get("keys") or []:
        if k.get("kty") == "RSA" and isinstance(k.get("kid"), str):
            keys[k["kid"]] = (jwt.PyJWK(k, algorithm="RS256").key, tuple(k.get("endorsements") or ()))
    return keys


def signing_key(kid: str) -> tuple | None:
    """A key by its id, read again when it's unknown (keys rotate) or the copy is a day old, never more than once a
    minute, so unknown ids can't make each delivery a call to Microsoft."""
    with _keys_lock:
        age = time.monotonic() - _keys["at"]
        if age > KEYS_MAX_AGE or (kid not in _keys["keys"] and age > KEYS_MIN_GAP):
            try:
                _keys.update(at=time.monotonic(), keys=_fetch_keys())
            except (httpx.HTTPError, ValueError, jwt.PyJWKError, Refused) as e:
                log.warning("Bot Framework's keys couldn't be read (%s)", type(e).__name__)
                _keys["at"] = time.monotonic()
        return _keys["keys"].get(kid)


def verify(authorization: str, app_id: str, service_url: str) -> dict:
    """The delivery's token, checked as Bot Framework says a bot checks it. Raises Refused for anything wrong."""
    if not authorization.startswith("Bearer ") or not GUID.fullmatch(app_id or "") or not allowed_service(service_url):
        raise Refused("no token, an unknown app, or a service address not allowed")
    token = authorization[len("Bearer "):]
    try:
        header = jwt.get_unverified_header(token)
        found = signing_key(str(header.get("kid") or "")) if header.get("alg") == "RS256" else None
        if not found or "msteams" not in found[1]:
            raise Refused("not signed by a key Bot Framework endorses for Teams")
        claims = jwt.decode(token, found[0], algorithms=["RS256"], audience=app_id, issuer=ISSUER, leeway=SKEW,
                            options={"require": ["exp", "iss", "aud"]})
    except jwt.PyJWTError as e:
        raise Refused(type(e).__name__) from e
    if str(claims.get("serviceurl") or "").rstrip("/") != service_url.rstrip("/"):
        raise Refused("the token names another service address")
    return claims


def _part(parent: dict, name: str) -> dict:
    """One object inside a delivery, or {} when it's missing or isn't an object."""
    found = parent.get(name)
    return found if isinstance(found, dict) else {}


def action_request(activity: dict, tenant: str) -> tuple[str, dict] | None:
    """A message action's job and its ids, or None: "Don't capture" (C1a) or "Save to Aerugo" (C1b). Only the channel,
    the message (and its post, for a reply), and who chose it are taken from a delivery; the worker reads the rest back
    from Graph."""
    value, data = _part(activity, "value"), _part(activity, "channelData")
    job = {DONT_CAPTURE: "teams.exclude", SAVE: "teams.save"}.get(str(value.get("commandId") or ""))
    if activity.get("type") != "invoke" or activity.get("name") != "composeExtension/submitAction" or not job:
        return None
    payload = _part(value, "messagePayload")
    channel = str(_part(data, "channel").get("id") or "")
    in_tenant = str(_part(data, "tenant").get("id") or "").lower()
    user, user_id = str(_part(activity, "from").get("aadObjectId") or "").lower(), str(_part(activity, "from").get("id") or "")
    message, post = str(payload.get("id") or ""), str(payload.get("replyToId") or "")
    if in_tenant != tenant.lower() or not teams.CHANNEL.fullmatch(channel) or not teams.MESSAGE.fullmatch(message) or not GUID.fullmatch(user):
        return None
    if job == "teams.save" and not teams.USER_ID.fullmatch(user_id):  # where the saver is told, privately
        return None
    return job, {"channel": channel, "message": message, "user": user, **({"post": post} if teams.MESSAGE.fullmatch(post) else {}),
                 **({"user_id": user_id} if job == "teams.save" else {})}


def ask_request(activity: dict, tenant: str, app: str, chars: int = 1000) -> dict | None:
    """An ask's job (C1b), or None: a message to the bot in its 1:1 chat, its question taken from the delivery (invariant
    13's recorded exception: the asker's own words to the bot, never kept), or a channel message mentioning the bot, whose
    question the worker reads back from Graph. Group chats are never read."""
    data, conversation = _part(activity, "channelData"), _part(activity, "conversation")
    who = _part(activity, "from")
    user, user_id, conv = str(who.get("aadObjectId") or "").lower(), str(who.get("id") or ""), str(conversation.get("id") or "")
    if activity.get("type") != "message" or str(_part(data, "tenant").get("id") or "").lower() != tenant.lower() \
            or not GUID.fullmatch(user) or not teams.USER_ID.fullmatch(user_id) or not teams.CONVERSATION.fullmatch(conv):
        return None
    base = {"user": user, "user_id": user_id, "conversation": conv}
    if conversation.get("conversationType") == "personal" and conv.startswith("a:"):
        return {**base, "via": "personal", "question": str(activity.get("text") or "").strip()[:chars]}
    channel, message = str(_part(data, "channel").get("id") or ""), str(activity.get("id") or "")
    mentioned = any(isinstance(e, dict) and e.get("type") == "mention" and _part(e, "mentioned").get("id") == f"28:{app}"
                    for e in activity.get("entities") or [])
    if conversation.get("conversationType") == "channel" and mentioned and teams.CHANNEL.fullmatch(channel) \
            and teams.MESSAGE.fullmatch(message) and conv.startswith(channel):
        post = conv.partition(";messageid=")[2]
        return {**base, "via": "mention", "channel": channel, "message": message, **({"post": post} if teams.MESSAGE.fullmatch(post) else {})}
    return None


class BotConnector(GraphClient):
    """The Bot Connector, as the app (its token from Entra, for Bot Framework): posts capture's notice in a channel."""
    scope = BOT_SCOPE

    def allowed(self, api_base: str) -> bool:
        return allowed_service(api_base)

    def __init__(self, service: str, login: str, tenant_id: str, client_id: str, certificate: dict):
        super().__init__(service, tenant_id, client_id, certificate)
        self.login = login.rstrip("/")

    def _token_url(self) -> str:
        return f"{self.login}/{self.tenant}/oauth2/v2.0/token"

    def member(self, conversation: str, user_id: str) -> dict:
        """Someone in a conversation, as Teams says now: their Entra id, email, tenant, and role. {} when they aren't in it."""
        if not teams.CONVERSATION.fullmatch(conversation) or not teams.USER_ID.fullmatch(user_id):
            raise ValueError("unexpected conversation or person")
        try:
            return self._get(f"{self.base}/v3/conversations/{quote(conversation, safe='')}/members/{quote(user_id, safe='')}", None)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 404):
                return {}
            raise

    def whisper(self, user_id: str, text: str) -> None:
        """A private reply, in the person's own 1:1 chat with the bot, opened by their id (C1b: their answer, or what
        became of their save). Plain text, so nothing in it becomes a link with a hidden address, or a mention."""
        if not teams.USER_ID.fullmatch(user_id):
            raise ValueError("unexpected person")
        chat = str(self._call("POST", f"{self.base}/v3/conversations", None, body={
            "isGroup": False, "tenantId": self.tenant, "bot": {"id": f"28:{self.client}"}, "members": [{"id": user_id}],
            "channelData": {"tenant": {"id": self.tenant}}}).get("id") or "")
        if not teams.CONVERSATION.fullmatch(chat) or not chat.startswith("a:"):
            raise ValueError("Teams opened no 1:1 chat")
        self._call("POST", f"{self.base}/v3/conversations/{quote(chat, safe='')}/activities", None,
                   body={"type": "message", "text": text, "textFormat": "plain"})

    def post(self, channel: str, text: str) -> str:
        """A new post in a channel: only capture's notice, ever. The conversation's id."""
        if not teams.CHANNEL.fullmatch(channel):
            raise ValueError("unexpected channel")
        body = {"isGroup": True, "tenantId": self.tenant, "activity": {"type": "message", "text": text},
                "channelData": {"channel": {"id": channel}, "tenant": {"id": self.tenant}}}
        return str(self._call("POST", f"{self.base}/v3/conversations", None, body=body).get("id") or "")


def bot_for(connection: dict) -> BotConnector:
    """The connection's Bot Connector: its own app and certificate, its tokens from Microsoft's sign-in (or, in dev, the
    fake Graph's)."""
    cfg = connection["config"] or {}
    login = "https://login.microsoftonline.com" if cfg["api_base"].rstrip("/") == GRAPH else cfg["api_base"]
    return BotConnector(cfg.get("bot_service") or SERVICE, login, cfg["tenant_id"], cfg["client_id"],
                        vault.get(connection["credential_ref"])["certificate"])


CONTAINER_SQL = """
SELECT c.id, c.org_id, c.kind, c.status, c.config, c.credential_ref, w.id AS team_id, w.slug AS team, w.name AS team_name,
       cc.capture, cc.default_ref AS ms_team
FROM connections c JOIN workspaces w ON w.id = c.workspace_id
JOIN connection_containers cc ON cc.connection_id = c.id AND cc.external_id = %(channel)s AND cc.selected
WHERE c.id = %(connection)s AND c.kind = 'teams' AND c.status <> 'disabled'
"""
DROP_PROPOSALS_SQL = """DELETE FROM knowledge_units WHERE status = 'proposed'
AND evidence && ARRAY(SELECT id FROM documents WHERE connection_id = %s AND external_key = ANY(%s))
AND (created_by = 'extraction' OR (created_by = 'teams-save' AND topic = %s))"""  # a save of that message alone (C1b)
# The section holding a saved message, in the document the connection keeps for its post (C1b).
HELD_SQL = """SELECT d.id, k.heading_path FROM documents d JOIN chunks k ON k.document_id = d.id
WHERE d.connection_id = %s AND d.external_key = ANY(%s) AND d.storage = 'index' AND k.heading_path = %s AND k.body = %s
ORDER BY d.external_key LIMIT 1"""


def channel_of(conn, payload: dict):
    """The connection and capture state of a channel it reads, or None: an unknown, paused, or unread one does nothing."""
    if not teams.CHANNEL.fullmatch(str(payload.get("channel") or "")):
        return None
    return conn.execute(CONTAINER_SQL, {"channel": payload["channel"], "connection": payload.get("connection_id")}).fetchone()


def audit(conn, org_id, action: str, details: dict) -> None:
    conn.execute("INSERT INTO audit_events (org_id, actor, action, scope, details) VALUES (%s, %s, %s, %s, %s)",
                 (org_id, "process:worker", action, [], Jsonb(details)))


def notice(conn, job) -> dict:
    """The one message the bot posts when capture turns on or off, if it's still in that state."""
    p = job["payload"]
    c = channel_of(conn, p)
    if c is None or c["capture"] != p.get("state"):
        return {"skipped": "capture changed since"}
    bot_for(c).post(p["channel"], NOTICE_ON.format(team=c["team_name"]) if p["state"] == "on" else NOTICE_OFF)
    return {"posted": p["state"]}


def exclude(conn, job) -> dict:
    """A message marked "don't capture": taken only once Graph shows the message in that channel and the person who
    chose it in the team; then recorded, the proposals made from its post dropped, and the channel read again in full,
    so its document is kept without it."""
    p = job["payload"]
    c = channel_of(conn, p)
    message, post = str(p.get("message") or ""), p.get("post")
    if c is None or not teams.MESSAGE.fullmatch(message) or not GUID.fullmatch(str(c["ms_team"] or "")):
        return {"skipped": "not a message this connection reads"}
    client = teams.client_for(c)
    found = client.message(c["ms_team"], p["channel"], message, post if isinstance(post, str) else None)
    if str(found.get("id") or "") != message or not teams.may_mark(client.members(c["ms_team"]), str(p.get("user") or "")):
        with conn.transaction():
            audit(conn, c["org_id"], "capture.exclude.refused", {"connection_id": str(c["id"]), "channel": p["channel"], "message": message})
        return {"skipped": "Graph doesn't show that message, or its marker isn't in the team"}
    with conn.transaction():
        added = conn.execute("INSERT INTO capture_exclusions (org_id, connection_id, container, message) VALUES (%s, %s, %s, %s) "
                             "ON CONFLICT DO NOTHING RETURNING message", (c["org_id"], c["id"], p["channel"], message)).fetchone()
        conn.execute(DROP_PROPOSALS_SQL, (c["id"], teams.holding(p["channel"], found), saved_topic(p["channel"], message)))  # F6 keeps a confirmed one
        if added:
            conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'connection.sync', %s)",
                         (c["org_id"], Jsonb({"connection_id": str(c["id"]), "full": True, "reason": "capture.exclude"})))
            audit(conn, c["org_id"], "capture.exclude", {"connection_id": str(c["id"]), "channel": p["channel"], "message": message})
    return {"excluded": bool(added)}


def saved_topic(channel: str, message: str) -> str:
    """A saved message's unit's topic: one per message, so a save is never made twice and a mark finds it."""
    return f"teams-save-{hashlib.sha256(channel.encode()).hexdigest()[:10]}-{message}"


def tell(c, user_id: str, text: str) -> None:
    """A private reply, in the person's 1:1 chat with the bot; failing to send it never undoes what was done."""
    try:
        bot_for(c).whisper(user_id, text)
    except (httpx.HTTPError, ValueError) as e:
        log.warning("couldn't send a Teams user their private reply: %s", type(e).__name__)


def save(conn, job) -> dict:
    """"Save to Aerugo" on a message (C1b, C2c's rules): read back from Graph (the message, the team), and, as the rules
    allow (`teams.may_save`), proposed as a unit citing the document that holds it, so it has exactly that team's readers,
    for the connection's team's curators to confirm. Its trust is what was said (observed), so no answer uses it until a
    curator confirms it (F4). The person who saved is told privately, in their 1:1 chat with the bot."""
    p = job["payload"]
    c = channel_of(conn, p)
    message, post, user, user_id = str(p.get("message") or ""), p.get("post"), str(p.get("user") or ""), str(p.get("user_id") or "")
    if c is None or not teams.MESSAGE.fullmatch(message) or not GUID.fullmatch(str(c["ms_team"] or "")) or not teams.USER_ID.fullmatch(user_id):
        return {"skipped": "not a message this connection reads"}
    person = bot_for(c).member(p["channel"], user_id)  # who's saving, as Teams says now, never the delivery's word
    if str(person.get("aadObjectId") or "").lower() != user.lower() or str(person.get("tenantId") or "").lower() != c["config"]["tenant_id"] \
            or str(person.get("userRole") or "user").lower() == "guest":
        tell(c, user_id, NOT_SAVED["saver"])
        with conn.transaction():
            audit(conn, c["org_id"], "capture.save.refused", {"connection_id": str(c["id"]), "channel": p["channel"],
                                                              "message": message, "user": user, "why": "saver"})
        return {"refused": "saver"}
    client = teams.client_for(c)
    found = client.message(c["ms_team"], p["channel"], message, post if isinstance(post, str) else None)
    excluded = frozenset(r["message"] for r in conn.execute(
        "SELECT message FROM capture_exclusions WHERE connection_id = %s AND container = %s", (c["id"], p["channel"])))
    why = teams.may_save(client.members(c["ms_team"]), user, found if str(found.get("id") or "") == message else {},
                         c["capture"] == "on", excluded)
    if why:
        tell(c, user_id, NOT_SAVED[why])
        with conn.transaction():
            audit(conn, c["org_id"], "capture.save.refused", {"connection_id": str(c["id"]), "channel": p["channel"],
                                                              "message": message, "user": user, "why": why})
        return {"refused": why}
    heading, body = teams.sections_of([found])[0]
    held = conn.execute(HELD_SQL, (c["id"], teams.holding(p["channel"], found), heading, body)).fetchone()
    if not held:  # newer than the last sync, or edited since: read the channel again, and try once more in a minute
        tries = int(p.get("tries") or 0)
        if tries + 1 >= SAVE_TRIES:
            tell(c, user_id, NOT_SAVED["not found"])
            return {"refused": "not found"}
        with conn.transaction():
            conn.execute("INSERT INTO jobs (org_id, kind, payload) SELECT %s, 'connection.sync', %s WHERE NOT EXISTS ("
                         "SELECT 1 FROM jobs WHERE kind = 'connection.sync' AND status = 'queued' AND payload->>'connection_id' = %s)",
                         (c["org_id"], Jsonb({"connection_id": str(c["id"]), "reason": "capture.save"}), str(c["id"])))
            conn.execute("INSERT INTO jobs (org_id, kind, payload, run_after) VALUES (%s, 'teams.save', %s, now() + interval '1 minute')",
                         (c["org_id"], Jsonb({**p, "tries": tries + 1})))
        return {"waiting": tries + 1}
    topic = saved_topic(p["channel"], message)
    with conn.transaction():
        if conn.execute("SELECT 1 FROM knowledge_units WHERE owner_team = %s AND topic = %s", (c["team_id"], topic)).fetchone():
            added = None
        else:
            added = conn.execute(
                "INSERT INTO knowledge_units (org_id, owner_team, kind, topic, title, body, trust, status, evidence, sections, created_by) "
                "VALUES (%s, %s, 'fact', %s, %s, %s, 'observed', 'proposed', %s::uuid[], %s, 'teams-save') RETURNING id",
                (c["org_id"], c["team_id"], topic, body.splitlines()[0][:120] or "Saved from Teams", body, [held["id"]],
                 [held["heading_path"]])).fetchone()
            audit(conn, c["org_id"], "capture.save", {"connection_id": str(c["id"]), "channel": p["channel"], "message": message,
                                                      "user": user, "unit": str(added["id"])})
    tell(c, user_id, SAVED.format(team=c["team_name"]) if added else NOT_SAVED["already"])
    return {"saved": bool(added)}
