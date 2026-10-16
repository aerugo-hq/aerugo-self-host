"""The AWS Glue Data Catalog (S7): a region's databases and their tables (columns, descriptions, owners, where the data
lives), metadata only. Keyless: the runner assumes an IAM role the company created for it, with its own identity
token (AssumeRoleWithWebIdentity: the GCP metadata server's identity token, or a token file the platform mounts, as
EKS does), so no AWS key exists anywhere. SageMaker Catalog's assets come from this same Glue Data Catalog, so this
connector covers them; its business glossary isn't read.

A table's readers mirror Lake Formation's grants, failing closed: a person granted SELECT (or ALL) on the table,
through the company's SAML identity provider, reads it, as `ext:aws.<email>`. An IAM user or role isn't a person; an
IAM Identity Center user isn't named by email; `IAMAllowedPrincipals` (IAM-only access control) can't be resolved to
people; so none of them reads anything. Glue records no lineage."""
import datetime
import hashlib
import hmac
import json
import random
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from . import config
from .catalogs import Entry, entry_key, owners_of
from .connectors import Container, Health
from .principals import is_valid

REGION = re.compile(r"^[a-z]{2}(-gov)?-[a-z]+-[1-9]$")
ROLE = re.compile(r"^arn:aws(-cn|-us-gov)?:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_/-]{1,512}$")
NAME = re.compile(r"^[a-z0-9_-]{1,255}$")  # Glue keeps database and table names lower case
SAML_USER = re.compile(r"^arn:aws(-cn|-us-gov)?:iam::[0-9]{12}:saml-provider/[A-Za-z0-9_.-]{1,128}:user/(.+)$")
EMAIL = re.compile(r"^[a-z0-9._%+-]{1,64}@[a-z0-9-]{1,63}(\.[a-z0-9-]{1,63})*\.[a-z]{2,24}$")
READS = {"SELECT", "ALL"}
TABLE_RESOURCES = ("Table", "TableWithColumns")
AUDIENCE = "sts.amazonaws.com"
MAX_PAGES = 500
RETRIES = 5
NS = {"sts": "https://sts.amazonaws.com/doc/2011-06-15/"}


@dataclass
class Credentials:
    key_id: str
    secret: str
    token: str


def allowed_base(api_base: str) -> bool:
    """AWS's own regional endpoints (an empty base), or an address the deployment allows (the dev fake)."""
    return not api_base or api_base.rstrip("/") in config.AWS_API_BASES


def web_identity_token() -> str:
    """The runner's own identity token, for AWS to trust: a token file the platform mounts, else the GCP metadata server's."""
    if config.AWS_WEB_IDENTITY_TOKEN_FILE:
        with open(config.AWS_WEB_IDENTITY_TOKEN_FILE) as f:
            return f.read().strip()
    r = httpx.get(f"{config.GCP_METADATA_BASE}/computeMetadata/v1/instance/service-accounts/default/identity",
                  params={"audience": AUDIENCE, "format": "full"}, headers={"Metadata-Flavor": "Google"}, timeout=5)
    r.raise_for_status()
    return r.text.strip()


def sign(method: str, url: str, region: str, service: str, body: bytes, headers: dict, creds: Credentials,
         now: datetime.datetime | None = None) -> dict:
    """Signature Version 4: the request's headers with its date, session token (when it has one), and Authorization
    added. Smoke holds it to AWS's own test vector (get-vanilla)."""
    parts = urlsplit(url)
    stamp = (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    signed = {**{k.lower(): str(v).strip() for k, v in headers.items()}, "host": parts.netloc, "x-amz-date": stamp,
              **({"x-amz-security-token": creds.token} if creds.token else {})}
    names = sorted(signed)
    canonical = "\n".join([method, parts.path or "/", parts.query, "".join(f"{k}:{signed[k]}\n" for k in names), ";".join(names),
                           hashlib.sha256(body).hexdigest()])
    scope = f"{stamp[:8]}/{region}/{service}/aws4_request"
    key = f"AWS4{creds.secret}".encode()
    for part in (stamp[:8], region, service, "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    to_sign = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return {**signed, "authorization": f"AWS4-HMAC-SHA256 Credential={creds.key_id}/{scope}, SignedHeaders={';'.join(names)}, Signature={signature}"}


class GlueClient:
    def __init__(self, api_base: str, region: str, role_arn: str, session: str, databases: list[str] | None):
        if not allowed_base(api_base):
            raise ValueError("that AWS address isn't allowed here")
        if not REGION.fullmatch(region) or not ROLE.fullmatch(role_arn):
            raise ValueError("name the AWS region and the IAM role's ARN")
        self.base, self.region, self.role, self.session = api_base.rstrip("/"), region, role_arn, session[:64]
        self.databases = [d for d in databases or [] if NAME.fullmatch(d)] or None
        self._creds: Credentials | None = None

    def _url(self, service: str, path: str = "/") -> str:
        return f"https://{service}.{self.region}.amazonaws.com{path}" if not self.base else f"{self.base}/{service}{path}"

    def _credentials(self) -> Credentials:
        """The role's temporary credentials, for the runner's own identity token; AWS signs none of this request."""
        if not self._creds:
            r = httpx.post(self._url("sts"), timeout=15, data={
                "Action": "AssumeRoleWithWebIdentity", "Version": "2011-06-15", "RoleArn": self.role,
                "RoleSessionName": self.session, "WebIdentityToken": web_identity_token(), "DurationSeconds": "3600"})
            r.raise_for_status()
            if "<!" in r.text:  # STS never sends a DTD or entities; refusing them rules out entity expansion
                raise RuntimeError("AWS's answer wasn't the role's credentials")
            found = ET.fromstring(r.text).find(".//sts:Credentials", NS)
            if found is None:
                raise RuntimeError("AWS didn't hand over the role's credentials")
            self._creds = Credentials(*(found.findtext(f"sts:{k}", "", NS) for k in ("AccessKeyId", "SecretAccessKey", "SessionToken")))
        return self._creds

    def _call(self, service: str, path: str, body: dict, headers: dict) -> dict:
        data, url = json.dumps(body).encode(), self._url(service, path)
        for attempt in range(RETRIES + 1):
            r = httpx.post(url, content=data, timeout=30, headers=sign("POST", url, self.region, service, data, headers, self._credentials()))
            throttled = r.status_code == 429 or (r.status_code == 400 and "Throttling" in r.text[:500])
            if not throttled or attempt == RETRIES:
                r.raise_for_status()
                return r.json()
            time.sleep(2 ** attempt + random.random())
        raise RuntimeError("unreachable")

    def _glue(self, action: str, body: dict, key: str) -> list[dict]:
        out, token = [], None
        for _ in range(MAX_PAGES):
            page = self._call("glue", "/", {**body, **({"NextToken": token} if token else {})},
                              {"content-type": "application/x-amz-json-1.1", "x-amz-target": f"AWSGlue.{action}"})
            out += page.get(key) or []
            token = page.get("NextToken")
            if not token:
                return out
        raise RuntimeError(f"Glue's {action} ran past {MAX_PAGES} pages")

    def test(self) -> Health:
        try:
            self._credentials()
            return Health(True, "connected")
        except httpx.HTTPError as e:
            return Health(False, f"AWS refused the role ({type(e).__name__}): check its trust policy and the runner's identity")

    def discover(self) -> list[Container]:
        names = self.databases or [d["Name"] for d in self._glue("GetDatabases", {}, "DatabaseList") if NAME.fullmatch(d.get("Name") or "")]
        return [Container(external_id=n, kind="database", visibility=None, default_ref=None, name=n) for n in names]

    def readers(self, database: str, table: str) -> tuple[list[str], dict[str, dict]]:
        """The people Lake Formation lets read a table, and the accounts it named, for identity matching."""
        out, people, token = set(), {}, None
        for _ in range(MAX_PAGES):
            body = {"Resource": {"Table": {"DatabaseName": database, "Name": table}}, "IncludeRelated": "TRUE",
                    **({"NextToken": token} if token else {})}
            page = self._call("lakeformation", "/ListPermissions", body, {"content-type": "application/json"})
            for grant in page.get("PrincipalResourcePermissions") or []:
                who = SAML_USER.fullmatch(str((grant.get("Principal") or {}).get("DataLakePrincipalIdentifier") or ""))
                email = who[2].strip().lower() if who else ""
                if not EMAIL.fullmatch(email) or not READS & set(grant.get("Permissions") or []):
                    continue
                if any(k in (grant.get("Resource") or {}) for k in TABLE_RESOURCES) and is_valid(p := f"ext:aws.{email}"):
                    out.add(p)
                    people[email] = {"email": email}
            token = page.get("NextToken")
            if not token:
                return sorted(out), people
        raise RuntimeError(f"Lake Formation's grants on {database}.{table} ran past {MAX_PAGES} pages")

    def entries(self, database: str) -> tuple[list[Entry], list, dict[str, dict]]:
        found, people = [], {}
        for t in self._glue("GetTables", {"DatabaseName": database}, "TableList"):
            name = str(t.get("Name") or "")
            if not NAME.fullmatch(name):
                continue
            readers, named = self.readers(database, name)
            people.update(named)
            storage = t.get("StorageDescriptor") or {}
            columns = [(str(c.get("Name") or ""), str(c.get("Type") or ""), str(c.get("Comment") or ""))
                       for c in (storage.get("Columns") or []) + (t.get("PartitionKeys") or [])]
            facts = [("Database", database), ("Table type", str(t.get("TableType") or "")), ("Location", str(storage.get("Location") or "")),
                     ("Classification", str((t.get("Parameters") or {}).get("classification") or ""))]
            found.append(Entry(key=entry_key("glue", f"{self.region}/{database}.{name}"), namespace="glue", name=f"{database}.{name}", kind="table",
                               description=str(t.get("Description") or ""), columns=columns, facts=[f for f in facts if f[1]],
                               owners=owners_of([t.get("Owner")]), readers=readers,
                               uri=f"https://{self.region}.console.aws.amazon.com/glue/home?region={self.region}#/v2/data-catalog/tables/view/{name}?database={database}",
                               version=str(t.get("UpdateTime") or t.get("CreateTime") or ""), path=database))
        return found, [], people


def client_for(conn, connection: dict) -> GlueClient:
    cfg = connection["config"] or {}
    return GlueClient(cfg.get("api_base") or "", cfg["region"], cfg["role_arn"], f"aerugo-{str(connection['id'])[:8]}",
                      cfg.get("databases"))
