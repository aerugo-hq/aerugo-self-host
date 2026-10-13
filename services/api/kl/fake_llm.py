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
        if self.path == "/google/oauth2/v3/certs":  # the keys Google signs ID tokens with
            return self._json(200, ci_jwks())
        self._json(404, {})

    def _drive(self, url):
        if self.headers.get("Authorization") != f"Bearer {DRIVE_TOKEN}":
            return self._json(401, {"error": {"message": "invalid credentials"}})
        q, rest = {k: v[0] for k, v in parse_qs(url.query).items()}, url.path[len("/google/drive/v3/"):]
        if rest == "drives":
            return self._json(200, {"drives": [{"id": d, "name": v["name"]} for d, v in DRIVES.items()]})
        if rest == "files":
            files = DRIVES.get(q.get("driveId", ""), {"files": {}})["files"].values()
            return self._json(200, {"files": [{k: v for k, v in f.items() if k != "text"} for f in files]})
        parts = rest.split("/")  # files/<id> or files/<id>/export
        spec = next((d["files"][parts[1]] for d in DRIVES.values() if len(parts) > 1 and parts[1] in d["files"]), None)
        if parts[0] != "files" or spec is None:
            return self._json(404, {"error": {"message": "file not found"}})
        body = drive_export(spec, q.get("mimeType", "")) if parts[-1] == "export" else spec.get("text", "").encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
