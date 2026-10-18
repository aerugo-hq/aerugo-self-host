// L3: the ID token from the sign-in, checked before a session is made: its RS256 signature against one of the identity
// provider's published keys (a JWKS), its issuer, its audience (this app), its expiry, and the sign-in's nonce. Anything
// else fails closed. Node's own crypto reads the keys, so no library is involved. Pure: the keys are passed in.
import crypto from "node:crypto";

const SKEW_SECONDS = 60; // clocks a minute apart still agree on an expiry

export class UnknownKey extends Error {} // signed with a key not in the set given: fetch the set again, once

const part = (text) => JSON.parse(Buffer.from(text, "base64url").toString("utf8"));

export function verifyIdToken(token, keys, { issuer, audience, nonce, now = Date.now() / 1000 }) {
  const parts = String(token ?? "").split(".");
  if (parts.length !== 3) throw new Error("not a signed token");
  let header, claims;
  try {
    [header, claims] = [part(parts[0]), part(parts[1])];
  } catch {
    throw new Error("not a readable token");
  }
  if (header.alg !== "RS256") throw new Error(`signed with ${header.alg}, not RS256`);
  const jwk = keys.find((k) => k.kid === header.kid && k.kty === "RSA" && (!k.use || k.use === "sig"));
  if (!jwk) throw new UnknownKey("signed with a key the identity provider doesn't publish");
  const signed = crypto.verify("RSA-SHA256", Buffer.from(`${parts[0]}.${parts[1]}`),
    crypto.createPublicKey({ key: jwk, format: "jwk" }), Buffer.from(parts[2], "base64url"));
  if (!signed) throw new Error("its signature doesn't verify");
  if (claims.iss !== issuer) throw new Error("issued by someone else");
  if (!(Array.isArray(claims.aud) ? claims.aud : [claims.aud]).includes(audience)) throw new Error("issued for another app");
  if (!(Number(claims.exp) + SKEW_SECONDS > now)) throw new Error("expired");
  if (!nonce || claims.nonce !== nonce) throw new Error("for another sign-in");
  return claims;
}
