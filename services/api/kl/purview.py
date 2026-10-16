"""Microsoft Purview's Data Map (S7): the assets in chosen collections (tables, views, files), their columns,
descriptions, owners and experts, and lineage, read by an Entra app registration signing in with its certificate, as
Microsoft 365 does (`directory.GraphClient`, for Purview's own audience). Metadata only.

Readers mirror who Purview lets read a collection's metadata, failing closed: the people and Entra groups its policy
names as data readers or data curators of that very collection (`READ_ROLES`; what a child inherits from its parent
isn't followed, so the readers are only ever fewer). Purview names them by Entra object id, so a person counts once an
Entra directory (S3) has listed them (`directory_people.external_id`), as `ext:microsoft.<email>`, and a group through
its members (`directory_groups.group_id`). Without an Entra directory, nobody reads them."""
import re

import httpx

from . import config
from .catalogs import Entry, owners_of
from .connectors import Container, Health
from .directory import GraphClient, ListingCutShort
from .microsoft365 import entra_groups
from .principals import is_valid
from .vault import vault

PURVIEW_HOST = re.compile(r"^https://[a-z0-9][a-z0-9-]{1,61}[a-z0-9]\.purview\.azure\.com$")
COLLECTION = re.compile(r"^[A-Za-z0-9_-]{1,36}$")
GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
API = {"api-version": "2023-09-01"}
POLICY_API = {"api-version": "2021-07-01"}
READ_ROLES = ("purviewmetadatarole_builtin_data-reader:", "purviewmetadatarole_builtin_data-curator:")
MAX_PAGES = 200

PEOPLE_SQL = """SELECT DISTINCT p.external_id, p.email FROM directory_people p JOIN connections c ON c.id = p.connection_id
WHERE c.kind = 'directory' AND c.config->>'provider' = 'entra' AND c.status <> 'disabled' AND p.active AND p.external_id IS NOT NULL"""


def allowed_base(api_base: str) -> bool:
    base = api_base.rstrip("/")
    return bool(PURVIEW_HOST.fullmatch(base)) or base in config.PURVIEW_API_BASES


class PurviewClient(GraphClient):
    scope = "https://purview.azure.net/.default"

    def allowed(self, api_base: str) -> bool:
        return allowed_base(api_base)

    def microsofts(self) -> bool:
        return bool(PURVIEW_HOST.fullmatch(self.base))

    def __init__(self, api_base: str, tenant_id: str, client_id: str, certificate: dict, collections: list[str],
                 people: dict[str, str], groups: dict[str, list[str]]):
        super().__init__(api_base, tenant_id, client_id, certificate)
        self.collections = [c for c in collections if COLLECTION.fullmatch(c)]
        self.people, self.groups = people, groups  # Entra object ids to emails; group ids to members' emails
        self._policies: dict[str, list[str]] | None = None

    def test(self) -> Health:
        try:
            self._access_token()
            return Health(True, "connected")
        except httpx.HTTPError as e:
            return Health(False, f"Microsoft refused the app ({type(e).__name__}): check the tenant, the app, and its certificate")

    def discover(self) -> list[Container]:
        return [Container(external_id=c, kind="collection", visibility=None, default_ref=None, name=c) for c in self.collections]

    def readers(self, collection: str) -> list[str]:
        """The people a collection's own policy lets read its metadata, by Entra object id, as their Entra emails."""
        if self._policies is None:
            self._policies = {}
            for policy in self._get(f"{self.base}/policystore/metadataPolicies", POLICY_API).get("values") or []:
                props = policy.get("properties") or {}
                name = str((props.get("collection") or {}).get("referenceName") or "")
                ids = self._policies.setdefault(name, [])
                for rule in props.get("attributeRules") or []:
                    if not str(rule.get("id") or "").startswith(READ_ROLES):
                        continue
                    for clause in rule.get("dnfCondition") or []:
                        for cond in clause:
                            if cond.get("attributeName") in ("principal.microsoft.id", "principal.microsoft.groups"):
                                ids += [str(v).lower() for v in cond.get("attributeValueIncludedIn") or [] if GUID.fullmatch(str(v).lower())]
        emails = set()
        for oid in self._policies.get(collection, []):
            emails |= {self.people[oid]} if oid in self.people else set(self.groups.get(oid, []))
        return sorted(p for e in emails if is_valid(p := f"ext:microsoft.{e}"))

    def _search(self, collection: str) -> list[dict]:
        out, token = [], None
        for _ in range(MAX_PAGES):
            body = {"keywords": None, "limit": 1000, "filter": {"collectionId": collection}, **({"continuationToken": token} if token else {})}
            page = self._call("POST", f"{self.base}/datamap/api/search/query", API, body=body)
            out += [h for h in page.get("value") or [] if GUID.fullmatch(str(h.get("id") or ""))]
            token = page.get("continuationToken")
            if not token:
                return out
        raise ListingCutShort(f"Purview's search of {collection} ran past {MAX_PAGES} pages")

    def entries(self, collection: str) -> tuple[list[Entry], list, dict[str, dict]]:
        found, lineage, readers = [], [], self.readers(collection)
        for hit in self._search(collection):
            body = self._get(f"{self.base}/datamap/api/atlas/v2/entity/guid/{hit['id']}", API)
            e, referred = body.get("entity") or {}, body.get("referredEntities") or {}
            attrs = e.get("attributes") or {}
            if str(e.get("typeName") or "").endswith("process"):
                continue
            name = str(attrs.get("qualifiedName") or "")
            columns = [(str(c.get("attributes", {}).get("name") or ""), str(c.get("attributes", {}).get("data_type") or ""),
                        str(c.get("attributes", {}).get("description") or ""))
                       for ref in (e.get("relationshipAttributes") or {}).get("columns") or []
                       if (c := referred.get(str(ref.get("guid") or ""))) is not None]
            contacts = e.get("contacts") or {}
            owners = owners_of(self.people.get(str(c.get("id") or "").lower()) for c in (contacts.get("Owner") or []) + (contacts.get("Expert") or []))
            found.append(Entry(key=f"purview-{hit['id']}", namespace="purview", name=name, kind=str(e.get("typeName") or "asset"),
                               description=str(attrs.get("userDescription") or attrs.get("description") or ""), columns=columns,
                               facts=[("Collection", collection), ("Type", str(e.get("typeName") or ""))], owners=owners, readers=readers,
                               uri=f"{self.base}/datamap/api/atlas/v2/entity/guid/{hit['id']}", version=str(e.get("updateTime") or ""),
                               path=collection))
            graph = self._get(f"{self.base}/datamap/api/atlas/v2/lineage/{hit['id']}", {**API, "direction": "INPUT", "depth": 2})
            for guid, node in (graph.get("guidEntityMap") or {}).items():
                upstream = str((node.get("attributes") or {}).get("qualifiedName") or "")
                if guid != hit["id"] and upstream and not str(node.get("typeName") or "").endswith("process"):
                    lineage.append((("purview", upstream), ("purview", name)))
        return found, lineage, {}


def client_for(conn, connection: dict) -> PurviewClient:
    cfg = connection["config"] or {}
    people = {r["external_id"]: r["email"] for r in conn.execute(PEOPLE_SQL).fetchall()}
    return PurviewClient(cfg["api_base"], cfg["tenant_id"], cfg["client_id"], vault.get(connection["credential_ref"])["certificate"],
                         cfg.get("collections") or [], people, entra_groups(conn))
