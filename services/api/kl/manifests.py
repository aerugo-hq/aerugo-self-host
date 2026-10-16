"""dbt and Dataform manifests from CI (S7): a pipeline (J1) pushes a project's manifest, and each model, source, or
table becomes a document (its description, columns, tags, owners, and where it's defined) plus an asset in the
pipeline's team, with lineage from what it depends on. Metadata only: a manifest holds no data. A manifest carries no
permissions, so its documents carry the readers the pipeline names, each holding a role on the team, as every pushed
document does. A model gone from the next manifest leaves."""
from .catalogs import Entry, markdown_of, owners_of, record_assets
from .pipelines import apply_push

MAX_NODES = 20000
DBT_KINDS = {"model", "seed", "snapshot", "source"}


def _relation(*parts) -> str:
    return ".".join(str(p) for p in parts if p).lower()


def dbt(manifest: dict) -> tuple[list[Entry], list]:
    """A dbt manifest.json: its models, seeds, snapshots, and sources, in the warehouse's namespace, and their lineage."""
    namespace = str((manifest.get("metadata") or {}).get("adapter_type") or "dbt").lower()[:200]
    nodes = {**(manifest.get("nodes") or {}), **(manifest.get("sources") or {})}
    if len(nodes) > MAX_NODES:
        raise ValueError(f"more than {MAX_NODES} nodes in one manifest")
    names, entries, lineage = {}, [], []
    for uid, n in nodes.items():
        if n.get("resource_type") in DBT_KINDS:
            names[uid] = _relation(n.get("database"), n.get("schema"), n.get("alias") or n.get("identifier") or n.get("name"))
    for uid, n in nodes.items():
        if uid not in names:
            continue
        config, meta = n.get("config") or {}, n.get("meta") or {}
        owners = owners_of([meta.get("owner"), (config.get("meta") or {}).get("owner"), *(meta.get("owners") or [])])
        facts = [("dbt", f"{n['resource_type']} {n.get('package_name') or ''}.{n.get('name') or ''}".strip())]
        if config.get("materialized"):
            facts.append(("Materialized as", str(config["materialized"])))
        if n.get("tags"):
            facts.append(("Tags", ", ".join(map(str, n["tags"]))))
        if n.get("original_file_path"):
            facts.append(("Defined in", str(n["original_file_path"])))
        columns = [(str(c.get("name") or ""), str(c.get("data_type") or ""), str(c.get("description") or ""))
                   for c in (n.get("columns") or {}).values()]
        entries.append(Entry(key=names[uid], namespace=namespace, name=names[uid], kind=n["resource_type"],
                             description=str(n.get("description") or ""), columns=columns, facts=facts, owners=owners,
                             path=str(n.get("package_name") or "")))
        for dep in (n.get("depends_on") or {}).get("nodes") or []:
            if dep in names:
                lineage.append(((namespace, names[dep]), (namespace, names[uid])))
    return entries, lineage


def dataform(graph: dict) -> tuple[list[Entry], list]:
    """A Dataform compiled graph (dataform compile --json): its tables and declarations in BigQuery, and their lineage."""
    actions = [(a, False) for a in graph.get("tables") or []] + [(a, True) for a in graph.get("declarations") or []]
    if len(actions) > MAX_NODES:
        raise ValueError(f"more than {MAX_NODES} actions in one graph")
    entries, lineage = [], []
    for a, declared in actions:
        t = a.get("target") or {}
        name = _relation(t.get("database"), t.get("schema"), t.get("name"))
        if not name:
            continue
        d = a.get("actionDescriptor") or {}
        columns = [(".".join(map(str, c.get("path") or [])), "", str(c.get("description") or "")) for c in d.get("columns") or []]
        facts = [("Dataform", "declaration" if declared else str(a.get("type") or "table"))]
        if a.get("tags"):
            facts.append(("Tags", ", ".join(map(str, a["tags"]))))
        entries.append(Entry(key=name, namespace="bigquery", name=name, kind=str(a.get("type") or "table"),
                             description=str(d.get("description") or ""), columns=columns, facts=facts))
        for dep in a.get("dependencyTargets") or []:
            source = _relation(dep.get("database"), dep.get("schema"), dep.get("name"))
            if source:
                lineage.append((("bigquery", source), ("bigquery", name)))
    return entries, lineage


def apply_manifest(conn, push: dict) -> dict:
    """A staged manifest push, as kl_ingest: its entries as the pipeline's documents (a model gone since the last
    manifest of this project is deleted), then the team's assets, lineage, and owners."""
    body = push["payload"]
    project = body["project"]
    entries, lineage = (dbt if body["kind"] == "dbt" else dataform)(body["manifest"])
    prefix = f"manifest/{project}/"
    upserts = [{"key": prefix + e.name, "title": e.name, "uri": e.uri or f"{e.namespace}:{e.name}", "markdown": markdown_of(e),
                "classification": body["classification"], "readers": body["readers"]} for e in entries]
    held = {r["source_path"] for r in conn.execute(
        "SELECT source_path FROM documents WHERE container_ref = %s AND source_path LIKE %s",
        (f"pipeline:{push['pipeline_id']}", prefix.replace("%", "") + "%")).fetchall()}
    result = apply_push(conn, {**push, "payload": {"upserts": upserts, "deletes": sorted(held - {u["key"] for u in upserts})}})
    result.update(record_assets(conn, push["org_id"], push["workspace_id"], entries, lineage, f"pipeline:{push['pipeline_id']}:{project}"))
    return result
