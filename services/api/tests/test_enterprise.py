"""The synthetic enterprise (F10b): the same retailer and size always make the same estate, and a size never renames a
node; its truth is consistent at every size; each loaded source sends exactly the edges the truth says it reveals,
by the names it uses (dbt through the real manifest parser); and who sees what follows the teams that reported it."""
import pytest

from kl import company, enterprise, manifests


@pytest.fixture(scope="module")
def estate():
    return enterprise.build(company.build())


def test_the_same_retailer_and_size_build_the_same_estate_and_a_size_never_renames_a_node(estate):
    again = enterprise.build(company.build())
    assert again.nodes == estate.nodes and again.edges == estate.edges
    bigger = {n.key for n in enterprise.build(company.build(), 2000).nodes}
    assert {n.key for n in estate.nodes} <= bigger and len(bigger) == len(estate.nodes) + 2000 - enterprise.DEFAULT_REPOS


@pytest.mark.parametrize("repos", [enterprise.CORE_REPO_COUNT, 40, 2000])
def test_the_truth_is_consistent_at_every_size(repos):
    assert enterprise.problems(enterprise.build(company.build(stores=15), repos)) == []


def test_fewer_repositories_than_the_core_is_refused():
    with pytest.raises(ValueError):
        enterprise.build(company.build(), enterprise.CORE_REPO_COUNT - 1)


def revealed(estate, source):
    by_key = {n.key: n for n in estate.nodes}
    return {(enterprise.name_in(by_key[e.src], source), enterprise.name_in(by_key[e.dst], source), e.kind)
            for e in estate.edges if source in e.sources}


@pytest.mark.parametrize("source", ["lineage-edw", "lineage-edp"])
def test_each_teams_openlineage_events_carry_exactly_the_edges_it_reveals(estate, source):
    sent = set()
    for event in enterprise.lineage_events(estate, source):
        job = ("job", event["job"]["namespace"], event["job"]["name"])
        sent |= {(("dataset", d["namespace"], d["name"]), job, "reads") for d in event["inputs"]}
        sent |= {(job, ("dataset", d["namespace"], d["name"]), "writes") for d in event["outputs"]}
    assert sent == revealed(estate, source) and sent


def test_the_dbt_manifest_parses_into_exactly_the_models_and_edges_dbt_reveals(estate):
    entries, lineage = manifests.dbt(enterprise.dbt_manifest(estate))
    assert {("dataset", e.namespace, e.name) for e in entries} == {enterprise.name_in(n, "dbt") for n in estate.nodes if enterprise.name_in(n, "dbt")}
    assert {(("dataset", *a), ("dataset", *b), "feeds") for a, b in lineage} == revealed(estate, "dbt")
    owners = {e.name: e.owners for e in entries}
    assert owners["acme-edp.staging.stg_sales"] == [estate.world.person("nia").email]


def test_datahub_holds_every_edw_table_with_its_owner_and_upstream(estate):
    tables = {t["name"]: t for t in enterprise.datahub_tables(estate)}
    assert len(tables) == len(enterprise.EDW_TABLES)
    assert tables["edw.sales_txn"]["upstream"] == ["edw_stg.oms_orders", "edw_stg.pos_sales"]
    assert {(("dataset", "teradata", up), ("dataset", "teradata", name), "feeds")
            for name, t in tables.items() for up in t["upstream"]} == revealed(estate, "datahub")


def test_who_sees_what_follows_the_teams_that_reported_it(estate):
    view = enterprise.atlas_view(estate)
    edp_nodes, edp_edges = enterprise.visible(view, {"edp"})
    edw_nodes, edw_edges = enterprise.visible(view, {"edw"})
    export = ("dataset", enterprise.EXPORTS, "sales_txn")
    assert view[0][export] == {"edw", "edp"}                           # the hand-off both teams report
    assert export in edp_nodes and export in edw_nodes
    assert {k for k in edp_edges if export in k[:2]} == {(export, ("job", enterprise.COMPOSER, "edw_exports.load_sales_txn"), "reads")}
    assert {k for k in edw_edges if export in k[:2]} == {(("job", "controlm", "EDW_EXPORTS/EXPORT_SALES_TXN"), export, "writes")}
    assert ("dataset", "teradata", "edw.sales_txn") in edw_nodes and ("dataset", "teradata", "edw.sales_txn") not in edp_nodes
    assert enterprise.visible(view, set()) == (set(), set())
    impact = enterprise.downstream(edp_edges, export)
    assert impact[("dataset", "bigquery", "acme-edp.marts.daily_sales")] == 5 and enterprise.downstream(edw_edges, export) == {}


def test_an_edw_table_has_two_names_until_o13_resolves_them(estate):
    names = {(s, ns) for s, ns, _ in estate.node("table:edw.sales_txn").names}
    assert names == {("datahub", "teradata"), ("lineage-edw", enterprise.TERADATA)}


def test_the_truth_flags_a_page_naming_what_the_estate_lacks(estate, monkeypatch):
    monkeypatch.setitem(enterprise.MENTIONS, "DATA-31", ("table:edw.no_such_table",))
    assert enterprise.problems(estate) == ["DATA-31 names table:edw.no_such_table, which the estate doesn't hold"]


def test_the_truth_ranks_usage_and_retires_only_what_nobody_reads(estate):
    truth = enterprise.truth(estate)
    assert truth["usage_rank"][0] == "table:edw_rpt.daily_sales"
    assert set(truth["unused"]) == {"table:edw_rpt.legacy_returns", "table:edw_rpt.promo_lift_2019"}
    assert truth["waves"]["table:edw.inv_daily"] == "2" and truth["replaces"]["table:acme-edp.marts.daily_sales"] == "table:edw_rpt.daily_sales"
