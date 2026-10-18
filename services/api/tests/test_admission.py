"""Who gets in (G4), and what they're a member of: a person's groups in OpenFGA come to match their token, and signing in
makes them a member of the org (never a service account); with admission `teams`, only someone a team grants a role, to
them or to one of their groups (or an org admin, or a service account), gets in, and a team granted to the whole org
onboards nobody; a refusal is never remembered; and when OpenFGA can't answer, nobody is let in on a guess."""
import httpx
import pytest
from fastapi import HTTPException

from kl import admission
from kl.auth import Caller


def person(name: str, *groups: str) -> Caller:
    return Caller(username=name, groups=tuple(groups), org_slug="acme")


class Fga:
    """A stand-in for OpenFGA: who's in which group and in the org, and which roles each person or group holds on teams
    (`teams`: a principal, as OpenFGA stores it, to its (role, team) pairs)."""

    def __init__(self, groups=None, teams=None, org=(), down=False):
        self.groups, self.teams, self.org, self.down, self.writes = groups or {}, teams or {}, set(org), down, []

    def read(self, key):
        if self.down:
            raise httpx.ConnectError("down")
        user, obj = key["user"], key["object"]
        if obj == "group:":
            return [{"user": user, "relation": "member", "object": f"group:{g}"} for g in self.groups.get(user, ())]
        if obj.startswith("org:"):
            return [{"user": user, "relation": "member", "object": obj}] if user in self.org else []
        return [{"user": user, "relation": role, "object": f"workspace:{team}"} for role, team in self.teams.get(user, ())]

    def write(self, writes=(), deletes=()):
        self.writes.append((set(writes), set(deletes)))

    def check(self, user, relation, obj):
        return False


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for cache in (admission._synced, admission._admitted):
        cache.clear()
    monkeypatch.setattr(admission, "mode", lambda: "teams")


def test_a_persons_groups_come_to_match_their_token():
    writes, deletes = admission.group_changes("user:nora", {"pilot", "old-team"}, {"pilot", "edw-engineering"})
    assert writes == {("user:nora", "member", "group:edw-engineering")} and deletes == {("user:nora", "member", "group:old-team")}


def test_an_unchanged_set_of_groups_writes_nothing(monkeypatch):
    fga = Fga(groups={"user:alice": ["payments"]}, org={"user:alice"}, teams={"user:alice": [("member", "payments")]})
    monkeypatch.setattr(admission, "fga", fga)
    admission.admit(person("alice", "payments"))
    assert fga.writes == []


def test_signing_in_makes_a_person_a_member_of_the_org(monkeypatch):
    fga = Fga()
    monkeypatch.setattr(admission, "fga", fga)
    monkeypatch.setattr(admission, "mode", lambda: "org")
    admission.admit(person("nora", "pilot"))
    assert ("user:nora", "member", "org:acme") in fga.writes[0][0]


def test_a_service_account_never_becomes_a_member_of_the_org_or_a_group(monkeypatch):
    fga = Fga()
    monkeypatch.setattr(admission, "fga", fga)
    admission.admit(Caller(username="service-account-ci", groups=("payments",), org_slug="acme", service=True))
    assert fga.writes == []


def test_someone_on_no_team_is_refused_while_admission_is_teams(monkeypatch):
    monkeypatch.setattr(admission, "fga", Fga())
    with pytest.raises(HTTPException) as refused:
        admission.admit(person("nora", "pilot"))
    assert refused.value.status_code == 403 and "onboarded" in refused.value.detail


def test_a_team_granted_to_the_whole_org_onboards_nobody(monkeypatch):
    monkeypatch.setattr(admission, "fga", Fga(org={"user:nora"}, teams={"org:acme#member": [("member", "commons")]}))
    with pytest.raises(HTTPException) as refused:
        admission.admit(person("nora", "pilot"))
    assert refused.value.status_code == 403


def test_a_team_member_or_auditor_gets_in(monkeypatch):
    monkeypatch.setattr(admission, "fga", Fga(teams={"user:alice": [("member", "payments")], "user:ann": [("auditor", "legal")]}))
    assert admission.admit(person("alice")).username == "alice"
    assert admission.admit(person("ann")).username == "ann"


def test_a_team_granted_to_ones_group_lets_one_in(monkeypatch):
    monkeypatch.setattr(admission, "fga", Fga(teams={"group:pilot#member": [("member", "edw")]}))
    assert admission.admit(person("nora", "pilot")).username == "nora"


def test_anyone_signed_in_gets_in_while_admission_is_org(monkeypatch):
    monkeypatch.setattr(admission, "fga", Fga())
    monkeypatch.setattr(admission, "mode", lambda: "org")
    assert admission.admit(person("nora", "pilot")).username == "nora"


def test_a_refusal_is_not_remembered_so_onboarding_takes_effect_at_once(monkeypatch):
    fga = Fga()
    monkeypatch.setattr(admission, "fga", fga)
    with pytest.raises(HTTPException):
        admission.admit(person("nora", "pilot"))
    fga.teams["user:nora"] = [("member", "edw")]
    assert admission.admit(person("nora", "pilot")).username == "nora"


def test_when_openfga_cant_answer_nobody_is_let_in_on_a_guess(monkeypatch):
    monkeypatch.setattr(admission, "fga", Fga(down=True))
    with pytest.raises(HTTPException) as unavailable:
        admission.admit(person("alice", "payments"))
    assert unavailable.value.status_code == 503
