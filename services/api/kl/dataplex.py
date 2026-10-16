"""Dataplex Universal Catalog (S7): a project's catalog entries, their aspects (schema, contacts), and lineage from the
Data Lineage API, read keyless as the runner's own identity (its workload identity on GCP, or its own key file
elsewhere), so only an org admin connects it. Metadata only. A BigQuery table's readers mirror BigQuery's own
access, failing closed: its dataset's access list and the table's IAM policy, people as `ext:google.<email>`, groups
through a Google Workspace directory (S2), the company's domain as the org only on a domain the connection names
(one of the company's own); project-level grants, special groups, service accounts, and everyone on the internet
grant nothing. An entry of any other system has no readers Aerugo can mirror,
so it's an asset for the team (K3) and not a document."""
import re

import httpx

from . import config, gcp_secrets
from .catalogs import Entry, entry_key, owners_of
from .connectors import Container, Health
from .gdrive import directory_groups
from .principals import is_valid

GOOGLE = "https://googleapis.com"  # the sentinel for Google's own hosts: dataplex., bigquery., datalineage.googleapis.com
PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
LOCATION = re.compile(r"^[a-z0-9-]{2,40}$")
BQ_TABLE = re.compile(r"^bigquery:([a-z][a-z0-9-]{4,28}[a-z0-9])\.([A-Za-z0-9_]{1,1024})\.([A-Za-z0-9_$-]{1,1024})$")
READ_ROLES = {"roles/bigquery.dataViewer", "roles/bigquery.dataEditor", "roles/bigquery.dataOwner", "roles/bigquery.admin"}
DATASET_ROLES = {"READER", "WRITER", "OWNER"}
DATASET_FIELDS = (("userByEmail", "user"), ("groupByEmail", "group"), ("domain", "domain"))
MAX_PAGES = 200


def allowed_base(api_base: str) -> bool:
    return api_base.rstrip("/") in config.DATAPLEX_API_BASES


class DataplexClient:
    def __init__(self, api_base: str, projects: list[str], location: str, domain: str | None, groups: dict):
        if not allowed_base(api_base):
            raise ValueError("that Google API address isn't allowed here")
        self.base, self.domain, self.groups = api_base.rstrip("/"), domain, groups
        self.projects = [p for p in projects if PROJECT.fullmatch(p)]
        self.location = location if LOCATION.fullmatch(location) else "global"
        self._token: str | None = None

    def _host(self, api: str) -> str:
        return f"https://{api}.googleapis.com" if self.base == GOOGLE else f"{self.base}/{api}"  # Google's, or the dev fake's

    def _access_token(self) -> str:
        if not self._token:
            self._token = gcp_secrets.runner_token()
        return self._token

    def _call(self, method: str, url: str, **kw) -> dict:
        r = httpx.request(method, url, headers={"Authorization": f"Bearer {self._access_token()}"}, timeout=30, **kw)
        r.raise_for_status()
        return r.json()

    def test(self) -> Health:
        try:
            self._access_token()
            return Health(True, "connected")
        except httpx.HTTPError as e:
            return Health(False, f"Google refused the runner's identity ({type(e).__name__})")

    def discover(self) -> list[Container]:
        return [Container(external_id=p, kind="project", visibility=None, default_ref=None, name=p) for p in self.projects]

    def _search(self, project: str) -> list[dict]:
        out, token = [], None
        for _ in range(MAX_PAGES):
            params = {"query": f"projectid={project}", "pageSize": 1000, **({"pageToken": token} if token else {})}
            page = self._call("POST", f"{self._host('dataplex')}/v1/projects/{project}/locations/global:searchEntries", params=params)
            out += [r["dataplexEntry"] for r in page.get("results") or [] if r.get("dataplexEntry")]
            token = page.get("nextPageToken")
            if not token:
                return out
        raise RuntimeError(f"Dataplex's search of {project} ran past {MAX_PAGES} pages")

    def _readers(self, project: str, dataset: str, table: str) -> list[str]:
        """BigQuery's own readers of a table, mirrored and failing closed."""
        members = []
        ds = self._call("GET", f"{self._host('bigquery')}/bigquery/v2/projects/{project}/datasets/{dataset}")
        for a in ds.get("access") or []:
            if a.get("role") in DATASET_ROLES:
                members += [f"{kind}:{a[field]}" for field, kind in DATASET_FIELDS if a.get(field)]
        policy = self._call("POST", f"{self._host('bigquery')}/bigquery/v2/projects/{project}/datasets/{dataset}/tables/{table}:getIamPolicy",
                            json={})
        members += [m for b in policy.get("bindings") or [] if b.get("role") in READ_ROLES for m in b.get("members") or []]
        out = set()
        for m in members:
            kind, _, who = str(m).partition(":")
            who = who.strip().lower()
            if kind == "domain":
                if self.domain and who == self.domain.lower():  # the company's own domain, confirmed: the org
                    out.add(f"org:{config.ORG_SLUG}")
                continue
            emails = [who] if kind == "user" else self.groups.get(who, []) if kind == "group" else []
            out |= {p for e in emails if is_valid(p := f"ext:google.{e}")}
        return sorted(out)

    def _upstream(self, project: str, fqn: str) -> list[tuple[str, str]]:
        """What feeds an entry, from the Data Lineage API; what it feeds is another entry's upstream."""
        links = self._call("POST", f"{self._host('datalineage')}/v1/projects/{project}/locations/{self.location}:searchLinks",
                           json={"target": {"fullyQualifiedName": fqn}, "pageSize": 100}).get("links") or []
        return [(ns, name) for link in links
                for ns, _, name in [str((link.get("source") or {}).get("fullyQualifiedName") or "").partition(":")] if name]

    def entries(self, project: str) -> tuple[list[Entry], list, dict[str, dict]]:
        found, lineage = [], []
        for hit in self._search(project):
            e = self._call("GET", f"{self._host('dataplex')}/v1/{hit['name']}", params={"view": "ALL"})
            fqn = str(e.get("fullyQualifiedName") or "")
            namespace, _, name = fqn.partition(":") if ":" in fqn else ("dataplex", "", fqn)
            source = e.get("entrySource") or {}
            aspects = {k.rsplit(".", 1)[-1]: v.get("data") or {} for k, v in (e.get("aspects") or {}).items()}
            columns = [(str(f.get("name") or ""), str(f.get("dataType") or ""), str(f.get("description") or ""))
                       for f in (aspects.get("schema") or {}).get("fields") or []]
            owners = owners_of(i.get("id") or i.get("name") for i in (aspects.get("contacts") or {}).get("identities") or [])
            facts = [("System", str(source.get("system") or "")), ("Entry type", str(e.get("entryType") or "").rsplit("/", 1)[-1])]
            facts += [(f"Label {k}", str(v)) for k, v in sorted((source.get("labels") or {}).items())]
            table = BQ_TABLE.fullmatch(fqn)
            found.append(Entry(key=entry_key("dataplex", fqn), namespace=namespace, name=name, kind=str(e.get("entryType") or "entry").rsplit("/", 1)[-1],
                               description=str(source.get("description") or (aspects.get("overview") or {}).get("content") or ""),
                               columns=columns, facts=[f for f in facts if f[1]], owners=owners,
                               readers=self._readers(*table.groups()) if table else [], uri=str(source.get("resource") or ""),
                               version=str(e.get("updateTime") or ""), path=project))
            lineage += [(upstream, (namespace, name)) for upstream in self._upstream(project, fqn)]
        people = {p.split(".", 1)[1]: {"email": p.split(".", 1)[1]} for e in found for p in e.readers if p.startswith("ext:google.")}
        return found, lineage, people


def client_for(conn, connection: dict) -> DataplexClient:
    cfg = connection["config"] or {}
    return DataplexClient(cfg["api_base"], cfg.get("projects") or [], cfg.get("location") or "global", cfg.get("domain"),
                          directory_groups(conn))
