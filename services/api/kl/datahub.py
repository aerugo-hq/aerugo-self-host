"""DataHub (S7): the datasets of chosen platforms (their descriptions, columns, owners, tags, glossary terms, and upstream
lineage) through its GraphQL API, with an access token for a DataHub user made for Aerugo. Metadata only. Each
company runs its own DataHub, so its address must be one the deployment allows (`KL_DATAHUB_API_BASES`).

DataHub decides who may view metadata through its policies, which an ordinary token can't read, so readers fail
closed to the people DataHub names as a dataset's owners, as `ext:datahub.<email>` (a group owner reaches no one).
Everyone else who may view it in DataHub just doesn't find it in Aerugo."""
import hashlib
import re

import httpx

from . import config
from .catalogs import Entry, owners_of
from .connectors import Container, Health
from .principals import is_valid
from .vault import vault

PLATFORM = re.compile(r"^[a-z0-9_-]{1,64}$")
URN = re.compile(r"^urn:li:dataset:\(urn:li:dataPlatform:([a-z0-9_-]{1,64}),([^,()]{1,500}),([A-Z]{1,20})\)$")
PAGE = 100
MAX_PAGES = 200
QUERY = """query aerugo($input: ScrollAcrossEntitiesInput!) {
  scrollAcrossEntities(input: $input) {
    nextScrollId
    searchResults { entity { urn ... on Dataset {
      properties { description }
      editableProperties { description }
      schemaMetadata { fields { fieldPath nativeDataType description } }
      ownership { owners { owner { ... on CorpUser { properties { email } } } } }
      tags { tags { tag { properties { name } } } }
      glossaryTerms { terms { term { properties { name } } } }
      upstream: lineage(input: {direction: UPSTREAM, start: 0, count: 100}) { relationships { entity { urn } } }
    } } }
  }
}"""


def allowed_base(api_base: str) -> bool:
    return api_base.rstrip("/") in config.DATAHUB_API_BASES


class DataHubClient:
    def __init__(self, api_base: str, token: str, platforms: list[str]):
        if not allowed_base(api_base):
            raise ValueError("that DataHub address isn't allowed here")
        self.base, self._token = api_base.rstrip("/"), token
        self.platforms = [p for p in platforms if PLATFORM.fullmatch(p)]

    def _query(self, variables: dict) -> dict:
        r = httpx.post(f"{self.base}/api/graphql", json={"query": QUERY, "variables": variables}, timeout=60,
                       headers={"Authorization": f"Bearer {self._token}"})
        r.raise_for_status()
        body = r.json()
        if body.get("errors"):  # a GraphQL error is a failed sync, never an empty listing
            raise RuntimeError(f"DataHub refused the query: {str(body['errors'][0].get('message'))[:200]}")
        return body["data"]["scrollAcrossEntities"]

    def test(self) -> Health:
        try:
            self._query({"input": {"types": ["DATASET"], "query": "*", "count": 1}})
            return Health(True, "connected")
        except (httpx.HTTPError, RuntimeError) as e:
            return Health(False, f"DataHub refused the token ({type(e).__name__})")

    def discover(self) -> list[Container]:
        return [Container(external_id=p, kind="platform", visibility=None, default_ref=None, name=p) for p in self.platforms]

    def entries(self, platform: str) -> tuple[list[Entry], list, dict[str, dict]]:
        found, lineage, people, scroll = [], [], {}, None
        for _ in range(MAX_PAGES):
            page = self._query({"input": {"types": ["DATASET"], "query": "*", "count": PAGE, "scrollId": scroll,
                                          "orFilters": [{"and": [{"field": "platform", "values": [f"urn:li:dataPlatform:{platform}"]}]}]}})
            for hit in page.get("searchResults") or []:
                d = hit.get("entity") or {}
                m = URN.fullmatch(str(d.get("urn") or ""))
                if not m or m[1] != platform:
                    continue
                name = m[2].lower()
                owners = owners_of(((o.get("owner") or {}).get("properties") or {}).get("email")
                                   for o in (d.get("ownership") or {}).get("owners") or [])
                people.update({e: {"email": e} for e in owners})
                tags = [str(((t.get("tag") or {}).get("properties") or {}).get("name") or "") for t in (d.get("tags") or {}).get("tags") or []]
                terms = [str(((t.get("term") or {}).get("properties") or {}).get("name") or "") for t in (d.get("glossaryTerms") or {}).get("terms") or []]
                facts = [("Platform", platform), ("Environment", m[3])] + [("Tag", t) for t in tags if t] + [("Glossary term", t) for t in terms if t]
                found.append(Entry(key=f"datahub-{hashlib.sha256(d['urn'].encode()).hexdigest()[:32]}", namespace=platform, name=name, kind="dataset",
                                   description=str((d.get("editableProperties") or {}).get("description") or (d.get("properties") or {}).get("description") or ""),
                                   columns=[(str(f.get("fieldPath") or ""), str(f.get("nativeDataType") or ""), str(f.get("description") or ""))
                                            for f in (d.get("schemaMetadata") or {}).get("fields") or []],
                                   facts=facts, owners=owners, readers=sorted(p for e in owners if is_valid(p := f"ext:datahub.{e}")),
                                   uri=f"{self.base}/dataset/{d['urn']}", path=platform))
                for rel in (d.get("upstream") or {}).get("relationships") or []:
                    up = URN.fullmatch(str((rel.get("entity") or {}).get("urn") or ""))
                    if up:
                        lineage.append(((up[1], up[2].lower()), (platform, name)))
            scroll = page.get("nextScrollId")
            if not scroll:
                return found, lineage, people
        raise RuntimeError(f"DataHub's listing of {platform} ran past {MAX_PAGES} pages")


def client_for(conn, connection: dict) -> DataHubClient:
    cfg = connection["config"] or {}
    return DataHubClient(cfg["api_base"], vault.get(connection["credential_ref"])["token"], cfg.get("platforms") or [])
