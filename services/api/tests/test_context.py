"""Who is asking (F8): a role names a title or its head before a comma; the asker's role and place come from their own
listing; a model is told only their role and place; a contact's current holder is found up the asker's manager chain,
else as the one person with that title in the unit's places, never two, never the asker; and each directory's listing
says title, department, place, and manager the way that directory names them."""
from kl import company
from kl.context import asked_by, holder_in, places_of, role_matches, roles_of
from kl.directory import about


def person(email, title, manager=None, state=None, office=None, name=None):
    return {"email": email, "display_name": name or email.split("@")[0].title(), "title": title, "department": None,
            "office": office, "city": None, "state": state, "country": None, "manager_email": manager}


def test_a_role_names_a_title_or_its_head_before_a_comma():
    assert role_matches("District manager", "District manager, Mountain")
    assert role_matches("store manager", "Store manager")
    assert not role_matches("Manager", "District manager, Mountain")
    assert not role_matches("District manager", "District managers' assistant")
    assert not role_matches("District manager", None)


def test_the_askers_role_and_place_come_from_their_listing_and_a_model_hears_only_those():
    me = {**person("ben@acme.test", "Store associate", state="CO", office="CO201"), "department": "Store operations"}
    assert roles_of(me) == {"Store associate", "Store operations"} and places_of(me) == {"CO201", "CO"}
    assert asked_by(me) == "Asked by: Store associate, Store operations at CO201, CO."
    assert "ben@" not in asked_by(me)
    assert asked_by(None) == ""


def test_a_contacts_holder_is_found_up_the_askers_manager_chain():
    dara = person("dara@acme.test", "District manager, Mountain", "priya@acme.test", "CO")
    store = person("sm@acme.test", "Store manager", "dara@acme.test", "CO")
    me = person("ben@acme.test", "Store associate", "sm@acme.test", "CO")
    people = {p["email"]: p for p in (dara, store)}
    assert holder_in("District manager", me, people, [], set()) == dara
    assert holder_in("Store manager", me, people, [], set()) == store


def test_else_the_one_person_with_that_title_in_the_places_and_never_two_or_the_asker():
    risk = person("risk@acme.test", "Risk on-call", state="OR")
    other = person("risk2@acme.test", "Risk on-call", state="CA")
    me = person("ben@acme.test", "Store associate", state="CO")
    assert holder_in("Risk on-call", me, {}, [risk, other], {"OR"}) == risk
    assert holder_in("Risk on-call", me, {}, [risk, other], {"OR", "CA"}) is None  # two: no holder
    assert holder_in("Risk on-call", me, {}, [risk], {"CO"}) is None               # none there
    assert holder_in("Store associate", me, {}, [me], {"CO"}) is None             # never the asker


def test_a_chain_that_loops_or_breaks_stops():
    a = person("a@acme.test", "Lead", "b@acme.test")
    b = person("b@acme.test", "Lead", "a@acme.test")
    assert holder_in("Director", person("me@acme.test", "x", "a@acme.test"), {"a@acme.test": a, "b@acme.test": b}, [], set()) is None
    assert holder_in("Director", person("me@acme.test", "x", "gone@acme.test"), {}, [], set()) is None


def test_a_listing_keeps_short_text_and_a_manager_only_by_a_valid_email():
    got = about(" Store manager ", "Stores", "CO201", None, "CO", "US", "Dara@Acme.test")
    assert got == {"title": "Store manager", "department": "Stores", "office": "CO201", "city": None, "state": "CO",
                   "country": "US", "manager_email": "dara@acme.test"}
    assert about(manager="not an email")["manager_email"] is None and about(title="x" * 300)["title"] == "x" * 200


def test_on_the_retailer_everyone_but_the_tops_reports_to_someone_and_ben_up_to_dara():
    world = company.build()
    ben = world.person("ben")
    store_manager = company.manager_of(world, ben)
    assert store_manager.title == "Store manager" and store_manager.place == "CO201"
    assert company.manager_of(world, store_manager).name == "Dara Singh"
    assert company.manager_of(world, world.person("dara")).name == "Priya Shah"  # Max Weber has left
    tenant = company.entra_tenant(world)
    me = next(u for u in tenant["users"] if u["mail"] == ben.email)
    assert me["officeLocation"] == "CO201" and me["state"] == "CO" and me["manager"]["mail"] == store_manager.email
