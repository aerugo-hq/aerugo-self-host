"""The synthetic retailer (F9): the same seed and size always make the same company; its truth is consistent at any size;
each person reads only what each source's own permissions grant; the truth holds every kind of trap later items are
measured on; and what each fake serves carries those permissions."""
import pytest

from kl import company


@pytest.fixture(scope="module")
def world():
    return company.build()


def test_the_same_seed_and_size_build_the_same_company(world):
    again = company.build()
    assert [p.__dict__ for p in again.people] == [p.__dict__ for p in world.people]
    assert [d.__dict__ for d in again.docs] == [d.__dict__ for d in world.docs]


def test_another_seed_or_size_changes_who_works_there_but_never_a_documents_key(world):
    other = company.build(seed=8, stores=15)
    assert [p.name for p in other.people] != [p.name for p in world.people]
    keys = {d.name: d.key for d in other.docs}
    assert all(keys[d.name] == d.key for d in world.docs)


@pytest.mark.parametrize("stores", [3, 6, 15, 40])
def test_the_truth_is_consistent_at_every_size(stores):
    world = company.build(stores=stores)
    assert company.problems(world) == []
    assert len([p for p in world.places if p.kind == "store"]) == stores


def test_fewer_stores_than_the_people_who_sign_in_need_is_refused():
    with pytest.raises(ValueError):
        company.build(stores=2)


def test_each_person_reads_only_what_the_sources_grant(world):
    ana, cora, ivy, kai, gus = (world.visible(u) for u in ("ana", "cora", "ivy", "kai", "gus"))
    assert {"returns-2026", "key-thread-OR101", "recall-notice", "helpdesk-pos"} <= ana
    assert not {"lp-leaders", "pay-ranges", "QUAL-9", "key-thread-CO201", "pos-down", "supplier-audit"} & ana
    assert {"lp-leaders", "pos-down", "QUAL-8"} <= cora and "QUAL-9" not in cora   # a leader, but not a quality lead
    assert "pay-ranges" in ivy and "QUAL-9" in kai
    assert not {"leave-requests", "hr-warning"} & gus                               # People is a private team
    assert not {"teams-forklift", "meeting-recall-standup"} & world.visible("eli")   # not loaded until C1 and C3


def test_the_truth_holds_every_kind_of_trap(world):
    facts = {f.id: f for f in world.facts}
    assert facts["returns-window-2025"].status == "superseded" and facts["returns-window"].contradicted_by
    assert facts["sell-through"].contradicted_by == ("merch-sellthrough-2025",)
    assert facts["recall-decision"].kind == "decision"
    kinds = {q.kind for q in world.questions}
    assert {"stale", "decision", "location", "permission", "undocumented", "sensitive", "capture", "two-source"} <= kinds
    assert {q.needs for q in world.questions} >= {"C1", "C3"}  # F8's questions count since it was built, and C2a's
    max_weber = next(p for p in world.people if p.name == "Max Weber")
    assert not max_weber.active and not any(max_weber.email in members for members in world.groups.values())


def test_what_each_fake_serves_carries_the_sources_permissions(world):
    site = company.confluence_site(world)
    handbook, storeops = site["spaces"]["HANDBOOK"], site["spaces"]["STOREOPS"]
    assert handbook["groups"] == ["confluence-users"]
    assert next(p for p in storeops["pages"] if p["title"].startswith("Loss prevention"))["restricted_groups"] == ["store-leaders"]
    jira = company.jira_site(world)["projects"]["QUAL"]
    kai = world.person("kai")
    regulator = next(i for i in jira["issues"] if i["key"] == "QUAL-9")
    assert regulator["security"] and [v["emailAddress"] for v in regulator["viewers"]] == [kai.email]
    slack = company.slack_workspace(world)
    portland = slack["channels"]["CRTSOR101"]
    assert portland["private"] and world.person("ana").slack in portland["members"] and world.person("ben").slack not in portland["members"]
    assert not slack["channels"]["CRTALL"]["private"]
    max_weber = next(p for p in world.people if p.name == "Max Weber")
    assert next(u for u in slack["users"] if u["id"] == max_weber.slack)["deleted"]
    assert next(u for u in company.entra_tenant(world)["users"] if u["mail"] == max_weber.email)["accountEnabled"] is False


def test_pages_become_confluences_storage_format_with_their_text_escaped():
    assert company.storage("Fish & <chips>\n## Steps\n1. one\n2. two\n- a\n| A | B |\n| 1 | 2 |") == (
        "<p>Fish &amp; &lt;chips&gt;</p><h2>Steps</h2><ol><li>one</li><li>two</li></ol><ul><li>a</li></ul>"
        "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>")
