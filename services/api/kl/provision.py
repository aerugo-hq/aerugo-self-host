"""Bootstrap, run on every `make up`; each step is idempotent:
  1. make sure OpenBao has the KV v2 mount the API uses
  2. apply pending database migrations (kl/migrations), and give the web server's role its password (H3)
  3. load the GitHub App credentials from .secrets/ into OpenBao (dev OpenBao is in-memory)
  4. create the OpenFGA store, model, and seed tuples, or upgrade the model if authz/model.json changed
  5. link dev users to source identities (alice = KL_DEV_GITHUB_LOGIN, bob = octocat)"""
import hashlib
import json
import os
import re
import sys
import time

import httpx
import psycopg

from .authz import OpenFGA
from .migrate import OWNER_DB, apply_pending
from .vault import vault

FGA = os.environ.get("KL_OPENFGA_URL", "http://openfga:8080").rstrip("/")
STORE = os.environ.get("KL_OPENFGA_STORE", "kl-dev")
AUTHZ_DIR = os.environ.get("KL_AUTHZ_DIR", "/app/authz")
BAO = os.environ.get("KL_OPENBAO_URL", "http://openbao:8200").rstrip("/")
SECRETS_DIR = os.environ.get("KL_SECRETS_DIR", "/run/kl-secrets")
ORG_SLUG = os.environ.get("KL_ORG_SLUG", "acme")
GITHUB_LOGIN = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}$")


def wait_for(url: str, seconds: int = 120) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    sys.exit(f"provision: timed out waiting for {url}")


def fail(store_id: str, what: str, r: httpx.Response) -> None:
    httpx.delete(f"{FGA}/stores/{store_id}", timeout=5)  # don't leave a half-built store behind
    sys.exit(f"provision: {what} rejected ({r.status_code}): {r.text}")


def ensure_kv_mount() -> None:
    """The API stores model keys in KV v2 at secret/. Create the mount if the backend lacks it."""
    wait_for(f"{BAO}/v1/sys/health")
    h = vault.headers()  # the services' token, signed in for with the platform's identity in a deployment (M6)
    mounts = httpx.get(f"{BAO}/v1/sys/mounts", headers=h, timeout=5)
    mounts.raise_for_status()
    body = mounts.json()
    existing = body.get("data", body)
    if "secret/" not in existing:
        r = httpx.post(f"{BAO}/v1/sys/mounts/secret", headers=h, timeout=5,
                       json={"type": "kv", "options": {"version": "2"}})
        r.raise_for_status()
        print("provision: enabled KV v2 at secret/")


def import_github_app(prefix: str = "github-app") -> None:
    """Load one App's credentials from .secrets/ into OpenBao: github-app (the read-only connector) or
    curator-app (opens compiler PRs on knowledge repos)."""
    setup = "make curator-app" if prefix == "curator-app" else "make github-app"
    meta_path = os.path.join(SECRETS_DIR, f"{prefix}.json")
    pem_path = os.path.join(SECRETS_DIR, f"{prefix}.pem")
    if not (os.path.exists(meta_path) and os.path.exists(pem_path)):
        print(f"provision: no {prefix} in .secrets/ (run `{setup} ORG=<org>`); skipping")
        return
    with open(meta_path) as f:
        meta = json.load(f)
    with open(pem_path) as f:
        data = {"app_id": str(meta["id"]), "slug": meta.get("slug") or "", "private_key": f.read(),
                "client_id": meta.get("client_id") or ""}  # the App's OAuth client: proves people's GitHub logins
    extra = os.path.join(SECRETS_DIR, f"{prefix}-secrets.json")
    if os.path.exists(extra):
        with open(extra) as f:
            stored = json.load(f)
        data["webhook_secret"] = stored.get("webhook_secret") or ""
        data["client_secret"] = stored.get("client_secret") or ""
    r = httpx.post(f"{BAO}/v1/secret/data/kl/{ORG_SLUG}/connectors/{prefix}",
                   headers=vault.headers(), json={"data": data}, timeout=5)
    r.raise_for_status()
    print(f"provision: {prefix} {data['slug'] or data['app_id']} loaded into OpenBao")


def model_digest() -> tuple[dict, str]:
    with open(os.path.join(AUTHZ_DIR, "model.json")) as f:
        model = json.load(f)
    return model, hashlib.sha256(json.dumps(model, sort_keys=True).encode()).hexdigest()


def record_model(digest: str, model_id: str) -> None:
    with psycopg.connect(OWNER_DB, autocommit=True) as conn:
        conn.execute("INSERT INTO kl.authz_model_versions (sha256, model_id) VALUES (%s, %s) "
                     "ON CONFLICT (sha256) DO UPDATE SET model_id = EXCLUDED.model_id, applied_at = now()",
                     (digest, model_id))


def upgrade_model(store_id: str) -> None:
    """Write a new authorization model version when authz/model.json changed. Existing tuples carry over."""
    model, digest = model_digest()
    with psycopg.connect(OWNER_DB) as conn:
        known = conn.execute("SELECT 1 FROM kl.authz_model_versions WHERE sha256 = %s", (digest,)).fetchone()
    if known:
        print("provision: authorization model up to date")
        return
    r = httpx.post(f"{FGA}/stores/{store_id}/authorization-models", json=model, timeout=10)
    if r.status_code >= 400:
        sys.exit(f"provision: authorization model rejected ({r.status_code}): {r.text}")
    model_id = r.json()["authorization_model_id"]
    record_model(digest, model_id)
    print(f"provision: authorization model changed; wrote version {model_id}")


def ensure_seed_tuples() -> None:
    """Seed tuples are additive: any added to seed-tuples.json since the store was created are written
    on the next `make up`, so a new seed fixture never needs `make reset`. Nothing is deleted here."""
    with open(os.path.join(AUTHZ_DIR, "seed-tuples.json")) as f:
        seed = {tuple(t) for t in json.load(f)["tuples"]}
    fga = OpenFGA(FGA, STORE)
    existing = set()
    for obj in sorted({o for _, _, o in seed}):
        existing |= {(t["user"], t["relation"], t["object"]) for t in fga.read({"object": obj})}
    if missing := seed - existing:
        fga.write(writes=missing, deletes=set())
        print(f"provision: wrote {len(missing)} new seed tuple(s)")


def seed_dev_identities() -> None:
    """Dev links. alice = your GitHub login (KL_DEV_GITHUB_LOGIN). bob = octocat, a real GitHub account
    outside your org, so bob is linked but GitHub grants him nothing. carol stays unlinked. A deployment sets
    KL_DEV_IDENTITIES=0: no dev person exists there, so every dev link (and its grant) is removed, never made."""
    enabled = os.environ.get("KL_DEV_IDENTITIES", "1") != "0"
    links = {"user:bob": "ext:github.octocat"} if enabled else {}
    login = os.environ.get("KL_DEV_GITHUB_LOGIN", "").strip().lower() if enabled else ""
    if login and GITHUB_LOGIN.fullmatch(login):
        links["user:alice"] = f"ext:github.{login}"
    elif login:
        print(f"provision: KL_DEV_GITHUB_LOGIN {login!r} isn't a valid GitHub username; alice stays unlinked")
    with psycopg.connect(OWNER_DB) as conn:
        org_id = conn.execute("SELECT id FROM kl.orgs WHERE slug = %s", (ORG_SLUG,)).fetchone()[0]
        conn.execute("DELETE FROM kl.principal_aliases WHERE org_id = %s AND verified_by = 'dev-seed'", (org_id,))
        for canonical, source in links.items():
            conn.execute("INSERT INTO kl.principal_aliases (org_id, source, canonical, verified_by) "
                         "VALUES (%s, %s, %s, 'dev-seed') ON CONFLICT (org_id, source) "
                         "DO UPDATE SET canonical = EXCLUDED.canonical, verified_by = 'dev-seed'",
                         (org_id, source, canonical))
    fga = OpenFGA(FGA, STORE)
    desired = {(c, "owner", "identity:" + s.split(":", 1)[1]) for c, s in links.items()}
    existing = set()
    for user in ("user:alice", "user:bob"):
        existing |= {(t["user"], t["relation"], t["object"])
                     for t in fga.read({"user": user, "relation": "owner", "object": "identity:"})}
    fga.write(writes=desired - existing, deletes=existing - desired)
    print("provision: dev identities:", ", ".join(f"{c} = {s}" for c, s in sorted(links.items())))


def ensure_embedding_models() -> None:
    """Org default embedding model (a small local model unless one is already active), one HNSW index per
    model over its own vectors, and a backfill job so every chunk gets embedded."""
    with psycopg.connect(OWNER_DB, autocommit=True) as conn:
        org_id = conn.execute("SELECT id FROM kl.orgs WHERE slug = %s", (ORG_SLUG,)).fetchone()[0]
        if not conn.execute("SELECT 1 FROM kl.embedding_models WHERE org_id = %s AND workspace_id IS NULL "
                            "AND status = 'active'", (org_id,)).fetchone():
            conn.execute("INSERT INTO kl.embedding_models (org_id, provider, model, dims, status) "
                         "VALUES (%s, 'local', 'BAAI/bge-small-en-v1.5', 384, 'active')", (org_id,))
            print("provision: org default embedding model: local BAAI/bge-small-en-v1.5 (384 dims)")
        for model_id, dims in conn.execute("SELECT id, dims FROM kl.embedding_models WHERE org_id = %s "
                                           "AND status IN ('active', 'building')", (org_id,)).fetchall():
            index = "chunk_embeddings_hnsw_" + model_id.hex[:12]
            conn.execute(f"CREATE INDEX IF NOT EXISTS {index} ON kl.chunk_embeddings USING hnsw "
                         f"((embedding::vector({int(dims)})) vector_cosine_ops) WHERE model_id = '{model_id}'")
        if not conn.execute("SELECT 1 FROM kl.jobs WHERE kind = 'embeddings.backfill' "
                            "AND status IN ('queued', 'running')").fetchone():
            conn.execute("INSERT INTO kl.jobs (org_id, kind) VALUES (%s, 'embeddings.backfill')", (org_id,))
        # Dev only: index the sandbox knowledge bundles as if they came from <workspace>-knowledge repos.
        if os.environ.get("KL_SEED_KNOWLEDGE_DIR") and not conn.execute(
                "SELECT 1 FROM kl.jobs WHERE kind = 'knowledge.seed' AND status IN ('queued', 'running')").fetchone():
            conn.execute("INSERT INTO kl.jobs (org_id, kind) VALUES (%s, 'knowledge.seed')", (org_id,))


def ensure_web_role() -> None:
    """The web server's database role (H3, migration 0055) gets its password from KL_WEB_PASSWORD, on every run, so a
    rotated password takes effect at the next deploy. Unset, the role keeps none and the web server can't connect:
    no one can sign in, which is the failure we want."""
    from psycopg import sql
    password = os.environ.get("KL_WEB_PASSWORD", "")
    if not password:
        print("provision: KL_WEB_PASSWORD is unset: the web server can't reach its sessions")
        return
    with psycopg.connect(OWNER_DB, autocommit=True) as conn:
        conn.execute(sql.SQL("ALTER ROLE kl_web PASSWORD {}").format(sql.Literal(password)))
    print("provision: the web server's database role is ready")


def main() -> None:
    ensure_kv_mount()
    print("provision: migrations applied:", apply_pending() or "none pending")
    ensure_web_role()
    import_github_app()
    import_github_app("curator-app")
    ensure_embedding_models()
    wait_for(f"{FGA}/healthz")
    stores = httpx.get(f"{FGA}/stores", timeout=5).json().get("stores", [])
    if any(s.get("name") == STORE for s in stores):
        upgrade_model(next(s["id"] for s in stores if s.get("name") == STORE))
        ensure_seed_tuples()
        seed_dev_identities()
        return

    r = httpx.post(f"{FGA}/stores", json={"name": STORE}, timeout=5)
    r.raise_for_status()
    store_id = r.json()["id"]

    model, digest = model_digest()
    r = httpx.post(f"{FGA}/stores/{store_id}/authorization-models", json=model, timeout=10)
    if r.status_code >= 400:
        fail(store_id, "authorization model", r)
    model_id = r.json()["authorization_model_id"]
    record_model(digest, model_id)

    with open(os.path.join(AUTHZ_DIR, "seed-tuples.json")) as f:
        keys = [{"user": u, "relation": rel, "object": obj} for u, rel, obj in json.load(f)["tuples"]]
    for i in range(0, len(keys), 50):
        r = httpx.post(f"{FGA}/stores/{store_id}/write",
                       json={"authorization_model_id": model_id, "writes": {"tuple_keys": keys[i:i + 50]}},
                       timeout=10)
        if r.status_code >= 400:
            fail(store_id, "seed tuples", r)

    print(f"provision: store {store_id}, model {model_id}, {len(keys)} tuples")
    seed_dev_identities()


if __name__ == "__main__":
    main()
