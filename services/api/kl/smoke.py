"""M0 smoke suite: every provisioned piece is reachable, and permissions hold end to end.

Run with `make smoke`. Exit code 0 means every required check passed; optional checks
(live model call, GitHub App) report SKIP until you configure them in .env.
The permission checks here become the seed of the release-gating leak suite.
"""
import asyncio
import glob
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
import uuid
from datetime import datetime

import httpx
import jwt
import psycopg
from psycopg.rows import dict_row

API = os.environ.get("KL_API_URL", "http://api:8000")
TOKEN_URL = os.environ.get("KL_TOKEN_URL", "http://keycloak:8080/realms/acme/protocol/openid-connect/token")
APP_DB = os.environ["KL_APP_DATABASE_URL"]
OWNER_DB = os.environ["KL_OWNER_DATABASE_URL"]
INGEST_DB = os.environ["KL_INGEST_DATABASE_URL"]
BAO_URL = os.environ.get("KL_OPENBAO_URL", "http://openbao:8200")
BAO_TOKEN = os.environ.get("KL_OPENBAO_TOKEN", "")
ISSUER = os.environ.get("KL_OIDC_ISSUER", "http://localhost:8081/realms/acme")
PASSWORDS = {u: f"{u}-dev-only" for u in ("alice", "bob", "carol", "dave", "olivia")}  # olivia: the org admin

SKIP = object()
CHECKS = []
_tokens: dict[str, str] = {}


def check(fn):
    CHECKS.append(fn)
    return fn


# ------------------------------------------------------------------ helpers
def wait_for(url: str, seconds: int = 240) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=3).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise SystemExit(f"timed out waiting for {url}")


def token(user: str, client_id: str = "kl-cli") -> str:
    key = (user, client_id)
    if key not in _tokens:
        deadline, last = time.monotonic() + 240, ""  # Keycloak is slow on first boot
        while True:
            try:
                r = httpx.post(TOKEN_URL, timeout=10, data={
                    "grant_type": "password", "client_id": client_id,
                    "username": user, "password": PASSWORDS[user], "scope": "openid",
                })
                if r.status_code == 200:
                    _tokens[key] = r.json()["access_token"]
                    break
                last = f"{r.status_code} {r.text[:200]}"
            except httpx.HTTPError as e:
                last = type(e).__name__
            if time.monotonic() > deadline:
                raise AssertionError(f"could not get a token for {user}: {last}")
            time.sleep(3)
    return _tokens[key]


def api(method: str, path: str, user: str | None = None, **kw) -> httpx.Response:
    headers = kw.pop("headers", {})
    if user:
        headers["Authorization"] = f"Bearer {token(user)}"
    return httpx.request(method, f"{API}{path}", headers=headers, timeout=60, **kw)


# The M0 seed fixture. Everything else (GitHub-synced docs, curated concepts, concepts merged into a knowledge
# repo) follows other permissions and has its own checks, so these exact-set checks look at the seed only.
SEED_DOCS = {"pay-runbook", "fin-close", "handbook", "pay-drift", "legal-hold"}


def search_ids(user: str, scope: str = "org", q: str = "incident") -> set[str]:
    """Seed-fixture documents the user finds."""
    r = api("GET", "/v1/search", user, params={"q": q, "scope": scope, "limit": 50})
    assert r.status_code == 200, f"search as {user}, scope={scope}: {r.status_code} {r.text[:200]}"
    return {hit["id"] for hit in r.json()["results"] if hit["id"] in SEED_DOCS}


def acme_org_id() -> str:
    with psycopg.connect(OWNER_DB) as conn:
        return str(conn.execute("SELECT id FROM kl.orgs WHERE slug = 'acme'").fetchone()[0])


def app_conn(principals: list[str], org_id: str) -> psycopg.Connection:
    conn = psycopg.connect(APP_DB, row_factory=dict_row)
    conn.execute("SELECT set_config('kl.org_id', %s, false), set_config('kl.principals', %s, false)",
                 (org_id, ",".join(principals)))
    return conn


ALICE = ["user:alice", "org:acme", "group:payments"]


# ------------------------------------------------------------------ infrastructure
@check
def postgres_has_pgvector():
    """Postgres is up with pgvector installed"""
    with psycopg.connect(OWNER_DB) as conn:
        row = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()
    assert row, "pgvector extension missing"


@check
def api_ready():
    """API reaches Postgres, OpenFGA, OpenBao, and the IdP"""
    deadline = time.monotonic() + 240  # Keycloak's first boot can lag the rest of the stack
    while True:
        r = httpx.get(f"{API}/readyz", timeout=30)
        if r.status_code == 200 or time.monotonic() > deadline:
            break
        time.sleep(3)
    assert r.status_code == 200, f"{r.status_code} {r.text[:400]}"


# ------------------------------------------------------------------ database isolation
@check
def rls_fails_closed():
    """RLS: the app role sees nothing without a request context"""
    with psycopg.connect(APP_DB) as conn:
        docs = conn.execute("SELECT count(*) FROM documents").fetchone()[0]
        chunks = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    assert docs == 0 and chunks == 0, f"expected 0 documents and 0 chunks, saw {docs} and {chunks}"


@check
def rls_filters_by_acl():
    """RLS: alice's principals see exactly the rows their ACLs allow"""
    with app_conn(ALICE, acme_org_id()) as conn:
        keys = {r["external_key"] for r in conn.execute("SELECT external_key FROM documents")}
    # RLS is the ACL layer only: all five curated concepts' ACLs admit alice's principals here. Their
    # audience rule and workspace scope narrow them above this layer (the curated and graph checks).
    assert keys == {"pay-runbook", "pay-drift", "handbook", "legal-hold", *KB_TIERS}, keys


@check
def rls_rejects_other_org():
    """RLS: a context naming another org returns nothing"""
    with app_conn(ALICE, "00000000-0000-0000-0000-000000000000") as conn:
        n = conn.execute("SELECT count(*) AS n FROM documents").fetchone()["n"]
    assert n == 0, f"saw {n} rows across an org boundary"


@check
def audit_is_append_only():
    """Audit log is append-only for the app role"""
    with app_conn(["user:alice", "org:acme"], acme_org_id()) as conn:
        try:
            conn.execute("DELETE FROM audit_events")
        except psycopg.errors.InsufficientPrivilege:
            conn.rollback()
            return
    raise AssertionError("the app role was able to DELETE from audit_events")


# ------------------------------------------------------------------ identity
@check
def rejects_missing_and_forged_tokens():
    """API rejects missing tokens and forged ones (alg confusion included)"""
    assert api("GET", "/v1/whoami").status_code == 401, "no token was accepted"
    now = int(time.time())
    forged = jwt.encode({"preferred_username": "alice", "aud": "kl-api", "iss": ISSUER,
                         "iat": now, "exp": now + 300}, "forged-key-that-is-not-the-idp-signing-key", algorithm="HS256")
    r = api("GET", "/v1/whoami", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401, f"forged HS256 token got {r.status_code}"


@check
def identity_resolves_workspaces():
    """Identity: alice's token carries her group; OpenFGA resolves her workspaces and admin rights"""
    r = api("GET", "/v1/whoami", "alice")
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
    me = r.json()
    assert me["groups"] == ["payments"], me
    assert {"payments", "commons"} <= set(me["workspaces"]) and "finance" not in me["workspaces"], me
    assert me["admin_of"] == ["payments"], me


# ------------------------------------------------------------------ scope and permissions
@check
def workspace_scope():
    """Scope: alice searching payments sees the live runbook only"""
    got = search_ids("alice", "payments")
    assert got == {"pay-runbook"}, got


@check
def org_toggle_alice():
    """Org toggle widens scope, not permissions (alice: payments)"""
    got = search_ids("alice")
    assert got == {"pay-runbook", "handbook"}, got


@check
def org_toggle_bob():
    """Org toggle widens scope, not permissions (bob: finance)"""
    got = search_ids("bob")
    assert got == {"fin-close", "handbook"}, got


@check
def org_toggle_carol():
    """A member of two teams sees both teams and nothing more (carol)"""
    got = search_ids("carol")
    assert got == {"pay-runbook", "fin-close", "handbook"}, got


@check
def private_workspace():
    """Private workspace: 404 for non-members, visible to members (dave)"""
    r = api("GET", "/v1/search", "alice", params={"q": "incident", "scope": "legal"})
    assert r.status_code == 404, f"non-member targeting a private workspace got {r.status_code}"
    got = search_ids("dave")
    assert got == {"handbook", "legal-hold"}, got


@check
def drift_is_caught():
    """ACL drift: a row the index allows but OpenFGA denies never reaches anyone, and is audited"""
    with psycopg.connect(OWNER_DB) as conn:
        acl = conn.execute("SELECT acl_principals FROM kl.documents WHERE external_key = 'pay-drift'").fetchone()[0]
    assert acl == ["group:payments"], f"seed changed: {acl}"
    assert "pay-drift" not in (search_ids("alice") | search_ids("carol")), "drifted document was returned"
    with psycopg.connect(OWNER_DB) as conn:
        n = conn.execute("SELECT count(*) FROM kl.audit_events WHERE action = 'search' "
                         "AND details->'dropped_by_post_check' ? 'pay-drift'").fetchone()[0]
    assert n > 0, "post-check drops are not being audited"


# ------------------------------------------------------------------ secrets and models
@check
def secrets_stay_out_of_postgres():
    """Secrets: an admin registers a model; the key lands in OpenBao, only a reference in Postgres"""
    name, key = "smoke-" + secrets.token_hex(3), "sk-smoke-" + secrets.token_hex(16)
    r = api("POST", "/v1/workspaces/payments/models", "alice",
            json={"name": name, "provider": "openai_compatible", "model": "smoke-model", "api_key": key})
    assert r.status_code == 201, f"{r.status_code} {r.text[:300]}"
    assert key not in r.text, "the API echoed the key back"
    ref = r.json()["credential_ref"]
    assert ref.startswith("openbao://secret/"), ref
    with psycopg.connect(OWNER_DB) as conn:
        row = conn.execute("SELECT row_to_json(m)::text FROM kl.model_endpoints m WHERE name = %s",
                           (name,)).fetchone()[0]
        audit = conn.execute("SELECT coalesce(string_agg(details::text, ' '), '') FROM kl.audit_events").fetchone()[0]
    assert key not in row and key not in audit, "secret material found in Postgres"
    v = httpx.get(f"{BAO_URL}/v1/secret/data/{ref.split('openbao://secret/', 1)[1]}",
                  headers={"X-Vault-Token": BAO_TOKEN}, timeout=10)
    assert v.status_code == 200 and v.json()["data"]["data"]["api_key"] == key, "key not found in OpenBao"


@check
def non_admin_cannot_register():
    """Secrets: a non-admin cannot register models in another team's workspace"""
    r = api("POST", "/v1/workspaces/payments/models", "bob",
            json={"name": "nope", "provider": "anthropic", "model": "x", "api_key": "k"})
    assert r.status_code == 403, f"expected 403, got {r.status_code}"


# ------------------------------------------------------------------ connectors and jobs (M1 slice 1)
@check
def ingest_role_fails_closed():
    """RLS: the worker role sees no jobs or connections without an org context"""
    with psycopg.connect(INGEST_DB) as conn:
        n = conn.execute("SELECT (SELECT count(*) FROM jobs) + (SELECT count(*) FROM connections)").fetchone()[0]
    assert n == 0, f"worker role saw {n} rows with no org context"


@check
def worker_runs_jobs():
    """Jobs: the worker claims a queued job and finishes it inside its org"""
    with psycopg.connect(OWNER_DB) as conn:
        org = conn.execute("SELECT id FROM kl.orgs WHERE slug = 'acme'").fetchone()[0]
        job_id = conn.execute("INSERT INTO kl.jobs (org_id, kind) VALUES (%s, 'noop') RETURNING id", (org,)).fetchone()[0]
    deadline, status = time.monotonic() + 60, None
    while time.monotonic() < deadline:
        with psycopg.connect(OWNER_DB) as conn:
            status = conn.execute("SELECT status FROM kl.jobs WHERE id = %s", (job_id,)).fetchone()[0]
        if status == "done":
            return
        time.sleep(1)
    raise AssertionError(f"job {job_id} is still {status!r} after 60s; check `docker compose logs worker`")


@check
def non_admin_cannot_connect():
    """Connections: a non-admin cannot connect sources to another team's workspace"""
    r = api("POST", "/v1/workspaces/payments/connections", "bob", json={"name": "nope", "installation_id": 1})
    assert r.status_code == 403, f"expected 403, got {r.status_code}"


@check
def identity_links_are_per_user():
    """Identities: a source identity link expands only its own user (bob = octocat, carol unlinked)"""
    bob, carol = api("GET", "/v1/whoami", "bob").json(), api("GET", "/v1/whoami", "carol").json()
    assert bob.get("identities") == ["ext:github.octocat"], f"bob: {bob.get('identities')}"
    assert carol.get("identities") == [], f"carol: {carol.get('identities')}"


@check
def webhooks_reject_unsigned():
    """Webhooks: unsigned and wrongly signed GitHub deliveries are rejected"""
    body = json.dumps({"zen": "Keep it logically awesome."}).encode()
    base = {"X-GitHub-Event": "ping", "X-GitHub-Delivery": str(uuid.uuid4()), "Content-Type": "application/json"}
    unsigned = httpx.post(f"{API}/v1/webhooks/github", content=body, headers=base, timeout=30)
    forged = httpx.post(f"{API}/v1/webhooks/github", content=body, timeout=30,
                        headers={**base, "X-Hub-Signature-256": "sha256=" + hmac.new(b"guess", body, hashlib.sha256).hexdigest()})
    assert unsigned.status_code == 401 and forged.status_code == 401, \
        f"unsigned got {unsigned.status_code}, forged got {forged.status_code}"


# ------------------------------------------------------------------ semantic search (M2)
PARAPHRASE = "who do I contact if checkout goes down"  # shares no keywords with the payments runbook


def seed_hits(user: str, q: str, scope: str = "org") -> list[dict]:
    r = api("GET", "/v1/search", user, params={"q": q, "scope": scope, "limit": 50})
    assert r.status_code == 200, f"search as {user}: {r.status_code} {r.text[:200]}"
    return [h for h in r.json()["results"] if h["id"] in SEED_DOCS]


@check
def embeddings_cover_seed():
    """Embeddings: every seed chunk gets a vector from the org default model"""
    deadline, missing = time.monotonic() + 180, None
    while time.monotonic() < deadline:
        with psycopg.connect(OWNER_DB) as conn:
            missing = conn.execute(
                """SELECT count(*) FROM kl.chunks c JOIN kl.documents d ON d.id = c.document_id
                   WHERE d.container_ref IS NULL
                     AND NOT EXISTS (SELECT 1 FROM kl.chunk_embeddings e WHERE e.chunk_id = c.id)""").fetchone()[0]
        if missing == 0:
            return
        time.sleep(2)
    raise AssertionError(f"{missing} seed chunk(s) still unembedded after 180s; check `docker compose logs worker`")


@check
def semantic_search_finds_paraphrase():
    """Semantic search: a question sharing no keywords with the runbook still finds it"""
    hits = seed_hits("alice", PARAPHRASE, scope="payments")
    runbook = next((h for h in hits if h["id"] == "pay-runbook"), None)
    assert runbook, f"the paraphrase didn't find the runbook: {[h['id'] for h in hits]}"
    assert runbook["matched"] == ["vector"], f"expected a match by meaning only, got {runbook['matched']}"


@check
def semantic_search_ranks_by_meaning():
    """Semantic search: an accounting question ranks the month-end close doc first (carol sees three docs)"""
    hits = seed_hits("carol", "accounting period deadline")
    assert hits and hits[0]["id"] == "fin-close", f"ranking was {[h['id'] for h in hits]}"


@check
def vector_path_obeys_permissions():
    """Semantic search: matches by meaning obey the same permissions (bob and dave never get the runbook)"""
    for user in ("bob", "dave"):
        ids = {h["id"] for h in seed_hits(user, PARAPHRASE)}
        assert not ids & {"pay-runbook", "pay-drift"}, f"{user} got {sorted(ids)}"


# ------------------------------------------------------------------ ask (slice A)
FAKE_LLM = "http://fake-llm:8080"
PAGE_QUESTION = "who do I page when payments are down?"
CLOSE_TEXT = "controller within one business day"  # appears only in the confidential close checklist


def fake_prompts() -> list[str]:
    return [m.get("content", "") for req in httpx.get(f"{FAKE_LLM}/requests", timeout=10).json()
            for m in req.get("messages", [])]


def reset_fake() -> None:
    httpx.delete(f"{FAKE_LLM}/requests", timeout=10)


def ensure_fake_models() -> None:
    """payments/smoke-fake allows public + internal and is registered through the API by alice (payments admin).
    finance/smoke-fake also allows confidential; it's set up directly because no seed user administers finance.
    Dev OpenBao keeps secrets in memory, so after a restart these rows outlive their keys: the fake keys are
    written back on every run."""
    r = api("POST", "/v1/workspaces/payments/models", "alice", json={
        "name": "smoke-fake", "provider": "openai_compatible", "model": "fake",
        "base_url": "http://fake-llm:8080/v1", "api_key": "unused"})
    assert r.status_code in (201, 409), f"register payments model: {r.status_code} {r.text[:200]}"
    for ws in ("payments", "finance"):
        v = httpx.post(f"{BAO_URL}/v1/secret/data/kl/acme/{ws}/models/smoke-fake",
                       headers={"X-Vault-Token": BAO_TOKEN}, json={"data": {"api_key": "unused"}}, timeout=10)
        assert v.status_code in (200, 204), f"secrets backend: {v.status_code}"
    with psycopg.connect(OWNER_DB) as conn:
        if conn.execute("SELECT 1 FROM kl.model_endpoints m JOIN kl.workspaces w ON w.id = m.workspace_id "
                        "WHERE w.slug = 'finance' AND m.name = 'smoke-fake'").fetchone():
            return
        org_id, ws_id = conn.execute("SELECT org_id, id FROM kl.workspaces WHERE slug = 'finance'").fetchone()
        conn.execute("""INSERT INTO kl.model_endpoints (org_id, workspace_id, name, provider, model, base_url,
                             credential_ref, allowed_classifications, created_by)
                        VALUES (%s, %s, 'smoke-fake', 'openai_compatible', 'fake', 'http://fake-llm:8080/v1',
                                'openbao://secret/kl/acme/finance/models/smoke-fake',
                                '{public,internal,confidential}', 'smoke')""", (org_id, ws_id))


def ask_as(user: str, question: str, model: str, scope: str = "org") -> httpx.Response:
    return api("POST", "/v1/ask", user, json={"question": question, "model": model, "scope": scope})


@check
def ask_answers_with_citations():
    """Ask: alice's answer cites the payments runbook; sources reach the model labelled as untrusted data"""
    ensure_fake_models()
    reset_fake()
    r = ask_as("alice", PAGE_QUESTION, "payments/smoke-fake", scope="payments")
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    body = r.json()
    assert "pay-runbook" in [c["id"] for c in body["citations"]], f"citations: {body['citations']}"
    assert any(c["cited"] for c in body["citations"]), "the answer cites no source"
    prompts = fake_prompts()
    assert any("untrusted data" in p for p in prompts), "sources weren't labelled as untrusted"
    assert any("page the payments on-call" in p for p in prompts), "the runbook text never reached the model"


@check
def ask_never_calls_model_without_sources():
    """Ask: bob gets 'couldn't find' for payments content, and the model is never called"""
    ensure_fake_models()
    reset_fake()
    r = ask_as("bob", PAGE_QUESTION, "finance/smoke-fake", scope="payments")
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    body = r.json()
    assert body["citations"] == [] and body["answer"].startswith("I couldn't find"), body
    assert fake_prompts() == [], "the model was called with no visible sources"


@check
def ask_model_needs_membership():
    """Ask: a model in a workspace you're not in can't be used or discovered (dave and the finance model: 404)"""
    r = ask_as("dave", PAGE_QUESTION, "finance/smoke-fake")
    assert r.status_code == 404, f"expected 404, got {r.status_code}"


@check
def egress_withholds_confidential():
    """Egress: confidential text never reaches a model that may not receive it; an allowed model does get it"""
    ensure_fake_models()
    reset_fake()
    blocked = ask_as("carol", "what delays the month-end close?", "payments/smoke-fake")
    assert blocked.status_code == 200, f"{blocked.status_code} {blocked.text[:300]}"
    assert "fin-close" not in [c["id"] for c in blocked.json()["citations"]], "confidential doc sent to a non-confidential model"
    assert blocked.json()["withheld_by_policy"] >= 1, "nothing was withheld"
    assert not any(CLOSE_TEXT in p for p in fake_prompts()), "confidential text reached a model that may not receive it"
    reset_fake()
    allowed = ask_as("carol", "what delays the month-end close?", "finance/smoke-fake")
    assert allowed.status_code == 200, f"{allowed.status_code} {allowed.text[:300]}"
    assert "fin-close" in [c["id"] for c in allowed.json()["citations"]], "control: the finance model should receive it"
    assert any(CLOSE_TEXT in p for p in fake_prompts()), "control: confidential text should reach the allowed model"


# ------------------------------------------------------------------ MCP (slice B)
MCP_URL = os.environ.get("KL_MCP_URL", "http://mcp:8090")
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
MCP_INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "1"}}}


async def _mcp_call(raw_token: str, tool: str, args: dict):
    from mcp import ClientSession
    try:  # newer v1 SDKs
        from mcp.client.streamable_http import streamable_http_client
        transport = streamable_http_client(f"{MCP_URL}/mcp", http_client=httpx.AsyncClient(
            headers={"Authorization": f"Bearer {raw_token}"}, timeout=60))
    except ImportError:  # older v1 SDKs
        from mcp.client.streamable_http import streamablehttp_client
        transport = streamablehttp_client(f"{MCP_URL}/mcp", headers={"Authorization": f"Bearer {raw_token}"})
    async with transport as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, args)
    # Checked after the client closes: raised inside it, anyio would wrap the failure in an ExceptionGroup.
    text = result.content[0].text if result.content else ""
    assert not result.isError, f"MCP tool {tool} failed: {text[:300]}"
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured.get("result", structured)
    return json.loads(text)


def mcp_call(user: str, tool: str, args: dict):
    return asyncio.run(_mcp_call(token(user, client_id="kl-mcp-cli"), tool, args))


@check
def mcp_requires_a_token():
    """MCP: no token gets 401 pointing at protected-resource metadata, which names Keycloak as the auth server"""
    r = httpx.post(f"{MCP_URL}/mcp", json=MCP_INIT, headers=MCP_HEADERS, timeout=30)
    assert r.status_code == 401, f"expected 401, got {r.status_code}"
    found = re.search(r'resource_metadata="([^"]+)"', r.headers.get("www-authenticate", ""))
    assert found, f"WWW-Authenticate has no resource_metadata: {r.headers.get('www-authenticate')!r}"
    path = "/" + found.group(1).split("/", 3)[3]  # same path, reached from inside the network
    servers = [s.rstrip("/") for s in httpx.get(f"{MCP_URL}{path}", timeout=30).json().get("authorization_servers", [])]
    assert ISSUER.rstrip("/") in servers, f"metadata lists {servers}"


@check
def mcp_rejects_api_tokens():
    """MCP: a token issued for the REST API is refused here (audience binding: no token reuse across services)"""
    r = httpx.post(f"{MCP_URL}/mcp", json=MCP_INIT, timeout=30,
                   headers={**MCP_HEADERS, "Authorization": f"Bearer {token('alice')}"})
    assert r.status_code == 401, f"an API-audience token got {r.status_code}"


@check
def mcp_search_uses_caller_permissions():
    """MCP: search through MCP gives alice her documents and bob his, never each other's"""
    alice = {h["id"] for h in mcp_call("alice", "search", {"query": "incident", "limit": 50})["results"]}
    bob = {h["id"] for h in mcp_call("bob", "search", {"query": "incident", "limit": 50})["results"]}
    assert "pay-runbook" in alice and "fin-close" not in alice, f"alice via MCP: {sorted(alice)}"
    assert "fin-close" in bob and "pay-runbook" not in bob, f"bob via MCP: {sorted(bob)}"


@check
def mcp_ask_cites_sources():
    """MCP: ask through MCP answers with citations on the same egress-checked path"""
    ensure_fake_models()
    out = mcp_call("alice", "ask", {"question": PAGE_QUESTION, "model": "payments/smoke-fake", "scope": "payments"})
    assert "pay-runbook" in [c["id"] for c in out["citations"]], f"citations via MCP: {out.get('citations')}"


# ------------------------------------------------------------------ OKF concepts (slice C1)
SEED_KEYS = ["pay-runbook", "fin-close", "handbook", "pay-drift", "legal-hold"]


def bronze_rows(keys: list[str], seconds: int = 180) -> list[tuple]:
    """Wait for the worker to build bronze concepts for these keys; returns (key, OKF text) pairs."""
    deadline, rows = time.monotonic() + seconds, []
    while time.monotonic() < deadline:
        with psycopg.connect(OWNER_DB) as conn:
            rows = conn.execute(
                "SELECT c.concept_key, v.content, v.key_ref FROM kl.concepts c JOIN kl.concept_versions v "
                "ON v.concept_id = c.id AND v.hash = c.latest_hash "
                "WHERE c.tier = 'bronze' AND c.concept_key = ANY(%s)", (keys,)).fetchall()
        if len(rows) == len(keys):
            from .originals import open_one  # stored encrypted since slice I3b
            return [(key, open_one(content, key_ref)) for key, content, key_ref in rows]
        time.sleep(2)
    raise AssertionError(f"bronze concepts built for {sorted(r[0] for r in rows)} of {sorted(keys)}")


@check
def bronze_concepts_are_valid_okf():
    """OKF: every seed document becomes a bronze concept that passes OKF v0.2 validation"""
    from . import okf
    for key, text in bronze_rows(SEED_KEYS):
        concept = okf.parse(text, f"/bronze/{key}.md")
        assert okf.validate(concept) == [], okf.validate(concept)
        assert concept.meta["x-kl"]["id"] == key and concept.meta["x-kl"]["audience"] == "source"


@check
def concepts_follow_source_permissions():
    """OKF: a concept is readable exactly by its source's readers; drift and unknown keys get 404"""
    bronze_rows(SEED_KEYS)
    expected = {("alice", "pay-runbook"): 200, ("carol", "pay-runbook"): 200, ("bob", "pay-runbook"): 404,
                ("alice", "fin-close"): 404, ("bob", "fin-close"): 200, ("alice", "pay-drift"): 404,
                ("alice", "legal-hold"): 404, ("dave", "legal-hold"): 200, ("alice", "no-such-concept"): 404}
    actual = {k: api("GET", f"/v1/concepts/{k[1]}", k[0]).status_code for k in expected}
    assert actual == expected, {k: v for k, v in actual.items() if expected[k] != v}
    assert api("GET", "/v1/concepts/pay-runbook/history", "bob").status_code == 404, "history leaks to non-readers"


@check
def concept_history_shows_progression():
    """OKF: a concept's history shows its bronze version, pinned to the source revision"""
    bronze_rows(["pay-runbook"])
    concept = api("GET", "/v1/concepts/pay-runbook", "alice").json()
    history = api("GET", "/v1/concepts/pay-runbook/history", "alice").json()
    assert concept["source_version"] == "a1b2c3d" and concept["tier"] == "bronze", concept
    assert concept["hash"] in [v["hash"] for v in history["versions"]], "latest version missing from history"
    assert any(e["kind"] == "bronze_version" and e["detail"]["hash"] == concept["hash"] for e in history["events"])


@check
def okf_validator_catches_problems():
    """OKF: the validator rejects broken concepts, enforces the audience rule, and keeps unknown keys"""
    from . import okf
    good = ("---\ntype: Playbook\ntitle: T\nstatus: stable\ncustom_key: kept\n"
            "sources:\n  - {id: s1, resource: 'https://example.test/a'}\n"
            "x-kl:\n  audience: workspace/payments\n  classification: internal\n---\n"
            "# T\n\nPage the lead [^s1]. See [the ledger](/systems/ledger.md).\n")
    known = {"/t.md", "/systems/ledger.md"}
    concept = okf.parse(good, "/t.md")
    assert okf.validate(concept, known, "workspace/payments") == [], okf.validate(concept, known, "workspace/payments")
    assert okf.parse(okf.render(concept), "/t.md").meta["custom_key"] == "kept", "unknown key lost on round-trip"
    broken = {
        "missing type": (good.replace("type: Playbook\n", ""), "workspace/payments"),
        "bad status": (good.replace("status: stable", "status: final"), "workspace/payments"),
        "audience wider than its sources": (good, "group/payments-leads"),
        "audience in another workspace": (good, "workspace/finance"),
        "broken link": (good.replace("/systems/ledger.md", "/systems/missing.md"), "workspace/payments"),
        "unknown classification": (good.replace("classification: internal", "classification: secret"), "workspace/payments"),
        "footnote without a source": (good.replace("[^s1]", "[^nope]"), "workspace/payments"),
    }
    missed = [name for name, (text, floor) in broken.items() if not okf.validate(okf.parse(text, "/t.md"), known, floor)]
    assert not missed, f"validator missed: {missed}"


# ------------------------------------------------------------------ curated knowledge and graph (slice C2a)
KB_TIERS = {"kb-sev1-playbook": "gold", "kb-ledger": "silver", "kb-settlement-keys": "silver",
            "kb-incident-comms": "gold", "kb-legal-escalation": "silver"}
KB_EDGES = {("kb-sev1-playbook", "kb-ledger"), ("kb-sev1-playbook", "kb-incident-comms"),
            ("kb-sev1-playbook", "kb-legal-escalation"), ("kb-ledger", "kb-settlement-keys")}


def curated_ready(seconds: int = 180) -> None:
    """Wait for the worker to index the seed bundles and embed their chunks."""
    deadline, state = time.monotonic() + seconds, None
    while time.monotonic() < deadline:
        with psycopg.connect(OWNER_DB) as conn:
            tiers = dict(conn.execute("SELECT concept_key, tier FROM kl.concepts WHERE concept_key LIKE 'kb-%'").fetchall())
            edges = set(conn.execute("SELECT f.concept_key, e.to_key FROM kl.concept_edges e "
                                     "JOIN kl.concepts f ON f.id = e.from_concept_id").fetchall())
            unembedded = conn.execute(
                "SELECT count(*) FROM kl.chunks c JOIN kl.documents d ON d.id = c.document_id WHERE d.tier <> 'evidence' "
                "AND NOT EXISTS (SELECT 1 FROM kl.chunk_embeddings e WHERE e.chunk_id = c.id)").fetchone()[0]
        state = (tiers, sorted(edges), unembedded)
        if tiers == KB_TIERS and KB_EDGES <= edges and unembedded == 0:
            return
        time.sleep(2)
    raise AssertionError(f"seed bundles not ready (concepts, edges, unembedded chunks): {state}; "
                         "check `docker compose logs worker`")


def alice_search(q: str, limit: int = 20) -> list[dict]:
    r = api("GET", "/v1/search", "alice", params={"q": q, "scope": "payments", "limit": limit})
    assert r.status_code == 200, r.text[:200]
    return r.json()["results"]


@check
def curated_bundles_indexed():
    """Curated: the seed bundles index as silver and gold concepts, with their relations as graph edges"""
    curated_ready()


@check
def curated_ranks_above_evidence():
    """Curated: the reviewed Sev1 playbook ranks above the runbook it summarizes"""
    curated_ready()
    ids = [h["id"] for h in alice_search(PAGE_QUESTION)]
    assert "kb-sev1-playbook" in ids and "pay-runbook" in ids, ids
    assert ids.index("kb-sev1-playbook") < ids.index("pay-runbook"), f"order: {ids}"


@check
def canonical_lookup_goes_first():
    """Curated: asking for a concept by one of its aliases returns that concept first"""
    curated_ready()
    hits = alice_search("Sev1 Playbook")
    assert hits and hits[0]["id"] == "kb-sev1-playbook" and "canonical" in hits[0]["matched"], hits[:2]


@check
def graph_reaches_related_concepts():
    """Graph: search follows a relation out of its scope to a concept the caller can see, never to hidden ones"""
    curated_ready()
    hits = {h["id"]: h for h in alice_search(PAGE_QUESTION, limit=50)}
    comms = hits.get("kb-incident-comms")  # lives in commons, outside the payments scope: only the graph finds it
    assert comms and "graph" in comms["matched"] and comms["related_to"] == "kb-sev1-playbook", sorted(hits)
    leaked = {"kb-legal-escalation", "kb-settlement-keys"} & set(hits)
    assert not leaked, f"graph traversal leaked {leaked}"


@check
def hidden_concepts_stay_hidden():
    """Graph: an edge never reveals a concept the caller can't see (narrower audience, private workspace)"""
    curated_ready()
    related = {r["id"] for r in api("GET", "/v1/concepts/kb-sev1-playbook/related", "alice").json()["related"]}
    assert related == {"kb-ledger", "kb-incident-comms"}, f"alice's neighbors of the playbook: {related}"
    assert api("GET", "/v1/concepts/kb-ledger/related", "alice").json()["related"] == [], "a hidden neighbor leaked"
    expected = {("alice", "kb-settlement-keys"): 404, ("alice", "kb-legal-escalation"): 404,
                ("dave", "kb-legal-escalation"): 200, ("carol", "kb-sev1-playbook"): 200,
                ("bob", "kb-sev1-playbook"): 404, ("dave", "kb-sev1-playbook"): 404}
    actual = {k: api("GET", f"/v1/concepts/{k[1]}", k[0]).status_code for k in expected}
    assert actual == expected, {k: v for k, v in actual.items() if expected[k] != v}
    assert api("GET", "/v1/concepts/kb-sev1-playbook/related", "bob").status_code == 404


@check
def curated_concept_history():
    """Curated: a curated concept reads back as OKF, with an indexed event in its history"""
    curated_ready()
    concept = api("GET", "/v1/concepts/kb-sev1-playbook", "alice").json()
    assert concept["tier"] == "gold" and "kb-sev1-playbook" in concept["okf"], concept
    kinds = [e["kind"] for e in api("GET", "/v1/concepts/kb-sev1-playbook/history", "alice").json()["events"]]
    assert "indexed" in kinds, kinds


@check
def mcp_get_concept():
    """MCP: get_concept returns a concept to its readers and 'not found' to everyone else"""
    curated_ready()
    assert mcp_call("alice", "get_concept", {"key": "kb-sev1-playbook"})["tier"] == "gold"
    try:
        mcp_call("bob", "get_concept", {"key": "kb-sev1-playbook"})
    except AssertionError as e:
        assert "not found" in str(e), str(e)
    else:
        raise AssertionError("bob got a concept he can't see")


@check
def knowledge_ci_rules():
    """Knowledge CI: seed bundles pass; missing or duplicate ids, secrets, and card numbers fail, unechoed"""
    from . import okf
    for bundle in ("payments-knowledge", "commons-knowledge", "legal-knowledge"):
        problems = okf.check_bundle(f"/seed-knowledge/{bundle}", knowledge=True)
        assert problems == [], problems
    head = "---\ntype: Note\nx-kl:\n  id: n1\n---\n"
    fake_key = "AKIA" + "IOSFODNN7EXAMPLE"  # built at runtime, so no key-shaped string sits in the repo
    files = {"/a.md": head + "fine\n", "/b.md": head + "same id\n", "/c.md": "---\ntype: Note\n---\nno id\n",
             "/d.md": head.replace("n1", "n4") + f"key {fake_key}\n",
             "/e.md": head.replace("n1", "n5") + "card 4111 1111 1111 1111\n"}
    problems = "\n".join(okf.check_files(files, knowledge=True)[0])
    for expected in ("/b.md: `x-kl.id` n1 is already used", "/c.md: `x-kl.id` is required",
                     "/d.md:6: possible AWS access key", "/e.md:6: possible payment card number"):
        assert expected in problems, f"missing {expected!r} in:\n{problems}"
    assert fake_key not in problems and "4111" not in problems, "a finding echoed the secret"
    assert not any(line.startswith("/a.md") for line in problems.splitlines()), problems


# ------------------------------------------------------------------ compiler (slice C3a)
def compile_as(user: str, topic: str = PAGE_QUESTION, model: str = "payments/smoke-fake"):
    return api("POST", "/v1/workspaces/payments/compile", user, json={"topic": topic, "model": model})


@check
def compiler_audience_rule():
    """Compiler: a draft's audience is the org or a group on every kept source's reader list; others are left out"""
    from . import compiler

    def s(key, *acl):
        return {"external_key": key, "acl_principals": list(acl)}

    audience, kept, excluded = compiler.choose_audience("acme", [
        s("a", "group:payments"), s("b", "group:payments", "group:finance"), s("c", "ext:github.alice"),
        s("d", "group:finance")])
    got = (audience, [x["external_key"] for x in kept], [x["external_key"] for x in excluded])
    assert got == ("group/payments", ["a", "b"], ["c", "d"]), got
    assert compiler.choose_audience("acme", [s("a", "org:acme"), s("b", "org:acme", "group:x")])[0] == "org"
    assert compiler.choose_audience("acme", [s("c", "ext:github.alice", "user:bob")])[:2] == (None, []), \
        "sources readable only by named people must never get an audience"


@check
def compile_requires_admin_and_own_model():
    """Compiler: only a workspace admin can compile, and only with that workspace's own model"""
    ensure_fake_models()
    assert compile_as("carol").status_code == 403, "a member who isn't an admin compiled"
    assert compile_as("alice", model="finance/smoke-fake").status_code == 422, "another team's model was used"


@check
def compile_drafts_valid_concept():
    """Compiler: the draft passes knowledge CI, is pinned to its sources, and its audience can read them all"""
    from . import okf
    ensure_fake_models()
    reset_fake()
    r = compile_as("alice")
    assert r.status_code == 201, f"{r.status_code} {r.text[:300]}"
    out = r.json()
    path = f"/{out['concept_id']}.md"
    assert okf.check_files({path: out["okf"]}, knowledge=True)[0] == [], "the stored draft fails knowledge CI"
    meta = okf.parse(out["okf"], path).meta
    with psycopg.connect(OWNER_DB) as conn:
        uri = conn.execute("SELECT source_uri FROM kl.documents WHERE external_key = 'pay-runbook'").fetchone()[0]
    assert out["sources"] == ["pay-runbook"] and meta["sources"][0]["resource"] == uri, meta["sources"]
    assert meta["x-kl"]["audience"] == "group/payments" and meta["status"] == "draft", meta
    assert meta["generated"]["by"].startswith("kl-compiler/") and not meta.get("verified"), "a draft can't claim review"
    assert any("untrusted data" in p and f"Write the concept: {PAGE_QUESTION}" in p for p in fake_prompts()), \
        "sources didn't reach the compiler model labelled as untrusted data"


@check
def compile_proposal_readers():
    """Compiler: a proposal is readable by its audience (carol, in payments) and nobody else (bob, dave)"""
    ensure_fake_models()
    r = compile_as("alice")
    assert r.status_code == 201, r.text[:300]
    pid = r.json()["proposal"]
    assert api("GET", f"/v1/proposals/{pid}", "carol").status_code == 200
    assert [api("GET", f"/v1/proposals/{pid}", u).status_code for u in ("bob", "dave")] == [404, 404]

    def listed(user):
        return {p["id"] for p in api("GET", "/v1/workspaces/payments/proposals", user).json()["proposals"]}

    assert pid in listed("carol") and pid not in listed("bob"), "the proposal list ignores its readers"


@check
def compile_rejects_invalid_drafts():
    """Compiler: a draft citing a source it wasn't given fails knowledge checks and isn't stored"""
    ensure_fake_models()

    def stored() -> int:
        with psycopg.connect(OWNER_DB) as conn:
            return conn.execute("SELECT count(*) FROM kl.proposals").fetchone()[0]

    before = stored()
    r = compile_as("alice", topic=PAGE_QUESTION + " make-a-bad-draft")
    assert r.status_code == 422 and "[^s9]" in r.text, f"{r.status_code} {r.text[:300]}"
    assert stored() == before, "a rejected draft was stored"


# ------------------------------------------------------------------ curator (slice C3b)
@check
def curator_reader_rule():
    """Curator: a PR opens only if every repo reader is linked to someone who can read every source"""
    from .curator import unverified_readers
    links = {"rama": ["user:alice"], "eve": ["user:bob"]}
    can_read = lambda user: user == "user:alice"  # noqa: E731  (alice can read every source; bob can't)
    assert unverified_readers(["rama"], links, can_read) == []
    assert unverified_readers(["rama", "eve", "mallory"], links, can_read) == ["eve", "mallory"], \
        "a reader who can't read a source (eve) or isn't linked at all (mallory) must block the PR"


@check
def pr_request_rules():
    """Curator: only an admin of the proposal's workspace can request its PR; others get 403 or 404"""
    ensure_fake_models()
    r = compile_as("alice")
    assert r.status_code == 201, r.text[:300]
    pid = r.json()["proposal"]
    assert api("POST", f"/v1/proposals/{pid}/pr", "carol").status_code == 403, "a member who isn't an admin"
    assert api("POST", f"/v1/proposals/{pid}/pr", "bob").status_code == 404, "someone who can't read the proposal"
    assert api("POST", "/v1/proposals/00000000-0000-0000-0000-000000000000/pr", "alice").status_code == 404


# ------------------------------------------------------------------ knowledge map API (slice D1)
FIXTURES = set(SEED_KEYS) | set(KB_TIERS)  # GitHub-synced nodes follow GitHub's permissions; checked elsewhere
MAP_EXPECTED = {
    "alice": {"pay-runbook", "handbook", "kb-sev1-playbook", "kb-ledger", "kb-incident-comms"},
    "bob": {"fin-close", "handbook", "kb-incident-comms"},
    "carol": {"pay-runbook", "fin-close", "handbook", "kb-sev1-playbook", "kb-ledger", "kb-incident-comms"},
    "dave": {"handbook", "legal-hold", "kb-incident-comms", "kb-legal-escalation"},
}


def graph(user: str, **params) -> dict:
    r = api("GET", "/v1/graph", user, params=params)
    assert r.status_code == 200, f"graph as {user}: {r.status_code} {r.text[:300]}"
    return r.json()


@check
def map_shows_exactly_what_each_user_can_see():
    """Map: each user's graph holds exactly the documents and concepts they can see (drift, audiences, private teams)"""
    curated_ready()
    got = {user: {n["id"] for n in graph(user)["nodes"]} & FIXTURES for user in MAP_EXPECTED}
    wrong = {u: {"extra": sorted(got[u] - e), "missing": sorted(e - got[u])} for u, e in MAP_EXPECTED.items() if got[u] != e}
    assert not wrong, wrong


@check
def map_edges_never_reach_hidden_nodes():
    """Map: edges join only visible nodes, cross-team links are flagged, and a hidden concept leaves no edge behind"""
    curated_ready()
    for user in MAP_EXPECTED:
        g = graph(user)
        ids = {n["id"] for n in g["nodes"]}
        dangling = [e for e in g["edges"] if e["source"] not in ids or e["target"] not in ids]
        assert not dangling, f"{user} got edges to nodes they can't see: {dangling}"
    edges = {(e["source"], e["target"]): e for e in graph("alice")["edges"]}
    assert ("kb-sev1-playbook", "kb-ledger") in edges and not edges[("kb-sev1-playbook", "kb-ledger")]["cross_team"]
    assert edges.get(("kb-sev1-playbook", "kb-incident-comms"), {}).get("cross_team"), "payments to commons is cross-team"
    hidden = {"kb-legal-escalation", "kb-settlement-keys", "pay-drift"}
    assert not [k for k in edges if hidden & set(k)], "an edge points at a hidden node"


@check
def map_totals_count_visible_only():
    """Map: totals and team clusters count only what the viewer can see; only legal's member sees the legal cluster"""
    curated_ready()
    for user in MAP_EXPECTED:
        g = graph(user)
        t = g["totals"]
        assert t["nodes"] == len(g["nodes"]) == sum(t["by_tier"].values()) and t["edges"] == len(g["edges"]), t
        assert t["cross_team_edges"] == sum(e["cross_team"] for e in g["edges"])
        assert sum(c["nodes"] for c in g["clusters"]) == sum(len(n["workspaces"]) for n in g["nodes"])
        clusters = {c["workspace"] for c in g["clusters"]}
        assert ("legal" in clusters) == (user == "dave"), f"{user} sees clusters {sorted(clusters)}"


@check
def map_replays_and_scopes():
    """Map: a replay at a past date shows only what existed then; a team view shows that team; private teams 404"""
    curated_ready()
    assert graph("alice", as_of="2000-01-01T00:00:00Z")["nodes"] == [], "nothing existed in 2000"
    later = {n["id"] for n in graph("alice", as_of="2999-01-01T00:00:00Z")["nodes"]} & FIXTURES
    assert later == MAP_EXPECTED["alice"], later
    team = {n["id"] for n in graph("alice", scope="payments")["nodes"]} & FIXTURES
    assert team == {"pay-runbook", "kb-sev1-playbook", "kb-ledger"}, team
    assert api("GET", "/v1/graph", "alice", params={"scope": "legal"}).status_code == 404, "a private team's map leaked"
    assert api("GET", "/v1/graph", "alice", params={"as_of": "yesterday"}).status_code == 422


# ------------------------------------------------------------------ automatic map links (slice N)
@check
def map_links_appear_on_their_own():
    """Map: near-identical documents become semantic neighbors and the system they name an entity, visible only to their readers"""
    import uuid
    from .authz import fga
    ws = f"nl-{uuid.uuid4().hex[:8]}"  # a throwaway team: the seed teams stay untouched
    system = f"{ws}-gateway"
    texts = {f"{ws}-a": f"Restarting the `{system}`: drain traffic, restart the {system}, then confirm its health check passes.",
             f"{ws}-b": f"Restarting the `{system}`: drain traffic, restart the {system}, and confirm its health check passes.",
             f"{ws}-c": "Expense reports are due on the fifth business day, with receipts for anything over fifty dollars."}
    a, b, c = texts
    org, ids = acme_org_id(), {}
    with psycopg.connect(OWNER_DB) as conn:
        ws_id = conn.execute("INSERT INTO kl.workspaces (org_id, slug, name, discoverability) "
                             "VALUES (%s, %s, 'Links test', 'private') RETURNING id", (org, ws)).fetchone()[0]
        for key, text in texts.items():
            ids[key] = conn.execute(
                "INSERT INTO kl.documents (org_id, source_kind, source_uri, source_version, external_key, title, content_hash, "
                "classification, acl_principals) VALUES (%s, 'seed', %s, 'v1', %s, %s, %s, 'internal', %s) RETURNING id",
                (org, f"https://kb.acme.test/{key}", key, f"Links test {key[-1]}", key, ["user:alice"])).fetchone()[0]
            conn.execute("INSERT INTO kl.chunks (org_id, document_id, ordinal, heading_path, body, acl_principals) "
                         "VALUES (%s, %s, 0, NULL, %s, %s)", (org, ids[key], text, ["user:alice"]))
            conn.execute("INSERT INTO kl.workspace_documents (workspace_id, document_id, org_id) VALUES (%s, %s, %s)",
                         (ws_id, ids[key], org))
        conn.execute("INSERT INTO kl.jobs (org_id, kind) VALUES (%s, 'embeddings.backfill')", (org,))  # embed, then link
    tuples = {("user:alice", "member", f"workspace:{ws}")} | {("user:alice", "viewer", f"document:{k}") for k in texts}
    fga.write(writes=tuples)
    left = None
    try:
        ready, deadline = 0, time.monotonic() + 120
        while time.monotonic() < deadline:
            with psycopg.connect(OWNER_DB) as conn:
                ready = conn.execute("SELECT count(*) FROM kl.document_vectors v JOIN kl.entity_scans s "
                                     "ON s.document_id = v.document_id WHERE v.document_id = ANY(%s)",
                                     (list(ids.values()),)).fetchone()[0]
            if ready >= len(ids):
                break
            time.sleep(2)
        assert ready >= len(ids), "the test documents never got their vectors and entity scans"
        g = graph("alice", scope=ws, min_mentions=1)
        semantic = {frozenset((e["source"], e["target"])): e for e in g["edges"] if e["kind"] == "semantic"}
        assert frozenset((a, b)) in semantic and semantic[frozenset((a, b))]["score"] > 0.8, \
            f"near-identical documents weren't linked: {list(semantic.values())}"
        entity = f"entity:system:{system}"
        entities = {n["id"]: n for n in g["nodes"] if n["tier"] == "entity"}
        assert entity in entities and entities[entity]["workspaces"] == [ws], f"the system both name isn't an entity: {sorted(entities)}"
        mentions = {(e["source"], e["target"]) for e in g["edges"] if e["kind"] == "mentions"}
        assert {(a, entity), (b, entity)} <= mentions and (c, entity) not in mentions, f"mention edges: {sorted(mentions)}"
        # bob can't see the documents, so the entity and the edge don't exist for him: not on his map, not under RLS.
        assert entity not in {n["id"] for n in graph("bob", min_mentions=1)["nodes"]}, "bob's map shows an entity named only in alice's documents"
        bob = ["user:bob", "org:acme", "group:finance", "group:commons", "ext:github.octocat"]
        with app_conn(bob, org) as conn:
            assert not conn.execute("SELECT 1 FROM kl.entities WHERE key = %s", (system,)).fetchall(), "RLS let bob read the entity"
            assert not conn.execute("SELECT 1 FROM kl.semantic_edges WHERE source_id = ANY(%s) OR target_id = ANY(%s)",
                                    (list(ids.values()), list(ids.values()))).fetchall(), "RLS let bob read the documents' edges"
        with app_conn(ALICE, org) as conn:
            assert conn.execute("SELECT 1 FROM kl.entities WHERE key = %s", (system,)).fetchall(), "RLS hid the entity from alice"
    finally:
        with psycopg.connect(OWNER_DB) as conn:
            conn.execute("DELETE FROM kl.documents WHERE id = ANY(%s)", (list(ids.values()),))
            conn.execute("DELETE FROM kl.workspaces WHERE slug = %s", (ws,))
            left = conn.execute("SELECT count(*) FROM kl.entities WHERE key = %s", (system,)).fetchone()[0]
        fga.write(deletes=tuples)
    assert left == 0, "the entity outlived the last document naming it"


@check
def entities_follow_visibility():
    """Map: an entity named only in documents you can't see doesn't exist for you (bob never sees the runbook's ledger-db)"""
    curated_ready()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:  # the seed's entities come from the same indexing pass
        with psycopg.connect(OWNER_DB) as conn:
            rows = conn.execute("SELECT e.kind, e.key, array_agg(d.external_key) FROM kl.entities e "
                                "JOIN kl.entity_mentions m ON m.entity_id = e.id JOIN kl.documents d ON d.id = m.document_id "
                                "GROUP BY e.kind, e.key").fetchall()
        if any(kind == "system" and key == "ledger-db" for kind, key, _ in rows):
            break
        time.sleep(2)
    assert any(kind == "system" and key == "ledger-db" for kind, key, _ in rows), "the seed runbook's ledger-db wasn't extracted"
    for user in MAP_EXPECTED:
        g = graph(user, min_mentions=1)
        docs = {n["id"] for n in g["nodes"] if n["tier"] != "entity"}
        shown = {n["id"] for n in g["nodes"] if n["tier"] == "entity"}
        unseen = {f"entity:{kind}:{key}" for kind, key, named_in in rows if not set(named_in) & docs}
        assert not shown & unseen, f"{user} sees entities named only in documents they can't: {sorted(shown & unseen)[:5]}"
        ids = {n["id"] for n in g["nodes"]}
        assert not [e for e in g["edges"] if e["source"] not in ids or e["target"] not in ids], f"{user} got a dangling edge"
    assert "entity:system:ledger-db" not in {n["id"] for n in graph("bob", min_mentions=1)["nodes"]}, "bob sees payments' ledger-db"
    assert "entity:system:ledger-db" in {n["id"] for n in graph("alice", min_mentions=1)["nodes"]}, "alice can't see ledger-db"


# ------------------------------------------------------------------ admin reads (slice D3)
@check
def models_list_for_members_only():
    """Admin: a team's members can list its models, never their credentials; non-members get 404"""
    ensure_fake_models()
    r = api("GET", "/v1/workspaces/payments/models", "alice")
    assert r.status_code == 200, r.text[:200]
    assert "smoke-fake" in {m["name"] for m in r.json()["models"]}, r.json()
    assert "openbao://" not in r.text and not any({"api_key", "credential_ref"} & set(m) for m in r.json()["models"]), \
        "a credential or its reference was returned"
    assert [api("GET", "/v1/workspaces/payments/models", u).status_code for u in ("bob", "dave")] == [404, 404]
    assert api("GET", "/v1/workspaces/legal/models", "alice").status_code == 404, "a private team's models leaked"


@check
def audit_trail_for_workspace_admins_only():
    """Admin: a workspace's admins read its audit trail, holding only that workspace's events; others get 403 or 404"""
    ensure_fake_models()
    assert compile_as("alice").status_code == 201  # leaves a compile event scoped to payments
    r = api("GET", "/v1/audit", "alice", params={"workspace": "payments", "limit": 100})
    assert r.status_code == 200, r.text[:200]
    events = r.json()["events"]
    assert events and all("payments" in e["scope"] for e in events), "an event from another workspace leaked"
    assert any(e["action"] == "compile" for e in events), [e["action"] for e in events][:10]
    assert api("GET", "/v1/audit", "carol", params={"workspace": "payments"}).status_code == 403, "a member who isn't an admin"
    assert api("GET", "/v1/audit", "bob", params={"workspace": "payments"}).status_code == 404
    assert api("GET", "/v1/audit", "alice", params={"workspace": "legal"}).status_code == 404, "a private team's existence leaked"


# ------------------------------------------------------------------ team purge (slice H1)
PURGE_DELAY = int(os.environ.get("KL_PURGE_DELAY_SECONDS", "10"))


@check
def purge_removes_a_whole_team():
    """Purge: removing a team deletes its documents, indexes, concepts, models, secrets, and authorization; shared documents stay"""
    import uuid
    from .authz import fga
    from .vault import vault
    ws = f"pt-{uuid.uuid4().hex[:8]}"  # a throwaway team: this check never touches the seed teams
    only, shared = f"{ws}-only", f"{ws}-shared"
    ids = {}
    with psycopg.connect(OWNER_DB) as conn:
        org = acme_org_id()
        ws_id = conn.execute("INSERT INTO kl.workspaces (org_id, slug, name, discoverability) "
                             "VALUES (%s, %s, 'Purge test', 'private') RETURNING id", (org, ws)).fetchone()[0]
        commons = conn.execute("SELECT id FROM kl.workspaces WHERE slug = 'commons'").fetchone()[0]
        for key in (only, shared):
            ids[key] = conn.execute(
                "INSERT INTO kl.documents (org_id, source_kind, source_uri, source_version, external_key, title, content_hash, "
                "classification, acl_principals) VALUES (%s, 'seed', %s, 'v1', %s, %s, %s, 'internal', %s) RETURNING id",
                (org, f"https://kb.acme.test/{key}", key, f"Purge test {key}", key, ["user:alice"])).fetchone()[0]
            conn.execute("INSERT INTO kl.chunks (org_id, document_id, ordinal, heading_path, body, acl_principals) "
                         "VALUES (%s, %s, 0, NULL, %s, %s)", (org, ids[key], f"purge test text for {key}", ["user:alice"]))
        conn.execute("INSERT INTO kl.workspace_documents (workspace_id, document_id, org_id) VALUES (%s, %s, %s), (%s, %s, %s), (%s, %s, %s)",
                     (ws_id, ids[only], org, ws_id, ids[shared], org, commons, ids[shared], org))
        conn.execute("INSERT INTO kl.jobs (org_id, kind) VALUES (%s, 'embeddings.backfill')", (org,))  # embed and build bronze
    fga.write(writes={("user:alice", "admin", f"workspace:{ws}"), ("user:alice", "member", f"workspace:{ws}"),
                      ("user:alice", "viewer", f"document:{only}")})
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:  # the team's document gets its bronze concept and its embedding
        with psycopg.connect(OWNER_DB) as conn:
            derived = conn.execute(
                "SELECT (SELECT count(*) FROM kl.concepts WHERE document_id = %s) + (SELECT count(*) FROM kl.chunk_embeddings e "
                "JOIN kl.chunks c ON c.id = e.chunk_id WHERE c.document_id = %s)", (ids[only], ids[only])).fetchone()[0]
        if derived >= 2:
            break
        time.sleep(2)
    assert derived >= 2, "the test team's document never got its concept and embedding"
    r = api("POST", f"/v1/workspaces/{ws}/models", "alice", json={
        "name": "pt-model", "provider": "openai_compatible", "model": "x", "base_url": f"{FAKE_LLM}/v1", "api_key": "purge-test-key"})
    assert r.status_code == 201, r.text[:200]
    with psycopg.connect(OWNER_DB) as conn:
        ref = conn.execute("SELECT credential_ref FROM kl.model_endpoints WHERE workspace_id = %s", (ws_id,)).fetchone()[0]
    assert vault.get(ref)["api_key"] == "purge-test-key"

    # Stored originals are encrypted per team (slice I3b): the team's own document under its key, the shared one
    # under the org's. Keep the team's ciphertext, as a backup taken before the purge would.
    from .originals import org_key, team_key

    def version_of(doc_id):
        with psycopg.connect(OWNER_DB) as conn:
            return conn.execute("SELECT v.concept_id, v.hash, v.content, v.key_ref FROM kl.concept_versions v "
                                "JOIN kl.concepts c ON c.id = v.concept_id WHERE c.document_id = %s", (doc_id,)).fetchone()
    deadline = time.monotonic() + 60
    while version_of(ids[shared]) is None and time.monotonic() < deadline:
        time.sleep(2)
    backup, shared_version = version_of(ids[only]), version_of(ids[shared])
    assert backup[3] == team_key(ws_id) and backup[2].startswith("vault:v1:") and "purge test text" not in backup[2], \
        f"the team's original isn't ciphertext under its key: {backup[3]}"
    assert shared_version and shared_version[3] == org_key(), "a document two teams hold should be under the org's key"
    # A document that was the team's alone when written, then shared, has its history under the team's key: the
    # purge must re-wrap it before destroying that key. Stage that case on the shared document.
    shared_text = vault.decrypt(org_key(), shared_version[2])
    vault.transit_key(team_key(ws_id))
    with psycopg.connect(OWNER_DB) as conn:
        conn.execute("UPDATE kl.concept_versions SET content = %s, key_ref = %s WHERE concept_id = %s AND hash = %s",
                     (vault.encrypt(team_key(ws_id), shared_text), team_key(ws_id), shared_version[0], shared_version[1]))

    # The request: only an admin, with the slug typed again, cancellable inside the window.
    assert api("POST", f"/v1/workspaces/{ws}/purge", "carol", json={"confirm": ws}).status_code == 404, "a private team leaked"
    assert api("POST", "/v1/workspaces/payments/purge", "carol", json={"confirm": "not-payments"}).status_code == 403
    assert api("POST", f"/v1/workspaces/{ws}/purge", "alice", json={"confirm": "payments"}).status_code == 422
    first = api("POST", f"/v1/workspaces/{ws}/purge", "alice", json={"confirm": ws})
    assert first.status_code == 202, first.text[:200]
    assert api("POST", f"/v1/workspaces/{ws}/purge", "alice", json={"confirm": ws}).status_code == 409
    assert api("DELETE", f"/v1/workspaces/{ws}/purge", "alice").status_code == 200, "cancelling inside the window"
    assert api("GET", f"/v1/purges/{first.json()['id']}", "alice").json()["status"] == "cancelled"
    second = api("POST", f"/v1/workspaces/{ws}/purge", "alice", json={"confirm": ws})
    assert second.status_code == 202, second.text[:200]
    pid = second.json()["id"]
    deadline = time.monotonic() + PURGE_DELAY + 120
    while (status := api("GET", f"/v1/purges/{pid}", "alice").json())["status"] != "done" and time.monotonic() < deadline:
        time.sleep(3)
    assert status["status"] == "done", status
    result = status["result"]
    assert result["documents"] == 1 and result["model_endpoints"] == 1 and result["remaining"] == 0, result
    assert result.get("rewrapped") == 1 and result.get("key_destroyed"), f"the purge kept its key or the shared history: {result}"
    try:
        vault.decrypt(backup[3], backup[2])
    except (httpx.HTTPStatusError, ValueError):
        pass
    else:
        raise AssertionError("a copy of the purged team's original, taken before the purge, can still be read")
    after = version_of(ids[shared])
    assert after[3] == org_key() and vault.decrypt(org_key(), after[2]) == shared_text, "the shared document's history didn't survive"

    # Everything the team owned is gone; the document it shared stays with the other team.
    with psycopg.connect(OWNER_DB) as conn:
        left = conn.execute(
            "SELECT (SELECT count(*) FROM kl.documents WHERE id = %(d)s) + (SELECT count(*) FROM kl.chunks WHERE document_id = %(d)s) "
            "+ (SELECT count(*) FROM kl.concepts WHERE document_id = %(d)s) + (SELECT count(*) FROM kl.workspaces WHERE id = %(w)s) "
            "+ (SELECT count(*) FROM kl.model_endpoints WHERE workspace_id = %(w)s)", {"d": ids[only], "w": ws_id}).fetchone()[0]
        kept = conn.execute("SELECT array_agg(w.slug) FROM kl.workspace_documents wd JOIN kl.workspaces w ON w.id = wd.workspace_id "
                            "WHERE wd.document_id = %s", (ids[shared],)).fetchone()[0]
        record = conn.execute("SELECT details FROM kl.audit_events WHERE action = 'workspace.purged' AND %s = ANY(scope)",
                              (ws,)).fetchone()
        conn.execute("DELETE FROM kl.documents WHERE id = %s", (ids[shared],))  # tidy up the shared test document
    assert left == 0, f"{left} row(s) of the purged team remain"
    assert kept == ["commons"], f"the shared document should stay with commons only, not {kept}"
    assert record and record[0]["remaining"] == 0, "no deletion record in the audit log"
    assert not fga.read({"object": f"workspace:{ws}"}) and not fga.read({"object": f"document:{only}"}), "authorization survived"
    try:
        vault.get(ref)
        raise AssertionError("the model's secret survived the purge")
    except httpx.HTTPStatusError as e:
        assert e.response.status_code == 404, e
    assert api("GET", f"/v1/purges/{pid}", "bob").status_code == 404, "someone else read the purge record"
    print(f"      {ws}: {result['documents']} document, {result['chunks']} chunk, {result['model_endpoints']} model, "
          f"{result['secrets']} secret, {result['tuples']} tuples removed; 1 shared document kept")


# ------------------------------------------------------------------ small fixes (slice S)
@check
def registry_accepts_only_spoken_providers():
    """Models: the registry refuses providers the gateway doesn't speak, instead of failing on every call later"""
    r = api("POST", "/v1/workspaces/payments/models", "alice", json={
        "name": "never-made", "provider": "vertex", "model": "gemini", "api_key": "x"})
    assert r.status_code == 422, f"vertex was accepted: {r.status_code}"


@check
def model_update_rotate_and_remove():
    """Models: admins update settings, rotate a key (older versions destroyed), and remove a model and its secret"""
    import uuid
    from . import config
    from .vault import vault
    name = f"rot-{uuid.uuid4().hex[:6]}"
    assert api("POST", "/v1/workspaces/payments/models", "alice", json={
        "name": name, "provider": "openai_compatible", "model": "x", "base_url": f"{FAKE_LLM}/v1", "api_key": "key-one"}).status_code == 201
    path = f"/v1/workspaces/payments/models/{name}"
    assert api("PATCH", path, "carol", json={"api_key": "stolen"}).status_code == 403, "a non-admin changed a model"
    r = api("PATCH", path, "alice", json={"api_key": "key-two", "allowed_classifications": ["public"]})
    assert r.status_code == 200 and r.json()["changed"] == ["allowed_classifications", "api_key"], r.text[:200]
    with psycopg.connect(OWNER_DB) as conn:
        ref = conn.execute("SELECT credential_ref FROM kl.model_endpoints WHERE name = %s", (name,)).fetchone()[0]
    assert vault.get(ref)["api_key"] == "key-two"
    secret_path = ref.split("openbao://secret/", 1)[1]
    old = httpx.get(f"{config.OPENBAO_URL}/v1/secret/data/{secret_path}", params={"version": 1},
                    headers={"X-Vault-Token": config.OPENBAO_TOKEN}, timeout=5)
    assert old.status_code == 404 or not (old.json().get("data") or {}).get("data"), "the old key is still readable"
    models = {m["name"]: m for m in api("GET", "/v1/workspaces/payments/models", "alice").json()["models"]}
    assert models[name]["allowed_classifications"] == ["public"]
    assert api("DELETE", path, "alice").status_code == 200
    assert name not in {m["name"] for m in api("GET", "/v1/workspaces/payments/models", "alice").json()["models"]}
    try:
        vault.get(ref)
        raise AssertionError("the removed model's secret survived")
    except httpx.HTTPStatusError as e:
        assert e.response.status_code == 404, e
    assert api("DELETE", path, "alice").status_code == 404


@check
def batch_checks_agree_with_single_checks():
    """Authorization: batched checks (the map's path past OpenFGA's list cap) agree with one-by-one checks"""
    from .authz import fga
    objects = [f"document:{k}" for k in ("pay-runbook", "fin-close", "pay-drift", "handbook", "legal-hold")]
    single = {o for o in objects if fga.check("user:alice", "viewer", o)}
    batched = fga.batch_check("user:alice", "viewer", objects)
    assert batched == single, f"batched {sorted(batched)} vs single {sorted(single)}"
    assert "document:pay-runbook" in batched and "document:pay-drift" not in batched, batched


# ------------------------------------------------------------------ teams and members (slice K1)
@check
def teams_and_members_without_sql():
    """Teams: an org admin creates a team, its admins manage members, and access follows membership on the next request"""
    import uuid
    from .authz import fga
    ws = f"kt-{uuid.uuid4().hex[:8]}"  # a throwaway team, removed at the end
    new = {"slug": ws, "name": "Team test", "discoverability": "private"}
    try:
        assert api("POST", "/v1/workspaces", "alice", json=new).status_code == 403, "only org admins create teams"
        r = api("POST", "/v1/workspaces", "olivia", json=new)
        assert r.status_code == 201, r.text[:200]
        assert api("POST", "/v1/workspaces", "olivia", json=new).status_code == 409
        members = lambda user: api("GET", f"/v1/workspaces/{ws}/members", user)  # noqa: E731
        assert members("alice").status_code == 404, "a private team showed itself to a non-member"
        grant = lambda user, body: api("PUT", f"/v1/workspaces/{ws}/members", user, json=body).status_code  # noqa: E731
        assert grant("olivia", {"principal": "user:alice", "role": "admin"}) == 200
        assert members("alice").status_code == 200, "a new admin can't see her team"
        assert grant("alice", {"principal": "group:finance", "role": "member"}) == 200
        scoped_search = lambda user: api("GET", "/v1/search", user, params={"q": "incident", "scope": ws}).status_code  # noqa: E731
        assert scoped_search("bob") == 200, "a member through his group can't reach the team"
        assert grant("bob", {"principal": "user:bob", "role": "admin"}) == 403, "a member made himself admin"
        assert grant("alice", {"principal": "user:alice,user:bob", "role": "member"}) == 422, "an injected principal got through"
        assert grant("alice", {"principal": "org:acme", "role": "member"}) == 422
        assert api("PATCH", f"/v1/workspaces/{ws}", "alice", json={"name": "Team test, renamed"}).status_code == 200
        assert api("DELETE", f"/v1/workspaces/{ws}/members", "alice",
                   params={"principal": "group:finance", "role": "member"}).status_code == 200
        assert scoped_search("bob") == 404, "a removed member still reached the team on his next request"
        assert api("PATCH", f"/v1/workspaces/{ws}", "alice", json={"discoverability": "org"}).status_code == 200
        assert scoped_search("bob") == 200, "an org-wide team isn't searchable by the rest of the org"
        listed = {w["slug"]: w for w in api("GET", "/v1/workspaces", "alice").json()["workspaces"]}
        assert listed[ws]["name"] == "Team test, renamed" and "admin" in listed[ws]["roles"], listed.get(ws)
        assert {g["principal"] for g in members("alice").json()["members"]} == {"user:alice"}
    finally:  # tidy up the throwaway team and its tuples
        doomed = {(t["user"], t["relation"], t["object"]) for t in fga.read({"object": f"workspace:{ws}"})}
        if doomed:
            fga.write(deletes=doomed)
        with psycopg.connect(OWNER_DB) as conn:
            conn.execute("DELETE FROM kl.workspaces WHERE slug = %s", (ws,))


# ------------------------------------------------------------------ identity links (slice K2)
@check
def identity_links_are_proven_and_revocable():
    """Identity: sign-in states are single-use and bound to their user; links are listed and revoked by their owner or an org admin"""
    import secrets as pysecrets
    from .authz import fga
    org = acme_org_id()
    alices, alices_fresh, carols_old = (pysecrets.token_urlsafe(32) for _ in range(3))
    with psycopg.connect(OWNER_DB) as conn:
        conn.execute("INSERT INTO kl.identity_link_states (state, org_id, username, provider) VALUES "
                     "(%s, %s, 'alice', 'github'), (%s, %s, 'alice', 'github')", (alices, org, alices_fresh, org))
        conn.execute("INSERT INTO kl.identity_link_states (state, org_id, username, provider, created_at) "
                     "VALUES (%s, %s, 'carol', 'github', now() - interval '1 hour')", (carols_old, org))
    complete = lambda user, state: api("POST", "/v1/identities/github/complete", user,  # noqa: E731
                                       json={"code": "not-a-real-code", "state": state})
    refused = "didn't start here"
    r = complete("bob", alices)
    assert r.status_code == 400 and refused in r.text, "someone else's sign-in state was accepted"
    assert refused in complete("alice", alices).text, "a sign-in state worked twice"
    assert refused in complete("carol", carols_old).text, "an expired sign-in state was accepted"
    r = complete("alice", alices_fresh)  # her own fresh state gets past the check, then GitHub (or setup) refuses the code
    assert r.status_code in (400, 409) and refused not in r.text, f"her own fresh state was refused: {r.text[:200]}"

    login = f"kl-test-{pysecrets.token_hex(4)}"
    source = f"ext:github.{login}"
    with psycopg.connect(OWNER_DB) as conn:
        conn.execute("INSERT INTO kl.principal_aliases (org_id, source, canonical, verified_by) "
                     "VALUES (%s, %s, 'user:carol', 'smoke')", (org, source))
    fga.write(writes={("user:carol", "owner", f"identity:github.{login}")})
    mine = lambda user: {i["source"] for i in api("GET", "/v1/identities", user).json()["identities"]}  # noqa: E731
    assert source in mine("carol") and source not in mine("bob"), "links are listed to the wrong people"
    assert api("GET", "/v1/identities", "bob", params={"all": "true"}).status_code == 403
    everyone = api("GET", "/v1/identities", "olivia", params={"all": "true"}).json()["identities"]
    assert source in {i["source"] for i in everyone}, "an org admin can't see everyone's links"
    assert api("DELETE", f"/v1/identities/{source}", "bob").status_code == 404, "someone else removed carol's link"
    assert api("DELETE", f"/v1/identities/{source}", "carol").status_code == 200
    assert source not in mine("carol"), "the link survived its removal"
    assert not fga.read({"object": f"identity:github.{login}"}), "the link's authorization survived its removal"


# ------------------------------------------------------------------ document formats (slice L)
def make_pdf(pages: list[str]) -> bytes:
    """A minimal valid PDF with one line of text per page (an empty string makes a page with no text layer)."""
    objs, kids = [], []
    font_id = 3 + 2 * len(pages)
    for i, text in enumerate(pages):
        page_id, content_id = 3 + 2 * i, 4 + 2 * i
        kids.append(f"{page_id} 0 R")
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode() if text else b""
        objs.append((page_id, f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R "
                              f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()))
        objs.append((content_id, b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"))
    objs = [(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
            (2, f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>".encode())] + objs
    objs.append((font_id, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"))
    out, offsets = bytearray(b"%PDF-1.4\n"), {}
    for oid, body in sorted(objs):
        offsets[oid] = len(out)
        out += b"%d 0 obj\n" % oid + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % offsets[i] for i in range(1, len(objs) + 1))
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


@check
def document_formats_keep_their_structure():
    """Formats: Word, PowerPoint, HTML, and PDF become sections with their headings, tables, and notes"""
    import io
    from docx import Document
    from pptx import Presentation
    from .extract import extract
    doc = Document()
    doc.add_heading("Payments runbook", 0)
    doc.add_heading("Rollback", level=1)
    doc.add_paragraph("Roll back to the previous build before debugging.")
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Window", "Tuesday 10:00"
    buf = io.BytesIO()
    doc.save(buf)
    title, sections = extract("runbook.docx", buf.getvalue(), "x")
    assert title == "Payments runbook" and sections[0][0] == "Payments runbook > Rollback", (title, sections)
    assert "Window | Tuesday 10:00" in sections[0][1], sections[0][1]

    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = "Deploy windows"
    slide.placeholders[1].text = "Tuesdays and Thursdays"
    slide.notes_slide.notes_text_frame.text = "Mention the freeze"
    buf = io.BytesIO()
    deck.save(buf)
    _, sections = extract("deploys.pptx", buf.getvalue(), "Deploys")
    assert sections[0][0].endswith("Deploy windows") and "Speaker notes: Mention the freeze" in sections[0][1], sections

    page = ("<title>Onboarding</title><nav>Home</nav><h2>First week</h2><p>Laptop &amp; badge.</p>"
            "<script>steal()</script><table><tr><td>Mon</td><td>Accounts</td></tr></table>")
    title, sections = extract("onboarding.html", page.encode(), "x")
    body = sections[0][1]
    assert title == "Onboarding" and sections[0][0].endswith("First week") and "Mon | Accounts" in body, sections
    assert "steal" not in body and "Home" not in body and "Laptop & badge." in body, body

    _, sections = extract("settlement.pdf", make_pdf(["Settlement runs nightly", "Keys rotate quarterly"]), "x")
    assert [h.split(" > ")[-1] for h, _ in sections] == ["Page 1", "Page 2"] and "Keys rotate" in sections[1][1], sections


@check
def unreadable_documents_fail_loudly():
    """Formats: scanned PDFs, damaged files, zip bombs, and empty pages are refused with a reason, never indexed empty"""
    import io
    import zipfile
    from .extract import ExtractionError, extract
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"\0" * (101 * 1024 * 1024))  # ~100 KB on disk, 101 MB inflated
    cases = {"scan.pdf": (make_pdf([""]), "OCR"), "broken.pdf": (b"%PDF-1.4 junk", "readable PDF"),
             "fake.docx": (b"not a zip", "Office file"), "bomb.docx": (bomb.getvalue(), "inflate"),
             "empty.html": (b"<script>x()</script>", "no text"), "clip.mp4": (b"hi", "speech model"), "tool.exe": (b"MZ", "unsupported"),
             "fake.png": (b"not an image", "readable image"),
             "bomb.xlsx": (bomb.getvalue(), "inflate"), "broken.odt": (b"not a zip", "Office file"),
             "empty.csv": (b"\n\n", "no text")}
    for name, (data, reason) in cases.items():
        try:
            extract(name, data, "x")
        except ExtractionError as e:
            assert reason in str(e), f"{name}: {e}"
        else:
            raise AssertionError(f"{name} was accepted")


@check
def more_formats_keep_their_structure():
    """Formats: Excel, CSV, OpenDocument, email, and plain text become sections; each section of a sheet repeats its header row"""
    import io
    import zipfile
    from email.message import EmailMessage
    from .extract import extract
    s = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    book = io.BytesIO()
    with zipfile.ZipFile(book, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{s}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                   '<sheets><sheet name="Owners" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{s}"><si><t>System</t></si><si><t>Owner</t></si>'
                   '<si><t>ledger-db</t></si><si><t>payments</t></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{s}"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c>'
                   '<c r="B1" t="s"><v>1</v></c></row><row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2" t="s"><v>3</v></c>'
                   '<c r="C2"><v>3</v></c></row></sheetData></worksheet>')
    _, sections = extract("owners.xlsx", book.getvalue(), "Owners")
    assert sections[-1][0].endswith("Owners, rows 1\u20131") and "| ledger-db | payments | 3 |" in sections[-1][1], sections
    rows = "system,owner\n" + "".join(f"svc-{i},team-{i % 3}\n" for i in range(120))
    _, sections = extract("services.csv", rows.encode(), "Services")
    assert len(sections) == 3 and all(b.startswith("| system | owner |") for _, b in sections), [h for h, _ in sections]
    ns = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
          'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"')
    cells = lambda *values: "".join(f"<table:table-cell><text:p>{v}</text:p></table:table-cell>" for v in values)  # noqa: E731
    odt = io.BytesIO()
    with zipfile.ZipFile(odt, "w") as z:
        z.writestr("content.xml", f'<office:document-content {ns}><office:body><office:text>'
                   '<text:h text:outline-level="1">Failover</text:h><text:p>Check replication lag first.</text:p>'
                   f'<text:h text:outline-level="2">Contacts</text:h><table:table><table:table-row>{cells("Team", "Channel")}'
                   f'</table:table-row><table:table-row>{cells("payments", "#payments-oncall")}</table:table-row></table:table>'
                   '</office:text></office:body></office:document-content>')
    _, sections = extract("failover.odt", odt.getvalue(), "Failover")
    assert [h.split(" > ")[-1] for h, _ in sections if h] == ["Failover", "Contacts"], sections
    assert "| payments | #payments-oncall |" in sections[-1][1], "a table in a document belongs under its heading"
    mail = EmailMessage()
    mail["Subject"], mail["From"] = "Ledger failover tonight", "priya@acme.test"
    mail.set_content("We fail over at 22:00 UTC.")
    mail.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf", filename="plan.pdf")
    title, sections = extract("failover.eml", mail.as_bytes(), "Email")
    text = "\n".join(b for _, b in sections)
    assert title == "Ledger failover tonight" and "22:00 UTC" in text and "plan.pdf" in text, (title, text)
    assert "rotate keys" in extract("notes.txt", b"Remember to rotate keys quarterly.", "Notes")[1][-1][1]


@check
def scans_and_images_are_read_with_ocr():
    """Formats: a scanned PDF and a picture of text are read with OCR, a logo isn't indexed, and RTF becomes plain text"""
    import io
    from PIL import Image, ImageDraw, ImageFont
    from .extract import ExtractionError, extract

    def picture(lines, size=(1700, 500)):
        img = Image.new("L", size, 255)
        draw, font = ImageDraw.Draw(img), ImageFont.load_default(size=64)
        for i, line in enumerate(lines):
            draw.text((60, 80 + 140 * i), line, fill=0, font=font)
        return img

    scan, photo, logo = io.BytesIO(), io.BytesIO(), io.BytesIO()
    picture(["Settlement runs nightly", "Rotate the ledger keys quarterly"]).save(scan, "PDF", resolution=200)
    picture(["Whiteboard: failover plan", "Drain traffic before the switch"]).save(photo, "PNG")
    picture(["ACME"], size=(600, 250)).save(logo, "PNG")
    _, sections = extract("scan.pdf", scan.getvalue(), "Scan")
    text = " ".join(b for _, b in sections).lower()
    assert "settlement runs nightly" in text and "quarterly" in text and "read by OCR" in sections[0][0], sections
    _, sections = extract("whiteboard.png", photo.getvalue(), "Whiteboard")
    assert "drain traffic" in sections[0][1].lower(), sections
    try:
        extract("logo.png", logo.getvalue(), "Logo")
    except ExtractionError as e:
        assert "no text" in str(e), str(e)
    else:
        raise AssertionError("a logo was indexed as text")
    rtf = rb"{\rtf1\ansi{\fonttbl{\f0 Times;}}\pard Ledger failover\par Check replication lag first, caf\'e9.\par}"
    text = "\n".join(b for _, b in extract("failover.rtf", rtf, "Failover")[1])
    assert "Check replication lag first, café." in text and "Times" not in text, text


# ------------------------------------------------------------------ answer quality (slice M1)
@check
def retrieval_meets_the_golden_set():
    """Quality: the golden set's questions find their answers (hit@1, MRR, found@5 at or above threshold), with no leaks"""
    from .eval import run, search_keys, store
    report = run(search_keys)
    store(report)
    assert not report["leaks"], f"documents shown to people who mustn't see them: {report['leaks']}"
    misses = [(c["user"], c["query"], c["top"]) for c in report["cases"] if c["rank"] != 1]
    assert report["passed"], f"below threshold: {report['failed']}; not first: {misses}"
    m = report["metrics"]
    print(f"      {len(report['cases'])} questions: hit@1 {m['hit_at_1']}, MRR {m['mrr']}, found@5 {m['found_at_5']}"
          + (f"; not first: {', '.join(q for _, q, _ in misses)}" if misses else ""))


@check
def a_query_naming_an_alias_finds_the_entitys_other_names():
    """Q2, entity resolution in search: a query naming one of an entity's aliases also finds a document that names the entity another way (another alias, or the page's title), for someone who may see the curated page declaring those aliases; someone who can't see that page gets no such expansion, even for a document they may read"""
    from .authz import fga
    curated_ready()
    tag = uuid.uuid4().hex[:6]
    key = f"q2-{tag}-notes"
    tuples = {(f"user:{u}", "viewer", f"document:{key}") for u in ("alice", "bob")}
    try:
        with psycopg.connect(OWNER_DB) as conn:
            org = acme_org_id()
            commons = conn.execute("SELECT id FROM kl.workspaces WHERE slug = 'commons'").fetchone()[0]
            doc = conn.execute(
                "INSERT INTO kl.documents (org_id, source_kind, source_uri, source_version, external_key, title, "
                "content_hash, classification, acl_principals) VALUES (%s, 'seed', %s, 'v1', %s, %s, %s, 'internal', %s) "
                "RETURNING id", (org, f"https://kb.acme.test/{key}", key, f"Incident notes {tag}", key, ["user:alice", "user:bob"])).fetchone()[0]
            conn.execute("INSERT INTO kl.chunks (org_id, document_id, ordinal, heading_path, body, acl_principals) VALUES (%s, %s, 0, NULL, %s, %s)",
                         (org, doc, f"We opened the Sev1 Playbook and paged the on-call, as noted {tag}.", ["user:alice", "user:bob"]))
            conn.execute("INSERT INTO kl.workspace_documents (workspace_id, document_id, org_id) VALUES (%s, %s, %s)", (commons, doc, org))
        fga.write(writes=tuples)

        def hit(user):
            results = api("GET", "/v1/search", user, params={"q": "payments sev1", "scope": "org", "limit": 50}).json()["results"]
            return next((h for h in results if h["id"] == key), None)
        found = hit("alice")  # "payments sev1" is an alias of the Sev1 playbook; the notes say "Sev1 Playbook"
        assert found and "alias" in found["matched"], f"a query naming an alias didn't find the entity's other names: {found}"
        theirs = hit("bob")  # bob may read the notes, but not the payments page that declares the aliases
        assert not theirs or "alias" not in theirs["matched"], "an alias from a page someone can't see expanded their search"
    finally:
        doomed = {t for t in tuples if fga.read({"user": t[0], "relation": t[1], "object": t[2]})}
        if doomed:
            fga.write(deletes=doomed)
        with psycopg.connect(OWNER_DB) as conn:
            conn.execute("DELETE FROM kl.documents WHERE external_key = %s", (key,))


@check
def who_to_ask_never_suggests_someone_who_has_left():
    """Q6, who to ask knows who has left: someone a directory says has left isn't suggested about a page, even as a member of its owning team; they're listed as gone, and their team is suggested instead; once the directory lists them active again, or isn't there, they're suggested as before"""
    curated_ready()
    name = f"q6-dir-{secrets.token_hex(3)}"

    def people(user):
        r = api("GET", "/v1/concepts/kb-sev1-playbook/people", user)
        assert r.status_code == 200, r.text[:200]
        return r.json()

    before = people("alice")
    assert "carol" in {p["user"] for p in before["people"]}, f"carol isn't among who to ask about the Sev1 playbook to begin with: {before}"
    with psycopg.connect(OWNER_DB, autocommit=True) as conn:
        org = acme_org_id()
        payments = conn.execute("SELECT id FROM kl.workspaces WHERE slug = 'payments'").fetchone()[0]
        cid = conn.execute(  # a directory as its sync leaves it, without a sync: one that never runs during the check
            "INSERT INTO kl.connections (org_id, workspace_id, kind, name, config, credential_ref, created_by, status, last_synced_at) "
            "VALUES (%s, %s, 'directory', %s, %s::jsonb, 'openbao://secret/kl/connections/q6-none', 'smoke', 'ready', now()) RETURNING id",
            (org, payments, name, json.dumps({"provider": "okta", "every_minutes": 10080}))).fetchone()[0]
        conn.execute("INSERT INTO kl.directory_people (org_id, connection_id, email, display_name, active) VALUES (%s, %s, 'carol@acme.test', 'Carol', false)",
                     (org, cid))
    try:
        after = people("alice")
        assert "carol" not in {p["user"] for p in after["people"]}, f"someone who has left was suggested: {after['people']}"
        gone = {g["user"]: g for g in after["left"]}
        assert "carol" in gone and "payments" in gone["carol"]["instead"], f"who left, and their team instead, weren't said: {after['left']}"
        assert "payments" in {t["team"] for t in after["teams"]}, f"their team wasn't suggested: {after['teams']}"
        with psycopg.connect(OWNER_DB, autocommit=True) as conn:
            conn.execute("UPDATE kl.directory_people SET active = true WHERE connection_id = %s", (cid,))
        assert "carol" in {p["user"] for p in people("alice")["people"]}, "someone back in the directory wasn't suggested again"
    finally:
        with psycopg.connect(OWNER_DB, autocommit=True) as conn:
            conn.execute("DELETE FROM kl.connections WHERE id = %s", (cid,))


@check
def who_wrote_a_page_in_git_is_who_to_ask():
    """Q7, Git authors: the people who wrote a page in Git (its commits' authors) are among who to ask about it, with how much they wrote, as far as they've linked their GitHub accounts; an author nobody linked is named as no one; a bot's commits count for nobody"""
    from .authz import fga
    from .sync_github import authors_of
    commits = [{"author": {"login": "dave-gh"}, "commit": {"author": {"date": "2026-09-01T10:00:00Z"}}},
               {"author": {"login": "dave-gh"}, "commit": {"author": {"date": "2026-09-03T10:00:00Z"}}},
               {"author": {"login": "renovate[bot]"}}, {"author": None}, {"author": {"login": "stranger-gh"}}]
    assert authors_of(commits) == [("dave-gh", 2, "2026-09-03T10:00:00Z"), ("stranger-gh", 1, None)], authors_of(commits)
    curated_ready()
    tag = secrets.token_hex(3)
    login = f"dave-gh{tag}"
    with psycopg.connect(OWNER_DB, autocommit=True) as conn:
        org = acme_org_id()
        doc = conn.execute("SELECT document_id FROM kl.concepts WHERE concept_key = 'kb-sev1-playbook'").fetchone()[0]
        conn.execute("INSERT INTO kl.principal_aliases (org_id, source, canonical, verified_by) VALUES (%s, %s, 'user:dave', 'smoke')",
                     (org, f"ext:github.{login}"))
        conn.cursor().executemany("INSERT INTO kl.document_authors (org_id, document_id, login, commits, at_commit) VALUES (%s, %s, %s, %s, 'smoke')",
                                  [(org, doc, login, 3), (org, doc, f"nobody-gh{tag}", 5)])
    try:
        people = {p["user"]: p["reasons"] for p in api("GET", "/v1/concepts/kb-sev1-playbook/people", "alice").json()["people"]}
        assert "wrote it (3 commits)" in people.get("dave", []), f"who wrote the page isn't among who to ask: {people}"
        assert not any(u.startswith("nobody-gh") for u in people), f"an author nobody linked was named: {people}"
    finally:
        with psycopg.connect(OWNER_DB, autocommit=True) as conn:
            conn.execute("DELETE FROM kl.document_authors WHERE login = ANY(%s)", ([login, f"nobody-gh{tag}"],))
            conn.execute("DELETE FROM kl.principal_aliases WHERE source = %s", (f"ext:github.{login}",))


@check
def attachments_are_their_own_documents_with_their_pages_readers():
    """Q7, attachments: a Jira issue's attached file becomes its own document, read through the extractors, with exactly the issue's readers, and linked to its issue by address; a format nothing reads isn't one"""
    from .authz import fga
    tag = secrets.token_hex(3).upper()
    key = f"ATT{tag}"
    alice = {"accountId": f"557058:alice{tag.lower()}", "emailAddress": "alice@acme.test", "displayName": "Alice"}
    principal = f"ext:atlassian.{alice['accountId'].replace(':', '.')}"
    link = {("user:alice", "owner", f"identity:{principal[4:]}")}
    att = f"{int(tag, 16) % 10**9}"
    setup = {"projects": {key: {"name": "Attachments", "company_wide": False, "browsers": [alice], "issues": [
        {"key": f"{key}-1", "summary": f"Cutover runbook {tag}", "description": "See the attached notes.",
         "attachments": [{"id": att, "filename": "notes.txt", "text": f"Rehearse the cutover twice {tag}."},
                         {"id": f"{att}9", "filename": "diagram.sketch", "text": "binary"}]}]}}}
    name = f"att-{tag.lower()}"
    with psycopg.connect(OWNER_DB, autocommit=True) as conn:
        conn.execute("INSERT INTO kl.principal_aliases (org_id, source, canonical, verified_by) VALUES (%s, %s, 'user:alice', 'smoke') "
                     "ON CONFLICT DO NOTHING", (acme_org_id(), principal))
    fga.write(writes=link)

    def hits(user):
        return {h["id"] for h in api("GET", "/v1/search", user, params={"q": tag, "scope": "org", "limit": 50}).json()["results"]}
    try:
        assert httpx.post(f"{FAKE_JIRA}/setup", json=setup, timeout=10).status_code == 200
        r = api("POST", "/v1/workspaces/payments/connections/jira", "alice", json={
            "name": name, "api_base": FAKE_JIRA, "email": "jira-bot@acme.test", "api_token": "smoke-jira-token", "projects": [key], "every_minutes": 10080})
        assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and f"jira-att-{att}" not in hits("alice"):
            time.sleep(2)
        assert f"jira-att-{att}" in hits("alice"), "the attached file never became its own document"
        assert f"jira-att-{att}" not in hits("bob"), "an attachment reached someone its issue doesn't"
        page = api("GET", f"/v1/documents/jira-att-{att}/content", "alice").text
        assert "Rehearse the cutover twice" in page and f"/browse/{key}-1" in page, f"the attachment's text, or its issue, is missing: {page[:300]}"
        assert f"jira-att-{att}9" not in hits("alice"), "a format nothing reads became a document"
    finally:
        api("DELETE", f"/v1/workspaces/payments/connections/{name}", "alice")
        fga.write(deletes=link)
        with psycopg.connect(OWNER_DB, autocommit=True) as conn:
            conn.execute("DELETE FROM kl.principal_aliases WHERE source = %s", (principal,))
            conn.execute("DELETE FROM kl.source_identities WHERE source = 'atlassian' AND external_id LIKE %s", (f"%{tag.lower()}",))


@check
def diagrams_audio_and_video_are_read_by_the_models_a_team_chose():
    """Q8, diagrams, audio, and video: a source's own transcript becomes sections by when each minute starts; with no speech model chosen, audio isn't read; a team admin opts in with models from the team's own registry (another team's, or a non-admin, refused); then audio is transcribed into timestamped sections and a diagram described beside its OCR, each only by a model whose allowed classifications include the content's"""
    from .extract import ExtractionError, extract
    from .media import media_for
    ensure_fake_models()
    vtt = b"WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nWelcome to the rehearsal.\n\n00:01:30.000 --> 00:01:33.000\nWe fail over at noon.\n"
    _, sections = extract("talk.vtt", vtt, "Rehearsal")
    assert [h for h, _ in sections] == ["At 00:00:01", "At 00:01:30"], f"a transcript's sections aren't by time: {sections}"
    try:
        extract("standup.mp3", b"ID3fake", "Standup")
        raise AssertionError("audio was read without a speech model")
    except ExtractionError:
        pass
    assert api("PUT", "/v1/workspaces/payments/media", "bob", json={"vision_model": "smoke-fake"}).status_code in (403, 404), "a non-admin opted a team in"
    assert api("PUT", "/v1/workspaces/payments/media", "alice", json={"vision_model": "no-such-model"}).status_code == 422, "a model outside the registry was taken"
    r = api("PUT", "/v1/workspaces/payments/media", "alice", json={"vision_model": "smoke-fake", "speech_model": "smoke-fake"})
    assert r.status_code == 200, r.text[:200]
    try:
        assert api("GET", "/v1/workspaces/payments/media", "alice").json()["speech_model"] == "smoke-fake"
        with psycopg.connect(OWNER_DB, row_factory=dict_row) as conn:
            conn.execute("SET search_path = kl, public")
            team = conn.execute("SELECT id FROM workspaces WHERE slug = 'payments'").fetchone()["id"]
            media = media_for(conn, team, "internal")
            refused = media_for(conn, team, "restricted")  # the fake model's allowed classifications stop short of this
        assert media and media.speech and media.vision, "the team's chosen models weren't handed to the extractors"
        assert refused is None, "a model was handed content its allowed classifications don't include"
        _, heard = extract("standup.mp3", b"ID3fake", "Standup", media)
        assert [h for h, _ in heard] == ["At 00:00:00", "At 00:01:15"] and "fail over at noon" in heard[1][1], f"the transcript wasn't timestamped: {heard}"
        png = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000" "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")
        _, seen = extract("flow.png", png, "Flow", media)
        assert seen[0][0] == "Diagram" and "Ledger" in seen[0][1], f"the diagram wasn't described: {seen}"
    finally:
        api("PUT", "/v1/workspaces/payments/media", "alice", json={})


@check
def knowledge_health_says_what_needs_someone_and_the_queue_acts_on_it():
    """E2, knowledge health: a team's admins see each curated page that needs someone, said plainly (never verified, sharing a name with another page, linked from nothing, with no owner); confirming it counts as a review now; retiring it leaves it out of search until it's confirmed again; asking for an update is shown until its content changes; every action is recorded; nobody but the team's admins sees the page or acts"""
    from .authz import fga
    curated_ready()
    tag = uuid.uuid4().hex[:6]
    key = f"kb-health-{tag}"
    okf_text = (f"---\ntitle: Settlement window {tag}\nx-kl:\n  id: {key}\n  aliases: [sev1 playbook]\n---\n\n"
                f"# Settlement window {tag}\n\nSettlement closes at five.\n")
    tuples = {("group:payments#member", "viewer", f"document:{key}")}
    try:
        with psycopg.connect(OWNER_DB) as conn:
            org = acme_org_id()
            payments = conn.execute("SELECT id FROM kl.workspaces WHERE slug = 'payments'").fetchone()[0]
            doc = conn.execute("INSERT INTO kl.documents (org_id, source_kind, source_uri, source_version, external_key, title, content_hash, "
                               "classification, acl_principals, tier) VALUES (%s, 'okf', %s, 'v1', %s, %s, 'h1', 'internal', %s, 'silver') RETURNING id",
                               (org, f"https://kb.acme.test/{key}", key, f"Settlement window {tag}", ["group:payments"])).fetchone()[0]
            conn.execute("INSERT INTO kl.chunks (org_id, document_id, ordinal, heading_path, body, acl_principals) VALUES (%s, %s, 0, NULL, %s, %s)",
                         (org, doc, f"Settlement closes at five {tag}.", ["group:payments"]))
            conn.execute("INSERT INTO kl.workspace_documents (workspace_id, document_id, org_id) VALUES (%s, %s, %s)", (payments, doc, org))
            cid = conn.execute("INSERT INTO kl.concepts (org_id, concept_key, tier, document_id, title, acl_principals, aliases, latest_hash) "
                               "VALUES (%s, %s, 'silver', %s, %s, %s, %s, 'h1') RETURNING id",
                               (org, key, doc, f"Settlement window {tag}", ["group:payments"], ["sev1 playbook"])).fetchone()[0]
            conn.execute("INSERT INTO kl.concept_versions (concept_id, org_id, hash, content) VALUES (%s, %s, 'h1', %s)", (cid, org, okf_text))
        fga.write(writes=tuples)

        def health(user="alice"):
            r = api("GET", "/v1/workspaces/payments/health", user)
            return r if user != "alice" else r.json()

        def mine():
            return next((p for p in health()["needing"] if p["id"] == key), None)
        assert health("bob").status_code in (403, 404), "someone outside the team's admins saw its health"
        page = mine()
        kinds = sorted(i["kind"] for i in page["issues"]) if page else []
        assert kinds == ["conflicting", "orphaned", "ownerless", "stale"], f"the page's problems weren't all said: {page}"
        assert any("Sev1" in i["text"] or "sev1" in i["text"] for i in page["issues"] if i["kind"] == "conflicting"), page["issues"]
        assert api("POST", f"/v1/workspaces/payments/health/{key}", "bob", json={"action": "confirm"}).status_code in (403, 404), "a non-admin worked the queue"
        assert api("POST", f"/v1/workspaces/payments/health/{key}", "alice", json={"action": "confirm"}).status_code == 201
        assert "stale" not in [i["kind"] for i in mine()["issues"]], "a confirmed page still reads as stale"

        def found():
            return key in {h["id"] for h in api("GET", "/v1/search", "alice", params={"q": f"settlement {tag}", "scope": "org"}).json()["results"]}
        assert found(), "the page isn't searchable to begin with"
        assert api("POST", f"/v1/workspaces/payments/health/{key}", "alice", json={"action": "retire", "note": "Merged into the runbook"}).status_code == 201
        assert not found() and mine()["retired"], "a retired page was still found"
        assert api("POST", f"/v1/workspaces/payments/health/{key}", "alice", json={"action": "confirm"}).status_code == 201
        assert found(), "a page confirmed after retiring didn't come back"
        assert api("POST", f"/v1/workspaces/payments/health/{key}", "alice", json={"action": "update", "note": "The window moved"}).status_code == 201
        assert mine()["update_requested"]["note"] == "The window moved", "an update request wasn't shown"
        reviews = [(r["id"], r["action"]) for r in health()["reviews"] if r["id"] == key]
        assert reviews[:4] == [(key, "update"), (key, "confirm"), (key, "retire"), (key, "confirm")], f"the actions weren't recorded: {reviews}"
    finally:
        doomed = {t for t in tuples if fga.read({"user": t[0], "relation": t[1], "object": t[2]})}
        if doomed:
            fga.write(deletes=doomed)
        with psycopg.connect(OWNER_DB) as conn:
            conn.execute("DELETE FROM kl.documents WHERE external_key = %s", (key,))


@check
def search_narrows_by_source_and_container_and_says_where_each_result_sits():
    """E1, search by source: results say their container by name; the facets count only what the caller may see; a search narrowed to a source or a container returns only those, and so does Ask; a malformed filter is refused"""
    from .authz import fga
    tag = uuid.uuid4().hex[:6]
    docs = {f"e1-{tag}-a": ("jira", f"E1PROJ{tag}", f"Cutover issue {tag}"), f"e1-{tag}-b": ("confluence", f"E1SPACE{tag}", f"Cutover page {tag}")}
    tuples = {("user:alice", "viewer", f"document:{key}") for key in docs}
    try:
        with psycopg.connect(OWNER_DB) as conn:
            org = acme_org_id()
            commons = conn.execute("SELECT id FROM kl.workspaces WHERE slug = 'commons'").fetchone()[0]
            for key, (kind, container, title) in docs.items():
                doc = conn.execute("INSERT INTO kl.documents (org_id, source_kind, source_uri, source_version, external_key, title, content_hash, "
                                   "classification, acl_principals, container_ref) VALUES (%s, %s, %s, 'v1', %s, %s, %s, 'internal', %s, %s) RETURNING id",
                                   (org, kind, f"https://kb.acme.test/{key}", key, title, key, ["user:alice"], f"seed/{container}")).fetchone()[0]
                conn.execute("INSERT INTO kl.chunks (org_id, document_id, ordinal, heading_path, body, acl_principals) VALUES (%s, %s, 0, NULL, %s, %s)",
                             (org, doc, f"The cutover plan, as of today {tag}.", ["user:alice"]))
                conn.execute("INSERT INTO kl.workspace_documents (workspace_id, document_id, org_id) VALUES (%s, %s, %s)", (commons, doc, org))
        fga.write(writes=tuples)

        def search(user, **narrow):
            r = api("GET", "/v1/search", user, params={"q": tag, "scope": "org", "limit": 20, **narrow})
            assert r.status_code == 200, r.text[:200]
            return r.json()
        everything = search("alice")
        mine = {h["id"]: h for h in everything["results"] if h["id"] in docs}
        assert set(mine) == set(docs), f"the documents weren't found: {sorted(mine)}"
        assert mine[f"e1-{tag}-a"]["container"] == {"ref": f"seed/E1PROJ{tag}", "name": f"E1PROJ{tag}"}, mine[f"e1-{tag}-a"]
        kinds = {f["kind"] for f in everything["facets"]["sources"]}
        assert {"jira", "confluence"} <= kinds, f"the facets didn't count the sources: {everything['facets']}"
        only = search("alice", source="jira")
        assert [h["id"] for h in only["results"] if h["id"] in docs] == [f"e1-{tag}-a"] and all(h["source_kind"] == "jira" for h in only["results"]), only["results"]
        placed = search("alice", container=f"seed/E1SPACE{tag}")
        assert [h["id"] for h in placed["results"]] == [f"e1-{tag}-b"], placed["results"]
        theirs = search("bob")  # bob can read neither: the facets don't count them
        assert not any(c["ref"].endswith(tag) for c in theirs["facets"]["containers"]), "the facets counted documents the caller can't see"
        assert api("GET", "/v1/search", "alice", params={"q": tag, "source": "Jira; DROP"}).status_code == 422, "a malformed filter was taken"
    finally:
        doomed = {t for t in tuples if fga.read({"user": t[0], "relation": t[1], "object": t[2]})}
        if doomed:
            fga.write(deletes=doomed)
        with psycopg.connect(OWNER_DB) as conn:
            conn.execute("DELETE FROM kl.documents WHERE external_key = ANY(%s)", (list(docs),))


@check
def titles_outrank_passing_mentions():
    """Quality: a document whose title matches outranks one that only mentions the word (the resume case)"""
    import uuid
    from .authz import fga
    tag = uuid.uuid4().hex[:6]
    titled, mention = f"qt-{tag}-guide", f"qt-{tag}-notes"
    docs = {titled: ("Quokka onboarding guide", "Steps for new starters: laptop, badge, and accounts."),
            mention: ("Weekly notes", "We talked about the quokka exhibit, then resumed the deploy.")}
    tuples = {("user:alice", "viewer", f"document:{key}") for key in docs}
    try:
        with psycopg.connect(OWNER_DB) as conn:
            org = acme_org_id()
            commons = conn.execute("SELECT id FROM kl.workspaces WHERE slug = 'commons'").fetchone()[0]
            for key, (title, body) in docs.items():
                doc = conn.execute(
                    "INSERT INTO kl.documents (org_id, source_kind, source_uri, source_version, external_key, title, "
                    "content_hash, classification, acl_principals) VALUES (%s, 'seed', %s, 'v1', %s, %s, %s, 'internal', %s) "
                    "RETURNING id", (org, f"https://kb.acme.test/{key}", key, title, key, ["user:alice"])).fetchone()[0]
                conn.execute("INSERT INTO kl.chunks (org_id, document_id, ordinal, heading_path, body, acl_principals) "
                             "VALUES (%s, %s, 0, NULL, %s, %s)", (org, doc, body, ["user:alice"]))
                conn.execute("INSERT INTO kl.workspace_documents (workspace_id, document_id, org_id) VALUES (%s, %s, %s)",
                             (commons, doc, org))
        fga.write(writes=tuples)
        r = api("GET", "/v1/search", "alice", params={"q": "quokka", "scope": "org", "limit": 10})
        assert r.status_code == 200, r.text[:200]
        ranked = [hit["id"] for hit in r.json()["results"] if hit["id"] in docs]
        assert ranked and ranked[0] == titled, f"the passing mention outranked the titled document: {ranked}"
    finally:
        doomed = {t for t in tuples if fga.read({"user": t[0], "relation": t[1], "object": t[2]})}
        if doomed:
            fga.write(deletes=doomed)
        with psycopg.connect(OWNER_DB) as conn:
            conn.execute("DELETE FROM kl.documents WHERE external_key = ANY(%s)", (list(docs),))


# ------------------------------------------------------------------ reranker and answer grading (slice M2)
def fake_reranker(**changes) -> None:
    """The fake model's /rerank as the org's reranker, set by olivia (the org admin); public + internal only."""
    body = {"model": "fake-rerank", "base_url": "http://fake-llm:8080/v1", "allowed_classifications": ["public", "internal"],
            **changes}
    r = api("PUT", "/v1/reranker", "olivia", json=body)
    assert r.status_code == 200, f"set the reranker: {r.status_code} {r.text[:200]}"


def no_reranker() -> None:
    r = api("DELETE", "/v1/reranker", "olivia")
    assert r.status_code in (200, 404), f"remove the reranker: {r.status_code} {r.text[:200]}"


def fake_reranks() -> list[dict]:
    return httpx.get(f"{FAKE_LLM}/reranks", timeout=10).json()


@check
def reranker_is_for_org_admins():
    """Reranker: only an org admin sets, reads, or removes it, and its key stays in the secrets backend"""
    try:
        r = api("PUT", "/v1/reranker", "alice", json={"model": "x", "base_url": "http://fake-llm:8080/v1"})
        assert r.status_code == 403, f"a team admin set the org's reranker: {r.status_code}"
        assert api("GET", "/v1/reranker", "bob").status_code == 403, "a member read the reranker's settings"
        key = "rk-" + secrets.token_hex(12)
        fake_reranker(api_key=key)
        got = api("GET", "/v1/reranker", "olivia")
        assert got.status_code == 200 and got.json()["has_key"] and key not in got.text, f"reranker settings: {got.text[:200]}"
        with psycopg.connect(OWNER_DB) as conn:
            ref, row = conn.execute("SELECT credential_ref, to_jsonb(r)::text FROM kl.rerank_models r").fetchone()
        assert ref.startswith("openbao://") and key not in row, "the reranker's key reached Postgres"
    finally:
        no_reranker()
    assert api("GET", "/v1/reranker", "olivia").status_code == 404, "the reranker outlived its removal"


@check
def reranker_reads_only_what_it_may():
    """Reranker: reads only passages the caller may see and its egress list allows; a withheld one keeps its place"""
    try:
        fake_reranker()
        reset_fake()
        on = api("GET", "/v1/search", "bob", params={"q": "incident", "scope": "org", "limit": 10})
        off = api("GET", "/v1/search", "bob", params={"q": "incident", "scope": "org", "limit": 10, "rerank": "false"})
        everything = api("GET", "/v1/search", "bob", params={"q": "incident", "scope": "org", "limit": 50, "rerank": "false"})
        assert on.status_code == off.status_code == everything.status_code == 200, on.text[:200]
        assert on.json()["reranked"] and not off.json()["reranked"], "the reranker didn't run, or ran when switched off"
        passages = [d for req in fake_reranks() for d in req.get("documents", [])]
        assert passages, "the reranker was never called"
        visible = {hit["title"] for hit in everything.json()["results"]}
        strangers = [p.splitlines()[0][:60] for p in passages if p.split("\n", 1)[0].split(" > ")[0] not in visible]
        assert not strangers, f"the reranker read documents bob can't see: {strangers}"
        assert not any(CLOSE_TEXT in p for p in passages), "a confidential passage reached a reranker that may not read it"
        ids_on, ids_off = [h["id"] for h in on.json()["results"]], [h["id"] for h in off.json()["results"]]
        if "fin-close" in ids_off and "fin-close" in ids_on:
            assert ids_on.index("fin-close") == ids_off.index("fin-close"), \
                f"withheld fin-close moved: {ids_off} -> {ids_on}"
        assert not {"pay-runbook", "pay-drift"} & set(ids_on), "bob saw payments documents"
        print(f"      {len(passages)} passages read; withheld fin-close stayed at "
              f"#{ids_on.index('fin-close') + 1 if 'fin-close' in ids_on else '-'}")
    finally:
        no_reranker()


@check
def search_survives_a_broken_reranker():
    """Reranker: when it fails, search still answers, in fusion's order"""
    try:
        fake_reranker(base_url="http://fake-llm:8080/broken")  # answers 404
        on = api("GET", "/v1/search", "alice", params={"q": "incident", "scope": "org", "limit": 10})
        off = api("GET", "/v1/search", "alice", params={"q": "incident", "scope": "org", "limit": 10, "rerank": "false"})
        assert on.status_code == 200, f"search failed with the reranker down: {on.status_code} {on.text[:200]}"
        assert not on.json()["reranked"], "results claim a rerank that failed"
        assert [h["id"] for h in on.json()["results"]] == [h["id"] for h in off.json()["results"]], "fusion's order wasn't kept"
    finally:
        no_reranker()


@check
def eval_gate_fails_broken_ranking():
    """Quality: the eval gate fails a deliberately broken ranking (every result list reversed)"""
    from .eval import run, search_keys
    broken = run(lambda user, query: list(reversed(search_keys(user, query))))
    assert not broken["passed"] and broken["failed"], f"a reversed ranking passed the gate: {broken['metrics']}"
    print(f"      reversed: hit@1 {broken['metrics']['hit_at_1']}, MRR {broken['metrics']['mrr']}; failed on {', '.join(broken['failed'])}")


@check
def answers_are_graded_against_their_sources():
    """Quality: answers are graded on what they cite: a right source, nothing invented, claims their sources support"""
    from .eval import ask_with, grade, run_answers
    ensure_fake_models()
    judge = {"provider": "openai_compatible", "model": "fake", "base_url": f"{FAKE_LLM}/v1", "api_key": None,
             "allowed": ["public", "internal"]}
    ask = ask_with("payments/smoke-fake")
    reset_fake()
    reply = ask("alice", PAGE_QUESTION)
    assert reply and reply["citations"] and all("passage" in c and "classification" in c for c in reply["citations"]), \
        f"ask didn't return passages when asked: {str(reply)[:200]}"
    first = reply["citations"][0]
    case = {"user": "alice", "query": PAGE_QUESTION, "relevant": [first["id"]], "facts": ["page the on-call"]}
    golden = {"cases": [case], "answer_thresholds": {"faithfulness": 0.9}}
    honest = run_answers(ask, judge, golden)
    graded = honest["answers"][0]
    assert graded["cited_right"] == 1.0 and graded["faithfulness"] == 1.0 and honest["passed"], \
        f"an honest answer was graded unfaithful: {graded}; its source began {first.get('passage', '')[:120]!r}"
    httpx.post(f"{FAKE_LLM}/mode", json={"answer": "unfaithful"}, timeout=10)
    try:
        lying = run_answers(ask, judge, golden)
    finally:
        reset_fake()
    assert not lying["passed"] and lying["answers"][0]["faithfulness"] == 0.0, f"an invented claim passed: {lying['answers']}"
    phantom = run_answers(lambda u, q: {"answer": "Page the on-call [1] [7].", "citations": reply["citations"][:1]},
                          judge, golden)
    assert not phantom["passed"] and phantom["answers"][0]["phantom"] == [7], "a citation to a source never given passed"
    withheld = {"answer": "Log it with the controller [1].", "citations": [
        {"n": 1, "id": "fin-close", "title": "Month-end close checklist", "cited": True, "classification": "confidential",
         "passage": CLOSE_TEXT}]}
    calls = []
    out = grade({"user": "bob", "question": "q", "relevant": ["fin-close"]}, withheld, judge,
                chat=lambda *a, **k: calls.append(1) or ("{}", {}))
    assert not calls and out["graded"].startswith("withheld"), f"the judge read a passage its egress list forbids: {out}"
    plain = api("POST", "/v1/ask", "alice", json={"question": PAGE_QUESTION, "model": "payments/smoke-fake"})
    assert plain.status_code == 200 and all("passage" not in c for c in plain.json()["citations"]), \
        "ask returned passages nobody asked for"


@check
def each_claim_of_an_answer_is_checked_against_what_it_cites():
    """Q3, claim-level checking: each sentence of an answer is checked against the passages it cites; an honest answer's claims are supported, an invented one's aren't, which makes its confidence low and says why; the claims are kept with the conversation, and hidden with the answer; the threshold is calibrated on the golden set's key facts and stored, and answers use it"""
    from .eval import calibrate_claims, claim_samples
    ensure_fake_models()
    reset_fake()
    honest = api("POST", "/v1/ask", "alice", json={"question": PAGE_QUESTION, "model": "payments/smoke-fake"}).json()
    assert honest["claims"] and all(c["supported"] for c in honest["claims"]), f"an honest answer's claims weren't supported: {honest['claims']}"
    httpx.post(f"{FAKE_LLM}/mode", json={"answer": "unfaithful"}, timeout=10)
    try:
        lying = api("POST", "/v1/ask", "alice", json={"question": PAGE_QUESTION, "model": "payments/smoke-fake"}).json()
    finally:
        reset_fake()
    assert lying["claims"] and not any(c["supported"] for c in lying["claims"]), f"an invented claim was supported: {lying['claims']}"
    assert lying["confidence"]["level"] == "low" and any("not supported" in r for r in lying["confidence"]["reasons"]), lying["confidence"]
    samples = claim_samples()
    assert any(label for _, label in samples) and not all(label for _, label in samples), f"the golden set gave no labeled claims: {samples}"
    fitted = calibrate_claims()
    with psycopg.connect(OWNER_DB) as conn:
        held = conn.execute("SELECT threshold FROM kl.claim_calibration c JOIN kl.orgs o ON o.id = c.org_id WHERE o.slug = 'acme'").fetchone()
    assert (fitted is None) == (held is None) and (not held or abs(held[0] - fitted["threshold"]) < 1e-6), f"the calibrated threshold wasn't stored: {fitted} {held}"


@check
def missing_model_key_is_reported():
    """Models: a model whose key vanished from the secrets backend says so (502, no backend address); setting it again fixes it"""
    ensure_fake_models()
    name = "gone-" + secrets.token_hex(3)
    r = api("POST", "/v1/workspaces/payments/models", "alice", json={
        "name": name, "provider": "openai_compatible", "model": "fake", "base_url": "http://fake-llm:8080/v1",
        "api_key": "unused"})
    assert r.status_code == 201, f"register: {r.status_code} {r.text[:200]}"
    try:
        gone = httpx.delete(f"{BAO_URL}/v1/secret/metadata/kl/acme/payments/models/{name}",
                            headers={"X-Vault-Token": BAO_TOKEN}, timeout=10)
        assert gone.status_code in (200, 204), f"secrets backend: {gone.status_code}"
        r = ask_as("alice", PAGE_QUESTION, f"payments/{name}")
        assert r.status_code == 502 and "key is missing" in r.text, f"expected a 502 naming the missing key: {r.status_code} {r.text[:200]}"
        assert "openbao" not in r.text.lower() and "8200" not in r.text, "the error gave away the secrets backend's address"
        fixed = api("PATCH", f"/v1/workspaces/payments/models/{name}", "alice", json={"api_key": "unused-again"})
        assert fixed.status_code == 200, f"setting the key again: {fixed.status_code} {fixed.text[:200]}"
        r = ask_as("alice", PAGE_QUESTION, f"payments/{name}")
        assert r.status_code == 200, f"the model still fails with its key set again: {r.status_code} {r.text[:200]}"
    finally:
        api("DELETE", f"/v1/workspaces/payments/models/{name}", "alice")


@check
def feedback_names_only_visible_results():
    """Feedback: only someone who can see a result can mark it; hidden, drifted, and missing documents get 404"""
    def mark(user, **changes):
        body = {"kind": "search", "query": "[smoke] who handles payments incidents", "verdict": "right", **changes}
        return api("POST", "/v1/feedback", user, json=body)
    r = mark("alice", result="pay-runbook")
    assert r.status_code == 201, f"alice couldn't mark a document she can see: {r.status_code} {r.text[:200]}"
    with psycopg.connect(OWNER_DB) as conn:
        scope, details = conn.execute("SELECT scope, details FROM kl.audit_events WHERE action = 'feedback' "
                                      "AND actor = 'alice' ORDER BY id DESC LIMIT 1").fetchone()
    assert "payments" in scope and details["result"] == "pay-runbook", f"feedback wasn't audited in its team: {scope} {details}"
    for user, key in (("bob", "pay-runbook"), ("dave", "fin-close"), ("alice", "pay-drift"), ("alice", "no-such-document")):
        r = mark(user, result=key)
        assert r.status_code == 404, f"{user} marking {key}: {r.status_code}; hidden, drifted, and missing must all be 404"
    r = api("POST", "/v1/feedback", "bob", json={"kind": "ask", "query": "[smoke] close question", "verdict": "helpful",
                                                 "cited": ["fin-close", "pay-runbook"]})
    assert r.status_code == 404, f"an answer's feedback named a source bob can't see: {r.status_code}"
    for changes in ({"verdict": "helpful", "result": "pay-runbook"}, {}, {"verdict": "missing", "result": "pay-runbook"}):
        r = mark("alice", **changes)
        assert r.status_code == 422, f"malformed feedback {changes} was accepted: {r.status_code}"


@check
def feedback_grows_the_golden_set():
    """Golden set: feedback becomes candidate cases to review; known questions are skipped, unanswered ones listed as gaps"""
    from .eval import load
    from .golden_mine import candidates, events
    tag = secrets.token_hex(3)
    asked = f"[smoke] where is the close checklist {tag}"
    for body in ({"query": asked, "verdict": "right", "result": "fin-close"},
                 {"query": "month-end close", "verdict": "right", "result": "fin-close"},  # already in the golden set
                 {"query": f"[smoke] vendor onboarding {tag}", "verdict": "missing"}):
        r = api("POST", "/v1/feedback", "bob", json={"kind": "search", **body})
        assert r.status_code == 201, f"feedback {body['query']}: {r.status_code} {r.text[:200]}"
    ready, gaps = candidates(events(), load(), include_smoke=True)
    mine = [c for c in ready if c["query"] == asked]
    assert mine and mine[0]["user"] == "bob" and mine[0]["relevant"] == ["fin-close"], f"no candidate for bob's question: {ready[:3]}"
    assert not any(c["user"] == "bob" and c["query"] == "month-end close" for c in ready), "a question already in the golden set came back"
    assert any(tag in g["query"] for g in gaps), "an unanswered question wasn't listed as a gap"
    real, _ = candidates(events(), load())
    assert not any(c["query"].startswith("[smoke]") for c in real), "smoke's own feedback leaked into the real candidates"


@check
def budget_stops_a_team_at_its_limit():
    """Budgets: only an org admin caps a team's model; past its monthly budget it refuses with 429, and the team's admins see why"""
    ensure_fake_models()
    name = "budget-" + secrets.token_hex(3)
    r = api("POST", "/v1/workspaces/payments/models", "alice", json={
        "name": name, "provider": "openai_compatible", "model": "fake", "base_url": "http://fake-llm:8080/v1",
        "api_key": "unused"})
    assert r.status_code == 201, f"register: {r.status_code} {r.text[:200]}"
    try:
        for user in ("alice", "bob"):  # a team admin can't lift their own team's cap, and a member can't either
            r = api("PUT", f"/v1/workspaces/payments/models/{name}/budget", user, json={"monthly_tokens": 10**9})
            assert r.status_code == 403, f"{user} set a budget: {r.status_code}"
        r = api("PUT", f"/v1/workspaces/payments/models/{name}/budget", "olivia", json={"monthly_tokens": 150})
        assert r.status_code == 200 and r.json()["monthly_tokens"] == 150, f"set the budget: {r.status_code} {r.text[:200]}"
        first = ask_as("alice", PAGE_QUESTION, f"payments/{name}")   # the fake reports 112 tokens a call
        second = ask_as("alice", PAGE_QUESTION, f"payments/{name}")  # 224: past 80% of 150
        assert first.status_code == second.status_code == 200, f"asks under budget: {first.status_code} {second.status_code}"
        assert first.json()["budget"] == {"limit": 150, "used": 112, "warning": False}, first.json().get("budget")
        assert second.json()["budget"] == {"limit": 150, "used": 224, "warning": True}, second.json().get("budget")
        third = ask_as("alice", PAGE_QUESTION, f"payments/{name}")
        assert third.status_code == 429 and "monthly budget" in third.text, f"a spent budget answered: {third.status_code} {third.text[:200]}"
        assert int(third.headers.get("retry-after", "0")) > 0, "no Retry-After on a spent budget"
        listed = {m["name"]: m for m in api("GET", "/v1/workspaces/payments/models", "alice").json()["models"]}[name]
        assert listed["monthly_token_budget"] == 150 and listed["used_this_month"] == 224, f"admins can't see the spend: {listed}"
        with psycopg.connect(OWNER_DB) as conn:
            actions = [a for (a,) in conn.execute("SELECT action FROM kl.audit_events WHERE details->>'model' = %s "
                                                  "AND 'payments' = ANY(scope) ORDER BY id", (f"payments/{name}",))]
        assert "budget.warning" in actions and "budget.exhausted" in actions, f"the team's audit trail doesn't say why: {actions}"
    finally:
        api("DELETE", f"/v1/workspaces/payments/models/{name}", "alice")


@check
def bursts_past_the_rate_limit_get_429():
    """Rate limits: past a route's limit, per person or per team, REST and MCP both answer 429 with Retry-After"""
    r = api("PUT", "/v1/limits/search", "alice", json={"per_user_per_minute": 1, "per_team_per_minute": 1})
    assert r.status_code == 403, f"a team admin changed the org's rate limits: {r.status_code}"
    try:
        r = api("PUT", "/v1/limits/search", "olivia", json={"per_user_per_minute": 3, "per_team_per_minute": 100000})
        assert r.status_code == 200, f"set the limit: {r.status_code} {r.text[:200]}"
        # Seven in a row against three a minute: even across a minute boundary, one window holds four.
        burst = [api("GET", "/v1/search", "carol", params={"q": "incident", "limit": 3}) for _ in range(7)]
        refused = [b for b in burst if b.status_code == 429]
        assert refused and all(b.status_code in (200, 429) for b in burst), f"burst: {[b.status_code for b in burst]}"
        retry = int(refused[0].headers.get("retry-after", "0"))
        assert 1 <= retry <= 60 and "rate limit" in refused[0].text, f"Retry-After {retry}: {refused[0].text[:200]}"
        # MCP calls the same handlers, so it shares the counters: three in a row against one a minute.
        api("PUT", "/v1/limits/search", "olivia", json={"per_user_per_minute": 1, "per_team_per_minute": 100000})
        errors = []
        for _ in range(3):
            try:
                mcp_call("dave", "search", {"query": "hold", "limit": 3})
            except Exception as e:  # the MCP client raises on a tool error
                errors.append(str(e))
        assert any("rate limit" in e for e in errors), f"MCP searches past the limit weren't refused: {errors[:2]}"
        # Per team: a team's asks are capped together, whoever asks. Five against two a minute.
        ensure_fake_models()
        api("PUT", "/v1/limits/ask", "olivia", json={"per_user_per_minute": 1000, "per_team_per_minute": 2})
        asks = [ask_as(user, PAGE_QUESTION, "payments/smoke-fake") for user in ("alice", "carol", "alice", "carol", "alice")]
        over = [a for a in asks if a.status_code == 429]
        assert over and "team payments" in over[0].text, f"five asks against a team limit of two: {[a.status_code for a in asks]}"
        limits = {row["route"]: row for row in api("GET", "/v1/limits", "bob").json()["limits"]}
        assert limits["ask"]["custom"] and limits["ask"]["per_team_per_minute"] == 2, f"limits as members see them: {limits}"
    finally:
        for route in ("search", "ask"):
            api("DELETE", f"/v1/limits/{route}", "olivia")
    assert not any(row["custom"] for row in api("GET", "/v1/limits", "bob").json()["limits"]), "the limits weren't reset"


@check
def github_sign_in_starts():
    """Optional: linking GitHub starts at GitHub's sign-in with the App's OAuth client, our callback, and a state"""
    from urllib.parse import parse_qs, urlsplit
    r = api("POST", "/v1/identities/github/start", "alice")
    if r.status_code == 409:
        return SKIP  # the App's OAuth credentials aren't in OpenBao yet: make github-app, then make up
    assert r.status_code == 200, r.text[:200]
    url = r.json()["url"]
    q = parse_qs(urlsplit(url).query)
    assert url.startswith("https://github.com/login/oauth/authorize?"), url
    assert q.get("client_id") and len(q.get("state", [""])[0]) >= 32, q
    assert q.get("redirect_uri") == ["http://localhost:3000/link/github/callback"], q.get("redirect_uri")
    print("      sign-in starts at GitHub with the App's client and a 43-character single-use state")


@check
def github_bronze_concepts():
    """Optional: synced GitHub docs become bronze concepts pinned to their commit, readable only by GitHub's readers"""
    with psycopg.connect(OWNER_DB) as conn:
        row = conn.execute("SELECT external_key FROM kl.documents WHERE source_kind = 'github' "
                           "AND container_ref IS NOT NULL ORDER BY external_key LIMIT 1").fetchone()
    if not row:
        return SKIP
    key = row[0]
    bronze_rows([key])
    with psycopg.connect(OWNER_DB) as conn:
        current, indexed = conn.execute(
            "SELECT d.content_hash, c.source_hash FROM kl.documents d JOIN kl.concepts c "
            "ON c.document_id = d.id AND c.tier = 'bronze' WHERE d.external_key = %s", (key,)).fetchone()
    concept = api("GET", f"/v1/concepts/{key}", "alice")
    assert concept.status_code == 200, concept.text[:200]
    # Versions are content-addressed: a concept stays pinned to the commit its content came from, which
    # is older than the repo's head whenever later commits didn't touch the file.
    assert indexed == current, "the bronze concept doesn't reflect the document's current content"
    pinned = concept.json()["source_version"]
    assert f"/blob/{pinned}/" in concept.json()["okf"], "bronze resource isn't pinned to the commit its content came from"
    assert [api("GET", f"/v1/concepts/{key}", u).status_code for u in ("bob", "carol", "dave")] == [404] * 3


@check
def live_model_probe():
    """Optional: a real model call through the registry (set KL_SMOKE_* in .env)"""
    provider, model = os.environ.get("KL_SMOKE_PROVIDER"), os.environ.get("KL_SMOKE_MODEL")
    key, base = os.environ.get("KL_SMOKE_API_KEY"), os.environ.get("KL_SMOKE_BASE_URL") or None
    if not (provider and model and key):
        return SKIP
    name = "live-" + secrets.token_hex(3)
    r = api("POST", "/v1/workspaces/payments/models", "alice",
            json={"name": name, "provider": provider, "model": model, "api_key": key, "base_url": base})
    assert r.status_code == 201, f"register: {r.status_code} {r.text[:300]}"
    p = api("POST", f"/v1/workspaces/payments/models/{name}/probe", "alice")
    assert p.status_code == 200 and p.json().get("ok"), f"probe: {p.status_code} {p.text[:300]}"


def github_app_id() -> str | None:
    if os.environ.get("GITHUB_APP_ID"):
        return os.environ["GITHUB_APP_ID"]
    meta = "/run/kl-secrets/github-app.json"  # written by `make github-app`
    if os.path.exists(meta):
        with open(meta) as f:
            return str(json.load(f)["id"])
    return None


@check
def github_app_auth():
    """Optional: GitHub App authentication (run `make github-app ORG=<your-org>` first)"""
    app_id, pem_path = github_app_id(), "/run/kl-secrets/github-app.pem"
    if not app_id or not os.path.exists(pem_path):
        return SKIP
    with open(pem_path) as f:
        pem = f.read()
    now = int(time.time())
    app_jwt = jwt.encode({"iat": now - 60, "exp": now + 540, "iss": app_id}, pem, algorithm="RS256")
    h = {"Authorization": f"Bearer {app_jwt}", "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}
    me = httpx.get("https://api.github.com/app", headers=h, timeout=15)
    assert me.status_code == 200, f"GET /app: {me.status_code} {me.text[:200]}"
    inst = httpx.get("https://api.github.com/app/installations", headers=h, timeout=15)
    assert inst.status_code == 200, f"GET /app/installations: {inst.status_code}"
    n = len(inst.json())
    print(f"      app '{me.json().get('slug')}', {n} installation(s)"
          + ("" if n else " - install it on your test repos (link printed by make github-app)"))


@check
def github_connection_syncs():
    """Optional: an admin connects the GitHub installation; the worker discovers its repos and syncs their Markdown"""
    pem_path = "/run/kl-secrets/github-app.pem"
    if not github_app_id() or not os.path.exists(pem_path):
        return SKIP
    r = api("GET", "/v1/workspaces/payments/connectors/github/installations", "alice")
    assert r.status_code == 200, f"installations: {r.status_code} {r.text[:300]}"
    installs = [i for i in r.json()["installations"] if i["repositories"]]
    if not installs:
        print("      the App has no installation with repositories yet; install it on your test repos")
        return SKIP
    inst, name = installs[0], "smoke-github"
    c = api("POST", "/v1/workspaces/payments/connections", "alice",
            json={"name": name, "installation_id": inst["installation_id"]})
    assert c.status_code in (201, 409), f"create: {c.status_code} {c.text[:300]}"
    ref = api("POST", f"/v1/workspaces/payments/connections/{name}/refresh", "alice")
    assert ref.status_code == 202, f"refresh: {ref.status_code} {ref.text[:300]}"
    requested = datetime.fromisoformat(ref.json()["requested_at"])
    deadline, synced = time.monotonic() + 180, None
    while True:
        got = api("GET", f"/v1/workspaces/payments/connections/{name}", "alice").json()
        synced = datetime.fromisoformat(got["last_synced_at"]) if got.get("last_synced_at") else None
        if (synced and synced > requested) or got["status"] == "error" or time.monotonic() > deadline:
            break
        time.sleep(2)
    assert synced and synced > requested and got["status"] != "error", \
        f"sync didn't finish: status {got['status']!r}, error {got.get('last_error')}"
    found = {x["external_id"] for x in got["containers"]}
    assert found == set(inst["repositories"]), f"discovered {sorted(found)}, expected {inst['repositories']}"
    with open(pem_path) as f:
        key_line = f.read().strip().splitlines()[1][:40]  # a line of private key material
    with psycopg.connect(OWNER_DB) as conn:
        rows = conn.execute("SELECT container_ref, source_version, source_uri, acl_principals FROM kl.documents "
                            "WHERE source_kind IN ('github', 'okf') AND container_ref = ANY(%s)", (sorted(found),)).fetchall()
        dump = conn.execute(
            """SELECT coalesce(string_agg(t, ' '), '') FROM (
                 SELECT row_to_json(c)::text AS t FROM kl.connections c
                 UNION ALL SELECT row_to_json(j)::text FROM kl.jobs j
                 UNION ALL SELECT details::text FROM kl.audit_events) x""").fetchone()[0]
    assert key_line not in dump, "GitHub App key material found in Postgres"
    repos = {row[0] for row in rows}
    assert repos >= found, f"no Markdown synced from {sorted(found - repos)}"
    for repo, version, uri, acl in rows:
        assert re.fullmatch(r"[0-9a-f]{40}", version or "") and f"/blob/{version}/" in uri, f"{uri} isn't pinned to a commit"
        assert acl and all(p.startswith("ext:github.") for p in acl), f"{uri} carries non-GitHub grants: {acl}"
    print(f"      {len(rows)} Markdown file(s) from {', '.join(sorted(repos))}, each pinned to its commit")


@check
def github_permissions_mirrored():
    """Optional: GitHub's permissions decide who finds synced docs (alice = you, bob = octocat, carol unlinked)"""
    login = os.environ.get("KL_DEV_GITHUB_LOGIN", "").strip().lower()
    with psycopg.connect(OWNER_DB) as conn:
        synced = conn.execute("SELECT count(*) FROM kl.documents "
                              "WHERE source_kind = 'github' AND container_ref IS NOT NULL").fetchone()[0]
    if not login or not synced:
        if not login:
            print("      set KL_DEV_GITHUB_LOGIN in .env, then make up")
        return SKIP
    me = api("GET", "/v1/whoami", "alice").json()
    assert me.get("identities") == [f"ext:github.{login}"], f"alice: {me.get('identities')}"

    def github_hits(user: str) -> list[dict]:
        r = api("GET", "/v1/search", user, params={"q": "synchronous replication", "scope": "org"})
        assert r.status_code == 200, f"search as {user}: {r.status_code}"
        return [h for h in r.json()["results"] if re.match(r"https://github\.com/.+/blob/[0-9a-f]{40}/", h["source"])]

    alice = github_hits("alice")
    assert alice, "alice can't find the synced ADR; does your GitHub login have access to payments-api?"
    for user in ("bob", "carol", "dave"):
        assert not github_hits(user), f"{user} found GitHub docs that GitHub doesn't grant them"
    print(f"      alice found '{alice[0]['title']}' ({alice[0]['section']}); bob, carol, and dave found nothing")


def smoke_connection() -> dict | None:
    r = api("GET", "/v1/workspaces/payments/connections/smoke-github", "alice")
    return r.json() if r.status_code == 200 and r.json().get("last_synced_at") else None


def wait_for_sync_after(moment: datetime, seconds: int = 120) -> datetime | None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        c = smoke_connection()
        if c and datetime.fromisoformat(c["last_synced_at"]) > moment:
            return datetime.fromisoformat(c["last_synced_at"])
        time.sleep(2)
    return None


@check
def webhook_push_resyncs():
    """Optional: a signed GitHub push re-syncs the affected connection; replaying the delivery does nothing"""
    secrets_path, conn_info = "/run/kl-secrets/github-app-secrets.json", smoke_connection()
    if not os.path.exists(secrets_path) or not conn_info:
        return SKIP
    with open(secrets_path) as f:
        secret = json.load(f).get("webhook_secret")
    repo = next((c for c in conn_info["containers"] if c["selected"]), None)
    if not secret or not repo:
        return SKIP
    before = datetime.fromisoformat(conn_info["last_synced_at"])
    body = json.dumps({"ref": f"refs/heads/{repo['default_ref']}",
                       "repository": {"full_name": repo["external_id"], "default_branch": repo["default_ref"]},
                       "installation": {"id": conn_info["installation_id"]}}).encode()
    headers = {"X-GitHub-Event": "push", "X-GitHub-Delivery": str(uuid.uuid4()), "Content-Type": "application/json",
               "X-Hub-Signature-256": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}
    first = httpx.post(f"{API}/v1/webhooks/github", content=body, headers=headers, timeout=30)
    assert first.status_code == 202 and first.json().get("queued", 0) >= 1, f"push: {first.status_code} {first.text[:200]}"
    replay = httpx.post(f"{API}/v1/webhooks/github", content=body, headers=headers, timeout=30)
    assert replay.status_code == 202 and replay.json() == {"duplicate": True, "queued": 0}, f"replay: {replay.text[:200]}"
    assert wait_for_sync_after(before), "the push didn't trigger a re-sync within 120s; check `docker compose logs worker`"
    print(f"      push to {repo['external_id']} re-synced smoke-github; the replay was ignored")


@check
def scheduled_resync():
    """Optional: a connection that hasn't synced within the window is re-synced on schedule, with no event"""
    if not smoke_connection():
        return SKIP
    with psycopg.connect(OWNER_DB) as conn:
        stale = conn.execute("UPDATE kl.connections SET last_synced_at = now() - interval '1 day' "
                             "WHERE name = 'smoke-github' RETURNING last_synced_at").fetchone()[0]
    assert wait_for_sync_after(stale, seconds=150), "the scheduler didn't re-sync a day-old connection within 150s"
    print("      a day-old connection was re-synced by the scheduler")


@check
def github_semantic_search():
    """Optional: the paraphrase finds synced GitHub docs by meaning for alice, never for bob or carol"""
    if not os.environ.get("KL_DEV_GITHUB_LOGIN") or not smoke_connection():
        return SKIP

    def synced(user: str) -> list[dict]:
        r = api("GET", "/v1/search", user, params={"q": PARAPHRASE, "limit": 50})
        assert r.status_code == 200, f"search as {user}: {r.status_code}"
        return [h for h in r.json()["results"] if re.match(r"https://github\.com/.+/blob/[0-9a-f]{40}/", h["source"])]

    alice = [h for h in synced("alice") if "vector" in h["matched"]]
    assert alice, "no synced doc matched by meaning for alice"
    for user in ("bob", "carol"):
        assert not synced(user), f"{user} got synced docs by meaning"
    print(f"      alice: '{alice[0]['title']}' ({alice[0]['section']}) by meaning; bob and carol: nothing")


@check
def github_knowledge_repo():
    """Optional: a *-knowledge repo syncs as curated concepts pinned to its commit, readable only by GitHub's readers"""
    # Runs after the GitHub sync checks, so a newly created knowledge repo is synced by the time it's checked.
    with psycopg.connect(OWNER_DB) as conn:
        tiers = dict(conn.execute("SELECT external_key, tier FROM kl.documents WHERE source_kind = 'okf' "
                                  "AND external_key IN ('gh-kb-rollback', 'gh-kb-deploys')").fetchall())
        if not tiers:
            return SKIP  # run `make sandbox-github ORG=<your-org>` to create the repo, then make smoke
        doc = conn.execute("SELECT source_uri, source_version FROM kl.documents "
                           "WHERE external_key = 'gh-kb-rollback'").fetchone()
        edge = conn.execute("SELECT 1 FROM kl.concept_edges e JOIN kl.concepts f ON f.id = e.from_concept_id "
                            "WHERE f.concept_key = 'gh-kb-rollback' AND e.to_key = 'gh-kb-deploys'").fetchone()
    assert tiers == {"gh-kb-rollback": "gold", "gh-kb-deploys": "silver"}, tiers
    assert edge, "the rollback concept's relation didn't become an edge"
    uri, commit = doc
    assert re.fullmatch(r"[0-9a-f]{40}", commit) and uri.endswith(f"/blob/{commit}/rollback.md"), doc
    assert api("GET", "/v1/concepts/gh-kb-rollback", "alice").status_code == 200
    related = {r["id"] for r in api("GET", "/v1/concepts/gh-kb-rollback/related", "alice").json()["related"]}
    assert related == {"gh-kb-deploys"}, related
    assert [api("GET", "/v1/concepts/gh-kb-rollback", u).status_code for u in ("bob", "carol", "dave")] == [404] * 3
    print(f"      2 curated concepts from payments-knowledge at {commit[:7]}, linked; alice reads them, bob, carol, and dave don't")


@check
def curator_pull_request_loop():
    """Optional: the curator opens a proposal as a PR; once merged it's indexed, and history shows the whole path"""
    from .github import CURATOR_REF, GitHubNotConfigured, load_app
    try:
        curator = load_app(CURATOR_REF)
    except GitHubNotConfigured:
        return SKIP  # run `make curator-app ORG=<your-org>`, install it on payments-knowledge, then make up
    with psycopg.connect(OWNER_DB) as conn:
        repo = conn.execute("SELECT external_id FROM kl.connection_containers "
                            "WHERE split_part(external_id, '/', 2) = 'payments-knowledge' LIMIT 1").fetchone()
    if not repo:
        return SKIP
    if os.environ.get("KL_SMOKE_GITHUB_WRITES") != "1":  # it writes to GitHub, and adds a curated page each run
        print("      set KL_SMOKE_GITHUB_WRITES=1 in .env to let it open and merge a pull request in your sandbox")
        return SKIP
    ensure_fake_models()
    r = compile_as("alice")
    assert r.status_code == 201, r.text[:300]
    pid, key = r.json()["proposal"], r.json()["concept_id"]
    assert api("POST", f"/v1/proposals/{pid}/pr", "alice").status_code == 202

    def proposal() -> dict:
        return api("GET", f"/v1/proposals/{pid}", "alice").json()

    deadline = time.monotonic() + 120
    while (p := proposal())["status"] == "drafted" and time.monotonic() < deadline:
        time.sleep(2)
    assert p["status"] == "pr_opened", f"proposal is {p['status']}: {p.get('events')}"
    # Act as the reviewer and merge (sandbox only). The worker must notice from GitHub, then index the merge.
    full = repo[0]
    inst = curator.repo_installation(full)
    merged = curator.call(inst, "PUT", f"/repos/{full}/pulls/{p['pr_number']}/merge",
                          json={"merge_method": "squash"}).json()["sha"]
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        concept = api("GET", f"/v1/concepts/{key}", "alice")
        if proposal()["status"] == "merged" and concept.status_code == 200 \
                and concept.json()["source_version"] == merged:
            break
        time.sleep(3)
    else:
        raise AssertionError(f"merge {merged[:7]} not indexed; proposal is {proposal()['status']}")
    kinds = [e["kind"] for e in api("GET", f"/v1/concepts/{key}/history", "alice").json()["events"]]
    assert {"pr_opened", "merged", "indexed"} <= set(kinds), kinds
    assert api("GET", f"/v1/concepts/{key}", "bob").status_code == 404
    print(f"      {p['pr_url']} opened, merged at {merged[:7]}, and indexed; history: {' -> '.join(dict.fromkeys(kinds))}")


@check
def github_checks_leave_the_seed_alone():
    """Optional: the GitHub checks' connection is removed when they're done, and what it brought in goes with it, so the checks after them see the seed alone"""
    r = api("GET", "/v1/workspaces/payments/connections/smoke-github", "alice")
    if r.status_code == 404:
        return SKIP
    repos = [c["external_id"] for c in r.json()["containers"]]
    assert api("DELETE", "/v1/workspaces/payments/connections/smoke-github", "alice").status_code == 202

    def brought_in() -> int:
        with psycopg.connect(OWNER_DB) as conn:
            return conn.execute("SELECT count(*) FROM kl.documents WHERE container_ref = ANY(%s)", (repos,)).fetchone()[0]

    deadline = time.monotonic() + 120  # removal is a job
    while brought_in() and time.monotonic() < deadline:
        time.sleep(2)
    assert not brought_in(), f"documents from {repos} stayed after their connection was removed"


@check
def web_signin_keeps_tokens_server_side():
    """Optional: the web app signs alice in with PKCE, keeps her tokens server-side, and serves her map within her permissions"""
    import html
    from urllib.parse import parse_qs, urlsplit
    web, public_web = os.environ.get("KL_WEB_URL", "http://web:3000"), "http://localhost:3000"
    try:
        httpx.get(f"{web}/auth/login", timeout=5)
    except httpx.HTTPError:
        return SKIP  # run `make web`
    assert httpx.get(f"{web}/api/graph", timeout=10).status_code == 401, "the proxy served an anonymous request"
    public_kc, internal_kc = ISSUER.split("/realms/")[0], TOKEN_URL.split("/realms/")[0]
    with httpx.Client(timeout=30) as browser:  # a browser without JavaScript; redirects are followed by hand
        auth = browser.get(f"{web}/auth/login").headers["location"]
        q = parse_qs(urlsplit(auth).query)
        assert q.get("code_challenge_method") == ["S256"] and q.get("client_id") == ["kl-web"] and q.get("code_challenge"), auth
        # Keycloak hands out localhost:8081 URLs (its public name); inside the network it's keycloak:8080.
        page = browser.get(auth.replace(public_kc, internal_kc))
        form = re.search(r"<form[^>]*kc-form-login[^>]*>", page.text)
        assert form, f"no Keycloak login form (status {page.status_code})"
        action = html.unescape(re.search(r'action="([^"]+)"', form.group(0)).group(1))
        back = browser.post(action.replace(public_kc, internal_kc),
                            data={"username": "alice", "password": PASSWORDS["alice"], "credentialId": ""})
        callback = back.headers.get("location", "")
        assert callback.startswith(f"{public_web}/auth/callback?"), f"Keycloak sent the browser to {callback[:120]!r}"
        landed = browser.get(callback.replace(public_web, web))
        assert landed.is_redirect, f"callback: {landed.status_code} {landed.text[:200]}"
        seen = landed.text + " ".join(landed.headers.values())
        # Keycloak's own SSO cookies (on Keycloak's domain) are JWTs by design. The app's cookies must never be:
        # the session cookie is an opaque random id, and nothing the app sends the browser holds a token.
        app_cookies = {c.name: c.value for c in browser.cookies.jar if c.name.startswith("kl_")}
        assert re.fullmatch(r"[A-Za-z0-9_-]{43}", app_cookies.get("kl_session", "")), f"session cookie: {app_cookies}"
        jwt_shape = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.")
        assert not jwt_shape.search(seen + " ".join(app_cookies.values())), "a token reached the browser"
        me = browser.get(f"{web}/api/whoami")
        assert me.status_code == 200 and me.json()["username"] == "alice", me.text[:200]
        got = {n["id"] for n in browser.get(f"{web}/api/graph").json()["nodes"]} & FIXTURES
        assert got == MAP_EXPECTED["alice"], got
        forged = browser.post(f"{web}/api/ask", headers={"Origin": "https://attacker.example"},
                              json={"question": PAGE_QUESTION, "model": "payments/smoke-fake"})
        assert forged.status_code == 403, "a cross-site POST went through"
    print("      alice signed in with PKCE; the browser holds only an opaque session cookie; her map matches her permissions")


# ------------------------------------------------------------------ telemetry (slice G)
TELEMETRY_FILE = "/telemetry/otel.jsonl"  # the collector's file export, shared read-only with smoke
ASK_STAGES = {"POST /v1/ask", "retrieve", "retrieve.lexical", "retrieve.vector", "retrieve.post_check",
              "ask.egress", "ask.model"}


def telemetry_on() -> bool:
    """`make obs` sets the endpoint (smoke shares the services' environment) and the collector writes the file."""
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")) and os.path.exists(TELEMETRY_FILE)


TOKEN = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.")  # a JWT's header and payload


def telemetry_lines():
    """Every line the collector exported, oldest file first, one at a time. The export runs to hundreds of megabytes,
    so nothing here holds it in memory; the collector rotates it into the current file and a few older ones."""
    folder, name = os.path.split(TELEMETRY_FILE)
    files = sorted(glob.glob(os.path.join(folder, name.replace(".jsonl", "*.jsonl"))), key=os.path.getmtime)
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                yield from f
        except FileNotFoundError:  # rotated away while we read
            continue
        except PermissionError:
            raise AssertionError(f"smoke can't read {path}: run `make obs`, whose telemetry-init makes the export readable")


def telemetry_until(scan, done, seconds: int = 45):
    """Scan the export until `done(result)` holds or `seconds` pass, and return the last result either way."""
    deadline = time.monotonic() + seconds
    while True:
        result = scan()
        if done(result) or time.monotonic() > deadline:
            return result
        time.sleep(2)


def ask_traces() -> dict[str, set[str]]:
    """Span names by trace. A line naming no ask stage can't add one to a trace, so only lines that name one are
    decoded: most of the export is never parsed."""
    stages, traces = tuple(f'"{s}"' for s in ASK_STAGES), {}
    for line in telemetry_lines():
        if not any(stage in line for stage in stages):
            continue
        try:
            batch = json.loads(line)
        except json.JSONDecodeError:
            continue
        for resource in batch.get("resourceSpans", []):
            for scope in resource.get("scopeSpans", []):
                for s in scope.get("spans", []):
                    traces.setdefault(s.get("traceId", ""), set()).add(s.get("name", ""))
    return traces


@check
def telemetry_traces_every_ask_stage():
    """Optional: one ask is one trace, with spans for retrieval, the post-check, egress, and the model call"""
    if not telemetry_on():
        return SKIP  # run `make obs`
    ensure_fake_models()
    r = api("POST", "/v1/ask", "alice", json={"question": PAGE_QUESTION, "model": "payments/smoke-fake", "scope": "payments"})
    assert r.status_code == 200, r.text[:200]
    traces = telemetry_until(ask_traces, lambda t: any(ASK_STAGES <= names for names in t.values()))
    best = max(traces.values(), key=lambda names: len(names & ASK_STAGES), default=set())
    assert ASK_STAGES <= best, f"no single trace had every stage; closest was missing {sorted(ASK_STAGES - best)}"
    print(f"      one trace: {' > '.join(sorted(ASK_STAGES, key=len))}")


@check
def telemetry_counts_policy_decisions():
    """Optional: the seeded ACL drift and the egress withholds from this run are exported as metrics"""
    if not telemetry_on():
        return SKIP
    wanted = ('"kl.acl_drift.drops"', '"kl.egress.withheld"', '"kl.http.server.duration"')

    def seen() -> set[str]:
        found: set[str] = set()
        for line in telemetry_lines():
            found |= {w for w in wanted if w in line}
            if len(found) == len(wanted):
                break
        return found
    found = telemetry_until(seen, lambda f: len(f) == len(wanted))
    missing = [w.strip('"') for w in wanted if w not in found]
    assert not missing, f"metrics not exported: {missing}"


@check
def telemetry_never_carries_content():
    """Optional: exported telemetry holds no document text, question, model answer, or token (canary check)"""
    if not telemetry_on():
        return SKIP
    from .fake_llm import ANSWER
    canaries = {"confidential document text": CLOSE_TEXT, "a question": PAGE_QUESTION, "a model answer": ANSWER[:40],
                "a compile topic": "make-a-bad-draft"}

    def scan():  # every line, every file: the whole export, in constant memory (a canary can't span two lines)
        size, kinds, found, token = 0, set(), set(), False
        for line in telemetry_lines():
            size += len(line)
            kinds |= {k for k in ("resourceSpans", "resourceMetrics") if k in line}
            found |= {name for name, canary in canaries.items() if canary and canary in line}
            token = token or bool(TOKEN.search(line))
        return size, kinds, found, token
    size, kinds, found, token = telemetry_until(scan, lambda r: len(r[1]) == 2)
    assert kinds == {"resourceSpans", "resourceMetrics"}, "nothing exported yet, so nothing to check"
    assert not found, f"telemetry carried {sorted(found)}"
    assert not token, "telemetry carried a token"
    print(f"      {size // 1024} KB exported; none of {len(canaries)} canaries or any token in it")


# ------------------------------------------------------------------ alerts in the app (slice P, ADR 0005)
def evaluate_alerts() -> None:
    """Have the worker evaluate alerts now rather than on its next tick, and wait for it to finish."""
    r = api("POST", "/v1/alerts/evaluate", "olivia")
    assert r.status_code == 202, f"evaluate: {r.status_code} {r.text[:200]}"
    job, deadline = r.json()["job"], time.monotonic() + 60
    while time.monotonic() < deadline:
        with psycopg.connect(OWNER_DB) as conn:
            status = conn.execute("SELECT status FROM kl.jobs WHERE id = %s", (job,)).fetchone()[0]
        if status in ("done", "failed"):
            assert status == "done", "the alert evaluation job failed"
            return
        time.sleep(1)
    raise AssertionError("the worker never ran the alert evaluation")


@check
def alerts_fire_in_the_app():
    """Alerts: a forced ACL drift spike raises an alert in the app; org admins see it, team admins only their team's, members get 403"""
    for _ in range(8):  # each of alice's searches meets the seeded drift row, which the post-check drops
        search_ids("alice")
    evaluate_alerts()
    r = api("GET", "/v1/alerts", "olivia")
    assert r.status_code == 200, f"org admin: {r.status_code} {r.text[:200]}"
    alerts = r.json()["alerts"]
    drift = [a for a in alerts if a["rule"] == "acl_drift" and a["subject"] == "search" and a["status"] == "firing"]
    assert drift and drift[0]["severity"] == "page" and drift[0]["workspace"] is None, \
        f"no drift alert: {[(a['rule'], a['subject']) for a in alerts]}"
    assert not any(a["rule"] == "worker_down" for a in alerts), "the worker is running, but the app says it stopped"
    with psycopg.connect(OWNER_DB) as conn:  # the missing-key check answered 502s earlier: they must be signals
        errors = conn.execute("SELECT count(*) FROM kl.signals WHERE kind = 'server_error' "
                              "AND at > now() - interval '1 hour'").fetchone()[0]
    assert errors > 0, "server errors aren't being recorded, so their alert can never fire"
    text = json.dumps(drift[0])
    assert "pay-drift" not in text and "alice" not in text, "the alert carried a document or a person, not just ids"
    assert api("GET", "/v1/alerts", "bob").status_code == 403, "a member read alerts"
    team = api("GET", "/v1/alerts", "alice").json()["alerts"]  # alice administers payments, and only payments
    assert all(a["workspace"] == "payments" for a in team), \
        f"alice saw alerts beyond her team: {[(a['rule'], a['workspace']) for a in team]}"
    assert any(a["rule"] == "budget_exhausted" for a in team), "payments' admin can't see the budget alert the budget check raised"
    assert api("POST", f"/v1/alerts/{drift[0]['id']}/acknowledge", "alice").status_code == 404, "a team admin acknowledged an org alert"
    assert api("POST", f"/v1/alerts/{drift[0]['id']}/acknowledge", "olivia").status_code == 200
    seen = {a["id"]: a for a in api("GET", "/v1/alerts", "olivia").json()["alerts"]}[drift[0]["id"]]
    assert seen["acknowledged_by"] == "olivia" and seen["status"] == "firing", f"acknowledged: {seen}"
    print(f"      {drift[0]['summary'][:90]}...")


@check
def alert_webhook_posts_what_fires():
    """Alerts: an org admin sends firing alerts to a webhook (Slack or any other), whose URL stays in the secrets backend"""
    hook = f"{FAKE_LLM}/alerts"
    assert api("PUT", "/v1/alerts/webhook", "alice", json={"url": hook}).status_code == 403, "a team admin set the org's webhook"
    try:
        r = api("PUT", "/v1/alerts/webhook", "olivia", json={"url": hook})
        assert r.status_code == 200, f"set: {r.status_code} {r.text[:200]}"
        with psycopg.connect(OWNER_DB) as conn:
            ref, row = conn.execute("SELECT webhook_ref, to_jsonb(s)::text FROM kl.alert_settings s").fetchone()
        assert ref and hook not in row, "the webhook URL reached Postgres"
        before = len(httpx.get(hook, timeout=10).json())
        r = api("POST", "/v1/alerts/webhook/test", "olivia")
        assert r.status_code == 200 and r.json()["delivered"], f"test message: {r.text[:200]}"
        with psycopg.connect(OWNER_DB) as conn:  # post the drift alert again, as if this webhook were new
            conn.execute("UPDATE kl.alerts SET notified = NULL WHERE rule = 'acl_drift' AND status = 'firing'")
        for _ in range(8):  # and keep it firing
            search_ids("alice")
        evaluate_alerts()
        posted = httpx.get(hook, timeout=10).json()[before:]
        assert any(p.get("text", "").startswith("[knowledge-layer] Test") for p in posted), f"no test message: {posted[:2]}"
        # Resolutions owed from earlier runs arrive too (they resolved while no webhook was set); look for the page.
        drift = [p for p in posted if p.get("alert", {}).get("rule") == "acl_drift" and p["alert"].get("status") == "firing"]
        assert drift and drift[0]["text"].startswith("[knowledge-layer] Page:"), \
            f"the drift alert wasn't posted: {[p.get('text') for p in posted]}"
        assert "pay-drift" not in json.dumps(drift) and "alice" not in json.dumps(drift), "the posted alert carried content"
    finally:
        api("DELETE", "/v1/alerts/webhook", "olivia")
    assert not api("GET", "/v1/alerts/webhook", "olivia").json()["set"], "the webhook outlived its removal"


# ------------------------------------------------------------------ data subjects and access reviews (slice I1)
OTHER_PEOPLE = ("bob", "carol", "dave", "olivia")


@check
def export_holds_only_your_data():
    """Data subjects: alice's export holds her teams, links, model use, and audit trail, and no one else's data"""
    r = api("GET", "/v1/me/export", "alice")
    assert r.status_code == 200, f"export: {r.status_code} {r.text[:200]}"
    data = r.json()
    assert data["subject"] == "alice" and "payments" in data["teams"]["admin"], f"teams: {data['teams']}"
    assert data["audit_events"] and data["model_usage"], "alice's export misses her activity or her model use"
    assert all(link["identity"].startswith("ext:") for link in data["identity_links"])
    text = json.dumps(data)
    leaked = [p for p in OTHER_PEOPLE if f'"{p}"' in text or f"user:{p}" in text or f"admin:{p}" in text]
    assert not leaked, f"alice's export names other people: {leaked}"
    assert "ext:github.octocat" not in text, "alice's export names bob's GitHub identity"
    assert api("GET", "/v1/users/alice/export", "bob").status_code == 403, "a member exported someone else's data"
    r = api("GET", "/v1/users/alice/export", "olivia")
    assert r.status_code == 200 and r.json()["subject"] == "alice", "an org admin couldn't answer alice's request"
    print(f"      {len(data['audit_events'])} audit events, {len(data['identity_links'])} identity link(s), "
          f"{sum(u['calls'] for u in data['model_usage'])} model calls")


@check
def erasure_leaves_only_a_pseudonym():
    """Data subjects: erasing a person removes their access and links, and every record names a pseudonym instead"""
    from .authz import fga
    who, org = f"zoe-{secrets.token_hex(3)}", acme_org_id()
    with psycopg.connect(OWNER_DB) as conn:  # a person with a little history: an event, a mention, a link, a team
        conn.execute("INSERT INTO kl.audit_events (org_id, actor, action, details) VALUES (%s, %s, 'search', %s::jsonb)",
                     (org, who, json.dumps({"query": "zoe's private question", "returned": ["handbook"]})))
        conn.execute("INSERT INTO kl.audit_events (org_id, actor, action, details) VALUES (%s, 'olivia', 'team.grant', %s::jsonb)",
                     (org, json.dumps({"principal": f"user:{who}", "role": "member"})))
        conn.execute("INSERT INTO kl.principal_aliases (org_id, source, canonical, verified_by) VALUES (%s, %s, %s, 'admin:olivia')",
                     (org, f"ext:github.{who}", f"user:{who}"))
    fga.write(writes={(f"user:{who}", "member", "workspace:commons")})
    try:
        assert api("POST", f"/v1/users/{who}/erase", "bob", json={"confirm": who}).status_code == 403, "a member erased someone"
        r = api("POST", f"/v1/users/{who}/erase", "olivia", json={"confirm": "someone-else"})
        assert r.status_code == 422, f"an unconfirmed erasure ran: {r.status_code}"
        r = api("POST", f"/v1/users/{who}/erase", "olivia", json={"confirm": who})
        assert r.status_code == 200, f"erase: {r.status_code} {r.text[:200]}"
        out = r.json()
        pseudonym = out["pseudonym"]
        assert out["counts"]["remaining"] == 0 and out["counts"]["identity_links"] == 1 and out["authorization_removed"] >= 1, out
        assert not fga.check(f"user:{who}", "member", "workspace:commons"), "the erased person kept their access"
        with psycopg.connect(OWNER_DB) as conn:
            own = [d for (d,) in conn.execute("SELECT details FROM kl.audit_events WHERE actor = %s", (pseudonym,))]
            mentions = conn.execute("SELECT count(*) FROM kl.audit_events WHERE details->>'principal' = %s",
                                    (f"user:{pseudonym}",)).fetchone()[0]
            named = conn.execute("SELECT count(*) FROM kl.audit_events WHERE actor = %s OR strpos(details::text, %s) > 0",
                                 (who, who)).fetchone()[0]
            named += conn.execute("SELECT count(*) FROM kl.principal_aliases WHERE strpos(source || canonical, %s) > 0",
                                  (who,)).fetchone()[0]
            record = conn.execute("SELECT details FROM kl.audit_events WHERE action = 'subject.erased' "
                                  "ORDER BY id DESC LIMIT 1").fetchone()[0]
        assert len(own) == 1 and "query" not in own[0] and own[0].get("erased"), f"their own event: {own}"
        assert mentions == 1 and named == 0, f"olivia's event names the pseudonym {mentions}x; rows still naming them: {named}"
        assert record["pseudonym"] == pseudonym and who not in json.dumps(record), "the erasure's own record names the person"
    finally:
        try:
            fga.write(deletes={(f"user:{who}", "member", "workspace:commons")})
        except RuntimeError:
            pass  # already gone, as it should be


@check
def access_review_shows_who_can_see_what():
    """Access reviews: org admins see every team's admins, members, and documents by classification; no one else can"""
    assert api("GET", "/v1/access-review", "alice").status_code == 403, "a team admin ran the org's access review"
    r = api("GET", "/v1/access-review", "olivia")
    assert r.status_code == 200, f"review: {r.status_code} {r.text[:200]}"
    review = r.json()
    teams = {t["workspace"]: t for t in review["teams"]}
    assert {"payments", "finance", "commons", "legal"} <= set(teams), f"teams in the review: {sorted(teams)}"
    assert "user:alice" in teams["payments"]["admins"], f"payments' admins: {teams['payments']['admins']}"
    assert "user:dave" in teams["legal"]["members"] + teams["legal"]["admins"], "legal's member is missing (private team)"
    assert sum(teams["payments"]["documents"].values()) > 0, "no document counts"
    assert "user:olivia" in review["org_admins"], f"org admins: {review['org_admins']}"


# ------------------------------------------------------------------ retention and legal holds (slice I2)
def run_job(kind: str, user: str = "olivia") -> None:
    """Queue a job through the API (an org admin's endpoint) and wait for the worker to finish it."""
    r = api("POST", {"retention.apply": "/v1/retention/apply", "alerts.evaluate": "/v1/alerts/evaluate"}[kind], user)
    assert r.status_code == 202, f"{kind}: {r.status_code} {r.text[:200]}"
    job, deadline = r.json()["job"], time.monotonic() + 120
    while time.monotonic() < deadline:
        with psycopg.connect(OWNER_DB) as conn:
            status, error = conn.execute("SELECT status, last_error FROM kl.jobs WHERE id = %s", (job,)).fetchone()
        if status in ("done", "failed"):
            assert status == "done", f"the {kind} job failed: {error}"
            return
        time.sleep(1)
    raise AssertionError(f"the worker never ran {kind}")


def footprint(conn, doc_id, chunk_ids: list, concept_ids: list) -> dict:
    """Rows anywhere that still hold or derive from a document: every kl table with a document_id column
    (so a table added later is covered too), plus its chunks' embeddings, its concepts' history, and map links."""
    tables = [t for (t,) in conn.execute(
        "SELECT c.table_name FROM information_schema.columns c JOIN information_schema.tables t "
        "ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
        "WHERE c.table_schema = 'kl' AND c.column_name = 'document_id' AND t.table_type = 'BASE TABLE'")]
    rows = {t: conn.execute(f"SELECT count(*) FROM kl.{t} WHERE document_id = %s", (doc_id,)).fetchone()[0] for t in tables}
    rows["documents"] = conn.execute("SELECT count(*) FROM kl.documents WHERE id = %s", (doc_id,)).fetchone()[0]
    rows["semantic_edges"] = conn.execute("SELECT count(*) FROM kl.semantic_edges WHERE %s IN (source_id, target_id)",
                                          (doc_id,)).fetchone()[0]
    rows["chunk_embeddings"] = conn.execute("SELECT count(*) FROM kl.chunk_embeddings WHERE chunk_id = ANY(%s)",
                                            (chunk_ids,)).fetchone()[0]
    for t in ("concept_versions", "concept_events"):
        rows[t] = conn.execute(f"SELECT count(*) FROM kl.{t} WHERE concept_id = ANY(%s)", (concept_ids,)).fetchone()[0]
    return {t: n for t, n in rows.items() if n}


@check
def expired_documents_leave_every_table():
    """Retention: an expired document disappears from every table and from authorization; a legal hold keeps another until released"""
    import uuid
    from .authz import fga
    org, tag = acme_org_id(), uuid.uuid4().hex[:8]
    teams = {name: f"rt-{name}-{tag}" for name in ("free", "held")}  # throwaway teams: the seed stays untouched
    ws_ids, ids, keys = {}, {}, {}
    with psycopg.connect(OWNER_DB) as conn:
        for name, slug in teams.items():
            ws_ids[name] = conn.execute("INSERT INTO kl.workspaces (org_id, slug, name, discoverability) "
                                        "VALUES (%s, %s, 'Retention test', 'private') RETURNING id", (org, slug)).fetchone()[0]
            keys[name] = f"{slug}-doc"
            ids[name] = conn.execute(
                "INSERT INTO kl.documents (org_id, source_kind, source_uri, source_version, external_key, title, content_hash, "
                "classification, acl_principals, created_at, acl_synced_at) VALUES (%s, 'seed', %s, 'v1', %s, 'Retention test', "
                "%s, 'internal', %s, now() - interval '400 days', now() - interval '400 days') RETURNING id",
                (org, f"https://kb.acme.test/{keys[name]}", keys[name], keys[name], ["user:alice"])).fetchone()[0]
            conn.execute("INSERT INTO kl.chunks (org_id, document_id, ordinal, heading_path, body, acl_principals) "
                         "VALUES (%s, %s, 0, NULL, %s, %s)", (org, ids[name], f"An old note for the retention test {tag}.", ["user:alice"]))
            conn.execute("INSERT INTO kl.workspace_documents (workspace_id, document_id, org_id) VALUES (%s, %s, %s)",
                         (ws_ids[name], ids[name], org))
        conn.execute("INSERT INTO kl.jobs (org_id, kind) VALUES (%s, 'embeddings.backfill')", (org,))  # derived rows too
    tuples = {("user:alice", "viewer", f"document:{k}") for k in keys.values()} | {("user:alice", "admin", f"workspace:{teams['held']}")}
    fga.write(writes=tuples)
    policies = []
    try:
        deadline, derived = time.monotonic() + 120, 0
        while time.monotonic() < deadline:  # every derived row exists before retention runs: embeddings, concepts, links
            with psycopg.connect(OWNER_DB) as conn:
                derived = conn.execute("SELECT count(*) FROM kl.concepts c JOIN kl.document_vectors v ON v.document_id = c.document_id "
                                       "WHERE c.document_id = ANY(%s)", (list(ids.values()),)).fetchone()[0]
            if derived >= 2:
                break
            time.sleep(2)
        assert derived >= 2, "the test documents never got their concepts and vectors"
        for user in ("alice", "bob"):
            r = api("PUT", "/v1/retention/policies", user, json={"workspace": teams["free"], "target": "documents", "keep_days": 30})
            assert r.status_code == 403, f"{user} set a retention policy: {r.status_code}"
        for name in teams:
            r = api("PUT", "/v1/retention/policies", "olivia", json={"workspace": teams[name], "target": "documents", "keep_days": 30})
            assert r.status_code == 200, f"policy: {r.status_code} {r.text[:200]}"
            policies.append(r.json()["id"])
        r = api("POST", "/v1/legal-holds", "olivia", json={"workspace": teams["held"], "reason": "smoke: litigation hold"})
        assert r.status_code == 201, f"hold: {r.status_code} {r.text[:200]}"
        hold = r.json()["id"]
        r = api("POST", f"/v1/workspaces/{teams['held']}/purge", "alice", json={"confirm": teams["held"]})  # its admin
        assert r.status_code == 409 and "legal hold" in r.text, f"a held team could be purged: {r.status_code} {r.text[:200]}"
        with psycopg.connect(OWNER_DB) as conn:
            chunk_ids = {n: [c for (c,) in conn.execute("SELECT id FROM kl.chunks WHERE document_id = %s", (ids[n],))] for n in teams}
            concept_ids = {n: [c for (c,) in conn.execute("SELECT id FROM kl.concepts WHERE document_id = %s", (ids[n],))] for n in teams}
        run_job("retention.apply")
        with psycopg.connect(OWNER_DB) as conn:
            gone = footprint(conn, ids["free"], chunk_ids["free"], concept_ids["free"])
            kept = footprint(conn, ids["held"], chunk_ids["held"], concept_ids["held"])
        assert not gone, f"the expired document left rows behind: {gone}"
        assert kept.get("documents") == 1 and kept.get("chunks") and kept.get("concepts"), f"the held document lost rows: {kept}"
        assert not fga.check("user:alice", "viewer", f"document:{keys['free']}"), "the expired document is still authorized"
        assert fga.check("user:alice", "viewer", f"document:{keys['held']}"), "the held document lost its authorization"
        assert api("POST", f"/v1/legal-holds/{hold}/release", "olivia").status_code == 200
        run_job("retention.apply")
        with psycopg.connect(OWNER_DB) as conn:
            after = footprint(conn, ids["held"], chunk_ids["held"], concept_ids["held"])
        assert not after, f"once released, the held document should have expired: {after}"
        runs = api("GET", "/v1/retention", "olivia").json()["runs"]
        assert runs and runs[0]["counts"].get("documents") == 1, f"the run record: {runs[:1]}"
        print(f"      the expired document left nothing in any of {len(kept)} tables; the held one stayed until its release")
    finally:
        for p in policies:
            api("DELETE", f"/v1/retention/policies/{p}", "olivia")
        with psycopg.connect(OWNER_DB) as conn:
            conn.execute("DELETE FROM kl.legal_holds WHERE workspace_id = ANY(%s)", (list(ws_ids.values()),))
            conn.execute("DELETE FROM kl.documents WHERE id = ANY(%s)", (list(ids.values()),))
            conn.execute("DELETE FROM kl.workspaces WHERE id = ANY(%s)", (list(ws_ids.values()),))
        for t in tuples:
            try:
                fga.write(deletes={t})
            except RuntimeError:
                pass  # already gone, as it should be


@check
def retention_policies_have_guardrails():
    """Retention: only org admins set policies and holds; the audit trail is kept at least 30 days; org-wide records take no team"""
    cases = (({"target": "audit_events", "keep_days": 7}, 422),
             ({"target": "audit_events", "keep_days": 90, "classification": "internal"}, 422),
             ({"target": "audit_events", "keep_days": 90, "workspace": "payments"}, 422),
             ({"target": "documents", "keep_days": 30, "workspace": "no-such-team"}, 404))
    for body, code in cases:
        r = api("PUT", "/v1/retention/policies", "olivia", json=body)
        assert r.status_code == code, f"{body}: {r.status_code}, expected {code}"
    assert api("POST", "/v1/legal-holds", "alice", json={"reason": "a team admin's hold"}).status_code == 403
    assert api("POST", "/v1/legal-holds", "olivia", json={"reason": "x"}).status_code == 422, "a hold needs a reason"
    assert api("GET", "/v1/retention", "bob").status_code == 403, "a member read the org's retention settings"


@check
def secrets_backend_keeps_and_shreds_keys():
    """Secrets: OpenBao stores to disk so keys survive restarts; Transit encrypts, and a destroyed key makes its ciphertext unreadable"""
    from .vault import vault
    assert vault.storage() == "file", f"OpenBao keeps everything in memory ({vault.storage()}): a restart would lose every key"
    name = f"kl-smoke-{secrets.token_hex(3)}"  # the services' token holds only keys named kl-... (G1)
    vault.transit_key(name)
    vault.transit_key(name)  # creating it again changes nothing
    sealed = vault.encrypt(name, "a stored original")
    assert sealed.startswith("vault:v1:") and "original" not in sealed, f"not ciphertext: {sealed[:40]}"
    assert vault.decrypt(name, sealed) == "a stored original"
    vault.destroy_key(name)
    vault.destroy_key(name)  # destroying it again is fine
    try:
        vault.decrypt(name, sealed)
    except (httpx.HTTPStatusError, ValueError):
        pass
    else:
        raise AssertionError("ciphertext was still readable after its key was destroyed")


@check
def the_services_openbao_token_holds_only_their_paths():
    """Secrets (G1): the services' OpenBao token is never root: it holds secrets under kl/ and the kl- encryption keys, and is refused another secret, a key that isn't Aerugo's, the policies, a new token, a new mount, and a new auth method; it lasts years, since nothing renews it"""
    h = {"X-Vault-Token": BAO_TOKEN}
    me = httpx.get(f"{BAO_URL}/v1/auth/token/lookup-self", headers=h, timeout=5).json()["data"]
    assert sorted(me["policies"]) == ["aerugo-services", "default"], f"the services' token holds {me['policies']}"
    assert me["ttl"] > 365 * 24 * 3600, f"the services' token expires in {me['ttl']}s, and nothing renews it"
    path = f"kl/acme/smoke-g1-{secrets.token_hex(3)}"
    r = httpx.post(f"{BAO_URL}/v1/secret/data/{path}", headers=h, json={"data": {"v": "1"}}, timeout=5)
    assert r.status_code == 200, f"the services' token couldn't write under kl/: {r.status_code}"
    try:
        refused = {"a secret outside kl/": ("POST", "secret/data/smoke-g1", {"data": {"v": "1"}}),
                   "a key that isn't Aerugo's": ("POST", "transit/keys/smoke-g1", {}),
                   "the policies": ("GET", "sys/policies/acl", None),
                   "a new token": ("POST", "auth/token/create", {}),
                   "a new mount": ("POST", "sys/mounts/smoke-g1", {"type": "kv"}),
                   "a new auth method": ("POST", "sys/auth/smoke-g1", {"type": "userpass"})}
        for what, (method, target, body) in refused.items():
            r = httpx.request(method, f"{BAO_URL}/v1/{target}", headers=h, json=body, timeout=5)
            assert r.status_code == 403, f"the services' token reached {what}: {r.status_code}"
    finally:
        httpx.delete(f"{BAO_URL}/v1/secret/metadata/{path}", headers=h, timeout=5)


@check
def stored_originals_are_ciphertext():
    """Per-team keys: every stored concept version and proposal draft is ciphertext under a team's or the org's key, and still reads back"""
    deadline, plain = time.monotonic() + 150, None
    while time.monotonic() < deadline:  # the worker encrypts older rows a batch a tick
        with psycopg.connect(OWNER_DB) as conn:
            plain = conn.execute("SELECT (SELECT count(*) FROM kl.concept_versions WHERE key_ref IS NULL) + "
                                 "(SELECT count(*) FROM kl.proposals WHERE key_ref IS NULL)").fetchone()[0]
        if plain == 0:
            break
        time.sleep(5)
    assert plain == 0, f"{plain} stored original(s) are still plaintext"
    with psycopg.connect(OWNER_DB) as conn:
        bad = conn.execute(
            "SELECT count(*) FROM (SELECT content, key_ref FROM kl.concept_versions "
            "UNION ALL SELECT content, key_ref FROM kl.proposals) x "
            "WHERE left(content, 9) <> 'vault:v1:' OR key_ref !~ '^kl-acme-(org|[0-9a-f-]{36})$'").fetchone()[0]
    assert bad == 0, f"{bad} stored original(s) aren't ciphertext under a team's or the org's key"
    r = api("GET", "/v1/concepts/pay-runbook", "alice")
    assert r.status_code == 200 and r.json()["okf"].startswith("---"), "an encrypted concept didn't read back as OKF"


# ------------------------------------------------------------------ backups (slice I4)
@check
def wal_is_archived_to_backups():
    """Backups: Postgres archives each finished write-ahead log segment to the encrypted pgBackRest repository"""
    with psycopg.connect(OWNER_DB, autocommit=True) as conn:
        assert conn.execute("SHOW archive_mode").fetchone()[0] == "on", "WAL archiving is off, so no point-in-time recovery"
        assert "pgbackrest" in conn.execute("SHOW archive_command").fetchone()[0], "WAL isn't archived to pgBackRest"
        before = conn.execute("SELECT archived_count FROM pg_stat_archiver").fetchone()[0]
        conn.execute("SELECT txid_current()")  # a committed transaction: fresh log to archive
        conn.execute("SELECT pg_switch_wal()")  # finish the current segment, so it's archived now
        deadline = time.monotonic() + 60
        while True:
            count, failed_at, archived_at = conn.execute(
                "SELECT archived_count, last_failed_time, last_archived_time FROM pg_stat_archiver").fetchone()
            if count > before or time.monotonic() > deadline:
                break
            time.sleep(1)
    assert count > before, "a finished WAL segment wasn't archived within a minute (see `docker compose logs postgres`)"
    assert failed_at is None or archived_at > failed_at, f"archiving failed at {failed_at} and hasn't succeeded since"


# ------------------------------------------------------------------ personal-data detection (slice I5)
@check
def personal_data_is_flagged_for_review():
    """Personal data: a résumé-like document is flagged for its team's admins with a suggested classification, holding counts, never values"""
    import uuid
    from .authz import fga
    org, tag = acme_org_id(), uuid.uuid4().hex[:8]
    ws, email, phone = f"pd-{tag}", f"jordan.{tag}@example.test", "(555) 010-0199"
    docs = {"cv": [(None, f"Jordan Example\n{email}\n{phone}"), ("Experience", "Data engineer at Acme Corp, 2021 to now."),
                   ("Education", "B.S., State University."), ("Skills", "Python, SQL, Airflow.")],
            "runbook": [(None, "Restarting the ledger: drain traffic, restart it, confirm its health check. "
                               "Questions go to ops@acme.test.")]}
    keys, ids = {name: f"{ws}-{name}" for name in docs}, {}
    with psycopg.connect(OWNER_DB) as conn:
        ws_id = conn.execute("INSERT INTO kl.workspaces (org_id, slug, name, discoverability) "
                             "VALUES (%s, %s, 'Personal data test', 'private') RETURNING id", (org, ws)).fetchone()[0]
        for name, sections in docs.items():
            ids[name] = conn.execute(
                "INSERT INTO kl.documents (org_id, source_kind, source_uri, source_version, external_key, title, content_hash, "
                "classification, acl_principals) VALUES (%s, 'seed', %s, 'v1', %s, %s, %s, 'internal', %s) RETURNING id",
                (org, f"https://kb.acme.test/{keys[name]}", keys[name], f"Personal data test {name}", keys[name],
                 ["user:alice"])).fetchone()[0]
            for ordinal, (heading, body) in enumerate(sections):
                conn.execute("INSERT INTO kl.chunks (org_id, document_id, ordinal, heading_path, body, acl_principals) "
                             "VALUES (%s, %s, %s, %s, %s, %s)", (org, ids[name], ordinal, heading, body, ["user:alice"]))
            conn.execute("INSERT INTO kl.workspace_documents (workspace_id, document_id, org_id) VALUES (%s, %s, %s)",
                         (ws_id, ids[name], org))
        conn.execute("INSERT INTO kl.jobs (org_id, kind) VALUES (%s, 'embeddings.backfill')", (org,))  # indexing scans them
    tuples = {("user:alice", "admin", f"workspace:{ws}"), ("user:alice", "member", f"workspace:{ws}")} | \
             {("user:alice", "viewer", f"document:{k}") for k in keys.values()}
    fga.write(writes=tuples)
    try:
        deadline, flags = time.monotonic() + 120, {}
        while time.monotonic() < deadline:
            with psycopg.connect(OWNER_DB) as conn:
                flags = {name: conn.execute("SELECT status, suggested, kinds, to_jsonb(f)::text FROM kl.privacy_flags f "
                                            "WHERE document_id = %s", (ids[name],)).fetchone() for name in docs}
            if all(flags.values()):
                break
            time.sleep(2)
        assert all(flags.values()), "the test documents were never scanned"
        status, suggested, kinds, raw = flags["cv"]
        assert status == "open" and suggested == "confidential" and {"email", "phone", "resume_layout"} <= set(kinds), \
            f"the résumé wasn't flagged: {flags['cv'][:3]}"
        assert email not in raw and "010-0199" not in raw, "the flag stored the personal data it found"
        assert flags["runbook"][0] == "clear" and flags["runbook"][1] is None, f"an ordinary runbook was flagged: {flags['runbook'][:3]}"
        assert api("GET", "/v1/personal-data", "bob").status_code == 403, "a member read the personal-data review"
        listed = {f["id"]: f for f in api("GET", "/v1/personal-data", "alice").json()["flags"]}
        assert keys["cv"] in listed and keys["runbook"] not in listed, f"the review list: {sorted(listed)}"
        assert email not in json.dumps(listed[keys["cv"]]), "the review list showed the personal data"
        r = api("POST", f"/v1/personal-data/{keys['cv']}/resolve", "alice", json={"action": "reclassify"})
        assert r.status_code == 200 and r.json()["classification"] == "confidential", f"reclassify: {r.status_code} {r.text[:200]}"
        with psycopg.connect(OWNER_DB) as conn:
            now = conn.execute("SELECT d.classification, d.classification_floor, f.status, kl.stricter('internal', d.classification_floor) "
                               "FROM kl.documents d JOIN kl.privacy_flags f ON f.document_id = d.id WHERE d.id = %s",
                               (ids["cv"],)).fetchone()
        assert now[:3] == ("confidential", "confidential", "reclassified"), f"after reclassifying: {now}"
        assert now[3] == "confidential", "a re-index declaring it internal would make it laxer again"
        r = api("POST", f"/v1/personal-data/{keys['runbook']}/resolve", "alice", json={"action": "dismiss"})
        assert r.status_code == 404, "a document with nothing to review had a flag to resolve"
    finally:
        with psycopg.connect(OWNER_DB) as conn:
            conn.execute("DELETE FROM kl.documents WHERE id = ANY(%s)", (list(ids.values()),))
            conn.execute("DELETE FROM kl.workspaces WHERE slug = %s", (ws,))
        for t in tuples:
            try:
                fga.write(deletes={t})
            except RuntimeError:
                pass


# ------------------------------------------------------------------ repos routed to their own teams (slice F1)
@check
def repos_route_to_their_own_teams():
    """Connections: an admin of both teams routes one repo to another team; its documents leave the old team, and no other team's copies do"""
    import uuid
    from psycopg.rows import dict_row
    from .authz import fga
    from .sync_github import leave_former_teams, team_for
    org, tag = acme_org_id(), uuid.uuid4().hex[:8]
    target, other, repo, name = f"rt-to-{tag}", f"rt-other-{tag}", f"acme/routed-{tag}", f"route-{tag}"
    with psycopg.connect(OWNER_DB) as conn:
        payments = conn.execute("SELECT id FROM kl.workspaces WHERE slug = 'payments'").fetchone()[0]
        ids = {slug: conn.execute("INSERT INTO kl.workspaces (org_id, slug, name, discoverability) "
                                  "VALUES (%s, %s, 'Route test', 'private') RETURNING id", (org, slug)).fetchone()[0]
               for slug in (target, other)}
        cid = conn.execute("INSERT INTO kl.connections (org_id, workspace_id, kind, name, config, credential_ref, status, created_by) "
                           "VALUES (%s, %s, 'github', %s, '{}', 'unused', 'disabled', 'smoke') RETURNING id",  # disabled: its syncs skip
                           (org, payments, name)).fetchone()[0]
        conn.execute("INSERT INTO kl.connection_containers (connection_id, org_id, external_id, container_kind) "
                     "VALUES (%s, %s, %s, 'repo')", (cid, org, repo))
        doc = conn.execute("INSERT INTO kl.documents (org_id, source_kind, source_uri, external_key, title, content_hash, "
                           "acl_principals, container_ref) VALUES (%s, 'github', %s, %s, 'Routed doc', 'h', %s, %s) RETURNING id",
                           (org, f"https://github.com/{repo}/blob/x/a.md", f"gh-routed-{tag}", ["user:alice"], repo)).fetchone()[0]
        for ws in (payments, ids[other]):  # synced into payments, and placed by another team's connection too
            conn.execute("INSERT INTO kl.workspace_documents (workspace_id, document_id, org_id) VALUES (%s, %s, %s)", (ws, doc, org))
    tuples = {("user:alice", "admin", f"workspace:{target}")}
    fga.write(writes=tuples)
    route = f"/v1/workspaces/payments/connections/{name}/routes"
    try:
        body = {"repository": repo, "workspace": target}
        assert api("PUT", route, "bob", json=body).status_code == 403, "someone who doesn't run the connection routed its repo"
        r = api("PUT", route, "alice", json={**body, "workspace": other})
        assert r.status_code == 403, "a repo went to a team its router doesn't administer"
        assert api("PUT", route, "alice", json={**body, "repository": "acme/not-here"}).status_code == 404
        r = api("PUT", route, "alice", json=body)
        assert r.status_code == 200 and r.json()["team"] == target, f"route: {r.status_code} {r.text[:200]}"
        listed = {c["external_id"]: c for c in api("GET", f"/v1/workspaces/payments/connections/{name}", "alice").json()["containers"]}
        assert listed[repo]["team"] == target, f"the connection lists the repo under {listed[repo]['team']}"
        with psycopg.connect(OWNER_DB, row_factory=dict_row) as conn:
            c = conn.execute("SELECT id, workspace_id, config FROM kl.connections WHERE id = %s", (cid,)).fetchone()
            assert str(team_for(c, repo)) == str(ids[target]), c["config"]
            conn.execute("SET search_path = kl, public")  # as the worker's connections have
            leave_former_teams(conn, c, repo, team_for(c, repo))  # what the next sync does, then it places the repo in its team
            conn.execute("INSERT INTO kl.workspace_documents (workspace_id, document_id, org_id) VALUES (%s, %s, %s)",
                         (ids[target], doc, org))
            placed = {r["slug"] for r in conn.execute("SELECT w.slug FROM kl.workspace_documents wd JOIN kl.workspaces w "
                                                      "ON w.id = wd.workspace_id WHERE wd.document_id = %s", (doc,))}
        assert placed == {target, other}, f"after the move it's in {sorted(placed)}: it should leave payments, and the other team keep its copy"
        r = api("PUT", route, "alice", json={"repository": repo, "workspace": None})
        assert r.status_code == 200 and r.json()["team"] == "payments", r.text[:200]
        with psycopg.connect(OWNER_DB, row_factory=dict_row) as conn:
            c = conn.execute("SELECT workspace_id, config FROM kl.connections WHERE id = %s", (cid,)).fetchone()
        assert team_for(c, repo) == c["workspace_id"] and str(ids[target]) in c["config"]["former_teams"][repo], c["config"]
    finally:
        with psycopg.connect(OWNER_DB) as conn:
            conn.execute("DELETE FROM kl.jobs WHERE payload->>'connection_id' = %s", (str(cid),))
            conn.execute("DELETE FROM kl.connections WHERE id = %s", (cid,))
            conn.execute("DELETE FROM kl.documents WHERE id = %s", (doc,))
            conn.execute("DELETE FROM kl.workspaces WHERE id = ANY(%s)", (list(ids.values()),))
        fga.write(deletes=tuples)


# ------------------------------------------------------------------ pipelines (slice J1)
FAKE_CI = "http://fake-llm:8080/oidc"  # a GitHub Actions-shaped token issuer, inside the fake model (smoke only)


@check
def pipelines_push_with_their_own_identity():
    """Pipelines: a registered repo's CI token pushes documents only their readers find; other repos, bad tokens, and readers off the team are refused; a pushed delete leaves nothing"""
    import uuid
    from .authz import fga
    from .pipelines import document_key
    tag = uuid.uuid4().hex[:8]
    name, repo = f"ci-{tag}", f"acme/handbook-{tag}"

    def mint(repository=repo, **claims):
        return httpx.post(f"{FAKE_CI}/token", json={"repository": repository, **claims}, timeout=10).json()["token"]

    def push(token, body):
        return httpx.post(f"{API}/v1/workspaces/payments/documents", json=body, timeout=30,
                          headers={"Authorization": f"Bearer {token}"})

    def applied(r):
        assert r.status_code == 202, f"push: {r.status_code} {r.text[:200]}"
        url, deadline = f"{API}/v1/workspaces/payments/documents/pushes/{r.json()['push']}", time.monotonic() + 120
        while time.monotonic() < deadline:
            state = httpx.get(url, headers={"Authorization": f"Bearer {mint()}"}, timeout=10).json()
            if state["status"] != "queued":
                assert state["status"] == "done", f"the push failed: {state}"
                return state["result"]
            time.sleep(1)
        raise AssertionError("the worker never applied the push")

    body = {"name": name, "issuer": FAKE_CI, "repository": repo}
    assert api("POST", "/v1/workspaces/payments/pipelines", "bob", json=body).status_code in (403, 404), "a non-admin registered a pipeline"
    r = api("POST", "/v1/workspaces/payments/pipelines", "alice", json={**body, "issuer": "https://evil.example"})
    assert r.status_code == 422, "a pipeline was registered for an issuer the platform doesn't trust"
    r = api("POST", "/v1/workspaces/payments/pipelines", "alice", json=body)
    assert r.status_code == 201, f"register: {r.status_code} {r.text[:200]}"
    pid = r.json()["id"]
    doc = {"key": "runbooks/rotate.md", "title": f"Rotating the CI signing keys {tag}", "classification": "internal",
           "uri": f"https://github.com/{repo}/blob/main/runbooks/rotate.md", "readers": ["user:alice"],
           "markdown": f"# Rotating the CI signing keys {tag}\n\nRotate the signing keys every ninety days."}
    try:
        assert push(mint("acme/another-repo"), {"upserts": [doc]}).status_code == 403, "another repository's token pushed"
        assert push(mint(audience="kl-api"), {"upserts": [doc]}).status_code == 401, "a token for another audience pushed"
        assert push(mint(ttl=-60), {"upserts": [doc]}).status_code == 401, "an expired token pushed"
        assert push("not-a-token", {"upserts": [doc]}).status_code == 401
        r = push(mint(), {"upserts": [{**doc, "readers": ["user:bob"]}]})
        assert r.status_code == 422, "a pipeline granted someone with no role on the team"
        r = push(mint(), {"upserts": [{**doc, "readers": ["org:acme"]}]})
        assert r.status_code == 422, "a pipeline published org-wide from a team that doesn't admit the whole org"
        assert applied(push(mint(), {"upserts": [doc]}))["upserted"] == 1
        key = document_key(pid, doc["key"])

        def finds(user):
            r = api("GET", "/v1/search", user, params={"q": tag, "scope": "org", "limit": 50})
            return key in {hit["id"] for hit in r.json()["results"]}
        assert finds("alice"), "the pushed document's reader didn't find it"
        assert not finds("carol") and not finds("bob"), "someone who isn't a reader found the pushed document"
        assert applied(push(mint(), {"upserts": [doc]}))["upserted"] == 1, "pushing the same document again should be harmless"
        with psycopg.connect(OWNER_DB) as conn:
            doc_id = conn.execute("SELECT id FROM kl.documents WHERE external_key = %s", (key,)).fetchone()[0]
            chunk_ids = [c for (c,) in conn.execute("SELECT id FROM kl.chunks WHERE document_id = %s", (doc_id,))]
            concept_ids = [c for (c,) in conn.execute("SELECT id FROM kl.concepts WHERE document_id = %s", (doc_id,))]
            kept = conn.execute("SELECT count(*) FROM kl.pipeline_pushes WHERE pipeline_id = %s AND payload <> '{}'::jsonb",
                                (pid,)).fetchone()[0]
        assert kept == 0, "a push's text was kept after it was applied"
        assert applied(push(mint(), {"deletes": [doc["key"]]}))["deleted"] == 1
        with psycopg.connect(OWNER_DB) as conn:
            left = footprint(conn, doc_id, chunk_ids, concept_ids)
        assert not left, f"the pushed delete left rows behind: {left}"
        assert not fga.check("user:alice", "viewer", f"document:{key}"), "the deleted document is still authorized"
    finally:
        api("DELETE", f"/v1/workspaces/payments/pipelines/{name}", "alice")


# ------------------------------------------------------------------ Google Drive (slice F2)
FAKE_GOOGLE = "http://fake-llm:8080/google"  # a fake Drive and OAuth token endpoint, inside the fake model (smoke only)
FAKE_JIRA = "http://fake-llm:8080/jira"  # a fake Jira Cloud, inside the fake model (smoke only)
FAKE_JIRA_B = "http://fake-llm:8080/jira-b"  # a second fake Jira site, whose keys may repeat the first's
FAKE_OKTA = "http://fake-llm:8080/okta"  # a fake Okta, inside the fake model (smoke only)
FAKE_GCP = "http://fake-llm:8080/gcp"  # a fake GCP Secret Manager, inside the fake model (smoke only)
FAKE_CONFLUENCE = "http://fake-llm:8080/confluence"  # a fake Confluence Cloud, inside the fake model (smoke only)
FAKE_MICROSOFT = "http://fake-llm:8080/microsoft"  # a fake Microsoft Graph and Entra token endpoint (smoke only)
FAKE_SLACK = "http://fake-llm:8080/slack"  # a fake Slack workspace (its Web API at /api), inside the fake model (smoke only)
FAKE_NOTION = "http://fake-llm:8080/notion"
FAKE_CATALOG = "http://fake-llm:8080"  # the fake catalogs (S7): /gcp-catalog, /databricks, /aws, /purview, /datahub  # a fake Notion (its API at /v1), inside the fake model (smoke only)


@check
def confluence_pages_carry_confluences_own_permissions():
    """S1, Confluence live: each page is readable by its space's readers narrowed by every restriction down the tree (a page under a restricted one inherits it; one nobody passes isn't indexed), named by its space and placed in its tree; a label narrows what's indexed; and an exported copy of a page the live connector reads is retired, so none is indexed twice"""
    import io
    import zipfile
    from .authz import fga
    tag = secrets.token_hex(3).upper()
    key, base = f"PAY{tag}", int(tag, 16) * 10
    ids = {n: str(base + i) for i, n in enumerate(("guide", "leads", "under", "nobody", "news"))}
    acct = {who: f"557058:cf{who}{tag.lower()}" for who in ("alice", "bob", "carol")}
    group = f"g-{tag.lower()}"
    page = lambda n, title, **more: {"id": ids[n], "title": f"{title} {tag}", "body": f"<p>{title} body {tag}</p>", "version": 1, **more}
    setup = {
        "users": {acct[w]: {"email": f"{w}@acme.test", "name": w.title()} for w in acct},
        "groups": {group: {"name": f"payments-{tag.lower()}", "members": [acct["bob"], acct["carol"]]}},
        "spaces": {key: {"id": str(base + 9), "name": f"Payments Engineering {tag}", "users": [acct["alice"]], "groups": [group],
                         "pages": [page("guide", "Guide", labels=["runbook"], attachments=[f"guide-notes-{tag}.txt"]),
                                   page("leads", "Leads", restricted_users=[acct["carol"]], attachments=[f"salaries-{tag}.xlsx"]),
                                   page("under", "Under", parentId=ids["leads"]),
                                   page("nobody", "Nobody", parentId=ids["leads"], restricted_users=[acct["alice"]])],
                         "blogposts": [page("news", "News")]}}}
    assert httpx.post(f"{FAKE_CONFLUENCE}/setup", json=setup, timeout=10).status_code == 200
    principal = lambda who: f"ext:atlassian.{acct[who].replace(':', '.')}"
    links = {(f"user:{who}", "owner", f"identity:{principal(who)[4:]}") for who in acct}
    with psycopg.connect(OWNER_DB, autocommit=True) as conn:
        org = conn.execute("SELECT id FROM kl.orgs WHERE slug = 'acme'").fetchone()[0]
        for who in acct:  # each has linked their Atlassian account
            conn.execute("INSERT INTO kl.principal_aliases (org_id, source, canonical, verified_by) VALUES (%s, %s, %s, 'smoke') "
                         "ON CONFLICT DO NOTHING", (org, principal(who), f"user:{who}"))
    fga.write(writes=links)
    name, iname = f"confluence-{tag.lower()}", f"cf-export-{tag.lower()}"
    body = {"name": name, "api_base": FAKE_CONFLUENCE, "email": "confluence-bot@acme.test", "api_token": "smoke-confluence-token",
            "spaces": [key], "every_minutes": 60}

    def finds(user, n):
        hits = api("GET", "/v1/search", user, params={"q": tag, "scope": "org", "limit": 50}).json()["results"]
        return f"confluence-{ids[n]}" in {h["id"] for h in hits}

    def exported_copies() -> int:
        with psycopg.connect(OWNER_DB) as conn:
            return conn.execute("SELECT count(*) FROM kl.documents WHERE container_ref LIKE 'import:%%' AND source_uri LIKE %s",
                                (f"%pageId={ids['guide']}",)).fetchone()[0]

    try:
        # an export of the same space, imported first: the live connector will take its page over
        export = io.BytesIO()
        with zipfile.ZipFile(export, "w") as z:
            z.writestr(f"{key}/index.html", "<html></html>")
            z.writestr(f"{key}/Guide-{tag}_{ids['guide']}.html", f"<html><head><title>Payments : Guide {tag}</title></head><body>"
                       f"<div id=\"main-content\"><p>The exported guide {tag}.</p></div></body></html>")
        r = api("POST", "/v1/workspaces/payments/imports", "alice", data={"name": iname, "readers": "group:payments",
                "base_url": "https://acme.atlassian.net/wiki"}, files={"file": ("export.zip", export.getvalue(), "application/zip")})
        assert r.status_code == 202, r.text[:200]
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and not exported_copies():
            time.sleep(2)
        assert exported_copies() == 1, "the export's copy of the page never arrived"

        refused = {"another team's secret": {**body, "email": None, "api_token": None,
                                             "credential_ref": "openbao://secret/kl/acme/finance/models/smoke-fake"},
                   "an internal address": {**body, "api_base": "http://10.0.0.5"},
                   "a malformed space key": {**body, "spaces": ["PAY KEY"]}}
        for what, wrong in refused.items():
            assert api("POST", "/v1/workspaces/payments/connections/confluence", "alice", json=wrong).status_code == 422, \
                f"{what} was accepted"
        r = api("POST", "/v1/workspaces/payments/connections/confluence", "alice", json=body)
        assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and not finds("alice", "guide"):
            time.sleep(3)
        assert finds("alice", "guide"), "the space's page never arrived"
        guide = api("GET", f"/v1/documents/confluence-{ids['guide']}/content", "alice").text
        assert f"guide-notes-{tag}" in guide and f"salaries-{tag}" not in guide, \
            "a page listed another page's attachments (one only its own readers may see)"
        assert finds("bob", "guide") and finds("carol", "guide"), "the space's group's members can't read its page"
        assert finds("carol", "leads") and not finds("alice", "leads") and not finds("bob", "leads"), "a restricted page leaked"
        assert finds("carol", "under") and not finds("alice", "under") and not finds("bob", "under"), \
            "a page under a restricted one didn't inherit its restriction"
        assert not any(finds(who, "nobody") for who in acct), "a page nobody passes every restriction of was indexed"
        assert finds("bob", "news"), "the space's blog post never arrived"
        under = api("GET", "/v1/documents", "carol", params={"scope": "org", "key": f"confluence-{ids['under']}"}).json()["documents"]
        assert under and under[0]["container"]["name"] == f"Payments Engineering {tag}", f"the page isn't named by its space: {under}"
        assert under[0]["path"] == f"Leads {tag} › Under {tag}", f"the page isn't placed in its tree: {under[0].get('path')}"
        assert exported_copies() == 0, "the export's copy of a page the live connector reads is still indexed: it's there twice"
        r = api("PATCH", f"/v1/workspaces/payments/connections/{name}", "alice", json={"labels": ["runbook"]})
        assert r.status_code == 200, r.text[:200]
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and finds("bob", "news"):
            time.sleep(3)
        assert not finds("bob", "news") and finds("bob", "guide"), "a label filter didn't narrow what's indexed"
        with psycopg.connect(OWNER_DB) as conn:
            seen = dict(conn.execute("SELECT external_id, email FROM kl.source_identities WHERE source = 'atlassian' "
                                     "AND external_id LIKE %s", (f"%cf%{tag.lower()}",)).fetchall())
        assert seen.get(principal("alice")[len("ext:atlassian."):]) == "alice@acme.test", f"Confluence's accounts weren't recorded: {seen}"
    finally:
        api("DELETE", f"/v1/workspaces/payments/connections/{name}", "alice")
        api("DELETE", f"/v1/workspaces/payments/imports/{iname}", "alice")
        fga.write(deletes=links)
        with psycopg.connect(OWNER_DB, autocommit=True) as conn:
            conn.execute("DELETE FROM kl.principal_aliases WHERE source LIKE %s", (f"ext:atlassian.%cf%{tag.lower()}",))
            conn.execute("DELETE FROM kl.source_identities WHERE source = 'atlassian' AND external_id LIKE %s", (f"%cf%{tag.lower()}",))


@check
def jira_issues_carry_jiras_own_permissions():
    """Jira (ADR 0011): synced by a service account, each issue is readable by exactly whom Jira lets see it: a project open to all of Jira reaches the org only on a site its admin confirmed is the company's; a restricted project, and an issue under a security level, reach only the people Jira names, once linked; the accounts Jira names are recorded for identity matching; another team's secret and an internal address are refused"""
    from .authz import fga
    tag = secrets.token_hex(3).upper()
    open_key, private_key = f"OPEN{tag}", f"PRIV{tag}"
    alice = {"accountId": f"557058:alice{tag.lower()}", "emailAddress": "Alice@acme.test", "displayName": "Alice"}
    carol = {"accountId": f"557058:carol{tag.lower()}", "emailAddress": "carol@acme.test", "displayName": "Carol"}
    setup = {"projects": {
        open_key: {"name": "Open", "company_wide": True, "browsers": [alice, carol], "issues": [
            {"key": f"{open_key}-1", "summary": f"Cutover plan {tag}", "description": f"Dual-run for 30 days {tag}.",
             "comments": [{"author": "Olivia", "body": f"Decided: Q3 {tag}."}]},
            {"key": f"{open_key}-2", "summary": f"Leads only {tag}", "description": "Restricted.",
             "security": {"name": "Leads"}, "viewers": [carol]}]},
        private_key: {"name": "Private", "company_wide": False, "browsers": [alice], "issues": [
            {"key": f"{private_key}-1", "summary": f"Private plan {tag}", "description": "Only the project's people."}]}}}
    assert httpx.post(f"{FAKE_JIRA}/setup", json=setup, timeout=10).status_code == 200
    name = f"jira-{tag.lower()}"
    principal = lambda acct: f"ext:atlassian.{acct['accountId'].replace(':', '.')}"
    links = {(f"user:{u}", "owner", f"identity:{principal(acct)[4:]}") for u, acct in (("alice", alice), ("carol", carol))}
    with psycopg.connect(OWNER_DB, autocommit=True) as conn:
        org = conn.execute("SELECT id FROM kl.orgs WHERE slug = 'acme'").fetchone()[0]
        for u, acct in (("alice", alice), ("carol", carol)):  # both have linked their Atlassian accounts
            conn.execute("INSERT INTO kl.principal_aliases (org_id, source, canonical, verified_by) VALUES (%s, %s, %s, 'smoke') "
                         "ON CONFLICT DO NOTHING", (org, principal(acct), f"user:{u}"))
    fga.write(writes=links)
    body = {"name": name, "api_base": FAKE_JIRA, "email": "jira-bot@acme.test", "api_token": "smoke-jira-token",
            "projects": [open_key, private_key], "company_site": True, "every_minutes": 60}

    def finds(user, key):
        hits = api("GET", "/v1/search", user, params={"q": tag, "scope": "org", "limit": 50}).json()["results"]
        return f"jira-{key}" in {h["id"] for h in hits}

    try:
        other_team = {**body, "email": None, "api_token": None, "credential_ref": "openbao://secret/kl/acme/finance/models/smoke-fake"}
        assert api("POST", "/v1/workspaces/payments/connections/jira", "alice", json=other_team).status_code == 422, \
            "a reference to another team's secret was accepted"
        assert api("POST", "/v1/workspaces/payments/connections/jira", "alice", json={**body, "api_base": "http://10.0.0.5"}).status_code == 422, \
            "an internal address was accepted"
        r = api("POST", "/v1/workspaces/payments/connections/jira", "alice", json=body)
        assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and not finds("alice", f"{open_key}-1"):
            time.sleep(3)
        assert finds("alice", f"{open_key}-1"), "the open project's issue never arrived"
        assert finds("bob", f"{open_key}-1"), "a project open to all of Jira, on a confirmed company site, didn't reach the org"
        assert finds("carol", f"{open_key}-2"), "the person Jira names can't find the issue under a security level"
        assert not finds("alice", f"{open_key}-2") and not finds("bob", f"{open_key}-2"), "an issue under a security level leaked"
        assert finds("alice", f"{private_key}-1"), "the restricted project's browser can't find its issue"
        assert not finds("bob", f"{private_key}-1") and not finds("carol", f"{private_key}-1"), "a restricted project leaked"
        listed = {c["name"]: c for c in api("GET", "/v1/workspaces/payments/connections", "alice").json()["connections"]}
        assert listed[name]["every_minutes"] == 60 and listed[name]["last_synced_at"], listed.get(name)
        named = api("GET", "/v1/documents", "alice", params={"scope": "org", "key": f"jira-{open_key}-1"}).json()["documents"]
        assert named and named[0]["container"]["name"] == "Open", f"the issue doesn't carry its project's name: {named}"
        with psycopg.connect(OWNER_DB) as conn:
            seen = dict(conn.execute("SELECT external_id, email FROM kl.source_identities WHERE source = 'atlassian' "
                                     "AND external_id LIKE %s", (f"%{tag.lower()}",)).fetchall())
        assert seen.get(principal(alice)[len("ext:atlassian."):]) == "alice@acme.test", f"Jira's accounts weren't recorded: {seen}"
    finally:
        api("DELETE", f"/v1/workspaces/payments/connections/{name}", "alice")
        fga.write(deletes=links)
        with psycopg.connect(OWNER_DB, autocommit=True) as conn:
            conn.execute("DELETE FROM kl.principal_aliases WHERE source LIKE %s", (f"ext:atlassian.%{tag.lower()}",))
            conn.execute("DELETE FROM kl.source_identities WHERE source = 'atlassian' AND external_id LIKE %s", (f"%{tag.lower()}",))


@check
def decisions_are_their_own_items_latest_first():
    """Q5, decisions: a Jira comment that records a decision, and an issue's resolution, become decisions with who made them and when; listed latest first, each says what it replaced; a comment that decides nothing isn't one; someone who can't read the issue gets none of them"""
    from .authz import fga
    tag = secrets.token_hex(3).upper()
    key = f"DEC{tag}"
    olivia = {"accountId": f"557058:olivia{tag.lower()}", "emailAddress": "olivia@acme.test", "displayName": "Olivia"}
    principal = f"ext:atlassian.{olivia['accountId'].replace(':', '.')}"
    link = {("user:alice", "owner", f"identity:{principal[4:]}")}
    setup = {"projects": {key: {"name": "Decisions", "company_wide": False, "browsers": [olivia], "issues": [
        {"key": f"{key}-1", "summary": f"Order history cutover {tag}", "description": "When we cut over.",
         "assignee": {"displayName": "Olivia"}, "resolution": {"name": "Done"}, "resolutiondate": "2025-07-01T09:00:00.000+0000",
         "comments": [{"author": "Olivia", "created": "2025-03-01T09:00:00.000+0000", "body": f"We decided to cut over in Q2 {tag}."},
                      {"author": "Bob", "created": "2025-04-01T09:00:00.000+0000", "body": f"Looks good to me {tag}."},
                      {"author": "Olivia", "created": "2025-06-01T09:00:00.000+0000", "body": f"Decision: we moved the cutover to Q3 {tag}."}]}]}}}
    name = f"decisions-{tag.lower()}"
    with psycopg.connect(OWNER_DB, autocommit=True) as conn:
        conn.execute("INSERT INTO kl.principal_aliases (org_id, source, canonical, verified_by) VALUES (%s, %s, 'user:alice', 'smoke') "
                     "ON CONFLICT DO NOTHING", (acme_org_id(), principal))
    fga.write(writes=link)
    try:
        assert httpx.post(f"{FAKE_JIRA}/setup", json=setup, timeout=10).status_code == 200
        r = api("POST", "/v1/workspaces/payments/connections/jira", "alice", json={
            "name": name, "api_base": FAKE_JIRA, "email": "jira-bot@acme.test", "api_token": "smoke-jira-token", "projects": [key], "every_minutes": 10080})
        assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
        deadline = time.monotonic() + 120
        listed = []
        while time.monotonic() < deadline and len(listed) < 3:
            listed = api("GET", "/v1/decisions", "alice", params={"q": tag}).json()["decisions"]
            time.sleep(2)
        assert [(d["at"], d["by"]) for d in listed] == [("2025-07-01", "Olivia"), ("2025-06-01", "Olivia"), ("2025-03-01", "Olivia")], \
            f"the decisions weren't listed latest first, with who and when: {listed}"
        assert "Q3" in listed[1]["text"] and "Q2" in listed[1]["replaces"]["text"], f"a decision didn't say what it replaced: {listed[1]}"
        assert not any("Looks good" in d["text"] for d in listed), "a comment that decides nothing was listed as a decision"
        assert listed[0]["document"]["id"] == f"jira-{key}-1", listed[0]
        assert api("GET", "/v1/decisions", "bob", params={"q": tag}).json()["decisions"] == [], "someone who can't read the issue saw its decisions"
    finally:
        api("DELETE", f"/v1/workspaces/payments/connections/{name}", "alice")
        fga.write(deletes=link)
        with psycopg.connect(OWNER_DB, autocommit=True) as conn:
            conn.execute("DELETE FROM kl.principal_aliases WHERE source = %s", (principal,))
            conn.execute("DELETE FROM kl.source_identities WHERE source = 'atlassian' AND external_id LIKE %s", (f"%{tag.lower()}",))


@check
def a_connection_never_touches_another_connections_documents():
    """A connection's documents are its own: another team's connection to another Jira site with the same project key and the same issue keys deletes none of the first connection's issues, overwrites neither their content nor their readers, and doesn't rename their project; each team keeps its own issues and readers, and removing the second connection leaves the first's alone"""
    from .authz import fga
    tag = secrets.token_hex(3).upper()
    key = f"EDP{tag}"  # one project key on both sites, as two companies' Jira sites can share one
    alice = {"accountId": f"557058:alice{tag.lower()}", "emailAddress": "alice@acme.test", "displayName": "Alice"}
    bob = {"accountId": f"557058:bob{tag.lower()}", "emailAddress": "bob@acme.test", "displayName": "Bob"}
    issue = lambda n, summary: {"key": f"{key}-{n}", "summary": f"{summary} {tag}", "description": f"{summary}, in detail {tag}."}
    sites = {FAKE_JIRA: {key: {"name": f"Orders {tag}", "browsers": [alice], "issues": [issue(1, "Cutover plan"), issue(2, "Rollback plan")]}},
             FAKE_JIRA_B: {key: {"name": f"Elsewhere {tag}", "browsers": [bob], "issues": [issue(1, "Stranger plan"), issue(3, "Stranger extra")]}}}
    for site, projects in sites.items():  # issue 1 is on both sites, with the same updated time (the version)
        assert httpx.post(f"{site}/setup", json={"projects": projects}, timeout=10).status_code == 200
    principal = lambda acct: f"ext:atlassian.{acct['accountId'].replace(':', '.')}"
    links = {(f"user:{u}", "owner", f"identity:{principal(acct)[4:]}") for u, acct in (("alice", alice), ("bob", bob))}
    stranger_admin = ("user:bob", "admin", "workspace:finance")  # no seed user administers finance
    with psycopg.connect(OWNER_DB, autocommit=True) as conn:
        org = conn.execute("SELECT id FROM kl.orgs WHERE slug = 'acme'").fetchone()[0]
        for u, acct in (("alice", alice), ("bob", bob)):  # each has linked their account on their own site
            conn.execute("INSERT INTO kl.principal_aliases (org_id, source, canonical, verified_by) VALUES (%s, %s, %s, 'smoke') "
                         "ON CONFLICT DO NOTHING", (org, principal(acct), f"user:{u}"))
    fga.write(writes=links | {stranger_admin})
    first, second = f"jira-{tag.lower()}-a", f"jira-{tag.lower()}-b"
    body = {"api_base": FAKE_JIRA, "email": "jira-bot@acme.test", "api_token": "smoke-jira-token", "projects": [key], "every_minutes": 60}

    def finds(user, issue_key):
        hits = api("GET", "/v1/search", user, params={"q": tag, "scope": "org", "limit": 50}).json()["results"]
        return f"jira-{issue_key}" in {h["id"] for h in hits}

    def until(found, seconds=120):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not found():
            time.sleep(3)
        return found()

    def project_name(user, issue_key):
        docs = api("GET", "/v1/documents", user, params={"scope": "org", "key": f"jira-{issue_key}"}).json()["documents"]
        return docs[0]["container"]["name"] if docs else None

    try:
        r = api("POST", "/v1/workspaces/payments/connections/jira", "alice", json={**body, "name": first})
        assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
        assert until(lambda: finds("alice", f"{key}-1") and finds("alice", f"{key}-2")), "payments' issues never arrived"
        r = api("POST", "/v1/workspaces/finance/connections/jira", "bob", json={**body, "name": second, "api_base": FAKE_JIRA_B})
        assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
        assert until(lambda: finds("bob", f"{key}-3")), "the second site's own issue never arrived"
        touched = {"payments' rollback issue was deleted": not finds("alice", f"{key}-2"),
                   "alice lost payments' cutover issue": not finds("alice", f"{key}-1"),
                   "bob reads payments' cutover issue": finds("bob", f"{key}-1")}
        assert not any(touched.values()), f"the other site's connection touched payments' documents: {[t for t, v in touched.items() if v]}"
        with psycopg.connect(OWNER_DB) as conn:
            held = conn.execute(
                """SELECT d.source_uri, array_agg(w.slug ORDER BY w.slug) FROM kl.documents d
                   JOIN kl.workspace_documents wd ON wd.document_id = d.id JOIN kl.workspaces w ON w.id = wd.workspace_id
                   WHERE d.external_key = %s GROUP BY d.source_uri""", (f"jira-{key}-1",)).fetchall()
        assert held == [(f"{FAKE_JIRA}/browse/{key}-1", ["payments"])], f"payments' issue changed hands: {held}"
        assert not finds("alice", f"{key}-3"), "the second site's issue leaked to the first's reader"
        assert project_name("alice", f"{key}-1") == f"Orders {tag}", "payments' issue is named by the other site's project"
        assert project_name("bob", f"{key}-3") == f"Elsewhere {tag}", "the second site's issue isn't named by its own project"
        assert api("DELETE", f"/v1/workspaces/finance/connections/{second}", "bob").status_code == 202
        assert until(lambda: not finds("bob", f"{key}-3")), "the removed connection's issue stayed"
        assert finds("alice", f"{key}-1") and finds("alice", f"{key}-2"), "removing the other connection took payments' issues"
    finally:
        api("DELETE", f"/v1/workspaces/finance/connections/{second}", "bob")
        api("DELETE", f"/v1/workspaces/payments/connections/{first}", "alice")
        fga.write(deletes=links | {stranger_admin})
        with psycopg.connect(OWNER_DB, autocommit=True) as conn:
            conn.execute("DELETE FROM kl.principal_aliases WHERE source LIKE %s", (f"ext:atlassian.%{tag.lower()}",))
            conn.execute("DELETE FROM kl.source_identities WHERE source = 'atlassian' AND external_id LIKE %s", (f"%{tag.lower()}",))


@check
def slack_messages_carry_each_channels_own_readers():
    """S4, Slack: a bot reads the channels it's in; a private channel's messages reach its members, a public channel's the whole org on a workspace confirmed as the company's, a channel shared with another organization only this workspace's members; a thread is one document; an edit, a deletion, a member removed, and the bot leaving a channel all carry over on the next sync; a rate limit is waited out; a user token, an internal address, a malformed channel, and another team's secret are refused"""
    from .authz import fga
    tag = secrets.token_hex(3).upper()
    uid = {u: f"U{u.upper()}{tag}" for u in ("alice", "bob", "carol", "dave")}
    person = lambda u, **more: {"id": uid[u], "team_id": "T0SMOKE", "name": u, "profile": {"real_name": u.title(), "email": f"{u}@acme.test"}, **more}
    ts = lambda n: f"17600{n:05d}.000{n:03d}"
    private, public, shared, gone = (f"C{x}{tag}" for x in ("PRIV", "PUB", "CONN", "BYE"))
    channels = lambda: {
        private: {"name": f"pay-{tag.lower()}", "private": True, "members": [uid["alice"], uid["carol"], f"UOTHER{tag}"], "messages": [
            {"ts": ts(1), "user": uid["alice"], "text": f"Cutover rehearsal {tag} on Friday"},
            {"ts": ts(2), "user": uid["carol"], "text": f"Rollback owner {tag}?", "thread_ts": ts(2), "reply_count": 1,
             "replies": [{"ts": ts(3), "user": uid["alice"], "text": f"Carol owns rollback {tag}", "thread_ts": ts(2)}]}]},
        public: {"name": f"general-{tag.lower()}", "members": [uid["alice"], uid["bob"]], "messages": [
            {"ts": ts(10), "user": uid["bob"], "text": f"Office move {tag} next week"}]},
        shared: {"name": f"connect-{tag.lower()}", "shared": True, "members": [uid["bob"], f"UOTHER{tag}"], "messages": [
            {"ts": ts(20), "user": uid["bob"], "text": f"Vendor contract {tag} draft"}]},
        gone: {"name": f"bye-{tag.lower()}", "bot_member": False, "members": [uid["dave"]], "messages": [
            {"ts": ts(30), "user": uid["dave"], "text": f"Never indexed {tag}"}]}}
    state = {"users": [person(u) for u in uid], "channels": channels(), "throttle": True}
    principal = lambda u: f"ext:slack.{uid[u]}"
    links = {(f"user:{u}", "owner", f"identity:{principal(u)[4:]}") for u in uid}
    name = f"slack-{tag.lower()}"
    path = f"/v1/workspaces/payments/connections/{name}"
    body = {"name": name, "api_base": f"{FAKE_SLACK}/api", "bot_token": "xoxb-smoke-slack-token", "company_workspace": True,
            "history_days": 3650, "every_minutes": 60}  # its messages are a year old: a window of ten years reads them

    def hits(u):
        return {h["id"]: h for h in api("GET", "/v1/search", u, params={"q": tag, "scope": "org", "limit": 50}).json()["results"]}

    day = lambda channel: f"slack-{channel}-2025-10-09"
    thread = f"slack-{private}-{ts(2)}"

    def synced_after(moment):
        got = api("GET", path, "alice").json()
        return got.get("last_synced_at") and datetime.fromisoformat(got["last_synced_at"]) > moment

    def resync(new_state):
        assert httpx.post(f"{FAKE_SLACK}/setup", json=new_state, timeout=10).status_code == 200
        moment = datetime.fromisoformat(api("GET", path, "alice").json()["last_synced_at"])
        assert api("POST", path + "/refresh", "alice").status_code == 202
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and not synced_after(moment):
            time.sleep(2)
        assert synced_after(moment), "the Slack connection didn't sync again"

    with psycopg.connect(OWNER_DB, autocommit=True) as conn:
        org = conn.execute("SELECT id FROM kl.orgs WHERE slug = 'acme'").fetchone()[0]
        for u in uid:  # each has linked their Slack account
            conn.execute("INSERT INTO kl.principal_aliases (org_id, source, canonical, verified_by) VALUES (%s, %s, %s, 'smoke') "
                         "ON CONFLICT DO NOTHING", (org, principal(u), f"user:{u}"))
    fga.write(writes=links)
    try:
        assert httpx.post(f"{FAKE_SLACK}/setup", json=state, timeout=10).status_code == 200
        refusals = {"a user token was accepted": {**body, "bot_token": "xoxp-a-person"},
                    "an internal address was accepted": {**body, "api_base": "http://10.0.0.5"},
                    "a malformed channel was accepted": {**body, "channels": ["general"]},
                    "a reference to another team's secret was accepted": {**body, "bot_token": None,
                                                                         "credential_ref": "openbao://secret/kl/acme/finance/models/smoke-fake"}}
        for problem, attempt in refusals.items():
            assert api("POST", "/v1/workspaces/payments/connections/slack", "alice", json=attempt).status_code == 422, problem
        r = api("POST", "/v1/workspaces/payments/connections/slack", "alice", json=body)
        assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and day(private) not in hits("alice"):
            time.sleep(3)
        alice, bob, carol, dave = (hits(u) for u in ("alice", "bob", "carol", "dave"))
        assert day(private) in alice and thread in carol and day(private) not in bob and thread not in dave, \
            "a private channel's messages didn't reach exactly its members"
        assert day(public) in dave and day(public) in carol, "a public channel on the company's own workspace didn't reach the org"
        assert day(shared) in bob and day(shared) not in alice and day(shared) not in dave, \
            "a channel shared with another organization reached people outside it"
        assert not any(k.startswith(f"slack-{gone}") for k in alice | dave), "a channel the bot isn't in was indexed"
        assert alice[day(private)]["source_kind"] == "slack"
        doc = api("GET", f"/v1/documents/{thread}/content", "alice").json()
        assert f"Carol owns rollback {tag}" in json.dumps(doc), "a thread's reply isn't in its document"
        named = api("GET", "/v1/documents", "alice", params={"scope": "org", "key": thread}).json()["documents"]
        assert named and named[0]["container"]["name"] == f"#pay-{tag.lower()}", f"the thread isn't named by its channel: {named}"

        edited = channels()
        edited[private]["messages"][0] = {"ts": ts(1), "user": uid["alice"], "text": f"Cutover rehearsal moved {tag} to Monday",
                                          "edited": {"ts": ts(4)}}
        edited[private]["members"] = [uid["alice"], f"UOTHER{tag}"]  # carol leaves the private channel
        edited[shared]["messages"] = []  # the vendor message is deleted
        edited[public]["bot_member"] = False  # the bot leaves #general
        resync({**state, "channels": edited, "throttle": False})
        content = json.dumps(api("GET", f"/v1/documents/{day(private)}/content", "alice").json())
        assert "moved" in content and "on Friday" not in content, "an edit didn't carry over"
        assert thread not in hits("carol") and day(private) not in hits("carol"), "someone removed from a channel still finds it"
        assert day(shared) not in hits("bob"), "a deleted message stayed indexed"
        assert day(public) not in hits("dave"), "a channel the bot left stayed indexed"
    finally:
        api("DELETE", path, "alice")
        fga.write(deletes=links)
        with psycopg.connect(OWNER_DB, autocommit=True) as conn:
            conn.execute("DELETE FROM kl.principal_aliases WHERE source = ANY(%s)", ([principal(u) for u in uid],))
            conn.execute("DELETE FROM kl.source_identities WHERE source = 'slack' AND external_id LIKE %s", (f"%{tag}",))


@check
def microsoft_365_files_carry_their_own_sharing():
    """S5, Microsoft 365: an app granted site by site (Sites.Selected) reads those sites' libraries and nothing else; each file reaches the people it's shared with, an Entra group's members through the Entra directory, and a sharing link's named people, while an organization link and a SharePoint site group reach no one; a download redirected anywhere but SharePoint is refused; a deleted file, and a member taken out of the group, carry over on the next sync; an internal address, a malformed site, a junk certificate, and another team's secret are refused"""
    from .authz import fga
    tag = secrets.token_hex(3)
    tenant = str(uuid.uuid4())
    directory_app = httpx.post(f"{FAKE_MICROSOFT}/app", json={"tenant_id": tenant}, timeout=30).json()
    app = httpx.post(f"{FAKE_MICROSOFT}/app", json={"tenant_id": tenant}, timeout=30).json()
    someone_else = httpx.post(f"{FAKE_MICROSOFT}/app", json={"tenant_id": tenant}, timeout=30).json()
    people = ("alice", "bob", "carol", "dave")
    ids = {u: str(uuid.uuid4()) for u in people}
    principal = lambda u: f"ext:microsoft.{u}@acme.test"
    user = lambda u: {"id": ids[u], "mail": f"{u}@acme.test", "userPrincipalName": f"{u}@acme.test", "displayName": u.title(), "accountEnabled": True}
    team = str(uuid.uuid4())
    group_of = lambda *members: {team: {"mail": f"team-{tag}@acme.test", "name": "Team", "members": [{"user": ids[m]} for m in members]}}
    site, hidden = (f"acme.sharepoint.com,{uuid.uuid4()},{uuid.uuid4()}" for _ in range(2))
    library = f"b!LIB{tag}{secrets.token_hex(6)}"
    shared_with = lambda u: {"roles": ["read"], "grantedToV2": {"user": {"id": ids[u], "displayName": u.title(), "email": f"{u}@acme.test"}}}
    item = lambda i, name, perms, **more: {"id": f"I{i}{tag}", "name": f"{name} {tag}.txt", "file": {"mimeType": "text/plain"}, "size": 64,
                                         "cTag": f"c{i}", "parentReference": {"id": "root"}, "text": f"{name} {tag}: text.",
                                         "permissions": perms, **more}
    files = [{"id": f"F{tag}", "name": "Sub", "folder": {"childCount": 1}, "parentReference": {"id": "root"}},
             item(1, "Plan", [shared_with("carol")], parentReference={"id": f"F{tag}"}, webUrl="https://acme.sharepoint.com/sites/pay/plan.txt"),
             item(2, "Group", [{"roles": ["read"], "grantedToV2": {"group": {"id": team, "displayName": "Team"}}}]),
             item(3, "Linked", [{"roles": ["read"], "link": {"scope": "organization", "type": "view"}}]),
             item(4, "Members", [{"roles": ["read"], "grantedToV2": {"siteGroup": {"id": "3", "displayName": "Payments Members"}}}]),
             item(5, "Diverted", [shared_with("dave")], bad_download=True),
             item(6, "Named", [{"roles": ["read"], "link": {"scope": "users", "type": "view"},
                               "grantedToIdentitiesV2": [{"siteUser": {"loginName": "i:0#.f|membership|bob@acme.test"}}]}])]
    sites = lambda listed: {site: {"name": "Payments", "granted": [app["client_id"]], "drives": {library: {"name": "Documents", "items": listed}}},
                            hidden: {"name": "Secret", "granted": [someone_else["client_id"]],
                                     "drives": {f"b!HID{tag}{secrets.token_hex(6)}": {"name": "Docs", "items": [item(9, "Hidden", [shared_with("alice")])]}}}}
    key = lambda i: f"m365-{hashlib.sha256(library.encode()).hexdigest()[:10]}-I{i}{tag}"
    dname, name = f"entra-{tag}", f"m365-{tag}"
    dpath, path = f"/v1/workspaces/payments/connections/{dname}", f"/v1/workspaces/payments/connections/{name}"
    body = {"name": name, "api_base": FAKE_MICROSOFT, "tenant_id": tenant, "client_id": app["client_id"], "certificate": app["certificate"],
            "sites": [site, hidden], "every_minutes": 60}
    links = {(f"user:{u}", "owner", f"identity:{principal(u)[4:]}") for u in people}

    def hits(u):
        return {h["id"] for h in api("GET", "/v1/search", u, params={"q": tag, "scope": "org", "limit": 50}).json()["results"]}

    def until(test, seconds=150):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not test():
            time.sleep(3)
        return test()

    with psycopg.connect(OWNER_DB, autocommit=True) as conn:
        org = conn.execute("SELECT id FROM kl.orgs WHERE slug = 'acme'").fetchone()[0]
        for u in people:  # each has linked their Microsoft account
            conn.execute("INSERT INTO kl.principal_aliases (org_id, source, canonical, verified_by) VALUES (%s, %s, %s, 'smoke') "
                         "ON CONFLICT DO NOTHING", (org, principal(u), f"user:{u}"))
    fga.write(writes=links)
    try:
        assert httpx.post(f"{FAKE_MICROSOFT}/setup", json={"users": [user(u) for u in people], "groups": group_of("bob", "carol")},
                          timeout=10).status_code == 200
        assert httpx.post(f"{FAKE_MICROSOFT}/sites", json={"sites": sites(files)}, timeout=10).status_code == 200
        r = api("POST", "/v1/workspaces/payments/connections/directory", "olivia", json={
            "name": dname, "provider": "entra", "api_base": FAKE_MICROSOFT, "tenant_id": tenant, "client_id": directory_app["client_id"],
            "certificate": directory_app["certificate"], "every_minutes": 60})
        assert r.status_code == 201, f"the Entra directory: {r.status_code} {r.text[:200]}"
        refusals = {"an internal address was accepted": {**body, "api_base": "http://10.0.0.5"},
                    "a malformed site was accepted": {**body, "sites": ["https://acme.sharepoint.com/sites/pay"]},
                    "a junk certificate was accepted": {**body, "certificate": "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----"},
                    "a reference to another team's secret was accepted": {**body, "certificate": None,
                                                                         "credential_ref": "openbao://secret/kl/acme/finance/models/smoke-fake"}}
        for problem, attempt in refusals.items():
            assert api("POST", "/v1/workspaces/payments/connections/microsoft", "alice", json=attempt).status_code == 422, problem
        r = api("POST", "/v1/workspaces/payments/connections/microsoft", "alice", json=body)
        assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
        assert until(lambda: key(2) in hits("bob") and key(1) in hits("carol")), "Microsoft 365's files never arrived"
        alice, bob, carol, dave = (hits(u) for u in people)
        assert key(1) in carol and key(1) not in bob and key(1) not in dave, "a file shared with one person reached others"
        assert key(2) in bob and key(2) in carol and key(2) not in alice and key(2) not in dave, \
            "a file shared with an Entra group didn't reach exactly its members"
        assert all(key(3) not in h and key(4) not in h for h in (alice, bob, carol, dave)), \
            "an organization link or a SharePoint site group granted a file"
        assert key(5) not in dave, "a download redirected away from SharePoint was followed"
        assert key(6) in bob and key(6) not in alice, "a sharing link's named person didn't get the file, or someone else did"
        assert not any("I9" in k for h in (alice, bob, carol, dave) for k in h), "a site the app wasn't granted was read"
        doc = api("GET", "/v1/documents", "carol", params={"scope": "org", "key": key(1)}).json()["documents"]
        assert doc and doc[0]["path"] == "Sub" and doc[0]["container"]["name"] == "Payments / Documents", f"the file isn't placed in its library: {doc}"

        assert httpx.post(f"{FAKE_MICROSOFT}/sites", json={"sites": sites([f for f in files if f["id"] != f"I1{tag}"])}, timeout=10).status_code == 200
        assert httpx.post(f"{FAKE_MICROSOFT}/setup", json={"users": [user(u) for u in people], "groups": group_of("carol")},
                          timeout=10).status_code == 200
        assert api("POST", dpath + "/refresh", "olivia").status_code == 202  # the directory sees bob leave: Microsoft 365 is re-read
        assert until(lambda: key(2) not in hits("bob")), "someone taken out of the group still finds its file"
        assert until(lambda: key(1) not in hits("carol")), "a deleted file stayed indexed"
        assert key(2) in hits("carol"), "a member still in the group lost its file"
    finally:
        api("DELETE", path, "alice")
        api("DELETE", dpath, "olivia")
        fga.write(deletes=links)
        with psycopg.connect(OWNER_DB, autocommit=True) as conn:
            conn.execute("DELETE FROM kl.principal_aliases WHERE source = ANY(%s)", ([principal(u) for u in people],))


@check
def a_paused_connection_stays_paused_when_its_jobs_finish():
    """A pause that lands while a connection's discovery or sync is running stays: the job's end records its outcome (ready, or the error) without un-pausing it, and the API still says it's paused; and a source that signs nothing can't take a webhook (S8)"""
    from .worker import CONNECTION_FAILED_SQL, CONNECTION_READY_SQL
    name = f"paused-{secrets.token_hex(3)}"
    path = f"/v1/workspaces/payments/connections/{name}"
    r = api("POST", "/v1/workspaces/payments/connections/notion", "alice",
            json={"name": name, "api_base": f"{FAKE_NOTION}/v1", "token": "ntn_smoke_notion_integration_token", "every_minutes": 60})
    assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
    try:
        assert api("PATCH", path, "alice", json={"enabled": False}).status_code == 200
        with psycopg.connect(OWNER_DB, autocommit=True) as conn:
            conn.execute("SET search_path = kl")
            conn.execute(CONNECTION_READY_SQL, (r.json()["id"],))  # a discovery that read the connection before the pause ends
            assert conn.execute("SELECT status FROM connections WHERE id = %s", (r.json()["id"],)).fetchone()[0] == "disabled", \
                "a discovery's end un-paused the connection"
            conn.execute(CONNECTION_FAILED_SQL, ("the source refused", r.json()["id"]))  # a sync that fails after the pause
            status, error = conn.execute("SELECT status, last_error FROM connections WHERE id = %s", (r.json()["id"],)).fetchone()
            assert status == "disabled" and error == "the source refused", f"a failed job un-paused it, or lost its error: {status} {error}"
        assert api("GET", path, "alice").json()["enabled"] is False, "the API says a paused connection is running"
        assert api("POST", path + "/webhook", "alice", json={}).status_code == 422, "a source that signs nothing (Notion) took a webhook"
    finally:
        api("DELETE", path, "alice")


@check
def a_revoked_jira_grant_leaves_on_a_signed_webhook_and_on_the_refresh():
    """S8, faster permission changes: a signed Jira webhook queues its connection's readers refresh at once, which takes a revoked reader's issue away without re-reading any content; the same delivery again queues nothing; an unsigned, wrongly signed, or unknown connection's delivery is the same 401; between content syncs, the readers refresh on its own cadence brings a re-granted reader back; a removed webhook takes nothing"""
    import hashlib
    import hmac
    from .authz import fga
    tag = secrets.token_hex(3).upper()
    key = f"HOOK{tag}"
    alice = {"accountId": f"557058:alice{tag.lower()}", "emailAddress": "alice@acme.test", "displayName": "Alice"}
    carol = {"accountId": f"557058:carol{tag.lower()}", "emailAddress": "carol@acme.test", "displayName": "Carol"}
    issue = lambda text: {"key": f"{key}-1", "summary": f"Cutover plan {tag}", "description": text}
    setup = lambda browsers, text: {"projects": {key: {"name": "Hooks", "company_wide": False, "browsers": browsers, "issues": [issue(text)]}}}
    principal = lambda acct: f"ext:atlassian.{acct['accountId'].replace(':', '.')}"
    links = {(f"user:{u}", "owner", f"identity:{principal(acct)[4:]}") for u, acct in (("alice", alice), ("carol", carol))}
    name = f"hooks-{tag.lower()}"
    path = f"/v1/workspaces/payments/connections/{name}"
    body = {"name": name, "api_base": FAKE_JIRA, "email": "jira-bot@acme.test", "api_token": "smoke-jira-token", "projects": [key],
            "every_minutes": 10080}  # a week: no content sync runs again during the check

    def finds(user):
        return f"jira-{key}-1" in {h["id"] for h in api("GET", "/v1/search", user, params={"q": tag, "scope": "org", "limit": 50}).json()["results"]}

    def until(test, seconds=150):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not test():
            time.sleep(2)
        return test()

    def deliver(hook, secret, delivery, payload=b'{"webhookEvent": "project_updated"}'):
        signature = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest() if secret else ""
        return httpx.post(f"{API}{hook}", content=payload, timeout=10, headers={
            "Content-Type": "application/json", "X-Hub-Signature": signature, "X-Atlassian-Webhook-Identifier": delivery})

    with psycopg.connect(OWNER_DB, autocommit=True) as conn:
        org = conn.execute("SELECT id FROM kl.orgs WHERE slug = 'acme'").fetchone()[0]
        for u, acct in (("alice", alice), ("carol", carol)):
            conn.execute("INSERT INTO kl.principal_aliases (org_id, source, canonical, verified_by) VALUES (%s, %s, %s, 'smoke') "
                         "ON CONFLICT DO NOTHING", (org, principal(acct), f"user:{u}"))
    fga.write(writes=links)
    try:
        assert httpx.post(f"{FAKE_JIRA}/setup", json=setup([alice, carol], f"Dual-run for 30 days {tag}."), timeout=10).status_code == 200
        r = api("POST", "/v1/workspaces/payments/connections/jira", "alice", json=body)
        assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
        cid = r.json()["id"]
        assert until(lambda: finds("carol")), "the issue never arrived"
        made = api("POST", path + "/webhook", "alice", json={})
        assert made.status_code == 201 and made.json()["secret"], f"{made.status_code} {made.text[:200]}"
        hook, secret = "/v1/hooks/jira/" + made.json()["url"].rsplit("/v1/hooks/jira/", 1)[1], made.json()["secret"]
        assert hook == f"/v1/hooks/jira/{cid}" and api("GET", path, "alice").json()["webhook"] is True, "the hook isn't the connection's own"
        assert api("POST", path + "/webhook", "alice", json={"signing_secret": "x" * 20}).status_code == 422, "Jira took a Slack secret"
        refusals = {"an unsigned delivery": deliver(hook, None, f"d0-{tag}"),
                    "a wrongly signed delivery": deliver(hook, "not-the-secret", f"d0-{tag}"),
                    "a delivery for an unknown connection": deliver(f"/v1/hooks/jira/{uuid.uuid4()}", secret, f"d0-{tag}"),
                    "a Jira connection's delivery on Slack's hook": deliver(f"/v1/hooks/slack/{cid}", secret, f"d0-{tag}")}
        for problem, got in refusals.items():
            assert got.status_code == 401 and got.json() == {"detail": "invalid signature"}, f"{problem} wasn't refused alike: {got.status_code} {got.text[:100]}"

        assert httpx.post(f"{FAKE_JIRA}/setup", json=setup([alice], f"Rewritten later {tag}."), timeout=10).status_code == 200  # carol revoked
        got = deliver(hook, secret, f"d1-{tag}")
        assert got.status_code == 202 and got.json() == {"duplicate": False, "queued": 1}, f"{got.status_code} {got.text[:200]}"
        assert deliver(hook, secret, f"d1-{tag}").json()["duplicate"] is True, "a replayed delivery was taken again"
        assert until(lambda: not finds("carol"), 90), "a revoked reader kept the issue after a signed webhook"
        assert finds("alice"), "a reader still granted lost the issue"
        page = api("GET", f"/v1/documents/jira-{key}-1/content", "alice").text
        assert "Dual-run for 30 days" in page and "Rewritten later" not in page, "the readers refresh re-read the issue's content"
        read = api("GET", path, "alice").json()
        assert read["readers_read_at"] > read["last_synced_at"], f"the refresh didn't say when it read readers: {read}"

        assert httpx.post(f"{FAKE_JIRA}/setup", json=setup([alice, carol], f"Rewritten later {tag}."), timeout=10).status_code == 200
        with psycopg.connect(OWNER_DB, autocommit=True) as conn:  # its readers last read an hour ago: the refresh is due
            conn.execute("UPDATE kl.connections SET readers_refreshed_at = now() - interval '1 hour', "
                         "last_synced_at = now() - interval '1 hour' WHERE id = %s", (cid,))
        assert until(lambda: finds("carol"), 180), "the readers refresh never ran on its own cadence"
        assert api("DELETE", path + "/webhook", "alice").status_code == 204
        assert deliver(hook, secret, f"d2-{tag}").status_code == 401, "a removed webhook still took deliveries"
    finally:
        api("DELETE", path, "alice")
        fga.write(deletes=links)
        with psycopg.connect(OWNER_DB, autocommit=True) as conn:
            conn.execute("DELETE FROM kl.principal_aliases WHERE source LIKE %s", (f"ext:atlassian.%{tag.lower()}",))
            conn.execute("DELETE FROM kl.source_identities WHERE source = 'atlassian' AND external_id LIKE %s", (f"%{tag.lower()}",))


@check
def slack_hooks_are_signed_fresh_and_taken_once():
    """S8, Slack's Events API: a delivery signed with the app's signing secret over its timestamp and body, within five minutes, queues the connection's readers refresh, which takes a member removed from the channel off it; Slack's address check is answered only when signed; an unsigned delivery, an old one, and the same event again are refused or ignored; a Slack hook takes the app's signing secret, never one Aerugo makes"""
    import hashlib
    import hmac
    from .authz import fga
    tag = secrets.token_hex(3).upper()
    uid = {u: f"U{u.upper()}{tag}" for u in ("alice", "carol")}
    person = lambda u: {"id": uid[u], "team_id": "T0SMOKE", "name": u, "profile": {"real_name": u.title(), "email": f"{u}@acme.test"}}
    channel = f"CHOOK{tag}"
    state = lambda members: {"users": [person(u) for u in uid], "channels": {channel: {
        "name": f"hooks-{tag.lower()}", "private": True, "members": [uid[m] for m in members], "messages": [
            {"ts": "1760000001.000001", "user": uid["alice"], "text": f"Hook rehearsal {tag}"}]}}}
    principal = lambda u: f"ext:slack.{uid[u]}"
    links = {(f"user:{u}", "owner", f"identity:{principal(u)[4:]}") for u in uid}
    name = f"slack-hooks-{tag.lower()}"
    path = f"/v1/workspaces/payments/connections/{name}"
    signing = secrets.token_hex(16)
    doc = f"slack-{channel}-2025-10-09"

    def finds(user):
        return doc in {h["id"] for h in api("GET", "/v1/search", user, params={"q": tag, "scope": "org", "limit": 50}).json()["results"]}

    def until(test, seconds=150):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not test():
            time.sleep(2)
        return test()

    def deliver(hook, payload: dict, secret=signing, stamp=None):
        raw, stamp = json.dumps(payload).encode(), str(int(stamp or time.time()))
        signature = "v0=" + hmac.new(secret.encode(), b"v0:" + stamp.encode() + b":" + raw, hashlib.sha256).hexdigest()
        return httpx.post(f"{API}{hook}", content=raw, timeout=10, headers={
            "Content-Type": "application/json", "X-Slack-Request-Timestamp": stamp, "X-Slack-Signature": signature})

    with psycopg.connect(OWNER_DB, autocommit=True) as conn:
        org = conn.execute("SELECT id FROM kl.orgs WHERE slug = 'acme'").fetchone()[0]
        for u in uid:
            conn.execute("INSERT INTO kl.principal_aliases (org_id, source, canonical, verified_by) VALUES (%s, %s, %s, 'smoke') "
                         "ON CONFLICT DO NOTHING", (org, principal(u), f"user:{u}"))
    fga.write(writes=links)
    try:
        assert httpx.post(f"{FAKE_SLACK}/setup", json=state(["alice", "carol"]), timeout=10).status_code == 200
        r = api("POST", "/v1/workspaces/payments/connections/slack", "alice", json={
            "name": name, "api_base": f"{FAKE_SLACK}/api", "bot_token": "xoxb-smoke-slack-token", "channels": [channel],
            "history_days": 3650, "every_minutes": 10080})
        assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
        assert until(lambda: finds("carol")), "the channel never arrived"
        assert api("POST", path + "/webhook", "alice", json={}).status_code == 422, "a Slack hook took a secret Aerugo made"
        made = api("POST", path + "/webhook", "alice", json={"signing_secret": signing})
        assert made.status_code == 201 and made.json()["secret"] is None, "a Slack signing secret was echoed back"
        hook = f"/v1/hooks/slack/{r.json()['id']}"
        check = {"type": "url_verification", "challenge": f"challenge-{tag}"}
        assert deliver(hook, check).json() == {"challenge": f"challenge-{tag}"}, "Slack's signed address check wasn't answered"
        assert deliver(hook, check, secret="not-the-signing-secret").status_code == 401, "an unsigned address check was answered"
        event = {"type": "event_callback", "event_id": f"Ev{tag}1", "event": {"type": "member_left_channel", "channel": channel}}
        assert deliver(hook, event, stamp=time.time() - 600).status_code == 401, "a delivery ten minutes old was taken"
        assert httpx.post(f"{FAKE_SLACK}/setup", json=state(["alice"]), timeout=10).status_code == 200  # carol left the channel
        got = deliver(hook, event)
        assert got.status_code == 202 and got.json()["queued"] == 1, f"{got.status_code} {got.text[:200]}"
        assert deliver(hook, event).json()["duplicate"] is True, "the same event was taken twice"
        assert until(lambda: not finds("carol"), 90), "a member who left kept the channel after a signed event"
        assert finds("alice"), "a member still in the channel lost it"
    finally:
        api("DELETE", path, "alice")
        fga.write(deletes=links)
        with psycopg.connect(OWNER_DB, autocommit=True) as conn:
            conn.execute("DELETE FROM kl.principal_aliases WHERE source LIKE %s", (f"ext:slack.U%{tag}",))


@check
def notion_pages_reach_only_the_connecting_team():
    """S6, Notion live: what's shared with an integration (pages, child pages under their parents, a data source's rows) is readable by the connecting team's members and no one else, never the whole org, since Notion doesn't say who can read a page; a page in the trash or never shared isn't read; a page unshared leaves on the next sync; an export's copy of a page the live connector reads is retired; a token that isn't an integration's, an internal address, and another team's secret are refused"""
    import io
    import zipfile
    tag = secrets.token_hex(3)
    pid = lambda n: f"{int(tag, 16) * 100 + n:08x}-0000-4000-8000-{n:012x}"
    hexid = lambda n: pid(n).replace("-", "")
    text = lambda kind, s: {"id": str(uuid.uuid4()), "type": kind, kind: {"rich_text": [{"plain_text": s}]}, "has_children": False}
    pages = lambda child_shared=True: {
        pid(1): {"title": f"Runbook {tag}", "shared": True, "blocks": [text("heading_1", "Steps"), text("paragraph", f"Page the on-call {tag}.")]},
        pid(2): {"title": f"Escalation {tag}", "shared": child_shared, "parent": {"type": "page_id", "page_id": pid(1)},
                 "blocks": [text("bulleted_list_item", f"Then the lead {tag}")]},
        pid(3): {"title": f"Trashed {tag}", "shared": True, "in_trash": True, "blocks": [text("paragraph", f"gone {tag}")]},
        pid(4): {"title": f"Private {tag}", "shared": False, "blocks": [text("paragraph", f"not shared {tag}")]},
        pid(5): {"title": f"Decision {tag}", "parent": {"type": "data_source_id", "data_source_id": pid(50)},
                 "blocks": [text("paragraph", f"We chose Q3 {tag}.")]}}
    sources = {pid(50): {"title": f"Decisions {tag}", "shared": True, "rows": [pid(5)]}}
    name, iname = f"notion-live-{tag}", f"notion-export-{tag}"
    path = f"/v1/workspaces/payments/connections/{name}"
    body = {"name": name, "api_base": f"{FAKE_NOTION}/v1", "token": "ntn_smoke_notion_integration_token", "every_minutes": 60}

    def hits(u):
        return {h["id"] for h in api("GET", "/v1/search", u, params={"q": tag, "scope": "org", "limit": 50}).json()["results"]}

    def exported_copies():
        with psycopg.connect(OWNER_DB) as conn:
            return conn.execute("SELECT count(*) FROM kl.documents WHERE container_ref LIKE 'import:%%' AND source_uri = %s",
                                (f"https://www.notion.so/{hexid(1)}",)).fetchone()[0]

    def until(test, seconds=120):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not test():
            time.sleep(3)
        return test()

    try:
        export = io.BytesIO()  # an export of the same workspace, imported first: the live connector will take its page over
        with zipfile.ZipFile(export, "w") as z:
            z.writestr(f"Export/Runbook {tag} {hexid(1)}.md", f"# Runbook {tag}\n\nThe exported runbook {tag}.")
        r = api("POST", "/v1/workspaces/payments/imports", "alice", data={"name": iname, "readers": "group:payments"},
                files={"file": ("export.zip", export.getvalue(), "application/zip")})
        assert r.status_code == 202, r.text[:200]
        assert until(lambda: exported_copies() == 1), "the export's copy of the page never arrived"
        assert httpx.post(f"{FAKE_NOTION}/setup", json={"pages": pages(), "sources": sources}, timeout=10).status_code == 200
        refusals = {"a token that isn't an integration's was accepted": {**body, "token": "xoxb-not-notion-at-all-123456"},
                    "an internal address was accepted": {**body, "api_base": "http://10.0.0.5"},
                    "a reference to another team's secret was accepted": {**body, "token": None,
                                                                         "credential_ref": "openbao://secret/kl/acme/finance/models/smoke-fake"}}
        for problem, attempt in refusals.items():
            assert api("POST", "/v1/workspaces/payments/connections/notion", "alice", json=attempt).status_code == 422, problem
        r = api("POST", "/v1/workspaces/payments/connections/notion", "alice", json=body)
        assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
        key = lambda n: f"notion-{hexid(n)}"
        assert until(lambda: key(2) in hits("alice")), "Notion's pages never arrived"
        alice, carol, bob, dave = (hits(u) for u in ("alice", "carol", "bob", "dave"))
        assert {key(1), key(2), key(5)} <= alice and {key(1), key(2), key(5)} <= carol, "the connecting team's members can't read what's shared"
        assert not ({key(1), key(2), key(5)} & (bob | dave)), "a Notion page reached someone outside the connecting team"
        assert not ({key(3), key(4)} & alice), "a page in the trash, or never shared, was read"
        doc = api("GET", "/v1/documents", "alice", params={"scope": "org", "key": key(2)}).json()["documents"]
        assert doc and doc[0]["path"] == f"Runbook {tag}", f"a child page isn't placed under its parent: {doc}"
        row = api("GET", "/v1/documents", "alice", params={"scope": "org", "key": key(5)}).json()["documents"]
        assert row and row[0]["path"] == f"Decisions {tag}", f"a data source's row isn't placed under it: {row}"
        assert until(lambda: exported_copies() == 0), "the export's copy of a page the live connector reads stayed indexed"

        assert httpx.post(f"{FAKE_NOTION}/setup", json={"pages": pages(child_shared=False), "sources": sources}, timeout=10).status_code == 200
        assert api("POST", path + "/refresh", "alice").status_code == 202
        assert until(lambda: key(2) not in hits("alice")), "a page unshared from the integration stayed indexed"
        assert key(1) in hits("alice"), "a page still shared left"
    finally:
        api("DELETE", path, "alice")
        api("DELETE", f"/v1/workspaces/payments/imports/{iname}", "alice")


@check
def ci_pushes_dbt_and_dataform_manifests():
    """S7, dbt and Dataform manifests from CI: a registered pipeline's token pushes a project's manifest; each model and source becomes a document only its named readers find, and an asset in the pipeline's team with lineage from what it depends on; a catalog owner shows in who to ask for the page that describes the asset, never another team's owner of the same name; a model gone from the next manifest leaves; readers off the team and a manifest that isn't one are refused"""
    import uuid as uuidlib
    from .authz import fga
    from .pipelines import document_key
    tag = uuidlib.uuid4().hex[:8]
    repos = {"payments": f"acme/warehouse-{tag}", "finance": f"acme/finance-dbt-{tag}"}
    names = {ws: f"dbt-{ws[:3]}-{tag}" for ws in repos}
    stranger_admin = ("user:bob", "admin", "workspace:finance")  # no seed user administers finance

    def mint(ws):
        return httpx.post(f"{FAKE_CI}/token", json={"repository": repos[ws]}, timeout=10).json()["token"]

    def push(ws, body):
        r = httpx.post(f"{API}/v1/workspaces/{ws}/manifests", json=body, timeout=60, headers={"Authorization": f"Bearer {mint(ws)}"})
        return r

    def applied(ws, r):
        assert r.status_code == 202, f"manifest push: {r.status_code} {r.text[:200]}"
        url, deadline = f"{API}/v1/workspaces/{ws}/documents/pushes/{r.json()['push']}", time.monotonic() + 120
        while time.monotonic() < deadline:
            state = httpx.get(url, headers={"Authorization": f"Bearer {mint(ws)}"}, timeout=10).json()
            if state["status"] != "queued":
                assert state["status"] == "done", f"the manifest push failed: {state}"
                return state["result"]
            time.sleep(1)
        raise AssertionError("the worker never applied the manifest")

    def dbt(*nodes, owner="carol@acme.test"):
        orders = {"resource_type": "model", "package_name": "shop", "name": f"orders_{tag}", "database": "proj", "schema": "shop",
                  "description": f"Orders, one row per order {tag}.", "columns": {"order_id": {"name": "order_id", "data_type": "string",
                                                                                              "description": "The order"}},
                  "config": {"materialized": "table", "meta": {"owner": owner}}, "tags": ["finance"],
                  "original_file_path": "models/orders.sql", "depends_on": {"nodes": [f"source.shop.raw.raw_orders_{tag}"]}}
        ledger = {"resource_type": "model", "package_name": "shop", "name": "ledger", "description": f"The ledger model {tag}.",
                  "meta": {"owner": owner}, "depends_on": {"nodes": []}}
        source = {"resource_type": "source", "package_name": "shop", "name": f"raw_orders_{tag}", "identifier": f"raw_orders_{tag}",
                  "database": "proj", "schema": "raw", "description": f"Raw orders {tag}."}
        chosen = {"orders": (f"model.shop.orders_{tag}", orders), "ledger": ("model.shop.ledger", ledger)}
        return {"metadata": {"adapter_type": "bigquery"}, "nodes": dict(chosen[n] for n in nodes),
                "sources": {f"source.shop.raw.raw_orders_{tag}": source}}

    def hits(u):
        return {h["id"] for h in api("GET", "/v1/search", u, params={"q": tag, "scope": "org", "limit": 50}).json()["results"]}

    def owners_named(u):
        return {p["user"]: p["reasons"] for p in api("GET", "/v1/concepts/kb-ledger/people", u).json()["people"]}

    pids = {}
    fga.write(writes={stranger_admin})
    try:
        for user in ("carol", "dave"):  # their verified emails, for who to ask to match the catalog's owners
            api("GET", "/v1/whoami", user)
        for ws, admin in (("payments", "alice"), ("finance", "bob")):
            r = api("POST", f"/v1/workspaces/{ws}/pipelines", admin, json={"name": names[ws], "issuer": FAKE_CI, "repository": repos[ws]})
            assert r.status_code == 201, f"register: {r.status_code} {r.text[:200]}"
            pids[ws] = r.json()["id"]
        body = {"kind": "dbt", "project": "shop", "readers": ["group:payments"], "manifest": dbt("orders", "ledger")}
        assert push("payments", {**body, "readers": ["group:finance"]}).status_code == 422, "a reader off the team was accepted"
        assert push("payments", {**body, "manifest": {"nodes": "not a manifest"}}).status_code == 422, "a manifest that isn't one was accepted"
        result = applied("payments", push("payments", body))
        assert result["upserted"] == 3 and result["edges"] == 1, f"the manifest wasn't applied as its models: {result}"
        orders = document_key(pids["payments"], f"manifest/shop/proj.shop.orders_{tag}")
        assert orders in hits("alice") and orders in hits("carol"), "a model's document didn't reach its named readers"
        assert orders not in hits("bob") and orders not in hits("dave"), "a model's document reached people it doesn't name"
        impact = api("GET", "/v1/lineage/impact", "alice", params={"namespace": "bigquery", "name": f"proj.raw.raw_orders_{tag}", "kind": "dataset"})
        assert impact.status_code == 200 and f"proj.shop.orders_{tag}" in json.dumps(impact.json()), \
            f"the model's lineage from its source didn't reach the impact walk: {impact.text[:300]}"
        assert api("GET", "/v1/lineage/impact", "bob", params={"namespace": "bigquery", "name": f"proj.raw.raw_orders_{tag}", "kind": "dataset"}).status_code == 404, \
            "another team saw this team's lineage"
        assert any("owns ledger" in reason for reason in owners_named("alice").get("carol", [])), \
            f"the catalog's owner isn't in who to ask: {owners_named('alice')}"
        applied("finance", push("finance", {"kind": "dbt", "project": "fin", "readers": ["group:finance"], "manifest": dbt("ledger", owner="dave@acme.test")}))
        assert "dave" not in owners_named("alice"), "another team's catalog owner showed in who to ask"
        graph = {"tables": [{"type": "table", "target": {"database": "proj", "schema": "df", "name": f"daily_{tag}"},
                             "dependencyTargets": [{"database": "proj", "schema": "shop", "name": f"orders_{tag}"}],
                             "actionDescriptor": {"description": f"Daily totals {tag}.", "columns": [{"path": ["day"], "description": "The day"}]}}],
                 "declarations": []}
        result = applied("payments", push("payments", {"kind": "dataform", "project": "df", "readers": ["group:payments"], "manifest": graph}))
        assert result["upserted"] == 1 and result["edges"] == 1, f"the Dataform graph wasn't applied: {result}"
        applied("payments", push("payments", {**body, "manifest": dbt("ledger")}))  # orders is gone from the project
        assert orders not in hits("alice"), "a model gone from the next manifest stayed indexed"
        assert document_key(pids["payments"], f"manifest/df/proj.df.daily_{tag}") in hits("alice"), "another project's documents went too"
    finally:
        for ws, admin in (("payments", "alice"), ("finance", "bob")):
            api("DELETE", f"/v1/workspaces/{ws}/pipelines/{names[ws]}", admin)
        fga.write(deletes={stranger_admin})
        with psycopg.connect(OWNER_DB, autocommit=True) as conn:
            conn.execute("DELETE FROM kl.asset_owners WHERE source LIKE ANY(%s)", ([f"pipeline:{p}:%" for p in pids.values()],))


def catalog_tables(tag: str, readers: list[str]) -> list[dict]:
    """The fake catalogs' tables for one check (S7): orders, read by `readers` and fed by staging's raw_orders; a table only a
    group may read (with no directory to expand it, nobody); and a calendar in its own dataset the company's domain reads."""
    cols = [["order_id", "STRING", "The order"], ["amount", "NUMERIC", "In cents"]]
    return [{"name": f"sales_{tag}.orders", "description": f"Orders, one row per order {tag}.", "columns": cols, "owner": "carol@acme.test",
             "users": readers, "groups": [f"analysts-{tag}@acme.test"], "upstream": [f"staging_{tag}.raw_orders"], "version": "2"},
            {"name": f"staging_{tag}.raw_orders", "description": f"Raw orders as they land {tag}.", "columns": cols[:1],
             "users": ["carol@acme.test", "alice@acme.test"], "version": "1"},
            {"name": f"hr_{tag}.salaries", "description": f"Salaries, for the group only {tag}.", "groups": [f"hr-{tag}@acme.test"], "version": "1"},
            {"name": f"wide_{tag}.calendar", "description": f"The company calendar {tag}.", "domain": True, "version": "1"}]


CATALOG_PEOPLE = [f"{u}@acme.test" for u in ("alice", "bob", "carol", "dave", "mallory-not-here")]


def forget_matches(source: str, emails: list[str]) -> None:
    """Undo the identity matching a check caused: the sso-email links to these accounts of a source, their tuples, and
    the accounts themselves, so a later check starts from people unmatched."""
    from .authz import fga
    with psycopg.connect(OWNER_DB, autocommit=True) as conn:
        links = conn.execute("DELETE FROM kl.principal_aliases WHERE source = ANY(%s) AND verified_by = 'sso-email' RETURNING source, canonical",
                             ([f"ext:{source}.{e}" for e in emails],)).fetchall()
        conn.execute("DELETE FROM kl.source_identities WHERE source = %s AND external_id = ANY(%s)", (source, emails))
    held = {t for t in {(canonical, "owner", f"identity:{s[4:]}") for s, canonical in links}
            if fga.read({"user": t[0], "relation": t[1], "object": t[2]})}
    if held:
        fga.write(deletes=held)


def catalog_round(provider: str, admin: str, body: dict, key, lineage: tuple[str, str, str] | None, refusals: dict,
                  setup: dict | None = None, domain_reads: bool = False, then=None, identities: str | None = None) -> None:
    """One catalog's proof (S7): orders reaches the people the catalog lets read it, as themselves (identity matching),
    and no one else; a group with no directory to expand it, and every grant the catalog can't tie to a person, reach
    no one; with `domain_reads`, the company's domain reaches the org; the team sees the lineage, another team doesn't;
    taken out of the catalog's grants, a reader loses orders on the next sync; each refusal is refused; `then(path)`
    checks anything else while the connection is there."""
    tag = body["name"].rsplit("-", 1)[1]
    path = f"/v1/workspaces/payments/connections/{body['name']}"

    def setup_tables(readers):
        return httpx.post(f"{FAKE_CATALOG}/catalog/setup", json={"tables": catalog_tables(tag, readers), **(setup or {})}, timeout=10)

    def hits(u):
        return {h["id"] for h in api("GET", "/v1/search", u, params={"q": tag, "scope": "org", "limit": 50}).json()["results"]}

    def until(test, seconds=150):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not test():
            time.sleep(3)
        return test()

    def sync_state():
        with psycopg.connect(OWNER_DB, row_factory=dict_row) as conn:
            return conn.execute("SELECT status, last_error, last_synced_at FROM kl.connections WHERE name = %s", (body["name"],)).fetchone()

    orders, raw, salaries, calendar = (key(f"{db}_{tag}.{t}") for db, t in (("sales", "orders"), ("staging", "raw_orders"), ("hr", "salaries"),
                                                                             ("wide", "calendar")))
    try:
        assert setup_tables(["carol@acme.test", "mallory-not-here@acme.test"]).status_code == 200
        for problem, (who, attempt, status) in refusals.items():
            r = api("POST", "/v1/workspaces/payments/connections/catalog", who, json=attempt)
            assert r.status_code == status, f"{problem}: {r.status_code} {r.text[:200]}"
        r = api("POST", "/v1/workspaces/payments/connections/catalog", admin, json=body)
        assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
        assert until(lambda: (sync_state() or {}).get("last_synced_at") is not None or (sync_state() or {}).get("status") == "error"), \
            f"the {provider} catalog never synced"
        assert sync_state()["status"] != "error", f"the {provider} sync failed: {sync_state()['last_error']}"
        for u in ("alice", "bob", "carol", "dave"):  # identity matching links each to their catalog account by verified email
            api("GET", "/v1/whoami", u)
        assert until(lambda: orders in hits("carol")), f"{provider}: orders didn't reach the person the catalog lets read it"
        assert orders not in hits("alice") and orders not in hits("bob") and orders not in hits("dave"), \
            f"{provider}: orders reached someone the catalog doesn't let read it"
        assert raw in hits("alice") and raw in hits("carol"), f"{provider}: raw_orders didn't reach both its readers"
        assert not any(salaries in hits(u) for u in ("alice", "bob", "carol", "dave")), \
            f"{provider}: a table only a group may read reached someone with no directory to expand the group"
        if domain_reads:
            assert calendar in hits("bob") and calendar in hits("carol"), f"{provider}: the company's domain didn't reach the org"
        else:
            assert not any(calendar in hits(u) for u in ("alice", "bob", "carol", "dave")), f"{provider}: a grant to a domain reached people"
        doc = api("GET", "/v1/documents", "carol", params={"scope": "org", "key": orders}).json()["documents"]
        assert doc, f"{provider}: orders isn't in its reader's documents"
        content = api("GET", f"/v1/documents/{orders}/content", "carol")
        assert content.status_code == 200 and "order_id" in content.text and "carol@acme.test" in content.text, \
            f"{provider}: orders' page lacks its columns or owner: {content.text[:300]}"
        assert api("GET", f"/v1/documents/{orders}/content", "dave").status_code == 404, f"{provider}: a stranger read orders' page"
        if lineage:
            namespace, upstream, downstream = lineage
            impact = api("GET", "/v1/lineage/impact", "alice", params={"namespace": namespace, "name": upstream, "kind": "dataset"})
            assert impact.status_code == 200 and downstream in json.dumps(impact.json()), \
                f"{provider}: the catalog's lineage didn't reach the team's impact walk: {impact.text[:300]}"
            assert api("GET", "/v1/lineage/impact", "bob", params={"namespace": namespace, "name": upstream, "kind": "dataset"}).status_code == 404, \
                f"{provider}: another team saw this team's catalog lineage"
        assert setup_tables(["mallory-not-here@acme.test"]).status_code == 200  # carol's grant on orders is revoked
        assert api("POST", path + "/refresh", admin).status_code == 202
        assert until(lambda: orders not in hits("carol")), f"{provider}: a revoked reader kept orders after the next sync"
        assert raw in hits("carol"), f"{provider}: a table still granted left"
        if then:
            then(path)
    finally:
        api("DELETE", path, admin)
        if identities:
            forget_matches(identities, CATALOG_PEOPLE)


@check
def dataplex_entries_carry_bigquerys_own_readers():
    """S7, Dataplex: read keyless as the runner's own identity; a BigQuery table's entry is readable by the people its dataset and IAM policy name, as their Google accounts, and the company's domain reaches the org only when the connection names it; groups without a directory, everyone, special groups, service accounts, and metadata-only roles reach no one; lineage from the Data Lineage API reaches the team's impact walk only; a revoked reader loses the entry on the next sync; only an org admin connects it, without a secret, to a project named by id and an allowed address"""
    from .catalogs import entry_key
    tag = secrets.token_hex(3)
    body = {"name": f"dataplex-{tag}", "provider": "dataplex", "api_base": f"{FAKE_CATALOG}/gcp-catalog", "projects": ["acme-data"],
            "domain": "acme.test", "every_minutes": 60}
    refusals = {"a team admin connected the runner's own identity": ("alice", body, 403),
                "an internal address was accepted": ("olivia", {**body, "api_base": "http://10.0.0.5"}, 422),
                "a project not named by its id was accepted": ("olivia", {**body, "projects": ["Acme Data!"]}, 422),
                "a domain not the company's was accepted": ("olivia", {**body, "domain": "evil.test"}, 422),
                "a secret was taken for a keyless catalog": ("olivia", {**body, "token": "x" * 30}, 422)}
    catalog_round("Dataplex", "olivia", body, lambda name: entry_key("dataplex", f"bigquery:acme-data.{name}"),
                  ("bigquery", f"acme-data.staging_{tag}.raw_orders", f"acme-data.sales_{tag}.orders"), refusals, domain_reads=True,
                  identities="google")


@check
def unity_catalog_tables_carry_their_effective_readers():
    """S7, Unity Catalog: a service principal signs in by OAuth machine-to-machine; a table is readable by the people its effective permissions let SELECT it, as their Databricks accounts, and no one else (a group, and a browse-only grant, reach no one); its lineage reaches the team's impact walk only; a revoked reader loses it on the next sync; its catalogs change in place, validated; an address that isn't a Databricks workspace, a service principal not named by its id, and another team's secret are refused"""
    from .catalogs import entry_key
    tag = secrets.token_hex(3)
    dbx = httpx.post(f"{FAKE_CATALOG}/catalog/setup", json={"tables": []}, timeout=10).json()["databricks"]
    body = {"name": f"unity-{tag}", "provider": "unity", "api_base": f"{FAKE_CATALOG}/databricks", "client_id": dbx["client_id"],
            "client_secret": dbx["client_secret"], "every_minutes": 60}
    refusals = {"an internal address was accepted": ("alice", {**body, "api_base": "http://10.0.0.5"}, 422),
                "a service principal not named by its id was accepted": ("alice", {**body, "client_id": "aerugo"}, 422),
                "a reference to another team's secret was accepted": ("alice", {**body, "client_secret": None,
                                                                               "credential_ref": "openbao://secret/kl/acme/finance/models/smoke-fake"}, 422)}
    def lists_change_in_place(path):
        assert api("PATCH", path, "alice", json={"catalogs": ["main", "finance"]}).status_code == 200, "Unity's catalogs didn't change in place"
        assert api("GET", path, "alice").json()["settings"]["catalogs"] == ["main", "finance"], "Terraform can't read back the catalogs"
        assert api("PATCH", path, "alice", json={"catalogs": ["bad name!"]}).status_code == 422, "a malformed catalog was accepted"
        assert api("PATCH", path, "alice", json={"platforms": ["snowflake"]}).status_code == 422, "another catalog's list was accepted"
    catalog_round("Unity Catalog", "alice", body, lambda name: entry_key("unity-main", f"main.{name}"),
                  ("unity", f"main.staging_{tag}.raw_orders", f"main.sales_{tag}.orders"), refusals, then=lists_change_in_place,
                  identities="databricks")


@check
def glue_tables_carry_lake_formations_grants():
    """S7, Glue: the runner assumes the company's role with its own identity token (no AWS key), and signs every call (SigV4, held to AWS's own test vector); a table is readable by the people Lake Formation grants SELECT through the company's SAML provider, as their AWS accounts, and no one else (a SAML group, IAMAllowedPrincipals, a database-level grant, and DESCRIBE reach no one); a revoked reader loses it on the next sync; only an org admin connects it, without a secret, to a role and region named exactly"""
    import datetime
    from .catalogs import entry_key
    from .glue import Credentials, sign
    vanilla = sign("GET", "https://example.amazonaws.com/", "us-east-1", "service", b"", {},
                   Credentials("AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", ""), datetime.datetime(2015, 8, 30, 12, 36))
    assert vanilla["authorization"].endswith("Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"), \
        f"SigV4 doesn't match AWS's get-vanilla test vector: {vanilla['authorization']}"
    tag = secrets.token_hex(3)
    role = httpx.post(f"{FAKE_CATALOG}/catalog/setup", json={"tables": []}, timeout=10).json()["role"]
    body = {"name": f"glue-{tag}", "provider": "glue", "api_base": f"{FAKE_CATALOG}/aws", "region": "us-east-1", "role_arn": role,
            "databases": [f"sales_{tag}", f"staging_{tag}", f"hr_{tag}", f"wide_{tag}"], "every_minutes": 60}
    refusals = {"a team admin connected the runner's own identity": ("alice", body, 403),
                "an internal address was accepted": ("olivia", {**body, "api_base": "http://10.0.0.5"}, 422),
                "a role that isn't an IAM role's ARN was accepted": ("olivia", {**body, "role_arn": "arn:aws:iam::1:user/me"}, 422),
                "a region that isn't one was accepted": ("olivia", {**body, "region": "moon-1"}, 422)}
    catalog_round("Glue", "olivia", body, lambda name: entry_key("glue", f"us-east-1/{name}"), None, refusals, identities="aws")


@check
def purview_assets_carry_their_collections_readers():
    """S7, Purview: an Entra app signs in with its certificate, for Purview's own audience; an asset is readable by the people its collection's policy names as data readers, by their Entra object ids through an Entra directory, and no one else (a collection administrator, and anyone the directory doesn't list, reach no one); lineage through a process reaches the team's impact walk only; a revoked reader loses it on the next sync; an address that isn't a Purview account, a tenant not named by its id, and a client secret are refused"""
    tag = secrets.token_hex(3)
    tenant = str(uuid.uuid4())
    app = httpx.post(f"{FAKE_MICROSOFT}/app", json={"tenant_id": tenant}, timeout=30).json()
    people = ("alice", "bob", "carol", "dave")
    ids = {u: str(uuid.uuid4()) for u in people}
    directory = {"name": f"entra-purview-{tag}", "provider": "entra", "api_base": FAKE_MICROSOFT, "tenant_id": tenant,
                 "client_id": app["client_id"], "certificate": app["certificate"], "every_minutes": 60}
    users = [{"id": ids[u], "mail": f"{u}@acme.test", "userPrincipalName": f"{u}@acme.test", "displayName": u.title(), "accountEnabled": True}
             for u in people]
    body = {"name": f"purview-{tag}", "provider": "purview", "api_base": f"{FAKE_CATALOG}/purview", "tenant_id": tenant,
            "client_id": app["client_id"], "certificate": app["certificate"], "collections": [f"sales_{tag}", f"staging_{tag}", f"hr_{tag}", f"wide_{tag}"],
            "every_minutes": 60}
    refusals = {"an internal address was accepted": ("alice", {**body, "api_base": "http://10.0.0.5"}, 422),
                "a tenant not named by its id was accepted": ("alice", {**body, "tenant_id": "contoso.onmicrosoft.com"}, 422),
                "a client secret was accepted": ("alice", {**body, "certificate": None, "client_secret": "a-client-secret"}, 422)}
    guid = lambda name: str(uuid.uuid5(uuid.NAMESPACE_URL, f"purview/{name}"))
    try:
        assert httpx.post(f"{FAKE_MICROSOFT}/setup", json={"users": users, "groups": {}}, timeout=10).status_code == 200
        r = api("POST", "/v1/workspaces/payments/connections/directory", "olivia", json=directory)
        assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            with psycopg.connect(OWNER_DB) as conn:
                if conn.execute("SELECT count(*) FROM kl.directory_people p JOIN kl.connections c ON c.id = p.connection_id "
                                "WHERE c.name = %s AND p.external_id IS NOT NULL", (directory["name"],)).fetchone()[0] == len(people):
                    break
            time.sleep(2)
        else:
            raise AssertionError("the Entra directory never listed its people with their object ids")
        catalog_round("Purview", "alice", body, lambda name: f"purview-{guid(name)}",
                      ("purview", f"mssql://acme.database.windows.net/staging_{tag}/dbo/raw_orders",
                       f"mssql://acme.database.windows.net/sales_{tag}/dbo/orders"), refusals,
                      setup={"people": {f"{u}@acme.test": ids[u] for u in people}}, identities="microsoft")
    finally:
        api("DELETE", f"/v1/workspaces/payments/connections/{directory['name']}", "olivia")


@check
def datahub_datasets_reach_only_their_owners():
    """S7, DataHub: an access token reads a platform's datasets through GraphQL; DataHub's view policies can't be read with it, so a dataset is readable only by the people DataHub names as its owners, as their DataHub accounts (a group owner reaches no one); its upstream lineage reaches the team's impact walk only; an owner removed loses it on the next sync; an address the deployment doesn't allow, a platform not named as DataHub names them, and another team's secret are refused"""
    import hashlib
    tag = secrets.token_hex(3)
    token = httpx.post(f"{FAKE_CATALOG}/catalog/setup", json={"tables": []}, timeout=10).json()["datahub_token"]
    body = {"name": f"datahub-{tag}", "provider": "datahub", "api_base": f"{FAKE_CATALOG}/datahub", "platforms": ["snowflake"],
            "token": token, "every_minutes": 60}
    refusals = {"an internal address was accepted": ("alice", {**body, "api_base": "http://10.0.0.5"}, 422),
                "a platform DataHub wouldn't name was accepted": ("alice", {**body, "platforms": ["Snow Flake!"]}, 422),
                "a reference to another team's secret was accepted": ("alice", {**body, "token": None,
                                                                               "credential_ref": "openbao://secret/kl/acme/finance/models/smoke-fake"}, 422)}
    urn = lambda name: f"urn:li:dataset:(urn:li:dataPlatform:snowflake,{name},PROD)"
    # DataHub's readers are its owners, so the fake names each table's readers as its owners
    catalog_round("DataHub", "alice", body, lambda name: f"datahub-{hashlib.sha256(urn(name).encode()).hexdigest()[:32]}",
                  ("snowflake", f"staging_{tag}.raw_orders", f"sales_{tag}.orders"), refusals, setup=None, identities="datahub")


@check
def drive_reads_as_a_service_account():
    """Google Drive on a service account (ADR 0011): its key signs its own token request, sent only to the allowed Google address whatever the key names; folders shared with it are found and walked, subfolders too; each file keeps its own readers, the domain's or a person's; the people Drive names are recorded, so signing in matches them; a junk key and another team's secret are refused"""
    from .authz import fga
    tag = secrets.token_hex(3)
    key = httpx.post(f"{FAKE_GOOGLE}/service-account", json={"email": f"aerugo-{tag}@acme-dev.iam.gserviceaccount.com"}, timeout=30).json()
    sub = f"sub{tag}"
    folders = {
        f"top{tag}": {"name": "Top", "shared": True, "files": [
            {"id": f"wide{tag}", "name": f"Wide {tag}.txt", "mimeType": "text/plain", "md5Checksum": f"w{tag}", "text": f"Everyone may read this {tag}.",
             "permissions": [{"type": "domain", "role": "reader", "domain": "acme.test"}]},
            {"id": sub, "name": "Sub", "mimeType": "application/vnd.google-apps.folder"}]},
        sub: {"name": "Sub", "shared": False, "files": [
            {"id": f"carol{tag}", "name": f"Carol only {tag}.txt", "mimeType": "text/plain", "md5Checksum": f"c{tag}", "text": f"Only carol {tag}.",
             "permissions": [{"type": "user", "role": "reader", "emailAddress": "carol@acme.test"}]}]},
        f"hidden{tag}": {"name": "Not shared with it", "shared": False, "files": [
            {"id": f"never{tag}", "name": f"Never {tag}.txt", "mimeType": "text/plain", "md5Checksum": f"n{tag}", "text": f"Never indexed {tag}.",
             "permissions": [{"type": "domain", "role": "reader", "domain": "acme.test"}]}]}}
    assert httpx.post(f"{FAKE_GOOGLE}/folders", json={"folders": folders}, timeout=10).status_code == 200
    name = f"drive-{tag}"
    body = {"name": name, "service_account_key": json.dumps(key), "domain": "acme.test", "api_base": FAKE_GOOGLE, "every_minutes": 60,
            "drives": []}  # no shared drives: only what's shared with it, so earlier runs' fake drives stay out
    carol = "ext:google.carol@acme.test"

    def finds(user, file_id):
        hits = api("GET", "/v1/search", user, params={"q": tag, "scope": "org", "limit": 50}).json()["results"]
        return f"gd-{file_id}" in {h["id"] for h in hits}

    try:
        assert api("POST", "/v1/workspaces/payments/connections/google", "alice", json={**body, "service_account_key": "[1]"}).status_code == 422, \
            "a junk key was accepted"
        other_team = {"name": name, "credential_ref": "openbao://secret/kl/acme/finance/models/smoke-fake", "api_base": FAKE_GOOGLE}
        assert api("POST", "/v1/workspaces/payments/connections/google", "alice", json=other_team).status_code == 422, \
            "a reference to another team's secret was accepted"
        r = api("POST", "/v1/workspaces/payments/connections/google", "alice", json=body)
        assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and not finds("alice", f"wide{tag}"):
            time.sleep(3)
        assert finds("alice", f"wide{tag}"), "the shared folder's file never arrived: did the token request go where the key said?"
        assert finds("bob", f"wide{tag}"), "a file shared with the company's domain didn't reach the org"
        assert not finds("bob", f"never{tag}"), "a folder never shared with the service account was indexed"
        named = api("GET", "/v1/documents", "alice", params={"scope": "org", "key": f"gd-wide{tag}"}).json()["documents"]
        assert named and named[0]["container"]["name"] == "Top", f"the file doesn't carry its shared folder's name: {named}"
        assert not any(finds(u, f"carol{tag}") for u in ("alice", "bob", "carol")), "a file shared with carol alone was readable before she was matched"
        with psycopg.connect(OWNER_DB) as conn:
            seen = conn.execute("SELECT 1 FROM kl.source_identities WHERE source = 'google' AND external_id = 'carol@acme.test'").fetchone()
        assert seen, "Drive's account for carol wasn't recorded for identity matching"
        assert carol in api("GET", "/v1/whoami", "carol").json()["identities"], "signing in didn't match carol to her Google account"
        assert finds("carol", f"carol{tag}"), "carol can't find the file shared with her, though she's matched"
        assert not finds("alice", f"carol{tag}"), "the file shared with carol leaked to alice"
    finally:
        api("DELETE", f"/v1/workspaces/payments/connections/{name}", "alice")
        with psycopg.connect(OWNER_DB, autocommit=True) as conn:
            conn.execute("DELETE FROM kl.principal_aliases WHERE source = %s AND verified_by = 'sso-email'", (carol,))
            conn.execute("DELETE FROM kl.source_identities WHERE source = 'google' AND external_id = 'carol@acme.test'")
        stale = {(t["user"], t["relation"], t["object"]) for t in fga.read({"object": "identity:google.carol@acme.test"})}
        if stale:
            fga.write(deletes=stale)


@check
def a_directory_revokes_leavers_at_once():
    """Directories (ADR 0011): only an org admin connects, changes, or removes one (a team admin is refused, and still reads it); when it says someone has left, their matched identities are revoked at once, grants and all, without waiting for a sign-in that won't come; someone it listed before and lists no more has left too; matching refuses them after that; someone still there keeps theirs; an internal address and another team's secret are refused"""
    from .authz import fga
    tag = secrets.token_hex(3)
    leaver, stays, vanishes = f"557058.leaver{tag}", f"557058.stays{tag}", f"557058.vanishes{tag}"
    name = f"okta-{tag}"
    with psycopg.connect(OWNER_DB, autocommit=True) as conn:
        org = conn.execute("SELECT id FROM kl.orgs WHERE slug = 'acme'").fetchone()[0]
        conn.execute("INSERT INTO kl.source_identities (org_id, source, external_id, email) VALUES (%s, 'atlassian', %s, 'carol@acme.test'), "
                     "(%s, 'atlassian', %s, 'alice@acme.test'), (%s, 'atlassian', %s, 'bob@acme.test')", (org, leaver, org, stays, org, vanishes))

    def alias(user, external):
        with psycopg.connect(OWNER_DB) as conn:
            return bool(conn.execute("SELECT 1 FROM kl.principal_aliases WHERE source = %s AND canonical = %s AND verified_by = 'sso-email'",
                                     (f"ext:atlassian.{external}", f"user:{user}")).fetchone())

    def until(test, seconds=90):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not test():
            time.sleep(2)
        return test()

    grant = lambda user, external: fga.read({"user": f"user:{user}", "relation": "owner", "object": f"identity:atlassian.{external}"})
    person = lambda email, first, status="ACTIVE": {"status": status, "profile": {"email": email, "firstName": first, "lastName": "Smoke"}}
    users = [person("alice@acme.test", "Alice"), person("carol@acme.test", "Carol", "DEPROVISIONED"), person("bob@acme.test", "Bob")]
    body = {"name": name, "provider": "okta", "api_base": FAKE_OKTA, "api_token": "smoke-okta-token", "every_minutes": 60}
    path = f"/v1/workspaces/payments/connections/{name}"
    try:
        for user in ("alice", "carol", "bob"):
            api("GET", "/v1/whoami", user)
        assert alias("alice", stays) and alias("carol", leaver) and grant("carol", leaver) and alias("bob", vanishes),             "sign-in didn't match them to begin with"
        assert httpx.post(f"{FAKE_OKTA}/setup", json={"users": users}, timeout=10).status_code == 200
        assert api("POST", "/v1/workspaces/payments/connections/directory", "alice", json=body).status_code == 403,             "a team admin connected a directory, which decides who has left company-wide"
        assert api("POST", "/v1/workspaces/payments/connections/directory", "olivia", json={**body, "api_base": "http://10.0.0.5"}).status_code == 422,             "an internal address was accepted"
        other_team = {**body, "api_token": None, "credential_ref": "openbao://secret/kl/acme/finance/models/smoke-fake"}
        assert api("POST", "/v1/workspaces/payments/connections/directory", "olivia", json=other_team).status_code == 422,             "a reference to another team's secret was accepted"
        r = api("POST", "/v1/workspaces/payments/connections/directory", "olivia", json=body)
        assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
        assert api("GET", path, "alice").status_code == 200, "the team's admin can't see the directory connected in their team"
        for method, change in (("PATCH", {"enabled": False}), ("POST", None), ("DELETE", None)):
            suffix = "/refresh" if method == "POST" else ""
            assert api(method, path + suffix, "alice", **({"json": change} if change else {})).status_code == 403,                 f"a team admin could {method} a directory"
        assert until(lambda: not alias("carol", leaver)), "the leaver's matched identity outlived the directory saying she'd left"  # carol never signs in
        assert not grant("carol", leaver), "the leaver's grant outlived her match"
        assert alias("alice", stays) and grant("alice", stays) and alias("bob", vanishes), "someone still at the company lost their match"
        assert httpx.post(f"{FAKE_OKTA}/setup", json={"users": users[:2]}, timeout=10).status_code == 200  # bob is gone from Okta
        assert api("POST", path + "/refresh", "olivia").status_code == 202
        assert until(lambda: not alias("bob", vanishes)), "someone the directory no longer lists kept their matched identity"
        assert alias("alice", stays), "someone still listed lost their match"
        for user in ("carol", "bob"):
            api("GET", "/v1/whoami", user)
        assert not alias("carol", leaver) and not alias("bob", vanishes), "signing in matched someone the directory says has left"
    finally:
        api("DELETE", path, "olivia")
        httpx.post(f"{FAKE_OKTA}/setup", json={"users": []}, timeout=10)
        with psycopg.connect(OWNER_DB, autocommit=True) as conn:
            conn.execute("DELETE FROM kl.directory_people WHERE connection_id IN (SELECT id FROM kl.connections WHERE name = %s)", (name,))
            conn.execute("DELETE FROM kl.principal_aliases WHERE source LIKE %s", (f"ext:atlassian.%{tag}",))
            conn.execute("DELETE FROM kl.source_identities WHERE source = 'atlassian' AND external_id LIKE %s", (f"%{tag}",))
        stale = {(t["user"], t["relation"], t["object"]) for e in (leaver, stays, vanishes) for t in fga.read({"object": f"identity:atlassian.{e}"})}
        if stale:
            fga.write(deletes=stale)


@check
def a_google_directory_brings_groups_to_drive():
    """S2, Google Workspace as a directory: a service account acting as an admin (domain-wide delegation, exactly three read-only scopes, its token asked only of Google's address) lists people and groups; a Drive file shared with a group reaches its members, a nested group's too, and nobody else; a member suspended, then the directory synced, loses it; a member taken out of the group loses it on the Drive sync that follows; someone deleted between syncs loses their matched access; a listing that fails changes nothing; only an org admin connects one, with an admin in the company's domain and a team's own secret"""
    from .authz import fga
    tag = secrets.token_hex(3)
    eng, inner, admin = f"eng-{tag}@acme.test", f"inner-{tag}@acme.test", "directory-admin@acme.test"
    people = ("alice", "bob", "carol", "dave")
    google = lambda u: f"ext:google.{u}@acme.test"
    directory_key = httpx.post(f"{FAKE_GOOGLE}/service-account", json={"email": f"directory-{tag}@acme-dev.iam.gserviceaccount.com"}, timeout=30).json()
    drive_key = httpx.post(f"{FAKE_GOOGLE}/service-account", json={"email": f"drive-{tag}@acme-dev.iam.gserviceaccount.com"}, timeout=30).json()
    user = lambda u, **state: {"primaryEmail": f"{u}@acme.test", "name": {"fullName": u.title()}, **state}
    member = lambda email, kind="USER": {"email": email, "type": kind, "status": "ACTIVE"}
    workspace = {"subjects": [admin], "users": [user(u) for u in people],
                 "groups": {eng: [member("carol@acme.test"), member(inner, "GROUP"), {"type": "CUSTOMER", "id": "C0"}],
                            inner: [member("bob@acme.test"), member("someone@outside.test", "EXTERNAL")]}}
    fid = f"grouped{tag}"
    folders = {f"team{tag}": {"name": "Team", "shared": True, "files": [
        {"id": fid, "name": f"Group plan {tag}.txt", "mimeType": "text/plain", "md5Checksum": f"g{tag}", "text": f"Shared with a group {tag}.",
         "permissions": [{"type": "group", "role": "reader", "emailAddress": eng.upper()}]}]}}  # Drive's case isn't the directory's
    dname, gname = f"gws-{tag}", f"gdrive-{tag}"
    dpath = f"/v1/workspaces/payments/connections/{dname}"
    body = {"name": dname, "provider": "google", "api_base": FAKE_GOOGLE, "service_account_key": json.dumps(directory_key),
            "admin_email": admin, "every_minutes": 60}

    def until(test, seconds=120):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not test():
            time.sleep(2)
        return test()

    def finds(u):
        hits = api("GET", "/v1/search", u, params={"q": tag, "scope": "org", "limit": 50}).json()["results"]
        return f"gd-{fid}" in {h["id"] for h in hits}

    def matched(u):
        with psycopg.connect(OWNER_DB) as conn:
            return bool(conn.execute("SELECT 1 FROM kl.principal_aliases WHERE source = %s AND canonical = %s AND verified_by = 'sso-email'",
                                     (google(u), f"user:{u}")).fetchone())

    def directory_synced(after):
        with psycopg.connect(OWNER_DB) as conn:
            return bool(conn.execute("SELECT 1 FROM kl.connection_containers cc JOIN kl.connections c ON c.id = cc.connection_id "
                                     "WHERE c.name = %s AND cc.cursor IS NOT NULL AND cc.cursor::timestamptz > %s", (dname, after)).fetchone())

    def now():
        with psycopg.connect(OWNER_DB) as conn:
            return conn.execute("SELECT now()").fetchone()[0]

    def resync(state):
        assert httpx.post(f"{FAKE_GOOGLE}/admin/setup", json=state, timeout=10).status_code == 200
        moment = now()
        assert api("POST", dpath + "/refresh", "olivia").status_code == 202
        assert until(lambda: directory_synced(moment)), "the directory didn't sync again"

    try:
        assert httpx.post(f"{FAKE_GOOGLE}/admin/setup", json=workspace, timeout=10).status_code == 200
        assert httpx.post(f"{FAKE_GOOGLE}/folders", json={"folders": folders}, timeout=10).status_code == 200
        refusals = {"a team admin connected a directory": ("alice", body, 403),
                    "an internal address was accepted": ("olivia", {**body, "api_base": "http://10.0.0.5"}, 422),
                    "a malformed admin was accepted": ("olivia", {**body, "admin_email": "not-an-admin"}, 422),
                    "an admin outside the company's domains was accepted": ("olivia", {**body, "admin_email": "admin@evil.test"}, 422),
                    "a reference to another team's secret was accepted": ("olivia", {**body, "service_account_key": None,
                                                                                    "credential_ref": "openbao://secret/kl/acme/finance/models/smoke-fake"}, 422)}
        for problem, (who, attempt, status) in refusals.items():
            assert api("POST", "/v1/workspaces/payments/connections/directory", who, json=attempt).status_code == status, problem
        moment = now()
        r = api("POST", "/v1/workspaces/payments/connections/directory", "olivia", json=body)
        assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
        assert until(lambda: directory_synced(moment)), "the directory never synced"
        asked = [q for q in httpx.get(f"{FAKE_GOOGLE}/admin/requests", timeout=10).json() if q["iss"] == directory_key["client_email"]]
        scopes = sorted(["https://www.googleapis.com/auth/admin.directory.user.readonly",
                         "https://www.googleapis.com/auth/admin.directory.group.readonly",
                         "https://www.googleapis.com/auth/admin.directory.group.member.readonly"])
        assert asked and all(q["aud"] == f"{FAKE_GOOGLE}/token" and q["sub"] == admin and sorted(q["scope"].split(" ")) == scopes
                             for q in asked), f"the token was asked for wrongly: {asked}"
        assert api("GET", dpath, "olivia").json()["settings"]["admin_email"] == admin, "Terraform can't read back the admin it acts as"
        for u in people:  # sign-in links each person to the Workspace account the directory reported
            api("GET", "/v1/whoami", u)
        assert all(matched(u) for u in people), "sign-in didn't match people to their Workspace accounts"
        r = api("POST", "/v1/workspaces/payments/connections/google", "alice", json={
            "name": gname, "service_account_key": json.dumps(drive_key), "api_base": FAKE_GOOGLE, "every_minutes": 60, "drives": []})
        assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
        assert until(lambda: finds("carol")), "a member of the group the file is shared with can't find it"
        assert finds("bob"), "a member of a nested group can't find a file shared with the outer group"
        assert not finds("alice") and not finds("dave"), "a file shared with a group reached people outside it"

        suspended = {**workspace, "users": [user(u, suspended=(u == "carol")) for u in people]}
        resync(suspended)
        assert until(lambda: not matched("carol")), "a suspended member kept her matched identity"
        assert not finds("carol"), "a suspended member still finds the group's file"

        regrouped = {**suspended, "groups": {**workspace["groups"], inner: [member("someone@outside.test", "EXTERNAL")]}}
        resync(regrouped)  # bob leaves the nested group: Drive is re-read now, not at its next turn
        assert until(lambda: not finds("bob")), "someone taken out of the group still finds its file"

        resync({**regrouped, "users": [user(u, suspended=(u == "carol")) for u in people if u != "dave"]})  # dave deleted outright
        assert until(lambda: not matched("dave")), "someone deleted between syncs kept their matched identity"

        assert httpx.post(f"{FAKE_GOOGLE}/admin/setup", json={**regrouped, "users": [], "broken": True}, timeout=10).status_code == 200
        assert api("POST", dpath + "/refresh", "olivia").status_code == 202  # a listing that fails is never read as everyone leaving
        with psycopg.connect(OWNER_DB) as conn:
            cid = str(conn.execute("SELECT id FROM kl.connections WHERE name = %s", (dname,)).fetchone()[0])

        def failed():
            with psycopg.connect(OWNER_DB) as conn:
                return bool(conn.execute("SELECT 1 FROM kl.jobs WHERE payload->>'connection_id' = %s AND last_error IS NOT NULL",
                                         (cid,)).fetchone())
        assert until(failed), "a directory that can't be listed didn't fail"
        assert matched("alice") and matched("bob"), "a failed listing revoked people still at the company"
    finally:
        api("DELETE", f"/v1/workspaces/payments/connections/{gname}", "alice")
        api("DELETE", dpath, "olivia")
        httpx.post(f"{FAKE_GOOGLE}/admin/setup", json={}, timeout=10)
        with psycopg.connect(OWNER_DB, autocommit=True) as conn:
            conn.execute("DELETE FROM kl.principal_aliases WHERE source = ANY(%s) AND verified_by = 'sso-email'", ([google(u) for u in people],))
        stale = {(t["user"], t["relation"], t["object"]) for u in people for t in fga.read({"object": f"identity:{google(u)[4:]}"})
                 if t["user"] == f"user:{u}"}
        if stale:
            fga.write(deletes=stale)


@check
def an_entra_directory_revokes_leavers_and_lists_groups():
    """S3, Entra as a directory: an app registration signs in with its certificate (a PS256 client assertion naming the certificate by its thumbprint, for Entra's own token address, never a client secret) and lists people and groups; a disabled or deleted account has left, and a leaver's matched access goes at once; each group's people are kept, nested groups flattened, a group without an email by its id; a next page Graph names on another host fails the sync, and nothing changes; only an org admin connects one, with a tenant and app named by id and a certificate that's the key's own"""
    from .authz import fga
    tag = secrets.token_hex(3)
    tenant = str(uuid.uuid4())
    app = httpx.post(f"{FAKE_MICROSOFT}/app", json={"tenant_id": tenant}, timeout=30).json()
    stranger = httpx.post(f"{FAKE_MICROSOFT}/app", json={"tenant_id": tenant}, timeout=30).json()  # another app's certificate
    people = ("alice", "bob", "carol", "dave")
    ids = {u: str(uuid.uuid4()) for u in people}
    microsoft = lambda u: f"ext:microsoft.{u}@acme.test"
    user = lambda u, enabled=True: {"id": ids[u], "mail": f"{u}@acme.test", "userPrincipalName": f"{u}@acme.test",
                                    "displayName": u.title(), "accountEnabled": enabled}
    team, security = str(uuid.uuid4()), str(uuid.uuid4())
    groups = {team: {"mail": f"Team-{tag}@acme.test", "name": "Team", "members": [{"user": ids["carol"]}, {"group": security}]},
              security: {"name": "Security, no email", "members": [{"user": ids["bob"]}, {"user": ids["dave"]}]}}
    tenant_state = {"users": [user(u) for u in people], "groups": groups}
    name = f"entra-{tag}"
    path = f"/v1/workspaces/payments/connections/{name}"
    body = {"name": name, "provider": "entra", "api_base": FAKE_MICROSOFT, "tenant_id": tenant, "client_id": app["client_id"],
            "certificate": app["certificate"], "every_minutes": 60}

    def until(test, seconds=120):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not test():
            time.sleep(2)
        return test()

    def matched(u):
        with psycopg.connect(OWNER_DB) as conn:
            return bool(conn.execute("SELECT 1 FROM kl.principal_aliases WHERE source = %s AND canonical = %s AND verified_by = 'sso-email'",
                                     (microsoft(u), f"user:{u}")).fetchone())

    def held_groups():
        with psycopg.connect(OWNER_DB) as conn:
            rows = conn.execute("SELECT g.group_email, g.group_id, g.member_email FROM kl.directory_groups g JOIN kl.connections c "
                                "ON c.id = g.connection_id WHERE c.name = %s ORDER BY 1, 3", (name,)).fetchall()
        return {(g, gid, m) for g, gid, m in rows}

    def synced_after(moment):
        with psycopg.connect(OWNER_DB) as conn:
            return bool(conn.execute("SELECT 1 FROM kl.connection_containers cc JOIN kl.connections c ON c.id = cc.connection_id "
                                     "WHERE c.name = %s AND cc.cursor IS NOT NULL AND cc.cursor::timestamptz > %s", (name, moment)).fetchone())

    def now():
        with psycopg.connect(OWNER_DB) as conn:
            return conn.execute("SELECT now()").fetchone()[0]

    try:
        assert httpx.post(f"{FAKE_MICROSOFT}/setup", json=tenant_state, timeout=10).status_code == 200
        refusals = {"a team admin connected a directory": ("alice", body, 403),
                    "an internal address was accepted": ("olivia", {**body, "api_base": "http://10.0.0.5"}, 422),
                    "a tenant not named by its id was accepted": ("olivia", {**body, "tenant_id": "contoso.onmicrosoft.com"}, 422),
                    "a certificate that isn't one was accepted": ("olivia", {**body, "certificate": "not a certificate"}, 422),
                    "a key that isn't the certificate's was accepted": ("olivia", {**body, "certificate": app["certificate"].split(
                        "-----BEGIN CERTIFICATE-----")[0] + "-----BEGIN CERTIFICATE-----" + stranger["certificate"].split("-----BEGIN CERTIFICATE-----")[1]}, 422),
                    "a reference to another team's secret was accepted": ("olivia", {**body, "certificate": None,
                                                                                    "credential_ref": "openbao://secret/kl/acme/finance/models/smoke-fake"}, 422)}
        for problem, (who, attempt, status) in refusals.items():
            assert api("POST", "/v1/workspaces/payments/connections/directory", who, json=attempt).status_code == status, problem
        moment = now()
        r = api("POST", "/v1/workspaces/payments/connections/directory", "olivia", json=body)
        assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
        assert until(lambda: synced_after(moment)), "the Entra directory never synced"
        asked = httpx.get(f"{FAKE_MICROSOFT}/requests", timeout=10).json()
        assert asked and all(q["alg"] == "PS256" and q["aud"] == f"{FAKE_MICROSOFT}/{tenant}/oauth2/v2.0/token" and q["client_id"] == app["client_id"]
                             and q["scope"] == "https://graph.microsoft.com/.default" for q in asked), f"the token was asked for wrongly: {asked}"
        settings = api("GET", path, "olivia").json()["settings"]
        assert settings["tenant_id"] == tenant and settings["client_id"] == app["client_id"], f"Terraform can't read back the tenant and app: {settings}"
        team_key = f"team-{tag}@acme.test"
        assert held_groups() == {(team_key, team, "bob@acme.test"), (team_key, team, "carol@acme.test"), (team_key, team, "dave@acme.test"),
                                 (security, security, "bob@acme.test"), (security, security, "dave@acme.test")}, \
            f"the groups weren't kept as Entra lists them: {held_groups()}"
        for u in people:
            api("GET", "/v1/whoami", u)
        assert all(matched(u) for u in people), "sign-in didn't match people to their Entra accounts"

        moment = now()
        httpx.post(f"{FAKE_MICROSOFT}/setup", json={**tenant_state, "users": [user(u, enabled=(u != "carol")) for u in people if u != "dave"],
                                                   "deleted": [user("dave", enabled=False)]}, timeout=10).raise_for_status()
        assert api("POST", path + "/refresh", "olivia").status_code == 202
        assert until(lambda: synced_after(moment)), "the Entra directory didn't sync again"
        assert until(lambda: not matched("carol")), "a disabled account kept its matched identity"
        assert until(lambda: not matched("dave")), "a deleted account kept its matched identity"
        assert matched("alice") and matched("bob"), "people still enabled lost their match"
        assert {m for _, _, m in held_groups()} == {"bob@acme.test"}, f"a leaver still counts in a group: {held_groups()}"

        still = [user(u, enabled=(u != "carol")) for u in people if u != "dave"]  # more than a page, so Graph names a next one
        httpx.post(f"{FAKE_MICROSOFT}/setup", json={"users": still, "groups": groups, "next_elsewhere": True}, timeout=10).raise_for_status()
        with psycopg.connect(OWNER_DB) as conn:
            cid = str(conn.execute("SELECT id FROM kl.connections WHERE name = %s", (name,)).fetchone()[0])
            conn.execute("UPDATE kl.jobs SET last_error = NULL WHERE payload->>'connection_id' = %s", (cid,))
        assert api("POST", path + "/refresh", "olivia").status_code == 202

        def failed():
            with psycopg.connect(OWNER_DB) as conn:
                return bool(conn.execute("SELECT 1 FROM kl.jobs WHERE payload->>'connection_id' = %s AND last_error IS NOT NULL",
                                         (cid,)).fetchone())
        assert until(failed), "a listing whose next page is on another host didn't fail"
        assert matched("alice") and matched("bob"), "a failed listing revoked people still at the company"
    finally:
        api("DELETE", path, "olivia")
        with psycopg.connect(OWNER_DB, autocommit=True) as conn:
            conn.execute("DELETE FROM kl.principal_aliases WHERE source = ANY(%s) AND verified_by = 'sso-email'", ([microsoft(u) for u in people],))
        stale = {(t["user"], t["relation"], t["object"]) for u in people for t in fga.read({"object": f"identity:{microsoft(u)[4:]}"})
                 if t["user"] == f"user:{u}"}
        if stale:
            fga.write(deletes=stale)


@check
def a_secret_the_team_keeps_in_gcp_is_read_where_it_lives():
    """GCP Secret Manager references (ADR 0011): a connection can name a secret its team keeps in GCP, which the runner reads as its own workload identity, so Aerugo never holds the token; a secret named for another team, a look-alike team, or a project the deployment doesn't allow is refused"""
    tag = secrets.token_hex(3).upper()
    key, secret = f"GCP{tag}", f"kl_acme_payments_jira{tag.lower()}"
    setup = {"projects": {key: {"name": "Through GCP", "company_wide": True, "browsers": [], "issues": [
        {"key": f"{key}-1", "summary": f"Read with a GCP secret {tag}", "description": f"Only the reference was stored {tag}."}]}}}
    assert httpx.post(f"{FAKE_JIRA}/setup", json=setup, timeout=10).status_code == 200
    assert httpx.post(f"{FAKE_GCP}/secrets", json={"secrets": {secret: {"email": "jira-bot@acme.test", "api_token": "smoke-jira-token"}}},
                      timeout=10).status_code == 200
    ref, name = f"gcpsm://projects/acme-dev/secrets/{secret}", f"gcp-{tag.lower()}"
    body = {"name": name, "api_base": FAKE_JIRA, "credential_ref": ref, "projects": [key], "company_site": True}

    def finds(user, issue):
        hits = api("GET", "/v1/search", user, params={"q": tag, "scope": "org", "limit": 50}).json()["results"]
        return f"jira-{issue}" in {h["id"] for h in hits}

    try:
        for bad in (f"gcpsm://projects/acme-dev/secrets/kl_acme_finance_jira{tag.lower()}",  # another team's
                    f"gcpsm://projects/acme-dev/secrets/kl_acme_payments-x_jira{tag.lower()}",  # a look-alike team's
                    f"gcpsm://projects/someone-else/secrets/{secret}"):  # a project the deployment doesn't allow
            assert api("POST", "/v1/workspaces/payments/connections/jira", "alice", json={**body, "credential_ref": bad}).status_code == 422, \
                f"accepted {bad}"
        r = api("POST", "/v1/workspaces/payments/connections/jira", "alice", json=body)
        assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and not finds("alice", f"{key}-1"):
            time.sleep(3)
        assert finds("alice", f"{key}-1"), "the issue never arrived: the runner couldn't read the secret where it lives"
        got = api("GET", f"/v1/workspaces/payments/connections/{name}", "alice").json()
        assert got["settings"]["credential_ref"] == ref, f"the reference isn't read back: {got.get('settings')}"
    finally:
        api("DELETE", f"/v1/workspaces/payments/connections/{name}", "alice")


@check
def people_are_matched_to_their_source_accounts_by_verified_email():
    """Identity matching (ADR 0011): signing in links a person to the source accounts that report their SSO-verified company email, with the grant; an account someone else holds stays theirs; nobody else is matched to it; and a match that stops holding is removed, grant and all"""
    from .authz import fga
    tag = secrets.token_hex(3)
    mine, held = f"557058.match{tag}", f"557058.held{tag}"
    grant = lambda external: fga.read({"user": "user:alice", "relation": "owner", "object": f"identity:atlassian.{external}"})
    with psycopg.connect(OWNER_DB, autocommit=True) as conn:
        org = conn.execute("SELECT id FROM kl.orgs WHERE slug = 'acme'").fetchone()[0]
        conn.execute("INSERT INTO kl.source_identities (org_id, source, external_id, email) "
                     "VALUES (%s, 'atlassian', %s, 'alice@acme.test'), (%s, 'atlassian', %s, 'alice@acme.test')", (org, mine, org, held))
        conn.execute("INSERT INTO kl.principal_aliases (org_id, source, canonical, verified_by) VALUES (%s, %s, 'user:carol', 'smoke')",
                     (org, f"ext:atlassian.{held}"))  # carol holds this one, though it reports alice's email
    try:
        identities = api("GET", "/v1/whoami", "alice").json()["identities"]
        assert f"ext:atlassian.{mine}" in identities and grant(mine), f"alice wasn't matched to her account: {identities}"
        assert f"ext:atlassian.{held}" not in identities, "alice took over an account carol holds"
        assert f"ext:atlassian.{mine}" not in api("GET", "/v1/whoami", "bob").json()["identities"], "bob was matched to alice's account"
        with psycopg.connect(OWNER_DB, autocommit=True) as conn:  # Jira now reports another email for that account
            conn.execute("UPDATE kl.source_identities SET email = 'someone@acme.test' WHERE source = 'atlassian' AND external_id = %s", (mine,))
        identities = api("GET", "/v1/whoami", "alice").json()["identities"]
        assert f"ext:atlassian.{mine}" not in identities and not grant(mine), "a match that stopped holding survived"
    finally:
        with psycopg.connect(OWNER_DB, autocommit=True) as conn:
            conn.execute("DELETE FROM kl.principal_aliases WHERE source LIKE %s", (f"ext:atlassian.%{tag}",))
            conn.execute("DELETE FROM kl.source_identities WHERE source = 'atlassian' AND external_id LIKE %s", (f"%{tag}",))
        stale = {(t["user"], t["relation"], t["object"]) for e in (mine, held) for t in fga.read({"object": f"identity:atlassian.{e}"})}
        if stale:
            fga.write(deletes=stale)


@check
def a_document_reads_exactly_when_it_lists():
    """Reading a document in its space: its text, in sections, comes to exactly the people the documents list shows it to; anyone else gets a 404, as if it weren't there"""
    r = api("GET", "/v1/documents/pay-runbook/content", "alice")
    assert r.status_code == 200 and r.json()["title"] and r.json()["sections"], f"{r.status_code} {r.text[:200]}"
    assert any("page the payments on-call" in s["body"].lower() for s in r.json()["sections"]), "the runbook's text didn't come back"
    for user in ("bob", "dave"):
        assert api("GET", "/v1/documents/pay-runbook/content", user).status_code == 404, f"{user} read Payments' runbook"
    assert api("GET", "/v1/documents/legal-hold/content", "dave").status_code == 200, "dave can't read Legal's hold"
    assert api("GET", "/v1/documents/legal-hold/content", "alice").status_code == 404, "alice read Legal's private hold"
    assert api("GET", "/v1/documents/no-such-document/content", "alice").status_code == 404, "a missing document didn't look missing"


@check
def answers_carry_confidence_from_signals():
    """Confidence (slice K9): from what an answer cites, never from the model's opinion of itself: a verified page cited is high; one raw source is low; citing a superseded page lowers it; no answer at all is low, and each level says why"""
    from .confidence import confidence
    assert confidence(False, [], [])["level"] == "low"
    assert confidence(True, [], [])["reasons"] == ["The answer doesn't cite any source."]
    assert confidence(True, [{"id": "a", "tier": "gold"}], [])["level"] == "high"
    assert confidence(True, [{"id": "a", "tier": "evidence"}], [])["level"] == "low"
    lowered = confidence(True, [{"id": "q3", "tier": "gold"}, {"id": "q2", "tier": "silver"}], [("q3", "q2")])
    assert lowered["level"] == "medium" and any("superseded" in r for r in lowered["reasons"]), lowered


@check
def the_walk_never_carries_anyone_past_what_they_may_read():
    """The walk in search (slice K8): a page a result wrote down comes along, authorized on its own; a reference from a page someone can read to one they can't brings nothing"""
    with psycopg.connect(OWNER_DB, autocommit=True) as conn:
        ids = dict(conn.execute("SELECT external_key, id FROM kl.documents WHERE external_key IN ('handbook', 'legal-hold')").fetchall())
        conn.execute("INSERT INTO kl.reference_links (org_id, source_id, target_id, signal, strength, evidence) "
                     "SELECT org_id, %s, %s, 'identifier', 0.9, 'smoke-k8' FROM kl.documents WHERE id = %s ON CONFLICT DO NOTHING",
                     (ids["handbook"], ids["legal-hold"], ids["handbook"]))
    try:
        for q in ("handbook", "employee", "policy"):
            hits = [h["id"] for h in api("GET", "/v1/search", "alice", params={"q": q, "limit": 50}).json()["results"]]
            assert "legal-hold" not in hits, f"a reference walked alice into Legal's private hold ({q!r})"
    finally:
        with psycopg.connect(OWNER_DB, autocommit=True) as conn:
            conn.execute("DELETE FROM kl.reference_links WHERE evidence = 'smoke-k8'")


@check
def mcp_graph_tools_follow_the_same_permissions():
    """MCP graph tools (slice K12): an agent gets who to ask and related pages through the same permission path as the app; a page its user can't read is not found"""
    people = mcp_call("alice", "who_to_ask", {"key": "kb-sev1-playbook"})
    assert [p["user"] for p in people["people"]][:1] == ["alice"], people
    assert mcp_call("alice", "related", {"key": "kb-sev1-playbook"})["related"], "alice's related pages came back empty"
    for user in ("bob", "dave"):
        for tool in ("who_to_ask", "related"):
            try:
                mcp_call(user, tool, {"key": "kb-sev1-playbook"})
            except AssertionError as e:
                assert "not found" in str(e), f"{user}, {tool}: {e}"
            else:
                raise AssertionError(f"{user} got {tool} for a page they can't read")


@check
def search_follows_lineage_only_through_the_askers_teams():
    """Q4, lineage in search and Ask: a question naming a job ("what breaks if we retire it?") finds the pages describing what it feeds, through the lineage the asker's own teams reported, each page post-checked; a result says what the assets it describes feed; someone outside the reporting team gets no lineage hop, even to a page they may read"""
    from .authz import fga
    tag = uuid.uuid4().hex[:8]
    job, table, dash = f"Q4_LOAD_{tag}", f"Q4_TABLE_{tag}", f"Q4_DASH_{tag}"
    events = {"events": [{"job": {"namespace": "q4", "name": job}, "inputs": [], "outputs": [{"namespace": "q4", "name": table}]},
                         {"job": {"namespace": "q4", "name": f"q4_report_{tag}"}, "inputs": [{"namespace": "q4", "name": table}],
                          "outputs": [{"namespace": "q4", "name": dash}]}]}
    pages = {f"q4-{tag}-table": ("The orders table", table, ["user:alice"]), f"q4-{tag}-dash": ("The revenue dashboard", dash, ["user:alice", "user:bob"])}
    tuples = {(p, "viewer", f"document:{key}") for key, (_, _, readers) in pages.items() for p in readers}
    try:
        assert api("POST", "/v1/workspaces/payments/lineage", "alice", json=events).status_code == 200
        with psycopg.connect(OWNER_DB) as conn:
            org = acme_org_id()
            payments = conn.execute("SELECT id FROM kl.workspaces WHERE slug = 'payments'").fetchone()[0]
            for key, (title, alias, readers) in pages.items():
                doc = conn.execute("INSERT INTO kl.documents (org_id, source_kind, source_uri, source_version, external_key, title, content_hash, "
                                   "classification, acl_principals, tier) VALUES (%s, 'okf', %s, 'v1', %s, %s, %s, 'internal', %s, 'silver') RETURNING id",
                                   (org, f"https://kb.acme.test/{key}", key, title, key, readers)).fetchone()[0]
                conn.execute("INSERT INTO kl.chunks (org_id, document_id, ordinal, heading_path, body, acl_principals) VALUES (%s, %s, 0, NULL, %s, %s)",
                             (org, doc, f"{title}: what it holds, and who looks after it.", readers))
                conn.execute("INSERT INTO kl.workspace_documents (workspace_id, document_id, org_id) VALUES (%s, %s, %s)", (payments, doc, org))
                conn.execute("INSERT INTO kl.concepts (org_id, concept_key, tier, document_id, title, acl_principals, aliases) "
                             "VALUES (%s, %s, 'silver', %s, %s, %s, %s)", (org, key, doc, title, readers, [alias]))
        fga.write(writes=tuples)

        def results(user):
            r = api("GET", "/v1/search", user, params={"q": f"what breaks if we retire {job}", "scope": "org", "limit": 20})
            assert r.status_code == 200, r.text[:200]
            return {h["id"]: h for h in r.json()["results"]}
        mine = results("alice")
        assert {f"q4-{tag}-table", f"q4-{tag}-dash"} <= set(mine) and "lineage" in mine[f"q4-{tag}-dash"]["matched"], \
            f"the walk didn't reach the pages describing what the job feeds: {sorted(mine)}"
        assert dash in mine[f"q4-{tag}-table"].get("reaches", []), f"a result didn't say what its asset feeds: {mine[f'q4-{tag}-table']}"
        theirs = results("bob")  # bob may read the dashboard's page, but isn't on the team that reported the lineage
        assert f"q4-{tag}-table" not in theirs, "the walk passed through a page the asker can't read"
        assert f"q4-{tag}-dash" not in theirs or "lineage" not in theirs[f"q4-{tag}-dash"]["matched"], "another team's lineage carried a search"
    finally:
        doomed = {t for t in tuples if fga.read({"user": t[0], "relation": t[1], "object": t[2]})}
        if doomed:
            fga.write(deletes=doomed)
        with psycopg.connect(OWNER_DB) as conn:
            conn.execute("DELETE FROM kl.documents WHERE external_key = ANY(%s)", (list(pages),))
            conn.execute("DELETE FROM kl.assets WHERE namespace = 'q4' AND name LIKE %s", (f"%{tag}",))


@check
def the_graph_tools_walk_only_what_the_caller_may_see():
    """A1, the graph as tools: a node's neighbors, the shortest path between two nodes, and why two nodes are linked each come with their links' provenance; none passes through, or names, a node the caller can't see; a hidden end is not found; the same tools over MCP"""
    curated_ready()
    mine = api("GET", "/v1/graph/neighbors", "alice", params={"key": "kb-sev1-playbook"})
    assert mine.status_code == 200, mine.text[:200]
    near = {n["id"]: n for n in mine.json()["neighbors"]}
    assert "kb-ledger" in near and all(l.get("kind") for l in near["kb-ledger"]["links"]), f"a neighbor came without its link: {near.get('kb-ledger')}"
    hidden = [k for k in near if k.startswith("kb-") and api("GET", f"/v1/concepts/{k}", "alice").status_code != 200]
    assert not hidden, f"a neighbor alice can't read was named: {hidden}"
    walk = api("GET", "/v1/graph/path", "alice", params={"from": "kb-sev1-playbook", "to": "kb-incident-comms"}).json()
    assert walk["path"] and walk["path"][0]["id"] == "kb-sev1-playbook" and walk["path"][-1]["id"] == "kb-incident-comms", walk
    assert len(walk["hops"]) == len(walk["path"]) - 1 and all(h.get("kind") for h in walk["hops"]), walk["hops"]
    for user, page in (("alice", "kb-legal-escalation"), ("bob", "kb-sev1-playbook")):
        if api("GET", f"/v1/concepts/{page}", user).status_code == 404:  # a page they can't read
            assert api("GET", "/v1/graph/path", user, params={"from": page, "to": "kb-incident-comms"}).status_code == 404, f"{user} walked from {page}"
            assert api("GET", "/v1/graph/neighbors", user, params={"key": page}).status_code == 404, f"{user} saw {page}'s neighbors"
    why = api("GET", "/v1/graph/why", "alice", params={"a": "kb-sev1-playbook", "b": "kb-ledger"}).json()
    assert why["links"] and all("kind" in l for l in why["links"]), f"why two pages are linked wasn't said: {why}"
    over = mcp_call("alice", "why_linked", {"a": "kb-sev1-playbook", "b": "kb-ledger"})
    assert over["links"] == why["links"], f"MCP's answer differs from the API's: {over}"
    assert mcp_call("alice", "path", {"source": "kb-sev1-playbook", "target": "kb-incident-comms"})["path"], "MCP's path came back empty"


@check
def the_change_feed_shows_only_what_each_caller_could_read():
    """A3, the change feed: a document added, its readers changed, and removed each show up in order, from a cursor, to someone who could read it; someone who never could sees none of it; someone who lost it sees none of it once the post-check says they can't read it"""
    from .authz import fga
    tag = uuid.uuid4().hex[:6]
    key = f"a3-{tag}"
    tuples = {("user:alice", "viewer", f"document:{key}"), ("user:bob", "viewer", f"document:{key}")}

    def feed(user, since):
        r = api("GET", "/v1/changes", user, params={"since": since, "limit": 500})
        assert r.status_code == 200, r.text[:200]
        return r.json()
    start = feed("olivia", 0)
    while start["more"]:
        start = feed("olivia", start["next"])
    since = start["next"]
    try:
        fga.write(writes=tuples)
        with psycopg.connect(OWNER_DB) as conn:
            org = acme_org_id()
            conn.execute("INSERT INTO kl.documents (org_id, source_kind, source_uri, source_version, external_key, title, content_hash, "
                         "classification, acl_principals) VALUES (%s, 'seed', %s, 'v1', %s, %s, 'h1', 'internal', %s)",
                         (org, f"https://kb.acme.test/{key}", key, f"Feed {tag}", ["user:alice", "user:bob"]))
            conn.execute("UPDATE kl.documents SET acl_principals = %s, content_hash = 'h2' WHERE external_key = %s", (["user:alice"], key))
        fga.write(deletes={("user:bob", "viewer", f"document:{key}")})
        with psycopg.connect(OWNER_DB) as conn:
            conn.execute("UPDATE kl.documents SET content_hash = 'h3' WHERE external_key = %s", (key,))
            conn.execute("DELETE FROM kl.documents WHERE external_key = %s", (key,))
        hers = [(c["key"], c["change"]) for c in feed("alice", since)["changes"] if c["key"] == key]
        assert hers == [(key, "added"), (key, "changed"), (key, "changed"), (key, "removed")], f"the feed lost or reordered changes: {hers}"
        his = [(c["key"], c["change"]) for c in feed("bob", since)["changes"] if c["key"] == key]
        assert his == [], f"someone who lost a document still saw its changes, or saw it before losing it from the post-check: {his}"
        assert not [c for c in feed("dave", since)["changes"] if c["key"] == key], "someone who never could read it saw its changes"
    finally:
        doomed = {t for t in tuples if fga.read({"user": t[0], "relation": t[1], "object": t[2]})}
        if doomed:
            fga.write(deletes=doomed)
        with psycopg.connect(OWNER_DB) as conn:
            conn.execute("DELETE FROM kl.documents WHERE external_key = %s", (key,))


@check
def a_team_exports_its_curated_pages_as_an_okf_repository():
    """A4, OKF export: a team member downloads the team's curated pages as an OKF repository (a zip of each page's latest version at its own path, front matter and all), only those they may read; someone outside the team gets nothing"""
    import io
    import zipfile
    curated_ready()
    r = api("GET", "/v1/workspaces/payments/export.okf", "alice")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip", f"{r.status_code} {r.text[:200]}"
    files = zipfile.ZipFile(io.BytesIO(r.content))
    texts = {n: files.read(n).decode() for n in files.namelist()}
    sev1 = next((t for t in texts.values() if "id: kb-sev1-playbook" in t), None)
    assert sev1 and sev1.startswith("---") and "owners:" in sev1, f"the export lacks the Sev1 playbook as written: {sorted(texts)}"
    assert all(n.endswith(".md") and ".." not in n and not n.startswith("/") for n in texts), sorted(texts)
    assert api("GET", "/v1/workspaces/payments/export.okf", "dave").status_code == 404, "someone outside the team exported its pages"


@check
def lineage_shows_only_to_its_own_team():
    """Systems and data (slice K3): a team's lineage, pushed in OpenLineage's shape, becomes datasets, jobs, and the links between them on that team's map, and impact follows it downstream, nearest first; nobody outside the team sees any of it, and a non-admin can't push"""
    import uuid
    tag = uuid.uuid4().hex[:8]
    events = {"events": [{"job": {"namespace": "smoke", "name": f"load_{tag}"},
                          "inputs": [{"namespace": "smoke", "name": f"src_{tag}"}],
                          "outputs": [{"namespace": "smoke", "name": f"table_{tag}"}]},
                         {"job": {"namespace": "smoke", "name": f"report_{tag}"},
                          "inputs": [{"namespace": "smoke", "name": f"table_{tag}"}],
                          "outputs": [{"namespace": "smoke", "name": f"dash_{tag}"}]}]}
    impact = {"namespace": "smoke", "name": f"load_{tag}", "kind": "job"}
    try:
        assert api("POST", "/v1/workspaces/payments/lineage", "bob", json=events).status_code in (403, 404), "a non-admin pushed lineage"
        r = api("POST", "/v1/workspaces/payments/lineage", "alice", json=events)
        assert r.status_code == 200 and r.json()["edges"] == 4, f"{r.status_code} {r.text[:200]}"
        assert api("POST", "/v1/workspaces/payments/lineage", "alice", json=events).json()["edges"] == 4, "pushing again failed"
        g = api("GET", "/v1/graph", "alice").json()
        ids = {n["id"] for n in g["nodes"]}
        assert {f"asset:job:smoke/load_{tag}", f"asset:dataset:smoke/table_{tag}", f"asset:dataset:smoke/dash_{tag}"} <= ids, \
            "alice's map lacks her team's lineage"
        assert sum(1 for n in g["nodes"] if tag in n["id"]) == 5, "pushing the same events twice duplicated assets"
        assert any(e["kind"] == "lineage" and e["source"] == f"asset:job:smoke/load_{tag}" and e["target"] == f"asset:dataset:smoke/table_{tag}"
                   for e in g["edges"]), "a job's write isn't a lineage link"
        down = [(d["name"], d["hops"]) for d in api("GET", "/v1/lineage/impact", "alice", params=impact).json()["downstream"]]
        assert down == [(f"table_{tag}", 1), (f"report_{tag}", 2), (f"dash_{tag}", 3)], down
        for user in ("bob", "dave"):
            g = api("GET", "/v1/graph", user).json()
            assert not [n for n in g["nodes"] if tag in n["id"]], f"{user} saw another team's lineage"
            assert api("GET", "/v1/lineage/impact", user, params=impact).status_code == 404, f"{user} walked another team's lineage"
    finally:
        with psycopg.connect(OWNER_DB, autocommit=True) as conn:
            conn.execute("DELETE FROM kl.assets WHERE namespace = 'smoke' AND name LIKE %s", (f"%{tag}",))


@check
def who_to_ask_comes_from_the_page_and_never_leaks():
    """Who to ask (slice K6): for a page someone may read, the person who verified it, then its owning team's members (listed only for a team the asker is on), each with reasons; a page they can't read is a 404, and a team they aren't on is named without its people"""
    from .people import who_to_ask
    r = api("GET", "/v1/concepts/kb-sev1-playbook/people", "alice")
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
    people = r.json()["people"]
    assert [p["user"] for p in people][:2] == ["alice", "carol"], people
    assert people[0]["reasons"][0].startswith("verified it on") and any("owning team" in x for x in people[1]["reasons"]), people
    for user in ("bob", "dave"):
        assert api("GET", "/v1/concepts/kb-sev1-playbook/people", user).status_code == 404, \
            f"{user} learned who to ask about a page they can't read"
    named = who_to_ask({"x-kl": {"owners": ["group:legal"]}}, {}, [])
    assert named == {"people": [], "teams": [{"team": "legal", "reason": "owns it", "members_shown": False}], "left": []}, named


@check
def current_knowledge_ranks_before_what_it_replaced():
    """Current knowledge first (slice K5): among the results someone may see, a page another result supersedes ranks just after it; chains settle newest first, a replacement outside the results changes nothing, and a cycle can't loop"""
    from .ranking import current_first
    keys = lambda rows: [r["external_key"] for r in rows]
    r = lambda *ks: [{"external_key": k} for k in ks]
    assert keys(current_first(r("q2", "x", "q3"), [("q3", "q2")])) == ["x", "q3", "q2"]
    assert keys(current_first(r("q3", "x", "q2"), [("q3", "q2")])) == ["q3", "x", "q2"], "an ordered list moved"
    assert keys(current_first(r("a", "b", "c"), [("b", "a"), ("c", "b")])) == ["c", "b", "a"], "a chain didn't settle"
    assert keys(current_first(r("q2", "x"), [("q3", "q2")])) == ["q2", "x"], "a hidden replacement changed the order"
    assert len(current_first(r("a", "b"), [("a", "b"), ("b", "a")])) == 2, "a cycle lost a result"


@check
def recorded_references_link_what_a_document_names():
    """Recorded links (slice K2): a document that writes another's identifier or address links to it, with its provenance; never to itself, never through a plain word or an identifier two pages share; and a reference is shown only to someone who can read both of its documents"""
    from .links import identifier_names, references
    names = identifier_names([{"external_key": "kb-order-hist", "aliases": ["order_hist", "order history table"]},
                              {"external_key": "kb-order-migration", "aliases": ["edp-412"]},
                              {"external_key": "kb-edp-orders", "aliases": ["edp.orders"]},
                              {"external_key": "kb-ledger", "aliases": ["ledger"]},
                              {"external_key": "kb-a", "aliases": ["shared_name"]}, {"external_key": "kb-b", "aliases": ["shared_name"]}])
    assert names == {"order_hist": "kb-order-hist", "edp-412": "kb-order-migration", "edp.orders": "kb-edp-orders"}, names
    urls = {"https://jira.acme.test/browse/EDP-412": "kb-order-migration"}
    found = references("Reads ORDER_HIST; see EDP-412 at https://jira.acme.test/browse/EDP-412/#c1, and edp.orders. "
                       "The ledger, ord_load.sql, ORD_LOAD, and shared_name are not links.", names, urls, own="kb-sales-dashboard")
    assert found == {("kb-order-hist", "identifier"): "ORDER_HIST", ("kb-order-migration", "identifier"): "EDP-412",
                     ("kb-order-migration", "url"): "https://jira.acme.test/browse/EDP-412",
                     ("kb-edp-orders", "identifier"): "edp.orders"}, found
    assert references("ORDER_HIST loads nightly", names, urls, own="kb-order-hist") == {}, "a page linked to itself"
    # Visibility: a reference is read only when both of its documents are (RLS, then the map). The runbook is
    # Payments' (alice, carol), the handbook everyone's, and the hold Legal's, which is private (dave).
    with psycopg.connect(OWNER_DB, autocommit=True) as conn:
        ids = dict(conn.execute("SELECT external_key, id FROM kl.documents "
                                "WHERE external_key IN ('pay-runbook', 'handbook', 'legal-hold')").fetchall())
        for source, target in (("pay-runbook", "handbook"), ("pay-runbook", "legal-hold")):
            conn.execute("INSERT INTO kl.reference_links (org_id, source_id, target_id, signal, strength, evidence) "
                         "SELECT org_id, %s, %s, 'identifier', 0.9, 'smoke-k2' FROM kl.documents WHERE id = %s "
                         "ON CONFLICT DO NOTHING", (ids[source], ids[target], ids[source]))
    try:
        def references_of(user):
            g = api("GET", "/v1/graph", user).json()
            return {(e["source"], e["target"]): e for e in g["edges"] if e["kind"] == "reference" and e.get("evidence") == "smoke-k2"}
        alice = references_of("alice")
        assert ("pay-runbook", "handbook") in alice and alice[("pay-runbook", "handbook")]["signal"] == "identifier", f"alice: {alice}"
        assert ("pay-runbook", "legal-hold") not in alice, "alice saw a reference to a private team's document"
        for user in ("bob", "dave"):  # bob can't read the runbook; dave can read the hold, but not the runbook
            assert not references_of(user), f"{user} saw a reference with an end they can't read"
    finally:
        with psycopg.connect(OWNER_DB, autocommit=True) as conn:
            conn.execute("DELETE FROM kl.reference_links WHERE evidence = 'smoke-k2'")


@check
def connections_change_in_place_and_removal_keeps_what_others_hold():
    """Connections (slice T2): a team's admins pause one, change what it brings in, and rotate a Google account's secret and token (both or neither); removing one retires what only that team held, its authorization first, and leaves what another team still holds through its own source"""
    import uuid
    from .authz import fga
    tag = uuid.uuid4().hex[:8]
    ours, both, name, legal = f"drive{tag}a", f"drive{tag}b", f"t2-{tag}", f"t2-legal-{tag}"
    only_key, shared_key = f"gd-only{tag}", f"gd-shared{tag}"
    org_wide = [{"type": "domain", "role": "reader", "domain": "acme.test"}]
    for drive, fid, title in ((ours, f"only{tag}", "Payments only"), (both, f"shared{tag}", "Shared runbook")):
        httpx.post(f"{FAKE_GOOGLE}/files", json={"drive": drive, "name": title, "files": [
            {"id": fid, "name": f"{title} {tag}", "mimeType": "application/vnd.google-apps.document", "version": "1",
             "text": f"{title} {tag}\n{title} body {tag}", "permissions": org_wide}]}, timeout=10).raise_for_status()  # a Doc that's only a title has no text to index
    body = {"client_id": "fake-client.apps.googleusercontent.com", "client_secret": "fake-secret", "refresh_token": "smoke-refresh",
            "domain": "acme.test", "api_base": FAKE_GOOGLE}

    def teams_of(key):
        with psycopg.connect(OWNER_DB) as conn:
            return {r[0] for r in conn.execute("SELECT w.slug FROM kl.documents d JOIN kl.workspace_documents wd ON wd.document_id = d.id "
                                               "JOIN kl.workspaces w ON w.id = wd.workspace_id WHERE d.external_key = %s", (key,))}

    def until(test, seconds=120):
        deadline = time.monotonic() + seconds
        while not test() and time.monotonic() < deadline:
            time.sleep(2)
        return test()

    try:
        assert api("POST", "/v1/workspaces/payments/connections/google", "alice", json={**body, "name": name, "drives": [ours, both]}).status_code == 201
        assert api("POST", "/v1/workspaces/legal/connections/google", "olivia", json={**body, "name": legal, "drives": [both]}).status_code == 201
        assert until(lambda: teams_of(only_key) == {"payments"} and teams_of(shared_key) == {"payments", "legal"}), \
            f"the drives didn't index as expected: {teams_of(only_key)} {teams_of(shared_key)}"
        path = f"/v1/workspaces/payments/connections/{name}"
        assert api("PATCH", path, "bob", json={"enabled": False}).status_code in (403, 404), "a non-admin paused a connection"
        assert api("PATCH", path, "alice", json={"client_secret": "only-half"}).status_code == 422, "a rotation took one secret of two"
        assert api("PATCH", path, "alice", json={"repositories": ["acme/x"]}).status_code == 422, "a Google connection took repositories"
        r = api("PATCH", path, "alice", json={"enabled": False})
        assert r.status_code == 200 and r.json()["status"] == "disabled", f"pause: {r.status_code} {r.text[:200]}"
        r = api("PATCH", path, "alice", json={"enabled": True, "client_secret": "rotated-secret", "refresh_token": "rotated-refresh"})
        assert r.status_code == 200 and r.json()["status"] == "pending" and set(r.json()["changed"]) == {"credentials", "enabled"}, r.text[:200]
        with psycopg.connect(OWNER_DB) as conn:
            details = conn.execute("SELECT details::text FROM kl.audit_events WHERE action = 'connection.update' AND details->>'name' = %s "
                                   "ORDER BY at DESC LIMIT 1", (name,)).fetchone()[0]
        assert "rotated" not in details and "credentials" in details, "the audit recorded a secret, or not what changed"
        assert api("DELETE", path, "bob").status_code in (403, 404), "a non-admin removed a connection"
        assert api("DELETE", path, "alice").status_code == 202
        assert api("GET", path, "alice").status_code == 404, "a removed connection still answers"
        assert until(lambda: not teams_of(only_key) and teams_of(shared_key) == {"legal"}), \
            f"removal left the wrong things: only-ours in {teams_of(only_key)}, shared in {teams_of(shared_key)}"
        assert not fga.read({"object": f"document:{only_key}"}), "a removed document is still authorized"
        assert fga.read({"object": f"document:{shared_key}"}), "a document another team holds lost its readers"
    finally:
        for ws, n, who in (("payments", name, "alice"), ("legal", legal, "olivia")):
            api("DELETE", f"/v1/workspaces/{ws}/connections/{n}", who)


@check
def google_drive_syncs_each_files_readers():
    """Google Drive: Docs and Sheets index with each file's own readers; the org's domain is org-wide, a link grants nothing; Drive revokes and deletes carry over"""
    import uuid
    from .authz import fga
    org, tag = acme_org_id(), uuid.uuid4().hex[:8]
    drive, name, bad = f"drive{tag}", f"gdrive-{tag}", f"gdrive-bad-{tag}"
    doc_key, sheet_key = f"gd-doc{tag}", f"gd-sheet{tag}"  # the link-shared file must never be indexed
    files = [
        {"id": f"doc{tag}", "name": f"Oncall handbook {tag}", "mimeType": "application/vnd.google-apps.document", "version": "1",
         "text": f"Oncall handbook {tag}\nPage the payments on-call before anything else.",
         "permissions": [{"type": "user", "role": "reader", "emailAddress": "Alice@acme.test"}]},
        {"id": f"sheet{tag}", "name": f"Budget {tag}", "mimeType": "application/vnd.google-apps.spreadsheet", "version": "1",
         "text": f"team,budget {tag}\npayments,200", "permissions": [{"type": "domain", "role": "reader", "domain": "acme.test"}]},
        {"id": f"link{tag}", "name": f"notes {tag}.txt", "mimeType": "text/plain", "version": "1", "size": "40",
         "text": f"Link-shared notes {tag}", "permissions": [{"type": "anyone", "role": "reader"}]}]

    def drive_has(spec):
        httpx.post(f"{FAKE_GOOGLE}/files", json={"drive": drive, "name": "Engineering", "files": spec}, timeout=10).raise_for_status()

    def indexed():
        with psycopg.connect(OWNER_DB) as conn:
            return {r[0] for r in conn.execute("SELECT external_key FROM kl.documents WHERE source_kind = 'gdrive' "
                                               "AND container_ref = %s", (drive,))}

    def settles(keys):
        deadline = time.monotonic() + 120
        while indexed() != keys and time.monotonic() < deadline:
            time.sleep(2)
        return indexed() == keys

    def finds(user, key):
        r = api("GET", "/v1/search", user, params={"q": tag, "scope": "org", "limit": 50})
        return key in {hit["id"] for hit in r.json()["results"]}

    drive_has(files)
    link = ("user:alice", "owner", "identity:google.alice@acme.test")  # alice has linked her Google identity; bob hasn't
    with psycopg.connect(OWNER_DB) as conn:
        conn.execute("INSERT INTO kl.principal_aliases (org_id, source, canonical, verified_by) VALUES (%s, %s, 'user:alice', 'smoke') "
                     "ON CONFLICT DO NOTHING", (org, "ext:google.alice@acme.test"))
    if not fga.read({"user": link[0], "relation": link[1], "object": link[2]}):
        fga.write(writes={link})
    body = {"name": name, "client_id": "fake-client.apps.googleusercontent.com", "client_secret": "fake-secret",
            "refresh_token": "smoke-refresh", "domain": "acme.test", "drives": [drive], "api_base": FAKE_GOOGLE}
    try:
        assert api("POST", "/v1/workspaces/payments/connections/google", "bob", json=body).status_code in (403, 404), \
            "a non-admin connected a drive"
        r = api("POST", "/v1/workspaces/payments/connections/google", "alice", json={**body, "api_base": "https://evil.example"})
        assert r.status_code == 422, "a connection could name any server"
        r = api("POST", "/v1/workspaces/payments/connections/google", "alice", json=body)
        assert r.status_code == 201, f"connect: {r.status_code} {r.text[:200]}"
        assert settles({doc_key, sheet_key}), f"the drive didn't index as expected (and the link-shared file not at all): {sorted(indexed())}"
        assert finds("alice", doc_key) and not finds("bob", doc_key), "the Doc reached someone it isn't shared with"
        assert finds("alice", sheet_key) and finds("bob", sheet_key), "a file shared with the org's domain should reach the whole org"
        files[0]["permissions"] = [{"type": "user", "role": "reader", "emailAddress": "bob@acme.test"}]  # bob hasn't linked
        drive_has([files[0], files[2]])  # alice's access revoked, the Sheet deleted
        assert api("POST", f"/v1/workspaces/payments/connections/{name}/refresh", "alice").status_code == 202
        assert settles({doc_key}), f"the deleted Sheet is still indexed: {sorted(indexed())}"
        deadline = time.monotonic() + 60
        while finds("alice", doc_key) and time.monotonic() < deadline:
            time.sleep(2)
        assert not finds("alice", doc_key), "revoked in Drive, but alice still finds the Doc"
        r = api("POST", "/v1/workspaces/payments/connections/google", "alice",
                json={**body, "name": bad, "refresh_token": "not-the-token"})
        assert r.status_code == 201
        deadline, state = time.monotonic() + 90, None  # a failed job is retried with backoff: its first error is the answer
        while time.monotonic() < deadline and not (state and (state[1] or state[0] == "done")):
            with psycopg.connect(OWNER_DB) as conn:
                state = conn.execute("SELECT j.status, j.last_error FROM kl.jobs j JOIN kl.connections c "
                                     "ON c.id::text = j.payload->>'connection_id' WHERE c.name = %s AND j.kind = 'connection.discover' "
                                     "ORDER BY j.created_at DESC LIMIT 1", (bad,)).fetchone()
            time.sleep(1)
        assert state and state[0] != "done" and state[1], f"a connection with a wrong refresh token discovered drives (job: {state})"
    finally:
        with psycopg.connect(OWNER_DB) as conn:
            keys = [r[0] for r in conn.execute("SELECT external_key FROM kl.documents WHERE container_ref = %s", (drive,))]
            conn.execute("DELETE FROM kl.jobs WHERE payload->>'connection_id' IN "
                         "(SELECT id::text FROM kl.connections WHERE name = ANY(%s))", ([name, bad],))
            conn.execute("DELETE FROM kl.connections WHERE name = ANY(%s)", ([name, bad],))
            conn.execute("DELETE FROM kl.documents WHERE container_ref = %s", (drive,))
            conn.execute("DELETE FROM kl.principal_aliases WHERE source = 'ext:google.alice@acme.test'")
        tuples = {(t["user"], t["relation"], t["object"]) for k in keys for t in fga.read({"object": f"document:{k}"})}
        tuples |= {link} if fga.read({"user": link[0], "relation": link[1], "object": link[2]}) else set()
        if tuples:
            fga.write(deletes=tuples)


@check
def google_identities_link_by_sign_in():
    """Identity: a Google sign-in links its verified email to whoever started it; states are single-use, bound to their user and provider; unverified emails, foreign tokens, and someone else's account are refused"""
    import uuid
    from urllib.parse import parse_qsl, urlsplit
    from .authz import fga
    tag = uuid.uuid4().hex[:6]
    client = {"client_id": f"signin-{tag}.apps.googleusercontent.com", "client_secret": "fake-signin-secret", "api_base": FAKE_GOOGLE}
    carol = f"carol.{tag}@acme.test"
    source = f"ext:google.{carol}"

    def code(email, **who):
        return httpx.post(f"{FAKE_GOOGLE}/signin", json={"email": email, **who}, timeout=10).json()["code"]

    def start(user):
        r = api("POST", "/v1/identities/google/start", user)
        assert r.status_code == 200, f"start: {r.status_code} {r.text[:200]}"
        return dict(parse_qsl(urlsplit(r.json()["url"]).query))

    def complete(user, state, c):
        return api("POST", "/v1/identities/google/complete", user, json={"code": c, "state": state})

    assert api("PUT", "/v1/identities/google/client", "bob", json=client).status_code == 403, "a non-admin set the sign-in client"
    r = api("PUT", "/v1/identities/google/client", "olivia", json={**client, "api_base": "https://evil.example"})
    assert r.status_code == 422, "the sign-in client could name any server"
    assert api("PUT", "/v1/identities/google/client", "olivia", json=client).status_code == 200
    try:
        q = start("carol")
        assert q["client_id"] == client["client_id"] and q["redirect_uri"].endswith("/link/google/callback") \
            and q["scope"] == "openid email" and len(q["state"]) >= 40, q
        assert complete("bob", q["state"], code(carol)).status_code == 400, "bob completed carol's sign-in"
        q = start("carol")  # bob's attempt used up her state
        r = complete("carol", q["state"], code(carol.upper()))
        assert r.status_code == 200 and r.json()["linked"] == source, f"link: {r.status_code} {r.text[:200]}"
        assert complete("carol", q["state"], code(carol)).status_code == 400, "a sign-in state was used twice"
        assert source in {i["source"] for i in api("GET", "/v1/identities", "carol").json()["identities"]}
        dave = f"dave.{tag}@acme.test"
        assert complete("dave", start("dave")["state"], code(dave, verified=False)).status_code == 400, "an unverified email linked"
        r = complete("dave", start("dave")["state"], code(dave, aud="someone-else.apps.googleusercontent.com"))
        assert r.status_code == 400, "a token issued for another client linked"
        assert complete("dave", start("dave")["state"], code(carol)).status_code == 409, "carol's Google account linked to dave too"
        r = api("POST", "/v1/identities/github/complete", "dave", json={"code": "x", "state": start("dave")["state"]})
        assert r.status_code == 400, "a Google sign-in state completed a GitHub link"
        assert api("DELETE", f"/v1/identities/{source}", "carol").status_code == 200
        assert source not in {i["source"] for i in api("GET", "/v1/identities", "carol").json()["identities"]}
        assert not fga.read({"object": f"identity:google.{carol}"}), "the unlinked identity's authorization survived"
    finally:
        with psycopg.connect(OWNER_DB) as conn:
            conn.execute("DELETE FROM kl.principal_aliases WHERE source LIKE %s", (f"ext:google.%{tag}@acme.test",))
        for email in (carol, f"dave.{tag}@acme.test"):
            tuples = {(t["user"], t["relation"], t["object"]) for t in fga.read({"object": f"identity:google.{email}"})}
            if tuples:
                fga.write(deletes=tuples)
        api("DELETE", "/v1/identities/google/client", "olivia")


# ------------------------------------------------------------------ imports: Confluence and Notion exports (slice J2)
@check
def confluence_and_notion_exports_import():
    """Imports: Notion and Confluence exports become documents only their chosen readers find; a newer export updates pages and removes missing ones; bad uploads are refused"""
    import io
    import uuid
    import zipfile
    tag = uuid.uuid4().hex[:8]
    wiki, oncall, gone = (uuid.uuid4().hex for _ in range(3))
    nname, cname, bname = f"notion-{tag}", f"confluence-{tag}", f"bomb-{tag}"

    def zipped(files: dict) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for path, body in files.items():
                z.writestr(path, body)
        return buf.getvalue()

    def upload(user, name, blob, **form):
        return api("POST", "/v1/workspaces/payments/imports", user, data={"name": name, **form},
                   files={"file": ("export.zip", blob, "application/zip")})

    def finished(name):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            listed = {i["name"]: i for i in api("GET", "/v1/workspaces/payments/imports", "alice").json()["imports"]}
            run = (listed.get(name) or {}).get("last_run")
            if run and run["status"] != "queued":
                return run
            time.sleep(2)
        raise AssertionError(f"the worker never applied the {name} import")

    def titles(user):
        r = api("GET", "/v1/search", user, params={"q": tag, "scope": "org", "limit": 50})
        return {hit["title"] for hit in r.json()["results"]}

    def notion(oncall_text, with_gone=True):
        files = {f"Wiki {tag} {wiki}.md": f"# Wiki {tag}\n\nWelcome to the wiki, {tag}.",
                 f"Wiki {tag} {wiki}/Oncall {tag} {oncall}.md": f"# Oncall {tag}\n\n{oncall_text} {tag}."}
        if with_gone:
            files[f"Gone {tag} {gone}.md"] = f"# Gone {tag}\n\nThis page leaves in the next export, {tag}."
        return zipped(files)

    try:
        r = upload("bob", nname, notion("Page the on-call."), readers="user:alice")
        assert r.status_code in (403, 404), "a non-admin imported into payments"
        r = upload("alice", nname, notion("Page the on-call."), readers="user:bob")
        assert r.status_code == 422, "an import granted someone off the team"
        assert upload("alice", nname, b"not a zip", readers="user:alice").status_code == 422, "a file that isn't a zip was accepted"
        r = upload("alice", nname, zipped({"readme.txt": "hello"}), readers="user:alice")
        assert r.status_code == 422, "an archive that's neither export was accepted"
        r = upload("alice", nname, notion("Page the payments on-call first."), readers="user:alice")
        assert r.status_code == 202 and r.json()["kind"] == "notion", f"upload: {r.status_code} {r.text[:200]}"
        run = finished(nname)
        assert run["status"] == "done" and run["result"]["pages"] == 3, run
        assert {f"Wiki {tag}", f"Oncall {tag}", f"Gone {tag}"} <= titles("alice"), titles("alice")
        assert not titles("carol") & {f"Wiki {tag}", f"Oncall {tag}"}, "someone who isn't a reader found an imported page"
        r = upload("alice", nname, notion("Page the payments on-call, then the database team.", with_gone=False), readers="user:alice")
        assert r.status_code == 202
        run = finished(nname)
        assert run["status"] == "done" and run["result"]["removed"] == 1 and run["result"]["changed"] >= 1, run
        assert f"Gone {tag}" not in titles("alice"), "a page missing from the newer export is still there"
        page = (f"<html><head><title>Engineering : Failover {tag}</title></head><body>"
                "<div id=\"breadcrumb-section\">Engineering Home</div>"
                f"<div id=\"main-content\"><h1>Steps</h1><p>Promote the standby, {tag}.</p></div>"
                "<div id=\"footer\">Document generated by Confluence</div></body></html>")
        r = upload("alice", cname, zipped({"ENG/index.html": "<html></html>", f"ENG/Failover-{tag}_4242.html": page}),
                   readers="group:payments", base_url="https://acme.atlassian.net/wiki")
        assert r.status_code == 202 and r.json()["kind"] == "confluence", r.text[:200]
        run = finished(cname)
        assert run["status"] == "done" and run["result"]["pages"] == 1, run
        assert f"Failover {tag}" in titles("carol"), "carol, in the payments group, didn't find the Confluence page"
        assert f"Failover {tag}" not in titles("bob"), "bob found the payments group's Confluence page"
        bomb = io.BytesIO()  # 501 MB of zeros, written a megabyte at a time so smoke itself stays small
        with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z, z.open(f"Big {tag} {uuid.uuid4().hex}.md", "w") as f:
            for _ in range(501):
                f.write(b"\0" * (1024 * 1024))
        assert upload("alice", bname, bomb.getvalue(), readers="user:alice").status_code == 202
        run = finished(bname)
        assert run["status"] == "failed" and "size limit" in (run["error"] or ""), f"a zip bomb was imported: {run}"
        with psycopg.connect(OWNER_DB) as conn:
            kept = conn.execute("SELECT count(*) FROM kl.import_runs r JOIN kl.imports i ON i.id = r.import_id "
                                "WHERE i.name = ANY(%s) AND r.archive IS NOT NULL AND r.status <> 'queued'",
                                ([nname, cname, bname],)).fetchone()[0]
        assert kept == 0, "an upload was kept after it was applied"
    finally:
        for n in (nname, cname, bname):
            api("DELETE", f"/v1/workspaces/payments/imports/{n}", "alice")
        # Removal is a job: wait for it, so the next check never cites a page that's about to go.
        deadline = time.monotonic() + 120
        while titles("alice") and time.monotonic() < deadline:
            time.sleep(2)


# ------------------------------------------------------------------ conversations (slice R1)
@check
def conversations_are_private_and_follow_ups_respect_egress():
    """Conversations: yours alone (row-level security, admins included); follow-ups carry an earlier answer only to a model whose egress allows it; an answer whose source you lose is hidden; export and erasure cover them"""
    import uuid
    from .authz import fga
    from .fake_llm import ANSWER
    ensure_fake_models()
    org, tag = acme_org_id(), uuid.uuid4().hex[:8]
    key, ghost, made = f"conv-{tag}", f"ghost{tag}", []

    def start(user, question, model, scope="org"):
        r = api("POST", "/v1/conversations", user, json={"question": question, "model": model, "scope": scope})
        assert r.status_code == 201, f"start: {r.status_code} {r.text[:200]}"
        made.append(r.json()["conversation"]["id"])
        return r.json()

    def follow(user, cid, question, model):
        return api("POST", f"/v1/conversations/{cid}/messages", user, json={"question": question, "model": model})

    try:
        first = start("alice", PAGE_QUESTION, "payments/smoke-fake", "payments")
        cid = first["conversation"]["id"]
        assert any(c["id"] == "pay-runbook" for c in first["answer"]["citations"]), first["answer"]["citations"]
        assert cid in {c["id"] for c in api("GET", "/v1/conversations", "alice").json()["conversations"]}
        assert cid not in {c["id"] for c in api("GET", "/v1/conversations", "bob").json()["conversations"]}, "bob listed alice's conversation"
        for method, path, kw in (("GET", f"/v1/conversations/{cid}", {}), ("DELETE", f"/v1/conversations/{cid}", {}),
                                 ("PATCH", f"/v1/conversations/{cid}", {"json": {"title": "mine now"}}),
                                 ("POST", f"/v1/conversations/{cid}/messages", {"json": {"question": "and then?", "model": "payments/smoke-fake"}})):
            assert api(method, path, "bob", **kw).status_code == 404, f"bob could {method} alice's conversation"
        assert api("GET", f"/v1/conversations/{cid}", "olivia").status_code == 404, "an org admin read someone's conversation"
        found = api("GET", "/v1/conversations", "alice", params={"q": PAGE_QUESTION[4:14]}).json()["conversations"]
        assert cid in {c["id"] for c in found}, "searching your own questions didn't find the conversation"

        reset_fake()  # carol is in payments and finance: finance's model may read confidential sources, payments' may not
        talk = start("carol", "what delays the month-end close?", "finance/smoke-fake")
        assert any(c["id"] == "fin-close" for c in talk["answer"]["citations"]), "control: finance's model should get the close doc"
        tid = talk["conversation"]["id"]
        reset_fake()
        r = follow("carol", tid, "who signs off on it?", "payments/smoke-fake")
        assert r.status_code == 200, f"follow-up: {r.status_code} {r.text[:200]}"
        prompts = fake_prompts()
        assert any("what delays the month-end close?" in p for p in prompts), "a follow-up lost the person's earlier question"
        assert not any(ANSWER in p for p in prompts), "an answer drawn from confidential sources reached a model that may not receive them"
        assert not any(CLOSE_TEXT in p for p in prompts), "confidential text reached a stricter model through a follow-up"
        reset_fake()
        assert follow("carol", tid, "and when is it due?", "finance/smoke-fake").status_code == 200
        assert any(ANSWER in p for p in fake_prompts()), "control: the model that wrote an answer should get it back"
        roles = [m["role"] for m in api("GET", f"/v1/conversations/{tid}", "carol").json()["messages"]]
        assert roles == ["user", "assistant"] * 3, roles

        with psycopg.connect(OWNER_DB) as conn:  # a note only alice may read, then her access to it goes
            ws_id = conn.execute("SELECT id FROM kl.workspaces WHERE slug = 'payments'").fetchone()[0]
            doc = conn.execute(
                "INSERT INTO kl.documents (org_id, source_kind, source_uri, source_version, external_key, title, content_hash, "
                "classification, acl_principals) VALUES (%s, 'seed', %s, 'v1', %s, %s, %s, 'internal', %s) RETURNING id",
                (org, f"https://kb.acme.test/{key}", key, f"Escalation note {tag}", key, ["user:alice"])).fetchone()[0]
            conn.execute("INSERT INTO kl.chunks (org_id, document_id, ordinal, heading_path, body, acl_principals) "
                         "VALUES (%s, %s, 0, NULL, %s, %s)", (org, doc, f"Escalation note {tag}: page the ledger lead after ten minutes.", ["user:alice"]))
            conn.execute("INSERT INTO kl.workspace_documents (workspace_id, document_id, org_id) VALUES (%s, %s, %s)", (ws_id, doc, org))
        fga.write(writes={("user:alice", "viewer", f"document:{key}")})
        # Keyword search needs every word of the question in the passage, and the note has no embedding yet: ask in its words.
        note = start("alice", f"escalation note {tag}", "payments/smoke-fake", "payments")
        assert any(c["id"] == key for c in note["answer"]["citations"]), f"the note wasn't a source: {note['answer']['citations']}"
        fga.write(deletes={("user:alice", "viewer", f"document:{key}")})
        with psycopg.connect(OWNER_DB) as conn:
            conn.execute("UPDATE kl.documents SET acl_principals = '{}' WHERE external_key = %s", (key,))
            conn.execute("UPDATE kl.chunks SET acl_principals = '{}' WHERE document_id = %s", (doc,))
        answer = api("GET", f"/v1/conversations/{note['conversation']['id']}", "alice").json()["messages"][1]
        assert answer.get("hidden") and ANSWER not in answer["content"] and not answer["citations"], "an answer outlived access to its source"

        mine = api("GET", "/v1/me/export", "alice").json()
        assert any(c["title"] == " ".join(PAGE_QUESTION.split()) for c in mine["conversations"]), "the export left out conversations"
        with psycopg.connect(OWNER_DB) as conn:
            g = conn.execute("INSERT INTO kl.conversations (org_id, owner, title) VALUES (%s, %s, 'a question') RETURNING id",
                             (org, ghost)).fetchone()[0]
            conn.execute("INSERT INTO kl.conversation_messages (org_id, conversation_id, role, content) VALUES (%s, %s, 'user', 'my own words')", (org, g))
        exported = api("GET", f"/v1/users/{ghost}/export", "olivia").json()["conversations"]
        assert len(exported) == 1 and exported[0]["messages"][0]["content"] == "my own words", "an admin's export left out conversations"
        assert api("POST", f"/v1/users/{ghost}/erase", "olivia", json={"confirm": ghost}).status_code == 200
        with psycopg.connect(OWNER_DB) as conn:
            left = conn.execute("SELECT count(*) FROM kl.conversations WHERE owner = %s", (ghost,)).fetchone()[0]
        assert left == 0, "erasure left a person's conversations behind"

        r = api("PATCH", f"/v1/conversations/{cid}", "alice", json={"title": "Paging   payments"})
        assert r.status_code == 200 and r.json()["conversation"]["title"] == "Paging payments"
        assert api("DELETE", f"/v1/conversations/{cid}", "alice").status_code == 200
        assert api("GET", f"/v1/conversations/{cid}", "alice").status_code == 404
    finally:
        reset_fake()
        with psycopg.connect(OWNER_DB) as conn:
            conn.execute("DELETE FROM kl.conversations WHERE id::text = ANY(%s) OR owner = %s", (made, ghost))
            conn.execute("DELETE FROM kl.documents WHERE external_key = %s", (key,))
        if fga.read({"user": "user:alice", "relation": "viewer", "object": f"document:{key}"}):
            fga.write(deletes={("user:alice", "viewer", f"document:{key}")})


@check
def an_answers_graph_holds_only_what_you_can_see():
    """Map focus: an answer's sources and their neighbors, through the same permission path as the map; a source you can't see is ignored, never revealed"""
    r = api("GET", "/v1/graph", "alice", params={"focus": "pay-runbook", "min_mentions": 1})
    assert r.status_code == 200, r.text[:200]
    g = r.json()
    ids = {n["id"] for n in g["nodes"]}
    assert g["focus"] == ["pay-runbook"] and "pay-runbook" in ids, g.get("focus")
    assert all(e["source"] in ids and e["target"] in ids for e in g["edges"]), "an edge left the focused neighborhood"
    whole = {n["id"] for n in api("GET", "/v1/graph", "alice", params={"min_mentions": 1}).json()["nodes"]}
    assert ids <= whole and len(ids) < len(whole), "focus should narrow the map, never widen it"
    b = api("GET", "/v1/graph", "bob", params={"focus": "pay-runbook", "min_mentions": 1}).json()
    assert b["focus"] == [] and not b["nodes"], f"bob's focus on a document he can't see revealed something: {b['nodes'][:3]}"


# ------------------------------------------------------------------ focus and modes in Ask (slice R1)
@check
def asking_can_focus_on_documents_and_choose_how_it_searches():
    """Ask: @-focus answers from only the documents named, never from one you can't open; modes search by words, by meaning, or curated knowledge only"""
    ensure_fake_models()

    def ask(user, question, **extra):
        return api("POST", "/v1/ask", user, json={"question": question, "model": "payments/smoke-fake", "scope": "org", **extra})

    r = ask("alice", PAGE_QUESTION, focus=["kb-ledger"])
    assert r.status_code == 200, f"focus: {r.status_code} {r.text[:200]}"
    assert {c["id"] for c in r.json()["citations"]} == {"kb-ledger"}, f"a focused answer drew on other documents: {r.json()['citations']}"
    reset_fake()
    r = ask("alice", "what delays the month-end close?", focus=["fin-close"])
    assert r.status_code == 200 and not r.json()["citations"], "focusing on a document alice can't open gave her its content"
    assert not fake_prompts(), "focusing on a hidden document still called the model"
    assert ask("alice", PAGE_QUESTION, focus=["../etc"]).status_code == 422, "a malformed focus key was accepted"
    assert ask("alice", PAGE_QUESTION, focus=[f"doc{i}" for i in range(6)]).status_code == 422, "more than five focused documents"
    assert ask("alice", PAGE_QUESTION, mode="everything").status_code == 422, "an unknown mode was accepted"
    words = {c["id"] for c in ask("alice", PARAPHRASE, mode="keyword").json()["citations"]}
    meaning = {c["id"] for c in ask("alice", PARAPHRASE, mode="meaning").json()["citations"]}
    assert "pay-runbook" not in words and "pay-runbook" in meaning, f"keyword mode found a paraphrase, or meaning mode didn't: {words} {meaning}"
    curated = [c["id"] for c in ask("alice", PAGE_QUESTION, mode="curated").json()["citations"]]
    with psycopg.connect(OWNER_DB) as conn:
        tiers = {r[0] for r in conn.execute("SELECT tier FROM kl.documents WHERE external_key = ANY(%s)", (curated,))}
    assert curated and tiers <= {"gold", "silver"}, f"curated mode returned uncurated sources: {tiers}"
    r = api("POST", "/v1/conversations", "alice", json={"question": PAGE_QUESTION, "model": "payments/smoke-fake", "focus": ["kb-ledger"]})
    assert r.status_code == 201 and {c["id"] for c in r.json()["answer"]["citations"]} == {"kb-ledger"}, "a conversation ignored its focus"
    with psycopg.connect(OWNER_DB) as conn:
        conn.execute("DELETE FROM kl.conversations WHERE id = %s", (r.json()["conversation"]["id"],))


# ------------------------------------------------------------------ documents and pipeline runs (slice R2)
@check
def the_documents_list_holds_only_what_you_can_open():
    """Documents: each person's list holds exactly what they can open (drift never shows); every filter narrows it, none widens it"""
    def docs(user, **params):
        r = api("GET", "/v1/documents", user, params={"limit": 100, **params})
        assert r.status_code == 200, f"{user}: {r.status_code} {r.text[:200]}"
        return r.json()

    everything = docs("alice")["documents"]
    alice = {d["id"] for d in everything}
    assert "pay-runbook" in alice and "fin-close" not in alice, f"alice's list: {sorted(alice)[:12]}"
    assert "pay-drift" not in alice, "a document OpenFGA denies (the seeded drift) was listed"
    bob = {d["id"] for d in docs("bob")["documents"]}
    assert "pay-runbook" not in bob and "fin-close" in bob, f"bob's list: {sorted(bob)[:12]}"
    assert all(d["classification"] == "internal" for d in docs("alice", classification="internal")["documents"])
    curated = next((d["tier"] for d in everything if d["tier"] in ("gold", "silver")), None)
    assert curated, "alice should see some curated knowledge"
    narrowed = docs("alice", tier=curated)["documents"]
    assert narrowed and all(d["tier"] == curated for d in narrowed), "the tier filter let other tiers through"
    assert all(d["tier"] not in ("gold", "silver") for d in docs("alice", tier="evidence")["documents"])
    assert all("payments" in d["workspaces"] for d in docs("alice", scope="payments")["documents"])
    titled = docs("alice", q="incident")["documents"]
    assert titled and all("incident" in d["title"].lower() for d in titled), "the title filter matched other titles"
    assert [d["id"] for d in docs("alice", key="pay-runbook")["documents"]] == ["pay-runbook"]
    assert docs("alice", key="fin-close")["documents"] == [], "asking for a hidden document by its key revealed it"
    page = docs("alice", limit=2)
    assert len(page["documents"]) <= 2 and page["next_offset"] == 2, f"paging: {page['next_offset']}"
    assert api("GET", "/v1/documents", "alice", params={"classification": "secret"}).status_code == 422


@check
def a_teams_admins_see_its_runs_and_retry_the_failed_ones():
    """Pipeline: a team's admins see its runs and why one failed; a failed removal can run again, a failed import can't (its upload is gone), and nobody else sees or retries them"""
    import uuid
    org, tag = acme_org_id(), uuid.uuid4().hex[:8]
    jobs, imports = [], []
    with psycopg.connect(OWNER_DB) as conn:
        ws = conn.execute("SELECT id FROM kl.workspaces WHERE slug = 'payments'").fetchone()[0]
        imp = conn.execute("INSERT INTO kl.imports (org_id, workspace_id, name, kind, readers, classification, created_by) "
                           "VALUES (%s, %s, %s, 'notion', %s, 'internal', 'alice') RETURNING id",
                           (org, ws, f"runs-{tag}", ["group:payments"])).fetchone()[0]
        imports.append(imp)
        run = conn.execute("INSERT INTO kl.import_runs (org_id, import_id, status, error, created_by) "
                           "VALUES (%s, %s, 'failed', 'not a Notion export', 'alice') RETURNING id", (org, imp)).fetchone()[0]
        for kind, payload, error in (("import.retire", {"import_id": str(imp)}, "the database was restarting"),
                                     ("import.apply", {"run_id": str(run)}, "not a Notion export")):
            jobs.append(conn.execute("INSERT INTO kl.jobs (org_id, kind, payload, status, attempts, last_error) "
                                     "VALUES (%s, %s, %s, 'failed', 5, %s) RETURNING id", (org, kind, json.dumps(payload), error)).fetchone()[0])
    retire, apply = jobs
    try:
        r = api("GET", "/v1/workspaces/payments/runs", "alice")
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        runs = {x["id"]: x for x in r.json()["runs"]}
        assert retire in runs and apply in runs, "a team's failed runs weren't listed for its admin"
        assert runs[retire]["target"] == f"runs-{tag}" and runs[retire]["error"] == "the database was restarting"
        assert runs[retire]["retryable"] and not runs[apply]["retryable"], "only the removal can run again: the import's upload is gone"
        failed = api("GET", "/v1/workspaces/payments/runs", "alice", params={"status": "failed"}).json()["runs"]
        assert failed and all(x["status"] == "failed" for x in failed)
        assert api("GET", "/v1/workspaces/payments/runs", "bob").status_code == 404, "someone outside the team saw its runs"
        assert api("GET", "/v1/workspaces/payments/runs", "carol").status_code == 403, "a member who isn't an admin saw its runs"
        assert api("POST", f"/v1/workspaces/payments/runs/{retire}/retry", "carol").status_code == 403
        assert api("POST", f"/v1/workspaces/payments/runs/{apply}/retry", "alice").status_code == 409, "a failed import was retried"
        r = api("POST", f"/v1/workspaces/payments/runs/{retire}/retry", "alice")
        assert r.status_code == 200 and r.json()["status"] == "queued", f"retry: {r.status_code} {r.text[:200]}"
        assert api("POST", f"/v1/workspaces/payments/runs/{retire}/retry", "alice").status_code in (404, 409), "a run was queued twice"
        with psycopg.connect(OWNER_DB) as conn:
            audited = conn.execute("SELECT count(*) FROM kl.audit_events WHERE action = 'run.retry' AND details->>'run' = %s",
                                   (str(retire),)).fetchone()[0]
        assert audited == 1, "the retry wasn't audited"
    finally:
        with psycopg.connect(OWNER_DB) as conn:
            conn.execute("DELETE FROM kl.jobs WHERE id = ANY(%s)", (jobs,))
            conn.execute("DELETE FROM kl.imports WHERE id = ANY(%s)", (imports,))


# ------------------------------------------------------------------ home and costs (slice R3)
@check
def home_counts_only_what_you_can_open():
    """Home: each of your teams with the documents you can open there (the count Documents lists, never the seeded drift), your own recent conversations, and failed runs only for a team's admins"""
    import uuid
    org, title = acme_org_id(), f"home check {uuid.uuid4().hex[:8]}"
    with psycopg.connect(OWNER_DB) as conn:
        cid = conn.execute("INSERT INTO kl.conversations (org_id, owner, title) VALUES (%s, 'alice', %s) RETURNING id", (org, title)).fetchone()[0]
    try:
        r = api("GET", "/v1/home", "alice")
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        home = r.json()
        teams = {t["slug"]: t for t in home["teams"]}
        assert "payments" in teams and teams["payments"]["role"] == "admin", teams
        listed = api("GET", "/v1/documents", "alice", params={"scope": "payments", "limit": 100}).json()
        assert listed["next_offset"] is None, "the check assumes payments holds at most 100 documents"
        assert teams["payments"]["documents"] == len(listed["documents"]), \
            f"home counts {teams['payments']['documents']} payments documents, Documents lists {len(listed['documents'])}"
        assert "failed_runs" in teams["payments"], "a team's admin wasn't shown its failed runs"
        assert title in [c["title"] for c in home["recent_conversations"]], "alice's own conversation wasn't recent"
        carol = {t["slug"]: t for t in api("GET", "/v1/home", "carol").json()["teams"]}
        assert carol["payments"]["role"] == "member" and "failed_runs" not in carol["payments"], "a member saw a team's failed runs"
        bob = api("GET", "/v1/home", "bob").json()
        assert "payments" not in {t["slug"] for t in bob["teams"]}, "bob's home listed a team he isn't in"
        assert title not in [c["title"] for c in bob["recent_conversations"]], "someone else's conversation showed on bob's home"
    finally:
        with psycopg.connect(OWNER_DB) as conn:
            conn.execute("DELETE FROM kl.conversations WHERE id = %s", (cid,))


@check
def costs_show_a_teams_spend_against_its_budgets():
    """Costs: a team's admins see each model's tokens this month against its budget (counted exactly as the budget gate counts) and by day; org admins see every team's month; nobody else sees either"""
    ensure_fake_models()
    assert ask_as("alice", PAGE_QUESTION, "payments/smoke-fake", "payments").status_code == 200, "couldn't spend anything today"
    r = api("GET", "/v1/workspaces/payments/usage", "alice")
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
    usage = r.json()
    model = next((m for m in usage["models"] if m["model"] == "smoke-fake"), None)
    assert model and model["month_tokens"] >= 112 and model["month_calls"] >= 1, f"this month's use: {model}"
    today = time.strftime("%Y-%m-%d", time.gmtime())
    assert any(d["day"] == today and d["model"] == "smoke-fake" and d["tokens"] >= 112 for d in usage["daily"]), "today's use is missing"
    with psycopg.connect(OWNER_DB) as conn:
        gate = conn.execute(
            "SELECT coalesce(sum(coalesce(u.prompt_tokens, 0) + coalesce(u.completion_tokens, 0)), 0) FROM kl.model_usage u "
            "JOIN kl.model_endpoints m ON m.id = u.endpoint_id JOIN kl.workspaces w ON w.id = m.workspace_id "
            "WHERE w.slug = 'payments' AND m.name = 'smoke-fake' "
            "AND u.at >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'").fetchone()[0]
    assert model["month_tokens"] == gate, f"Costs shows {model['month_tokens']} tokens this month; the budget gate counts {gate}"
    assert api("GET", "/v1/workspaces/payments/usage", "carol").status_code == 403, "a member who isn't an admin saw the team's costs"
    assert api("GET", "/v1/workspaces/payments/usage", "bob").status_code == 404, "someone outside the team saw its costs"
    assert api("GET", "/v1/workspaces/payments/usage", "alice", params={"days": 0}).status_code == 422
    org = api("GET", "/v1/usage", "olivia")
    assert org.status_code == 200, f"{org.status_code} {org.text[:200]}"
    payments = next((t for t in org.json()["teams"] if t["team"] == "payments"), None)
    assert payments and payments["tokens"] >= gate, f"every team's month: {payments}"
    assert api("GET", "/v1/usage", "alice").status_code == 403, "someone who isn't an org admin saw every team's costs"


# ------------------------------------------------------------------ status and the developer page (slice R4)
@check
def status_says_whats_up_and_nothing_more():
    """Status: signed-in people see the API's version and which parts are up, never an address or an error; the endpoint list comes from the API itself; neither answers without a token"""
    r = api("GET", "/v1/status", "bob")
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
    s = r.json()
    assert s["ok"] is True and set(s["checks"]) == {"database", "authorization", "secrets", "sign_in"} and all(s["checks"].values()), s
    shown = json.dumps(s).lower()
    assert not any(w in shown for w in ("http", "://", "localhost", "error", "postgres", "keycloak", "openbao", "openfga")), f"status exposed internals: {shown}"
    assert httpx.get(f"{API}/v1/status", timeout=10).status_code == 401, "status answered without a token"
    spec = api("GET", "/v1/openapi.json", "bob")
    assert spec.status_code == 200 and "/v1/conversations" in spec.json()["paths"], "the endpoint list didn't come from the API"
    assert "/v1/openapi.json" not in spec.json()["paths"], "the description lists itself"
    assert httpx.get(f"{API}/v1/openapi.json", timeout=10).status_code == 401, "the API's description answered without a token"


# ------------------------------------------------------------------ service accounts (slice T1)
TERRAFORM_CLIENT = "kl-terraform"
TERRAFORM_SECRET = os.environ.get("KL_TERRAFORM_SECRET", "kl-terraform-dev-secret")  # the dev realm's; never a real one


def client_token(client_id: str, secret: str) -> str:
    """A client's own token, by the client-credentials flow: how a service account such as Terraform signs in."""
    r = httpx.post(TOKEN_URL, data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": secret}, timeout=15)
    assert r.status_code == 200, f"client-credentials token for {client_id}: {r.status_code} {r.text[:200]}"
    return r.json()["access_token"]


@check
def service_accounts_act_only_once_registered_and_only_as_granted():
    """Service accounts: a client's own token is refused until an org admin registers it, and again once disabled or removed; it holds only the roles it's given and reads no documents; removing it removes every role"""
    from .authz import fga
    user = f"user:service-account-{TERRAFORM_CLIENT}"

    def svc(method, path):
        headers = {"Authorization": f"Bearer {client_token(TERRAFORM_CLIENT, TERRAFORM_SECRET)}"}
        return httpx.request(method, f"{API}{path}", headers=headers, timeout=30)

    def sweep():
        api("DELETE", f"/v1/service-accounts/{TERRAFORM_CLIENT}", "olivia")
        for kind in ("workspace:", "org:"):
            left = {(t["user"], t["relation"], t["object"]) for t in fga.read({"user": user, "object": kind})}
            if left:
                fga.write(deletes=left)

    sweep()  # a run that failed halfway may have left it behind
    try:
        assert svc("GET", "/v1/whoami").status_code == 401, "an unregistered client's own token was accepted"
        r = api("POST", "/v1/service-accounts", "carol", json={"client_id": TERRAFORM_CLIENT, "name": "Terraform"})
        assert r.status_code == 403, "someone who isn't an org admin registered a service account"
        r = api("POST", "/v1/service-accounts", "olivia", headers={"Aerugo-Managed-By": "terraform"},
                json={"client_id": TERRAFORM_CLIENT, "name": "Terraform", "org_admin": True})
        assert r.status_code == 201 and r.json()["org_admin"] is True, f"register: {r.status_code} {r.text[:200]}"
        marked = api("GET", f"/v1/service-accounts/{TERRAFORM_CLIENT}", "olivia").json()["managed_by"]
        assert marked and marked["by"] == "terraform", f"registered as Terraform, but not marked as managed by it: {marked}"
        assert api("POST", "/v1/service-accounts", "olivia", json={"client_id": TERRAFORM_CLIENT, "name": "Twice"}).status_code == 409
        who = svc("GET", "/v1/whoami")
        assert who.status_code == 200 and who.json()["username"] == f"service-account-{TERRAFORM_CLIENT}", f"whoami: {who.status_code} {who.text[:200]}"
        assert svc("GET", "/v1/usage").status_code == 200, "an org-admin service account couldn't do what org admins do"
        docs = svc("GET", "/v1/documents")
        assert docs.status_code == 200 and docs.json()["documents"] == [], "a service account read documents nobody granted it"
        r = api("PATCH", f"/v1/service-accounts/{TERRAFORM_CLIENT}", "olivia", json={"org_admin": False})
        assert r.status_code == 200 and r.json()["org_admin"] is False
        assert svc("GET", "/v1/usage").status_code == 403, "org admin outlived its removal"
        r = api("PUT", "/v1/workspaces/payments/members", "alice", json={"principal": user, "role": "admin"})
        assert r.status_code in (200, 201), f"adding the service account to a team: {r.status_code} {r.text[:200]}"
        assert svc("GET", "/v1/workspaces/payments/runs").status_code == 200, "a team role given to a service account didn't take"
        assert api("PATCH", f"/v1/service-accounts/{TERRAFORM_CLIENT}", "olivia", json={"enabled": False}).status_code == 200
        assert svc("GET", "/v1/whoami").status_code == 401, "a disabled service account's token still worked"
        assert api("PATCH", f"/v1/service-accounts/{TERRAFORM_CLIENT}", "olivia", json={"enabled": True}).status_code == 200
        assert svc("GET", "/v1/whoami").status_code == 200, "re-enabling a service account didn't restore it"
        r = api("DELETE", f"/v1/service-accounts/{TERRAFORM_CLIENT}", "olivia")
        assert r.status_code == 200 and r.json()["roles_removed"] >= 1, f"remove: {r.status_code} {r.text[:200]}"
        assert svc("GET", "/v1/whoami").status_code == 401, "a removed service account's token still worked"
        assert not fga.read({"user": user, "object": "workspace:"}), "removing a service account left its team roles behind"
        with psycopg.connect(OWNER_DB) as conn:
            marks = conn.execute("SELECT count(*) FROM kl.managed_resources WHERE kind = 'service_account' AND key = %s",
                                 (TERRAFORM_CLIENT,)).fetchone()[0]
        assert marks == 0, "removing a service account left its managed-by mark behind"
        with psycopg.connect(OWNER_DB) as conn:
            actions = {a for (a,) in conn.execute("SELECT action FROM kl.audit_events WHERE details->>'client_id' = %s", (TERRAFORM_CLIENT,))}
        assert {"service_account.register", "service_account.update", "service_account.remove"} <= actions, f"audited: {actions}"
    finally:
        sweep()


# ------------------------------------------------------------------ what tools manage (slice T2)
@check
def what_terraform_manages_is_marked_and_teams_read_by_slug():
    """Ownership: a change sent as Terraform marks what it manages, and every read says so; a change made by hand keeps the mark (it's drift for the next plan); removing something removes its mark; a malformed header is refused; a team reads by its slug only for those who can reach it"""
    import uuid
    from .authz import fga
    ws = f"tf-{uuid.uuid4().hex[:6]}"
    tf = {"Aerugo-Managed-By": "terraform", "Aerugo-Managed-Workspace": "prod"}
    model = {"name": "tf-model", "provider": "openai_compatible", "model": "fake", "base_url": "http://fake-llm:8080/v1", "api_key": "unused"}
    try:
        r = api("POST", "/v1/workspaces", "olivia", headers={"Aerugo-Managed-By": "Terraform; drop"}, json={"slug": ws, "name": "x"})
        assert r.status_code == 422, "a malformed manager header was accepted"
        r = api("POST", "/v1/workspaces", "olivia", headers=dict(tf), json={"slug": ws, "name": "Terraform check"})
        assert r.status_code == 201, f"create team: {r.status_code} {r.text[:200]}"
        team = api("GET", f"/v1/workspaces/{ws}", "olivia").json()
        assert team["managed_by"]["by"] == "terraform" and team["managed_by"]["workspace"] == "prod", team
        assert api("PATCH", f"/v1/workspaces/{ws}", "olivia", json={"name": "Renamed by hand"}).status_code == 200
        team = api("GET", f"/v1/workspaces/{ws}", "olivia").json()
        assert team["name"] == "Renamed by hand" and team["managed_by"]["by"] == "terraform", "a change made by hand cleared Terraform's mark"

        r = api("PUT", f"/v1/workspaces/{ws}/members", "olivia", headers=dict(tf), json={"principal": "user:carol", "role": "member"})
        assert r.status_code == 200, f"grant: {r.status_code} {r.text[:200]}"
        members = {(m["principal"], m["role"]): m for m in api("GET", f"/v1/workspaces/{ws}/members", "olivia").json()["members"]}
        assert members[("user:carol", "member")]["managed_by"]["by"] == "terraform", members
        r = api("POST", f"/v1/workspaces/{ws}/models", "olivia", headers=dict(tf), json=model)
        assert r.status_code == 201, f"register model: {r.status_code} {r.text[:200]}"
        assert api("POST", f"/v1/workspaces/{ws}/models", "olivia", headers=dict(tf), json=model).status_code == 409, "a duplicate model wasn't a conflict"
        r = api("PUT", f"/v1/workspaces/{ws}/models/tf-model/budget", "olivia", headers=dict(tf), json={"monthly_tokens": 5000})
        assert r.status_code == 200, f"budget: {r.status_code} {r.text[:200]}"
        listed = next(m for m in api("GET", f"/v1/workspaces/{ws}/models", "olivia").json()["models"] if m["name"] == "tf-model")
        assert listed["managed_by"]["by"] == "terraform" and listed["budget_managed_by"]["by"] == "terraform", listed
        assert api("DELETE", f"/v1/workspaces/{ws}/models/tf-model", "olivia").status_code == 200
        r = api("DELETE", f"/v1/workspaces/{ws}/members", "olivia", params={"principal": "user:carol", "role": "member"})
        assert r.status_code == 200
        with psycopg.connect(OWNER_DB) as conn:
            left = conn.execute("SELECT kind, key FROM kl.managed_resources WHERE key LIKE %s", (f"{ws}/%",)).fetchall()
        assert not left, f"removing things left their marks behind: {left}"

        assert api("GET", f"/v1/workspaces/{ws}", "bob").status_code == 200, "an org-wide team should read by its slug for the whole org"
        assert api("PATCH", f"/v1/workspaces/{ws}", "olivia", json={"discoverability": "private"}).status_code == 200
        assert api("GET", f"/v1/workspaces/{ws}", "bob").status_code == 404, "a private team read by its slug for someone outside it"
        assert api("GET", "/v1/workspaces/no-such-team", "olivia").status_code == 404
    finally:
        with psycopg.connect(OWNER_DB) as conn:
            conn.execute("DELETE FROM kl.managed_resources WHERE key = %s OR key LIKE %s", (ws, f"{ws}/%"))
            conn.execute("DELETE FROM kl.model_endpoints WHERE workspace_id IN (SELECT id FROM kl.workspaces WHERE slug = %s)", (ws,))
            conn.execute("DELETE FROM kl.workspaces WHERE slug = %s", (ws,))
        for t in fga.read({"object": f"workspace:{ws}"}):
            fga.write(deletes={(t["user"], t["relation"], t["object"])})
        httpx.delete(f"{BAO_URL}/v1/secret/metadata/kl/acme/{ws}/models/tf-model", headers={"X-Vault-Token": BAO_TOKEN}, timeout=10)


# ------------------------------------------------------------------ runner
def main() -> None:
    wait_for(f"{API}/healthz")
    counts = {"PASS": 0, "SKIP": 0, "FAIL": 0}
    only = os.environ.get("KL_SMOKE_ONLY", "")  # debugging: run only the checks whose names hold this
    for fn in [f for f in CHECKS if only in f.__name__]:
        label = (fn.__doc__ or fn.__name__).strip()
        try:
            status = "SKIP" if fn() is SKIP else "PASS"
            print(f"{status}  {label}")
        except AssertionError as e:
            status = "FAIL"
            print(f"FAIL  {label}\n      {e}")
        except Exception as e:  # noqa: BLE001
            status = "FAIL"
            print(f"FAIL  {label}\n      {type(e).__name__}: {e}")
        counts[status] += 1
    print(f"\n{counts['PASS']} passed, {counts['SKIP']} skipped, {counts['FAIL']} failed")
    sys.exit(1 if counts["FAIL"] else 0)


if __name__ == "__main__":
    main()
