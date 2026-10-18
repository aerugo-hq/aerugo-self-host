// Server-side sessions for the web app (a backend-for-frontend). The browser gets only an opaque, httpOnly session
// cookie; access and refresh tokens never reach it, so a script injected into a page can't read them.
//
// Sessions and sign-ins in progress live in Postgres (H3, migration 0055), so a restart signs no one out and every web
// server sees every session. A row holds the SHA-256 of the cookie, never the cookie, and tokens only encrypted
// (AES-256-GCM, bound to their row, with a key derived from KL_WEB_SESSION_KEY), so a stolen row or backup signs no
// one in. A session ends when it's idle too long or reaches its absolute lifetime, whichever is first.
import crypto from "node:crypto";
import { tx } from "./db.js";
import { UnknownKey, verifyIdToken } from "./idtoken.mjs";

const env = (name, fallback) => process.env[name] || fallback;
export const WEB_URL = env("KL_WEB_URL", "http://localhost:3000");
export const API_URL = env("KL_API_URL", "http://api:8000");
// M9: how long this server waits for the API, above the API's own 90 seconds for a model, which Ask may wait on.
export const API_TIMEOUT_MS = 1000 * (Number(env("KL_WEB_API_TIMEOUT_SECONDS", "180")) || 180);
const PUBLIC_ISSUER = env("KL_OIDC_PUBLIC_ISSUER", "http://localhost:8081/realms/acme"); // where browsers go
const INTERNAL_ISSUER = env("KL_OIDC_INTERNAL_ISSUER", "http://keycloak:8080/realms/acme"); // what this server calls
const CLIENT_ID = env("KL_OIDC_CLIENT_ID", "kl-web");
export const REDIRECT_URI = `${WEB_URL}/auth/callback`;
export const SESSION_COOKIE = "kl_session";
export const STATE_COOKIE = "kl_login";
const IDLE_SECONDS = 60 * (Number(env("KL_WEB_SESSION_IDLE_MINUTES", "30")) || 30);
export const MAX_SECONDS = 3600 * (Number(env("KL_WEB_SESSION_MAX_HOURS", "10")) || 10);
export const COOKIE_OPTIONS = { httpOnly: true, sameSite: "lax", path: "/", secure: WEB_URL.startsWith("https://") };
const LOGIN_SECONDS = 600;
const IDP_TIMEOUT_MS = 10_000;

const random = (bytes) => crypto.randomBytes(bytes).toString("base64url");
const sha256 = (text) => crypto.createHash("sha256").update(text).digest();

let sealKey;
function key() {
  if (!sealKey) {
    const raw = process.env.KL_WEB_SESSION_KEY || "";
    if (raw.length < 32) throw new Error("KL_WEB_SESSION_KEY must be set, at least 32 characters: no session can be kept");
    sealKey = sha256(`kl-web-sessions:${raw}`);
  }
  return sealKey;
}

// AES-256-GCM, its additional data the row's own hash: a ciphertext moved to another row doesn't open.
function seal(text, row) {
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv("aes-256-gcm", key(), iv);
  cipher.setAAD(row);
  const body = Buffer.concat([cipher.update(String(text), "utf8"), cipher.final()]);
  return Buffer.concat([Buffer.from([1]), iv, cipher.getAuthTag(), body]);
}

function open(blob, row) {
  if (blob[0] !== 1) throw new Error("a session token sealed in an unknown format");
  const decipher = crypto.createDecipheriv("aes-256-gcm", key(), blob.subarray(1, 13));
  decipher.setAAD(row);
  decipher.setAuthTag(blob.subarray(13, 29));
  return Buffer.concat([decipher.update(blob.subarray(29)), decipher.final()]).toString("utf8");
}

export async function startLogin() {
  const state = random(24);
  const verifier = random(48);
  const nonce = random(16);
  const row = sha256(state);
  await tx(async (db) => {
    // Anyone can start a sign-in, so the expired ones go first: the table never holds more than ten minutes of them.
    await db.query("DELETE FROM kl.web_logins WHERE expires_at <= now()");
    await db.query(
      "INSERT INTO kl.web_logins (state_hash, org_id, verifier, nonce, expires_at) " +
      "VALUES ($1, kl.current_org(), $2, $3, now() + make_interval(secs => $4))",
      [row, seal(verifier, row), nonce, LOGIN_SECONDS]);
  });
  const query = new URLSearchParams({
    response_type: "code", client_id: CLIENT_ID, redirect_uri: REDIRECT_URI, scope: "openid", state, nonce,
    code_challenge: crypto.createHash("sha256").update(verifier).digest("base64url"), code_challenge_method: "S256",
  });
  return { url: `${PUBLIC_ISSUER}/protocol/openid-connect/auth?${query}`, state };
}

export class SignInError extends Error {} // a sign-in that failed for a reason the person may see; anything else isn't shown
class Refused extends SignInError {} // the identity provider said no (an expired or revoked grant), not that it's unreachable

async function tokens(form) {
  const r = await fetch(`${INTERNAL_ISSUER}/protocol/openid-connect/token`, {
    method: "POST", cache: "no-store", signal: AbortSignal.timeout(IDP_TIMEOUT_MS),
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_id: CLIENT_ID, ...form }),
  });
  if (r.status === 400 || r.status === 401) throw new Refused(`the identity provider refused the request (${r.status})`);
  if (!r.ok) throw new Error(`the identity provider couldn't answer (${r.status})`);
  return r.json();
}

// The identity provider's published signing keys, cached; a token naming a key not among them fetches the set again,
// once (Keycloak rotates its keys). L3: a sign-in's ID token is verified against them before any session is made.
let signingKeys = [];
async function fetchSigningKeys() { // throws when the identity provider doesn't answer: not a refusal, try again shortly
  const r = await fetch(`${INTERNAL_ISSUER}/protocol/openid-connect/certs`, { cache: "no-store", signal: AbortSignal.timeout(IDP_TIMEOUT_MS) });
  if (!r.ok) throw new Error(`the identity provider's keys couldn't be read (${r.status})`);
  signingKeys = (await r.json()).keys || [];
}

function refused(e) {
  console.warn(`sign-in refused, the ID token check failed: ${e.message}`); // what failed, never the token
  return new SignInError("the sign-in response couldn't be verified");
}

async function verifiedIdToken(idToken, nonce) {
  const expected = { issuer: PUBLIC_ISSUER, audience: CLIENT_ID, nonce };
  try {
    return verifyIdToken(idToken, signingKeys, expected);
  } catch (e) {
    if (!(e instanceof UnknownKey)) throw refused(e);
  }
  await fetchSigningKeys();
  try {
    return verifyIdToken(idToken, signingKeys, expected);
  } catch (e) {
    throw refused(e);
  }
}

// Read for display only, after verification for the ID token: the API verifies every access token itself.
export function claims(jwt) {
  try {
    return JSON.parse(Buffer.from(String(jwt).split(".")[1], "base64url").toString("utf8"));
  } catch {
    return {};
  }
}

const lifetime = (t) => Number(t.expires_in) || 60;

export async function finishLogin({ state, code, cookieState }) {
  if (!state || !code || state !== cookieState) throw new SignInError("this sign-in expired or didn't start here");
  const row = sha256(state);
  const pending = await tx(async (db) => (await db.query(
    "DELETE FROM kl.web_logins WHERE state_hash = $1 AND expires_at > now() RETURNING verifier, nonce", [row])).rows[0]);
  if (!pending) throw new SignInError("this sign-in expired or didn't start here");
  const t = await tokens({ grant_type: "authorization_code", code, redirect_uri: REDIRECT_URI,
                           code_verifier: open(pending.verifier, row) });
  await verifiedIdToken(t.id_token, pending.nonce); // L3: signed by the identity provider, for this app and this sign-in
  const user = claims(t.access_token).preferred_username;
  if (!user || !t.refresh_token) throw new SignInError("the sign-in response had no user or no refresh token");
  const sid = random(32);
  const id = sha256(sid);
  await tx((db) => db.query(
    "INSERT INTO kl.web_sessions (id_hash, org_id, username, access_token, refresh_token, access_expires_at, " +
    "idle_expires_at, expires_at) VALUES ($1, kl.current_org(), $2, $3, $4, now() + make_interval(secs => $5), " +
    "now() + make_interval(secs => $6), now() + make_interval(secs => $7))",
    [id, user, seal(t.access_token, id), seal(t.refresh_token, id), lifetime(t), Math.min(IDLE_SECONDS, MAX_SECONDS), MAX_SECONDS]));
  return sid;
}

// The access token for a session cookie, refreshed when it's within 30 seconds of expiring; null once the session
// has ended (idle, past its lifetime, signed out, or its grant refused). Throws when the database or the identity
// provider can't answer, so a passing outage never signs anyone out.
export async function accessToken(sid) {
  if (!sid) return null;
  const id = sha256(sid);
  const s = await tx(async (db) => {
    const row = (await db.query(
      "SELECT access_token, least(idle_expires_at, expires_at) <= now() AS ended, " +
      "access_expires_at > now() + interval '30 seconds' AS fresh, last_seen_at < now() - interval '1 minute' AS seen_long_ago " +
      "FROM kl.web_sessions WHERE id_hash = $1", [id])).rows[0];
    if (row?.ended) await db.query("DELETE FROM kl.web_sessions WHERE id_hash = $1", [id]);
    else if (row?.seen_long_ago) {  // idle time restarts, at most once a minute so a busy page isn't a write per call
      await db.query("UPDATE kl.web_sessions SET last_seen_at = now(), " +
                     "idle_expires_at = least(now() + make_interval(secs => $2), expires_at) WHERE id_hash = $1", [id, IDLE_SECONDS]);
    }
    return row;
  });
  if (!s || s.ended) return null;
  if (s.fresh) return open(s.access_token, id);
  return refresh(id);
}

// M2: one refresh per session at a time. The session's row stays locked while the identity provider answers, so
// requests that arrive together wait, then find it already refreshed and use the new token: with refresh-token
// rotation, a second refresh with the old token would be refused and sign the person out. Only this session's own
// requests wait, never another's; the role's time limits (migration 0055) bound how long.
async function refresh(id) {
  return tx(async (db) => {
    const s = (await db.query(
      "SELECT access_token, refresh_token, access_expires_at > now() + interval '30 seconds' AS fresh " +
      "FROM kl.web_sessions WHERE id_hash = $1 AND least(idle_expires_at, expires_at) > now() FOR UPDATE", [id])).rows[0];
    if (!s) return null;
    if (s.fresh) return open(s.access_token, id);  // refreshed by another request while this one waited
    let t;
    try {
      t = await tokens({ grant_type: "refresh_token", refresh_token: open(s.refresh_token, id) });
    } catch (e) {
      if (!(e instanceof Refused)) throw e;
      await db.query("DELETE FROM kl.web_sessions WHERE id_hash = $1", [id]);
      return null;
    }
    await db.query(
      "UPDATE kl.web_sessions SET access_token = $2, refresh_token = $3, access_expires_at = now() + make_interval(secs => $4) " +
      "WHERE id_hash = $1", [id, seal(t.access_token, id), seal(t.refresh_token || open(s.refresh_token, id), id), lifetime(t)]);
    return t.access_token;
  });
}

// Ends the session here and at the identity provider (its refresh token revoked there, so it can't be used even if
// the browser never finishes the logout redirect); returns where the browser goes to end its single sign-on.
export async function endSession(sid) {
  if (sid) {
    const id = sha256(sid);
    const s = await tx(async (db) => (await db.query(
      "DELETE FROM kl.web_sessions WHERE id_hash = $1 RETURNING refresh_token", [id])).rows[0]);
    if (s) {
      try {
        const r = await fetch(`${INTERNAL_ISSUER}/protocol/openid-connect/logout`, {
          method: "POST", cache: "no-store", signal: AbortSignal.timeout(IDP_TIMEOUT_MS),
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: new URLSearchParams({ client_id: CLIENT_ID, refresh_token: open(s.refresh_token, id) }),
        });
        if (!r.ok && r.status !== 400) console.warn(`sign-out: the identity provider answered ${r.status}`);
      } catch (e) {
        console.warn(`sign-out: the identity provider couldn't be reached (${e.name})`); // the session here is gone anyway
      }
    }
  }
  const query = new URLSearchParams({ client_id: CLIENT_ID, post_logout_redirect_uri: `${WEB_URL}/` });
  return `${PUBLIC_ISSUER}/protocol/openid-connect/logout?${query}`;
}
