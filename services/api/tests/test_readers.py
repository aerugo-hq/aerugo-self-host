"""Who may read what (M4): each connector's readers function, and the principal rules every one of them leans on.
These decide permissions before anything is indexed, so each fails closed: anything not understood grants nothing."""
from kl import confluence, curator, gcp_secrets, gdrive, identity_match, jira, microsoft365, slack
from kl.principals import is_team, is_valid

GROUP_ID = "0f3c2a1e-4b5d-4e6f-8a9b-0c1d2e3f4a5b"


# ---- principals
def test_a_principal_is_a_known_kind_and_a_safe_name():
    assert is_valid("user:alice") and is_valid("org:acme") and is_valid("ext:github.octo-cat")


def test_a_principal_that_could_split_widen_or_wildcard_is_refused():
    for bad in ("user:", "admin:alice", "user:alice,org:acme", "user:*", "user:al ice", "user:-alice", "user:" + "a" * 129):
        assert not is_valid(bad), bad


def test_a_team_slug_is_lowercase_letters_digits_and_dashes():
    assert is_team("payments") and is_team("data-2")
    for bad in ("Payments", "pay_ments", "a,b", "-pay", ""):
        assert not is_team(bad), bad


# ---- Google Drive
def drive(*permissions: dict, domain: str | None = "acme.test", groups: dict | None = None) -> list[str]:
    return gdrive.readers(list(permissions), "acme", domain, groups)


def test_a_drive_file_shared_with_a_person_reaches_that_person():
    assert drive({"type": "user", "role": "reader", "emailAddress": "Alice@Acme.Test"}) == ["ext:google.alice@acme.test"]


def test_a_drive_role_that_cant_read_grants_nothing():
    assert drive({"type": "user", "role": "unknown", "emailAddress": "alice@acme.test"}) == []


def test_a_drive_group_share_reaches_the_groups_members_as_the_directory_lists_them():
    groups = {"team@acme.test": ["bob@acme.test", "carol@acme.test"]}
    got = drive({"type": "group", "role": "reader", "emailAddress": "Team@Acme.Test"}, groups=groups)
    assert got == ["ext:google.bob@acme.test", "ext:google.carol@acme.test"]


def test_a_drive_group_no_directory_lists_grants_nothing():
    assert drive({"type": "group", "role": "reader", "emailAddress": "unknown@acme.test"}, groups={}) == []


def test_a_drive_domain_share_reaches_the_org_only_for_the_companys_own_domain():
    assert drive({"type": "domain", "role": "reader", "domain": "ACME.test"}) == ["org:acme"]
    assert drive({"type": "domain", "role": "reader", "domain": "other.test"}) == []
    assert drive({"type": "domain", "role": "reader", "domain": "acme.test"}, domain=None) == []


def test_a_drive_link_for_anyone_grants_nothing():
    assert drive({"type": "anyone", "role": "reader"}) == []


def test_a_drive_address_that_isnt_a_safe_principal_is_dropped():
    assert drive({"type": "user", "role": "reader", "emailAddress": "al ice@acme.test"}) == []


# ---- Microsoft 365
def m365(*permissions, groups=None):
    seen = {}
    return microsoft365.readers(list(permissions), groups or {}, seen), seen


def test_a_sharepoint_file_shared_with_a_person_reaches_them_and_notes_their_account():
    got, seen = m365({"roles": ["read"], "grantedToV2": {"user": {"email": "Bob@Acme.Test", "displayName": "Bob"}}})
    assert got == ["ext:microsoft.bob@acme.test"] and seen["bob@acme.test"]["name"] == "Bob"


def test_a_sharepoint_entra_group_reaches_its_members():
    got, _ = m365({"roles": ["write"], "grantedToV2": {"group": {"id": GROUP_ID.upper()}}},
                  groups={GROUP_ID: ["carol@acme.test", "dave@acme.test"]})
    assert got == ["ext:microsoft.carol@acme.test", "ext:microsoft.dave@acme.test"]


def test_a_sharepoint_organization_link_and_a_site_group_grant_nothing():
    got, _ = m365({"roles": ["read"], "link": {"scope": "organization", "type": "view"}},
                  {"roles": ["read"], "grantedToV2": {"siteGroup": {"id": "3", "displayName": "Members"}}})
    assert got == []


def test_a_sharepoint_link_reaches_only_the_people_it_names():
    got, _ = m365({"roles": ["read"], "link": {"scope": "users"},
                   "grantedToIdentitiesV2": [{"siteUser": {"loginName": "i:0#.f|membership|bob@acme.test"}}]})
    assert got == ["ext:microsoft.bob@acme.test"]


def test_a_sharepoint_role_that_cant_read_grants_nothing():
    got, _ = m365({"roles": ["sp.custom"], "grantedToV2": {"user": {"email": "bob@acme.test"}}})
    assert got == []


# ---- Slack
USERS = {"U1": {"team": "T1", "gone": False, "bot": False}, "U2": {"team": "T1", "gone": True, "bot": False},
         "U3": {"team": "T1", "gone": False, "bot": True}, "U4": {"team": "T2", "gone": False, "bot": False}}


def channel_readers(channel, company=True):
    return slack.readers_of(channel, ["U1", "U2", "U3", "U4", "U9"], USERS, "T1", company, "acme")


def test_a_public_unshared_channel_on_a_confirmed_company_workspace_reaches_the_org():
    assert channel_readers({"is_private": False}) == ["org:acme"]


def test_a_private_channel_reaches_only_its_present_human_members_from_this_workspace():
    assert channel_readers({"is_private": True}) == ["ext:slack.U1"]


def test_a_shared_channel_or_an_unconfirmed_workspace_never_reaches_the_whole_org():
    assert channel_readers({"is_private": False, "is_ext_shared": True}) == ["ext:slack.U1"]
    assert channel_readers({"is_private": False}, company=False) == ["ext:slack.U1"]


# ---- Confluence
def test_a_confluence_page_is_read_only_by_who_passes_every_restricted_level():
    assert confluence.narrowed({"a", "b", "c"}, [{"b", "c", "d"}, {"c"}]) == {"c"}


def test_a_confluence_restriction_under_an_open_space_is_exactly_its_own_readers():
    assert confluence.narrowed(confluence.EVERYONE, [{"a", "b"}]) == {"a", "b"}


def test_a_confluence_page_without_restrictions_keeps_its_spaces_readers():
    assert confluence.narrowed(confluence.EVERYONE, []) == confluence.EVERYONE


# ---- Jira
def test_a_jira_account_id_becomes_a_principal_its_colons_made_dots():
    assert jira.account("557058:abc-123") == "ext:atlassian.557058.abc-123"


def test_a_jira_account_id_that_isnt_safe_grants_nothing():
    assert jira.account(None) is None and jira.account("bad id") is None


def test_jira_readers_are_the_accounts_it_names_each_noted_for_identity_matching():
    seen = {}
    got = jira.readers_of([{"accountId": "557058:bob", "emailAddress": "bob@acme.test", "displayName": "Bob"},
                           {"accountId": "bad id"}], seen)
    assert got == ["ext:atlassian.557058.bob"] and seen == {"557058.bob": {"email": "bob@acme.test", "name": "Bob"}}


# ---- the curator
def test_a_draft_is_held_back_from_repo_readers_who_are_unlinked_or_cant_read_every_source():
    links = {"octo": ["alice"], "bob-gh": ["bob"]}
    assert curator.unverified_readers(["octo", "bob-gh", "nobody"], links, lambda user: user == "alice") == ["bob-gh", "nobody"]


# ---- identity matching and secret references
def test_only_a_well_formed_email_in_the_companys_own_domains_is_matched(monkeypatch):
    monkeypatch.setattr(identity_match.config, "ORG_EMAIL_DOMAINS", ["acme.test"])
    assert identity_match.company_email(" Alice@Acme.Test ") == "alice@acme.test"
    assert identity_match.company_email("alice@other.test") is None
    assert identity_match.company_email("not an email") is None and identity_match.company_email(None) is None


def test_a_gcp_secret_reference_names_a_project_a_secret_and_a_version():
    assert gcp_secrets.parse("gcpsm://projects/acme-prod-1/secrets/kl_acme_pay_key") == ("acme-prod-1", "kl_acme_pay_key", "latest")
    assert gcp_secrets.parse("gcpsm://projects/acme-prod-1/secrets/kl_acme_pay_key/versions/3") == ("acme-prod-1", "kl_acme_pay_key", "3")
    assert gcp_secrets.parse("gcpsm://projects/../secrets/x") is None and gcp_secrets.parse(None) is None


def test_a_team_owns_only_secrets_named_for_it_in_a_project_the_deployment_allows(monkeypatch):
    monkeypatch.setattr(gcp_secrets.config, "GCP_SECRET_PROJECTS", ["acme-prod-1"])
    ref = lambda project, secret: f"gcpsm://projects/{project}/secrets/{secret}"
    assert gcp_secrets.team_owns("acme", "payments", ref("acme-prod-1", "kl_acme_payments_jira"))
    assert not gcp_secrets.team_owns("acme", "payments", ref("acme-prod-1", "kl_acme_finance_jira"))  # another team's
    assert not gcp_secrets.team_owns("acme", "payments", ref("acme-prod-1", "kl_acme_payments_"))     # no name
    assert not gcp_secrets.team_owns("acme", "payments", ref("other-proj-1", "kl_acme_payments_jira"))  # not allowed
