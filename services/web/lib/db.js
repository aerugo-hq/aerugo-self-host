// The web server's database (H3): one pool per process, connected as kl_web, which reaches only the session tables
// (migration 0055). Every query runs in a transaction set to this deployment's org, as row-level security requires.
import pg from "pg";

const ORG_SLUG = process.env.KL_ORG_SLUG || "acme";

// On globalThis so route handlers bundled separately share one pool.
const shared = (globalThis.__klWebDb ??= { pool: null, org: null });

function pool() {
  if (!shared.pool) {
    const connectionString = process.env.KL_WEB_DATABASE_URL;
    if (!connectionString) throw new Error("KL_WEB_DATABASE_URL is not set: the web server can't keep sessions");
    shared.pool = new pg.Pool({ connectionString, max: Number(process.env.KL_WEB_DB_POOL_MAX) || 10,
                                connectionTimeoutMillis: 5000, idleTimeoutMillis: 60_000 });
  }
  return shared.pool;
}

// fn(client) inside one transaction in this org's row-level security context; committed if it returns, rolled back
// if it throws (and the error rethrown).
export async function tx(fn) {
  const client = await pool().connect();
  try {
    await client.query("BEGIN");
    if (!shared.org) {
      shared.org = (await client.query("SELECT kl.resolve_org($1) AS id", [ORG_SLUG])).rows[0]?.id;
      if (!shared.org) throw new Error(`org ${ORG_SLUG} not found`);
    }
    await client.query("SELECT set_config('kl.org_id', $1, true)", [shared.org]);
    const out = await fn(client);
    await client.query("COMMIT");
    return out;
  } catch (e) {
    await client.query("ROLLBACK").catch(() => {}); // the connection may be gone; the original error is what matters
    throw e;
  } finally {
    client.release();
  }
}
