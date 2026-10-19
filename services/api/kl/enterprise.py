"""The synthetic enterprise (F10b, docs/ORG_INTELLIGENCE_PLAN.md O2): the retailer's data estate, with known truth, on
which the Atlas and every item after it (O3 to O26) is proven.

Acme Outfitters (kl.company) runs a Teradata warehouse, the EDW, and is moving it to a BigQuery platform, the EDP, a
wave at a time. `build(world, repos)` makes the estate from the retailer, the same every time:

- the EDW: staging tables Control-M loads from the source systems' extracts, core tables, and reports, with the legacy
  BI reports that read them; and, for the migration, its nightly exports to Cloud Storage;
- the EDP: Composer jobs loading those exports into raw tables, and the edp-dbt project's staging models and marts,
  with the Looker dashboards that read them;
- repositories, as many as asked for (the size), each with its kind and owning team;
- the truth: every real node and edge, and for each edge the sources that reveal it; the name each loaded source knows
  a node by (an EDW table is `teradata` / `edw.sales_txn` to DataHub but `teradata://edw.acme.test:1025` to
  OpenLineage, two nodes in the Atlas until O13 resolves them); each table's reads a day (its usage rank), each node's
  system and owner, the right migration wave, what replaces what, and what the data teams' pages name.

Each source belongs to a team and is loaded now (the EDW's exports' OpenLineage, Composer's, the edp-dbt manifest, and
the EDW's DataHub) or waits on the item that builds it (Control-M, query history, BI, repositories). The render
functions give each loaded source what it sends; kl.enterprise_eval loads them and scores the Atlas. Nothing here
touches a network or a database.

    python -m kl.enterprise [--repos N] [--stores N] [--seed S]    # the truth, as JSON
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from dataclasses import dataclass

from . import company

TERADATA = "teradata://edw.acme.test:1025"  # the EDW, as OpenLineage names a Teradata system
EXPORTS = "gs://acme-edw-exports"
EXTRACTS = "sftp://etl.acme.test"
EDP = "acme-edp"
COMPOSER = "composer-edp"
CORE_REPO_COUNT = 15
DEFAULT_REPOS = 40


@dataclass(frozen=True)
class Source:
    team: str          # the team whose connection, push, or pipeline brings it in
    waits_on: str      # the item that builds it; empty: loaded now


SOURCES = {
    "lineage-edw": Source("edw", ""),     # OpenLineage from the EDW's export jobs
    "datahub": Source("edw", ""),         # the EDW's catalog: its tables, owners, and table lineage
    "lineage-edp": Source("edp", ""),     # OpenLineage from Composer
    "dbt": Source("edp", ""),             # the edp-dbt manifest, pushed from CI
    "controlm": Source("edw", "O10"),     # folders, jobs, conditions, and the scripts they run
    "dbql": Source("edw", "O9"),          # Teradata's query log: usage, and lineage through O6
    "bigquery-jobs": Source("edp", "O7"),
    "microstrategy": Source("edw", "O24"),
    "looker": Source("edp", "O24"),
    "github": Source("", "O4"),           # each repository to its owning team; analyzed by O5
}
LOADED = tuple(s for s, src in SOURCES.items() if not src.waits_on)

# The EDW's tables: name, domain, Control-M job that builds it, what it's built from (tables, or a source system's
# extract for a staging table), reads a day (query history), and its migration wave ("retire": not migrated)
EDW_TABLES = (
    ("edw_stg.pos_sales", "sales", "LOAD_STG_POS", ("pos/sales",), 3, "1"),
    ("edw_stg.oms_orders", "sales", "LOAD_STG_OMS", ("oms/orders",), 3, "1"),
    ("edw_stg.plan_items", "merchandising", "LOAD_STG_ITEMS", ("plan/items",), 2, "1"),
    ("edw_stg.erp_stores", "stores", "LOAD_STG_STORES", ("erp/stores",), 2, "1"),
    ("edw_stg.wms_inventory", "inventory", "LOAD_STG_WMS_INV", ("wms/inventory",), 2, "2"),
    ("edw_stg.wms_receipts", "inventory", "LOAD_STG_WMS_RCPT", ("wms/receipts",), 2, "2"),
    ("edw_stg.erp_gl", "finance", "LOAD_STG_ERP_GL", ("erp/gl",), 1, "3"),
    ("edw.sales_txn", "sales", "LOAD_SALES_TXN", ("edw_stg.pos_sales", "edw_stg.oms_orders"), 410, "1"),
    ("edw.item", "merchandising", "LOAD_ITEM", ("edw_stg.plan_items",), 380, "1"),
    ("edw.store", "stores", "LOAD_STORE", ("edw_stg.erp_stores",), 350, "1"),
    ("edw.inv_daily", "inventory", "LOAD_INV_DAILY", ("edw_stg.wms_inventory",), 220, "2"),
    ("edw.receipts", "inventory", "LOAD_RECEIPTS", ("edw_stg.wms_receipts",), 45, "2"),
    ("edw.gl_balances", "finance", "LOAD_GL", ("edw_stg.erp_gl",), 60, "3"),
    ("edw_rpt.daily_sales", "sales", "BUILD_DAILY_SALES", ("edw.sales_txn", "edw.store", "edw.item"), 1840, "1"),
    ("edw_rpt.inv_position", "inventory", "BUILD_INV_POSITION", ("edw.inv_daily", "edw.receipts", "edw.item", "edw.store"), 960, "2"),
    ("edw_rpt.gl_summary", "finance", "BUILD_GL_SUMMARY", ("edw.gl_balances",), 140, "3"),
    ("edw_rpt.legacy_returns", "sales", "BUILD_LEGACY_RETURNS", ("edw.sales_txn",), 0, "retire"),
    ("edw_rpt.promo_lift_2019", "merchandising", "BUILD_PROMO_LIFT", ("edw.sales_txn", "edw.item"), 0, "retire"),
)
SYSTEM_OF_EXTRACT = {"pos": "POS", "oms": "OMS", "plan": "PLAN", "erp": "ERP", "wms": "WMS"}
EXPORTED = (("edw.sales_txn", "EXPORT_SALES_TXN"), ("edw.item", "EXPORT_ITEM"), ("edw.store", "EXPORT_STORE"),
            ("edw.inv_daily", "EXPORT_INV_DAILY"))  # waves 1 and 2 so far: what the EDW exports each night
# The EDP's dbt models: name (schema.table), what it's built from, reads a day, and the EDW table it replaces
DBT_MODELS = (
    ("staging.stg_sales", ("raw.edw_sales_txn",), 4, ""),
    ("staging.stg_item", ("raw.edw_item",), 4, ""),
    ("staging.stg_store", ("raw.edw_store",), 4, ""),
    ("staging.stg_inventory", ("raw.edw_inv_daily",), 3, ""),
    ("marts.fct_sales", ("staging.stg_sales", "staging.stg_item", "staging.stg_store"), 300, "edw.sales_txn"),
    ("marts.dim_item", ("staging.stg_item",), 260, "edw.item"),
    ("marts.dim_store", ("staging.stg_store",), 240, "edw.store"),
    ("marts.daily_sales", ("marts.fct_sales", "marts.dim_store"), 1210, "edw_rpt.daily_sales"),
    ("marts.fct_inventory", ("staging.stg_inventory", "staging.stg_item", "staging.stg_store"), 180, "edw.inv_daily"),
)
DASHBOARDS = (  # tool, name, the table it reads, its source
    ("microstrategy", "Daily sales flash", "edw_rpt.daily_sales", "EDW"),
    ("microstrategy", "Inventory position", "edw_rpt.inv_position", "EDW"),
    ("microstrategy", "GL summary", "edw_rpt.gl_summary", "EDW"),
    ("looker", "Sales flash", "marts.daily_sales", "EDP"),
    ("looker", "Inventory health", "marts.fct_inventory", "EDP"),
)
# Repositories: name, kind, owning team, system, what it builds (a job folder, "dbt", or a dashboard tool), what it depends on
REPOS = (
    ("edw-etl", "pipeline", "edw", "EDW", "EDW_DAILY", ()),
    ("edw-exports", "pipeline", "edw", "EDW", "EDW_EXPORTS", ()),
    ("edw-reports", "configuration", "edw", "EDW", "microstrategy", ()),
    ("edp-composer", "pipeline", "edp", "EDP", COMPOSER, ()),
    ("edp-dbt", "pipeline", "edp", "EDP", "dbt", ()),
    ("edp-terraform", "infrastructure", "edp", "EDP", "", ()),
    ("looker-edp", "configuration", "edp", "EDP", "looker", ()),
    ("retail-common", "library", "ecommerce", "shared", "", ()),
    ("pos-gateway", "service", "it-support", "POS", "", ("retail-common",)),
    ("oms-api", "service", "ecommerce", "OMS", "", ("retail-common",)),
    ("storefront", "service", "ecommerce", "OMS", "", ("retail-common",)),
    ("wms-sync", "service", "distribution", "WMS", "", ()),
    ("erp-connector", "service", "accounting", "ERP", "", ()),
    ("promo-lift-2019", "deprecated", "edw", "EDW", "", ()),
    ("teradata-jdbc", "vendored", "edw", "EDW", "", ()),
)
CALLS = (("storefront", "oms-api"), ("oms-api", "pos-gateway"))
GENERATED_KINDS = ("service", "library", "service", "pipeline", "service", "configuration", "deprecated", "vendored")
GENERATED_TEAMS = ("stores", "distribution", "contact-center", "merchandising", "ecommerce", "marketing",
                   "product-quality", "accounting", "it-support", "edw", "edp")
# What the data teams' pages and tickets (kl.company's DATA space and project) name, for O15 to link
MENTIONS = {
    "edw-to-edp": ("table:edw.sales_txn", "table:edw.item", "table:edw.store", "table:edw_rpt.daily_sales", "table:edw.inv_daily",
                   "table:edw.receipts", "table:edw_rpt.inv_position", "table:edw.gl_balances", "table:edw_rpt.gl_summary",
                   "table:edw_rpt.legacy_returns", "table:edw_rpt.promo_lift_2019", "repo:edp-dbt"),
    "inv-daily-runbook": ("job:EDW_DAILY/LOAD_INV_DAILY", "job:EDW_DAILY/LOAD_STG_WMS_INV", "table:edw.inv_daily",
                          "table:edw_rpt.inv_position"),
    "DATA-12": ("table:edw.sales_txn", "table:edw.item", "table:edw.store", "job:EDW_EXPORTS/EXPORT_SALES_TXN",
                "job:EDW_EXPORTS/EXPORT_ITEM", "job:EDW_EXPORTS/EXPORT_STORE", "table:acme-edp.marts.fct_sales",
                "table:acme-edp.marts.dim_item", "table:acme-edp.marts.dim_store", "table:acme-edp.marts.daily_sales", "repo:edp-dbt"),
    "DATA-31": ("job:EDW_DAILY/LOAD_STG_WMS_INV", "job:EDW_DAILY/LOAD_INV_DAILY", "table:edw.inv_daily"),
}
# Where impact is checked from, as a loaded source names it
IMPACT_FROM = (("lineage-edw", "table:edw.sales_txn"), ("lineage-edp", f"file:{EXPORTS}/sales_txn"),
               ("dbt", f"table:{EDP}.raw.edw_inv_daily"), ("datahub", "table:edw_stg.wms_inventory"))


@dataclass(frozen=True)
class Node:
    key: str                               # stable across seeds and sizes: table:edw.sales_txn, job:EDW_DAILY/LOAD_ITEM
    kind: str                              # as the Atlas holds it: dataset, job, dashboard, repo
    system: str
    team: str                              # the team that owns it
    names: tuple[tuple[str, str, str], ...]  # (source, namespace, name): what each loaded source calls it
    owner: str = ""                        # an email
    domain: str = ""
    reads_per_day: int | None = None       # a table's usage, from query history
    wave: str = ""                         # an EDW table's migration wave
    replaces: str = ""                     # the EDW table an EDP table replaces
    role: str = ""                         # a repository's kind: service, library, pipeline, deprecated...


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    kind: str                              # in the direction data or control flows: reads, writes, feeds, triggers...
    sources: tuple[str, ...]


@dataclass(frozen=True)
class Estate:
    world: company.World
    repos: int
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]

    def node(self, key: str) -> Node:
        return next(n for n in self.nodes if n.key == key)


# ------------------------------------------------------------------ the estate
def build(world: company.World, repos: int = DEFAULT_REPOS) -> Estate:
    if repos < CORE_REPO_COUNT:
        raise ValueError(f"an estate has at least {CORE_REPO_COUNT} repositories: the data teams' own, and the source systems'")
    people = {p.name: p.email for p in world.people}
    first = lambda title: next(p.email for p in world.people if p.title == title and p.active and not p.username)
    nia, otto = world.person("nia").email, world.person("otto").email
    nodes: dict[str, Node] = {}
    edges: dict[tuple[str, str, str], set[str]] = defaultdict(set)

    def add(key, kind, system, team, names=(), **kw):
        nodes[key] = Node(key, kind, system, team, tuple(names), **kw)

    def link(src, dst, kind, *sources):
        edges[(src, dst, kind)].update(sources)

    for path, system in ((t, SYSTEM_OF_EXTRACT[t.split("/")[0]]) for row in EDW_TABLES for t in row[3] if "/" in t):
        add(f"file:{EXTRACTS}/{path}", "dataset", system, "edw")
    for name, domain, job, built_from, reads, wave in EDW_TABLES:
        layer = name.split(".")[0]
        owner = otto if layer == "edw_stg" else people["Greta Lund"] if layer == "edw" else first("BI developer")
        add(f"table:{name}", "dataset", "EDW", "edw", [("datahub", "teradata", name)], owner=owner, domain=domain,
            reads_per_day=reads, wave=wave)
        add(f"job:EDW_DAILY/{job}", "job", "EDW", "edw", owner=owner, domain=domain)
        for up in built_from:
            upstream = f"file:{EXTRACTS}/{up}" if "/" in up else f"table:{up}"
            link(upstream, f"job:EDW_DAILY/{job}", "reads", "controlm")
            if "/" not in up:
                link(upstream, f"table:{name}", "feeds", "datahub", "dbql")
                upstream_job = next(row[2] for row in EDW_TABLES if row[0] == up)
                link(f"job:EDW_DAILY/{upstream_job}", f"job:EDW_DAILY/{job}", "triggers", "controlm")
        link(f"job:EDW_DAILY/{job}", f"table:{name}", "writes", "controlm")
    for table, job in EXPORTED:
        short = table.split(".")[1]
        load_job = next(row[2] for row in EDW_TABLES if row[0] == table)
        nodes[f"table:{table}"] = _named(nodes[f"table:{table}"], ("lineage-edw", TERADATA, table))
        add(f"job:EDW_EXPORTS/{job}", "job", "EDW", "edw", [("lineage-edw", "controlm", f"EDW_EXPORTS/{job}")], owner=otto)
        add(f"file:{EXPORTS}/{short}", "dataset", "EDW", "edw",
            [("lineage-edw", EXPORTS, short), ("lineage-edp", EXPORTS, short)], owner=otto)
        link(f"table:{table}", f"job:EDW_EXPORTS/{job}", "reads", "lineage-edw", "controlm")
        link(f"job:EDW_EXPORTS/{job}", f"file:{EXPORTS}/{short}", "writes", "lineage-edw", "controlm")
        link(f"job:EDW_DAILY/{load_job}", f"job:EDW_EXPORTS/{job}", "triggers", "controlm")
        raw = f"table:{EDP}.raw.edw_{short}"
        add(f"job:{COMPOSER}/edw_exports.load_{short}", "job", "EDP", "edp",
            [("lineage-edp", COMPOSER, f"edw_exports.load_{short}")], owner=nia)
        add(raw, "dataset", "EDP", "edp", [("lineage-edp", "bigquery", raw[6:]), ("dbt", "bigquery", raw[6:])],
            owner=nia, domain=nodes[f"table:{table}"].domain, reads_per_day=2)
        link(f"file:{EXPORTS}/{short}", f"job:{COMPOSER}/edw_exports.load_{short}", "reads", "lineage-edp")
        link(f"job:{COMPOSER}/edw_exports.load_{short}", raw, "writes", "lineage-edp")
    for name, built_from, reads, replaces in DBT_MODELS:
        key = f"table:{EDP}.{name}"
        owner = nia if name.startswith("staging.") else first("Analytics engineer")
        add(key, "dataset", "EDP", "edp", [("dbt", "bigquery", key[6:])], owner=owner, reads_per_day=reads,
            replaces=f"table:{replaces}" if replaces else "",
            domain=nodes[f"table:{replaces}"].domain if replaces else "")
        for up in built_from:
            link(f"table:{EDP}.{up}", key, "feeds", "dbt", "bigquery-jobs")
    for tool, name, table, system in DASHBOARDS:
        source_table = f"table:{EDP}.{table}" if system == "EDP" else f"table:{table}"
        add(f"dashboard:{tool}/{name}", "dashboard", "BI", "edp" if system == "EDP" else "edw")
        link(source_table, f"dashboard:{tool}/{name}", "reads", tool)
    for name, kind, team, system, builds, depends in _repos(repos):
        add(f"repo:{name}", "repo", system, team, owner=_lead(world, team), role=kind)
        for target in _built_by(nodes, builds):
            link(f"repo:{name}", target, "builds", "github")
        for dep in depends:
            link(f"repo:{name}", f"repo:{dep}", "depends_on", "github")
    for a, b in CALLS + _generated_calls(repos):
        link(f"repo:{a}", f"repo:{b}", "calls", "github")
    return Estate(world, repos, tuple(nodes.values()),
                  tuple(Edge(s, d, k, tuple(sorted(src))) for (s, d, k), src in edges.items()))


def _named(node: Node, name: tuple[str, str, str]) -> Node:
    return Node(**{**node.__dict__, "names": node.names + (name,)})


def _lead(world: company.World, team: str) -> str:
    """Who owns a team's repositories: its first leader, else its first member."""
    members = [p for p in world.people if p.team == team and p.active]
    return next((p.email for p in members if p.leads), members[0].email)


Repo = tuple[str, str, str, str, str, tuple[str, ...]]


def _repos(count: int) -> list[Repo]:
    """The core repositories, then generated ones up to `count`, each named only by its number, so a size never
    renames one; a generated service depends on the library generated before it, or retail-common."""
    out: list[Repo] = list(REPOS)
    library = "retail-common"
    for i in range(CORE_REPO_COUNT, count):
        kind, team = GENERATED_KINDS[i % len(GENERATED_KINDS)], GENERATED_TEAMS[i % len(GENERATED_TEAMS)]
        name = f"{team}-{kind}-{i:04d}"
        out.append((name, kind, team, f"{team} apps", "", (library,) if kind == "service" else ()))
        library = name if kind == "library" else library
    return out


def _generated_calls(count: int) -> tuple[tuple[str, str], ...]:
    """Every third generated service calls the service generated before it."""
    services = [r[0] for r in _repos(count)[CORE_REPO_COUNT:] if r[1] == "service"]
    return tuple((b, a) for i, (a, b) in enumerate(zip(services, services[1:])) if i % 3 == 0)


def _built_by(nodes: dict[str, Node], builds: str) -> list[str]:
    if builds in ("EDW_DAILY", "EDW_EXPORTS"):
        return sorted(k for k in nodes if k.startswith(f"job:{builds}/"))
    if builds == COMPOSER:
        return sorted(k for k in nodes if k.startswith(f"job:{COMPOSER}/"))
    if builds == "dbt":
        return sorted(k for k in nodes if k.startswith(f"table:{EDP}.") and ".raw." not in k)
    if builds in ("microstrategy", "looker"):
        return sorted(k for k in nodes if k.startswith(f"dashboard:{builds}/"))
    return []


# ------------------------------------------------------------------ the Atlas the loaded sources should make
AtlasKey = tuple[str, str, str]  # (kind, namespace, name), as an Atlas node is known


def name_in(node: Node, source: str) -> AtlasKey | None:
    found = next(((ns, nm) for s, ns, nm in node.names if s == source), None)
    return (node.kind, *found) if found else None


def atlas_view(estate: Estate) -> tuple[dict[AtlasKey, frozenset[str]], dict[tuple[AtlasKey, AtlasKey, str], frozenset[str]]]:
    """What the Atlas holds once the loaded sources are in: each node by the name each source uses, with the teams
    that reported it; each edge a loaded source reveals, between its names for the two ends, with its teams."""
    nodes: dict[AtlasKey, set[str]] = defaultdict(set)
    edges: dict[tuple[AtlasKey, AtlasKey, str], set[str]] = defaultdict(set)
    by_key = {n.key: n for n in estate.nodes}
    for n in estate.nodes:
        for source, ns, nm in n.names:
            nodes[(n.kind, ns, nm)].add(SOURCES[source].team)
    for e in estate.edges:
        for source in (s for s in e.sources if s in LOADED):
            a, b = name_in(by_key[e.src], source), name_in(by_key[e.dst], source)
            if a and b:  # problems() says when a loaded source doesn't name both ends
                edges[(a, b, e.kind)].add(SOURCES[source].team)
    return ({k: frozenset(v) for k, v in nodes.items()}, {k: frozenset(v) for k, v in edges.items()})


def visible(view, teams: set[str]) -> tuple[set[AtlasKey], set[tuple[AtlasKey, AtlasKey, str]]]:
    """What someone on `teams` sees: a node one of their teams reported, and an edge one of their teams reported
    between two nodes they see (invariant 15)."""
    nodes, edges = view
    seen = {k for k, t in nodes.items() if t & teams}
    return seen, {k for k, t in edges.items() if t & teams and k[0] in seen and k[1] in seen}


def downstream(edges: set[tuple[AtlasKey, AtlasKey, str]], start: AtlasKey, depth: int = 6) -> dict[AtlasKey, int]:
    """Impact over a set of edges, as the Atlas's walk computes it: each node downstream at its fewest hops."""
    out, frontier = {}, {start}
    for hop in range(1, depth + 1):
        frontier = {b for a, b, _ in edges if a in frontier and b != start and b not in out}
        out.update({b: hop for b in frontier})
        if not frontier:
            break
    return out


# ------------------------------------------------------------------ what each loaded source sends
def lineage_events(estate: Estate, source: str) -> list[dict]:
    """OpenLineage run events, one per job the source reports: what it read and what it wrote."""
    by_key = {n.key: n for n in estate.nodes}
    mine = [e for e in estate.edges if source in e.sources]

    def node(key: str) -> dict:
        _, namespace, name = name_in(by_key[key], source) or ("", "", "")
        return {"namespace": namespace, "name": name}
    events = []
    for job in (n for n in estate.nodes if n.kind == "job" and name_in(n, source)):
        events.append({"job": node(job.key),
                       "inputs": [node(e.src) for e in mine if e.dst == job.key and e.kind == "reads"],
                       "outputs": [node(e.dst) for e in mine if e.src == job.key and e.kind == "writes"]})
    return events


def dbt_manifest(estate: Estate) -> dict:
    """The edp-dbt project's manifest.json: its raw tables as sources, its staging models and marts as models."""
    by_key = {n.key: n for n in estate.nodes}
    feeds = [e for e in estate.edges if "dbt" in e.sources]
    uid = lambda n: (f"model.edp.{n.key.rsplit('.', 1)[1]}" if any(e.dst == n.key for e in feeds)
                     else f"source.edp.raw.{n.key.rsplit('.', 1)[1]}")
    nodes, sources = {}, {}
    for n in (n for n in estate.nodes if name_in(n, "dbt")):
        _, schema, table = n.key[len("table:"):].split(".")
        spec = {"resource_type": "model" if uid(n).startswith("model.") else "source", "package_name": "edp", "name": table,
                "database": EDP, "schema": schema, "description": f"{table}, in the EDP's {schema} layer.",
                "meta": {"owner": n.owner}, "tags": [n.domain] if n.domain else []}
        if spec["resource_type"] == "model":
            spec |= {"alias": table, "config": {"materialized": "table" if schema == "marts" else "view"},
                     "original_file_path": f"models/{schema}/{table}.sql",
                     "depends_on": {"nodes": [uid(by_key[e.src]) for e in feeds if e.dst == n.key]}}
            nodes[uid(n)] = spec
        else:
            sources[uid(n)] = spec | {"identifier": table}
    return {"metadata": {"adapter_type": "bigquery"}, "nodes": nodes, "sources": sources}


def datahub_tables(estate: Estate) -> list[dict]:
    """The EDW's tables as DataHub holds them (the fake's shape): each with its owner and the tables upstream of it."""
    upstream = defaultdict(list)
    for e in (e for e in estate.edges if "datahub" in e.sources):
        upstream[e.dst].append(e.src[len("table:"):])
    return [{"name": n.key[len("table:"):], "description": f"{n.key[len('table:'):]}: {n.domain}, in the EDW.", "columns": [],
             "users": [n.owner], "upstream": sorted(upstream[n.key])} for n in estate.nodes if name_in(n, "datahub")]


# ------------------------------------------------------------------ checks the truth holds itself to
def problems(estate: Estate) -> list[str]:
    """Everything wrong with the estate's truth: an edge to a node that isn't there, a source no one defined, a loaded
    source revealing an edge between nodes it doesn't name, an Atlas edge kind the loaders wouldn't record, a wave that
    moves a table before what it's built from, an unused table that migrates (or a used one that retires), and a page
    naming something the estate doesn't hold, or not naming it at all."""
    keys = {n.key: n for n in estate.nodes}
    out = [f"{e.src} {e.kind} {e.dst}: no such node" for e in estate.edges if e.src not in keys or e.dst not in keys]
    out += [f"{e.src} {e.kind} {e.dst}: unknown source {s}" for e in estate.edges for s in e.sources if s not in SOURCES]
    if out:
        return out
    for e in estate.edges:
        for s in (s for s in e.sources if s in LOADED):
            a, b = name_in(keys[e.src], s), name_in(keys[e.dst], s)
            if not a or not b:
                out.append(f"{e.src} {e.kind} {e.dst}: {s} reveals it but doesn't name both ends")
            elif e.kind != {("dataset", "job"): "reads", ("job", "dataset"): "writes"}.get((a[0], b[0]), "feeds"):
                out.append(f"{e.src} {e.kind} {e.dst}: the Atlas records {s}'s edge as another kind")
    for e in (e for e in estate.edges if e.kind == "feeds" and e.src.startswith("table:edw")):
        up, down = keys[e.src].wave, keys[e.dst].wave
        if down != "retire" and (up == "retire" or up > down):
            out.append(f"{e.dst} migrates in wave {down}, before {e.src} (wave {up}) that it's built from")
    for n in (n for n in estate.nodes if n.wave):
        if (n.wave == "retire") != (n.reads_per_day == 0):
            out.append(f"{n.key}: {n.reads_per_day} reads a day, wave {n.wave}")
    for name, named in MENTIONS.items():
        doc = estate.world.doc(name)
        text = " ".join([doc.title, doc.text, *(m[2] for m in doc.messages)]).lower()
        for key in named:
            short = key.split(":", 1)[1].rsplit("/", 1)[-1].rsplit(".", 1)[-1].lower()
            if key not in keys:
                out.append(f"{name} names {key}, which the estate doesn't hold")
            elif short not in text:
                out.append(f"{name} is said to name {key}, but its text never says {short}")
    return out


def truth(estate: Estate) -> dict:
    by_reads = sorted((n for n in estate.nodes if n.reads_per_day is not None), key=lambda n: (-(n.reads_per_day or 0), n.key))
    return {
        "company": company.COMPANY, "seed": estate.world.seed, "stores": estate.world.stores, "repos": estate.repos,
        "sources": {s: {"team": src.team, "waits_on": src.waits_on} for s, src in SOURCES.items()},
        "nodes": [{**n.__dict__, "names": [list(x) for x in n.names]} for n in estate.nodes],
        "edges": [e.__dict__ for e in estate.edges],
        "usage_rank": [n.key for n in by_reads],
        "unused": [n.key for n in by_reads if n.reads_per_day == 0],
        "systems": {s: sorted(n.key for n in estate.nodes if n.system == s) for s in sorted({n.system for n in estate.nodes})},
        "waves": {n.key: n.wave for n in estate.nodes if n.wave},
        "replaces": {n.key: n.replaces for n in estate.nodes if n.replaces},
        "mentions": MENTIONS,
    }


def main(argv: list[str]) -> int:
    def arg(name: str, default: int) -> int:
        return int(argv[argv.index(name) + 1]) if name in argv else default
    estate = build(company.build(arg("--seed", 7), arg("--stores", 6)), arg("--repos", DEFAULT_REPOS))
    wrong = problems(estate)
    if wrong:
        print("\n".join(wrong), file=sys.stderr)
        return 1
    print(json.dumps(truth(estate), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
