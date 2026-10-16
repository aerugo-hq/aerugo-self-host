"""Unity Catalog (S7): a Databricks workspace's catalogs, their schemas and tables (columns, comments, owners), and
table lineage, read by a service principal (OAuth machine-to-machine), metadata only. Each table's readers mirror
Unity Catalog's effective permissions, failing closed: a user holding SELECT or ALL PRIVILEGES (directly or inherited)
reads it, as `ext:databricks.<email>`; a group grants nothing (its members aren't listed to this principal), nor does a
service principal."""
import re

import httpx

from . import config
from .catalogs import Entry, entry_key, owners_of
from .connectors import Container, Health
from .principals import is_valid
from .vault import vault

HOST = re.compile(r"^https://[a-z0-9][a-z0-9.-]{0,120}\.(cloud\.databricks\.com|azuredatabricks\.net|gcp\.databricks\.com)$")
NAME = re.compile(r"^[A-Za-z0-9_-]{1,255}$")
EMAIL = re.compile(r"^[a-z0-9._%+-]{1,64}@[a-z0-9-]{1,63}(\.[a-z0-9-]{1,63})*\.[a-z]{2,24}$")
READS = {"SELECT", "ALL_PRIVILEGES", "ALL PRIVILEGES"}
MAX_PAGES = 500


def allowed_base(api_base: str) -> bool:
    base = api_base.rstrip("/")
    return bool(HOST.fullmatch(base)) or base in config.DATABRICKS_API_BASES


class UnityClient:
    def __init__(self, api_base: str, client_id: str, client_secret: str, catalogs: list[str] | None):
        if not allowed_base(api_base):
            raise ValueError("that isn't a Databricks workspace address")
        self.base, self.catalogs = api_base.rstrip("/"), [c for c in catalogs or [] if NAME.fullmatch(c)] or None
        self._auth, self._token = (client_id, client_secret), None

    def _access_token(self) -> str:
        """OAuth machine-to-machine: the service principal's own client credentials, for this workspace only."""
        if not self._token:
            r = httpx.post(f"{self.base}/oidc/v1/token", data={"grant_type": "client_credentials", "scope": "all-apis"},
                           auth=self._auth, timeout=15)
            r.raise_for_status()
            self._token = r.json()["access_token"]
        return self._token

    def _get(self, path: str, **params) -> dict:
        r = httpx.get(f"{self.base}{path}", params=params, headers={"Authorization": f"Bearer {self._access_token()}"}, timeout=30)
        r.raise_for_status()
        return r.json()

    def _pages(self, path: str, key: str, **params) -> list[dict]:
        out, token = [], None
        for _ in range(MAX_PAGES):
            page = self._get(path, **params, **({"page_token": token} if token else {}))
            out += page.get(key) or []
            token = page.get("next_page_token")
            if not token:
                return out
        raise RuntimeError(f"Unity Catalog's listing of {path} ran past {MAX_PAGES} pages")

    def test(self) -> Health:
        try:
            self._access_token()
            return Health(True, "connected")
        except httpx.HTTPError as e:
            return Health(False, f"Databricks refused the service principal ({type(e).__name__})")

    def discover(self) -> list[Container]:
        names = self.catalogs or [c["name"] for c in self._pages("/api/2.1/unity-catalog/catalogs", "catalogs") if NAME.fullmatch(c.get("name") or "")]
        return [Container(external_id=n, kind="catalog", visibility=None, default_ref=None, name=n) for n in names]

    def readers(self, full_name: str) -> list[str]:
        """The users Unity Catalog lets read a table, effective (inherited grants included); groups grant nothing."""
        body = self._get(f"/api/2.1/unity-catalog/effective-permissions/table/{full_name}")
        out = set()
        for a in body.get("privilege_assignments") or []:
            who = str(a.get("principal") or "").strip().lower()
            if EMAIL.fullmatch(who) and READS & {str(p.get("privilege") or "").upper() for p in a.get("privileges") or []}:
                if is_valid(p := f"ext:databricks.{who}"):
                    out.add(p)
        return sorted(out)

    def entries(self, catalog: str) -> tuple[list[Entry], list, dict[str, dict]]:
        found, lineage = [], []
        for schema in self._pages("/api/2.1/unity-catalog/schemas", "schemas", catalog_name=catalog):
            if not NAME.fullmatch(schema.get("name") or ""):
                continue
            for t in self._pages("/api/2.1/unity-catalog/tables", "tables", catalog_name=catalog, schema_name=schema["name"]):
                full = str(t.get("full_name") or "")
                if not all(NAME.fullmatch(part) for part in full.split(".")) or full.count(".") != 2:
                    continue
                found.append(Entry(key=entry_key(f"unity-{catalog}", full.lower()), namespace="unity", name=full.lower(), kind=str(t.get("table_type") or "table").lower(),
                                   description=str(t.get("comment") or ""),
                                   columns=[(str(c.get("name") or ""), str(c.get("type_text") or ""), str(c.get("comment") or ""))
                                            for c in t.get("columns") or []],
                                   facts=[("Catalog", catalog), ("Schema", schema["name"])], owners=owners_of([t.get("owner")]),
                                   readers=self.readers(full), uri=f"{self.base}/explore/data/{full.replace('.', '/')}",
                                   version=str(t.get("updated_at") or ""), path=f"{catalog} › {schema['name']}"))
                ups = self._get("/api/2.0/lineage-tracking/table-lineage", table_name=full, include_entity_lineage="false").get("upstreams") or []
                for up in ups:
                    info = up.get("tableInfo") or {}
                    source = ".".join(str(info.get(k) or "") for k in ("catalog_name", "schema_name", "name")).lower()
                    if source.count(".") == 2 and all(source.split(".")):
                        lineage.append((("unity", source), ("unity", full.lower())))
        people = {p.split(".", 1)[1]: {"email": p.split(".", 1)[1]} for e in found for p in e.readers}
        return found, lineage, people


def client_for(conn, connection: dict) -> UnityClient:
    cfg, secret = connection["config"] or {}, vault.get(connection["credential_ref"])
    return UnityClient(cfg["api_base"], cfg["client_id"], secret["client_secret"], cfg.get("catalogs"))
