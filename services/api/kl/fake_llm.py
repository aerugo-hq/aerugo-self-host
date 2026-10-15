"""Test-only OpenAI-compatible chat model and reranker (runs only under the smoke profile).

It answers by citing source [1], drafts concepts for the compiler, grades answers for the eval, and
reranks by the words a passage shares with the query. It records every request it receives, so the
smoke suite can prove what did and didn't leave the cell: which sources reached a model or the reranker,
and that nothing reached a model at all when the caller had no visible sources. POST /mode switches its
answers to an unfaithful one (claims no source makes), so smoke can prove the grader catches it. Under /oidc it is
also a CI token issuer (slice J1): GitHub Actions-shaped workload tokens, signed with a key made at start, so smoke can
prove pipeline pushes without GitHub. It runs only under the smoke profile, so nothing trusts it otherwise.
"""
import io
import json
import os
import re
import secrets
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

REQUESTS: list[dict] = []
RERANKS: list[dict] = []
ALERTS: list[dict] = []  # what the app's alert webhook posted (slice P); never reset, alerts arrive late
ANSWER = "According to the sources, page the on-call first [1]."
UNFAITHFUL = "The CFO approves every refund personally [1]."  # nothing in any source says this
MODE = {"answer": ANSWER}
STOP = {"according", "sources", "source", "first", "that", "this", "with", "from", "they", "their", "there",
        "what", "when", "which", "about", "should", "would"}
ISSUER = os.environ.get("KL_FAKE_OIDC_ISSUER", "http://fake-llm:8080/oidc")
_CI_KEY: list = []


def ci_key():
    if not _CI_KEY:
        from cryptography.hazmat.primitives.asymmetric import rsa
        _CI_KEY.append(rsa.generate_private_key(public_exponent=65537, key_size=2048))
    return _CI_KEY[0]


def ci_jwks() -> dict:
    import jwt
    key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(ci_key().public_key()))
    return {"keys": [{**key, "kid": "fake-ci", "use": "sig", "alg": "RS256"}]}


def ci_token(request: dict) -> str:
    """A workload token as GitHub Actions issues one, for the repository and ref asked for."""
    import jwt
    now, repo, ref = int(time.time()), request.get("repository", "acme/repo"), request.get("ref", "refs/heads/main")
    claims = {"iss": ISSUER, "aud": request.get("audience", "kl-ingest"), "sub": f"repo:{repo}:ref:{ref}",
              "repository": repo, "ref": ref, "iat": now, "nbf": now, "exp": now + int(request.get("ttl", 300))}
    return jwt.encode(claims, ci_key(), algorithm="RS256", headers={"kid": "fake-ci"})


# A fake Google Drive (slice F2): shared drives and files smoke sets through POST /google/files, the OAuth token endpoint,
# and the Drive v3 calls the connector makes, so smoke can prove sync and permissions without Google.
DRIVES: dict[str, dict] = {}  # drive id -> {"name": ..., "files": {file id: the file, with its text}}
DRIVE_REFRESH, DRIVE_TOKEN = "smoke-refresh", "fake-drive-token"
GOOGLE = os.environ.get("KL_FAKE_GOOGLE", "http://fake-llm:8080/google")  # the issuer of its sign-in ID tokens (slice F2b)
SIGNIN_CODES: dict[str, dict] = {}  # code -> who signed in; smoke sets these through POST /google/signin
# A fake Jira Cloud (ADR 0011): projects, issues, and who may see each, which smoke sets through POST /jira/setup, and the
# REST calls the connector makes, as its service account (an email and API token), so smoke proves the permission mapping.
JIRA: dict = {"projects": {}}
JIRA_AUTH = "jira-bot@acme.test:smoke-jira-token"
# A second site at /jira-b, set through POST /jira-b/setup: another company's Jira, whose project and issue keys may be
# the same as the first's, as two real sites' can be.
JIRA_SITES = {"jira": JIRA, "jira-b": {"projects": {}}}
# A fake Confluence Cloud (S1): spaces, their pages and blog posts, and who may read each (the space's grants, each page's
# restrictions, and groups' members), which smoke sets through POST /confluence/setup. It answers the calls the
# connector makes, at Confluence's own paths (/confluence/wiki/...).
CONFLUENCE: dict = {"spaces": {}, "groups": {}, "users": {}}
CONFLUENCE_AUTH = "confluence-bot@acme.test:smoke-confluence-token"
# Service accounts and shared folders (ADR 0011): smoke mints an account's key through POST /google/service-account (its
# token_uri points somewhere unreachable, so a worker that followed it would fail) and sets folders through POST
# /google/folders, each shared with the account or not.
SA_KEYS: dict = {}  # an account's email -> the public half of its key
FOLDERS: dict[str, dict] = {}  # folder id -> {"name", "shared": with the service account, "files": [files and subfolders]}
FOLDER_TYPE = "application/vnd.google-apps.folder"
# A fake Okta (ADR 0011: directories): users smoke sets through POST /okta/setup. Like Okta, its user list leaves the
# deprovisioned out unless they're searched for by status, so a directory has to ask for leavers to find them.
OKTA: dict = {"users": []}
OKTA_TOKEN = "smoke-okta-token"
# A fake Google Workspace Directory API (S2): users, recently deleted users, groups, and their members, which smoke sets
# through POST /google/admin/setup. Its token comes from the service-account token endpoint below, only for the three
# read-only directory scopes and an admin it was told to allow (domain-wide delegation's `sub`). Every token request is
# kept, so smoke can see what was asked for and where it went (GET /google/admin/requests). Pages are small on purpose.
ADMIN: dict = {"users": [], "deleted": [], "groups": {}, "subjects": [], "broken": False}
ADMIN_TOKEN = "fake-admin-token"
ADMIN_SCOPES = ["https://www.googleapis.com/auth/admin.directory.user.readonly",
                "https://www.googleapis.com/auth/admin.directory.group.readonly",
                "https://www.googleapis.com/auth/admin.directory.group.member.readonly"]
TOKEN_REQUESTS: list = []
ADMIN_PAGE = 2
# A fake GCP (ADR 0011): the metadata server's token, for the runner's own workload identity, and Secret Manager's
# access endpoint, holding the secrets smoke sets through POST /gcp/secrets as JSON objects.
GCP_SECRETS: dict = {}
GCP_TOKEN = "fake-gcp-workload-token"


def new_service_account(email: str) -> dict:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    SA_KEYS[email] = key.public_key()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    return {"type": "service_account", "client_email": email, "private_key": pem, "token_uri": "http://10.255.255.1/steal"}


def service_account_token(assertion: str) -> tuple[int, dict]:
    """Google's token endpoint for a service account: a JWT the account signed, aimed at this endpoint, for read-only
    Drive, or (S2) for exactly the three read-only directory scopes acting as an admin delegation allows."""
    import jwt as pyjwt
    try:
        iss = pyjwt.decode(assertion, options={"verify_signature": False}).get("iss")
        claims = pyjwt.decode(assertion, SA_KEYS[iss], algorithms=["RS256"], audience=f"{GOOGLE}/token")
    except Exception:  # noqa: BLE001 - any bad assertion is a refused grant
        return 400, {"error": "invalid_grant"}
    TOKEN_REQUESTS.append({"iss": claims.get("iss"), "scope": claims.get("scope"), "sub": claims.get("sub"), "aud": claims.get("aud")})
    if claims.get("scope") == "https://www.googleapis.com/auth/drive.readonly" and not claims.get("sub"):
        return 200, {"access_token": DRIVE_TOKEN, "token_type": "Bearer", "expires_in": 3600}
    if sorted(str(claims.get("scope") or "").split(" ")) == sorted(ADMIN_SCOPES):
        if claims.get("sub") not in ADMIN["subjects"]:
            return 401, {"error": "unauthorized_client", "error_description": "Client is unauthorized to retrieve access tokens"}
        return 200, {"access_token": ADMIN_TOKEN, "token_type": "Bearer", "expires_in": 3600}
    return 400, {"error": "invalid_scope"}


def admin_members(group: str, derived: bool, seen: frozenset = frozenset()) -> list[dict]:
    """A group's members as the Directory API lists them; with derived membership, nested groups' members too."""
    out = []
    for m in ADMIN["groups"].get(group, []):
        out.append(m)
        if derived and m.get("type") == "GROUP" and m["email"] not in seen:
            out += admin_members(m["email"], True, seen | {group})
    return out


def google_id_token(client_id: str, who: dict) -> str:
    """An ID token as Google's sign-in issues one: the account's email, and whether Google verified it."""
    import jwt
    now = int(time.time())
    claims = {"iss": GOOGLE, "aud": who.get("aud") or client_id, "sub": f"fake-{who['email']}", "email": who["email"],
              "email_verified": who.get("verified", True), "iat": now, "exp": now + 300}
    return jwt.encode(claims, ci_key(), algorithm="RS256", headers={"kid": "fake-ci"})


def drive_export(spec: dict, mime: str) -> bytes:
    """A Google Doc as Word, or a Sheet as Excel, from the file's text (the first line is the title or header)."""
    lines = spec.get("text", "").split("\n")
    buf = io.BytesIO()
    if mime.endswith("wordprocessingml.document"):
        from docx import Document
        doc = Document()
        doc.add_paragraph(lines[0], style="Title")
        for line in lines[1:]:
            doc.add_paragraph(line)
        doc.save(buf)
        return buf.getvalue()
    if mime.endswith("spreadsheetml.sheet"):
        ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        rows = "".join(f'<row r="{r}">' + "".join(f'<c r="{chr(65 + c)}{r}" t="inlineStr"><is><t>{v}</t></is></c>'
                                                 for c, v in enumerate(line.split(","))) + "</row>" for r, line in enumerate(lines, 1))
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                       '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
            z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
            z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{ns}"><sheetData>{rows}</sheetData></worksheet>')
        return buf.getvalue()
    return spec.get("text", "").encode()


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9-]*", text.lower()) if len(w) > 3 and w not in STOP}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, status: int, body) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

    def do_GET(self):
        if self.path == "/requests":
            return self._json(200, REQUESTS)
        if self.path == "/reranks":
            return self._json(200, RERANKS)
        if self.path == "/alerts":
            return self._json(200, ALERTS)
        if self.path == "/oidc/.well-known/openid-configuration":
            return self._json(200, {"issuer": ISSUER, "jwks_uri": f"{ISSUER}/.well-known/jwks"})
        if self.path == "/oidc/.well-known/jwks":
            return self._json(200, ci_jwks())
        if self.path.startswith("/google/drive/v3/"):
            return self._drive(urlparse(self.path))
        if self.path == "/google/admin/requests":
            return self._json(200, TOKEN_REQUESTS)
        if self.path.startswith("/google/admin/directory/v1/"):
            return self._admin(urlparse(self.path))
        if self.path == "/google/oauth2/v3/certs":  # the keys Google signs ID tokens with
            return self._json(200, ci_jwks())
        site = self.path.split("/")[1]
        if site in JIRA_SITES and self.path.startswith(f"/{site}/rest/api/3/"):
            return self._jira(urlparse(self.path), JIRA_SITES[site])
        if self.path.startswith("/confluence/wiki/"):
            return self._confluence(urlparse(self.path))
        if self.path.startswith("/okta/api/v1/users"):
            if self.headers.get("Authorization") != f"SSWS {OKTA_TOKEN}":
                return self._json(401, {"errorCode": "E0000011", "errorSummary": "Invalid token provided"})
            search = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}.get("search", "")
            gone = 'status eq "DEPROVISIONED"' in search
            return self._json(200, [u for u in OKTA["users"] if (u.get("status") == "DEPROVISIONED") == gone])
        if self.path == "/gcp-metadata/computeMetadata/v1/instance/service-accounts/default/token":
            if self.headers.get("Metadata-Flavor") != "Google":  # as the real metadata server insists
                return self._json(403, {"error": "Missing Metadata-Flavor: Google"})
            return self._json(200, {"access_token": GCP_TOKEN, "expires_in": 3599, "token_type": "Bearer"})
        if self.path.startswith("/gcp/v1/projects/") and self.path.endswith(":access"):
            if self.headers.get("Authorization") != f"Bearer {GCP_TOKEN}":
                return self._json(401, {"error": {"code": 401, "status": "UNAUTHENTICATED"}})
            import base64
            parts = self.path.split("/")  # /gcp/v1/projects/<p>/secrets/<s>/versions/<v>:access
            value = GCP_SECRETS.get(parts[6]) if len(parts) == 9 and parts[3] == "projects" and parts[5] == "secrets" else None
            if value is None:
                return self._json(404, {"error": {"code": 404, "status": "NOT_FOUND"}})
            return self._json(200, {"name": self.path[len("/gcp/v1/"):-len(":access")],
                                    "payload": {"data": base64.b64encode(json.dumps(value).encode()).decode()}})
        self._json(404, {})

    def _drive(self, url):
        if self.headers.get("Authorization") != f"Bearer {DRIVE_TOKEN}":
            return self._json(401, {"error": {"message": "invalid credentials"}})
        q, rest = {k: v[0] for k, v in parse_qs(url.query).items()}, url.path[len("/google/drive/v3/"):]
        if rest == "drives":
            return self._json(200, {"drives": [{"id": d, "name": v["name"]} for d, v in DRIVES.items()]})
        if rest == "files":
            query = q.get("q", "")
            if "sharedWithMe = true" in query:  # folders shared with the service account
                files = [{"id": fid, "name": f["name"], "mimeType": FOLDER_TYPE} for fid, f in FOLDERS.items() if f.get("shared")]
            elif "' in parents" in query:  # one folder's children: its files and its subfolders
                files = FOLDERS.get(query.split("'")[1], {"files": []})["files"]
            else:
                files = DRIVES.get(q.get("driveId", ""), {"files": {}})["files"].values()
            return self._json(200, {"files": [{k: v for k, v in f.items() if k != "text"} for f in files]})
        parts = rest.split("/")  # files/<id> or files/<id>/export
        spec = next((d["files"][parts[1]] for d in DRIVES.values() if len(parts) > 1 and parts[1] in d["files"]), None) or \
            next((f for d in FOLDERS.values() for f in d["files"] if len(parts) > 1 and f.get("id") == parts[1] and f.get("mimeType") != FOLDER_TYPE), None)
        if parts[0] != "files" or spec is None:
            return self._json(404, {"error": {"message": "file not found"}})
        body = drive_export(spec, q.get("mimeType", "")) if parts[-1] == "export" else spec.get("text", "").encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _confluence(self, url):
        import base64
        if self.headers.get("Authorization") != "Basic " + base64.b64encode(CONFLUENCE_AUTH.encode()).decode():
            return self._json(401, {"message": "Unauthorized"})
        params = parse_qs(url.query)
        q = {k: v[0] for k, v in params.items()}
        rest = url.path[len("/confluence/wiki/"):]
        parts = rest.split("/")
        spaces, groups, users = CONFLUENCE["spaces"], CONFLUENCE["groups"], CONFLUENCE["users"]
        by_id = {str(s["id"]): (k, s) for k, s in spaces.items()}
        content = {str(c["id"]): c for s in spaces.values() for kind in ("pages", "blogposts") for c in s.get(kind, [])}
        person = lambda a: {"accountId": a, "accountType": "atlassian", "email": (users.get(a) or {}).get("email"),
                            "displayName": (users.get(a) or {}).get("name") or a}
        read = lambda kind, who: {"principal": {"type": kind, "id": who}, "operation": {"key": "read", "targetType": "space"}}
        if rest == "rest/api/user/current":
            return self._json(200, {"accountId": "aerugo-bot", "displayName": "Aerugo bot"})
        if rest == "api/v2/spaces":
            keys = set(q["keys"].split(",")) if q.get("keys") else None
            return self._json(200, {"results": [{"id": str(s["id"]), "key": k, "name": s["name"]} for k, s in spaces.items()
                                                if keys is None or k in keys], "_links": {}})
        if len(parts) == 5 and parts[:3] == ["api", "v2", "spaces"] and parts[3] in by_id:
            key, s = by_id[parts[3]]
            if parts[4] in ("pages", "blogposts"):
                return self._json(200, {"results": [
                    {"id": str(c["id"]), "title": c["title"], "parentId": c.get("parentId"), "version": {"number": c.get("version", 1)},
                     "body": {"storage": {"value": c.get("body", "")}}, "_links": {"webui": f"/spaces/{key}/pages/{c['id']}"}}
                    for c in s.get(parts[4], [])], "_links": {}})
            if parts[4] == "permissions":
                grants = [read("user", a) for a in s.get("users", [])] + [read("group", g) for g in s.get("groups", [])]
                return self._json(200, {"results": grants + ([read("anonymous", None)] if s.get("anonymous") else []), "_links": {}})
        if len(parts) == 7 and parts[:3] == ["rest", "api", "content"] and parts[4:] == ["restriction", "byOperation", "read"]:
            c = content.get(parts[3])
            if c is None:
                return self._json(404, {"message": "No content found"})
            return self._json(200, {"operation": "read", "restrictions": {
                "user": {"results": [person(a) for a in c.get("restricted_users", [])]},
                "group": {"results": [{"id": g, "name": (groups.get(g) or {}).get("name", g)} for g in c.get("restricted_groups", [])]}}})
        if len(parts) == 5 and parts[:3] == ["rest", "api", "group"] and parts[4] == "membersByGroupId":
            if parts[3] not in groups:
                return self._json(404, {"message": "No group found"})
            return self._json(200, {"results": [person(a) for a in groups[parts[3]].get("members", [])]})
        if rest == "rest/api/group/by-id":
            g = groups.get(q.get("id", ""))
            return self._json(200, {"id": q.get("id"), "name": g["name"]}) if g else self._json(404, {"message": "No group found"})
        if rest == "rest/api/user/bulk":
            return self._json(200, {"results": [person(a) for a in params.get("accountId", []) if a in users]})
        if len(parts) == 5 and parts[:2] == ["api", "v2"] and parts[2] in ("pages", "blogposts") and parts[4] in ("labels", "attachments"):
            c = content.get(parts[3]) or {}
            if parts[4] == "labels":
                return self._json(200, {"results": [{"name": label} for label in c.get("labels", [])], "_links": {}})
            return self._json(200, {"results": [{"title": t} for t in c.get("attachments", [])], "_links": {}})
        return self._json(404, {"message": "Not found"})

    def _admin(self, url):
        """The Directory API's users, groups, and members, as Google pages them (pageToken, never a URL)."""
        if self.headers.get("Authorization") != f"Bearer {ADMIN_TOKEN}":
            return self._json(401, {"error": {"code": 401, "message": "Login Required", "status": "UNAUTHENTICATED"}})
        if ADMIN.get("broken"):  # a listing that can't finish: the client must fail, not read it as everyone leaving
            return self._json(503, {"error": {"code": 503, "message": "Backend Error"}})
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        rest = url.path[len("/google/admin/directory/v1/"):]
        page = lambda items, key: self._json(200, {key: items[int(q.get("pageToken") or 0):int(q.get("pageToken") or 0) + ADMIN_PAGE],
                                                   **({"nextPageToken": str(int(q.get("pageToken") or 0) + ADMIN_PAGE)}
                                                      if int(q.get("pageToken") or 0) + ADMIN_PAGE < len(items) else {})})
        if rest == "users" and q.get("customer") == "my_customer":
            return page(ADMIN["deleted"] if q.get("showDeleted") == "true" else ADMIN["users"], "users")
        if rest == "groups" and q.get("customer") == "my_customer":
            return page([{"email": g} for g in ADMIN["groups"]], "groups")
        parts = rest.split("/")
        if len(parts) == 3 and parts[0] == "groups" and parts[2] == "members":
            from urllib.parse import unquote
            group = unquote(parts[1])
            if group not in ADMIN["groups"]:
                return self._json(404, {"error": {"code": 404, "message": "Resource Not Found: groupKey"}})
            return page(admin_members(group, q.get("includeDerivedMembership") == "true"), "members")
        self._json(404, {"error": {"code": 404, "message": "not found"}})

    def _jira(self, url, site: dict):
        import base64
        if self.headers.get("Authorization") != "Basic " + base64.b64encode(JIRA_AUTH.encode()).decode():
            return self._json(401, {"errorMessages": ["Client must be authenticated to access this resource."]})
        q, rest = {k: v[0] for k, v in parse_qs(url.query).items()}, url.path.split("/rest/api/3/", 1)[1]
        projects = site["projects"]
        doc = lambda text: {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}
        if rest == "myself":
            return self._json(200, {"accountId": "aerugo-bot", "displayName": "Aerugo bot"})
        if rest == "project/search":
            return self._json(200, {"values": [{"id": k, "key": k, "name": p["name"]} for k, p in projects.items()], "isLast": True})
        if rest == "search/jql":
            key = q.get("jql", "").split("=", 1)[-1].split(" ORDER", 1)[0].strip()
            issues = [{"id": i["key"], "key": i["key"], "fields": {
                "summary": i["summary"], "description": doc(i.get("description", "")), "updated": i.get("updated", "2026-10-01T10:00:00.000+0000"),
                "security": i.get("security"), "issuetype": {"name": "Task"}, "status": {"name": "To Do"}, "labels": [],
                "assignee": None, "reporter": {"displayName": "Smoke"},
                "comment": {"comments": [{"author": {"displayName": c["author"]}, "created": "2026-10-01T10:00:00.000+0000",
                                          "body": doc(c["body"])} for c in i.get("comments", [])]}}}
                for i in projects.get(key, {}).get("issues", [])]
            return self._json(200, {"issues": issues, "isLast": True})
        parts = rest.split("/")
        if len(parts) == 3 and parts[0] == "project" and parts[2] == "permissionscheme" and parts[1] in projects:
            holder = "applicationRole" if projects[parts[1]].get("company_wide") else "projectRole"
            return self._json(200, {"permissions": [{"permission": "BROWSE_PROJECTS", "holder": {"type": holder}}]})
        if rest == "user/permission/search":
            if q.get("issueKey"):
                issue = next((i for p in projects.values() for i in p.get("issues", []) if i["key"] == q["issueKey"]), None)
                if issue is None:
                    return self._json(404, {"errorMessages": ["Issue does not exist"]})
                key = issue["key"].rsplit("-", 1)[0]
                users = issue.get("viewers") if issue.get("security") else projects[key].get("browsers", [])
            else:
                users = projects.get(q.get("projectKey", ""), {}).get("browsers", [])
            return self._json(200, [{"active": True, **u} for u in users or []])
        self._json(404, {"errorMessages": ["not found"]})

    def do_DELETE(self):
        if self.path == "/requests":  # a clean slate: both logs, and honest answers again
            REQUESTS.clear()
            RERANKS.clear()
            MODE["answer"] = ANSWER
            return self._json(200, {})
        self._json(404, {})

    def do_POST(self):
        path = self.path.rstrip("/")
        if path == "/alerts":  # the test webhook: the app posts alerts here when an org admin points it here
            ALERTS.append(self._body())
            return self._json(200, {})
        if path == "/oidc/token":  # a CI job asking for its workload token
            return self._json(200, {"token": ci_token(self._body())})
        if path == "/google/token":  # OAuth, as Google's token endpoint does
            form = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
            if form.get("grant_type", [""])[0] == "urn:ietf:params:oauth:grant-type:jwt-bearer":  # a service account (ADR 0011)
                return self._json(*service_account_token(form.get("assertion", [""])[0]))
            if form.get("grant_type", [""])[0] == "authorization_code":  # sign-in: a one-time code for an ID token
                who = SIGNIN_CODES.pop(form.get("code", [""])[0], None)
                if not who:
                    return self._json(400, {"error": "invalid_grant"})
                return self._json(200, {"id_token": google_id_token(form.get("client_id", [""])[0], who),
                                        "access_token": "fake-signin-token", "token_type": "Bearer"})
            if form.get("refresh_token", [""])[0] != DRIVE_REFRESH:  # Drive: a refresh token for an access token
                return self._json(400, {"error": "invalid_grant"})
            return self._json(200, {"access_token": DRIVE_TOKEN, "expires_in": 3600, "token_type": "Bearer"})
        if path == "/google/signin":  # smoke signs someone in: the code Google would send back to the callback
            code = secrets.token_urlsafe(24)
            SIGNIN_CODES[code] = self._body()
            return self._json(200, {"code": code})
        if path == "/google/service-account":  # smoke mints a service account's JSON key
            return self._json(200, new_service_account(self._body()["email"]))
        if path == "/google/folders":  # smoke sets folders, and which are shared with the service account
            FOLDERS.clear()
            FOLDERS.update(self._body()["folders"])
            return self._json(200, {})
        if path == "/gcp/secrets":  # smoke sets the fake Secret Manager's secrets, each a JSON object
            GCP_SECRETS.update(self._body()["secrets"])
            return self._json(200, {})
        if path == "/google/admin/setup":  # smoke sets the fake Workspace directory, and which admins delegation allows
            ADMIN.clear()
            ADMIN.update({"users": [], "deleted": [], "groups": {}, "subjects": [], "broken": False, **self._body()})
            TOKEN_REQUESTS.clear()
            return self._json(200, {})
        if path == "/okta/setup":  # smoke sets the fake Okta's users
            OKTA["users"] = self._body()["users"]
            return self._json(200, {})
        if path == "/confluence/setup":  # smoke sets the fake Confluence's spaces, pages, groups, and people
            CONFLUENCE.clear()
            CONFLUENCE.update({"spaces": {}, "groups": {}, "users": {}, **self._body()})
            return self._json(200, {})
        if path in ("/jira/setup", "/jira-b/setup"):  # smoke sets a fake Jira site's projects, issues, and who may see each
            site = JIRA_SITES[path.split("/")[1]]
            site.clear()
            site.update({"projects": {}, **self._body()})
            return self._json(200, {})
        if path == "/google/files":  # smoke sets a shared drive's files (and who each is shared with)
            request = self._body()
            DRIVES[request["drive"]] = {"name": request.get("name", request["drive"]), "files": {f["id"]: f for f in request["files"]}}
            return self._json(200, {})
        if path == "/mode":
            MODE["answer"] = UNFAITHFUL if self._body().get("answer") == "unfaithful" else ANSWER
            return self._json(200, {})
        if path == "/v1/rerank":
            request = self._body()
            RERANKS.append(request)
            asked = words(request.get("query", ""))
            docs = request.get("documents") or []
            results = [{"index": i, "relevance_score": len(asked & words(d)) / (1 + len(asked))} for i, d in enumerate(docs)]
            results.sort(key=lambda r: r["relevance_score"], reverse=True)
            return self._json(200, {"results": results[: request.get("top_n") or len(results)]})
        if path != "/v1/chat/completions":
            return self._json(404, {})
        request = self._body()
        REQUESTS.append(request)
        messages = request.get("messages") or []
        system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
        prompt = messages[-1].get("content", "") if messages else ""
        if "knowledge compiler" in system:
            content = compiler_draft(prompt)
        elif "grade answers" in system:
            content = grade(prompt)
        else:
            content = MODE["answer"]
        self._json(200, {"choices": [{"message": {"role": "assistant", "content": content}}],
                         "usage": {"prompt_tokens": 100, "completion_tokens": 12}})


def compiler_draft(prompt: str) -> str:
    """A compiler reply. A topic containing make-a-bad-draft cites a source the compiler wasn't given,
    so smoke can prove such drafts are rejected."""
    cite = "[^s9]" if "make-a-bad-draft" in prompt else "[^s1]"
    return json.dumps({"type": "Playbook", "title": "Paging during a payments outage",
                       "body": f"When payments are down, page the payments on-call lead first {cite}."})


def grade(prompt: str) -> str:
    """The eval's judge, in miniature: a claim is supported when most of its words appear in the cited
    sources, and a key fact is stated when most of its words appear in the answer."""
    sources, _, rest = prompt.partition("Answer to grade:")
    found = re.search(r"<<<\n(.*?)\n>>>", rest, re.S)
    answer = found.group(1) if found else ""
    facts = re.findall(r"^- (.+)$", rest.partition("Key facts to look for:")[2], re.M)
    pool, said = words(sources), words(answer)

    def covered(text: str, known: set[str]) -> bool:
        w = words(text)
        return bool(w) and len(w & known) >= 0.6 * len(w)

    claims = [c.strip() for c in re.split(r"(?<=[.!?])\s+", re.sub(r"\s*\[\d+\]", "", answer)) if c.strip()]
    return json.dumps({"claims": [{"claim": c, "supported": covered(c, pool)} for c in claims],
                       "facts": [covered(f, said) for f in facts]})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
