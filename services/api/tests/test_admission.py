"""Who gets in (G4): a person's groups in OpenFGA come to match their token; with admission `teams`, only someone on an
onboarded team (or an org admin, or a service account) gets in; a refusal is never remembered; and when OpenFGA can't
answer, nobody is let in on a guess."""
import httpx
import pytest
from fastapi import HTTPException

from kl import admission
from kl.auth import Caller


def person(name: str, *groups: str) -> Caller:
    return Caller(username=name, groups=tuple(groups), org_slug="acme")


class Fga:
    """A stand-in for OpenFGA: who's in which group, and who holds a team role."""

    def __init__(self, groups=None, teams=None, down=False):
        self.groups, self.teams, self.down, self.writes = groups or {}, teams or {}, down, []

    def read(self, key):
        if self.down:
            raise httpx.ConnectError("down")
        return [{"user": key["user"], "relation": "member", "object": f"group:{g}"} for g in self.groups.get(key["user"], ())]

    def write(self, writes=(), deletes=()):
        self.writes.append((set(writes), set(deletes)))

    def list_objects(self, user, relation, type_):
        return [f"workspace:{t}" for t in self.teams.get((user, relation), ())]

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
    fga = Fga(groups={"user:alice": ["payments"]}, teams={("user:alice", "member"): ["payments"]})
    monkeypatch.setattr(admission, "fga", fga)
    admission.admit(person("alice", "payments"))
    assert fga.writes == []


def test_someone_on_no_team_is_refused_while_admission_is_teams(monkeypatch):
    monkeypatch.setattr(admission, "fga", Fga())
    with pytest.raises(HTTPException) as refused:
        admission.admit(person("nora", "pilot"))
    assert refused.value.status_code == 403 and "onboarded" in refused.value.detail


def test_a_team_member_or_auditor_gets_in(monkeypatch):
    monkeypatch.setattr(admission, "fga", Fga(teams={("user:alice", "member"): ["payments"], ("user:ann", "auditor"): ["legal"]}))
    assert admission.admit(person("alice")).username == "alice"
    assert admission.admit(person("ann")).username == "ann"


def test_anyone_signed_in_gets_in_while_admission_is_org(monkeypatch):
    monkeypatch.setattr(admission, "fga", Fga())
    monkeypatch.setattr(admission, "mode", lambda: "org")
    assert admission.admit(person("nora", "pilot")).username == "nora"


def test_a_refusal_is_not_remembered_so_onboarding_takes_effect_at_once(monkeypatch):
    fga = Fga()
    monkeypatch.setattr(admission, "fga", fga)
    with pytest.raises(HTTPException):
        admission.admit(person("nora", "pilot"))
    fga.teams[("user:nora", "member")] = ["edw"]
    assert admission.admit(person("nora", "pilot")).username == "nora"


def test_when_openfga_cant_answer_nobody_is_let_in_on_a_guess(monkeypatch):
    monkeypatch.setattr(admission, "fga", Fga(down=True))
    with pytest.raises(HTTPException) as unavailable:
        admission.admit(person("alice", "payments"))
    assert unavailable.value.status_code == 503
