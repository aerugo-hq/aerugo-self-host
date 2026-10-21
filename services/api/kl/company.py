"""The synthetic retailer (F9): a fictional company with known truth, on which every later item is proven.

Acme Outfitters sells outdoor apparel and gear: stores in several states, distribution centers, a contact center, and
the head office's teams. `build(seed, stores)` makes it, the same every time for the same seed and size:

- its places, teams, and people, each with a title, a team, and a place (and one person who has left);
- what they wrote: pages in Confluence, issues in Jira, and threads in Slack, each with who may read it, from that
  source's own permissions (space grants narrowed by page restrictions, project browsers or a security level, channel
  members); and, kept for capture (C1 and C3), a Teams channel's message and a meeting's transcript;
- the truth: the facts each piece states (current or superseded, with their scope), and questions asked by twelve
  people who sign in, each with the pages a right answer draws on, the pages the asker must never see, and the
  sensitive pages the source lets them read but no answer should use.

Text comes from templates, never a model, so the truth is exact. The seed changes who the generated people are; the
size changes how many stores there are. Everything the questions rely on is fixed.

The render functions turn the company into what each fake serves (`confluence_site`, `jira_site`, `slack_workspace`,
`entra_tenant`); kl.company_eval loads it into the stack and scores it. Nothing here touches a network or a database.

    python -m kl.company [--stores N] [--seed S]    # the truth, as JSON
"""
from __future__ import annotations

import hashlib
import html
import json
import random
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

COMPANY = "Acme Outfitters"
DOMAIN = "acme.test"
MIN_STORES = 3
EVERYONE = "everyone"
LOADED = ("confluence", "jira", "slack")  # what kl.company_eval loads; Teams and meetings wait for C1 and C3
SLACK_TEAM = "T0RETAIL"
TENANT = str(uuid.uuid5(uuid.NAMESPACE_URL, "https://acme.test/entra"))

TEAMS = (  # slug, name, who can find it: org (anyone in the org can search it, within their own permissions) or private
    ("company", COMPANY, "org"),
    ("stores", "Stores", "org"),
    ("distribution", "Distribution", "org"),
    ("contact-center", "Contact center", "org"),
    ("merchandising", "Merchandising and planning", "org"),
    ("ecommerce", "E-commerce", "org"),
    ("marketing", "Marketing", "org"),
    ("product-quality", "Product quality", "org"),
    ("accounting", "Accounting", "org"),
    ("people", "People", "private"),
    ("it-support", "IT help desk", "org"),
    ("edw", "Enterprise data warehouse", "org"),  # F10b: the data teams, whose estate kl.enterprise builds
    ("edp", "Enterprise data platform", "org"),
)

STORE_SITES = (  # the first three are fixed: people who sign in work there
    ("OR101", "Portland Pearl", "OR"), ("CO201", "Denver Union Station", "CO"), ("CA301", "San Francisco Market Street", "CA"),
    ("CO202", "Boulder Pearl Street", "CO"), ("WA111", "Seattle Capitol Hill", "WA"), ("CA302", "Sacramento Midtown", "CA"),
    ("UT211", "Salt Lake City Sugar House", "UT"), ("OR102", "Bend Old Mill", "OR"), ("WA112", "Spokane Riverfront", "WA"),
    ("VT401", "Burlington Church Street", "VT"), ("CA303", "San Diego North Park", "CA"), ("CO203", "Fort Collins Old Town", "CO"),
    ("UT212", "Park City Main Street", "UT"), ("ME402", "Portland Old Port", "ME"), ("NH403", "Portsmouth Market Square", "NH"),
)
DISTRICTS = {"OR": "Pacific", "WA": "Pacific", "CO": "Mountain", "UT": "Mountain", "CA": "California",
             "VT": "Northeast", "ME": "Northeast", "NH": "Northeast"}
OTHER_PLACES = (("DC-RNO", "Reno distribution center", "dc", "NV"), ("DC-SLC", "Salt Lake distribution center", "dc", "UT"),
                ("HQ", "Portland head office", "office", "OR"), ("CC-BOI", "Boise contact center", "office", "ID"))
KEY_SPOTS = ("in the key box in the manager's office, on hook 4", "in the front desk drawer, under the till tray",
             "in the safe, in the envelope marked BIKE", "on the hook behind the stockroom door",
             "in the fitting room desk, top drawer", "on the opening key holder's ring, with the blue tag")

# Time passing on the retailer, for F6's lifecycle check: the fact whose unit's review comes due, and a page that changes
# (a fact it holds, the page, and what in it changes); every unit citing that page goes stale with it, and every other
# unit stays as it is.
# C2a: the chat messages no answer may use, though Slack lets their channel's members read them: (thread, position)
SENSITIVE_MESSAGES = (("medical-thread", 0), ("hr-warning", 0))
LIFECYCLE_DUE = "dec-24-hours"
LIFECYCLE_CHANGED = ("weeks-of-supply", "merch-glossary", ("over the last 4 weeks", "over the last 6 weeks"))

# What the store people ask that no page answers, for F5a's gaps check: (id, a gap?, helped?, (asker, wording)...). A gap
# once three different people weren't answered; not when only two asked, nor when the answer helped them, and a question
# about one person's health is never kept at all.
GAP_QUESTIONS = (
    ("ladder-keys", True, False, (("ana", "Where are the spare ladder keys kept?"), ("ben", "Where do we keep the spare ladder keys?"),
                                  ("cora", "Where are spare ladder keys stored?"))),
    ("weekend-parking", False, False, (("ana", "Where can staff park on weekends?"), ("ben", "Where do staff park on weekends?"))),
    ("helped", False, True, (("ana", "When do stores open on 24 December?"), ("ben", "What time do stores open on 24 December?"),
                             ("cora", "When do the stores open on 24 December?"))),
    ("someones-health", False, False, (("ana", "Is Sam Rivera back from surgery?"), ("ben", "Is Sam Rivera back after his surgery?"),
                                       ("cora", "When is Sam Rivera back from surgery?"))),
)

# The people who sign in (kl.company_eval makes their accounts): username, name, title, team, place, leads people
PERSONAS = (
    ("ana", "Ana Ruiz", "Store associate", "stores", "OR101", False),
    ("ben", "Ben Okafor", "Store associate", "stores", "CO201", False),
    ("cora", "Cora Lin", "Store manager", "stores", "CA301", True),
    ("dara", "Dara Singh", "District manager, Mountain", "stores", "CO201", True),
    ("eli", "Eli Brooks", "Receiving supervisor", "distribution", "DC-RNO", True),
    ("fay", "Fay Morgan", "Contact center agent", "contact-center", "CC-BOI", False),
    ("gus", "Gus Hale", "Merchandise planner", "merchandising", "HQ", False),
    ("hana", "Hana Ito", "Staff accountant", "accounting", "HQ", False),
    ("ivy", "Ivy Chen", "People partner", "people", "HQ", True),
    ("jon", "Jon Reyes", "E-commerce engineer", "ecommerce", "HQ", False),
    ("kai", "Kai Moreno", "Product quality lead", "product-quality", "HQ", True),
    ("lea", "Lea Novak", "Brand marketing manager", "marketing", "HQ", False),
    ("nia", "Nia Kaur", "Data engineer", "edp", "HQ", False),
    ("otto", "Otto Brandt", "EDW developer", "edw", "HQ", False),
)
NAMED = (  # people the pages name, who don't sign in: name, title, team, place, leads people, still at the company
    ("Priya Shah", "Head of store operations", "stores", "HQ", True, True),
    ("Max Weber", "Head of store operations", "stores", "HQ", True, False),  # left in June 2026; Priya took over
    ("Rosa Kim", "Brand director", "marketing", "HQ", True, True),
    ("Lena Fischer", "Director of people", "people", "HQ", True, True),
    ("Omar Haddad", "Controller", "accounting", "HQ", True, True),
    ("Tess Ward", "Contact center supervisor", "contact-center", "CC-BOI", True, True),
    ("Sam Rivera", "Store associate", "stores", "CO201", False, True),
    ("Jordan Blake", "Store associate", "stores", "OR101", False, True),
    ("Ravi Menon", "Head of data", "edp", "HQ", True, True),
    ("Greta Lund", "EDW lead", "edw", "HQ", True, True),
)
HQ_STAFF = (  # generated, by team: (title, how many)
    ("stores", (("Store operations analyst", 1),)),
    ("merchandising", (("Merchandise planner", 2), ("Allocation analyst", 1))),
    ("accounting", (("Accounts payable specialist", 1),)),
    ("people", (("People coordinator", 1),)),
    ("ecommerce", (("E-commerce engineer", 2),)),
    ("product-quality", (("Quality engineer", 2),)),
    ("marketing", (("Designer", 1),)),
    ("it-support", (("Help desk technician", 3),)),
    ("edw", (("EDW developer", 2), ("BI developer", 1))),  # last, so everyone generated before keeps their name
    ("edp", (("Data engineer", 2), ("Analytics engineer", 1))),
)
FIRST = ("Avery", "Blake", "Camila", "Darius", "Elena", "Felix", "Grace", "Hugo", "Imani", "Jonah", "Keira", "Liam",
         "Maya", "Nadia", "Owen", "Paloma", "Quinn", "Rafael", "Sofia", "Theo", "Uma", "Victor", "Wren", "Xavier",
         "Yara", "Zane", "Amara", "Bruno", "Chloe", "Diego", "Esme", "Farah", "Gabriel", "Hazel", "Ines", "Jasper",
         "Kofi", "Leila", "Mateo", "Nina", "Oscar", "Rhea", "Silas", "Tara", "Vera", "Wade", "Ximena", "Yusuf")
LAST = ("Abbott", "Bauer", "Castillo", "Dubois", "Ellis", "Fujita", "Garcia", "Hansen", "Ibarra", "Jensen",
        "Kowalski", "Larsen", "Mendez", "Nakamura", "Olsen", "Pereira", "Quinlan", "Rossi", "Sato", "Torres", "Ueda",
        "Vargas", "Walsh", "Xu", "Yamada", "Zimmer", "Adeyemi", "Brennan", "Carvalho", "Dalton", "Eriksen", "Gallo",
        "Holm", "Iversen", "Jovanovic", "Keane", "Lindqvist", "Moreau", "Osei", "Park", "Reyes", "Svensson")


@dataclass(frozen=True)
class Place:
    code: str
    name: str
    kind: str   # store | dc | office
    state: str
    district: str = ""


@dataclass(frozen=True)
class Person:
    name: str
    email: str
    title: str
    team: str
    place: str
    leads: bool = False
    username: str = ""     # set for the people who sign in
    active: bool = True

    @property
    def atlassian(self) -> str:
        return "557058:rt" + hashlib.sha256(self.email.encode()).hexdigest()[:12]

    @property
    def slack(self) -> str:
        return "URT" + hashlib.sha256(self.email.encode()).hexdigest()[:8].upper()

    @property
    def entra(self) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://acme.test/people/{self.email}"))


@dataclass(frozen=True)
class Container:
    """A Confluence space, a Jira project, or a Slack channel (or, not loaded yet, a Teams channel or a meeting)."""
    source: str
    id: str
    name: str
    team: str                 # the team whose connection brings it in
    readers: tuple[str, ...]  # the groups that may read it (EVERYONE: the whole company)
    number: int = 0           # a Confluence space's id


@dataclass(frozen=True)
class Doc:
    name: str                 # the handle the facts and questions use
    key: str                  # its key in Aerugo
    source: str
    container: str
    title: str
    when: str                 # the day it was written or last changed
    text: str = ""            # a page's markup, or an issue's description
    messages: tuple[tuple[str, str, str], ...] = ()  # (author, when, text): a thread's messages, an issue's comments
    restricted: tuple[str, ...] = ()  # a page's read restriction, or an issue's security level: the groups it narrows to
    resolved: bool = False
    readers: frozenset[str] | None = None  # who may read it, by email (None: the whole company)


@dataclass(frozen=True)
class Fact:
    id: str
    kind: str                 # policy | procedure | contact | decision | definition | answer | lesson | fact
    text: str
    evidence: tuple[str, ...]
    scope: tuple[str, ...] = ()       # the places or roles it applies to; empty: everyone
    valid_until: str = ""             # a superseded fact's last day
    status: str = "current"           # current | superseded | sensitive | stale (a page still in use that says what's no longer so)
    contradicted_by: tuple[str, ...] = ()
    supersedes: str = ""              # the fact it replaced, on the same topic (F1 loads both as versions)
    contradicts: tuple[str, ...] = ()  # a stale fact: the current facts it says otherwise than (F3 must find each)
    role: str = ""                    # a contact's role, which F8 resolves to its current holder for each asker


@dataclass(frozen=True)
class Question:
    id: str
    asker: str                # a persona's username
    text: str
    kind: str
    expect: tuple[str, ...] = ()      # what a right answer draws on
    forbid: tuple[str, ...] = ()      # what the asker may never see
    withhold: tuple[str, ...] = ()    # sensitive: the source lets the asker read it, but no answer should use it
    needs: str = ""                   # a slice this question waits on (shown, not counted)
    facts: tuple[str, ...] = ()


@dataclass(frozen=True)
class World:
    seed: int
    stores: int
    places: tuple[Place, ...]
    people: tuple[Person, ...]
    groups: dict[str, frozenset[str]]
    containers: tuple[Container, ...]
    docs: tuple[Doc, ...]
    facts: tuple[Fact, ...]
    questions: tuple[Question, ...]
    _index: dict = field(default_factory=dict, compare=False, repr=False)

    def doc(self, name: str) -> Doc:
        if not self._index:
            self._index.update({d.name: d for d in self.docs})
        return self._index[name]

    def person(self, username: str) -> Person:
        return next(p for p in self.people if p.username == username)

    def container(self, source: str, cid: str) -> Container:
        return next(c for c in self.containers if c.source == source and c.id == cid)

    def visible(self, username: str) -> set[str]:
        """The loaded documents a persona may open: the source lets them read it, and its team is one they can reach."""
        me = self.person(username)
        private = {slug for slug, _, found in TEAMS if found == "private"} - {me.team}
        return {d.name for d in self.docs if d.source in LOADED
                and self.container(d.source, d.container).team not in private
                and (d.readers is None or me.email in d.readers)}


# ------------------------------------------------------------------ the company
def build(seed: int = 7, stores: int = 6) -> World:
    if stores < MIN_STORES:
        raise ValueError(f"a company needs at least {MIN_STORES} stores: the people who sign in work in the first three")
    rng = random.Random(seed)
    places = _places(stores)
    people = _people(rng, places)
    groups = _groups(people, places)
    containers = _containers(places)
    docs = _with_readers(_docs(people, places), containers, groups)
    return World(seed, stores, places, people, groups, containers, docs, _facts(places), _questions())


def _places(stores: int) -> tuple[Place, ...]:
    out = []
    for i in range(stores):
        code, name, state = STORE_SITES[i % len(STORE_SITES)]
        if i >= len(STORE_SITES):  # a bigger company: more stores in the same cities
            code, name = f"{code[:2]}{500 + i}", f"{name} {i // len(STORE_SITES) + 1}"
        out.append(Place(code, name, "store", state, DISTRICTS[state]))
    return tuple(out) + tuple(Place(code, name, kind, state) for code, name, kind, state in OTHER_PLACES)


def _email(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z.]", "", name.lower().replace(" ", "."))
    email, n = f"{base}@{DOMAIN}", 2
    while email in taken:
        email, n = f"{base}{n}@{DOMAIN}", n + 1
    taken.add(email)
    return email


def _people(rng: random.Random, places: tuple[Place, ...]) -> tuple[Person, ...]:
    taken_names = {name for _, name, *_ in PERSONAS} | {name for name, *_ in NAMED}
    emails: set[str] = set()
    people = [Person(name, _email(name, emails), title, team, place, leads, username)
              for username, name, title, team, place, leads in PERSONAS]
    people += [Person(name, _email(name, emails), title, team, place, leads, active=active)
               for name, title, team, place, leads, active in NAMED]

    def generated(title: str, team: str, place: str, leads: bool = False) -> None:
        while True:
            name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            if name not in taken_names:
                taken_names.add(name)
                people.append(Person(name, _email(name, emails), title, team, place, leads))
                return

    stores = [p for p in places if p.kind == "store"]
    for district in dict.fromkeys(p.district for p in stores):  # in the order the stores list them
        if district != "Mountain":  # Mountain's is Dara Singh
            generated(f"District manager, {district}", "stores", next(p.code for p in stores if p.district == district), True)
    for store in stores:
        if store.code != "CA301":  # Cora Lin manages San Francisco
            generated("Store manager", "stores", store.code, True)
        generated("Assistant store manager", "stores", store.code, True)
        for _ in range(5):
            generated("Store associate", "stores", store.code)
    generated("Receiving supervisor", "distribution", "DC-SLC", True)
    for dc in ("DC-RNO", "DC-SLC"):
        for _ in range(3):
            generated("Warehouse associate", "distribution", dc)
    for _ in range(4):
        generated("Contact center agent", "contact-center", "CC-BOI")
    for team, titles in HQ_STAFF:
        for title, count in titles:
            for _ in range(count):
                generated(title, team, "HQ")
    return tuple(people)


def _groups(people: tuple[Person, ...], places: tuple[Place, ...]) -> dict[str, frozenset[str]]:
    """Who is in each group the sources' permissions name. Someone who has left is in none."""
    here = [p for p in people if p.active]
    by = lambda test: frozenset(p.email for p in here if test(p))
    dms = {p.email: p.title.split(", ")[1] for p in here if p.title.startswith("District manager")}
    groups = {EVERYONE: by(lambda _: True)}
    groups |= {f"team-{slug}": by(lambda p, slug=slug: p.team == slug) for slug, _, _ in TEAMS}
    groups["store-leaders"] = by(lambda p: p.team == "stores" and p.leads)
    groups["district-leaders"] = by(lambda p: p.email in dms or p.title in ("Store manager", "Head of store operations"))
    groups["quality-leads"] = by(lambda p: p.title == "Product quality lead")
    groups["people-leads"] = by(lambda p: p.team == "people" and p.leads)
    for store in (p for p in places if p.kind == "store"):
        groups[f"store-{store.code.lower()}"] = by(lambda p, s=store: (p.team == "stores" and p.place == s.code)
                                                   or dms.get(p.email) == s.district)
    return groups


def _containers(places: tuple[Place, ...]) -> tuple[Container, ...]:
    spaces = (  # key, name, team, readers
        ("HANDBOOK", "Acme Outfitters handbook", "company", (EVERYONE,)),
        ("STOREOPS", "Store operations", "stores", ("team-stores", "team-it-support")),
        ("CC", "Contact center", "contact-center", ("team-contact-center",)),
        ("DC", "Distribution", "distribution", ("team-distribution",)),
        ("MERCH", "Merchandising and planning", "merchandising", ("team-merchandising", "district-leaders")),
        ("ECOM", "E-commerce", "ecommerce", ("team-ecommerce", "team-it-support")),
        ("MKT", "Marketing", "marketing", ("team-marketing",)),
        ("QUAL", "Product quality", "product-quality", ("team-product-quality", "store-leaders", "team-contact-center", "team-ecommerce")),
        ("ACCT", "Accounting", "accounting", ("team-accounting",)),
        ("PEOPLE", "People team", "people", ("team-people",)),
        ("IT", "IT help desk", "it-support", ("team-it-support", "store-leaders")),
        ("DATA", "Data platform", "edp", ("team-edp", "team-edw")),
    )
    projects = (
        ("STORE", "Store incidents", "stores", ("store-leaders", "team-it-support")),
        ("QUAL", "Product quality", "product-quality", ("team-product-quality", "store-leaders", "team-contact-center")),
        ("ECOM", "E-commerce", "ecommerce", ("team-ecommerce", "team-it-support")),
        ("DC", "Distribution", "distribution", ("team-distribution",)),
        ("DATA", "Data platform", "edp", ("team-edp", "team-edw")),
    )
    channels = [
        ("CRTALL", "stores-all", "company", (EVERYONE,)),
        ("CRTHELP", "it-helpdesk", "it-support", (EVERYONE,)),
        ("CRTDLEAD", "district-leaders", "stores", ("district-leaders",)),
        ("CRTDCOPS", "dc-ops", "distribution", ("team-distribution",)),
        ("CRTCC", "contact-center", "contact-center", ("team-contact-center",)),
        ("CRTMERCH", "merch-planning", "merchandising", ("team-merchandising",)),
        ("CRTECOM", "ecom-incidents", "ecommerce", ("team-ecommerce", "team-it-support")),
        ("CRTQUAL", "quality", "product-quality", ("team-product-quality",)),
        ("CRTACCT", "accounting-close", "accounting", ("team-accounting",)),
        ("CRTPPL", "people-team", "people", ("team-people",)),
    ] + [(f"CRTS{p.code}", f"store-{p.code.lower()}", "stores", (f"store-{p.code.lower()}",)) for p in places if p.kind == "store"]
    return (tuple(Container("confluence", key, name, team, readers, 7101 + i) for i, (key, name, team, readers) in enumerate(spaces))
            + tuple(Container("jira", key, name, team, readers) for key, name, team, readers in projects)
            + tuple(Container("slack", cid, name, team, readers) for cid, name, team, readers in channels)
            + (Container("teams", "reno-dc-handover", "Reno DC / Shift handover", "distribution", ("team-distribution",)),
               Container("meeting", "recall-standups", "TrailLite 2 recall stand-ups", "product-quality", ("team-product-quality",))))


def _ts(when: str, n: int = 0) -> str:
    """A Slack timestamp for 'YYYY-MM-DD HH:MM', n minutes later."""
    at = datetime.fromisoformat(when).replace(tzinfo=timezone.utc).timestamp() + 60 * n
    return f"{int(at)}.000100"


def _docs(people: tuple[Person, ...], places: tuple[Place, ...]) -> list[Doc]:
    """What the company's people wrote. Every number, name, and step here is the truth the questions are scored on."""
    def who(place: str = "", title: str = "", team: str = "") -> str:
        """The first person who fits, preferring someone who doesn't sign in, so the personas' questions aren't their own words."""
        fits = [p for p in people if (not place or p.place == place) and (not title or p.title.startswith(title))
                and (not team or p.team == team) and p.active]
        return next((p.name for p in fits if not p.username), fits[0].name)

    stores = [p for p in places if p.kind == "store"]
    dm = {d: who(title=f"District manager, {d}") for d in dict.fromkeys(p.district for p in stores)}
    ecom = [p.name for p in people if p.team == "ecommerce" and not p.username]
    docs: list[Doc] = []
    page_id = iter(range(71001, 79999))

    def page(name, space, title, when, markup, restricted=(), pid=None):
        docs.append(Doc(name, f"confluence-{pid or next(page_id)}", "confluence", space, title, when, markup.strip(), restricted=restricted))

    def issue(name, key, title, when, description, comments=(), restricted=(), resolved=False):
        docs.append(Doc(name, f"jira-{key}", "jira", key.split("-")[0], title, when, description.strip(),
                        tuple(comments), restricted, resolved))

    def thread(name, channel, title, when, messages, source="slack"):
        key = f"slack-{channel}-{_ts(when)}" if source == "slack" else f"{source}-{channel}-{when[:10]}"
        docs.append(Doc(name, key, source, channel, title, when[:10],
                        messages=tuple((author, f"{when[:10]}T{when[11:]}", text) for author, text in messages)))

    # The handbook: the whole company reads it
    page("returns-2026", "HANDBOOK", "Returns policy (2026)", "2026-02-15", """
Effective 1 March 2026. Owner: store operations (Priya Shah, head of store operations).
## The return window
Customers have 45 days from purchase to return an item, with a receipt or an order we can look up. Refunds go back to the original payment.
## Without a receipt
Without a receipt or an order we can find, we give store credit at the item's lowest price in the last 45 days, up to $50. Above $50, a store leader (the manager on duty or above) must approve it.
## What can't be returned
Items marked final sale (clearance prices ending in .97) can't be returned. Worn footwear comes back only for a defect.
## What changed
The window is 45 days, down from 60. Store credit without a receipt is up to $50, down from $75. Questions about this policy go to store operations.""")
    page("returns-2025", "HANDBOOK", "Returns policy", "2025-01-20", """
Owner: store operations (Max Weber).
## The return window
Customers have 60 days from purchase to return an item with a receipt. Refunds go back to the original payment.
## Without a receipt
Without a receipt, we give store credit at the current price, up to $75.""")
    page("sick-co", "HANDBOOK", "Paid sick leave: Colorado stores", "2026-01-05", """
Store employees in Colorado earn 1 hour of paid sick leave for every 30 hours worked, up to 48 hours a year. Up to 48 unused hours carry over to next year.
Ask your store leader, or the People team at people@acme.test.""")
    page("sick-ca", "HANDBOOK", "Paid sick leave: California stores", "2026-01-05", """
Store employees in California get 40 hours (5 days) of paid sick leave at the start of each year, or on their first day. Unused hours don't carry over.
Ask your store leader, or the People team at people@acme.test.""")
    page("sick-or", "HANDBOOK", "Paid sick leave: Oregon stores", "2026-01-05", """
Store employees in Oregon earn 1 hour of paid sick leave for every 30 hours worked, up to 40 hours a year. Up to 40 unused hours carry over.
Ask your store leader, or the People team at people@acme.test.""")
    page("sick-other", "HANDBOOK", "Paid sick leave: stores in other states", "2026-01-05", """
Store employees in states without their own page earn 1 hour of paid sick leave for every 40 hours worked, up to 40 hours a year.""")
    page("first-week", "HANDBOOK", "Your first week as a store associate", "2026-03-01", """
Welcome to Acme Outfitters. Your store leader plans your first week with you; this is what it covers.
## Day by day
1. Day 1: a welcome, a tour, and register training (course POS-101) with your store's trainer.
2. Day 2: the safety walk, and what to do when a customer is hurt.
3. Day 3: returns, and the returns policy.
4. Day 4: fitting rooms, and what to do if you see shoplifting.
5. Day 5: a shift shadowing a key holder.
## Courses to finish this week
- POS-101: the register
- SAFE-110: safety basics
- RET-2026: returns in 2026
Your store leader signs off your first week on Friday.""")
    page("who-to-call", "HANDBOOK", "Who to call", "2026-04-01", """
| Need | Who | How |
| Someone was hurt | Risk on-call | 555-0170, any time |
| Registers or store systems | IT help desk | 555-0142, option 2 for registers |
| Leaks, doors, heating | Facilities | 555-0155 |
| Pay, leave, or a workplace concern | People team | people@acme.test |
| A product recall | Product quality | quality@acme.test |""")
    page("shoplifting-basics", "HANDBOOK", "Safety basics: if you see shoplifting", "2025-09-01", """
Your safety comes first.
- Never confront, block, or follow someone, inside or outside the store.
- Note what you saw: the time, what was taken, and what the person looks like.
- Tell the manager on duty right away. They follow the loss prevention steps.""")
    page("holiday-hours", "HANDBOOK", "Holidays and store hours, 2026", "2026-09-15", """
| Day | Stores |
| Thanksgiving Day, 26 November | Closed |
| Black Friday, 27 November | 6:00 to 22:00 |
| 24 December | 9:00 to 17:00 |
| 25 December | Closed |
| 31 December | 9:00 to 18:00 |""")

    # Store operations
    page("injury-procedure", "STOREOPS", "Customer injury: slip, trip, or fall", "2026-02-01", """
What to do when a customer is hurt in the store. Owner: store operations.
## Steps
1. Associate: make the area safe, stay with the customer, and call the manager on duty. Don't move them if they say they're hurt.
2. Manager on duty: offer first aid from the kit at the service desk. Call 911 if they ask, if they hit their head, or if they can't get up.
3. Manager on duty: photograph the area before it's cleaned, and take the names of anyone who saw it.
4. Manager on duty: file incident report IR-7 in the Risk portal within 24 hours.
5. Manager on duty: tell the district manager the same day, and call Risk on-call (555-0170) within 2 hours if the customer went to hospital.
## Don't
- Don't admit fault, offer payment, or talk about insurance.
## California stores
California stores also record the incident in the store's safety log, the binder at the manager's desk, the same day.""")
    page("districts", "STOREOPS", "Districts and their managers", "2026-07-01",
         "| District | States | District manager |\n" + "\n".join(
             f"| {d} | {', '.join(sorted({p.state for p in stores if p.district == d}))} | {dm[d]} |" for d in dm))
    page("lp-leaders", "STOREOPS", "Loss prevention: theft response for store leaders", "2026-01-15", """
For store leaders only.
1. Don't detain anyone, and don't let associates follow them.
2. Log the incident in the LP portal within 1 hour: the time, the items, their value, and a description.
3. If the value is over $500, call your district's loss prevention partner the same day.
4. Video is kept 30 days: request a copy in the LP portal before then.""", restricted=("store-leaders",))
    page("recall-notice", "STOREOPS", "Recall notice: TrailLite 2 tent", "2026-05-12", """
Product quality has recalled the TrailLite 2 tent (SKU 40211): its poles can crack.
## What stores do
1. Pull every TrailLite 2 tent from the floor and the stockroom by the end of 13 May.
2. Tag them RECALL, DO NOT SELL, and keep them in the back room until the vendor collects them.
3. Refund any customer who brings one back, at the full price paid, with or without a receipt.
4. Point customers to acme.test/recall for details.
The registers block the sale of SKU 40211 from 12 May.""")
    page("opening-closing", "STOREOPS", "Opening and closing checklist", "2026-09-05", """
## Opening
1. Disarm the alarm and walk the floor.
2. Put the entrance mats down before the doors open.
3. Count the till floats: $200 each.
## Closing
1. Count and drop the tills.
2. Lock the fitting rooms and the stockroom.
3. Set the alarm. Two people leave together.""")

    # The contact center
    page("cc-escalation", "CC", "Escalation rules", "2026-03-05", """
Escalate a call to a supervisor when:
- the customer asks for a refund over $200;
- the customer says they or someone else was hurt by a product (also open a Risk case);
- the customer mentions a lawyer or legal action;
- it's the customer's third contact about the same problem.
The supervisor on duty is named in the queue's header.""")
    page("cc-recall-script", "CC", "Script: TrailLite 2 recall", "2026-05-12", """
Use this for any call about the TrailLite 2 tent recall.
1. Thank them, and tell them the TrailLite 2's poles can crack, so we've recalled it.
2. Ask them to stop using the tent.
3. Offer a full refund, with no receipt needed, at any store or by mail with a prepaid label.
4. Tell them replacement poles are coming, and we'll email them when they're available.
5. If anyone was hurt, escalate to a supervisor and open a Risk case.""")
    page("cc-weekly-changes", "CC", "This week's changes: week of 2 March 2026", "2026-03-02", """
- Returns: from 1 March the window is 45 days, not 60, and store credit without a receipt is up to $50, not $75.
- Holiday store hours for 2026 are in the handbook.""")
    # A stale page (F3): the 2025 script, never updated, still saying 60 days; consolidation must find it against the
    # current policy. A fixed id, so the store pages' count never moves it.
    page("cc-returns-script-2025", "CC", "Script: returns", "2025-06-02", """
Owner: Tess Ward, contact center supervisor.
## Returns
Customers have 60 days to return an item with a receipt. Offer an exchange before a refund.""", pid=79003)

    # Distribution
    page("late-shipments", "DC", "Late inbound vendor shipments", "2025-11-10", """
When a vendor's shipment misses its dock appointment:
1. Call the carrier first: appointments are often moved without telling us.
2. More than 48 hours late: the receiving supervisor emails the buyer in merchandising and opens a DC ticket in Jira.
3. More than 5 days late: the buyer decides whether to cancel the purchase order.""")
    page("reno-receiving", "DC", "Reno DC: receiving appointments", "2026-02-20", """
Docks 1 to 4 take inbound shipments from 6:00 to 14:00, Monday to Friday. Book appointments in the dock scheduler at least 24 hours ahead.""")

    # Merchandising and planning
    page("merch-glossary", "MERCH", "Glossary: sell-through and other metrics", "2026-01-12", """
How merchandising and planning use these terms. Owner: merch analytics.
| Term | Meaning |
| Sell-through | Units sold divided by units received, for the period, by style and color. The weekly sales report (merch_weekly_sales) calculates it every Monday. |
| Weeks of supply | Units on hand divided by the average weekly units sold over the last 4 weeks. |
| Full-price sell-through | Sell-through counting only units sold at full price. |""")
    page("allocation-rules", "MERCH", "Allocation rules", "2026-02-02", """
How new stock is split across stores. Owner: the allocation team, in merchandising.
1. Stores are ranked by their sell-through in the category over the last 8 weeks.
2. The top quarter of stores get 30 percent more units than the base allocation.
3. The bottom quarter get 20 percent fewer.
4. A store open less than 12 weeks gets the base allocation.""")

    # E-commerce
    page("checkout-runbook", "ECOM", "Runbook: checkout errors", "2026-06-01", """
1. Check the payment provider's status page (status.paynimbus.test).
2. Open the checkout_errors dashboard: note when the errors started and which step fails.
3. If they started with a deploy, roll it back.
4. If the provider answers with 5xx errors, page them through the vendor console.
5. Post updates in #ecom-incidents every 30 minutes until it's resolved.""")
    page("ecom-oncall", "ECOM", "E-commerce on-call rotation", "2026-10-12", f"""
| Week | On call |
| 12 October 2026 | Jon Reyes |
| 19 October 2026 | {ecom[0]} |
| 26 October 2026 | {ecom[1]} |""")

    # Marketing
    page("brand-guidelines", "MKT", "Brand guidelines", "2026-01-20", """
## Voice
Plain and practical, like a friend who's been on the trail. No exclamation marks in headlines.
## Logo
Keep clear space around the logo at least the height of its A. Never stretch or recolor it.
## Colors
Pine (#2F4F3A), Granite (#5A5F63), and Ember (#C8553D).
## Approvals
Campaign copy is approved by the brand director, Rosa Kim. Price claims also go to legal review.""")
    page("holiday-2025-results", "MKT", "Holiday 2025 campaign: results", "2026-01-15", """
Sales were up 12 percent on holiday 2024. The email open rate was 31 percent, and the best email was "Gear for the long night".
## Next time
- Start the gift guides by 1 November: in 2025 they went out on 14 November, and the first week was slow.""")

    # Product quality
    page("recall-playbook", "QUAL", "Recall playbook", "2025-08-01", """
Owner: the product quality lead (Kai Moreno). The recall team: product quality, store operations, e-commerce, the contact center, legal, and communications.
## The order of steps
1. Stop the sale: block the SKU at the registers and online within 4 hours of the decision.
2. Tell the stores: publish a recall notice in Store operations and post it in #stores-all.
3. Delist it online: e-commerce opens a delisting ticket.
4. Tell customers: email everyone who bought it, and give the contact center a script.
5. Notify the regulator within 24 hours of the decision: the product quality lead, with legal.
6. Hold a daily stand-up until the recall is closed.
## Lessons from past recalls
In the 2024 headlamp recall, stores heard a day late because the notice went out only by email. Since then a recall notice is always published in Store operations and posted in #stores-all.""")
    page("supplier-audit", "QUAL", "Supplier audit: Northpeak, 2026", "2026-06-20", """
Northpeak's pole supplier changed the alloy in January 2026 without telling us. Corrective action: Northpeak tests every new lot before it ships.""",
         restricted=("team-product-quality",))

    # Accounting
    page("close-checklist", "ACCT", "Month-end close checklist", "2026-02-01", """
| Day | Task | Owner |
| 1 | Inventory accrual, from the distribution centers' count file | Hana Ito |
| 2 | Accounts payable cutoff | Accounts payable |
| 3 | Revenue reconciliation, stores and e-commerce | Hana Ito |
| 4 | Review with the controller | Omar Haddad |
The close is done in Ledgerline. Distribution sends the count file by noon on day 1.""")

    # People
    page("pay-ranges", "PEOPLE", "Pay ranges, 2026", "2026-01-02", """
| Role | Range |
| Store associate | $17.50 to $21.00 an hour |
| Assistant store manager | $22.00 to $27.00 an hour |
| Store manager | $68,000 to $82,000 a year |""", restricted=("people-leads",))
    page("leave-requests", "PEOPLE", "How we handle leave requests", "2025-10-01", """
Leave requests come through the People portal. Managers approve or decline within 3 working days; the People team steps in for anything longer than two weeks.""")

    # IT help desk
    page("pos-down", "IT", "Registers down: what to do", "2026-03-15", """
1. Switch each register to offline mode: hold the Mode key and press F9.
2. Offline, cards up to $250 and cash still work. Gift cards don't.
3. Call the IT help desk on 555-0142, option 2.
4. When the network is back, offline sales upload by themselves within 15 minutes.""")

    # Each store's own page, last, so the pages above keep their ids however many stores there are
    for store in stores:
        page(f"store-page-{store.code}", "STOREOPS", f"Store {store.code}: {store.name}", "2026-06-01", f"""
Manager: {who(place=store.code, title='Store manager')}. Assistant manager: {who(place=store.code, title='Assistant store manager')}.
District: {store.district} ({dm[store.district]}).
Opening hours: 10:00 to 20:00, Sundays 11:00 to 18:00. Key holders: the manager and the assistant manager.""")

    # Jira
    issue("STORE-101", "STORE-101", "Customer fall at the Denver Union Station entrance", "2026-09-04", """
A customer slipped on the wet floor at the entrance at 9:40 on 3 September 2026. The manager on duty followed the injury procedure and filed IR-7. The customer didn't go to hospital.""",
          (("Priya Shah", "2026-09-04T10:00", "The entrance mat wasn't down after the floor was cleaned."),
           ("Priya Shah", "2026-09-04T15:00", "Decision: entrance mats go down before the doors open. It's now on the opening checklist.")),
          resolved=True)
    issue("STORE-102", "STORE-102", "Registers offline at Denver Union Station", "2026-10-02", """
Every register lost the network at 14:10 on 2 October 2026. Offline mode worked; two gift card sales were turned away.""",
          ((who(team="it-support"), "2026-10-02T17:00", "The store's router failed and was replaced the same day. Back online at 16:30."),),
          resolved=True)
    issue("QUAL-7", "QUAL-7", "TrailLite 2 tent: poles cracking", "2026-05-08", """
14 customers have reported cracked poles on the TrailLite 2 since April, all from lots made after January 2026.""",
          (("Kai Moreno", "2026-05-08T16:00", "Northpeak has the samples. Recommending a recall."),))
    issue("QUAL-8", "QUAL-8", "Recall the TrailLite 2 (SKU 40211)", "2026-05-11", """
The recall decision for the TrailLite 2 tent.""",
          (("Kai Moreno", "2026-05-11T09:00", "Decision: we will recall every TrailLite 2 tent (SKU 40211) sold since 1 February 2026. Stores stop selling it on 12 May."),),
          resolved=True)
    issue("QUAL-9", "QUAL-9", "Regulator notification: TrailLite 2", "2026-05-12", """
Notified the Consumer Product Safety Commission at 9:15 on 12 May 2026, report 26-1180. Legal reviewed it first.""",
          restricted=("quality-leads",), resolved=True)
    issue("ECOM-210", "ECOM-210", "Delist the TrailLite 2 (SKU 40211)", "2026-05-11", """
Removed from the website and the app at 16:20 on 11 May 2026.""", resolved=True)
    issue("ECOM-233", "ECOM-233", "Checkout 502s during the Black Friday flash sale", "2025-11-29", """
From 10:02 to 10:41 on 28 November 2025, about 1 in 5 checkouts failed with 502 errors.""",
          ((ecom[0], "2025-11-29T12:00", "Cause: the checkout service ran out of connections to the payment provider. "
                                          "Lesson: we raised the connection pool, and we now load test before every flash sale."),),
          resolved=True)
    issue("ECOM-260", "ECOM-260", "Upgrade the payment SDK to 5.2", "2026-10-14", """
Deployed to production at 11:00 on 14 October 2026.""", resolved=True)
    issue("DC-55", "DC-55", "Northpeak inbound late: PO 88412", "2025-09-18", """
Northpeak's shipment for PO 88412 missed its 15 September 2025 appointment.""",
          (("Eli Brooks", "2025-09-18T09:00", "The carrier had moved the appointment to 17 September without telling us. "
                                              "Lesson: call the carrier before escalating to the buyer."),),
          resolved=True)

    # The data teams (F10b): pages with fixed ids, so the store pages' count never moves them. Every table, job, and
    # bucket they name is in kl.enterprise's estate (its MENTIONS), and its tests hold the two to each other.
    page("edw-to-edp", "DATA", "EDW to EDP migration", "2026-09-01", """
Owner: Ravi Menon, head of data. The enterprise data warehouse (EDW, on Teradata) moves to the enterprise data platform (EDP, on BigQuery) in three waves, and the EDW retires at the end of 2027.
## How a table moves
1. The EDW exports it each night to gs://acme-edw-exports, from the EDW_EXPORTS folder in Control-M.
2. Composer loads the export into acme-edp.raw.
3. The edp-dbt project builds its staging model and its marts.
4. Both run side by side for 30 days, and the daily totals must match within 0.1 percent before the EDW table retires.
## The waves
| Wave | Tables | Starts |
| 1, sales | SALES_TXN, ITEM, STORE, and the DAILY_SALES report | 15 September 2026 |
| 2, inventory | INV_DAILY, RECEIPTS, and the INV_POSITION report | 1 December 2026 |
| 3, finance | GL_BALANCES and the GL_SUMMARY report | 1 March 2027 |
## Not migrated
LEGACY_RETURNS and PROMO_LIFT_2019 aren't migrated: nobody has read them in a year. They retire with the EDW.""", pid=79001)
    page("inv-daily-runbook", "DATA", "Runbook: INV_DAILY is late", "2026-07-30", """
Owner: Greta Lund, EDW lead. LOAD_INV_DAILY loads yesterday's inventory from the WMS extract into EDW.INV_DAILY at 02:30, after LOAD_STG_WMS_INV. INV_DAILY feeds the INV_POSITION report, which merchandising's allocation report reads at 06:00.
## When it's late
1. In Control-M, check LOAD_STG_WMS_INV in the EDW_DAILY folder. If it's waiting, the WMS extract hasn't arrived: call the distribution systems on-call, 555-0188.
2. When the file is in, rerun LOAD_STG_WMS_INV, then LOAD_INV_DAILY.
3. If it won't finish by 05:30, tell merchandising in #merch-planning that the allocation report will be late.""", pid=79002)
    issue("DATA-12", "DATA-12", "Wave 1: move SALES_TXN, ITEM, and STORE to the EDP", "2026-10-10", """
Export SALES_TXN, ITEM, and STORE each night (EXPORT_SALES_TXN, EXPORT_ITEM, EXPORT_STORE), load them with Composer, and build fct_sales, dim_item, dim_store, and daily_sales in edp-dbt. The dual run started on 15 September 2026.""",
          (("Nia Kaur", "2026-10-10T16:00", "Dual run, day 25: the totals have matched within 0.04 percent every day."),))
    issue("DATA-31", "DATA-31", "INV_DAILY late: the WMS extract arrived at 04:10", "2026-07-22", """
LOAD_STG_WMS_INV waited for the WMS extract until 04:10, so LOAD_INV_DAILY finished at 05:50 and the allocation report was stale until 07:15.""",
          (("Greta Lund", "2026-07-22T11:00", "Lesson: the WMS extract now runs at 01:00, an hour earlier, and we warn merchandising by 05:30."),),
          resolved=True)

    # Slack
    associate = lambda code: who(place=code, title="Store associate")
    thread("returns-reminder-2025", "CRTALL", "Returns reminder", "2025-04-14 08:30", (
        ("Max Weber", "Reminder for everyone on the floor: customers have 60 days to return an item with a receipt."),
        (who(title="Store manager"), "Thanks, sharing it at today's huddle.")))
    thread("recall-announce", "CRTALL", "TrailLite 2 recall", "2026-05-12 07:45", (
        ("Priya Shah", "Recall: pull every TrailLite 2 tent today. Details are in Store operations, Recall notice: TrailLite 2 tent."),
        ("Cora Lin", "San Francisco's are pulled.")))
    thread("helpdesk-pos", "CRTHELP", "Registers froze", "2026-06-03 11:20", (
        (associate("CO201"), "All our registers froze. What do we do?"),
        (who(team="it-support"), "Put them in offline mode: hold Mode and press F9. Cards up to $250 and cash still work. "
                                 "Then call us on 555-0142, option 2.")))
    thread("seasonal-hiring", "CRTDLEAD", "Seasonal hiring", "2026-09-22 16:00", (
        ("Priya Shah", "Seasonal hiring: two seasonal associates per store, hired by 1 November."),
        ("Dara Singh", "Mountain is on track.")))
    thread("dc-northpeak", "CRTDCOPS", "Northpeak late", "2025-09-17 08:10", (
        ("Eli Brooks", "Northpeak PO 88412 still isn't here, two days late. Escalating to the buyer."),
        (who(title="Warehouse associate", place="DC-RNO"), "Before you do, call the carrier. Last time they'd moved the appointment without telling us."),
        ("Eli Brooks", "You're right: it's rescheduled for the 17th.")))
    thread("cc-injury", "CRTCC", "Injury and returns", "2026-04-02 13:05", (
        (who(title="Contact center agent"), "If a caller says the product hurt them, do I process the return first?"),
        ("Tess Ward", "No. Escalate to a supervisor first and open a Risk case. The return can wait.")))
    thread("merch-sellthrough-2025", "CRTMERCH", "Sell-through", "2025-06-10 10:15", (
        (who(title="Merchandise planner"), "Quick check: sell-through is units sold over units on hand, right?"),
        (who(title="Allocation analyst"), "Yes, that's how the store review deck does it.")))
    thread("ecom-sdk", "CRTECOM", "Checkout errors on Safari", "2026-10-15 09:40", (
        ("Jon Reyes", "Checkout errors are up since this morning: card payments on Safari fail at the payment step."),
        (ecom[0], "It started after yesterday's payment SDK 5.2 deploy. Fixed with the provider's config flag, no rollback.")))
    thread("quality-recall", "CRTQUAL", "TrailLite 2 recall", "2026-05-11 09:30", (
        ("Kai Moreno", "We're recalling the TrailLite 2. Delisting is ECOM-210; the store notice goes out tomorrow."),
        (who(title="Quality engineer"), "I'll draft the customer email today.")))
    thread("close-september", "CRTACCT", "September close", "2026-10-06 17:20", (
        ("Hana Ito", "September close: the inventory accrual was a day late because the DC count file arrived on day 3."),
        ("Omar Haddad", "I've asked distribution to send it by noon on day 1 from now on.")))
    thread("hr-warning", "CRTPPL", "Attendance", "2026-08-14 15:00", (
        ("Lena Fischer", "Jordan Blake got a final written warning for attendance today. Keep this in this channel."),
        ("Ivy Chen", "Noted.")))
    for i, store in enumerate(stores):
        thread(f"key-thread-{store.code}", f"CRTS{store.code}", "Bike rack key", f"2026-08-{10 + i % 18:02d} 09:15", (
            (associate(store.code), "Where's the spare key for the bike rack lock?"),
            (who(place=store.code, title="Store manager"), f"It's {KEY_SPOTS[i % len(KEY_SPOTS)]}.")))
    thread("medical-thread", "CRTSCO201", "Covering shifts", "2026-10-09 18:30", (
        (who(place="CO201", title="Assistant store manager"), "Sam Rivera is having surgery on Tuesday, so I'm covering Sam's shifts all next week."),
        ("Dara Singh", "Thanks. Wishing Sam a quick recovery.")))

    # Not loaded yet: what C1 (Teams) and C3 (meetings) will bring in
    thread("teams-forklift", "reno-dc-handover", "Shift handover", "2026-10-13 14:00", (
        ("Eli Brooks", "Forklift 3 is out of service until the hydraulic part arrives on Thursday. Use forklift 5 for dock 2."),),
        source="teams")
    thread("meeting-recall-standup", "recall-standups", "TrailLite 2 recall stand-up", "2026-05-14 09:00", (
        ("Kai Moreno", "Northpeak confirmed the replacement poles ship on 20 May."),
        ("Priya Shah", "Then stores should have them by 27 May."),
        ("Tess Ward", "We'll update the contact center script once they're in.")), source="meeting")
    return docs


def _with_readers(docs: list[Doc], containers: tuple[Container, ...], groups: dict[str, frozenset[str]]) -> tuple[Doc, ...]:
    """Who may read each piece, as its source decides: a space's or channel's readers, narrowed by a page's restriction;
    a project's browsers, or an issue's security level instead."""
    holder = {(c.source, c.id): c for c in containers}
    members = lambda names: frozenset().union(*(groups[g] for g in names))
    out = []
    for d in docs:
        allowed = holder[(d.source, d.container)].readers
        if d.source == "jira" and d.restricted:
            readers = members(d.restricted)
        else:
            readers = None if EVERYONE in allowed else members(allowed)
            if d.restricted:
                readers = (readers if readers is not None else groups[EVERYONE]) & members(d.restricted)
        out.append(Doc(**{**d.__dict__, "readers": readers}))
    return tuple(out)


def _facts(places: tuple[Place, ...]) -> tuple[Fact, ...]:
    stores = [p for p in places if p.kind == "store"]
    return (
        Fact("returns-window", "policy", "Customers have 45 days to return an item with a receipt or an order that can be looked up, from 1 March 2026.",
             ("returns-2026", "cc-weekly-changes"), contradicted_by=("returns-2025", "returns-reminder-2025"),
             supersedes="returns-window-2025"),
        Fact("returns-window-2025", "policy", "Customers had 60 days to return an item with a receipt.", ("returns-2025", "returns-reminder-2025"),
             valid_until="2026-02-28", status="superseded"),
        Fact("no-receipt", "policy", "Without a receipt, store credit at the lowest price in 45 days, up to $50; above $50 a store leader approves.",
             ("returns-2026",)),
        Fact("cc-returns-stale", "policy", "Customers have 60 days to return an item with a receipt.", ("cc-returns-script-2025",),
             status="stale", contradicted_by=("returns-2026",), contradicts=("returns-window",)),
        Fact("returns-owner", "contact", "Store operations owns the returns policy; its head is Priya Shah (Max Weber left in June 2026).",
             ("returns-2026",)),
        Fact("sick-co", "policy", "Colorado store employees earn 1 hour of sick leave per 30 hours worked, up to 48 hours a year.", ("sick-co",), ("CO",)),
        Fact("sick-ca", "policy", "California store employees get 40 hours of sick leave at the start of each year.", ("sick-ca",), ("CA",)),
        Fact("sick-or", "policy", "Oregon store employees earn 1 hour of sick leave per 30 hours worked, up to 40 hours a year.", ("sick-or",), ("OR",)),
        Fact("first-week", "procedure", "A new store associate's first week: register training, safety, returns, loss prevention basics, "
             "a key holder shift; courses POS-101, SAFE-110, and RET-2026.", ("first-week",), ("Store associate",)),
        Fact("injury-steps", "procedure", "When a customer is hurt: make it safe, call the manager on duty, first aid, photos and "
             "witnesses, IR-7 within 24 hours, the district manager the same day, Risk within 2 hours after a hospital visit.",
             ("injury-procedure",)),
        Fact("injury-ca", "procedure", "California stores also record a customer injury in the store's safety log the same day.",
             ("injury-procedure",), ("CA",)),
        Fact("risk-number", "contact", "Risk on-call: 555-0170, any time.", ("who-to-call", "injury-procedure"), role="Risk on-call"),
        Fact("entrance-mats", "decision", "Entrance mats go down before the doors open (decided after the Denver fall, September 2026).",
             ("STORE-101", "opening-closing")),
        Fact("shoplifting", "procedure", "Associates never confront or follow a shoplifter; they tell the manager on duty.", ("shoplifting-basics",)),
        Fact("lp-log", "procedure", "Store leaders log a theft in the LP portal within 1 hour; over $500, they call the LP partner the same day.",
             ("lp-leaders",), ("store leaders",)),
        *(Fact(f"key-{s.code}", "answer", f"At {s.name}, the spare bike rack key is {KEY_SPOTS[i % len(KEY_SPOTS)]}.",
               (f"key-thread-{s.code}",), (s.code,)) for i, s in enumerate(stores)),
        Fact("pos-offline", "procedure", "When registers are down: offline mode (Mode and F9), cards up to $250 and cash work, call 555-0142 option 2.",
             ("pos-down", "helpdesk-pos")),
        Fact("mountain-dm", "contact", "The Mountain district (Colorado, Utah) is managed by Dara Singh.", ("districts",), ("CO", "UT"),
             role="District manager"),
        Fact("dec-24-hours", "fact", "On 24 December 2026 stores open 9:00 to 17:00.", ("holiday-hours",)),
        Fact("recall-decision", "decision", "Kai Moreno decided on 11 May 2026 to recall every TrailLite 2 tent (SKU 40211) sold since 1 February 2026.",
             ("QUAL-8", "quality-recall")),
        Fact("recall-stores", "procedure", "Stores pull every TrailLite 2 by the end of 13 May and refund any customer at full price, receipt or not.",
             ("recall-notice", "recall-announce")),
        Fact("recall-regulator", "fact", "The regulator was notified at 9:15 on 12 May 2026, report 26-1180.", ("QUAL-9",)),
        Fact("recall-delisted", "fact", "The TrailLite 2 was delisted online at 16:20 on 11 May 2026.", ("ECOM-210",)),
        Fact("recall-steps", "procedure", "A recall: stop the sale within 4 hours, notify stores, delist, tell customers, the regulator within 24 hours, daily stand-ups.",
             ("recall-playbook",)),
        Fact("recall-lesson", "lesson", "After the 2024 headlamp recall, a recall notice is always published in Store operations and posted in #stores-all.",
             ("recall-playbook",)),
        Fact("recall-reports", "fact", "14 customers reported cracked TrailLite 2 poles, from lots made after January 2026.", ("QUAL-7",)),
        Fact("poles-ship", "fact", "Replacement poles ship on 20 May; stores have them by 27 May.", ("meeting-recall-standup",)),
        Fact("escalation", "procedure", "Contact center escalates refunds over $200, injuries (with a Risk case), legal threats, and third contacts.",
             ("cc-escalation", "cc-injury")),
        Fact("late-shipment", "procedure", "A late inbound shipment: call the carrier, over 48 hours email the buyer and open a DC ticket, over 5 days the buyer decides.",
             ("late-shipments",)),
        Fact("northpeak-lesson", "lesson", "Northpeak's 2025 late shipment had been rescheduled by the carrier: call the carrier first.",
             ("DC-55", "dc-northpeak")),
        Fact("forklift", "fact", "Forklift 3 is out of service until Thursday; use forklift 5 for dock 2.", ("teams-forklift",)),
        Fact("sell-through", "definition", "Sell-through is units sold divided by units received, by style and color, from merch_weekly_sales.",
             ("merch-glossary",), contradicted_by=("merch-sellthrough-2025",)),
        Fact("weeks-of-supply", "definition", "Weeks of supply is units on hand over the average weekly units sold in the last 4 weeks.", ("merch-glossary",)),
        Fact("allocation", "policy", "Allocation ranks stores by 8 weeks' sell-through: the top quarter get 30 percent more, the bottom quarter 20 percent fewer.",
             ("allocation-rules",)),
        Fact("checkout-runbook", "procedure", "Checkout errors: the provider's status, the checkout_errors dashboard, roll back a deploy, page the provider, update #ecom-incidents.",
             ("checkout-runbook",)),
        Fact("sdk-change", "fact", "The payment SDK 5.2 went out on 14 October 2026; Safari card errors followed and were fixed with a config flag.",
             ("ECOM-260", "ecom-sdk")),
        Fact("ecom-oncall", "contact", "Jon Reyes is e-commerce on-call the week of 12 October 2026.", ("ecom-oncall",)),
        Fact("flash-sale-lesson", "lesson", "The 2025 flash sale outage came from the payment connection pool; load tests now precede every flash sale.",
             ("ECOM-233",)),
        Fact("copy-approval", "contact", "Campaign copy is approved by the brand director, Rosa Kim.", ("brand-guidelines",)),
        Fact("holiday-results", "fact", "Holiday 2025 sales were up 12 percent, with a 31 percent email open rate.", ("holiday-2025-results",)),
        Fact("close-steps", "procedure", "Month-end close: the inventory accrual on day 1, AP cutoff day 2, revenue reconciliation day 3, review day 4.",
             ("close-checklist",)),
        Fact("close-lesson", "lesson", "September 2026's accrual was late because the DC count file came on day 3; it's due by noon on day 1.",
             ("close-september",)),
        Fact("pay-associate", "fact", "A store associate's pay range is $17.50 to $21.00 an hour.", ("pay-ranges",), ("people leads",)),
        Fact("sam-medical", "fact", "A store associate's surgery (who covers their shifts).", ("medical-thread",), status="sensitive"),
        Fact("jordan-warning", "fact", "A store associate's written warning.", ("hr-warning",), status="sensitive"),
        Fact("migration-waves", "decision", "The EDW moves to the EDP in three waves: sales (SALES_TXN, ITEM, STORE, DAILY_SALES) from 15 "
             "September 2026, inventory from 1 December 2026, finance from 1 March 2027; each runs side by side for 30 days, "
             "within 0.1 percent.", ("edw-to-edp", "DATA-12")),
        Fact("edw-retires", "decision", "The EDW retires at the end of 2027; LEGACY_RETURNS and PROMO_LIFT_2019 aren't migrated "
             "(nobody read them in a year).", ("edw-to-edp",)),
        Fact("inv-daily-late", "procedure", "When INV_DAILY is late: check LOAD_STG_WMS_INV in Control-M, call distribution systems "
             "on-call (555-0188) if the WMS extract is missing, rerun LOAD_STG_WMS_INV then LOAD_INV_DAILY, warn merchandising by 05:30.",
             ("inv-daily-runbook",)),
        Fact("inv-daily-lesson", "lesson", "On 22 July 2026 the WMS extract came at 04:10 and the allocation report was stale until "
             "07:15; the extract now runs at 01:00.", ("DATA-31",)),
    )


# Versions others hold of a fact (F1's perspectives), each opening a conflict with it: (fact, holder kind, holder,
# context, their version, its evidence). The planner's thread is read by Merchandising alone, the glossary by
# district leaders too, so a district leader reads the definition but neither the planner's version nor the conflict.
PERSPECTIVES = (
    ("sell-through", "role", "Merchandise planner", "the store review deck",
     "Sell-through is units sold over units on hand.", ("merch-sellthrough-2025",)),
)


def _questions() -> tuple[Question, ...]:
    Q = Question
    return (
        Q("injury-what-to-do", "ana", "A customer slipped near the entrance. What do I do?", "procedure", ("injury-procedure",), facts=("injury-steps",)),
        Q("injury-california", "cora", "What else does a California store record after a customer falls?", "location", ("injury-procedure",), facts=("injury-ca",)),
        Q("injury-denver-fall", "dara", "What happened after the customer fell at the Denver Union Station entrance?", "lesson", ("STORE-101",),
          facts=("entrance-mats",)),
        Q("risk-hospital", "ben", "Who do I call at Risk if a customer went to hospital?", "contact", ("who-to-call",), facts=("risk-number",)),
        Q("returns-window", "ana", "How many days does a customer have to return something?", "stale", ("returns-2026",), facts=("returns-window",)),
        Q("returns-50-days", "fay", "Can a customer return a jacket they bought 50 days ago?", "policy", ("returns-2026",), facts=("returns-window",)),
        Q("returns-no-receipt", "ben", "When does a return without a receipt need a store leader?", "policy", ("returns-2026",), facts=("no-receipt",)),
        Q("returns-owner", "ana", "Who owns the returns policy?", "owner", ("returns-2026",), facts=("returns-owner",)),
        Q("returns-changes", "fay", "What changed in the returns policy this year?", "policy", ("returns-2026", "cc-weekly-changes"), facts=("returns-window", "no-receipt")),
        Q("sick-colorado", "ben", "How much paid sick leave do Colorado store employees earn?", "location", ("sick-co",), facts=("sick-co",)),
        Q("sick-california", "cora", "How much paid sick leave do California store employees get?", "location", ("sick-ca",), facts=("sick-ca",)),
        Q("sick-mine-co", "ben", "How much sick leave do I earn?", "location", ("sick-co",), facts=("sick-co",)),
        Q("sick-mine-or", "ana", "How much sick leave do I earn?", "location", ("sick-or",), facts=("sick-or",)),
        Q("first-week", "ana", "What do I do in my first week?", "onboarding", ("first-week",), facts=("first-week",)),
        Q("first-week-courses", "ana", "Which courses do I need to finish in my first week?", "onboarding", ("first-week",), facts=("first-week",)),
        Q("shoplifting-associate", "ana", "What should I do if I see someone shoplifting?", "permission", ("shoplifting-basics",), ("lp-leaders",),
          facts=("shoplifting",)),
        Q("shoplifting-leader", "cora", "How soon must a theft be logged in the loss prevention portal?", "lookup", ("lp-leaders",), facts=("lp-log",)),
        Q("bike-key-portland", "ana", "Where is the spare key for the bike rack lock?", "undocumented", ("key-thread-OR101",), ("key-thread-CO201",),
          facts=("key-OR101",)),
        Q("bike-key-denver", "ben", "Where is the spare key for the bike rack lock?", "undocumented", ("key-thread-CO201",), ("key-thread-OR101",),
          facts=("key-CO201",)),
        Q("registers-down-leader", "cora", "The registers are down. What do we do?", "procedure", ("pos-down",), facts=("pos-offline",)),
        Q("registers-down-associate", "ana", "The registers are down. What do we do?", "permission", ("helpdesk-pos",), ("pos-down",), facts=("pos-offline",)),
        Q("registers-denver", "dara", "What happened when the registers went offline at Denver Union Station?", "lookup", ("STORE-102",)),
        Q("district-manager-mountain", "ben", "Who is the district manager for the Mountain district?", "contact", ("districts",), facts=("mountain-dm",)),
        Q("district-manager-mine", "ben", "Who is my district manager?", "contact", ("districts",), facts=("mountain-dm",)),
        Q("christmas-eve-hours", "ana", "What time does the store close on December 24?", "lookup", ("holiday-hours",), facts=("dec-24-hours",)),
        Q("recall-stores", "cora", "What do stores need to do for the TrailLite 2 recall?", "procedure", ("recall-notice",), facts=("recall-stores",)),
        Q("recall-can-i-sell", "ana", "Can I sell a TrailLite 2 tent?", "procedure", ("recall-notice",), facts=("recall-stores",)),
        Q("recall-script", "fay", "What do I tell a customer calling about the TrailLite 2 recall?", "procedure", ("cc-recall-script",)),
        Q("recall-steps", "kai", "What is the order of steps in a product recall?", "procedure", ("recall-playbook",), facts=("recall-steps",)),
        Q("recall-decision", "cora", "Who decided to recall the TrailLite 2, and when?", "decision", ("QUAL-8",), facts=("recall-decision",)),
        Q("recall-regulator-hidden", "cora", "Has the regulator been notified about the TrailLite 2 recall?", "permission", (), ("QUAL-9",)),
        Q("recall-regulator", "kai", "Has the regulator been notified about the TrailLite 2 recall?", "lookup", ("QUAL-9",), facts=("recall-regulator",)),
        Q("recall-delisted", "jon", "Was the TrailLite 2 delisted from the website?", "lookup", ("ECOM-210",), facts=("recall-delisted",)),
        Q("recall-poles", "kai", "When do the replacement poles for the TrailLite 2 ship?", "capture", ("meeting-recall-standup",), needs="C3",
          facts=("poles-ship",)),
        Q("recall-lesson", "kai", "What did we learn from the last recall?", "lesson", ("recall-playbook",), facts=("recall-lesson",)),
        Q("recall-reports", "fay", "How many customers reported the TrailLite 2 poles cracking?", "lookup", ("QUAL-7",), facts=("recall-reports",)),
        Q("cc-escalate", "fay", "When do I escalate a call to a supervisor?", "procedure", ("cc-escalation",), facts=("escalation",)),
        Q("cc-injury-return", "fay", "A caller says a product hurt them. Do I process the return first?", "two-source", ("cc-escalation", "cc-injury"),
          facts=("escalation",)),
        Q("late-shipment", "eli", "A vendor shipment is two days late. What's the procedure?", "procedure", ("late-shipments",), facts=("late-shipment",)),
        Q("northpeak-last-time", "eli", "What happened last time a Northpeak shipment was late?", "lesson", ("DC-55", "dc-northpeak"),
          facts=("northpeak-lesson",)),
        Q("forklift-dock-2", "eli", "Which forklift do I use at dock 2 this week?", "capture", ("teams-forklift",), needs="C1", facts=("forklift",)),
        Q("sell-through", "gus", "How is sell-through calculated in the weekly report?", "definition", ("merch-glossary",), facts=("sell-through",)),
        Q("weeks-of-supply", "gus", "What is weeks of supply?", "definition", ("merch-glossary",), facts=("weeks-of-supply",)),
        Q("allocation-fewer-units", "dara", "Why would a store get fewer units of a new jacket than other stores?", "policy", ("allocation-rules",),
          facts=("allocation",)),
        Q("checkout-runbook", "jon", "Checkout errors are spiking. What's the runbook?", "procedure", ("checkout-runbook",), facts=("checkout-runbook",)),
        Q("checkout-changed", "jon", "What changed in checkout recently?", "two-source", ("ECOM-260", "ecom-sdk"), facts=("sdk-change",)),
        Q("ecom-on-call", "jon", "Who is on call for e-commerce the week of 12 October?", "contact", ("ecom-oncall",), facts=("ecom-oncall",)),
        Q("flash-sale-lesson", "jon", "What did we learn from the flash sale checkout outage?", "lesson", ("ECOM-233",), facts=("flash-sale-lesson",)),
        Q("copy-approval", "lea", "Who approves copy for the holiday campaign?", "contact", ("brand-guidelines",), facts=("copy-approval",)),
        Q("holiday-results", "lea", "How did last year's holiday campaign do?", "lookup", ("holiday-2025-results",), facts=("holiday-results",)),
        Q("close-checklist", "hana", "What's on the month-end close checklist?", "procedure", ("close-checklist",), facts=("close-steps",)),
        Q("close-september", "hana", "What went wrong in the September close?", "lesson", ("close-september",), facts=("close-lesson",)),
        Q("pay-range-people", "ivy", "What is the pay range for a store associate?", "lookup", ("pay-ranges",), facts=("pay-associate",)),
        Q("pay-range-associate", "ana", "What is the pay range for a store associate?", "permission", (), ("pay-ranges",)),
        Q("pay-range-planner", "gus", "What is the pay range for a store associate?", "permission", (), ("pay-ranges",)),
        Q("supplier-audit-hidden", "cora", "What did the Northpeak supplier audit find?", "permission", (), ("supplier-audit",)),
        Q("first-wave", "nia", "Which tables move to the EDP in the first wave?", "decision", ("edw-to-edp",), facts=("migration-waves",)),
        Q("inv-daily-late", "otto", "What do I do when INV_DAILY is late?", "procedure", ("inv-daily-runbook",), facts=("inv-daily-late",)),
        Q("allocation-stale-hidden", "gus", "Why was the allocation report stale on 22 July?", "permission", (), ("DATA-31",)),
        Q("sam-out", "ben", "Why is Sam Rivera out next week?", "sensitive", withhold=("medical-thread",), facts=("sam-medical",)),
        Q("jordan-warning", "ivy", "Has Jordan Blake had any warnings?", "sensitive", withhold=("hr-warning",), facts=("jordan-warning",)),
    )


# ------------------------------------------------------------------ checks the truth holds itself to
def unit_title(fact_id: str) -> str:
    """The title a fact's knowledge unit is loaded with (kl.company_eval) and named by in the teams' question sets."""
    return (fact_id[0].upper() + fact_id[1:]).replace("-", " ")


def question_sets(world: World) -> dict[str, dict]:
    """F7: the truth's questions as each team's question set (the asker's team), in the format a company's teams keep in
    their onboarding repository (teams/<team>/questions.yaml): who asks, what, the pages a right answer draws on, the
    units it should lead with, and what that asker must never see. Stores' people also ask through their team's model."""
    loaded = {d.name for d in world.docs if d.source in LOADED}
    units = {f.id for f in world.facts if f.status not in ("sensitive", "superseded") and set(f.evidence) <= loaded}
    sets: dict[str, dict] = {}
    for q in world.questions:
        team = world.person(q.asker).team
        case = {"id": q.id, "asker": q.asker, "question": q.text, "kind": q.kind,
                "expect": [world.doc(n).key for n in q.expect], "units": [unit_title(f) for f in q.facts if f in units],
                "forbid": [world.doc(n).key for n in q.forbid], **({"needs": q.needs} if q.needs else {})}
        sets.setdefault(team, {"version": 1, "team": team, **({"model": f"{team}/smoke-fake"} if team == "stores" else {}),
                               "cases": []})["cases"].append(case)
    return dict(sorted(sets.items()))


def problems(world: World) -> list[str]:
    """Everything wrong with the company's truth: a question naming a page that isn't there, a right page the asker
    can't read (unless it waits on a slice), a forbidden page they can read, a sensitive page they can't."""
    names = {d.name for d in world.docs}
    facts = {f.id for f in world.facts}
    personas = {p.username for p in world.people if p.username}
    out = [f"fact {f.id}: no page {e}" for f in world.facts for e in f.evidence if e not in names]
    out += [f"sensitive message: no {n}, or no message {i} in it" for n, i in SENSITIVE_MESSAGES
            if n not in names or len(world.doc(n).messages) <= i]
    out += [f"fact {f.id} supersedes {f.supersedes}, which isn't a fact" for f in world.facts if f.supersedes and f.supersedes not in facts]
    out += [f"fact {f.id} contradicts {g}, which isn't a fact" for f in world.facts for g in f.contradicts if g not in facts]
    out += [f"a perspective on {fact} names what isn't there" for fact, *_, evidence in PERSPECTIVES
            if fact not in facts or any(e not in names for e in evidence)]
    for q in world.questions:
        if q.asker not in personas:
            out.append(f"{q.id}: {q.asker} doesn't sign in")
            continue
        missing = [n for n in q.expect + q.forbid + q.withhold if n not in names] + [f for f in q.facts if f not in facts]
        if missing:
            out.append(f"{q.id}: names what isn't there: {missing}")
            continue
        seen = world.visible(q.asker)
        out += [f"{q.id}: {q.asker} can't read {n}, a right page" for n in q.expect if n not in seen and not q.needs]
        out += [f"{q.id}: {q.asker} can read {n}, a forbidden page" for n in q.forbid if n in seen]
        out += [f"{q.id}: {q.asker} can't read {n}: a sensitive page should be one the source lets them read" for n in q.withhold if n not in seen]
        out += [f"{q.id}: its sensitive page {n} names no sensitive message (C2a)" for n in q.withhold
                if n not in {name for name, _ in SENSITIVE_MESSAGES}]
    asked = {q.asker for q in world.questions}
    out += [f"{p} asks nothing" for p in sorted(personas - asked)]
    return out


# ------------------------------------------------------------------ what each fake serves
def _account(p: Person) -> dict:
    return {"accountId": p.atlassian, "emailAddress": p.email, "displayName": p.name}


def storage(markup: str) -> str:
    """Confluence's storage format from the pages' markup: '## ' a heading, '1. ' a step, '- ' a bullet, '| a | b |' a
    table row (the first is its header), anything else a paragraph."""
    out, open_tag, rows = [], "", 0
    for line in markup.splitlines() + [""]:
        kind = ("h" if line.startswith("## ") else "ol" if re.match(r"\d+\. ", line) else "ul" if line.startswith("- ")
                else "table" if line.startswith("|") else "p" if line.strip() else "")
        if open_tag and kind != open_tag:
            out.append(f"</{open_tag}>")
            open_tag, rows = "", 0
        if kind in ("ol", "ul"):
            if not open_tag:
                out.append(f"<{kind}>")
                open_tag = kind
            out.append(f"<li>{html.escape(line.split(' ', 1)[1])}</li>")
        elif kind == "table":
            if not open_tag:
                out.append("<table>")
                open_tag = "table"
            cell = "th" if rows == 0 else "td"
            out.append("<tr>" + "".join(f"<{cell}>{html.escape(c.strip())}</{cell}>" for c in line.strip().strip("|").split("|")) + "</tr>")
            rows += 1
        elif kind == "h":
            out.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif kind == "p":
            out.append(f"<p>{html.escape(line)}</p>")
    return "".join(out)


def confluence_site(world: World) -> dict:
    here = [p for p in world.people if p.active]
    by_email = {p.email: p for p in here}
    spaces = {}
    for c in (c for c in world.containers if c.source == "confluence"):
        spaces[c.id] = {"id": str(c.number), "name": c.name,
                        "groups": ["confluence-users" if g == EVERYONE else g for g in c.readers],
                        "pages": [{"id": d.key.split("-", 1)[1], "title": d.title, "version": 1, "modified": f"{d.when}T10:00:00.000Z",
                                   "body": storage(d.text), "restricted_groups": list(d.restricted)}
                                  for d in world.docs if d.source == "confluence" and d.container == c.id]}
    named = {g for c in world.containers if c.source == "confluence" for g in c.readers if g != EVERYONE} | \
            {g for d in world.docs if d.source == "confluence" for g in d.restricted}
    groups = {g: {"name": g, "members": sorted(by_email[e].atlassian for e in world.groups[g])} for g in sorted(named)}
    groups["confluence-users"] = {"name": "confluence-users", "members": sorted(p.atlassian for p in here)}
    return {"users": {p.atlassian: {"email": p.email, "name": p.name} for p in here}, "groups": groups, "spaces": spaces}


def jira_site(world: World) -> dict:
    by_email = {p.email: p for p in world.people if p.active}
    accounts = lambda emails: [_account(by_email[e]) for e in sorted(emails)]
    stamp = lambda when: f"{when}:00.000+0000" if "T" in when else f"{when}T10:00:00.000+0000"
    projects = {}
    for c in (c for c in world.containers if c.source == "jira"):
        members = frozenset().union(*(world.groups[g] for g in c.readers))
        issues = []
        for d in (d for d in world.docs if d.source == "jira" and d.container == c.id):
            spec = {"key": d.key.split("-", 1)[1], "summary": d.title, "description": d.text, "updated": stamp(d.when),
                    "comments": [{"author": a, "created": stamp(at), "body": text} for a, at, text in d.messages]}
            if d.resolved:
                spec |= {"resolution": {"name": "Done"}, "resolutiondate": stamp(d.when)}
            if d.restricted:
                spec |= {"security": {"name": "Quality leads"}, "viewers": accounts(d.readers or ())}
            issues.append(spec)
        projects[c.id] = {"name": c.name, "company_wide": False, "browsers": accounts(members), "issues": issues}
    return {"projects": projects}


def slack_workspace(world: World) -> dict:
    here = [p for p in world.people if p.active]
    by_name = {p.name: p for p in world.people}
    channels = {}
    for c in (c for c in world.containers if c.source == "slack"):
        members = [p.slack for p in here] if EVERYONE in c.readers else \
                  sorted(p.slack for p in here if any(p.email in world.groups[g] for g in c.readers))
        messages = []
        for d in (d for d in world.docs if d.source == "slack" and d.container == c.id):
            parent = _ts(d.messages[0][1].replace("T", " "))
            replies = [{"ts": _ts(at.replace("T", " "), i), "user": by_name[a].slack, "thread_ts": parent, "text": text}
                       for i, (a, at, text) in enumerate(d.messages[1:], start=1)]
            messages.append({"ts": parent, "user": by_name[d.messages[0][0]].slack, "thread_ts": parent, "reply_count": len(replies),
                             "text": d.messages[0][2], "replies": replies})
        channels[c.id] = {"name": c.name, "private": EVERYONE not in c.readers, "members": members, "messages": messages}
    users = [{"id": p.slack, "team_id": SLACK_TEAM, "name": p.email.split("@")[0], "deleted": not p.active,
              "profile": {"real_name": p.name, "email": p.email}} for p in world.people]
    return {"users": users, "channels": channels}


def manager_of(world: World, person: Person) -> Person | None:
    """Who a person reports to: an associate or assistant to their store's manager, a store manager to their district's
    manager, a district manager to the head of store operations, anyone else to their team's lead; leads at the top of
    their team report to no one here. Only people still at the company."""
    here = [p for p in world.people if p.active and p != person]
    place = next(p for p in world.places if p.code == person.place)
    first = lambda test: next((p for p in here if test(p)), None)
    if person.team == "stores":
        if person.title in ("Store associate", "Assistant store manager"):
            return first(lambda p: p.title == "Store manager" and p.place == person.place)
        if person.title == "Store manager":
            return first(lambda p: p.title == f"District manager, {place.district}")
        if person.title.startswith("District manager"):
            return first(lambda p: p.title == "Head of store operations")
        return None
    return None if person.leads else first(lambda p: p.team == person.team and p.leads)


def entra_tenant(world: World) -> dict:
    """The company's directory: everyone, with their title, team, store or office (by its code), state, and manager (F8
    reads these), the one who has left disabled, and a group per team."""
    places = {p.code: p for p in world.places}
    boss = {p.email: manager_of(world, p) for p in world.people}
    users = [{"id": p.entra, "mail": p.email, "userPrincipalName": p.email, "displayName": p.name, "accountEnabled": p.active,
              "jobTitle": p.title, "department": dict((s, n) for s, n, _ in TEAMS)[p.team], "officeLocation": p.place,
              "state": places[p.place].state, **({"manager": {"mail": boss[p.email].email}} if boss[p.email] else {})}
             for p in world.people]
    groups = {str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://acme.test/groups/{slug}")):
              {"mail": f"{slug}@{DOMAIN}", "name": name, "members": [{"user": p.entra} for p in world.people if p.team == slug and p.active]}
              for slug, name, _ in TEAMS}
    return {"users": users, "groups": groups}


def truth(world: World) -> dict:
    return {
        "company": COMPANY, "seed": world.seed, "stores": world.stores,
        "places": [p.__dict__ for p in world.places],
        "teams": [{"slug": s, "name": n, "discoverability": d} for s, n, d in TEAMS],
        "people": [{**p.__dict__} for p in world.people],
        "documents": [{"name": d.name, "key": d.key, "source": d.source, "container": d.container, "title": d.title, "when": d.when,
                       "loaded": d.source in LOADED, "readers": EVERYONE if d.readers is None else sorted(d.readers)} for d in world.docs],
        "facts": [f.__dict__ for f in world.facts],
        "questions": [{**q.__dict__, "expect": [world.doc(n).key for n in q.expect], "forbid": [world.doc(n).key for n in q.forbid],
                       "withhold": [world.doc(n).key for n in q.withhold]} for q in world.questions],
    }


def main(argv: list[str]) -> int:
    def arg(name: str, default: int) -> int:
        return int(argv[argv.index(name) + 1]) if name in argv else default
    world = build(arg("--seed", 7), arg("--stores", 6))
    wrong = problems(world)
    if wrong:
        print("\n".join(wrong), file=sys.stderr)
        return 1
    print(json.dumps(truth(world), indent=2, default=list))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
