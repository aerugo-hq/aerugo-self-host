"""Experts' answers (F5b): a reply naming someone's sensitive matters or personal data is never kept as knowledge; a kept
reply's readers are the team's own members and groups, never a grant to the whole company, or everyone in it."""
from kl import experts


def test_a_reply_naming_someones_health_or_personal_data_is_never_kept():
    assert experts.keepable("The spare ladder keys are in the manager's office, hook 4.")
    assert not experts.keepable("Jamie Doe had surgery on Tuesday, so Lee covers the queue.")
    assert not experts.keepable("Her social security number is 123-45-6789, for the payroll form.")
    assert experts.keepable("Call the Risk line on 555-0170, any time.")  # a business number is knowledge


def test_a_kept_replys_readers_are_its_teams_own_or_the_whole_company(monkeypatch):
    monkeypatch.setattr(experts.notion, "team_readers", lambda team: ["group:payments", "user:alice"] if team == "payments" else [])
    assert experts.readers_for("team", "payments", "acme") == ["group:payments", "user:alice"]
    assert experts.readers_for("org", "payments", "acme") == ["org:acme"]
    assert experts.readers_for("team", "nobody", "acme") == []  # a team with no one on it keeps nothing
