// Server-side sessions for the web app (a backend-for-frontend). The browser gets only an opaque,
// httpOnly session cookie. Access and refresh tokens stay in this process, so a script injected into a
// page can't read them. Sessions live in memory: a restart signs everyone out, which is fine for dev.
import crypto from "node:crypto";

const env = (name, fallback) => process.env[name] || fallback;
export const WEB_URL = env("KL_WEB_URL", "http://localhost:3000");
export const API_URL = env("KL_API_URL", "http://api:8000");
const PUBLIC_ISSUER = env("KL_OIDC_PUBLIC_ISSUER", "http://localhost:8081/realms/acme"); // where browsers go
const INTERNAL_ISSUER = env("KL_OIDC_INTERNAL_ISSUER", "http://keycloak:8080/realms/acme"); // what this server calls
const CLIENT_ID = env("KL_OIDC_CLIENT_ID", "kl-web");
export const REDIRECT_URI = `${WEB_URL}/auth/callback`;
export const SESSION_COOKIE = "kl_session";
export const STATE_COOKIE = "kl_login";
export const COOKIE_OPTIONS = { httpOnly: true, sameSite: "lax", path: "/", secure: WEB_URL.startsWith("https://") };
const LOGIN_TTL_MS = 10 * 60 * 1000;

// On globalThis so route handlers bundled separately share one store.
const store = (globalThis.__klWeb ??= { sessions: new Map(), pending: new Map() });
const random = (bytes) => crypto.randomBytes(bytes).toString("base64url");

export function startLogin() {
  for (const [state, p] of store.pending) if (Date.now() - p.at > LOGIN_TTL_MS) store.pending.delete(state);
  const state = random(24);
  const verifier = random(48);
  const nonce = random(16);
  store.pending.set(state, { verifier, nonce, at: Date.now() });
  const query = new URLSearchParams({
    response_type: "code", client_id: CLIENT_ID, redirect_uri: REDIRECT_URI, scope: "openid", state, nonce,
    code_challenge: crypto.createHash("sha256").update(verifier).digest("base64url"), code_challenge_method: "S256",
  });
  return { url: `${PUBLIC_ISSUER}/protocol/openid-connect/auth?${query}`, state };
}

async function tokens(form) {
  const r = await fetch(`${INTERNAL_ISSUER}/protocol/openid-connect/token`, {
    method: "POST", cache: "no-store",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_id: CLIENT_ID, ...form }),
  });
  if (!r.ok) throw new Error(`the identity provider refused the request (${r.status})`);
  return r.json();
}

// Read for display and the nonce only: the API verifies every token's signature, issuer, audience, expiry.
export function claims(jwt) {
  try {
    return JSON.parse(Buffer.from(String(jwt).split(".")[1], "base64url").toString("utf8"));
  } catch {
    return {};
  }
}

function toSession(t, previous = {}) {
  return {
    access: t.access_token,
    refresh: t.refresh_token || previous.refresh,
    expires: Date.now() + (Number(t.expires_in) || 60) * 1000,
    user: claims(t.access_token).preferred_username || previous.user,
  };
}

export async function finishLogin({ state, code, cookieState }) {
  const pending = store.pending.get(state);
  store.pending.delete(state);
  if (!pending || !code || state !== cookieState || Date.now() - pending.at > LOGIN_TTL_MS) {
    throw new Error("this sign-in expired or didn't start here");
  }
  const t = await tokens({ grant_type: "authorization_code", code, redirect_uri: REDIRECT_URI,
                           code_verifier: pending.verifier });
  if (claims(t.id_token).nonce !== pending.nonce) throw new Error("the sign-in response didn't match this request");
  const sid = random(32);
  store.sessions.set(sid, toSession(t));
  return sid;
}

export async function accessToken(sid) {
  const s = sid ? store.sessions.get(sid) : undefined;
  if (!s) return null;
  if (Date.now() > s.expires - 30_000) {
    try {
      Object.assign(s, toSession(await tokens({ grant_type: "refresh_token", refresh_token: s.refresh }), s));
    } catch {
      store.sessions.delete(sid);
      return null;
    }
  }
  return s.access;
}

export function endSession(sid) {
  if (sid) store.sessions.delete(sid);
  const query = new URLSearchParams({ client_id: CLIENT_ID, post_logout_redirect_uri: `${WEB_URL}/` });
  return `${PUBLIC_ISSUER}/protocol/openid-connect/logout?${query}`;
}
