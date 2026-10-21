"""The company's Teams bot, C1a's half (docs/ENTERPRISE_PLAN.md section 6): its deliveries checked, capture's notice
posted, and "Don't capture" taken.

Invariant 13 for Teams (decided 2026-10-21): Bot Framework signs no HMAC over a delivery's body. It sends a token
Microsoft signs, which stands in for one, checked here before anything else is acted on: RS256 with a key Bot
Framework publishes and endorses for Teams, issued by Bot Framework, for the connection's own app, unexpired (five
minutes' skew), and naming the service address the activity came from, which must be on the allowlist. A delivery is
taken once, by its activity's id. Because the body itself isn't signed, a delivery only queues a job, and that job reads
back from Graph everything it acts on (the message, that it's in a channel the connection reads, that whoever marked it
is in its team).

The bot writes to Teams only capture's notice, posted in a channel when capture starts and stops (decided 2026-10-20),
through the Bot Connector, as the app, to the service address the connection names (on the allowlist), never one a
delivery names."""
import logging
import threading
import time
from urllib.parse import urlparse

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
DONT_CAPTURE = "dontCapture"  # the message action's command id, in the Teams app's manifest
NOTICE_ON = ("Aerugo now proposes knowledge from this channel's posts, for {team}'s curators to confirm; only this team's "
             "members can read it. To keep a message out, choose Don't capture from its More actions menu. To stop, ask "
             "{team}'s admins in Aerugo, or remove the Aerugo app from this team.")
NOTICE_OFF = "Aerugo no longer proposes knowledge from this channel."
MARK_REPLY = "Asked. If Aerugo reads this channel and you're in its team, this message won't be kept or proposed from again."

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


def mark_request(activity: dict, tenant: str) -> dict | None:
    """The ids of a "Don't capture" message action, or None: only the channel, the message (and its post, for a reply),
    and who chose it are taken from a delivery; the worker reads the rest back from Graph."""
    value, data = _part(activity, "value"), _part(activity, "channelData")
    if activity.get("type") != "invoke" or activity.get("name") != "composeExtension/submitAction" or value.get("commandId") != DONT_CAPTURE:
        return None
    payload = _part(value, "messagePayload")
    channel = str(_part(data, "channel").get("id") or "")
    in_tenant = str(_part(data, "tenant").get("id") or "").lower()
    user = str(_part(activity, "from").get("aadObjectId") or "").lower()
    message, post = str(payload.get("id") or ""), str(payload.get("replyToId") or "")
    if in_tenant != tenant.lower() or not teams.CHANNEL.fullmatch(channel) or not teams.MESSAGE.fullmatch(message) or not GUID.fullmatch(user):
        return None
    return {"channel": channel, "message": message, "user": user, **({"post": post} if teams.MESSAGE.fullmatch(post) else {})}


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
DROP_PROPOSALS_SQL = """DELETE FROM knowledge_units WHERE status = 'proposed' AND created_by = 'extraction'
AND evidence && ARRAY(SELECT id FROM documents WHERE connection_id = %s AND external_key = ANY(%s))"""


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
        conn.execute(DROP_PROPOSALS_SQL, (c["id"], teams.holding(p["channel"], found)))  # a curator's confirmed unit stays (F6)
        if added:
            conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'connection.sync', %s)",
                         (c["org_id"], Jsonb({"connection_id": str(c["id"]), "full": True, "reason": "capture.exclude"})))
            audit(conn, c["org_id"], "capture.exclude", {"connection_id": str(c["id"]), "channel": p["channel"], "message": message})
    return {"excluded": bool(added)}
