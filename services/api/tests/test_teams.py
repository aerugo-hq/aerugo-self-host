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
    assert teams_bot.action_request(activity, TENANT) == ("teams.exclude", {"channel": CHANNEL, "message": "1760364120000",
                                                                           "user": user, "post": "1760364000000"})
    assert teams_bot.action_request({**activity, "channelData": {"channel": {"id": CHANNEL}, "tenant": {"id": str(uuid.uuid4())}}}, TENANT) is None
    assert teams_bot.action_request({**activity, "value": {**activity["value"], "commandId": "save"}}, TENANT) is None
    assert teams_bot.action_request({**activity, "channelData": {"channel": {"id": "19:x@thread.tacv2/../teams"}, "tenant": {"id": TENANT}}}, TENANT) is None
    assert teams_bot.action_request({**activity, "type": "message"}, TENANT) is None


def test_only_someone_in_the_team_marks_a_message():
    eli, vendor = str(uuid.uuid4()), str(uuid.uuid4())
    members = [member("eli@acme.test", user=eli), member("vendor@else.test", "guest", user=vendor)]
    assert teams.may_mark(members, eli.upper())
    assert not teams.may_mark(members, vendor) and not teams.may_mark(members, str(uuid.uuid4())) and not teams.may_mark(members, "")


# ------------------------------------------------------------------ C1b: asks and saves
APP_USER = "29:1abcDEF-ghi_JKL"


def test_a_question_comes_from_a_1on1_message_or_from_a_mention_read_back_later():
    user = str(uuid.uuid4())
    base = {"type": "message", "id": "1760364300000", "from": {"id": APP_USER, "aadObjectId": user},
            "channelData": {"tenant": {"id": TENANT}}}
    personal = {**base, "text": "  Which forklift for dock 2?  ", "conversation": {"id": "a:1chatXYZ", "conversationType": "personal"}}
    assert teams_bot.ask_request(personal, TENANT, APP) == {"user": user, "user_id": APP_USER, "conversation": "a:1chatXYZ",
                                                           "via": "personal", "question": "Which forklift for dock 2?"}
    group = {**base, "text": "hi", "conversation": {"id": "19:meeting1@thread.v2", "conversationType": "groupChat"}}
    assert teams_bot.ask_request(group, TENANT, APP) is None  # group chats are never read

    def mention(app):
        return {**base, "text": "<at>Aerugo</at> which forklift?", "entities": [{"type": "mention", "mentioned": {"id": f"28:{app}"}}],
                "conversation": {"id": f"{CHANNEL};messageid=1760364000000", "conversationType": "channel"},
                "channelData": {"tenant": {"id": TENANT}, "channel": {"id": CHANNEL}}}
    asked = teams_bot.ask_request(mention(APP), TENANT, APP)
    assert asked == {"user": user, "user_id": APP_USER, "conversation": f"{CHANNEL};messageid=1760364000000", "via": "mention",
                     "channel": CHANNEL, "message": "1760364300000", "post": "1760364000000"} and "question" not in asked
    assert teams_bot.ask_request(mention(str(uuid.uuid4())), TENANT, APP) is None  # another app mentioned
    assert teams_bot.ask_request({**personal, "channelData": {"tenant": {"id": str(uuid.uuid4())}}}, TENANT, APP) is None


def test_a_mentions_question_drops_only_the_apps_own_mention():
    message = {"body": {"contentType": "html", "content": '<p><at id="0">Aerugo</at> where is <at id="1">Dock</at> 2?</p>'},
               "mentions": [{"id": 0, "mentioned": {"application": {"id": APP.upper()}}}, {"id": 1, "mentioned": {"user": {"id": "x"}}}]}
    assert teams.question_of(message, APP) == "where is @Dock 2?"


def test_only_someone_in_the_team_saves_their_own_message_or_one_in_a_captured_channel():
    eli, vendor, carol = (str(uuid.uuid4()) for _ in range(3))
    members = [member("eli@acme.test", user=eli), member("carol@acme.test", user=carol), member("vendor@else.test", "guest", user=vendor)]
    own = {**message("1760364000000", "Use forklift 5."), "from": {"user": {"id": eli.upper(), "displayName": "Eli"}}}
    assert teams.may_save(members, eli, own, False, frozenset()) is None                 # your own, anywhere the app reads
    assert teams.may_save(members, carol, own, False, frozenset()) == "someone else's"   # someone else's: only where captured
    assert teams.may_save(members, carol, own, True, frozenset()) is None
    assert teams.may_save(members, vendor, own, True, frozenset()) == "saver"            # a guest saves nothing
    assert teams.may_save(members, eli, own, True, frozenset({"1760364000000"})) == "marked"
    sensitive = {**own, "body": {"contentType": "html", "content": "<p>Sam is having surgery on Tuesday.</p>"}}
    assert teams.may_save(members, eli, sensitive, True, frozenset()) == "sensitive"
    assert teams.may_save(members, eli, {}, True, frozenset()) == "gone"


def test_a_save_is_taken_only_with_where_to_tell_the_saver():
    user = str(uuid.uuid4())
    activity = {"type": "invoke", "name": "composeExtension/submitAction", "id": "f:9", "from": {"id": APP_USER, "aadObjectId": user},
                "channelData": {"channel": {"id": CHANNEL}, "tenant": {"id": TENANT}},
                "value": {"commandId": "aerugoSave", "messagePayload": {"id": "1760364000000"}}}
    assert teams_bot.action_request(activity, TENANT) == ("teams.save", {"channel": CHANNEL, "message": "1760364000000", "user": user,
                                                                         "user_id": APP_USER})
    assert teams_bot.action_request({**activity, "from": {"aadObjectId": user}}, TENANT) is None


def test_the_teams_reply_is_plain_text_naming_sources_by_title():
    from kl import teams_ask
    result = {"answer": "Use [this](https://evil.example) <at>everyone</at>", "withheld_by_policy": 1, "confidence": {"level": "high"},
              "citations": [{"id": "teams-abc-1760364000000", "title": "Reno DC: Forklift 3", "cited": True},
                            {"id": "unit:0b1d", "title": "Forklift", "cited": True, "unit": {"kind": "fact"}},
                            {"id": "legal-hold", "title": "Not cited", "cited": False}]}
    reply = teams_ask.reply_of(result, "https://aerugo.example")
    assert "- Reno DC: Forklift 3: https://aerugo.example/#/documents/teams-abc-1760364000000" in reply
    assert "- Forklift (knowledge)" in reply and "Not cited" not in reply and "Confidence: high" in reply
    assert "Some sources aren't shown in Teams" in reply


class Lane:
    """Stands in for the answers lane's connection: the Teams connection, and the channel a mention is in."""

    def __init__(self, audits):
        self.audits = audits

    def transaction(self):
        import contextlib
        return contextlib.nullcontext()

    def execute(self, sql, params=()):
        if sql.startswith("INSERT INTO audit_events"):
            self.audits.append(params)
        row = {"id": "c1", "org_id": "o1", "kind": "teams", "status": "ready", "team": "distribution", "ms_team": str(uuid.UUID(int=7)),
               "config": {"tenant_id": TENANT, "client_id": APP, "api_base": "https://graph.microsoft.com",
                          "answers": {"model": "distribution/fake"}}}

        class Result:
            def fetchone(self):
                return row
        return Result()


def test_only_the_asker_teams_reads_back_is_answered_and_only_for_their_own_mention(monkeypatch):
    from kl import chat_ask, teams_ask
    eli = str(uuid.uuid4())
    told, asked, audits = [], [], []
    person = {"aadObjectId": eli, "email": "Eli@Acme.test", "tenantId": TENANT, "userRole": "user"}

    class Bot:
        def __init__(self, found):
            self.found = found

        def member(self, conversation, user_id):
            return self.found

    class Graph:
        def __init__(self, author):
            self.author = author

        def message(self, team, channel, message, post=None):
            return {"id": message, "from": {"user": {"id": self.author}}, "mentions": [],
                    "body": {"contentType": "html", "content": "<p>Which forklift?</p>"}}
    monkeypatch.setattr(teams_bot, "tell", lambda c, user_id, text: told.append((user_id, text)))
    monkeypatch.setattr(chat_ask, "answer_as",
                        lambda conn, account, email, q, s: asked.append((account, q)) or (None, {"answer": "Forklift 5."}, "eli"))

    def job(**more):
        return {"payload": {"connection_id": "c1", "user": eli, "user_id": APP_USER, "conversation": "a:1chatXYZ", **more}}
    for who in ({**person, "aadObjectId": str(uuid.uuid4())}, {**person, "userRole": "guest"}, {**person, "tenantId": str(uuid.uuid4())}, {}):
        monkeypatch.setattr(teams_bot, "bot_for", lambda c, who=who: Bot(who))
        assert teams_ask.ask(Lane(audits), job(via="personal", question="Which forklift?")) == {"refused": "outsider"}
    monkeypatch.setattr(teams_bot, "bot_for", lambda c: Bot(person))
    assert teams_ask.ask(Lane(audits), job(via="personal", question="Which forklift?")) == {"answered": True}
    assert asked[-1] == ("ext:microsoft.eli@acme.test", "Which forklift?") and told[-1] == (APP_USER, "Forklift 5.") and len(audits) == 1
    mention = job(via="mention", channel=CHANNEL, message="1760364300000", conversation=f"{CHANNEL};messageid=1760364000000")
    monkeypatch.setattr(teams, "client_for", lambda c: Graph(str(uuid.uuid4())))  # someone else's message
    assert teams_ask.ask(Lane(audits), mention) == {"skipped": "the message isn't the asker's"}
    monkeypatch.setattr(teams, "client_for", lambda c: Graph(eli))
    assert teams_ask.ask(Lane(audits), {"payload": {**mention["payload"], "question": "never from here"}}) == {"answered": True}
    assert asked[-1] == ("ext:microsoft.eli@acme.test", "Which forklift?")  # read back from Graph, not the delivery


def test_a_save_is_refused_unless_teams_says_the_saver_is_who_the_delivery_names(monkeypatch):
    eli, told = str(uuid.uuid4()), []

    class Bot:
        def __init__(self, found):
            self.found = found

        def member(self, conversation, user_id):
            return self.found

    class Graph:
        def message(self, team, channel, message, post=None):
            raise AssertionError("a save went on to Graph before its saver was confirmed")
    monkeypatch.setattr(teams_bot, "tell", lambda c, user_id, text: told.append(text))
    monkeypatch.setattr(teams, "client_for", lambda c: Graph())
    monkeypatch.setattr(teams_bot, "channel_of", lambda conn, p: {"id": "c1", "org_id": "o1", "ms_team": str(uuid.UUID(int=7)),
                                                                  "config": {"tenant_id": TENANT}, "capture": "on"})
    job = {"payload": {"connection_id": "c1", "channel": CHANNEL, "message": "1760364000000", "user": eli, "user_id": APP_USER}}
    person = {"aadObjectId": eli, "tenantId": TENANT, "userRole": "user"}
    for found in ({**person, "aadObjectId": str(uuid.uuid4())}, {**person, "userRole": "guest"}, {**person, "tenantId": str(uuid.uuid4())}, {}):
        monkeypatch.setattr(teams_bot, "bot_for", lambda c, found=found: Bot(found))
        assert teams_bot.save(Lane([]), job) == {"refused": "saver"}
    assert told == [teams_bot.NOT_SAVED["saver"]] * 4
