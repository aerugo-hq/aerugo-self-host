"""Teams (C1a): a standard channel reaches exactly its team's members and owners, never a guest or the whole org; a
sensitive or marked message is kept nowhere and moves its post's version; only consented teams' standard channels are
read; a delivery stands only on Microsoft's token, for this app, from an allowed service address it names, signed by a
key endorsed for Teams; and from a "Don't capture" action only the ids are taken."""
import time
import uuid

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from kl import config, teams, teams_bot

APP, TENANT = str(uuid.uuid4()), str(uuid.uuid4())
SERVICE = "https://smba.trafficmanager.net/amer/"
CHANNEL = "19:abcDEF123@thread.tacv2"


def member(email, *roles, user=None):
    return {"userId": user or str(uuid.uuid4()), "email": email, "displayName": email.split("@")[0], "roles": list(roles)}


def message(mid, text, *, reply_to=None, when="2026-10-13T14:00:00Z", **more):
    return {"id": mid, "messageType": "message", "createdDateTime": when, "replyToId": reply_to,
            "from": {"user": {"id": str(uuid.uuid4()), "displayName": "Eli Brooks"}},
            "body": {"contentType": "html", "content": f"<p>{text}</p>"}, **more}


def test_a_channel_reaches_its_teams_members_and_owners_never_a_guest_or_the_org():
    members = [member("ana@acme.test"), member("Eli@Acme.test", "owner"), member("vendor@else.test", "guest"),
               {"userId": str(uuid.uuid4()), "roles": [], "displayName": "No email"}]
    assert teams.readers(members) == ["ext:microsoft.ana@acme.test", "ext:microsoft.eli@acme.test"]
    assert not any(r.startswith("org:") for r in teams.readers(members))
    assert teams.readers([]) == []


def test_a_message_is_plain_text_with_its_mentions():
    body = {"contentType": "html", "content": '<div><at id="0">Eli Brooks</at> use forklift&nbsp;5<br>for dock 2</div>'}
    assert teams.plain(body) == "@Eli Brooks use forklift\xa05\nfor dock 2"
    assert teams.plain({"contentType": "text", "content": "a <b> stays"}) == "a <b> stays"
    assert not teams.kept({**message("1760364000000", "joined"), "messageType": "systemEventMessage"})
    assert not teams.kept({**message("1760364000000", "gone"), "deletedDateTime": "2026-10-14T00:00:00Z"})


def test_a_sensitive_or_marked_message_is_kept_nowhere_and_moves_its_posts_version():
    thread = [message("1760364000000", "Forklift 3 is out until Thursday."),
              message("1760364060000", "Sam Rivera is having surgery on Tuesday.", reply_to="1760364000000"),
              message("1760364120000", "Use forklift 5 for dock 2.", reply_to="1760364000000")]
    kept = [b for _, b in teams.sections_of(thread)]
    assert kept == ["Forklift 3 is out until Thursday.", "Use forklift 5 for dock 2."]  # C2a's gate, for Teams
    marked = [b for _, b in teams.sections_of(thread, frozenset({"1760364120000"}))]
    assert marked == ["Forklift 3 is out until Thursday."]
    assert teams.version(thread) != teams.version(thread, frozenset({"1760364120000"}))  # read again without it
    assert teams.holding(CHANNEL, thread[2]) == teams.holding(CHANNEL, thread[0]) == [teams.key_of(CHANNEL, "1760364000000")]


class Graph(teams.TeamsClient):
    """Graph as a team's owner left it: one team consented, one not; a standard, a private, and a shared channel."""

    def __init__(self, consented, refused):
        self.base, self.teams, self.consented, self.refused = "https://graph.microsoft.com", [consented, refused], consented, refused

    def _get(self, url, params, headers=None):
        if self.refused in url:
            raise httpx.HTTPStatusError("403", request=httpx.Request("GET", url), response=httpx.Response(403))
        return {"id": self.consented, "displayName": "Reno DC"}

    def _pages(self, path, params, headers=None):
        return [{"id": CHANNEL, "displayName": "Shift handover", "membershipType": "standard"},
                {"id": "19:private1@thread.tacv2", "displayName": "Leads", "membershipType": "private"},
                {"id": "19:shared1@thread.tacv2", "displayName": "Vendors", "membershipType": "shared"}]


def test_only_a_consented_teams_standard_channels_are_read():
    consented, refused = str(uuid.uuid4()), str(uuid.uuid4())
    found = Graph(consented, refused).discover()
    assert [(c.external_id, c.default_ref, c.name) for c in found] == [(CHANNEL, consented, "Reno DC / Shift handover")]


KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
SKYPE_ONLY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def keys(monkeypatch):
    found = {"teams": (KEY.public_key(), ("msteams", "skype")), "skype": (SKYPE_ONLY.public_key(), ("skype",))}
    monkeypatch.setattr(teams_bot, "signing_key", lambda kid: found.get(kid))
    monkeypatch.setattr(config, "TEAMS_BOT_SERVICES", ["https://smba.trafficmanager.net/"])


def token(key=KEY, kid="teams", **claims):
    now = int(time.time())
    body = {"iss": teams_bot.ISSUER, "aud": APP, "serviceurl": SERVICE, "nbf": now - 60, "exp": now + 3600, **claims}
    return "Bearer " + jwt.encode({k: v for k, v in body.items() if v is not None}, key, algorithm="RS256", headers={"kid": kid})


def test_a_delivery_stands_only_on_microsofts_token_for_this_app_and_its_service_address(keys):
    assert teams_bot.verify(token(), APP, SERVICE)["aud"] == APP
    refused = {
        "another app's token": (token(aud=str(uuid.uuid4())), SERVICE),
        "a token another issuer made": (token(iss="https://sts.windows.net/x/"), SERVICE),
        "an expired token": (token(exp=int(time.time()) - 3600), SERVICE),
        "a token for another service address": (token(serviceurl="https://smba.trafficmanager.net/emea/"), SERVICE),
        "a service address not allowed": (token(serviceurl="https://evil.example/"), "https://evil.example/"),
        "a key Bot Framework endorses only for Skype": (token(SKYPE_ONLY, "skype"), SERVICE),
        "a key Bot Framework doesn't publish": (token(rsa.generate_private_key(public_exponent=65537, key_size=2048), "mine"), SERVICE),
        "no token at all": ("", SERVICE),
        "an unsigned token": ("Bearer " + jwt.encode({"iss": teams_bot.ISSUER, "aud": APP, "serviceurl": SERVICE,
                                                      "exp": int(time.time()) + 60}, "", algorithm="none"), SERVICE),
    }
    for why, (auth, service) in refused.items():
        with pytest.raises(teams_bot.Refused):
            teams_bot.verify(auth, APP, service)
            pytest.fail(f"{why} was taken")


def test_from_a_dont_capture_action_only_the_ids_are_taken():
    user = str(uuid.uuid4())
    activity = {"type": "invoke", "name": "composeExtension/submitAction", "id": "f:123",
                "from": {"aadObjectId": user}, "channelData": {"channel": {"id": CHANNEL}, "tenant": {"id": TENANT}},
                "value": {"commandId": "dontCapture", "messagePayload": {"id": "1760364120000", "replyToId": "1760364000000",
                                                                          "body": {"content": "never taken from here"}}}}
    assert teams_bot.mark_request(activity, TENANT) == {"channel": CHANNEL, "message": "1760364120000", "user": user,
                                                        "post": "1760364000000"}
    assert teams_bot.mark_request({**activity, "channelData": {"channel": {"id": CHANNEL}, "tenant": {"id": str(uuid.uuid4())}}}, TENANT) is None
    assert teams_bot.mark_request({**activity, "value": {**activity["value"], "commandId": "save"}}, TENANT) is None
    assert teams_bot.mark_request({**activity, "channelData": {"channel": {"id": "19:x@thread.tacv2/../teams"}, "tenant": {"id": TENANT}}}, TENANT) is None
    assert teams_bot.mark_request({**activity, "type": "message"}, TENANT) is None


def test_only_someone_in_the_team_marks_a_message():
    eli, vendor = str(uuid.uuid4()), str(uuid.uuid4())
    members = [member("eli@acme.test", user=eli), member("vendor@else.test", "guest", user=vendor)]
    assert teams.may_mark(members, eli.upper())
    assert not teams.may_mark(members, vendor) and not teams.may_mark(members, str(uuid.uuid4())) and not teams.may_mark(members, "")
