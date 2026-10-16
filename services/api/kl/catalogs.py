"""Data catalogs (S7): metadata only, never data. Each catalog entry (a table, a view, a model) becomes two things:

  - a document with its description, columns, facts (its system, tags, glossary terms), and owners, readable by whom
    the catalog lets read the entry, mirrored and failing closed (an entry nobody in Aerugo may read isn't a document);
  - an asset, with its lineage edges and owners, for the team the catalog is connected to, as K3's pushed lineage is:
    the Map and impact walks show them to that team's members, and who to ask about a page that describes the asset
    includes its owners.

Each catalog's client (Dataplex, Glue, Unity Catalog, Purview, DataHub) lists entries and lineage; dbt and Dataform
manifests arrive from CI through a pipeline (`apply_manifest`). This module turns either into documents and assets."""
import hashlib
import re
from dataclasses import dataclass, field

from .markdown import parse as parse_markdown
from .sync_github import team_for
from .sync_items import Item, record_identities, sync_container

EMAIL = re.compile(r"^[a-z0-9._%+-]{1,64}@[a-z0-9-]{1,63}(\.[a-z0-9-]{1,63})*\.[a-z]{2,24}$")
MAX_ENTRIES = 20000  # per container per sync: past this the sync fails, rather than reading the rest as gone
NAME = re.compile(r"^[A-Za-z0-9_.:/@$-]{1,300}$")  # an asset's name, as a catalog writes it


@dataclass
class Entry:
    key: str                  # unique across sources, like dataplex-<hash>
    namespace: str            # the asset's namespace, as lineage names it: bigquery, glue, unity, snowflake
    name: str                 # its fully qualified name in that namespace, like proj.dataset.table
    kind: str                 # table, view, model, source
    description: str = ""
    columns: list[tuple[str, str, str]] = field(default_factory=list)  # (name, type, description)
    facts: list[tuple[str, str]] = field(default_factory=list)         # (label, value): system, tags, terms
    owners: list[str] = field(default_factory=list)                    # emails
    readers: list[str] = field(default_factory=list)                   # mirrored from the catalog
    uri: str = ""
    version: str = ""
    path: str = ""


def entry_key(source: str, name: str) -> str:
    """A document key for a catalog's entry: names hold any character a catalog allows, so the key is their hash."""
    return f"{source}-{hashlib.sha256(name.encode()).hexdigest()[:32]}"


def owners_of(values) -> list[str]:
    """The emails among a catalog's owner entries; a name that isn't an email can't be told apart, so it's dropped."""
    return sorted({str(v).strip().lower() for v in values or [] if EMAIL.fullmatch(str(v).strip().lower())})


def markdown_of(e: Entry) -> str:
    """An entry as a page: its name and description, facts, columns as a table, and owners."""
    lines = [f"# {e.name}", "", f"A {e.kind} in {e.namespace}." + (f" {e.description.strip()}" if e.description.strip() else "")]
    if e.facts:
        lines += ["", "## Facts", ""] + [f"- {label}: {value}" for label, value in e.facts]
    if e.columns:
        lines += ["", "## Columns", "", "| Column | Type | Description |", "|---|---|---|"]
        lines += [f"| {n} | {t or ''} | {(d or '').replace('|', '/').strip()} |" for n, t, d in e.columns[:500]]
    if e.owners:
        lines += ["", "## Owners", "", ", ".join(e.owners)]
    return "\n".join(lines)


def sections_of(e: Entry) -> tuple[str, list[tuple[str | None, str]]]:
    return parse_markdown(markdown_of(e), fallback_title=e.name)


def record_assets(conn, org_id, team, entries: list[Entry], lineage: list[tuple[tuple[str, str], tuple[str, str]]],
                  source: str) -> dict:
    """The entries' assets, lineage, and owners for a team (K3's tables): upserted, their owners from this source
    replaced. Assets aren't removed when an entry goes, as pushed lineage's aren't: their last_seen says when."""
    ids: dict[tuple[str, str], str] = {}

    def asset(namespace: str, name: str) -> str | None:
        if not NAME.fullmatch(name) or not 1 <= len(namespace) <= 200:
            return None
        if (namespace, name) not in ids:
            ids[(namespace, name)] = str(conn.execute(
                """INSERT INTO assets (org_id, workspace_id, kind, namespace, name) VALUES (%s, %s, 'dataset', %s, %s)
                   ON CONFLICT (workspace_id, kind, namespace, name) DO UPDATE SET last_seen = now() RETURNING id""",
                (org_id, team, namespace, name)).fetchone()["id"])
        return ids[(namespace, name)]
    edges = 0
    with conn.transaction():
        for e in entries:
            asset(e.namespace, e.name)
        for (sns, sname), (tns, tname) in lineage:
            s, t = asset(sns, sname), asset(tns, tname)
            if s and t and s != t:
                conn.execute("""INSERT INTO lineage_edges (org_id, workspace_id, source_id, target_id) VALUES (%s, %s, %s, %s)
                                ON CONFLICT (source_id, target_id) DO UPDATE SET last_seen = now()""", (org_id, team, s, t))
                edges += 1
        owned = [(ids[(e.namespace, e.name)], o) for e in entries if (e.namespace, e.name) in ids for o in e.owners]
        conn.execute("DELETE FROM asset_owners WHERE source = %s AND workspace_id = %s", (source, team))
        conn.cursor().executemany("INSERT INTO asset_owners (org_id, workspace_id, asset_id, email, source) VALUES (%s, %s, %s, %s, %s) "
                                  "ON CONFLICT DO NOTHING", [(org_id, team, a, o, source) for a, o in owned])
    return {"assets": len(ids), "edges": edges, "owners": len(owned)}


def sync_catalog(conn, connection: dict, client, container: str, source_kind: str, identities: str | None) -> dict:
    """One of a catalog connection's containers: its client's entries and lineage, and the accounts it named recorded
    for identity matching under `identities` (ext:<identities>.<email>)."""
    entries, lineage, people = client.entries(container)
    if identities and people:
        record_identities(conn, connection["org_id"], identities, people)
    return sync_entries(conn, connection, container, source_kind, entries, lineage)


def sync_entries(conn, connection: dict, container: str, source_kind: str, entries: list[Entry],
                 lineage: list[tuple[tuple[str, str], tuple[str, str]]]) -> dict:
    """A catalog connection's container: its entries as documents (with their own readers) and as the team's assets."""
    if len(entries) > MAX_ENTRIES:
        raise RuntimeError(f"more than {MAX_ENTRIES} entries in {container}")
    items = [Item(key=e.key, uri=e.uri or f"{e.namespace}:{e.name}", version=e.version or hashlib.sha256(markdown_of(e).encode()).hexdigest(),
                  title=e.name, path=e.path, acl=e.readers, sections=lambda e=e: sections_of(e)) for e in entries]
    result = sync_container(conn, connection, container, source_kind, items)
    result.update(record_assets(conn, connection["org_id"], team_for(connection, container), entries, lineage, str(connection["id"])))
    return result
